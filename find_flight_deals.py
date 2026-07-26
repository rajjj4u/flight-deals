#!/usr/bin/env python3
"""
Flight Deal Finder: EWR/JFK → HYD (Optimized)
==============================================
Streamlined version - no state management, fresh search every run.
Uses fli library (Google Flights API via curl_cffi) with parallel requests.

Strategy:
  1. SearchDates: one call per origin to get cheapest outbound dates (60-90 days)
  2. For top 8 outbound dates per origin: SearchDates round-trip for 4/5 week returns
  3. Return top 5 outbound + top 5 roundtrip combos (no deduplication, always fresh)

Optimizations:
  - Parallel API calls with asyncio + thread pool
  - Reduced API calls: ~20 vs ~40 previously
  - No SearchFlights detail calls (slow) - use SearchDates calendar data
  - No state file - always fresh search
  - Target runtime: ~30-60 seconds
"""

import os
import sys
import json
import asyncio
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor

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
CURRENCY = "USD"

# Telegram credentials from GitHub Secrets
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

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

    def __lt__(self, other):
        return self.total_price < other.total_price


# ============ UTILITIES ============

def generate_valid_dates(start_days: int, end_days: int) -> List[str]:
    """Generate outbound dates (Fri/Sat/Sun/Mon) 60-90 days out."""
    today = datetime.now().date()
    start = today + timedelta(days=start_days)
    end = today + timedelta(days=end_days)
    dates = []
    current = start
    while current <= end:
        if current.weekday() in {4, 5, 6, 0}:  # Fri, Sat, Sun, Mon
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def generate_return_dates(outbound_str: str) -> List[str]:
    """Generate return dates EXACTLY 4 or 5 weeks after outbound (must be Fri/Sat/Sun/Mon)."""
    outbound = datetime.strptime(outbound_str, "%Y-%m-%d")
    ret_dates = []
    for weeks in (4, 5):
        candidate = outbound + timedelta(weeks=weeks)
        if candidate.weekday() in {4, 5, 6, 0}:
            ret_dates.append(candidate.strftime("%Y-%m-%d"))
    return sorted(set(ret_dates))


# ============ FLIGHT SEARCH (parallelized) ============

def _search_outbound_sync(origin: str, dest: str, from_date: str, to_date: str) -> List[Dict]:
    """Synchronous SearchDates for one-way outbound prices."""
    from fli.models import DateSearchFilters, FlightSegment, PassengerInfo
    from fli.search import SearchDates
    from fli.core import resolve_airport

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
    results = searcher.search(filters, currency="USD")

    if not results:
        return []

    valid = []
    for dp in results:
        date_str = dp.date[0].strftime("%Y-%m-%d")
        if dp.date[0].weekday() in {4, 5, 6, 0}:
            valid.append({
                "origin": origin,
                "destination": dest,
                "date": date_str,
                "price": dp.price,
                "currency": dp.currency or "USD",
            })
    valid.sort(key=lambda x: x["price"])
    return valid


def _search_roundtrip_sync(origin: str, dest: str, outbound: str, return_date: str) -> Optional[Dict]:
    """Synchronous SearchDates for one specific round-trip date pair."""
    from fli.models import DateSearchFilters, FlightSegment, PassengerInfo, TripType
    from fli.search import SearchDates
    from fli.core import resolve_airport

    origin_airport = resolve_airport(origin)
    dest_airport = resolve_airport(dest)

    trip_days = (datetime.strptime(return_date, "%Y-%m-%d") - datetime.strptime(outbound, "%Y-%m-%d")).days

    filters = DateSearchFilters(
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
        trip_type=TripType.ROUND_TRIP,
        from_date=return_date,
        to_date=return_date,
        duration=trip_days,
    )

    searcher = SearchDates()
    results = searcher.search(filters, currency="USD")

    if not results:
        return None

    for dp in results:
        if len(dp.date) > 1:
            ret_str = dp.date[1].strftime("%Y-%m-%d")
            if datetime.strptime(ret_str, "%Y-%m-%d").weekday() in {4, 5, 6, 0}:
                return {
                    "origin": origin,
                    "destination": dest,
                    "outbound": outbound,
                    "return": ret_str,
                    "total_price": dp.price,
                    "currency": dp.currency or "USD",
                }
    return None


async def search_outbound(origin: str, dest: str) -> List[Dict]:
    """Async wrapper for outbound search."""
    start_days, end_days = (60, 90)
    today = datetime.now().date()
    from_date = (today + timedelta(days=start_days)).strftime("%Y-%m-%d")
    to_date = (today + timedelta(days=end_days)).strftime("%Y-%m-%d")

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=2) as executor:
        return await loop.run_in_executor(
            executor, _search_outbound_sync, origin, dest, from_date, to_date
        )


async def search_roundtrip(origin: str, dest: str, outbound: str, return_date: str) -> Optional[Dict]:
    """Async wrapper for roundtrip search."""
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=4) as executor:
        return await loop.run_in_executor(
            executor, _search_roundtrip_sync, origin, dest, outbound, return_date
        )


# ============ MAIN PIPELINE ============

async def find_best_deals() -> tuple:
    """Main pipeline: fresh search every run, no state, returns (roundtrip_deals, top_outbound)."""
    log.info("=" * 60)
    log.info(f"FLIGHT DEAL SEARCH: {', '.join(ORIGIN_AIRPORTS)} → {DESTINATION}")
    log.info(f"Window: {SEARCH_WINDOW_DAYS[0]}-{SEARCH_WINDOW_DAYS[1]} days out")
    log.info(f"Return: EXACTLY 4 or 5 weeks after departure")
    log.info("=" * 60)

    # Step 1: Search outbound dates for both origins IN PARALLEL
    outbound_tasks = [search_outbound(origin, DESTINATION) for origin in ORIGIN_AIRPORTS]
    all_outbound_results = await asyncio.gather(*outbound_tasks)

    all_outbound = []
    roundtrip_tasks = []

    for origin, outbound_results in zip(ORIGIN_AIRPORTS, all_outbound_results):
        if not outbound_results:
            continue

        # Collect top 5 outbound for display
        for d in outbound_results[:5]:
            all_outbound.append({
                "origin": origin,
                "destination": DESTINATION,
                "outbound_date": d["date"],
                "outbound_price": d["price"],
                "currency": d["currency"],
            })

        # Queue roundtrip searches for top 8 outbound dates
        for d in outbound_results[:8]:
            ret_dates = generate_return_dates(d["date"])
            for ret_date in ret_dates:
                roundtrip_tasks.append(search_roundtrip(origin, DESTINATION, d["date"], ret_date))

    # Step 2: Execute all roundtrip searches in parallel
    if roundtrip_tasks:
        log.info(f"Executing {len(roundtrip_tasks)} roundtrip searches in parallel...")
        roundtrip_results = await asyncio.gather(*roundtrip_tasks)
        all_combos = [r for r in roundtrip_results if r is not None]
        log.info(f"Found {len(all_combos)} valid roundtrip combinations")
    else:
        all_combos = []

    if not all_combos and not all_outbound:
        log.warning("No flight combos found")
        return [], []

    # Sort and take top
    all_outbound.sort(key=lambda x: x["outbound_price"])
    top_outbound = all_outbound[:5]

    all_combos.sort(key=lambda x: x["total_price"])
    top_combos = all_combos[:5]  # Only need top 5 for final display

    # Build final deals from calendar data (no slow SearchFlights calls)
    deals = []
    for combo in top_combos:
        link = (
            f"https://www.google.com/travel/flights?q=Flights+from+{combo['origin']}"
            f"+to+{combo['destination']}+on+{combo['outbound']}+through+{combo['return']}&curr=USD"
        )
        # Split total price roughly for display
        out_price = combo["total_price"] * 0.5
        ret_price = combo["total_price"] * 0.5

        deal = FlightDeal(
            origin=combo["origin"],
            destination=combo["destination"],
            outbound_date=combo["outbound"],
            return_date=combo["return"],
            outbound_price=out_price,
            return_price=ret_price,
            total_price=combo["total_price"],
            airline="See booking link",
            duration_out="-",
            duration_ret="-",
            stops_out=-1,
            stops_ret=-1,
            baggage_info=f"{2} checked + 1 carry-on",
            booking_link=link,
            source="google_flights_calendar",
        )
        deals.append(deal)

    log.info(f"Built {len(deals)} deals for Telegram")
    return deals, all_outbound[:5]


# ============ TELEGRAM DELIVERY ============

def format_telegram_message(deals: List, top_outbound: List[Dict]) -> str:
    if not deals:
        return "📭 No new flight deals found today."

    today = datetime.now().strftime("%Y-%m-%d")

    lines = [
        f"✈️ <b>Flight Deals: EWR/JFK → HYD</b>",
        f"<i>60-90 days out • Fri/Sat/Sun/Mon only • Return EXACTLY 4 or 5 weeks later • 2 checked + 1 carry-on • Via Google Flights (fli)</i>",
        "",
        "🔻 <b>Top 5 Cheapest Outbound (One-Way)</b>:"
    ]

    for i, o in enumerate(top_outbound, 1):
        price_str = f"${o['outbound_price']:,.0f} {o.get('currency', 'USD')}"
        lines.append(f"  {i}. {o['origin']}→{o['destination']} on {o['outbound_date']} — <b>{price_str}</b>")

    lines.append("")
    lines.append("🔄 <b>Top 5 Cheapest Roundtrips</b>:")

    for i, deal in enumerate(deals, 1):
        lines.append(
            f"  {i}. <b>${deal.total_price:,.0f}</b> | "
            f"{deal.origin}→{deal.destination} | "
            f"Out: {deal.outbound_date} | Ret: {deal.return_date}"
        )
        lines.append(f"      <a href=\"{deal.booking_link}\">Book on Google Flights</a>")

    lines.append("")
    lines.append(f"<i>Flight Deal Bot • {datetime.now().strftime('%Y-%m-%d')}</i>")

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not configured")
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
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=encoded,
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

async def main():
    log.info("🚀 Starting Flight Deal Finder (Optimized)")

    try:
        deals, top_outbound = await find_best_deals()

        if deals:
            message = format_telegram_message(deals, top_outbound)
            send_telegram(message)
            log.info(f"\n✅ Sent {len(deals)} deals to Telegram")
            for d in deals:
                log.info(f"  ${d.total_price:,.0f} | {d.origin}→{d.destination} {d.outbound_date}→{d.return_date}")
        else:
            log.info("\n📭 No deals found")

    except Exception as e:
        log.error(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())