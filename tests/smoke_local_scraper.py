"""
Smoke test for local portal scrapers.

This is an integration check, not a unit test. It runs one configured portal
from scraper_local.py against the real website, prints a short extraction
summary, and optionally writes the results into a temporary SQLite database.

Examples:
  LOCAL_SCRAPER_SOURCE=amat python tests/smoke_local_scraper.py
  LOCAL_SCRAPER_SOURCE=qgat_homes python tests/smoke_local_scraper.py

Useful env vars:
  LOCAL_SCRAPER_SOURCE         Portal source key from scraper_local.SCRAPERS
  LOCAL_SCRAPER_MAX_PAGES      Override the max_pages limit for the smoke run
  LOCAL_SCRAPER_WRITE_DB       true/false, default true
  LOCAL_SCRAPER_DB_PATH        Path for the temporary output DB
  LOCAL_SCRAPER_MIN_LISTINGS    Fail if fewer listings are extracted
"""

from __future__ import annotations

import copy
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import init_db, upsert_property
from scraper_local import SCRAPERS, _scrape_agency

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError as exc:  # pragma: no cover - environment issue
    print(
        "Playwright is not installed in this Python environment. Install the repo requirements first.",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REQUIRED_FIELDS = [
    "property_id",
    "source",
    "title",
    "url",
    "price",
    "rooms",
    "bathrooms",
    "sqm",
]


def main() -> int:
    source_key = os.getenv("LOCAL_SCRAPER_SOURCE", "amat")
    max_pages = int(os.getenv("LOCAL_SCRAPER_MAX_PAGES", "1"))
    write_db = os.getenv("LOCAL_SCRAPER_WRITE_DB", "true").lower() == "true"
    db_path = Path(os.getenv("LOCAL_SCRAPER_DB_PATH", "smoke_local_scraper.db"))
    min_listings = int(os.getenv("LOCAL_SCRAPER_MIN_LISTINGS", "1"))

    cfg = _get_scraper_config(source_key)
    if cfg is None:
        available = ", ".join(sorted(scraper["source"] for scraper in SCRAPERS))
        print(f"Unknown source '{source_key}'. Available sources: {available}", file=sys.stderr)
        return 2

    cfg = copy.deepcopy(cfg)
    cfg["max_pages"] = max_pages

    logger.info("Running smoke test for source '%s'", source_key)
    logger.info("Using base URL: %s", cfg["base_url"])

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        props = _scrape_agency(page, cfg)
        browser.close()

    if not props:
        print(f"No properties extracted for '{source_key}'", file=sys.stderr)
        return 1

    print(f"Extracted {len(props)} properties from '{source_key}'")
    print(_format_field_coverage(props, REQUIRED_FIELDS))
    _print_sample(props[:3])

    missing_required = _count_missing_required(props, REQUIRED_FIELDS)
    if missing_required:
        print(f"Warning: {missing_required} extracted properties are missing required fields")

    if write_db:
        init_db(db_path)
        for prop in props:
            upsert_property(prop)
        print(f"Saved {len(props)} properties into temporary DB: {db_path}")

    if len(props) < min_listings:
        print(
            f"Only {len(props)} properties extracted, which is below LOCAL_SCRAPER_MIN_LISTINGS={min_listings}",
            file=sys.stderr,
        )
        return 1

    return 0


def _get_scraper_config(source_key: str) -> Dict | None:
    for scraper in SCRAPERS:
        if scraper.get("source") == source_key:
            return scraper
    return None


def _format_field_coverage(props: List[Dict], fields: List[str]) -> str:
    lines = ["Field coverage:"]
    for field in fields:
        present = sum(1 for prop in props if prop.get(field) not in (None, ""))
        lines.append(f"  - {field}: {present}/{len(props)}")
    optional_fields = [
        "property_type",
        "operation",
        "city",
        "district",
        "neighborhood",
        "postal_code",
        "latitude",
        "longitude",
        "energy_rating",
        "year_built",
        "floor",
        "terrace",
        "elevator",
        "parking",
    ]
    lines.append("Optional field coverage:")
    for field in optional_fields:
        present = sum(1 for prop in props if prop.get(field) not in (None, ""))
        lines.append(f"  - {field}: {present}/{len(props)}")
    return "\n".join(lines)


def _count_missing_required(props: List[Dict], fields: List[str]) -> int:
    missing = 0
    for prop in props:
        if any(prop.get(field) in (None, "") for field in fields):
            missing += 1
    return missing


def _print_sample(props: List[Dict]) -> None:
    print("Sample rows:")
    for prop in props:
        print(
            "  - "
            f"{prop.get('property_id')} | {prop.get('title')} | "
            f"price={prop.get('price')} | rooms={prop.get('rooms')} | "
            f"bathrooms={prop.get('bathrooms')} | sqm={prop.get('sqm')} | "
            f"city={prop.get('city')} | district={prop.get('district')}"
        )


if __name__ == "__main__":
    raise SystemExit(main())