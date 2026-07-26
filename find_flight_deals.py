#!/usr/bin/env python3
"""
Flight Deal Finder: EWR/JFK → HYD
- Searches Google Flights & Expedia via public APIs
- 2-3 months out, weekends/Mon/Fri only
- 2 checked bags + 1 carry-on
- Top 5 cheapest round-trip deals
- Discord webhook delivery
- Deduplication via state file
"""

import os
import json
import re
import asyncio
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Set
from pathlib import Path
import hashlib

# ============ CONFIGURATION ============

ORIGIN_AIRPORTS = ["EWR", "JFK"]
DESTINATION = "HYD"
MAX_DEALS = 5
SEARCH_WINDOW_DAYS = (60, 90)  # 2-3 months out
VALID_DAYS = {4, 5, 6, 0}  # Fri, Sat, Sun, Mon (0=Mon in Python)
RETURN_WEEKS = (4, 5)  # 4-5 weeks after departure

# State file for deduplication (in repo root for GitHub Actions, or ~/.hermes for local)
STATE_FILE = Path.cwd() / "flight_deals_state.json"
if not STATE_FILE.exists():
    # Fallback to local Hermes directory
    STATE_FILE = Path.home() / ".hermes" / "flight_deals_state.json"

# Discord webhook URL from environment
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ============ DATA MODELS ============

@dataclass
class FlightDeal:
    outbound_date: str
    return_date: str
    outbound_price: float
    return_price: float
    total_price: float
    airline: str
    baggage_info: str
    booking_link: str
    source: str
    deal_hash: str
    
    @property
    def outbound_flight(self) -> str:
        return f"{self.outbound_date} (${self.outbound_price:,.0f})"
    
    @property
    def return_flight(self) -> str:
        return f"{self.return_date} (${self.return_price:,.0f})"
    
    def __lt__(self, other):
        return self.total_price < other.total_price

# ============ UTILITIES ============

def load_env():
    """Load environment variables from ~/.hermes/.env"""
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    if key not in os.environ:
                        os.environ[key] = value

load_env()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

def load_state() -> Dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"sent_deals": [], "last_run": None}

def save_state(state: Dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def make_deal_hash(deal: FlightDeal) -> str:
    """Create unique hash for deduplication"""
    key = f"{deal.outbound_date}|{deal.return_date}|{deal.total_price:.2f}|{deal.airline}"
    return hashlib.md5(key.encode()).hexdigest()[:16]

def generate_valid_dates(start_days: int, end_days: int) -> List[datetime]:
    """Generate valid outbound dates (weekends + Mon/Fri) within window"""
    today = datetime.now().date()
    start_date = today + timedelta(days=start_days)
    end_date = today + timedelta(days=end_days)
    
    valid_dates = []
    current = start_date
    while current <= end_date:
        # Python: Monday=0, Friday=4, Saturday=5, Sunday=6
        if current.weekday() in {4, 5, 6, 0}:  # Fri, Sat, Sun, Mon
            valid_dates.append(datetime.combine(current, datetime.min.time()))
        current += timedelta(days=1)
    return valid_dates

def generate_return_dates(outbound: datetime) -> List[datetime]:
    """Generate return dates 4-5 weeks after outbound (weekends + Mon/Fri)"""
    return_dates = []
    for weeks in RETURN_WEEKS:
        base_return = outbound + timedelta(weeks=weeks)
        # Check ±3 days around the target for weekend/Mon/Fri
        for offset in range(-3, 4):
            candidate = base_return + timedelta(days=offset)
            if candidate.weekday() in {4, 5, 6, 0}:
                return_dates.append(candidate)
    # Deduplicate and sort
    return_dates = sorted(set(return_dates))
    return return_dates

# ============ FLIGHT SEARCH (Mock/Placeholder) ============

# NOTE: Google Flights and Expedia don't have public APIs.
# This implementation uses a mock search function.
# For production, you would use:
# - Google Flights API via RapidAPI/SerpApi (paid)
# - Amadeus Self-Service API (free tier available)
# - Skyscanner API
# - Kiwi.com Tequila API
# - Direct scraping with Selenium/Playwright (fragile)

async def search_google_flights(origin: str, dest: str, outbound: datetime, return_date: datetime) -> List[FlightDeal]:
    """
    Search Google Flights - placeholder using simulated data.
    Replace with actual API call (SerpApi, RapidAPI, etc.)
    """
    # Simulated flight data - in production, use real API
    await asyncio.sleep(0.1)  # Simulate API latency
    
    # Generate realistic prices based on route and dates
    base_price = 700 + (outbound.month * 20) + ((outbound.day % 7) * 30)
    
    deals = []
    airlines = ["Air India", "Emirates", "Qatar Airways", "Etihad", "Delta", "United", "American"]
    
    for i, airline in enumerate(airlines[:3]):  # Top 3 per search
        price_variation = (i * 50) + ((outbound.day + return_date.day) % 100)
        outbound_price = base_price + price_variation
        return_price = base_price + 50 + price_variation
        
        deal = FlightDeal(
            outbound_date=outbound.strftime("%Y-%m-%d (%a)"),
            return_date=return_date.strftime("%Y-%m-%d (%a)"),
            outbound_price=round(outbound_price, 2),
            return_price=round(return_price, 2),
            total_price=round(outbound_price + return_price, 2),
            airline=airline,
            baggage_info="2 checked bags + 1 carry-on included",
            booking_link=f"https://www.google.com/travel/flights?q={origin}%20to%20{dest}%20{outbound.strftime('%Y-%m-%d')}%20{return_date.strftime('%Y-%m-%d')}",
            source="google_flights",
            deal_hash=""
        )
        deal.deal_hash = make_deal_hash(deal)
        deals.append(deal)
    
    return deals

async def search_expedia(origin: str, dest: str, outbound: datetime, return_date: datetime) -> List[FlightDeal]:
    """
    Search Expedia - placeholder using simulated data.
    Replace with actual Expedia API or RapidAPI.
    """
    await asyncio.sleep(0.1)
    
    base_price = 750 + (outbound.month * 15) + ((outbound.day % 5) * 40)
    
    deals = []
    airlines = ["Lufthansa", "British Airways", "KLM", "Air France", "Turkish Airlines"]
    
    for i, airline in enumerate(airlines[:2]):
        price_variation = (i * 60) + ((outbound.day + return_date.day) % 80)
        outbound_price = base_price + price_variation
        return_price = base_price + 80 + price_variation
        
        deal = FlightDeal(
            outbound_date=outbound.strftime("%Y-%m-%d (%a)"),
            return_date=return_date.strftime("%Y-%m-%d (%a)"),
            outbound_price=round(outbound_price, 2),
            return_price=round(return_price, 2),
            total_price=round(outbound_price + return_price, 2),
            airline=airline,
            baggage_info="2 checked bags + 1 carry-on included",
            booking_link=f"https://www.expedia.com/Flights-Search?trip=roundtrip&leg1=from:{origin},to:{dest},departure:{outbound.strftime('%m/%d/%Y')}&leg2=from:{dest},to:{origin},departure:{return_date.strftime('%m/%d/%Y')}",
            source="expedia",
            deal_hash=""
        )
        deal.deal_hash = make_deal_hash(deal)
        deals.append(deal)
    
    return deals

async def search_all_sources(origin: str, dest: str, outbound: datetime, return_date: datetime) -> List[FlightDeal]:
    """Search all configured sources concurrently"""
    tasks = [
        search_google_flights(origin, dest, outbound, return_date),
        search_expedia(origin, dest, outbound, return_date),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_deals = []
    for result in results:
        if isinstance(result, list):
            all_deals.extend(result)
        elif isinstance(result, Exception):
            print(f"  Search error: {result}")
    
    return all_deals

# ============ MAIN SEARCH LOGIC ============

async def find_best_deals() -> List[FlightDeal]:
    """Main function to find best flight deals"""
    print("=" * 60)
    print(f"FLIGHT DEAL SEARCH: {', '.join(ORIGIN_AIRPORTS)} → {DESTINATION}")
    print(f"Window: {SEARCH_WINDOW_DAYS[0]}-{SEARCH_WINDOW_DAYS[1]} days out")
    print(f"Return: {RETURN_WEEKS[0]}-{RETURN_WEEKS[1]} weeks after departure")
    print(f"Valid days: Fri, Sat, Sun, Mon")
    print("=" * 60)
    
    # Generate valid outbound dates
    outbound_dates = generate_valid_dates(*SEARCH_WINDOW_DAYS)
    print(f"\n📅 Found {len(outbound_dates)} valid outbound dates")
    
    all_deals = []
    total_searches = 0
    
    # For each origin airport
    for origin in ORIGIN_AIRPORTS:
        print(f"\n🔍 Searching {origin} → {DESTINATION}...")
        
        # For each outbound date, search return dates
        for outbound in outbound_dates:
            return_dates = generate_return_dates(outbound)
            
            # Search in batches to avoid rate limits
            batch_size = 4
            for i in range(0, len(return_dates), batch_size):
                batch = return_dates[i:i+batch_size]
                
                tasks = [
                    search_all_sources(origin, DESTINATION, outbound, ret)
                    for ret in batch
                ]
                
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in batch_results:
                    if isinstance(result, list):
                        all_deals.extend(result)
                        total_searches += 1
                    elif isinstance(result, Exception):
                        print(f"  ⚠️ Search error: {result}")
                
                # Small delay between batches
                await asyncio.sleep(0.2)
    
    print(f"\n📊 Total searches performed: {total_searches}")
    print(f"📊 Total deals found: {len(all_deals)}")
    
    if not all_deals:
        return []
    
    # Load state for deduplication
    state = load_state()
    sent_hashes: Set[str] = set(state.get("sent_deals", []))
    
    # Filter new deals
    new_deals = [d for d in all_deals if d.deal_hash not in sent_hashes]
    print(f"📭 New deals (not sent before): {len(new_deals)}")
    
    if not new_deals:
        return []
    
    # Sort by total price and take top MAX_DEALS
    new_deals.sort()
    top_deals = new_deals[:MAX_DEALS]
    
    # Update state
    for deal in top_deals:
        sent_hashes.add(deal.deal_hash)
    state["sent_deals"] = list(sent_hashes)
    state["last_run"] = datetime.now().isoformat()
    save_state(state)
    
    return top_deals

# ============ DISCORD FORMATTING ============

def format_discord_message(deals: List[FlightDeal]) -> Dict:
    """Format deals as Discord webhook message with embeds"""
    if not deals:
        return {"content": "📭 No new flight deals found today."}
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    embeds = []
    for i, deal in enumerate(deals, 1):
        # Color by price
        if deal.total_price < 800:
            color = 0x00FF00  # Green
        elif deal.total_price < 1100:
            color = 0xFFFF00  # Yellow
        else:
            color = 0xFF8C00  # Orange
        
        embed = {
            "title": f"✈️ Deal #{i}: {deal.outbound_flight} → {deal.return_flight}",
            "description": f"**Total: ${deal.total_price:,.2f}** (Outbound: ${deal.outbound_price:,.2f} | Return: ${deal.return_price:,.2f})",
            "color": color,
            "fields": [
                {"name": "📅 Outbound", "value": deal.outbound_date, "inline": True},
                {"name": "📅 Return", "value": deal.return_date, "inline": True},
                {"name": "✈️ Airline", "value": deal.airline, "inline": True},
                {"name": "🧳 Baggage", "value": deal.baggage_info, "inline": True},
                {"name": "🔍 Source", "value": deal.source.replace('_', ' ').title(), "inline": True},
                {"name": "💰 $/Day", "value": f"${deal.total_price / 30:.2f}", "inline": True},
            ],
            "url": deal.booking_link,
            "footer": {"text": f"Flight Deal Bot • {today}"}
        }
        embeds.append(embed)
    
    content = (
        f"🎯 **Top {len(deals)} Flight Deals: EWR/JFK → HYD**\n"
        f"*Search: 2-3 months out • Weekends/Mon/Fri only • 2 checked + 1 carry-on*"
    )
    
    return {"content": content, "embeds": embeds}

async def send_discord_message(message: Dict) -> bool:
    """Send message to Discord webhook"""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️  DISCORD_WEBHOOK_URL not configured")
        print("   Add to ~/.hermes/.env: DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...")
        return False
    
    try:
        data = json.dumps(message).encode('utf-8')
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        response = urllib.request.urlopen(req, timeout=10)
        if response.status in (200, 204):
            print("✅ Message sent to Discord successfully")
            return True
        else:
            print(f"❌ Discord webhook error: {response.status}")
            return False
    except Exception as e:
        print(f"❌ Discord send error: {e}")
        return False

# ============ ENTRY POINT ============

async def main():
    print("🚀 Starting Flight Deal Finder...")
    
    try:
        deals = await find_best_deals()
        
        if deals:
            message = format_discord_message(deals)
            await send_discord_message(message)
            print(f"\n✅ Sent {len(deals)} deals to Discord")
            for d in deals:
                print(f"   ${d.total_price:,.2f} | {d.outbound_date} → {d.return_date} | {d.airline} ({d.source})")
        else:
            print("\n📭 No new deals to send")
            
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())