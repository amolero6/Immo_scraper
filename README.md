# Immo Scraper 🏠

Automated real-estate monitoring system for **Sant Cugat del Vallès** and **Cerdanyola del Vallès**.

The system scrapes property listings from Idealista (via Apify) and local agencies (via Playwright), stores them in a local SQLite database, tracks price history, and sends Telegram alerts when a matching opportunity appears or a price drops.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Configuration](#configuration)
   - [Environment variables](#environment-variables)
   - [Create a Telegram bot](#create-a-telegram-bot)
   - [Get your Apify token](#get-your-apify-token)
5. [Adapting the local scraper](#adapting-the-local-scraper)
   - [Adding a new agency](#adding-a-new-agency)
   - [Finding the right CSS selectors](#finding-the-right-css-selectors)
6. [Running the scraper](#running-the-scraper)
   - [Manual run](#manual-run)
   - [Scheduled run with cron](#scheduled-run-with-cron)
7. [Alert criteria](#alert-criteria)
8. [Module reference](#module-reference)
9. [Database schema](#database-schema)
10. [Troubleshooting](#troubleshooting)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         main.py                             │
│                       (orchestrator)                        │
└──────────┬────────────────────────┬────────────────────────┘
           │                        │
           ▼                        ▼
┌──────────────────┐    ┌───────────────────────┐
│ scraper_local.py │    │  scraper_apify.py      │
│  (Playwright)    │    │  (Apify → Idealista)   │
│  amat.es, etc.   │    │                        │
└────────┬─────────┘    └───────────┬───────────┘
         │                          │
         └──────────┬───────────────┘
                    ▼
         ┌──────────────────┐
         │   database.py    │
         │   (SQLite3)      │
         │  properties      │
         │  price_history   │
         └────────┬─────────┘
                  │  new listing / price drop
                  ▼
         ┌──────────────────┐
         │ telegram_bot.py  │
         │  (HTTP alerts)   │
         └──────────────────┘
```

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.11+** | Check with `python --version` |
| **pip** | Comes with Python |
| **Chromium** (for Playwright) | Installed in the setup step below |
| **Apify account** | Free tier is enough; sign up at [apify.com](https://apify.com) |
| **Telegram account** | Needed to create the alert bot |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/amolero6/Immo_scraper.git
cd Immo_scraper

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install the Playwright browser (Chromium)
playwright install chromium
```

---

## Configuration

### Environment variables

Copy the example file and fill in your real values:

```bash
cp .env.example .env
```

Then open `.env` in your editor:

```dotenv
# Telegram bot credentials
TELEGRAM_BOT_TOKEN=123456789:ABCDEFabcdef...
TELEGRAM_CHAT_ID=-100123456789

# Apify API token
APIFY_API_TOKEN=apify_api_xxxxxxxxxxxx

# Feature flags – set to "false" to disable a scraper
ENABLE_LOCAL_SCRAPER=true
ENABLE_APIFY_SCRAPER=true
```

> ⚠️ The `.env` file is listed in `.gitignore` and will **never** be committed.

---

### Create a Telegram bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts (choose a name and username).
3. Copy the **token** you receive (looks like `123456789:ABCDEFabcdef…`) into `TELEGRAM_BOT_TOKEN`.
4. To get your **chat ID**:
   - For a private chat: message **@userinfobot** – it replies with your numeric user ID.
   - For a group or channel: add the bot to the group, send a message, then open
     `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and look for
     `"chat":{"id":...}`.
5. Set that value as `TELEGRAM_CHAT_ID`.

---

### Get your Apify token

1. Sign in at [console.apify.com](https://console.apify.com).
2. Go to **Settings → Integrations → API tokens**.
3. Click **+ Create new token**, copy it, and paste it into `APIFY_API_TOKEN`.

The scraper uses the community actor **`canadesk/idealista-scraper`**.
You can swap it for any other Idealista actor by changing `IDEALISTA_ACTOR_ID`
in `scraper_apify.py`. Check the actor's page on Apify Console for supported
input fields.

---

## Adapting the local scraper

`scraper_local.py` contains a `SCRAPERS` list. Each entry describes one agency website.
The template ships with a stub for **amat.es** — you need to fill in the real CSS selectors
before running.

### Adding a new agency

Open `scraper_local.py` and add a new dict to the `SCRAPERS` list:

```python
SCRAPERS: List[Dict] = [
    {
        "source": "amat",               # short name used as ID prefix
        "base_url": "https://www.amat.es/ca/compra/habitatge?q=Sant+Cugat",
        "listing_selector": "article.property-card",   # wraps each result
        "title_selector":   "h2.property-title",       # text of the listing title
        "price_selector":   "span.price",              # price text e.g. "450.000 €"
        "link_selector":    "a.property-link",         # <a href="...">
        "rooms_selector":   "span.rooms",              # e.g. "4 hab."
        "bathrooms_selector": "span.bathrooms",        # e.g. "2 baños"
        "sqm_selector":     "span.sqm",                # e.g. "120 m²"
        "pool_keyword":     "piscina",                 # text to detect pool
        "ac_keyword":       "aire condicionat",        # text to detect A/C
        "next_page_selector": "a[rel='next']",         # pagination link
        "max_pages": 10,
    },
    # --- Add more agencies below ---
    {
        "source": "finques_reig",
        "base_url": "https://www.finquesreig.com/venta/...",
        # ... selectors ...
    },
]
```

### Finding the right CSS selectors

1. Open the agency website in **Chrome** or **Firefox**.
2. Right-click a property card → **Inspect**.
3. Hover over elements in the DevTools panel to identify which tag / class wraps
   the title, price, number of rooms, etc.
4. Use the DevTools **Console** to test a selector before putting it in the config:
   ```js
   document.querySelectorAll("article.property-card")
   ```
5. Confirm the selector returns the expected elements, then copy it into `SCRAPERS`.

---

## Running the scraper

### Manual run

Make sure your virtual environment is active, then:

```bash
python main.py
```

You will see structured log output:

```
2026-04-29T08:00:01  INFO     __main__  ============================================================
2026-04-29T08:00:01  INFO     __main__  Immo Scraper run started.
2026-04-29T08:00:02  INFO     __main__  Running local (Playwright) scrapers …
2026-04-29T08:00:15  INFO     __main__  Local scrapers returned 42 properties.
2026-04-29T08:00:15  INFO     __main__  Running Apify (Idealista) scraper …
2026-04-29T08:01:10  INFO     __main__  Apify scraper returned 187 properties.
2026-04-29T08:01:11  INFO     __main__  Market snapshot – avg price: 612340 €  |  median price: 589000 €
2026-04-29T08:01:11  INFO     __main__  Run complete. New: 3 | Price drops: 1 | Alerts sent: 2
```

### Scheduled run with cron

To run automatically every day at **08:00**, open your crontab:

```bash
crontab -e
```

Add this line (replace the paths with your own):

```cron
0 8 * * * /Users/yourname/Immo_scraper/.venv/bin/python /Users/yourname/Immo_scraper/main.py >> /tmp/immo_scraper.log 2>&1
```

> **Tip:** On macOS, `cron` may need Full Disk Access. Alternatively, use a
> **launchd plist** or a simple `launchctl` job for more reliability.

To verify it was saved:

```bash
crontab -l
```

---

## Alert criteria

An alert is sent to Telegram only when **all** of the following conditions are met:

| Criterion | Default value | Where to change |
|-----------|---------------|-----------------|
| Price | < 700 000 € | `MAX_PRICE` in `main.py` |
| Rooms | ≥ 3 | `MIN_ROOMS` in `main.py` |
| Bathrooms | ≥ 2 | `MIN_BATHROOMS` in `main.py` |
| Price vs market | Below run average | Automatic (based on scraped data) |

Alerts are triggered by two events:
- **New listing**: a property ID appears in the database for the first time.
- **Price drop**: a property's price is lower than in the previous run.

---

## Module reference

| File | Responsibility |
|------|---------------|
| `main.py` | Orchestrator; runs all scrapers, upserts results, sends alerts. Entry-point for cron. |
| `database.py` | SQLite3 helper; creates tables, upserts properties, records price history, marks inactive listings. |
| `telegram_bot.py` | Sends Markdown-formatted messages to a Telegram chat via the Bot API. |
| `scraper_local.py` | Playwright headless scraper for local agency websites. Configurable per agency via `SCRAPERS`. |
| `scraper_apify.py` | Calls an Apify actor to scrape Idealista. Normalises raw actor output to the DB schema. |
| `requirements.txt` | Python dependencies. |
| `.env.example` | Template for secrets – copy to `.env` and fill in. |

---

## Database schema

The SQLite database is stored in `immo_scraper.db` next to `main.py`.

### `properties`

| Column | Type | Notes |
|--------|------|-------|
| `property_id` | TEXT PK | e.g. `idealista_12345678`, `amat_piso-cugat` |
| `source` | TEXT | `idealista`, `amat`, … |
| `title` | TEXT | Listing headline |
| `url` | TEXT | Direct link to the listing |
| `price` | INTEGER | Euros |
| `rooms` | INTEGER | Number of bedrooms |
| `bathrooms` | INTEGER | Number of bathrooms |
| `sqm` | INTEGER | Built area in m² |
| `has_pool` | INTEGER | `1` = yes, `0` = no |
| `has_ac` | INTEGER | `1` = yes, `0` = no |
| `orientation` | TEXT | e.g. `Sud`, `Est` |
| `first_seen` | TEXT | ISO-8601 UTC datetime |
| `last_seen` | TEXT | ISO-8601 UTC datetime |
| `status` | TEXT | `active` or `inactive` |

### `price_history`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto-increment |
| `property_id` | TEXT | FK → `properties.property_id` |
| `price` | INTEGER | Price at this point in time |
| `date` | TEXT | ISO-8601 UTC datetime |

A row is appended every time a price change is detected, including the initial insertion.
Use this table to plot price evolution over time.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'playwright'`**
→ Make sure your virtual environment is active (`source .venv/bin/activate`) and run
`pip install -r requirements.txt`.

**`playwright install chromium` fails**
→ Try `playwright install --with-deps chromium` to also install system dependencies.

**`EnvironmentError: TELEGRAM_BOT_TOKEN … must be set`**
→ Check that `.env` exists and contains the correct keys. Run `cat .env` to verify.

**`EnvironmentError: APIFY_API_TOKEN is not set`**
→ Same as above for the Apify token. Also confirm the token is valid in the Apify Console.

**No listings found by the local scraper**
→ The CSS selectors in `scraper_local.py` need to be updated for the current version of
the agency website. Follow the [Finding the right CSS selectors](#finding-the-right-css-selectors) guide.

**Apify actor fails with status `FAILED`**
→ Check the run logs in [Apify Console](https://console.apify.com) → **Actors** → **Runs**.
The most common causes are: insufficient Apify credits, the actor being deprecated, or
the proxy group `RESIDENTIAL` not being available on your plan.

**Property keeps appearing as "new" on every run**
→ The `property_id` is derived from the URL. If the agency website changes its URL
structure between runs, the same listing will look new. Inspect `_build_id()` in
`scraper_local.py` and adjust the slug extraction logic to produce a stable ID.
