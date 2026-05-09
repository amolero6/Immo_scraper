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
from __future__ import annotations

import logging
import re
import json
from typing import List, Dict

from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration – edit these per agency
# ---------------------------------------------------------------------------

SCRAPERS: List[Dict] = [
    {
        "source": "amat",
        "base_url": "https://www.amatimmobiliaris.com/ca/immobles-en-venda?town=SANT+CUGAT+DEL+VALLES",
        "city": "Sant Cugat del Vallès",
        # CSS selectors adapted to the real Amat listing cards.
        # The card is a Bootstrap panel and some fields are plain text inside blocks.
        "listing_selector": "div.panel.panel-default",
        "title_selector": "h3.title-product-container",
        "price_selector": "div.price-name-container span",
        "link_selector": "a[href*='/ca/venda/']",
        "rooms_selector": "div.habitaciones",
        "bathrooms_selector": "div.banos",
        "sqm_selector": "div.superficie",
        "pool_keyword": "piscina",                            # case-insensitive
        "ac_keyword": "aire condicionat",                     # case-insensitive
        "next_page_selector": "a[rel='next']",
        "max_pages": 10,
    },
    {
        "source": "qgat_homes",
        "base_url": "https://www.qgathomes.com/ca/venda/a-barcelona-sant_cugat_del_valles",
        "city": "Sant Cugat del Vallès",
        # Qgat listing card and field selectors.
        "listing_selector": "div.DLFichaParent",
        "title_selector": ".DLFichaTitulo a[href*='/ref-']",
        "price_selector": ".DLFichaPrecio .DLFichaPrecioVenta",
        "link_selector": ".DLFichaTitulo a[href*='/ref-']",
        "rooms_selector": ".iconos i.fa-bed",
        "bathrooms_selector": ".iconos i.fa-bath",
        "sqm_selector": ".iconos i.glyphicons-fit-frame-to-image span",
        "pool_keyword": "piscina",                            # case-insensitive
        "ac_keyword": "aire condicionat",                     # case-insensitive
        # Pagination is hash-based (#page-N) on this portal.
        "pagination_mode": "hash",
        "pagination_page_selector": "ul.pagination a[href^='#page-']",
        "max_pages": 10,
    },
    {
        "source": "finques_soler",
        "base_url": "https://solerfinques.com/propiedades/compra/viviendas/barcelona-province/sant-cugat-del-valles",
        "city": "Sant Cugat del Vallès",
        # Soler Finques cards are article-based and the metric block is ordered.
        "listing_selector": "article",
        "title_selector": "a[href*='/detalle/'] h5",
        "price_selector": "header > span",
        "link_selector": "a[href*='/detalle/']",
        "rooms_selector": "div.mt-auto p:nth-of-type(1)",
        "bathrooms_selector": "div.mt-auto p:nth-of-type(2)",
        "sqm_selector": "div.mt-auto p:nth-of-type(3)",
        "pool_keyword": "piscina",                            # case-insensitive
        "ac_keyword": "aire acondicionado",                  # case-insensitive
        "pagination_mode": "buttons",
        "pagination_button_selector": "button",
        "pagination_next_texts": ["Next", "Siguiente", "Següent", "Seg"],
        "max_pages": 10,
        "parse_mode": "soler_finques",
    },
    {
        "source": "bachs_finques",
        "base_url": "https://www.finquesbachs.com/compra/",
        "city": "Sant Cugat del Vallès",
        # Finques Bachs uses a WordPress/WP Residence style shortcode listing.
        "listing_selector": "div.listing_wrapper.property_unit_type6",
        "title_selector": "div.property_unit_type6_title_wrapper h4 a",
        "price_selector": "div.listing_unit_price_wrapper",
        "link_selector": "div.property_unit_type6_title_wrapper h4 a",
        "rooms_selector": "div.property_listing_details6_grid_view > div.inforoom_unit_type6:nth-of-type(1)",
        "bathrooms_selector": "div.property_listing_details6_grid_view > div.inforoom_unit_type6:nth-of-type(2)",
        "sqm_selector": "",
        "pool_keyword": "piscina",                            # case-insensitive
        "ac_keyword": "aire acondicionado",                  # case-insensitive
        "max_pages": 1,
        "parse_mode": "bachs_finques",
    },
    {
        "source": "fincas_moragas",
        "base_url": "https://fincasmoragas.com/ca/tipo/venda/?_sft_localidad=sant-cugat-del-valles",
        "city": "Sant Cugat del Vallès",
        # This site exposes an ItemList JSON-LD with direct URLs to detail pages;
        # we'll prefer extracting URLs from that JSON-LD and parsing detail pages.
        "parse_mode": "moragas",
        "max_pages": 1,
    },
    {
        "source": "organ",
        "base_url": "https://www.organ.es/es/venta/en-barcelona-sant_cugat_del_valles",
        "city": "Sant Cugat del Vallès",
        "listing_selector": "div.itemListadoSimpleInmueble",
        "title_selector": "div.datos div.titulo h2 a",
        "price_selector": "div.precios span.venta",
        "link_selector": "div.imagen a",
        # The small icon-list contains sqm and bedrooms; we'll parse them from the card text.
        "rooms_selector": "div.iconos ul.nav li",
        "bathrooms_selector": "div.iconos ul.nav li",
        "sqm_selector": "div.iconos ul.nav li",
        "pool_keyword": "piscina",
        "ac_keyword": "aire acondicionado",
        "max_pages": 5,
        "parse_mode": "organ",
    },
    {
        "source": "mashomes",
        "base_url": "https://www.mashomes.es/es/venta/en-barcelona-sant_cugat_del_valles",
        "city": "Sant Cugat del Vallès",
        "parse_mode": "mashomes",
        "max_pages": 1,
    },
    {
        "source": "fincas_cano_pujol",
        "base_url": "https://www.fincascanopujol.es/es/venta/en-barcelona-sant_cugat_del_valles",
        "city": "Sant Cugat del Vallès",
        "parse_mode": "fincas_cano_pujol",
        "max_pages": 1,
    },
    {
        "source": "tecnocasa",
        "base_url": "https://santcugat1.tecnocasa.es/inmuebles-en-venta",
        "city": "Sant Cugat del Vallès",
        "parse_mode": "tecnocasa",
        "max_pages": 3,
    },
    {
        "source": "best_house",
        "base_url": "https://www.best-house.es/santcugat.francescmacia/index.php?limciudad=124799&buscador=1",
        "city": "Sant Cugat del Vallès",
        "parse_mode": "best_house",
        "max_pages": 1,
    },
    {
        "source": "aproperties",
        "base_url": "https://www.aproperties.es/search?mod=sale&zone=1&area=63&loc=5&group=&dis=&q=Sant%20Cugat%20del%20Vall%C3%A8s",
        "city": "Sant Cugat del Vallès",
        "parse_mode": "aproperties",
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
    if cfg.get("parse_mode") == "moragas":
        return _scrape_moragas_agency(page, cfg)
    if cfg.get("parse_mode") == "mashomes":
        return _scrape_mashomes_agency(page, cfg)
    if cfg.get("parse_mode") == "fincas_cano_pujol":
        return _scrape_fincas_cano_pujol_agency(page, cfg)
    if cfg.get("parse_mode") == "tecnocasa":
        return _scrape_tecnocasa_agency(page, cfg)
    if cfg.get("parse_mode") == "best_house":
        return _scrape_best_house_agency(page, cfg)
    if cfg.get("parse_mode") == "aproperties":
        return _scrape_aproperties_agency(page, cfg)
    if cfg.get("pagination_mode") == "hash":
        return _scrape_agency_hash_pagination(page, cfg)
    if cfg.get("pagination_mode") == "buttons":
        return _scrape_agency_button_pagination(page, cfg)

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
            prop = _extract_property(card, cfg, page)
            if prop:
                props.append(prop)

        # ---- Pagination ----
        next_selector = cfg.get("next_page_selector")
        next_btn = page.query_selector(next_selector) if next_selector else None
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


def _scrape_moragas_agency(page: Page, cfg: Dict) -> List[Dict]:
    """Extract property URLs from JSON-LD ItemList then parse each detail page."""
    props: List[Dict] = []
    try:
        page.goto(cfg["base_url"], wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeout:
        logger.warning("Timeout loading '%s' – skipping.", cfg["base_url"])
        return props

    html = page.content()
    scripts = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    urls: List[str] = []
    for s in scripts:
        try:
            obj = json.loads(s)
        except Exception:
            continue
        # obj may be a dict with @graph or an ItemList directly
        if isinstance(obj, dict) and obj.get("@graph"):
            for node in obj.get("@graph", []):
                if node.get("@type") == "CollectionPage" and node.get("mainEntity"):
                    ie = node["mainEntity"].get("itemListElement", [])
                    for item in ie:
                        u = item.get("url")
                        if u:
                            urls.append(u)
        if isinstance(obj, dict) and obj.get("@type") == "ItemList":
            for item in obj.get("itemListElement", []):
                if isinstance(item, dict):
                    u = item.get("url") or (item.get("item") and item["item"].get("@id"))
                    if u:
                        urls.append(u)

    # Fallback: try links in page to /inmueble/
    if not urls:
        anchors = page.query_selector_all("a[href*='/inmueble/']")
        for a in anchors:
            try:
                href = a.get_attribute("href")
                if href and href.startswith("/"):
                    href = page.url.rstrip("/") + href
                if href:
                    urls.append(href)
            except Exception:
                continue

    # Deduplicate and limit
    seen = set()
    final_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            final_urls.append(u)
    final_urls = final_urls[: cfg.get("max_pages", 50)]

    for u in final_urls:
        try:
            page.goto(u, wait_until="domcontentloaded", timeout=30_000)
            body = page.locator("body").inner_text()
            title = page.query_selector("h1")
            title_text = title.inner_text().strip() if title else ""
            # Extract price by finding a euro amount on the page
            price = None
            price_match = re.search(r"(\d[\d\.,\s]*€)", body)
            if price_match:
                price = _parse_price(price_match.group(1))

            sqm = None
            sqm_match = re.search(r"(\d+)\s*(?:m²|m2)", body, re.IGNORECASE)
            if sqm_match:
                sqm = int(sqm_match.group(1))

            # Rooms: look for phrases indicating bedrooms (Catalan/ES)
            rooms = None
            rooms_match = re.search(r"(\d+)\s*(?:habitaci|habitaci[oó]n|dormitori|hab)\w*", body, re.IGNORECASE)
            if rooms_match:
                rooms = int(rooms_match.group(1))

            # Bathrooms (Catalan 'banys', Spanish 'baños')
            bathrooms = None
            bath_match = re.search(r"(\d+)\s*(?:banys|ba\u00f1os|ba\w*)", body, re.IGNORECASE)
            if bath_match:
                bathrooms = int(bath_match.group(1))

            prop_id = _build_id(cfg["source"], u)
            props.append(
                {
                    "property_id": prop_id,
                    "source": cfg["source"],
                    "title": title_text,
                    "url": u,
                    "price": price,
                    "rooms": rooms,
                    "bathrooms": bathrooms,
                    "sqm": sqm,
                    "has_pool": int(bool(re.search(r"piscina", body, re.IGNORECASE))),
                    "has_ac": int(bool(re.search(r"aire condicionat|aire acondicionado", body, re.IGNORECASE))),
                    "property_type": None,
                    "operation": None,
                    "city": cfg.get("city"),
                }
            )
        except Exception as exc:
            logger.warning("Error fetching detail '%s': %s", u, exc)

    return props


def _scrape_mashomes_agency(page: Page, cfg: Dict) -> List[Dict]:
    """Parse the server-rendered table `#infoListado` used by Mashomes/Mobiliagestion.

    The table rows include `td[data-info]` cells with keys: referencia, precio,
    superficie, dormitorios, banos, resumen and foto. We extract those fields
    without visiting detail pages to keep the run fast.
    """
    props: List[Dict] = []
    try:
        page.goto(cfg["base_url"], wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeout:
        logger.warning("Timeout loading '%s' – skipping.", cfg["base_url"])
        return props

    # Find table rows
    rows = page.query_selector_all("table#infoListado tbody tr")
    if not rows:
        # Fallback: try table with id infoListado without tbody
        rows = page.query_selector_all("table#infoListado tr")

    for row in rows:
        try:
            ref_td = row.query_selector("td[data-info='referencia']")
            ref = (ref_td.inner_text() or "").strip() if ref_td else ""

            price_td = row.query_selector("td[data-info='precio']")
            price = None
            if price_td:
                order = price_td.get_attribute("data-order")
                price = _parse_price(order) if order else _parse_price(price_td.inner_text() or "")

            sqm_td = row.query_selector("td[data-info='superficie']")
            sqm = None
            if sqm_td:
                sqm = _parse_int((sqm_td.inner_text() or "").replace("m", ""))

            rooms_td = row.query_selector("td[data-info='dormitorios']")
            rooms = _parse_int(rooms_td.inner_text() if rooms_td else "")

            bath_td = row.query_selector("td[data-info='banos']")
            bathrooms = _parse_int(bath_td.inner_text() if bath_td else "")

            resumen_td = row.query_selector("td[data-info='resumen']")
            resumen = (resumen_td.inner_text() or "").strip() if resumen_td else ""

            foto_td = row.query_selector("td[data-info='foto'] a")
            url = foto_td.get_attribute("href") if foto_td else ""
            if url and url.startswith("/"):
                from urllib.parse import urljoin
                url = urljoin(cfg["base_url"], url)

            # Build a title from resumen or reference if missing
            title = resumen.split("\n")[0][:120] if resumen else f"{cfg.get('city')} {ref}"

            property_id = _build_id(cfg["source"], url or ref)
            if not property_id:
                continue

            props.append(
                {
                    "property_id": property_id,
                    "source": cfg["source"],
                    "title": title,
                    "url": url,
                    "price": price,
                    "rooms": rooms,
                    "bathrooms": bathrooms,
                    "sqm": sqm,
                    "has_pool": int(bool(re.search(r"piscina", resumen, re.IGNORECASE))),
                    "has_ac": int(bool(re.search(r"aire condicionad|aire acondicionado", resumen, re.IGNORECASE))),
                    "city": cfg.get("city"),
                }
            )
        except Exception as exc:
            logger.warning("Could not parse Mashomes row: %s", exc)
            continue

    return props


def _scrape_fincas_cano_pujol_agency(page: Page, cfg: Dict) -> List[Dict]:
    """Parse Mobilia/Mobiliagestion table `#infoListado` for Fincas Cano & Pujol.

    This portal exposes many `td[data-info]` fields (link, referencia, precio,
    superficie, dormitorios, banos, resumen, tituloInmueble, idInmueble,
    latitud/longitud). We extract the most useful ones without visiting
    detail pages.
    """
    props: List[Dict] = []
    try:
        page.goto(cfg["base_url"], wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeout:
        logger.warning("Timeout loading '%s' – skipping.", cfg["base_url"])
        return props

    rows = page.query_selector_all("table#infoListado tbody tr")
    if not rows:
        rows = page.query_selector_all("table#infoListado tr")

    for row in rows:
        try:
            link_td = row.query_selector("td[data-info='link']")
            link = (link_td.inner_text() or "").strip() if link_td else ""
            # link may be in a child anchor
            if not link:
                a = row.query_selector("a[href*='/ref-']")
                link = a.get_attribute("href") if a else ""
            if link and link.startswith("/"):
                from urllib.parse import urljoin
                link = urljoin(cfg["base_url"], link)

            ref_td = row.query_selector("td[data-info='referencia']")
            referencia = (ref_td.inner_text() or "").strip() if ref_td else ""

            price_td = row.query_selector("td[data-info='precio']")
            price = None
            if price_td:
                order = price_td.get_attribute("data-order")
                price = _parse_price(order) if order else _parse_price(price_td.inner_text() or "")

            sqm_td = row.query_selector("td[data-info='superficie']")
            sqm = None
            if sqm_td:
                sqm = _parse_int((sqm_td.inner_text() or "").replace("m", ""))

            rooms = _parse_int(row.query_selector("td[data-info='dormitorios']").inner_text() if row.query_selector("td[data-info='dormitorios']") else "")
            bathrooms = _parse_int(row.query_selector("td[data-info='banos']").inner_text() if row.query_selector("td[data-info='banos']") else "")

            title_td = row.query_selector("td[data-info='tituloInmueble']")
            title = (title_td.inner_text() or "").strip() if title_td else ""
            resumen_td = row.query_selector("td[data-info='resumen']")
            resumen = (resumen_td.inner_text() or "").strip() if resumen_td else ""

            lat = None
            lon = None
            pos_td = row.query_selector("td[data-info='posicion']")
            if pos_td:
                lat_el = pos_td.query_selector("span[data-info='latitud']")
                lon_el = pos_td.query_selector("span[data-info='longitud']")
                lat = lat_el.inner_text().strip() if lat_el and lat_el.inner_text().strip() else None
                lon = lon_el.inner_text().strip() if lon_el and lon_el.inner_text().strip() else None

            id_td = row.query_selector("td[data-info='idInmueble']")
            id_inmueble = (id_td.inner_text() or "").strip() if id_td else ""

            property_id = _build_id(cfg["source"], link or f"ref-{referencia}" or id_inmueble)
            if not property_id:
                continue

            props.append(
                {
                    "property_id": property_id,
                    "source": cfg["source"],
                    "title": title or resumen or f"{cfg.get('city')} {referencia}",
                    "url": link,
                    "price": price,
                    "rooms": rooms,
                    "bathrooms": bathrooms,
                    "sqm": sqm,
                    "has_pool": int(bool(re.search(r"piscina", resumen or title or "", re.IGNORECASE))),
                    "has_ac": int(bool(re.search(r"aire condicionad|aire acondicionado", resumen or title or "", re.IGNORECASE))),
                    "city": cfg.get("city"),
                    "latitude": float(lat) if lat else None,
                    "longitude": float(lon) if lon else None,
                    "external_id": id_inmueble,
                }
            )
        except Exception as exc:
            logger.warning("Could not parse CanoPujol row: %s", exc)
            continue

    return props


def _scrape_tecnocasa_agency(page: Page, cfg: Dict) -> List[Dict]:
    """Extract properties from Tecnocasa local site.

    The site renders a container `.immobiliLista`; listings are injected by JS.
    We try several candidate card selectors under that container and fall
    back to scanning anchors inside the container when necessary.
    """
    props: List[Dict] = []
    try:
        page.goto(cfg["base_url"], wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeout:
        logger.warning("Timeout loading '%s' – skipping.", cfg["base_url"])
        return props

    try:
        page.wait_for_selector(".immobiliLista", timeout=15_000)
    except PlaywrightTimeout:
        logger.warning("Tecnocasa: listing container not found on %s", cfg["base_url"])
        return props

    container = page.query_selector(".immobiliLista")
    if not container:
        return props

    # Candidate selectors for item cards inside the container
    candidates = [
        ".immobiliListaAnnunci .estate-card",
        ".immobiliLista .estate-card",
        ".immobiliLista .item",
        ".immobiliLista .listing",
        ".immobiliLista article",
        ".immobiliLista .card",
        ".immobiliLista .box",
        ".immobiliLista .list-item",
        ".immobiliLista .col-sm-6",
    ]

    cards = []
    for sel in candidates:
        cards = page.query_selector_all(sel)
        if cards:
            break

    # If no structured cards found, fallback to anchors inside container
    if not cards:
        anchors = container.query_selector_all("a[href]")
        # Filter anchors that look like property links (path contains '/santcugatdelvalles/')
        card_hrefs = []
        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
                if href and ("/santcugatdelvalles/" in href or "/sant-cugat-del-valles/" in href or "/inmueble/" in href or "/immobile/" in href):
                    card_hrefs.append(href)
            except Exception:
                continue

        # Deduplicate and wrap anchors as minimal card-like dicts
        card_hrefs = list(dict.fromkeys(card_hrefs))
        for href in card_hrefs:
            # create a lightweight object with get_attribute/inner_text via JS evaluation
            try:
                url = href if href.startswith("http") else page.url.rstrip("/") + (href if href.startswith("/") else "/" + href)
            except Exception:
                url = href
            # Build minimal card dict using page.evaluate to extract surrounding text
            txt = ""
            try:
                txt = page.evaluate("(u) => {const a = Array.from(document.querySelectorAll('a[href]')).find(x=>x.getAttribute('href')===u||x.getAttribute('href')===('/'+u)); return a ? a.closest('article')?.innerText || a.closest('.card')?.innerText || a.innerText : ''; }", href)
            except Exception:
                txt = ""

            # Heuristics: use anchor text as title and url
            title = txt.split('\n')[0][:120] if txt else href.split('/')[-1]
            price = None
            price_match = re.search(r"(\d[\d\.,\s]*€)", txt)
            if price_match:
                price = _parse_price(price_match.group(1))

            sqm = None
            sqm_match = re.search(r"(\d+[\d\.,]*)\s*(?:m(?:²|2))", txt)
            if sqm_match:
                sqm = _parse_int(sqm_match.group(1))

            rooms = None
            rooms_match = re.search(r"(\d+)\s*(?:hab|habitaci|dormit)", txt, re.IGNORECASE)
            if rooms_match:
                rooms = int(rooms_match.group(1))

            bathrooms = None
            bath_match = re.search(r"(\d+)\s*(?:ba\w*)", txt, re.IGNORECASE)
            if bath_match:
                bathrooms = int(bath_match.group(1))

            prop_id = _build_id(cfg["source"], url)
            props.append({
                "property_id": prop_id,
                "source": cfg["source"],
                "title": title,
                "url": url,
                "price": price,
                "rooms": rooms,
                "bathrooms": bathrooms,
                "sqm": sqm,
                "city": cfg.get("city"),
            })

        return props

    # Parse structured cards
    for card in cards:
        try:
            # Link and title
            a = card.query_selector("a[href]")
            url = a.get_attribute("href") if a else ""
            if url and not url.startswith("http"):
                from urllib.parse import urljoin
                url = urljoin(cfg["base_url"], url)

            title_el = card.query_selector("h2") or card.query_selector("h3") or a
            title = title_el.inner_text().strip() if title_el else ""

            # Price: prefer explicit current-price element, otherwise extract first euro amount
            price = None
            price_el = card.query_selector(".estate-card-current-price") or card.query_selector(".estate-card-price")
            if price_el:
                price = _parse_price(price_el.inner_text() or "")
            else:
                # fallback: first euro-like amount in the card text
                txt_all = card.inner_text()
                pm = re.search(r"(\d[\d\.,\s]*€)", txt_all)
                if pm:
                    price = _parse_price(pm.group(1))

            txt = card.inner_text()
            sqm = None
            sqm_match = re.search(r"(\d+[\d\.,]*)\s*(?:m(?:²|2))", txt)
            if sqm_match:
                sqm = _parse_int(sqm_match.group(1))

            rooms = None
            rooms_match = re.search(r"(\d+)\s*(?:hab|habitaci|dormit)", txt, re.IGNORECASE)
            if rooms_match:
                rooms = int(rooms_match.group(1))

            bathrooms = None
            bath_match = re.search(r"(\d+)\s*(?:ba\w*)", txt, re.IGNORECASE)
            if bath_match:
                bathrooms = int(bath_match.group(1))

            property_id = _build_id(cfg["source"], url)
            if not property_id:
                continue

            props.append(
                {
                    "property_id": property_id,
                    "source": cfg["source"],
                    "title": title,
                    "url": url,
                    "price": price,
                    "rooms": rooms,
                    "bathrooms": bathrooms,
                    "sqm": sqm,
                    "city": cfg.get("city"),
                }
            )
        except Exception as exc:
            logger.warning("Could not parse Tecnocasa card: %s", exc)
            continue

    return props


def _scrape_best_house_agency(page: Page, cfg: Dict) -> List[Dict]:
    """Parse Best House paginated property cards from the listing page.

    The page renders `article.paginacion-ficha.propiedad` cards with the
    detail link in `.irAfichaPropiedad`, title in `h1.titulo`, price in
    `.paginacion-ficha-tituloprecio`, and metrics in `.paginacion-ficha-masdatos`.
    """
    props: List[Dict] = []
    try:
        page.goto(cfg["base_url"], wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeout:
        logger.warning("Timeout loading '%s' – skipping.", cfg["base_url"])
        return props

    try:
        page.wait_for_selector("article.paginacion-ficha.propiedad", timeout=15_000)
    except PlaywrightTimeout:
        logger.warning("Best House: no property cards found on %s", cfg["base_url"])
        return props

    cards = page.query_selector_all("article.paginacion-ficha.propiedad")
    for card in cards:
        try:
            link_el = card.query_selector("a.irAfichaPropiedad")
            url = link_el.get_attribute("href") if link_el else ""
            if url and not url.startswith("http"):
                from urllib.parse import urljoin
                url = urljoin(cfg["base_url"], url)

            title_el = card.query_selector("h1.titulo")
            title = title_el.inner_text().strip() if title_el else ""

            price_el = card.query_selector(".paginacion-ficha-tituloprecio")
            price_text = price_el.inner_text().strip() if price_el else ""
            price = _parse_price(price_text)

            # Metric list is ordered: reference, bedrooms, bathrooms, surface.
            metric_spans = [
                (el.inner_text() or "").strip()
                for el in card.query_selector_all(".paginacion-ficha-masdatos li span")
                if (el.inner_text() or "").strip()
            ]

            ref = metric_spans[0] if len(metric_spans) >= 1 else ""
            rooms = _parse_int(metric_spans[1]) if len(metric_spans) >= 2 else None
            bathrooms = _parse_int(metric_spans[2]) if len(metric_spans) >= 3 else None
            sqm = _parse_int(metric_spans[3]) if len(metric_spans) >= 4 else None

            full_text = card.inner_text().lower()
            has_pool = int(bool(re.search(r"piscina", full_text)))
            has_ac = int(bool(re.search(r"aire acondicionado|aire condicionat", full_text)))

            # Best House detail URLs end in /<id>/en/; use the numeric id for a stable property_id.
            property_match = re.search(r"/(\d+)/en/?$", url or "")
            property_key = property_match.group(1) if property_match else ref or url
            property_id = _build_id(cfg["source"], property_key)

            if not property_id or not url:
                continue

            props.append(
                {
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
                    "property_type": None,
                    "operation": None,
                    "city": cfg.get("city"),
                }
            )
        except Exception as exc:
            logger.warning("Could not parse Best House card: %s", exc)
            continue

    return props


def _scrape_aproperties_agency(page: Page, cfg: Dict) -> List[Dict]:
    """Extract aProperties search results across all result pages.

    aProperties renders `div.propertyBlock` cards and paginates with `p=N`
    query parameters. We detect the last available page from the pagination
    links and then visit each page one by one.
    """
    props: List[Dict] = []
    seen_property_ids: set[str] = set()

    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    def build_page_url(base_url: str, page_num: int) -> str:
        parsed = urlparse(base_url)
        query = parse_qs(parsed.query)
        if page_num <= 1:
            query.pop("p", None)
        else:
            query["p"] = [str(page_num)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    try:
        page.goto(cfg["base_url"], wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeout:
        logger.warning("Timeout loading '%s' – skipping.", cfg["base_url"])
        return props

    try:
        page.wait_for_selector(".properties-list .propertyBlock", timeout=15_000)
    except PlaywrightTimeout:
        logger.warning("aProperties: no property blocks found on %s", cfg["base_url"])
        return props

    detected_max = 1
    for link in page.query_selector_all(".pagination__container ul.pagination a"):
        href = link.get_attribute("href") or ""
        match = re.search(r"[?&]p=(\d+)", href)
        if match:
            detected_max = max(detected_max, int(match.group(1)))

    pages_to_scrape = min(cfg.get("max_pages", 10), detected_max)
    logger.info(
        "Source '%s': detected %d page(s), scraping up to %d page(s).",
        cfg.get("source"),
        detected_max,
        pages_to_scrape,
    )

    for page_num in range(1, pages_to_scrape + 1):
        page_url = build_page_url(cfg["base_url"], page_num)
        if page_num > 1:
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_selector(".properties-list .propertyBlock", timeout=15_000)
            except PlaywrightTimeout:
                logger.warning("Timeout loading aProperties page %d: %s", page_num, page_url)
                break

        cards = page.query_selector_all(".properties-list .propertyBlock")
        logger.debug("aProperties page %d: found %d cards.", page_num, len(cards))

        for card in cards:
            try:
                link_el = card.query_selector("a[href]")
                url = link_el.get_attribute("href") if link_el else ""
                if url and not url.startswith("http"):
                    from urllib.parse import urljoin
                    url = urljoin(cfg["base_url"], url)

                ref_el = card.query_selector(".propertyBlock__reference")
                ref = ref_el.inner_text().strip() if ref_el else ""

                title_el = card.query_selector(".propertyBlock__title")
                title = title_el.inner_text().strip() if title_el else ""

                location_el = card.query_selector(".propertyBlock__location")
                location = location_el.inner_text().strip() if location_el else ""

                price_el = card.query_selector(".propertyBlock__price")
                price_text = price_el.inner_text().strip() if price_el else ""
                price = _parse_price(price_text)

                surface_text = card.query_selector(".propertyBlock__surface .propertyBlock__value")
                rooms_text = card.query_selector(".propertyBlock__bedrooms .propertyBlock__value")
                bath_text = card.query_selector(".propertyBlock__bathrooms .propertyBlock__value")
                sqm = _parse_int(surface_text.inner_text() if surface_text else "")
                rooms = _parse_int(rooms_text.inner_text() if rooms_text else "")
                bathrooms = _parse_int(bath_text.inner_text() if bath_text else "")

                content_el = card.query_selector(".propertyBlock__content")
                content = content_el.inner_text().strip() if content_el else ""
                full_text = f"{title} {location} {content}".lower()

                property_id = _build_id(cfg["source"], url or ref)
                if not property_id or property_id in seen_property_ids:
                    continue

                seen_property_ids.add(property_id)
                props.append(
                    {
                        "property_id": property_id,
                        "source": cfg["source"],
                        "title": title,
                        "url": url,
                        "price": price,
                        "rooms": rooms,
                        "bathrooms": bathrooms,
                        "sqm": sqm,
                        "has_pool": int(bool(re.search(r"piscina", full_text))),
                        "has_ac": int(bool(re.search(r"aire acondicionado|aire condicionat", full_text))),
                        "property_type": None,
                        "operation": cfg.get("operation") or "sale",
                        "city": cfg.get("city"),
                        "district": location or None,
                    }
                )
            except Exception as exc:
                logger.warning("Could not parse aProperties card: %s", exc)
                continue

    return props


def _scrape_agency_button_pagination(page: Page, cfg: Dict) -> List[Dict]:
    """Extract listings from portals that paginate with numbered buttons."""
    props: List[Dict] = []
    seen_property_ids: set[str] = set()
    base_url = cfg["base_url"].split("#")[0]
    next_texts = {text.lower() for text in cfg.get("pagination_next_texts", ["Next", "Siguiente", "Següent", "Seg"])}

    try:
        page.goto(base_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector(cfg["listing_selector"], timeout=15_000)
    except PlaywrightTimeout:
        logger.warning("Timeout loading '%s' – skipping.", base_url)
        return props

    configured_max = cfg.get("max_pages", 10)
    detected_max = _detect_last_button_page(page, cfg.get("pagination_button_selector", "button"))
    pages_to_scrape = min(configured_max, detected_max) if detected_max else configured_max

    logger.info(
        "Source '%s': button pagination detected=%s, scraping up to %d page(s).",
        cfg.get("source"),
        detected_max if detected_max else "unknown",
        pages_to_scrape,
    )

    for page_num in range(1, pages_to_scrape + 1):
        try:
            page.wait_for_selector(cfg["listing_selector"], timeout=15_000)
        except PlaywrightTimeout:
            logger.warning("No listings on '%s' – stopping button pagination.", base_url)
            break

        cards = page.query_selector_all(cfg["listing_selector"])
        logger.debug("Found %d cards on this page.", len(cards))

        for card in cards:
            prop = _extract_property(card, cfg, page)
            if prop and prop["property_id"] not in seen_property_ids:
                props.append(prop)
                seen_property_ids.add(prop["property_id"])

        if page_num >= pages_to_scrape:
            break

        previous_first_href = _first_card_href(page, cfg)
        moved = _click_next_button(page, cfg.get("pagination_button_selector", "button"), next_texts)
        if not moved:
            break

        try:
            page.wait_for_function(
                """
                (args) => {
                    const first = document.querySelector(args.linkSelector);
                    if (!first) return false;
                    const href = first.getAttribute('href') || '';
                    return href && href !== args.previousHref;
                }
                """,
                arg={
                    "linkSelector": cfg["link_selector"],
                    "previousHref": previous_first_href,
                },
                timeout=5_000,
            )
        except PlaywrightTimeout:
            logger.info("Page %d did not visibly change after next-button click; stopping.", page_num + 1)
            break

    return props


def _scrape_agency_hash_pagination(page: Page, cfg: Dict) -> List[Dict]:
    """Extract listings from portals using hash pagination (#page-N)."""
    props: List[Dict] = []
    seen_property_ids: set[str] = set()
    base_url = cfg["base_url"].split("#")[0]

    try:
        page.goto(base_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector(cfg["listing_selector"], timeout=15_000)
    except PlaywrightTimeout:
        logger.warning("Timeout loading '%s' – skipping.", base_url)
        return props

    configured_max = cfg.get("max_pages", 10)
    detected_max = _detect_last_hash_page(page, cfg.get("pagination_page_selector", ""))
    pages_to_scrape = min(configured_max, detected_max) if detected_max else configured_max

    logger.info(
        "Source '%s': hash pagination detected=%s, scraping up to %d page(s).",
        cfg.get("source"),
        detected_max if detected_max else "unknown",
        pages_to_scrape,
    )

    for page_num in range(1, pages_to_scrape + 1):
        page_url = base_url if page_num == 1 else f"{base_url}#page-{page_num}"
        logger.debug("Fetching page %d: %s", page_num, page_url)

        if page_num > 1:
            moved = _click_hash_page_link(
                page,
                cfg.get("pagination_page_selector", ""),
                page_num,
            )
            if not moved:
                try:
                    page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
                except PlaywrightTimeout:
                    logger.warning("Timeout loading '%s' – stopping hash pagination.", page_url)
                    break

            try:
                page.wait_for_url(f"**#page-{page_num}", timeout=5_000)
            except PlaywrightTimeout:
                logger.info(
                    "Page %d did not change its hash after pagination click; stopping at current page.",
                    page_num,
                )
                break

        try:
            page.wait_for_selector(cfg["listing_selector"], timeout=15_000)
        except PlaywrightTimeout:
            logger.warning("No listings on '%s' – stopping hash pagination.", page_url)
            break

        cards = page.query_selector_all(cfg["listing_selector"])
        logger.debug("Found %d cards on this page.", len(cards))

        for card in cards:
            prop = _extract_property(card, cfg, page)
            if prop and prop["property_id"] not in seen_property_ids:
                props.append(prop)
                seen_property_ids.add(prop["property_id"])

    return props


def _detect_last_hash_page(page: Page, selector: str) -> int | None:
    """Detect the last page number from hash pagination links like '#page-5'."""
    if not selector:
        return None

    page_numbers: List[int] = []
    for link in page.query_selector_all(selector):
        href = link.get_attribute("href") or ""
        text = (link.inner_text() or "").strip()

        href_match = re.search(r"#page-(\d+)", href)
        if href_match:
            page_numbers.append(int(href_match.group(1)))
            continue

        text_match = re.fullmatch(r"\d+", text)
        if text_match:
            page_numbers.append(int(text))

    return max(page_numbers) if page_numbers else None


def _detect_last_button_page(page: Page, selector: str) -> int | None:
    """Detect the last page number from numbered pagination buttons."""
    if not selector:
        return None

    page_numbers: List[int] = []
    for button in page.query_selector_all(selector):
        text = (button.inner_text() or "").strip()
        if re.fullmatch(r"\d+", text):
            page_numbers.append(int(text))

    return max(page_numbers) if page_numbers else None


def _click_hash_page_link(page: Page, selector: str, page_num: int) -> bool:
    """Click a hash pagination link by page number."""
    if not selector:
        return False

    target = str(page_num)
    for link in page.query_selector_all(selector):
        text = (link.inner_text() or "").strip()
        if text == target:
            try:
                link.evaluate("el => el.click()")
                return True
            except Exception:
                return False
    return False


def _click_next_button(page: Page, selector: str, next_texts: set[str]) -> bool:
    """Click the button that advances to the next page."""
    if not selector:
        return False

    for button in page.query_selector_all(selector):
        text = (button.inner_text() or "").strip().lower()
        if text in next_texts:
            try:
                disabled = button.get_attribute("disabled") is not None or (button.get_attribute("aria-disabled") or "").lower() == "true"
                if disabled:
                    return False
                button.evaluate("el => el.click()")
                return True
            except Exception:
                return False
    return False


def _first_card_href(page: Page, cfg: Dict) -> str:
    """Return href of the first listing link currently visible, if any."""
    first_link = page.query_selector(cfg["link_selector"])
    return (first_link.get_attribute("href") or "") if first_link else ""


def _extract_property(card, cfg: Dict, page: Page | None = None) -> Dict | None:
    """Extract a single property dict from a Playwright element handle."""
    try:
        if cfg.get("parse_mode") == "soler_finques":
            return _extract_soler_finques_property(card, cfg)
        if cfg.get("parse_mode") == "bachs_finques":
            return _extract_bachs_finques_property(card, cfg, page)
            if cfg.get("parse_mode") == "organ":
                return _extract_organ_property(card, cfg)

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
        price_text = price_el.inner_text() if price_el else ""
        if not price_text or "€" not in price_text:
            price_candidates = [
                (el.text_content() or "").strip()
                for el in card.query_selector_all("*")
                if "€" in (el.text_content() or "")
            ]
            price_text = price_candidates[0] if price_candidates else price_text
        price = _parse_price(price_text)

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
            "property_type": cfg.get("property_type"),
            "operation": cfg.get("operation"),
            "city": cfg.get("city"),
            "district": cfg.get("district"),
            "neighborhood": cfg.get("neighborhood"),
            "postal_code": cfg.get("postal_code"),
            "latitude": cfg.get("latitude"),
            "longitude": cfg.get("longitude"),
            "energy_rating": cfg.get("energy_rating"),
            "year_built": cfg.get("year_built"),
            "floor": cfg.get("floor"),
            "terrace": int(bool(cfg.get("terrace", False))),
            "elevator": int(bool(cfg.get("elevator", False))),
            "parking": int(bool(cfg.get("parking", False))),
            "is_favourite": int(bool(cfg.get("is_favourite", False))),
        }
    except Exception as exc:
        logger.warning("Could not parse a property card: %s", exc)
        return None


def _extract_bachs_finques_property(card, cfg: Dict, page: Page | None) -> Dict | None:
    """Extract Finques Bachs property data, using the detail page for sqm."""
    try:
        title_el = card.query_selector(cfg["title_selector"])
        title = title_el.inner_text().strip() if title_el else ""

        link_el = card.query_selector(cfg["link_selector"])
        url = link_el.get_attribute("href") if link_el else ""
        if url and not url.startswith("http"):
            from urllib.parse import urljoin
            url = urljoin(cfg["base_url"], url)

        price_el = card.query_selector(cfg["price_selector"])
        price_text = price_el.inner_text() if price_el else ""
        price = _parse_price(price_text)

        rooms_el = card.query_selector(cfg["rooms_selector"])
        rooms = _parse_int(rooms_el.inner_text() if rooms_el else "")

        bath_el = card.query_selector(cfg["bathrooms_selector"])
        bathrooms = _parse_int(bath_el.inner_text() if bath_el else "")

        full_text = card.inner_text().lower()

        sqm = None
        if page and url:
            current_url = page.url
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                body_text = page.locator("body").inner_text()
                rooms_match = re.search(r"Habitaciones:\s*(\d+)", body_text, re.IGNORECASE)
                bath_match = re.search(r"Baños:\s*(\d+)", body_text, re.IGNORECASE)
                sqm_match = re.search(r"superficie de\s+(\d+)\s*m²", body_text, re.IGNORECASE)
                if not sqm_match:
                    sqm_match = re.search(r"(\d+)\s*m²", body_text, re.IGNORECASE)
                if rooms_match:
                    rooms = int(rooms_match.group(1))
                if bath_match:
                    bathrooms = int(bath_match.group(1))
                sqm = int(sqm_match.group(1)) if sqm_match else None
            finally:
                try:
                    page.goto(current_url, wait_until="domcontentloaded", timeout=30_000)
                except PlaywrightTimeout:
                    pass

        has_pool = cfg.get("pool_keyword", "piscina") in full_text
        has_ac = cfg.get("ac_keyword", "aire acondicionado") in full_text

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
            "orientation": None,
            "property_type": cfg.get("property_type"),
            "operation": cfg.get("operation"),
            "city": cfg.get("city"),
            "district": cfg.get("district"),
            "neighborhood": cfg.get("neighborhood"),
            "postal_code": cfg.get("postal_code"),
            "latitude": cfg.get("latitude"),
            "longitude": cfg.get("longitude"),
            "energy_rating": cfg.get("energy_rating"),
            "year_built": cfg.get("year_built"),
            "floor": cfg.get("floor"),
            "terrace": int(bool(cfg.get("terrace", False))),
            "elevator": int(bool(cfg.get("elevator", False))),
            "parking": int(bool(cfg.get("parking", False))),
            "is_favourite": int(bool(cfg.get("is_favourite", False))),
        }
    except Exception as exc:
        logger.warning("Could not parse a Finques Bachs card: %s", exc)
        return None


def _extract_soler_finques_property(card, cfg: Dict) -> Dict | None:
    """Extract a Soler Finques property card using ordered metrics."""
    try:
        title_el = card.query_selector(cfg["title_selector"])
        title = title_el.inner_text().strip() if title_el else ""

        link_el = card.query_selector(cfg["link_selector"])
        url = link_el.get_attribute("href") if link_el else ""
        if url and not url.startswith("http"):
            from urllib.parse import urljoin
            url = urljoin(cfg["base_url"], url)

        price_el = card.query_selector(cfg["price_selector"])
        price = _parse_price(price_el.inner_text() if price_el else "")

        metric_texts = [
            (el.text_content() or "").strip()
            for el in card.query_selector_all("div.mt-auto p")
            if (el.text_content() or "").strip()
        ]

        rooms = None
        bathrooms = None
        sqm = None

        if len(metric_texts) >= 4:
            rooms = _parse_int(metric_texts[0])
            bathrooms = _parse_int(metric_texts[1])
            sqm = _parse_int(metric_texts[2])
        elif len(metric_texts) == 3:
            rooms = _parse_int(metric_texts[0])
            bathrooms = _parse_int(metric_texts[1])
            sqm = _parse_int(metric_texts[2])
        elif len(metric_texts) == 2:
            sqm = _parse_int(metric_texts[0])
            rooms = _parse_int(title) if "habs" in title.lower() or "habit" in title.lower() else None

        full_text = card.inner_text().lower()
        has_pool = cfg.get("pool_keyword", "piscina") in full_text
        has_ac = cfg.get("ac_keyword", "aire acondicionado") in full_text

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
            "orientation": None,
            "property_type": cfg.get("property_type"),
            "operation": cfg.get("operation"),
            "city": cfg.get("city"),
            "district": cfg.get("district"),
            "neighborhood": cfg.get("neighborhood"),
            "postal_code": cfg.get("postal_code"),
            "latitude": cfg.get("latitude"),
            "longitude": cfg.get("longitude"),
            "energy_rating": cfg.get("energy_rating"),
            "year_built": cfg.get("year_built"),
            "floor": cfg.get("floor"),
            "terrace": int(bool(cfg.get("terrace", False))),
            "elevator": int(bool(cfg.get("elevator", False))),
            "parking": int(bool(cfg.get("parking", False))),
            "is_favourite": int(bool(cfg.get("is_favourite", False))),
        }
    except Exception as exc:
        logger.warning("Could not parse a Soler Finques card: %s", exc)
        return None


def _extract_organ_property(card, cfg: Dict) -> Dict | None:
    """Extract a property card from Organ listings (data present in the card)."""
    try:
        title_el = card.query_selector(cfg["title_selector"])
        title = title_el.inner_text().strip() if title_el else ""

        link_el = card.query_selector(cfg["link_selector"])
        url = link_el.get_attribute("href") if link_el else ""
        if url and not url.startswith("http"):
            from urllib.parse import urljoin
            url = urljoin(cfg["base_url"], url)

        price_el = card.query_selector(cfg["price_selector"])
        price = _parse_price(price_el.inner_text() if price_el else "")

        # Card text contains '9 m2', '1 banys', '10 Hab.' etc.
        txt = card.inner_text()

        # Rooms: look for explicit 'Hab' labels first to avoid matching sqm numbers.
        rooms = None
        rooms_match = re.search(r"(\d+)\s*(?:Hab(?:\.|)|Hab|Habitat|Habits|Hab\b)", txt, re.IGNORECASE)
        if rooms_match:
            rooms = int(rooms_match.group(1))

        # Bathrooms: look for 'banys' (cat) or 'baños' (es)
        bathrooms = None
        bath_match = re.search(r"(\d+)\s*(?:banys|ba\u00f1os|ba\w*)", txt, re.IGNORECASE)
        if bath_match:
            bathrooms = int(bath_match.group(1))

        # Surface area: parse after trying to get rooms/bathrooms
        sqm = None
        sqm_match = re.search(r"(\d+[\d\.,]*)\s*(?:m(?:²|2))", txt, re.IGNORECASE)
        if sqm_match:
            sqm = _parse_int(sqm_match.group(1))

        has_pool = bool(re.search(r"piscina", txt, re.IGNORECASE))
        has_ac = bool(re.search(r"aire acondicionado|aire condicionat", txt, re.IGNORECASE))

        property_id = _build_id(cfg["source"], url)
        if not property_id or not url:
            logger.debug("Skipping Organ card with no URL.")
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
            "orientation": None,
            "property_type": None,
            "operation": None,
            "city": cfg.get("city"),
        }
    except Exception as exc:
        logger.warning("Could not parse an Organ card: %s", exc)
        return None


def _parse_price(text: str) -> int | None:
    """Extract a numeric price from a string like '450.000 €' or '€ 450,000'."""
    cleaned = text.strip()
    if not cleaned:
        return None

    cleaned = cleaned.replace("€", "").replace(" ", "")
    cleaned = cleaned.replace(".", "").replace(",", ".")
    cleaned = re.sub(r"[^\d.]", "", cleaned)

    try:
        return float(cleaned)
    except ValueError:
        digits = re.sub(r"[^\d]", "", text)
        return float(digits) if digits else None


def _parse_int(text: str) -> int | None:
    """Extract the first integer found in *text*."""
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def _build_id(source: str, url: str) -> str:
    """Build a stable unique ID from source name and URL."""
    # Use the last path segment (or query) as a slug
    slug = url.rstrip("/").split("/")[-1].split("?")[0]
    return f"{source}_{slug}" if slug else ""
