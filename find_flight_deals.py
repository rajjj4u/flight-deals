import os
import sys
import logging
from datetime import datetime, timedelta
import httpx
from fli import FlightSearch

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("FlightDealFinder")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ORIGINS = ["JFK", "LGA", "EWR"]
DESTINATIONS = ["LAX", "SFO", "SAN", "SJC", "OAK", "SMF", "BUR", "ONT", "PSP", "JAC"]

# Search parameters
TRIP_DAYS = [4, 7]           # Search for 4-day and 7-day trips
LOOKAHEAD_DAYS = 60          # Look for flights over the next 60 days
PRICE_THRESHOLD = 250        # Notify if flight price is below this amount ($)

# Telegram credentials from GitHub Secrets / Env Vars
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram_message(message: str) -> None:
    """Send alert message to Telegram bot/channel."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Telegram credentials not set. Skipping Telegram alert.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        response = httpx.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("📱 Telegram alert sent successfully!")
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message: {e}")


def search_deals():
    """Main function to search flights across origins and destinations."""
    logger.info("🚀 Starting Flight Deal Finder")
    logger.info("=" * 60)
    logger.info(
        f"FLIGHT DEAL SEARCH: {', '.join(ORIGINS)} → {', '.join(DESTINATIONS)}"
    )
    logger.info("=" * 60)

    today = datetime.today()
    deals_found = []

    for origin in ORIGINS:
        for destination in DESTINATIONS:
            # Generate date ranges for trip durations
            for days_ahead in range(14, LOOKAHEAD_DAYS, 7):
                dep_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                
                for trip_duration in TRIP_DAYS:
                    ret_date = (today + timedelta(days=days_ahead + trip_duration)).strftime("%Y-%m-%d")

                    try:
                        # FlightSearch accepts direct YYYY-MM-DD date strings
                        search = FlightSearch(
                            origin=origin,
                            destination=destination,
                            departure_date=dep_date,
                            return_date=ret_date,
                        )
                        results = search.get()

                        if not results or not getattr(results, "flights", None):
                            continue

                        for flight in results.flights:
                            price = getattr(flight, "price", None)
                            if price and price <= PRICE_THRESHOLD:
                                deal_info = (
                                    f"✈️ *Flight Deal Found!*\n"
                                    f"• *Route:* {origin} ➔ {destination}\n"
                                    f"• *Dates:* {dep_date} to {ret_date}\n"
                                    f"• *Price:* ${price}\n"
                                    f"• *Airline:* {getattr(flight, 'airline', 'N/A')}\n"
                                )
                                logger.info(f"🎉 Deal found: {origin} -> {destination} on {dep_date} for ${price}")
                                deals_found.append(deal_info)

                    except Exception as e:
                        # Catch individual route/date search errors without crashing execution
                        logger.debug(f"Search failed for {origin}->{destination} ({dep_date}): {e}")

    # Summary & Alerting
    if deals_found:
        summary_msg = f"🔥 *Found {len(deals_found)} Flight Deals!*\n\n" + "\n---\n".join(deals_found[:10])
        send_telegram_message(summary_msg)
    else:
        logger.info("ℹ️ Search completed. No deals matching price threshold were found.")


if __name__ == "__main__":
    try:
        search_deals()
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        sys.exit(1)
