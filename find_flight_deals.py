#!/usr/bin/env python3
"""
Flight Deal Finder: NYC → California + Yellowstone (Domestic)
==============================================================
Uses fli library (Google Flights API via curl_cffi) with parallel requests.
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
ORIGIN_AIRPORTS = ["JFK", "LGA", "EWR"]

DESTINATION_AIRPORTS = [
    "LAX", "SFO", "SAN", "SJC", "OAK", "SMF", "BUR", "ONT", "PSP",  # California
    "JAC"  # Jackson Hole (Yellowstone/Grand Teton)
]

SEARCH_WINDOW_DAYS = (20, 35)
VALID_DEPARTURE_DAYS = {4, 5, 6, 0}  # Fri=4, Sat=5, Sun=6, Mon=0
RETURN_DAYS = (3, 5)
CURRENCY = "USD"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


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


def generate_valid_dates(start_days: int, end_days: int) -> List[str]:
    """Generate outbound dates (Fri/Sat/Sun/Mon) within search window."""
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
    """Generate return dates 3-5 days after outbound date."""
    outbound = datetime.strptime(outbound_str, "%Y-%m-%d")
    ret_dates = []
    for days in range(RETURN_DAYS[0], RETURN_DAYS[1] + 1):
        candidate = outbound + timedelta(days=days)
        ret_dates.append(candidate.strftime("%Y-%m-%d"))
    return ret_dates


# ============ FLIGHT SEARCH ============
def _search_outbound_sync(origin: str, dest: str, start_date: str, end_date: str) -> List[Dict]:
    """Search date calendar for outbound prices using fli SearchDates."""
    from fli import SearchDates, DateSearchFilters

    try:
        filters = DateSearchFilters(
            origin=origin,
            destination=dest,
            start_date=start_date,
            end_date=end_date,
            is_round_trip=False,
            currency=CURRENCY,
        )

        searcher = SearchDates()
        results = searcher.search(filters)

        if not results:
            return []

        outbound_deals = []
        for dp in results:
            date_str = getattr(dp, "date", None) or getattr(dp, "departure_date", None)
            price = getattr(dp, "price", None)
            if not date_str or price is None:
                continue

            dt = datetime.strptime(date_str, "%Y-%m-%d")
            if dt.weekday() in VALID_DEPARTURE_DAYS:
                outbound_deals.append({
                    "origin": origin,
                    "destination": dest,
                    "date": date_str,
                    "price": price,
                    "currency": CURRENCY,
                })

        outbound_deals.sort(key=lambda x: x["price"])
        return outbound_deals
    except Exception as e:
        log.warning(f"Outbound search error {origin}->{dest}: {e}")
        return []


def _search_roundtrip_sync(origin: str, dest: str, outbound: str, return_date: str) -> Optional[Dict]:
    """Search specific roundtrip flight pair using fli SearchFlights."""
    from fli import SearchFlights, FlightSearchFilters

    try:
        filters = FlightSearchFilters(
            origin=origin,
            destination=dest,
            departure_date=outbound,
            return_date=return_date,
            currency=CURRENCY,
        )

        searcher = SearchFlights()
        results = searcher.search(filters)

        if not results:
            return None

        # Take the lowest price option returned
        best_flight = results[0]
        price = getattr(best_flight, "price", None)
        if price is None:
            return None

        return {
            "origin": origin,
            "destination": dest,
            "outbound": outbound,
            "return": return_date,
            "total_price": float(price),
            "currency": CURRENCY,
        }
    except Exception as e:
        log.warning(f"Roundtrip search error {origin}->{dest} ({outbound} to {return_date}): {e}")
        return None


async def search_outbound(executor, origin: str, dest: str) -> List[Dict]:
    start_days, end_days = SEARCH_WINDOW_DAYS
    today = datetime.now().date()
    start_date = (today + timedelta(days=start_days)).strftime("%Y-%m-%d")
    end_date = (today + timedelta(days=end_days)).strftime("%Y-%m-%d")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor, _search_outbound_sync, origin, dest, start_date, end_date
    )


async def search_roundtrip(executor, origin: str, dest: str, outbound: str, return_date: str) -> Optional[Dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor, _search_roundtrip_sync, origin, dest, outbound, return_date
    )


# ============ MAIN PIPELINE ============
async def find_best_deals() -> tuple:
    log.info("=" * 60)
    log.info(f"FLIGHT DEAL SEARCH: {', '.join(ORIGIN_AIRPORTS)} → {', '.join(DESTINATION_AIRPORTS)}")
    log.info("=" * 60)

    executor = ThreadPoolExecutor(max_workers=4)

    # Step 1: Outbound calendar searches
    outbound_tasks = [
        search_outbound(executor, origin, dest)
        for origin in ORIGIN_AIRPORTS
        for dest in DESTINATION_AIRPORTS
    ]
    all_outbound_results = await asyncio.gather(*outbound_tasks)

    all_outbound = []
    roundtrip_tasks = []

    for outbound_results in all_outbound_results:
        if not outbound_results:
            continue

        for d in outbound_results[:3]:
            link = (
                f"https://www.google.com/travel/flights?q=Flights+from+{d['origin']}+to+{d['destination']}"
                f"+on+{d['date']}&curr=USD"
            )
            all_outbound.append({
                "origin": d["origin"],
                "destination": d["destination"],
                "outbound_date": d["date"],
                "outbound_price": d["price"],
                "currency": d["currency"],
                "booking_link": link,
            })

            # Queue top dates for roundtrip check
            for ret_date in generate_return_dates(d["date"]):
                roundtrip_tasks.append(
                    search_roundtrip(executor, d["origin"], d["destination"], d["date"], ret_date)
                )

    # Step 2: Execute roundtrip searches
    all_combos = []
    if roundtrip_tasks:
        log.info(f"Executing {len(roundtrip_tasks)} roundtrip searches...")
        roundtrip_results = await asyncio.gather(*roundtrip_tasks)
        all_combos = [r for r in roundtrip_results if r is not None]

    executor.shutdown(wait=False)

    all_outbound.sort(key=lambda x: x["outbound_price"])
    top_outbound = all_outbound[:5]

    all_combos.sort(key=lambda x: x["total_price"])
    top_combos = all_combos[:5]

    deals = []
    for combo in top_combos:
        link = (
            f"https://www.google.com/travel/flights?q=Flights+from+{combo['origin']}"
            f"+to+{combo['destination']}+on+{combo['outbound']}+through+{combo['return']}&curr=USD"
        )
        deal = FlightDeal(
            origin=combo["origin"],
            destination=combo["destination"],
            outbound_date=combo["outbound"],
            return_date=combo["return"],
            outbound_price=combo["total_price"] * 0.5,
            return_price=combo["total_price"] * 0.5,
            total_price=combo["total_price"],
            airline="Google Flights",
            duration_out="-",
            duration_ret="-",
            stops_out=0,
            stops_ret=0,
            baggage_info="Carry-on only",
            booking_link=link,
            source="google_flights",
        )
        deals.append(deal)

    return deals, top_outbound


def format_telegram_message(deals: List, top_outbound: List[Dict]) -> str:
    if not deals and not top_outbound:
        return "📭 No flight deals found."

    lines = [
        "✈️ <b>Flight Deals: NYC → CA + Yellowstone</b>",
        "",
        "🔻 <b>Top Outbound (One-Way)</b>:",
    ]
    for i, o in enumerate(top_outbound, 1):
        lines.append(f"  {i}. <a href=\"{o['booking_link']}\">{o['origin']}→{o['destination']}</a> on {o['outbound_date']} — <b>${o['outbound_price']:,.0f}</b>")

    lines.append("\n🔄 <b>Top Roundtrips</b>:")
    for i, d in enumerate(deals, 1):
        lines.append(f"  {i}. <b>${d.total_price:,.0f}</b> | {d.origin}→{d.destination} | Out: {d.outbound_date} | Ret: {d.return_date}")
        lines.append(f"      <a href=\"{d.booking_link}\">Book on Google Flights</a>")

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram environment variables not set.")
        return False

    import urllib.request
    import urllib.parse

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status in (200, 204)
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False


async def main():
    log.info("🚀 Starting Flight Deal Finder")
    try:
        deals, top_outbound = await find_best_deals()
        if deals or top_outbound:
            message = format_telegram_message(deals, top_outbound)
            send_telegram(message)
            log.info(f"✅ Finished. Deals found: {len(deals)}")
        else:
            log.info("📭 No deals found")
    except Exception as e:
        log.error(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
