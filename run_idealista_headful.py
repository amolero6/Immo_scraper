from __future__ import annotations

import logging
import os
import re
from typing import Dict, List

from database import init_db, upsert_property
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright
from scraper_local import _humanize_page_actions, _parse_int, _parse_price

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.idealista.com/venta-viviendas/sant-cugat-del-valles-barcelona/"
MAX_PAGES = int(os.getenv("IDEALISTA_MAX_PAGES", "10"))
WAIT_FOR_VERIFICATION_SECONDS = int(os.getenv("IDEALISTA_WAIT_FOR_VERIFICATION_SECONDS", "60"))


def main() -> int:
    init_db()
    props = scrape_idealista_properties()

    if not props:
        print("No se extrajeron propiedades de Idealista.")
        return 1

    print(f"Extracted {len(props)} properties from Idealista")
    for prop in props[:5]:
        print(f"  - {prop['title']} | {prop.get('price')}€ | {prop['url']}")

    saved = 0
    for prop in props:
        action = upsert_property(prop)
        if action == "inserted":
            saved += 1
    print(f"Saved {saved} new properties to database.")
    return 0


def scrape_idealista_properties() -> List[Dict]:
    props: List[Dict] = []
    seen_ids: set[str] = set()

    with sync_playwright() as pw:
        browser = None
        connected_via_cdp = False
        # Try to attach to an existing Chrome instance via CDP (remote debugging)
        default_context_kwargs = dict(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1366, "height": 900},
        )

        try:
            logger.info("Attempting to connect to Chrome via CDP at http://127.0.0.1:9222")
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
            connected_via_cdp = True
            logger.info("Connected to Chrome via CDP.")
        except Exception as exc:
            logger.info("CDP attach failed (%s); launching a fresh Chrome instance.", exc)
            browser = pw.chromium.launch(
                headless=False,
                channel="chrome",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )

        # Prefer an existing context from the connected browser to reuse profile/cookies
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = browser.new_context(**default_context_kwargs)
        context.set_default_timeout(30_000)
        context.set_default_navigation_timeout(30_000)
        context.add_init_script(
            "() => { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); Object.defineProperty(navigator, 'languages', {get: () => ['es-ES','es']}); }"
        )
        page = context.pages[0] if context.pages else context.new_page()

        logger.info("Using Chrome channel for the headful browser.")
        logger.info("Opening Idealista listing: %s", BASE_URL)
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)

        logger.info(
            "If Idealista shows device verification, wait until the listings are visible in the browser and then press Enter here.",
        )
        input("Press Enter once Idealista listings are visible...")

        # If the page got closed during verification or by the browser session,
        # recreate it before continuing.
        if page.is_closed():
            logger.warning("The current page was closed; opening a fresh page on the same context.")
            page = context.new_page()
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
            input("Press Enter once Idealista listings are visible again...")

        _humanize_page_actions(page)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except PlaywrightTimeout:
            logger.info("Timed out waiting for DOM ready after verification; continuing with current DOM.")

        if page.is_closed():
            logger.error("Page closed before scraping could start.")
            return 1

        total_results = _extract_total_results(page)
        if total_results is not None:
            estimated_pages = max(1, (total_results + 29) // 30)
            logger.info(
                "Idealista reports about %d listings; rough page estimate is %d+.",
                total_results,
                estimated_pages,
            )

        page_num = 1
        while page_num <= MAX_PAGES:
            try:
                page.wait_for_selector("article.item", timeout=30_000)
            except PlaywrightTimeout:
                logger.warning("No cards found on page %d; stopping.", page_num)
                break

            cards = page.query_selector_all("article.item")
            logger.info("Page %d: found %d article cards.", page_num, len(cards))

            for card in cards:
                prop = _extract_idealista_property(card)
                if not prop:
                    continue
                if prop["property_id"] in seen_ids:
                    continue
                props.append(prop)
                seen_ids.add(prop["property_id"])

            if page_num >= MAX_PAGES:
                logger.info("Reached configured max pages (%d); stopping.", MAX_PAGES)
                break

            if not _go_to_next_page(page):
                logger.info("No next page button found; stopping at page %d.", page_num)
                break

            page_num += 1

        # Close the browser only if we launched it ourselves. If we attached via CDP,
        # avoid closing the user's Chrome instance; just disconnect the client.
        try:
            if connected_via_cdp:
                try:
                    browser.disconnect()
                except Exception:
                    # Best-effort: if disconnect isn't supported, ignore.
                    pass
            else:
                browser.close()
        except Exception:
            # Ensure we don't raise during cleanup
            pass

    _log_extraction_quality(props)
    return props


def _page_url(page_num: int) -> str:
    if page_num <= 1:
        return BASE_URL
    return f"{BASE_URL.rstrip('/')}/pagina-{page_num}.htm"


def _extract_total_results(page) -> int | None:
    try:
        body_text = page.locator("body").inner_text(timeout=5_000)
    except Exception:
        return None

    patterns = [
        r"(\d[\d\.\s]*)\s+(?:casas|viviendas|pisos|propiedades)\b",
        r"(\d[\d\.\s]*)\s+anuncios\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            return _parse_int(match.group(1))
    return None


def _go_to_next_page(page) -> bool:
    candidate_selectors = [
        'a[aria-label*="Siguiente"]',
        'a[title*="Siguiente"]',
        'a[class*="icon-arrow-right-after"]',
        'a:has-text("Siguiente")',
        'button[aria-label*="Siguiente"]',
        'button:has-text("Siguiente")',
    ]

    for selector in candidate_selectors:
        locator = page.locator(selector)
        try:
            if locator.count() == 0:
                continue
            locator.first.click(timeout=5_000)
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
            page.wait_for_selector("article.item", timeout=30_000)
            return True
        except Exception:
            continue

    return False


def _log_extraction_quality(props: List[Dict]) -> None:
    if not props:
        return

    fields_to_check = [
        "title",
        "url",
        "price",
        "rooms",
        "bathrooms",
        "sqm",
        "city",
        "operation",
        "source",
    ]
    total = len(props)
    logger.info("Extraction quality summary over %d properties:", total)
    for field in fields_to_check:
        present = sum(prop.get(field) not in (None, "") for prop in props)
        logger.info("  %s: %d/%d present", field, present, total)

    sample = props[0]
    preview = {key: sample.get(key) for key in fields_to_check}
    logger.info("Sample extracted property fields: %s", preview)


def _extract_idealista_property(card) -> Dict | None:
    try:
        title_el = card.query_selector('a[href*="/inmueble/"]')
        if not title_el:
            return None

        title = (title_el.inner_text() or "").strip()
        url = title_el.get_attribute("href") or ""
        if url and not url.startswith("http"):
            url = f"https://www.idealista.com{url}"

        price_el = card.query_selector("span.item-price.h2-simulated")
        price_text = (price_el.inner_text() if price_el else "").strip()
        price = _parse_price(price_text)
        if price is None:
            euro_match = re.search(r"(\d[\d\.\s]*)\s*€", card.inner_text())
            if euro_match:
                price = _parse_price(euro_match.group(1))

        detail_texts = [
            (el.inner_text() or "").strip()
            for el in card.query_selector_all("div.item-detail-char span.item-detail")
        ]
        rooms = None
        sqm = None
        for detail in detail_texts:
            if rooms is None and re.search(r"hab\.?", detail, re.IGNORECASE):
                rooms = _parse_int(detail)
            if sqm is None and re.search(r"m²|m2", detail, re.IGNORECASE):
                sqm = _parse_int(detail)

        card_text = card.inner_text()
        description_el = card.query_selector("p.ellipsis")
        description = (description_el.inner_text() if description_el else card_text).strip()

        bathrooms = None
        bath_match = re.search(r"(\d+)\s*(?:bañ(?:o|os)|banys|bathrooms?)", description, re.IGNORECASE)
        if bath_match:
            bathrooms = int(bath_match.group(1))
        else:
            bath_match = re.search(r"(\d+)\s*baños", card_text, re.IGNORECASE)
            if bath_match:
                bathrooms = int(bath_match.group(1))

        property_id_match = re.search(r"/inmueble/(\d+)/", url)
        property_id = f"idealista_{property_id_match.group(1)}" if property_id_match else f"idealista_{abs(hash(url))}"

        has_pool = bool(re.search(r"piscina", card_text, re.IGNORECASE))
        has_ac = bool(re.search(r"aire acondicionado|aire condicionat", card_text, re.IGNORECASE))
        agent_el = card.query_selector(".hightop-agent-name")
        agent = (agent_el.inner_text() or "").strip() if agent_el else None

        return {
            "property_id": property_id,
            "source": "idealista_local",
            "title": title,
            "url": url,
            "price": price,
            "rooms": rooms,
            "bathrooms": bathrooms,
            "sqm": sqm,
            "has_pool": int(has_pool),
            "has_ac": int(has_ac),
            "orientation": None,
            "property_type": None,
            "operation": "sale",
            "city": "Sant Cugat del Vallès",
            "district": None,
            "neighborhood": None,
            "postal_code": None,
            "latitude": None,
            "longitude": None,
            "energy_rating": None,
            "year_built": None,
            "floor": None,
            "terrace": 0,
            "elevator": 0,
            "parking": 0,
            "is_favourite": 0,
            "agent": agent,
        }
    except Exception as exc:
        logger.warning("Could not parse Idealista card: %s", exc)
        return None


if __name__ == "__main__":
    raise SystemExit(main())
