# Flight Deal Finder: EWR/JFK → HYD

Automated daily flight deal finder for New York (EWR/JFK) to Hyderabad (HYD) flights.

## Features

- **Routes**: EWR → HYD and JFK → HYD
- **Date window**: 2-3 months from current date
- **Travel days**: Weekends (Fri/Sat/Sun) + Monday only
- **Trip duration**: 4-5 weeks (return 4-5 weeks after departure)
- **Baggage**: 2 checked bags + 1 carry-on included
- **Sources**: Google Flights & Expedia (via public search)
- **Output**: Top 5 cheapest round-trip deals
- **Delivery**: Discord webhook
- **Deduplication**: Tracks sent deals to avoid duplicates
- **Schedule**: Daily at 9:00 AM UTC via GitHub Actions

## Quick Setup

### 1. Clone & Configure

```bash
git clone <your-repo>
cd flight_deals
```

### 2. Add Discord Webhook

1. Create a Discord webhook in your channel settings
2. Copy the webhook URL
3. Add as GitHub Secret: `DISCORD_WEBHOOK_URL`

### 3. Enable GitHub Actions

The workflow is at `.github/workflows/flight-deals.yml` and runs daily at 9 AM UTC.

### 4. Test Locally

```bash
pip install -r requirements.txt
DISCORD_WEBHOOK_URL="your_webhook_url" python find_flight_deals.py
```

## How It Works

1. **Date Generation**: Creates valid outbound dates (Fri/Sat/Sun/Mon) 60-90 days out
2. **Return Dates**: For each outbound, generates return dates 4-5 weeks later (also Fri/Sat/Sun/Mon)
3. **Search**: Queries Google Flights & Expedia for each date combination
4. **Filtering**: Finds top 5 cheapest round-trip deals
5. **Deduplication**: Uses hash of (dates + price + airline) to avoid re-sending
6. **Delivery**: Sends formatted Discord embed with booking links

## State Persistence

The `flight_deals_state.json` file tracks sent deal hashes and is committed back to the repo by the GitHub Action, ensuring persistence across runs.

## Customization

Edit `find_flight_deals.py` to change:
- `ORIGIN_AIRPORTS` - Add/remove airports
- `DESTINATION` - Change destination
- `SEARCH_WINDOW_DAYS` - Adjust search window
- `VALID_DAYS` - Change valid travel days
- `RETURN_WEEKS` - Adjust trip duration
- `MAX_DEALS` - Number of deals to send

## Requirements

- Python 3.11+
- aiohttp
- Discord webhook URL
- GitHub repository with Actions enabled

## Note on Flight Search APIs

Google Flights and Expedia don't offer free public APIs. This script uses:
- Public search URL scraping (limited, may break)
- For production, consider: Amadeus API (free tier), RapidAPI flight APIs, or Kiwi Tequila API

## License

MIT