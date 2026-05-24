from scraper_local import SCRAPERS, _scrape_yaencontre_agency
from database import init_db, upsert_property
from playwright.sync_api import sync_playwright
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

cfg = next(c for c in SCRAPERS if c['source'] == 'yaencontre')

init_db()

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False, args=[
        '--disable-blink-features=AutomationControlled',
    ])
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
    ctx.add_init_script("() => { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); Object.defineProperty(navigator, 'languages', {get: () => ['es-ES','es']}); }")
    page = ctx.new_page()
    print('Browser opened. If a challenge appears, please interact with the page (accept cookies, solve captcha).')
    input('When the listings are visible, press Enter to continue...')
    props = _scrape_yaencontre_agency(page, cfg)
    print('Found', len(props), 'properties')
    
    # Save to database
    saved = 0
    for p in props:
        action = upsert_property(p)
        if action == 'inserted':
            saved += 1
        print(f"  [{action}] {p.get('title')} | {p.get('price')}€ | {p.get('url')}")
    
    print(f'\n✓ Saved {saved} new properties to database.')
    browser.close()
