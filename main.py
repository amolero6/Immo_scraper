"""
main.py
-------
Orchestrator for the Immo Scraper project.

Run manually or via cron:
  python main.py
  # or with cron (every day at 08:00):
  # 0 8 * * * /path/to/venv/bin/python /path/to/immo_scraper/main.py >> /var/log/immo_scraper.log 2>&1

Environment variables (see .env.example):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, APIFY_API_TOKEN
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Dict

import pandas as pd
from dotenv import load_dotenv

# Load environment variables before importing project modules
load_dotenv()

from database import init_db, upsert_property, mark_inactive, get_property, get_price_history
from telegram_bot import send_alert, format_new_property_message, format_price_drop_message
from scraper_local import scrape_all_local
from scraper_apify import scrape_idealista
from run_idealista_headful import scrape_idealista_properties
from matching import best_similarity_match
from similarity_config import FAVORITE_PROFILES, MIN_SIMILARITY_SCORE, ALERT_LOCATION_TERMS

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filter criteria (edit to match your requirements)
# ---------------------------------------------------------------------------

MAX_PRICE: int = 700_000      # €
MIN_ROOMS: int = 3
MIN_BATHROOMS: int = 2
MIN_SIMILARITY_SCORE_ALERT: int = MIN_SIMILARITY_SCORE

# ---------------------------------------------------------------------------
# Feature flags (set to False to disable individual scrapers)
# ---------------------------------------------------------------------------

ENABLE_LOCAL_SCRAPER: bool = True
ENABLE_APIFY_SCRAPER: bool = False
ENABLE_IDEALISTA_LOCAL: bool = False


# ---------------------------------------------------------------------------
# Main orchestration logic
# ---------------------------------------------------------------------------

def run() -> None:
    """
    Full scraping pipeline:
      1. Initialise the database.
      2. Fetch properties from all configured sources.
      3. Upsert each property; detect new listings and price drops.
      4. Mark unseen properties as inactive.
      5. Send Telegram alerts for relevant events.
    """
    logger.info("=" * 60)
    logger.info("Immo Scraper run started.")
    logger.info("=" * 60)

    # ---- Step 1: Database ----
    init_db()

    # ---- Step 2: Collect raw data ----
    raw_properties: List[Dict] = []

    if ENABLE_LOCAL_SCRAPER:
        logger.info("Running local (Playwright) scrapers …")
        try:
            local_props = scrape_all_local()
            logger.info("Local scrapers returned %d properties.", len(local_props))
            raw_properties.extend(local_props)
        except Exception as exc:
            logger.error("Local scrapers failed: %s", exc, exc_info=True)

    if ENABLE_APIFY_SCRAPER:
        logger.info("Running Apify (Idealista) scraper …")
        try:
            apify_props = scrape_idealista()
            logger.info("Apify scraper returned %d properties.", len(apify_props))
            raw_properties.extend(apify_props)
        except Exception as exc:
            logger.error("Apify scraper failed: %s", exc, exc_info=True)

    if ENABLE_IDEALISTA_LOCAL:
        logger.info("Running local headful Idealista scraper …")
        try:
            idealista_local_props = scrape_idealista_properties()
            logger.info("Local Idealista scraper returned %d properties.", len(idealista_local_props))
            raw_properties.extend(idealista_local_props)
        except Exception as exc:
            logger.error("Local Idealista scraper failed: %s", exc, exc_info=True)

    logger.info("Total properties fetched this run: %d", len(raw_properties))

    if not raw_properties:
        logger.warning("No properties retrieved. Exiting without updating the database.")
        return

    # ---- Step 3: Upsert & detect events ----
    seen_ids: List[str] = []
    new_properties: List[Dict] = []
    price_drops: List[tuple] = []    # (prop_dict, old_price)

    for prop in raw_properties:
        pid = prop.get("property_id")
        if not pid:
            logger.warning("Property without ID encountered – skipping: %s", prop)
            continue

        similarity = best_similarity_match(prop, FAVORITE_PROFILES)
        prop["similarity_score"] = similarity["similarity_score"]
        prop["similarity_profile"] = similarity["similarity_profile"]

        # Snapshot of the current DB state before upsert
        existing = get_property(pid)

        action = upsert_property(prop)
        seen_ids.append(pid)

        if action == "inserted":
            new_properties.append(prop)
        elif action == "updated" and existing:
            old_price = existing.get("price")
            new_price = prop.get("price")
            if old_price and new_price and new_price < old_price:
                price_drops.append((prop, old_price))

    # ---- Step 4: Mark unseen as inactive ----
    inactive_count = mark_inactive(seen_ids)
    logger.info("Properties marked inactive this run: %d", inactive_count)

    # ---- Step 5: Compute market averages with Pandas ----
    df = pd.DataFrame(raw_properties)
    if not df.empty and "price" in df.columns:
        avg_price = df["price"].dropna().mean()
        median_price = df["price"].dropna().median()
        logger.info(
            "Market snapshot – avg price: %.0f €  |  median price: %.0f €",
            avg_price,
            median_price,
        )
    else:
        avg_price = None
        median_price = None

    # ---- Step 6: Send Telegram alerts ----
    alerts_sent = 0

    for prop in new_properties:
        if _matches_criteria(prop, avg_price, MIN_SIMILARITY_SCORE_ALERT):
            msg = format_new_property_message(prop)
            if send_alert(msg):
                alerts_sent += 1

    for prop, old_price in price_drops:
        if _matches_criteria(prop, avg_price, MIN_SIMILARITY_SCORE_ALERT):
            msg = format_price_drop_message(prop, old_price)
            if send_alert(msg):
                alerts_sent += 1

    logger.info(
        "Run complete. New: %d | Price drops: %d | Alerts sent: %d",
        len(new_properties),
        len(price_drops),
        alerts_sent,
    )
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Filtering helper
# ---------------------------------------------------------------------------

def _matches_criteria(
    prop: Dict,
    avg_price: float | None = None,
    min_similarity_score: int = 0,
) -> bool:
    """
    Return True if *prop* satisfies the monitoring criteria:
      - price < MAX_PRICE
      - rooms >= MIN_ROOMS
      - bathrooms >= MIN_BATHROOMS
      - similarity score >= min_similarity_score
      - location must mention one of the configured alert terms
      - (optional) price below market average
    """
    price = prop.get("price")
    rooms = prop.get("rooms")
    bathrooms = prop.get("bathrooms")
    similarity_score = prop.get("similarity_score") or 0

    if price is None or rooms is None or bathrooms is None:
        return False

    if price >= MAX_PRICE:
        return False
    if rooms < MIN_ROOMS:
        return False
    if bathrooms < MIN_BATHROOMS:
        return False
    if similarity_score < min_similarity_score:
        logger.debug(
            "Property '%s' excluded: similarity %s < min %s",
            prop.get("property_id"),
            similarity_score,
            min_similarity_score,
        )
        return False
    if ALERT_LOCATION_TERMS and _has_location_hints(prop) and not _matches_location(
        prop, ALERT_LOCATION_TERMS
    ):
        logger.debug(
            "Property '%s' excluded: no alert location terms matched",
            prop.get("property_id"),
        )
        return False

    # Bonus filter: alert only if price is below the market average (if known)
    if avg_price is not None and price >= avg_price:
        logger.debug(
            "Property '%s' excluded: price %d >= avg %.0f",
            prop.get("property_id"),
            price,
            avg_price,
        )
        return False

    return True


def _matches_location(prop: Dict, location_terms: List[str]) -> bool:
    haystack = _normalize_text(
        " ".join(
            str(value)
            for value in [
                prop.get("title", ""),
                prop.get("city", ""),
                prop.get("district", ""),
                prop.get("neighborhood", ""),
                prop.get("postal_code", ""),
                prop.get("url", ""),
                prop.get("orientation", ""),
            ]
            if value
        )
    )
    return any(_normalize_text(term) in haystack for term in location_terms if term)


def _has_location_hints(prop: Dict) -> bool:
    return any(
        prop.get(field)
        for field in ("city", "district", "neighborhood", "postal_code", "latitude", "longitude")
    )


def _normalize_text(text: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", text)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.lower().split())


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run()
