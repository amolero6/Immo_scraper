"""
enrich_details.py
-----------------
Fetch full detail-page data (description, features, year built when shown)
for the listings that matter — across ALL sources (Idealista, Yaencontre and
the local agencies), not just Idealista — and store them in the population
database.

The full description is where motivated-seller signals live ("herencia",
"urge vender", "para reformar", "precio negociable", …) plus features the
cards don't carry (terraza, ascensor, parking, planta, orientación...).

Usage (run occasionally, e.g. 2-3x/week, after the main scrape):
    python enrich_details.py sant_cugat --limit 40
    python enrich_details.py cerdanyola --min-price 400000 --max-price 700000
    python enrich_details.py sant_cugat --property-ids @sant_cugat_enrich_shortlist.txt

Safety: attaches to your existing Chrome via CDP (port 9222) like
run_idealista_headful.py, visits pages with human-like random delays, and
pauses for you to solve a captcha whenever one appears (capped at
MAX_CAPTCHA_PROMPTS per run so a bad run can't hang forever). Only fetches
listings that don't yet have a long description, so repeated runs are cheap.

Selector coverage: several small agencies (qgat_homes, mashomes,
fincas_cano_pujol) share the exact same site template, so one selector set
covers all three. Yaencontre sits behind bot-detection (Datadome) and needs
the real, already-authenticated browser session — same as the main scraper.
Anything not explicitly listed in SOURCE_DESCRIPTION_SELECTORS falls back to
GENERIC_DESCRIPTION_SELECTORS, then an XPath anchor on a heading literally
saying "Descripció(n)", then the longest <p> on the page — so a brand new
agency added later still gets *something* without code changes, even if
imperfectly.
"""
from __future__ import annotations

import argparse
import logging
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from database import init_db, update_property_details, mark_enriched
import database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger(__name__)

DELAY_RANGE = (8.0, 20.0)   # seconds between detail pages
MAX_CAPTCHA_PROMPTS = 5     # safety cap: solve up to this many verification pages per run

CAPTCHA_MARKERS = re.compile(
    r"captcha|verificaci[oó]n|robot|unusual traffic|geo.captcha", re.IGNORECASE
)

# Per-source description selectors, tried in order (first non-empty wins).
# qgat_homes / mashomes / fincas_cano_pujol are confirmed to share one CMS
# template (same ".IDDescripcionBig" class across all three).
SOURCE_DESCRIPTION_SELECTORS: dict[str, list[str]] = {
    "idealista_local": ["div.comment p", "div.comment", "[class*='comment'] p"],
    "idealista": ["div.comment p", "div.comment", "[class*='comment'] p"],
    "qgat_homes": [".IDDescripcionBig"],
    "mashomes": [".IDDescripcionBig"],
    "fincas_cano_pujol": [".IDDescripcionBig"],
    "fincas_moragas": [".des-inmueble p.wp-block-paragraph", "p.wp-block-paragraph"],
    "fincas_calvo": [".descripcion"],
    # aproperties splits the description across several sibling <p> tags with
    # no single clean container, and its only ".description"-named element is
    # actually the NEIGHBORHOOD blurb ("Acerca de Valldoreix") — deliberately
    # left with no selector here so it falls through to the meta-description
    # / longest-<p> fallbacks instead of confidently grabbing the wrong text.
}

# Tried for any source (including as a follow-up if the specific selectors
# above miss, and for any agency not yet in the dict above). Deliberately
# excludes a bare "[class*='descrip']" (no descendant restriction) — that
# also matches BEM-named amenity-list containers like
# "description__featuresCaractList" (aproperties), grabbing a feature list
# instead of prose.
GENERIC_DESCRIPTION_SELECTORS = [
    "div.description", ".descripcion", ".description-text",
    "[class*='descrip'] p", "article p",
]
# Last-resort XPath: the first <p> that appears anywhere after a heading
# whose text contains "Descripci" (covers Vue/Nuxt-rendered sites like
# finquessoler where class names are generic Tailwind utility classes with
# no stable selector, but the heading text + DOM order are reliable).
_DESCRIPTION_HEADING_XPATH = (
    "xpath=//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6]"
    "[contains(translate(text(), 'DESCRIPCIÓ', 'descripció'), 'descripci')]/following::p[1]"
)

# Legal/cookie-consent boilerplate is often the single longest <p> on a
# Spanish site (GDPR/RGPD notices run long) — exclude it from the "longest
# <p>" last-resort fallback so it doesn't beat the real (shorter, chunked)
# description paragraphs.
_BOILERPLATE_RE = re.compile(
    r"reglamento \(ue\)|\brgpd\b|\bgdpr\b|pol[ií]tica de (privacidad|cookies)|"
    r"aviso legal|protecci[oó]n de datos|utilizamos cookies|esta web utiliza cookies",
    re.IGNORECASE,
)

# Containers known to hold text that LOOKS like a description but isn't one
# (confirmed on aproperties: a "Sobre el barrio de X" neighborhood blurb that
# is often longer than the actual property description's own paragraphs, so
# the "longest <p>" heuristic below would otherwise grab the wrong text).
_NON_DESCRIPTION_ANCESTOR_SELECTORS = [".district_container__content"]


TARGET_MIN, TARGET_MAX = 500_000, 600_000  # your tighter objective band, prioritized first


def pick_targets(
    min_price: int, max_price: int, limit: int,
    property_ids: list[str] | None = None, skip_enriched: bool = False,
) -> list[dict]:
    """
    Choose which active listings to enrich.

    With `property_ids`: fetch exactly those. By default this force-refreshes
    them (bypasses the "already has a description" skip) — right for a
    pre-visit refresh of one listing. Pass `skip_enriched=True` (used by the
    shortlist `@file` mode) to instead SKIP any that already have a long
    description, so a batch run only spends its detail-page budget on listings
    that are genuinely new / not yet enriched.

    Otherwise: pick from active in-band listings still missing a description,
    prioritizing (1) the tighter 500-600k objective band over the wider
    400-700k search band, then (2) the most recently listed first — those are
    the ones you haven't seen enriched yet and are most likely to matter for
    an imminent decision. Cheapest-first (the old order) tended to burn the
    enrichment budget on stock you'd already looked at repeatedly.
    """
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    # `property_ids is not None` means the caller explicitly requested id-list
    # mode (a comma list or an @file). An EMPTY such list means "nothing to do"
    # — it must NOT fall through to the default "grab 40 unenriched" branch,
    # which is what happened when a fully-worked-through shortlist file was
    # passed (all comments, 0 ids) and 40 arbitrary listings got picked up.
    if property_ids is not None:
        if not property_ids:
            conn.close()
            return []
        placeholders = ",".join("?" * len(property_ids))
        # "already enriched" = its detail page has been VISITED (enriched_at set),
        # NOT "has a >=400 char description" — some agencies have short but
        # complete descriptions and would otherwise loop forever.
        enriched_clause = " AND enriched_at IS NULL" if skip_enriched else ""
        rows = conn.execute(
            f"""
            SELECT property_id, url, price, title, source
            FROM properties
            WHERE property_id IN ({placeholders}) AND url IS NOT NULL{enriched_clause}
            """,
            property_ids,
        ).fetchall()
    else:
        # NB: no longer restricted to idealista — every source has a selector
        # (specific or generic fallback) since SOURCE_DESCRIPTION_SELECTORS /
        # GENERIC_DESCRIPTION_SELECTORS were added.
        rows = conn.execute(
            """
            SELECT property_id, url, price, title, source
            FROM properties
            WHERE status = 'active'
              AND price BETWEEN ? AND ?
              AND url IS NOT NULL
              AND enriched_at IS NULL
            ORDER BY
              CASE WHEN price BETWEEN ? AND ? THEN 0 ELSE 1 END,
              first_seen DESC
            LIMIT ?
            """,
            (min_price, max_price, TARGET_MIN, TARGET_MAX, limit),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _is_excluded_ancestor(el) -> bool:
    """True if `el` sits inside a known false-positive container (e.g. a
    neighborhood blurb that isn't the property's own description)."""
    if not _NON_DESCRIPTION_ANCESTOR_SELECTORS:
        return False
    exclude_selector = ", ".join(_NON_DESCRIPTION_ANCESTOR_SELECTORS)
    return bool(el.evaluate("(el, sel) => !!el.closest(sel)", exclude_selector))


def _extract_description(page, source: str) -> Optional[str]:
    # Every tier below is checked against _is_excluded_ancestor — a class-name
    # match alone isn't enough evidence on sites (aproperties) whose "description"
    # class is reused for an unrelated neighborhood blurb.
    for selector in SOURCE_DESCRIPTION_SELECTORS.get(source, []):
        el = page.query_selector(selector)
        if el and not _is_excluded_ancestor(el):
            text = (el.inner_text() or "").strip()
            if len(text) > 40:
                return text

    for selector in GENERIC_DESCRIPTION_SELECTORS:
        el = page.query_selector(selector)
        if el and not _is_excluded_ancestor(el):
            text = (el.inner_text() or "").strip()
            if len(text) > 40:
                return text

    try:
        el = page.query_selector(_DESCRIPTION_HEADING_XPATH)
        if el and not _is_excluded_ancestor(el):
            text = (el.inner_text() or "").strip()
            if len(text) > 40:
                return text
    except Exception:
        pass

    # Prefer the SEO <meta name="description"> here: it's near-universal and
    # curated by the site to summarize THIS property (good signal, if often
    # truncated ~150-250 chars). Deliberately tried BEFORE "longest <p>" —
    # a page with cookie/privacy/terms-of-service paragraphs (common on
    # Spanish sites, several distinct boilerplate types beyond just GDPR
    # notices) can easily out-length the real, shorter, chunked description,
    # and a blind "longest wins" comparison lost to legal text in practice.
    meta_el = page.query_selector("meta[name='description']")
    if meta_el and not _is_excluded_ancestor(meta_el):
        content = (meta_el.get_attribute("content") or "").strip()
        if len(content) > 40 and not _BOILERPLATE_RE.search(content):
            return content

    # Last resort only: longest <p>, excluding known false-positive
    # containers and legal/cookie boilerplate.
    exclude_selector = ", ".join(_NON_DESCRIPTION_ANCESTOR_SELECTORS)
    candidates = page.eval_on_selector_all(
        "p",
        """(els, excludeSel) => els
            .filter(el => !excludeSel || !el.closest(excludeSel))
            .map(el => el.innerText.trim())""",
        exclude_selector,
    )
    candidates = [t for t in candidates if len(t) > 80 and not _BOILERPLATE_RE.search(t)]
    return max(candidates, key=len) if candidates else None


def extract_details(page, source: str) -> dict:
    """
    Pull description + feature list from a detail page, whatever the source.
    Tries source-specific selectors first (several small agencies share a
    site template), then generic fallbacks — see module docstring.
    """
    out: dict = {}

    description = _extract_description(page, source)
    if description:
        out["description"] = description[:6000]

    # Idealista exposes a clean structured feature list; everywhere else, we
    # fall back to regex over the whole visible page text, which is noisier
    # but works across arbitrary site structures without per-site selectors.
    feature_texts = [
        (el.inner_text() or "").strip()
        for el in page.query_selector_all("div.details-property li, .details-property_features li")
    ]
    blob = " | ".join(feature_texts)
    if not blob:
        try:
            blob = page.locator("body").inner_text(timeout=5_000)
        except Exception:
            blob = ""

    if blob:
        if re.search(r"terraza|terrassa", blob, re.IGNORECASE):
            out["terrace"] = 1
        if re.search(r"ascensor", blob, re.IGNORECASE):
            out["elevator"] = 1
        if re.search(r"garaje|parking|p[aá]rking|plaza de garaje", blob, re.IGNORECASE):
            out["parking"] = 1
        year_match = re.search(
            r"(?:construi|constru[iï]|any de construcci)[a-zóï]*[^\d]{0,15}(\d{4})",
            blob, re.IGNORECASE,
        )
        if year_match:
            year = int(year_match.group(1))
            if 1800 <= year <= 2026:
                out["year_built"] = year
        floor_match = re.search(r"(Planta \d+[ªa]?|Bajo|Entresuelo|[ÁA]tico)", blob)
        if floor_match:
            out["floor"] = floor_match.group(1)
        energy_match = re.search(r"[Cc]alificaci[oó]n energ[eé]tica[:\s]*([A-G])\b", blob)
        if energy_match:
            out["energy_rating"] = energy_match.group(1)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich in-band listings with detail-page data.")
    parser.add_argument("population", choices=["sant_cugat", "sant_quirze", "cerdanyola"])
    parser.add_argument("--limit", type=int, default=40, help="Max detail pages per run")
    parser.add_argument("--min-price", type=int, default=400_000)
    parser.add_argument("--max-price", type=int, default=700_000)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument(
        "--property-ids", default=None,
        help="Comma-separated property_id(s) to force-refresh right now, e.g. before a visit "
             "(bypasses the 'already has description' skip): --property-ids idealista_123,idealista_456. "
             "Or read a list from a file with '@path', e.g. --property-ids @sant_cugat_enrich_shortlist.txt "
             "(the shortlist build_report.py writes each run — one property_id per line, '#' lines ignored). "
             "In '@file' (batch) mode, already-enriched listings are SKIPPED so only new entrants get "
             "visited; add --force to re-enrich everything in the file anyway.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="In '@file' mode, re-enrich listings even if they already have a description "
             "(default: skip already-enriched, so a batch run only hits new entrants).",
    )
    args = parser.parse_args()

    init_db(population=args.population)
    ids = None
    skip_enriched = False
    if args.property_ids:
        if args.property_ids.startswith("@"):
            shortlist_path = Path(args.property_ids[1:])
            ids = [
                line.strip() for line in shortlist_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            # Batch/shortlist mode: only enrich the not-yet-enriched, unless --force.
            skip_enriched = not args.force
            logger.info(
                "Loaded %d property_id(s) from %s (skip already-enriched: %s)",
                len(ids), shortlist_path, skip_enriched,
            )
        else:
            ids = [pid.strip() for pid in args.property_ids.split(",")]  # explicit: force-refresh
    targets = pick_targets(
        args.min_price, args.max_price, args.limit,
        property_ids=ids, skip_enriched=skip_enriched,
    )
    if not targets:
        logger.info("Nothing to enrich — everything requested is already enriched (or nothing matched).")
        return 0
    logger.info("Enriching %d listings for %s …", len(targets), args.population)

    enriched = 0
    with sync_playwright() as pw:
        browser = None
        connected_via_cdp = False
        try:
            browser = pw.chromium.connect_over_cdp(args.cdp_url)
            connected_via_cdp = True
            logger.info("Attached to Chrome via CDP at %s", args.cdp_url)
        except Exception as exc:
            logger.info("CDP attach failed (%s); launching headed Chrome.", exc)
            browser = pw.chromium.launch(
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )
        context = browser.contexts[0] if browser.contexts else browser.new_context(
            locale="es-ES", timezone_id="Europe/Madrid",
        )
        page = context.pages[0] if context.pages else context.new_page()

        captcha_prompts = 0
        for target in targets:
            try:
                page.goto(target["url"], wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:
                logger.warning("Failed to open %s: %s", target["url"], exc)
                continue

            body = ""
            try:
                body = page.locator("body").inner_text(timeout=5_000)[:2000]
            except Exception:
                pass
            if CAPTCHA_MARKERS.search(body or ""):
                if captcha_prompts >= MAX_CAPTCHA_PROMPTS:
                    logger.warning(
                        "Too many verification pages this run (%d) — stopping to stay safe. Enriched %d so far.",
                        captcha_prompts, enriched,
                    )
                    break
                captcha_prompts += 1
                input(
                    f"Verification page detected ({target.get('source', '?')}, "
                    f"{captcha_prompts}/{MAX_CAPTCHA_PROMPTS}). Solve it in the browser, then press Enter…"
                )

            details = extract_details(page, target.get("source", ""))
            if details:
                if update_property_details(target["property_id"], **details):
                    enriched += 1
                    logger.info(
                        "  + %s (%s €): %s chars desc, extras=%s",
                        target["property_id"], target["price"],
                        len(details.get("description", "")),
                        [k for k in details if k != "description"],
                    )
            else:
                logger.info("  - %s: no details extracted (short/no description on page)", target["property_id"])

            # We reached this point, so the page loaded and wasn't a blocking
            # captcha: mark the detail page as visited regardless of how much we
            # extracted. This is what stops short-description listings (some
            # agencies) from being re-visited every single run.
            mark_enriched(target["property_id"])

            time.sleep(random.uniform(*DELAY_RANGE))

        try:
            if connected_via_cdp:
                browser.disconnect()
            else:
                browser.close()
        except Exception:
            pass

    logger.info("Done. Enriched %d/%d listings.", enriched, len(targets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
