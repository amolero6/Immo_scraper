import os
from typing import List, Dict

from scraper_local import SCRAPERS, _scrape_yaencontre_agency
from database import init_db, upsert_property
from playwright.sync_api import sync_playwright
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# URLs and configuration for different populations
YAENCONTRE_URLS = {
    "sant_cugat": "https://www.yaencontre.com/venta/pisos/sant-cugat-del-valles",
    "sant_quirze": "https://www.yaencontre.com/venta/pisos/sant-quirze-del-valles",
    "cerdanyola": "https://www.yaencontre.com/venta/pisos/cerdanyola-del-valles",
}

YAENCONTRE_CITIES = {
    "sant_cugat": "Sant Cugat del Vallès",
    "sant_quirze": "Sant Quirze del Vallès",
    "cerdanyola": "Cerdanyola del Vallès",
}

# Get the base config from SCRAPERS
_base_cfg = next(c for c in SCRAPERS if c['source'] == 'yaencontre')
YAENCONTRE_CDP_URL = os.getenv("YAENCONTRE_CDP_URL", "http://127.0.0.1:9234")


def _make_yaencontre_config(url: str, city: str) -> Dict:
    """Create a Yaencontre scraper config for a given population."""
    cfg = _base_cfg.copy()
    cfg["base_url"] = url
    cfg["city"] = city
    return cfg


def scrape_yaencontre_properties(population: str = "sant_cugat") -> List[Dict]:
    """
    Scrape Yaencontre properties for a given population.
    
    Args:
        population: Population key (e.g., "sant_cugat", "sant_quirze", "cerdanyola")
    
    Returns:
        List of property dictionaries.
    """
    url = YAENCONTRE_URLS.get(population, YAENCONTRE_URLS["sant_cugat"])
    city = YAENCONTRE_CITIES.get(population, YAENCONTRE_CITIES["sant_cugat"])
    cfg = _make_yaencontre_config(url, city)
    return _scrape_yaencontre_properties_generic(cfg)


def _scrape_yaencontre_properties_generic(cfg: Dict) -> List[Dict]:
    """Attach to an existing Chrome (CDP) or launch a headed browser and
    return the list of extracted Yaencontre property dicts.
    
    Args:
        cfg: Configuration dictionary with base_url, city, and selectors.
    """
    props: List[Dict] = []
    with sync_playwright() as pw:
        browser = None
        connected_via_cdp = False

        try:
            browser = pw.chromium.connect_over_cdp(YAENCONTRE_CDP_URL)
            connected_via_cdp = True
            logger.info("Connected to Yaencontre Chrome via CDP: %s", YAENCONTRE_CDP_URL)
        except Exception as exc:
            logger.info("CDP attach failed (%s); launching a fresh headed browser.", exc)
            browser = pw.chromium.launch(headless=False, args=[
                '--disable-blink-features=AutomationControlled',
            ])

        if browser.contexts:
            ctx = browser.contexts[0]
        else:
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale='es-ES',
                timezone_id='Europe/Madrid',
                viewport={'width': 1366, 'height': 900},
            )
        try:
            ctx.add_init_script("() => { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); Object.defineProperty(navigator, 'languages', {get: () => ['es-ES','es']}); }")
        except Exception:
            pass

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(cfg['base_url'], wait_until='domcontentloaded', timeout=30_000)
        print(f'Browser opened and navigated to Yaencontre ({cfg.get("city", "Unknown")}). If a challenge appears, please interact with the page (accept cookies, solve captcha).')
        input('When the listings are visible, press Enter to continue...')
        props = _scrape_yaencontre_agency(page, cfg)

        # Cleanup: don't close the user's Chrome when attached via CDP
        try:
            if connected_via_cdp:
                try:
                    browser.disconnect()
                except Exception:
                    pass
            else:
                browser.close()
        except Exception:
            pass

    return props


def main() -> int:
    init_db()
    props = scrape_yaencontre_properties()

    if not props:
        print("No se extrajeron propiedades de Yaencontre.")
        return 1

    print(f"Extracted {len(props)} properties from Yaencontre")
    for prop in props[:5]:
        print(f"  - {prop['title']} | {prop.get('price')}€ | {prop['url']}")

    saved = 0
    for p in props:
        action = upsert_property(p)
        if action == 'inserted':
            saved += 1
    print(f'Saved {saved} new properties to database.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
