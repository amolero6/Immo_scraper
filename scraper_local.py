"""
scraper_local.py
----------------
Playwright-based scraper template for local real-estate agencies.

This file is intentionally generic.  Each agency section is clearly marked
with  # TODO: adjust CSS selectors  comments so you can adapt it to the
real website's HTML structure.

Dependencies:
  pip install playwright
  playwright install chromium
"""

import logging
import re
from typing import List, Dict

from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration – edit these per agency
# ---------------------------------------------------------------------------

SCRAPERS: List[Dict] = [
    {
        "source": "amat",
        "base_url": "https://www.amat.es/ca/compra/habitatge?q=Sant+Cugat+del+Vall%C3%A8s",
        # CSS selectors – fill in the real ones after inspecting the page
        "listing_selector": "article.property-card",          # TODO: adjust
        "title_selector": "h2.property-title",                # TODO: adjust
        "price_selector": "span.price",                       # TODO: adjust
        "link_selector": "a.property-link",                   # TODO: adjust
        "rooms_selector": "span.rooms",                       # TODO: adjust
        "bathrooms_selector": "span.bathrooms",               # TODO: adjust
        "sqm_selector": "span.sqm",                           # TODO: adjust
        "pool_keyword": "piscina",                            # case-insensitive
        "ac_keyword": "aire condicionat",                     # case-insensitive
        "next_page_selector": "a[rel='next']",                # TODO: adjust
        "max_pages": 10,
    },
    # Add more agencies here following the same shape
]


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def scrape_all_local() -> List[Dict]:
    """
    Run all configured local scrapers and return a flat list of property dicts.

    Returns:
        List of property dictionaries ready to be upserted into the database.
    """
    all_props: List[Dict] = []

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

        for cfg in SCRAPERS:
            logger.info("Scraping source: %s", cfg["source"])
            try:
                props = _scrape_agency(page, cfg)
                logger.info(
                    "Source '%s': %d properties found.", cfg["source"], len(props)
                )
                all_props.extend(props)
            except Exception as exc:
                logger.error(
                    "Error scraping '%s': %s", cfg["source"], exc, exc_info=True
                )

        browser.close()

    return all_props


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _scrape_agency(page: Page, cfg: Dict) -> List[Dict]:
    """Navigate an agency listing and extract all property cards."""
    props: List[Dict] = []
    url = cfg["base_url"]
    pages_scraped = 0

    while url and pages_scraped < cfg.get("max_pages", 10):
        logger.debug("Fetching page %d: %s", pages_scraped + 1, url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeout:
            logger.warning("Timeout loading '%s' – skipping.", url)
            break

        # Wait for at least one listing card to appear
        try:
            page.wait_for_selector(cfg["listing_selector"], timeout=15_000)
        except PlaywrightTimeout:
            logger.warning(
                "No listing cards found on '%s' with selector '%s'.",
                url,
                cfg["listing_selector"],
            )
            break

        cards = page.query_selector_all(cfg["listing_selector"])
        logger.debug("Found %d cards on this page.", len(cards))

        for card in cards:
            prop = _extract_property(card, cfg)
            if prop:
                props.append(prop)

        # ---- Pagination ----
        next_btn = page.query_selector(cfg.get("next_page_selector", ""))
        if next_btn:
            next_href = next_btn.get_attribute("href")
            if next_href and not next_href.startswith("http"):
                # Resolve relative URLs
                from urllib.parse import urljoin
                next_href = urljoin(cfg["base_url"], next_href)
            url = next_href
        else:
            url = None

        pages_scraped += 1

    return props


def _extract_property(card, cfg: Dict) -> Dict | None:
    """Extract a single property dict from a Playwright element handle."""
    try:
        # ---- Title ----
        title_el = card.query_selector(cfg["title_selector"])
        title = title_el.inner_text().strip() if title_el else ""

        # ---- URL ----
        link_el = card.query_selector(cfg["link_selector"])
        url = link_el.get_attribute("href") if link_el else ""
        if url and not url.startswith("http"):
            from urllib.parse import urljoin
            url = urljoin(cfg["base_url"], url)

        # ---- Price ----
        price_el = card.query_selector(cfg["price_selector"])
        price = _parse_price(price_el.inner_text() if price_el else "")

        # ---- Rooms ----
        rooms_el = card.query_selector(cfg["rooms_selector"])
        rooms = _parse_int(rooms_el.inner_text() if rooms_el else "")

        # ---- Bathrooms ----
        bath_el = card.query_selector(cfg["bathrooms_selector"])
        bathrooms = _parse_int(bath_el.inner_text() if bath_el else "")

        # ---- Surface area ----
        sqm_el = card.query_selector(cfg["sqm_selector"])
        sqm = _parse_int(sqm_el.inner_text() if sqm_el else "")

        # ---- Boolean features (detected from full card text) ----
        full_text = card.inner_text().lower()
        has_pool = cfg.get("pool_keyword", "piscina") in full_text
        has_ac = cfg.get("ac_keyword", "aire condicionat") in full_text

        # ---- Unique ID (source + URL slug) ----
        property_id = _build_id(cfg["source"], url)

        if not property_id or not url:
            logger.debug("Skipping card with no URL.")
            return None

        return {
            "property_id": property_id,
            "source": cfg["source"],
            "title": title,
            "url": url,
            "price": price,
            "rooms": rooms,
            "bathrooms": bathrooms,
            "sqm": sqm,
            "has_pool": has_pool,
            "has_ac": has_ac,
            "orientation": None,  # Not typically available in listings
        }
    except Exception as exc:
        logger.warning("Could not parse a property card: %s", exc)
        return None


def _parse_price(text: str) -> int | None:
    """Extract an integer price from a string like '450.000 €' or '€ 450,000'."""
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _parse_int(text: str) -> int | None:
    """Extract the first integer found in *text*."""
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def _build_id(source: str, url: str) -> str:
    """Build a stable unique ID from source name and URL."""
    # Use the last path segment (or query) as a slug
    slug = url.rstrip("/").split("/")[-1].split("?")[0]
    return f"{source}_{slug}" if slug else ""
