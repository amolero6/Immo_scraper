"""
Debug script: Check if pagination is working and why we only get 42/216 properties.
Run manually: python debug_pagination.py
"""
from scraper_local import SCRAPERS, _goto_with_retry, _humanize_page_actions
from playwright.sync_api import sync_playwright
import logging
import re

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

cfg = next(c for c in SCRAPERS if c['source'] == 'yaencontre')

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False, args=[
        '--disable-blink-features=AutomationControlled',
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-background-timer-throttling',
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
    ctx.add_init_script("() => { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); }")
    page = ctx.new_page()

    # Navigate to page 1
    logger.info("Navigating to page 1...")
    _goto_with_retry(page, cfg['base_url'], cfg['source'])
    _humanize_page_actions(page)
    
    page.wait_for_selector(cfg["listing_selector"], timeout=25000)
    
    # Check title for total
    h1 = page.query_selector('h1')
    if h1:
        h1txt = h1.inner_text() or ""
        print(f"h1 text: {h1txt}")
        m = re.search(r"(\d[\d\.\s]*)\s+Pisos", h1txt)
        if m:
            total = int(m.group(1).replace('.', '').replace(' ', ''))
            print(f"✓ Total properties expected: {total}")
    
    cards_p1 = page.query_selector_all(cfg["listing_selector"])
    print(f"✓ Page 1 found {len(cards_p1)} listings")
    
    # Try pagination
    for page_num in range(1, 4):  # Try pages 1-3
        print(f"\n--- Page {page_num} ---")
        
        # Count current cards
        cards = page.query_selector_all(cfg["listing_selector"])
        print(f"Current page has {len(cards)} cards")
        
        # List first card title
        if cards:
            first_title = cards[0].query_selector(cfg["title_selector"])
            if first_title:
                print(f"  First card: {first_title.inner_text()[:60]}")
        
        # Look for next button
        if page_num < 3:
            next_texts = set(text.lower() for text in cfg.get("pagination_next_texts", ["›", "»"]))
            buttons = page.query_selector_all("button")
            next_btn = None
            for btn in buttons:
                btn_text = btn.inner_text().lower().strip()
                if any(text in btn_text for text in next_texts):
                    next_btn = btn
                    print(f"  Found next button: '{btn.inner_text()}'")
                    break
            
            if next_btn and next_btn.is_enabled():
                print(f"  → Clicking next...")
                try:
                    next_btn.click()
                    try:
                        page.wait_for_load_state('networkidle', timeout=20000)
                    except:
                        page.wait_for_timeout(2000)
                    
                    # Check what's on page 2
                    page.wait_for_selector(cfg["listing_selector"], timeout=15000)
                    new_cards = page.query_selector_all(cfg["listing_selector"])
                    print(f"  ✓ Page {page_num+1} loaded with {len(new_cards)} cards")
                except Exception as e:
                    print(f"  ✗ Error clicking next: {e}")
                    break
            else:
                print(f"  ✗ No enabled next button found")
                break
    
    print("\n[Press Ctrl+C or close the browser window to exit...]")
    input()
    browser.close()
