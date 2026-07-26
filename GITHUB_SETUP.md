# Flight Deal Finder - GitHub Setup Instructions

## Quick Setup (Manual - No CLI needed)

### 1. Create GitHub Repository

1. Go to: **https://github.com/new**
2. Repository name: `flight-deals`
3. Owner: `rajjj4u`
4. Public ✓
5. **Don't** initialize with README, .gitignore, or license
6. Click **"Create repository"**

### 2. Push Your Code

```bash
cd /Users/rajusiripuram/.hermes/scripts/flight_deals
git remote add origin https://github.com/rajjj4u/flight-deals.git
git branch -M main
git push -u origin main
```

### 3. Add Discord Webhook as GitHub Secret

1. Go to: **https://github.com/rajjj4u/flight-deals/settings/secrets/actions**
2. Click **"New repository secret"**
3. **Name**: `DISCORD_WEBHOOK_URL`
4. **Secret**: Paste your Discord webhook URL (format: `https://discord.com/api/webhooks/XXXXXX/YYYYYY`)
5. Click **"Add secret"**

### 4. Verify It Works

1. Go to: **https://github.com/rajjj4u/flight-deals/actions**
2. Click **"Flight Deals EWR/JFK → HYD"** workflow
3. Click **"Run workflow"** → **"Run workflow"**
3. Watch the run - it should complete and send deals to Discord

### 5. Automatic Daily Runs

The workflow runs **automatically every day at 9:00 AM UTC** (defined in `.github/workflows/flight-deals.yml`).

You can change the schedule by editing the cron expression:
- `0 9 * * *` = 9:00 AM UTC daily
- `0 14 * * *` = 2:00 PM UTC (10 AM EDT)
- `0 13 * * *` = 1:00 PM UTC (9 AM EDT)

## Files in This Project

| File | Purpose |
|------|---------|
| `find_flight_deals.py` | Main flight search script |
| `requirements.txt` | Python dependencies (aiohttp) |
| `.github/workflows/flight-deals.yml` | GitHub Actions workflow |
| `README.md` | Documentation |
| `.gitignore` | Git ignore rules |
| `flight_deals_state.json` | **Auto-generated** - tracks sent deals (committed by workflow) |

## How It Works

1. **Daily at 9 AM UTC**: GitHub Actions triggers the workflow
2. **Installs Python + deps**: Sets up environment
3. **Restores state**: Checks out `flight_deals_state.json` from repo
4. **Searches flights**: Finds EWR/JFK → HYD deals (2-3 months out, weekends/Mon/Fri, 4-5 week trips, 2 checked + 1 carry-on)
5. **Deduplicates**: Skips deals already sent (using hash of dates+price+airline)
6. **Sends to Discord**: Posts top 5 deals as rich embeds
7. **Commits state**: Pushes updated `flight_deals_state.json` back to repo

## Customization

Edit `find_flight_deals.py` to change:
- `ORIGIN_AIRPORTS = ["EWR", "JFK"]` - Add/remove airports
- `DESTINATION = "HYD"` - Change destination
- `SEARCH_WINDOW_DAYS = (60, 90)` - Search 2-3 months out
- `VALID_DAYS = {4, 5, 6, 0}` - Valid departure days (Fri/Sat/Sun/Mon)
- `RETURN_WEEKS = (4, 5)` - Trip duration 4-5 weeks
- `MAX_DEALS = 5` - Number of deals to send

## Need Help?

- **Discord webhook**: Channel Settings → Integrations → Webhooks → New Webhook
- **GitHub Actions logs**: Click any workflow run to see detailed output
- **State file**: Check `flight_deals_state.json` in repo to see sent deals

## License

MIT - Feel free to fork and modify!