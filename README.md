# 🏠 Real Estate Finder Agent

A Fetch.ai uAgent that finds real estate listings and populates a Google Sheet automatically.
Built with the Claude Agent SDK + HomeHarvest scraper.

## Architecture

```
User Input (natural language)
        ↓
Fetch.ai uAgent  (uagent_bridge.py)
        ↓
Claude (workflow.py)   ← parses intent into structured filters
        ↓
HomeHarvest (scraper.py)   ← scrapes Redfin + Realtor.com
        ↓
Google Sheets (sheets.py)  ← creates & populates spreadsheet
        ↓
Returns sheet URL to user
```

## Data Source: Why HomeHarvest?

HomeHarvest is the best free option for a project like this:

| Option | Pros | Cons |
|---|---|---|
| **HomeHarvest** ✅ | Free, 3 sources at once, structured data | Zillow blocks often |
| Raw scraping | Free | Breaks constantly, hard to maintain |
| ScraperAPI / Oxylabs | Reliable | Paid ($50+/mo) |
| Official APIs | Reliable | Restricted access, paid |

HomeHarvest scrapes **Redfin** and **Realtor.com** reliably with no API key.
It returns clean pandas DataFrames with price, sqft, beds, baths, and listing URLs.

## Project Structure

```
real-estate-finder/
├── .env.example          # Copy to .env and fill in keys
├── requirements.txt      # Python dependencies
├── scraper.py            # HomeHarvest wrapper — fetches listings
├── sheets.py             # Google Sheets integration — creates the spreadsheet
├── workflow.py           # Core logic — ties scraper + sheets together
├── uagent_bridge.py      # Fetch.ai uAgent layer
├── test_workflow.py      # Test without Fetch.ai (start here!)
└── run_agent.sh          # Launch the agent
```

## Setup

### 1. Install dependencies
```bash
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Set up environment variables
```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```
Get your Anthropic API key from: https://console.anthropic.com/

### 3. Set up Google Sheets (one-time, ~5 minutes)

1. Go to https://console.cloud.google.com/
2. Create a new project (or select existing)
3. Enable **Google Sheets API** and **Google Drive API**
4. Go to **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
5. Select **Desktop App**, download the JSON
6. Save it as `credentials.json` in this folder
7. Run the auth setup:
   ```bash
   python sheets.py
   ```
   A browser window will open — log in and authorize.
   A `token.json` file will be saved. You won't need to do this again.

### 4. Test the workflow
```bash
python test_workflow.py
```
This runs the full pipeline without the Fetch.ai layer.
You should see a Google Sheets URL printed at the end.

### 5. Run the full agent
```bash
bash run_agent.sh
# or: python uagent_bridge.py
```

## Example Queries

The agent understands natural language:

- `"3 bedroom house in Austin TX under 700000"`
- `"2 bed apartment for rent in San Diego CA"`
- `"condo in Miami FL between 400k and 800k"`
- `"studio apartment for rent in Chicago under 2000 a month"`
- `"4 bed family home in suburbs of Seattle WA"`

## Troubleshooting

**403 Forbidden from Redfin/Realtor**: You've been rate-limited. Wait a few minutes and try again. The scraper uses `realtor.com` + `redfin` (Zillow is excluded by default because it blocks too aggressively).

**No results found**: Try broadening your search — wider price range, fewer filters, or a larger city.

**Google Sheets auth error**: Delete `token.json` and run `python sheets.py` again to re-authenticate.