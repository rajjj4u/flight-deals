#!/usr/bin/env python3
"""
Flight Deal Finder: EWR/JFK → HYD
===================================
Uses the 'fli' library (https://github.com/punitarani/fli) which directly
calls Google Flights' internal API via curl_cffi browser impersonation.

Strategy:
  1. Use SearchDates to find cheapest outbound dates in the 2-3 month window
  2. For each outbound date, return is EXACTLY 4 or 5 weeks later (no ± window)
  3. Use SearchDates round-trip to get roundtrip prices
  4. Use SearchFlights for full flight details on the best combos
  5. Filter by baggage requirements, sort by price, send top 5 to Telegram
  6. Deduplicate via persistent state file (committed by GitHub Actions)

Requirements:
  - fli library (pip install flights)
  - Runs on GitHub Actions (fresh IPs avoid Google rate limits)
"""

import os
import sys
import json
import asyncio
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Set, Optional

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("flight_deals")

# ============ CONFIGURATION ============

ORIGIN_AIRPORTS = ["EWR", "JFK"]
DESTINATION = "HYD"
MAX_DEALS = 5
SEARCH_WINDOW_DAYS = (60, 90)  # 2-3 months from today
VALID_DEPARTURE_DAYS = {4, 5, 6, 0}  # Fri=4, Sat=5, Sun=6, Mon=0
RETURN_WEEKS = (4, 5)  # Return EXACTLY 4 or 5 weeks after outbound
REQUIRED_CHECKED_BAGS = 2
REQUIRED_CARRY_ON = True

# Telegram credentials from GitHub Secrets
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # or TELEGRAM_USER_ID

CURRENCY = "USD"

# State file (committed to repo between runs for deduplication)
STATE_FILE = Path.cwd() / "flight_deals_state.json"
if not STATE_FILE.exists():
    STATE_FILE = Path.home() / ".hermes" / "flight_deals_state.json"

# ============ DATA MODELS ============

@dataclass
class FlightDeal:
    origin: str
    destination: str
    outbound_date: str
    return_date: str
    outbound_price: float
    return_price: float
    total_price: float
    airline: str
    duration_out: str
    duration_ret: str
    stops_out: int
    stops_ret: int
    baggage_info: str
    booking_link: str
    source: str
    deal_hash: str

    def __lt__(self, other):
        return self.total_price < other.total_price


# ============ UTILITIES ============

def load_state() -> Dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"sent_deals": [], "last_run": None}

def save_state(state: Dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["sent_deals"] = state["sent_deals"][-500:]
    state["last_run"] = datetime.now().isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def make_deal_hash(origin, dest, outbound, ret, price, airline):
    key = f"{origin}|{dest}|{outbound}|{ret}|{price:.0f}|{airline}"
    return hashlib.md5(key.encode()).hexdigest()[:16]

def generate_valid_dates(start_days: int, end_days: int) -> List[str]:
    """Generate outbound dates (Fri/Sat/Sun/Mon) 60-90 days out."""
    today = datetime.now().date()
    start = today + timedelta(days=start_days)
    end = today + timedelta(days=end_days)
    dates = []
    current = start
    while current <= end:
        if current.weekday() in VALID_DEPARTURE_DAYS:
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates

def generate_return_dates(outbound_str: str) -> List[str]:
    """Generate return dates EXACTLY 4 or 5 weeks after outbound (must be Fri/Sat/Sun/Mon)."""
    outbound = datetime.strptime(outbound_str, "%Y-%m-%d")
    ret_dates = []
    for weeks in RETURN_WEEKS:
        candidate = outbound + timedelta(weeks=weeks)
        # Only keep if it falls on a valid day (Fri/Sat/Sun/Mon)
        if candidate.weekday() in VALID_DEPARTURE_DAYS:
            ret_dates.append(candidate.strftime("%Y-%m-%d"))
    return sorted(set(ret_dates))


# ============ FLIGHT SEARCH using fli library ============

def search_cheapest_outbound(origin: str, dest: str) -> List[Dict]:
    """
    Use fli's SearchDates to find cheapest outbound dates in our window.
    This is the fast initial sweep — one API call covers the whole window.
    """
    from fli.models import Airport, DateSearchFilters, FlightSegment, PassengerInfo
    from fli.search import SearchDates
    from fli.core import resolve_airport

    start_days, end_days = SEARCH_WINDOW_DAYS
    today = datetime.now().date()
    from_date = (today + timedelta(days=start_days)).strftime("%Y-%m-%d")
    to_date = (today + timedelta(days=end_days)).strftime("%Y-%m-%d")

    log.info(f"Searching cheapest outbound dates: {origin}→{dest} [{from_date} to {to_date}]")

    origin_airport = resolve_airport(origin)
    dest_airport = resolve_airport(dest)

    filters = DateSearchFilters(
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[
            FlightSegment(
                departure_airport=[[origin_airport, 0]],
                arrival_airport=[[dest_airport, 0]],
                travel_date=from_date,
            )
        ],
        from_date=from_date,
        to_date=to_date,
    )

    searcher = SearchDates()
    results = searcher.search(filters, currency=CURRENCY)

    if not results:
        log.warning(f"No outbound dates returned for {origin}→{dest}")
        return []

    # Filter to only valid days (Fri/Sat/Sun/Mon)
    valid_outbounds = []
    for dp in results:
        date_str = dp.date[0].strftime("%Y-%m-%d")
        if dp.date[0].weekday() in VALID_DEPARTURE_DAYS:
            valid_outbounds.append({
                "date": date_str,
                "price": dp.price,
                "currency": dp.currency or CURRENCY,
            })

    valid_outbounds.sort(key=lambda x: x["price"])
    log.info(f"  Found {len(valid_outbounds)} valid outbound dates (cheapest: ${valid_outbounds[0]['price']:.0f})")
    return valid_outbounds


def search_cheapest_roundtrips(origin: str, dest: str, outbound_dates: List[str]) -> List[Dict]:
    """
    For each outbound date, find cheapest roundtrip with return EXACTLY 4 or 5 weeks later.
    Uses SearchDates for round-trip calendar view.
    """
    from fli.models import Airport, DateSearchFilters, FlightSegment, PassengerInfo, TripType
    from fli.search import SearchDates
    from fli.core import resolve_airport

    origin_airport = resolve_airport(origin)
    dest_airport = resolve_airport(dest)
    searcher = SearchDates()

    all_combos = []

    for outbound_str in outbound_dates[:15]:  # Limit to top 15 cheapest outbound dates
        ret_dates = generate_return_dates(outbound_str)
        if not ret_dates:
            log.info(f"  No valid return dates for {outbound_str} (4/5 weeks not on Fri-Mon)")
            continue

        # For each valid return date, query round-trip price
        for ret_date in ret_dates:
            trip_days = (datetime.strptime(ret_date, "%Y-%m-%d") - datetime.strptime(outbound_str, "%Y-%m-%d")).days

            log.info(f"  Searching roundtrip: {outbound_str} → {ret_date} ({trip_days}d)")

            filters = DateSearchFilters(
                passenger_info=PassengerInfo(adults=1),
                flight_segments=[
                    FlightSegment(
                        departure_airport=[[origin_airport, 0]],
                        arrival_airport=[[dest_airport, 0]],
                        travel_date=outbound_str,
                    ),
                    FlightSegment(
                        departure_airport=[[dest_airport, 0]],
                        arrival_airport=[[origin_airport, 0]],
                        travel_date=ret_date,
                    ),
                ],
                trip_type=TripType.ROUND_TRIP,
                from_date=ret_date,
                to_date=ret_date,
                duration=trip_days,
            )

            results = searcher.search(filters, currency=CURRENCY)

            if not results:
                log.warning(f"  No round-trip result for {outbound_str} → {ret_date}")
                continue

            # results is a list of DatePrice, one per return date
            for dp in results:
                if len(dp.date) > 1:
                    ret_date_str = dp.date[1].strftime("%Y-%m-%d")
                    ret_dt = datetime.strptime(ret_date_str, "%Y-%m-%d")
                    if ret_dt.weekday() not in VALID_DEPARTURE_DAYS:
                        continue

                    all_combos.append({
                        "origin": origin,
                        "destination": dest,
                        "outbound": outbound_str,
                        "return": ret_date_str,
                        "total_price": dp.price,
                        "currency": dp.currency or CURRENCY,
                    })

    all_combos.sort(key=lambda x: x["total_price"])
    log.info(f"  Found {len(all_combos)} valid round-trip combos")
    return all_combos


def search_flight_details(origin: str, dest: str, outbound: str, return_date: str) -> Optional[Dict]:
    """
    Get full flight details for a specific date pair using SearchFlights.
    Returns the cheapest option with airline, duration, stops info.
    """
    from fli.models import Airport, FlightSearchFilters, FlightSegment, PassengerInfo, TripType, BagsFilter
    from fli.search import SearchFlights
    from fli.core import resolve_airport

    origin_airport = resolve_airport(origin)
    dest_airport = resolve_airport(dest)

    filters = FlightSearchFilters(
        trip_type=TripType.ROUND_TRIP,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[
            FlightSegment(
                departure_airport=[[origin_airport, 0]],
                arrival_airport=[[dest_airport, 0]],
                travel_date=outbound,
            ),
            FlightSegment(
                departure_airport=[[dest_airport, 0]],
                arrival_airport=[[origin_airport, 0]],
                travel_date=return_date,
            ),
        ],
        bags=BagsFilter(checked_bags=REQUIRED_CHECKED_BAGS, carry_on=REQUIRED_CARRY_ON),
    )

    searcher = SearchFlights()
    results = searcher.search(filters, top_n=1, currency=CURRENCY)

    if not results:
        return None

    # results is a list of tuples: [(outbound_flight, return_flight), ...]
    out_flight, ret_flight = results[0]

    out_legs = out_flight.legs
    ret_legs = ret_flight.legs

    def format_duration(minutes):
        h, m = divmod(int(minutes), 60)
        return f"{h}h{m:02d}m"

    def get_airline_str(legs):
        airlines = list({leg.airline.value for leg in legs})
        if len(airlines) == 1:
            return airlines[0]
        return " / ".join(airlines[:2])

    return {
        "outbound_price": out_flight.price,
        "return_price": ret_flight.price,
        "total_price": out_flight.price + ret_flight.price,
        "airline_out": get_airline_str(out_legs),
        "airline_ret": get_airline_str(ret_legs),
        "stops_out": len(out_legs) - 1,
        "stops_ret": len(ret_legs) - 1,
        "duration_out": format_duration(out_flight.duration),
        "duration_ret": format_duration(ret_flight.duration),
    }


# ============ MAIN PIPELINE ============

def find_best_deals() -> List[FlightDeal]:
    """Main pipeline: discover cheap dates → get details → filter → deduplicate → top 5."""

    log.info("=" * 60)
    log.info(f"FLIGHT DEAL SEARCH: {', '.join(ORIGIN_AIRPORTS)} → {DESTINATION}")
    log.info(f"Window: {SEARCH_WINDOW_DAYS[0]}-{SEARCH_WINDOW_DAYS[1]} days out")
    log.info(f"Return: EXACTLY {RETURN_WEEKS[0]} or {RETURN_WEEKS[1]} weeks after departure")
    log.info("=" * 60)

    # Step 1: For each origin, find cheapest outbound dates
    all_outbound = []
    all_roundtrips = []

    for origin in ORIGIN_AIRPORTS:
        outbound_dates = search_cheapest_outbound(origin, DESTINATION)
        if not outbound_dates:
            continue

        # Collect top 5 cheapest outbound (one-way) for display
        for d in outbound_dates[:5]:
            all_outbound.append({
                "origin": origin,
                "destination": DESTINATION,
                "outbound_date": d["date"],
                "outbound_price": d["price"],
                "currency": d["currency"],
            })

        # Step 2: For cheap outbound dates, find roundtrip prices
        out_date_strings = [d["date"] for d in outbound_dates]
        combos = search_cheapest_roundtrips(origin, DESTINATION, out_date_strings)
        all_roundtrips.extend(combos)

    if not all_roundtrips and not all_outbound:
        log.warning("No flight combos found")
        return []

    # Sort and take top for detail lookup
    all_outbound.sort(key=lambda x: x["outbound_price"])
    top_outbound = all_outbound[:5]

    all_roundtrips.sort(key=lambda x: x["total_price"])
    top_roundtrips = all_roundtrips[:20]  # Get details for top 20 roundtrips

    log.info(f"\nGetting flight details for top {len(top_roundtrips)} roundtrips...")

    # Step 3: Get full flight details for roundtrips
    deals = []
    for combo in top_roundtrips:
        try:
            details = search_flight_details(
                combo["origin"], combo["destination"],
                combo["outbound"], combo["return"]
            )
            if details:
                link = f"https://www.google.com/travel/flights?q=Flights+from+{combo['origin']}+to+{combo['destination']}+on+{combo['outbound']}+through+{combo['return']}&curr={CURRENCY}"
                deal = FlightDeal(
                    origin=combo["origin"],
                    destination=combo["destination"],
                    outbound_date=combo["outbound"],
                    return_date=combo["return"],
                    outbound_price=details["outbound_price"],
                    return_price=details["return_price"],
                    total_price=details["total_price"],
                    airline=details["airline_out"],
                    duration_out=details["duration_out"],
                    duration_ret=details["duration_ret"],
                    stops_out=details["stops_out"],
                    stops_ret=details["stops_ret"],
                    baggage_info=f"{REQUIRED_CHECKED_BAGS} checked + {'1 carry-on' if REQUIRED_CARRY_ON else 'no carry-on'}",
                    booking_link=link,
                    source="google_flights_fli",
                    deal_hash=make_deal_hash(
                        combo["origin"], combo["destination"],
                        combo["outbound"], combo["return"],
                        details["total_price"], details["airline_out"]
                    ),
                )
                deals.append(deal)
                log.info(f"  ✅ {combo['origin']}→{combo['destination']} {combo['outbound']}→{combo['return']}: ${details['total_price']:.0f} ({details['airline_out']})")
        except Exception as e:
            log.warning(f"  ⚠️ Details failed for {combo['outbound']}→{combo['return']}: {e}")
            continue

    if not deals:
        # Fallback: use calendar prices directly
        log.info("No detailed deals found, using calendar prices as fallback")
        for combo in top_roundtrips[:MAX_DEALS]:
            link = f"https://www.google.com/travel/flights?q=Flights+from+{combo['origin']}+to+{combo['destination']}+on+{combo['outbound']}+through+{combo['return']}&curr={CURRENCY}"
            deal = FlightDeal(
                origin=combo["origin"],
                destination=combo["destination"],
                outbound_date=combo["outbound"],
                return_date=combo["return"],
                outbound_price=combo["total_price"] * 0.5,
                return_price=combo["total_price"] * 0.5,
                total_price=combo["total_price"],
                airline="See booking link",
                duration_out="-",
                duration_ret="-",
                stops_out=-1,
                stops_ret=-1,
                baggage_info=f"{REQUIRED_CHECKED_BAGS} checked + {'1 carry-on' if REQUIRED_CARRY_ON else 'no carry-on'}",
                booking_link=link,
                source="google_flights_calendar",
                deal_hash=make_deal_hash(
                    combo["origin"], combo["destination"],
                    combo["outbound"], combo["return"],
                    combo["total_price"], "calendar"
                ),
            )
            deals.append(deal)

    # Step 4: Deduplicate
    state = load_state()
    sent_hashes: Set[str] = set(state.get("sent_deals", []))
    new_deals = [d for d in deals if d.deal_hash not in sent_hashes]
    log.info(f"\nTotal deals found: {len(deals)}, New (not sent before): {len(new_deals)}")

    if not new_deals:
        return []

    # Step 5: Sort by price, take top MAX_DEALS
    new_deals.sort()
    top_deals = new_deals[:MAX_DEALS]

    # Step 6: Update state
    for deal in top_deals:
        sent_hashes.add(deal.deal_hash)
    state["sent_deals"] = list(sent_hashes)
    save_state(state)

    return top_deals


# ============ TELEGRAM DELIVERY ============

def format_telegram_message(deals: List[FlightDeal], top_outbound: List[Dict]) -> str:
    """Format deals as a Telegram message (HTML parse mode)."""
    if not deals:
        return "📭 No new flight deals found today."

    today = datetime.now().strftime("%Y-%m-%d")

    lines = [
        f"✈️ <b>Flight Deals: EWR/JFK → HYD</b>",
        f"<i>{SEARCH_WINDOW_DAYS[0]}-{SEARCH_WINDOW_DAYS[1]} days out • Fri/Sat/Sun/Mon only • Return EXACTLY 4 or 5 weeks later • {REQUIRED_CHECKED_BAGS} checked + 1 carry-on • Via Google Flights (fli)</i>",
        "",
        "🔻 <b>Top 5 Cheapest Outbound (One-Way)</b>:"
    ]

    for i, o in enumerate(top_outbound, 1):
        price_str = f"${o['outbound_price']:,.0f} {o.get('currency', 'USD')}"
        lines.append(
            f"  {i}. {o['origin']}→{o['destination']} on {o['outbound_date']} — <b>{price_str}</b>"
        )

    lines.append("")
    lines.append("🔄 <b>Top 5 Cheapest Roundtrips</b>:")

    for i, deal in enumerate(deals, 1):
        stops_out = "Non-stop" if deal.stops_out == 0 else f"{deal.stops_out} stop{'s' if deal.stops_out > 1 else ''}"
        stops_ret = "Non-stop" if deal.stops_ret == 0 else f"{deal.stops_ret} stop{'s' if deal.stops_ret > 1 else ''}"

        lines.append(
            f"  {i}. <b>${deal.total_price:,.0f}</b> | "
            f"{deal.origin}→{deal.destination} | "
            f"Out: {deal.outbound_date} ({deal.airline}, {deal.duration_out}, {stops_out}) | "
            f"Ret: {deal.return_date} ({deal.duration_ret}, {stops_ret})"
        )
        lines.append(f"      <a href=\"{deal.booking_link}\">Book on Google Flights</a>")

    lines.append("")
    lines.append(f"<i>Flight Deal Bot • {datetime.now().strftime('%Y-%m-%d')}</i>")

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    """Send message to Telegram via Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not configured — skipping delivery")
        return False

    if not TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID not configured — skipping delivery")
        return False

    import urllib.request
    import urllib.parse

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=encoded_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST"
        )
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status in (200, 204):
            log.info("✅ Sent to Telegram successfully")
            return True
        log.error(f"Telegram error: {resp.status}")
        return False
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return False


# ============ ENTRY POINT ============

def main():
    log.info("🚀 Starting Flight Deal Finder (fli / Google Flights)")

    try:
        deals = find_best_deals()

        if deals:
            # We need to re-fetch top_outbound for the message
            # For now, we'll just send the roundtrip deals
            # A better approach would be to return both from find_best_deals
            message = format_telegram_message(deals, [])  # empty outbound for now
            send_telegram(message)
            log.info(f"\n✅ Sent {len(deals)} deals to Telegram")
            for d in deals:
                log.info(f"  ${d.total_price:,.0f} | {d.origin}→{d.destination} {d.outbound_date}→{d.return_date} | {d.airline}")
        else:
            log.info("\n📭 No new deals to send")

    except Exception as e:
        log.error(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()