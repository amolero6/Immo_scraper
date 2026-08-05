"""
analysis.py
-----------
Shared analysis library for the Immo Scraper project.

Single source of truth used by the parametrized notebooks
(`market_analysis.ipynb`, `rank_offer_candidates.ipynb`) so the same logic
serves every population (sant_cugat, sant_quirze, cerdanyola).

Main building blocks
    load_data()             raw tables from the population DB
    add_effective_age()     relist detection -> true days-on-market
    cluster_listings()      cross-source dedup + multi-agency signal
    build_daily_panel()     reconstructed day x property active/price panel
    build_features()        per-listing analytical features
    fit_hedonic()           fair-value model (log price ~ characteristics)
    kaplan_meier()          survival curve of time-to-delist
    compute_market_daily()  daily market indicators (inventory, flows, cuts, supply)
    market_timing_verdict() "is this a good month to buy?" signals
    load_notariado() / gap_analysis()   closing-price baselines vs asking
    Affordability           mortgage / cash math for your budget
    estimate_offer()        per-listing opening/target/walkaway offer buckets

All prices scraped are ASKING prices; the notary baselines are CLOSING prices.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent

POPULATIONS: Dict[str, Dict] = {
    "sant_cugat": {
        "db": "immo_scraper.db",
        "label": "Sant Cugat del Vallès",
        "city_pattern": r"sant\s*cugat",
        "notariado_keys": ["sant_cugat", "zip_08173", "zip_08195"],
        # titles to exclude (zones you already ruled out / non-residential)
        "exclude_title_pattern": r"Centre|Estació|Monestir|Nau|El\s+Coll|Sant Francesc|Terreny|Terreno|Parcela|Parcel·la|Les\s+Planes",
    },
    "sant_quirze": {
        "db": "immo_scraper_sant_quirze.db",
        "label": "Sant Quirze del Vallès",
        "city_pattern": r"sant\s*quirze",
        "notariado_keys": [],
        "exclude_title_pattern": None,
    },
    "cerdanyola": {
        "db": "immo_scraper_cerdanyola.db",
        "label": "Cerdanyola del Vallès",
        "city_pattern": r"cerdanyola",
        "notariado_keys": [],
        "exclude_title_pattern": None,
    },
}

# Wide search band: opportunities below 500k (e.g. 450k + renovation) and
# inflated asking prices up to 700k that may close near your budget.
SEARCH_BAND: Tuple[int, int] = (400_000, 700_000)
# Broad band for general market-trend context (not filtered to your budget) —
# excludes bare land/parking-only listings below and ultra-luxury outliers above.
MARKET_TREND_BAND: Tuple[int, int] = (150_000, 1_500_000)
# What you actually want to pay (620k only if truly exceptional).
TARGET_BAND: Tuple[int, int] = (500_000, 600_000)
HARD_MAX_PRICE: int = 620_000
RENO_BAND: Tuple[int, int] = (400_000, 500_000)   # value-add candidates
RENO_BUDGET: int = 100_000

PORTAL_SOURCES = {"idealista_local", "yaencontre", "idealista"}

# Motivated-seller signals in title/description (Spanish + Catalan).
MOTIVATED_PATTERNS: Dict[str, str] = {
    "inheritance": r"herencia|her[eè]ncia",
    "urgency": r"\burge\b|urgente|urgent\b|traslado|trasllat",
    "negotiable": r"negociabl|escucha ofertas|escoltem ofertes|precio a convenir",
    "opportunity": r"oportunidad|oportunitat|ocasi[oó]n|chollo|rebajad|rebaixat",
    "renovation": r"a reformar|para reformar|per reformar|reforma integral|para actualizar|per actualitzar|origen\b",
    "investor": r"inversor|inversores|inversi[oó]n|rentabilidad",
    "divorce_or_sale_urgency": r"divorcio|divorci|liquidaci[oó]n",
}

# Known sub-areas per town, used to backfill the (almost always empty)
# structured `neighborhood` column from free-text titles. Curated from titles
# actually observed in the data rather than guessed, so matches are
# high-precision; a title that matches nothing is left blank rather than
# forcing a wrong guess.
NEIGHBORHOODS: Dict[str, List[str]] = {
    "sant_cugat": [
        "La Floresta", "Valldoreix", "Can Mates - Volpelleres", "Volpelleres", "Can Mates",
        "Coll Favà - Can Magí", "Coll Favà", "Coll Fava", "Can Magí",
        "El Coll - Sant Francesc", "El Coll", "Sant Francesc",
        "Centre - Estació", "Centre", "Estació", "Mira-sol", "Mirasol",
        "Can Matas", "Torreblanca", "Monestir", "Can Barata",
        "Parc Central - El Colomer - Pla de la Pagesa", "Parc Central", "El Colomer",
        "Pla de la Pagesa", "Sant Domènec", "Sant Domenec", "Mas Gener",
    ],
    "sant_quirze": [
        "Sant Quirze Parc-Vallsuau-Castellet", "Sant Quirze Parc- Vallsuau - Castellet",
        "Sant Quirze Parc", "Vallsuau", "Castellet", "Mas Duran", "Can Casablanques",
        "Can Llobateres - Can Pallars", "Can Pallàs - Can Llobateras",
        "Can Llobateres", "Can Llobateras", "Can Pallars", "Can Pallàs",
        "Poble Sec", "Les Fonts", "Cucut", "Centre",
    ],
    "cerdanyola": [
        "Centre - Cordelles", "Sant Ramon", "Sant Ramón", "Catalunya - Fontetes",
        "Altamira - Canaletes", "Guiera - Montflorit", "Serraparera", "Bellaterra",
        "La Clota - Zona industrial", "La Clota", "Centre",
    ],
}


def _extract_neighborhood(title: str, population: str) -> Optional[str]:
    """
    Best-effort neighborhood from free title text, matched against the
    curated NEIGHBORHOODS whitelist. Longest match wins so a specific name
    ('Coll Favà - Can Magí') beats a generic substring ('Centre') when both
    would match. Returns None (not a guess) if nothing in the whitelist hits.
    """
    candidates = NEIGHBORHOODS.get(population, [])
    if not candidates or not title:
        return None
    norm_title = _norm_text(title)
    hits = [name for name in candidates if _norm_text(name) in norm_title]
    if not hits:
        return None
    return max(hits, key=len)


# Listing TYPE tokens (anchored at the start of the title, where the property
# type normally sits) that mean "not a home" — commercial premises, industrial
# units, a bare storage room or parking spot listed on their own. Applied to
# every population, unlike the population-specific exclude_title_pattern.
NON_RESIDENTIAL_PATTERN = (
    r"^\s*(locales?\s+comerciales?|nau(\s+industrial)?|traster(o|os)\b|"
    r"plaza(s)?\s+de\s+(garaje|parking)|garaje(s)?\b)"
)

# Nearby municipalities: a listing whose title names one of these (and it isn't
# the population's own town) is an out-of-area card the portal slipped into the
# results — drop it, or it pollutes candidates AND market aggregates.
OTHER_MUNICIPALITIES = [
    "Barcelona", "Viladecavalls", "Santa Coloma de Gramenet", "Hostafrancs",
    "Horta", "Camp de l'Arpa", "Rubí", "Rubi", "Terrassa", "Sabadell", "Badalona",
    "Montcada", "Ripollet", "Barberà", "Barbera", "Castellar del Vallès",
    "Sentmenat", "Hospitalet", "Molins de Rei", "El Papiol", "Castellbisbal",
    "Sant Cugat", "Sant Quirze", "Cerdanyola",
]

# Positive quality signals only findable in free text (not on any portal card):
# light, views, condition, garden/storage, quiet, and whether it's exterior.
# Kept separate from MOTIVATED_PATTERNS since these describe the HOME, not
# the seller's situation.
QUALITY_TEXT_PATTERNS: Dict[str, str] = {
    "luminous": r"luminos[ao]|molta llum|mucha luz|gran luminosidad",
    "views": r"vistas? (a|al|a la)|vistes a|con vistas|amb vistes",
    "renovated": r"reformad[ao] integralmente|a estrenar|reci[eé]n reformad[ao]|reforma integral|obra nueva|acabat de reformar",
    "garden": r"jard[ií]n privad|jard[íi] privat|jard[ií]n comunitario",
    "storage": r"trastero|traster\b",
    "quiet": r"tranquil[oa]|silencios[ao]|sense soroll|sin ruido|zona tranquila",
    "exterior": r"\bexterior\b",
}

# Orientation and FGC/transit proximity need a captured value, not just a
# yes/no flag, so they get dedicated regexes instead of a QUALITY_TEXT_PATTERNS
# entry. The orientation regex anchors on "orientaci(ó/ón)" and looks for a
# compass word within a short window after it, to avoid "este" matching stray
# unrelated words elsewhere in a long description.
_ORIENTATION_RE = re.compile(
    r"orientaci\w*.{0,25}?\b(noreste|nordest|noroeste|nordoest|sureste|sudest|suroeste|sudoest|"
    r"nord|norte|sud|sur|est\b|este\b|oest\b|oeste\b)",
    re.IGNORECASE,
)
_ORIENTATION_CANON = {
    "nord": "norte", "norte": "norte", "sud": "sur", "sur": "sur",
    "est": "este", "este": "este", "oest": "oeste", "oeste": "oeste",
    "nordest": "noreste", "noreste": "noreste", "nordoest": "noroeste", "noroeste": "noroeste",
    "sudest": "sureste", "sureste": "sureste", "sudoest": "suroeste", "suroeste": "suroeste",
}
_WALK_MIN_RE = re.compile(
    r"(\d{1,2})\s*min\w*.{0,20}?(estaci[oó]n|fgc|tren|renfe)", re.IGNORECASE
)
_TRANSIT_PATTERN = r"\bfgc\b|estaci[oó]n (de )?(tren|fgc|renfe)|\brenfe\b|cerca del?\s*tren"
_STREET_RE = re.compile(
    r"\b((?:calle|avenida|avinguda|passeig|paseo|carrer|ronda|pla[cç]a|plaza|carretera)\s+[^,]+)",
    re.IGNORECASE,
)
_FEE_RE = re.compile(r"comunidad[:\s]*([\d][\d.,]*)\s*€", re.IGNORECASE)
_IBI_RE = re.compile(r"\bibi[:\s]*([\d][\d.,]*)\s*€", re.IGNORECASE)


def _parse_eu_amount(raw: Optional[str]) -> Optional[float]:
    """'1.250,50' / '1250' -> float. European thousands-dot, decimal-comma."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    cleaned = str(raw).replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


BRIDGE_DAYS = 5          # inactive gaps <= this (that later reactivate) are treated as scrape noise
RELIST_LOOKBACK_DAYS = 90
RELIST_PRICE_TOL = 0.03
RELIST_SQM_TOL = 3
CLUSTER_PRICE_TOL = 0.015
CLUSTER_SQM_TOL = 3
TITLE_SIM_THRESHOLD = 0.45

NOW_UTC = pd.Timestamp.now(tz="UTC")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _norm_text(text) -> str:
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return ""
    normalized = unicodedata.normalize("NFKD", str(text))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.lower().split())


def _title_sim(a: str, b: str) -> float:
    a, b = _norm_text(a), _norm_text(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# Portal titles share so much boilerplate ("… en venda a Sant Cugat del
# Vallès - Can Mates") that raw SequenceMatcher scores 0.9+ for listings that
# differ only in the one word that matters ("Pis" vs "Àtic"). These two
# extractors capture the discriminating words so matches can be VETOED on
# contradiction, regardless of price/sqm/title-similarity agreement.
_TYPE_PATTERNS = [
    ("atico", r"\b(?:atico|atic)\b"),
    ("planta_baja", r"\bplanta bai?xa\b|\bbaixos\b"),
    ("duplex", r"\bduplex\b"),
    ("estudio", r"\b(?:estudio|estudi|loft)\b"),
    ("casa", r"\b(?:casa|chalet|xalet|torre|masia|adosad\w+|adossad\w+|pareado|aparellad\w+)\b"),
    ("piso", r"\b(?:piso|pis|apartamento|apartament)\b"),
]
# type pairs that portals genuinely mix up for the same unit (idealista says
# "Piso" where an agency says "Planta baixa"/"Dúplex"/"Estudi")
_COMPATIBLE_TYPES = {
    frozenset({"piso", "planta_baja"}),
    frozenset({"piso", "duplex"}),
    frozenset({"piso", "estudio"}),
}
_STREET_KEYWORDS = (
    r"calle|carrer|avenida|avinguda|paseo|passeig|ronda|rambla|"
    r"plaza|placa|pasaje|passatge|camino|cami"
)
_STREET_STOP = {"de", "del", "d", "la", "el", "les", "los", "las", "dels", "en"}


def _title_type(title) -> str | None:
    t = _norm_text(title)
    if not t:
        return None
    for name, pat in _TYPE_PATTERNS:
        if re.search(pat, t):
            return name
    return None


def _street_tokens(title) -> set:
    t = _norm_text(title)
    if not t:
        return set()
    m = re.search(rf"\b(?:{_STREET_KEYWORDS})\b\s+(.+?)(?:,|$)", t)
    if not m:
        return set()
    toks = {w.strip("'") for w in re.split(r"[^a-z0-9']+", m.group(1)) if w}
    return {w for w in toks if len(w) > 1 and w not in _STREET_STOP and not w.isdigit()}


def _titles_contradict(title_a, title_b) -> bool:
    """True when two titles cannot be the same physical property: they declare
    different property types (piso vs atico vs casa…) or explicitly different
    streets. Used as a veto on relist/cluster matches — numeric tolerances
    (price/sqm/rooms) are too loose on their own for lookalike listings."""
    ta, tb = _title_type(title_a), _title_type(title_b)
    if ta and tb and ta != tb and frozenset({ta, tb}) not in _COMPATIBLE_TYPES:
        return True
    sa, sb = _street_tokens(title_a), _street_tokens(title_b)
    if sa and sb and not (sa & sb):
        return True
    return False


def _to_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def round_to(value: float, step: int = 5_000) -> int:
    return int(round(value / step) * step)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data(population: str, project_dir: Path = PROJECT_DIR) -> Dict[str, pd.DataFrame]:
    """Load properties, price_history, property_events (and runs) for a population."""
    cfg = POPULATIONS[population]
    db_path = project_dir / cfg["db"]
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        properties = pd.read_sql_query("SELECT * FROM properties", conn)
        history = pd.read_sql_query(
            "SELECT property_id, price, date FROM price_history", conn
        )
        events = pd.read_sql_query(
            "SELECT property_id, event_type, event_date, source, old_status, new_status, "
            "old_price, new_price FROM property_events",
            conn,
        )
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        runs = (
            pd.read_sql_query("SELECT * FROM runs", conn)
            if "runs" in tables
            else pd.DataFrame(columns=["run_date", "source", "listings_returned", "status"])
        )
    finally:
        conn.close()

    # Basic typing / cleaning
    for col in ["price", "price_first_seen", "rooms", "bathrooms", "sqm", "year_built",
                "has_pool", "has_ac", "terrace", "elevator", "parking",
                "similarity_score", "latitude", "longitude"]:
        if col in properties.columns:
            properties[col] = pd.to_numeric(properties[col], errors="coerce")
    properties["first_seen"] = _to_utc(properties["first_seen"])
    properties["last_seen"] = _to_utc(properties["last_seen"])

    exclude = cfg.get("exclude_title_pattern")
    if exclude:
        mask = properties["title"].fillna("").str.contains(exclude, case=False, regex=True)
        properties = properties.loc[~mask].reset_index(drop=True)

    non_res_mask = properties["title"].fillna("").str.contains(NON_RESIDENTIAL_PATTERN, case=False, regex=True)
    if non_res_mask.any():
        properties = properties.loc[~non_res_mask].reset_index(drop=True)

    # Drop out-of-area listings (portal "recommended" cards from other towns)
    foreign = [
        m for m in OTHER_MUNICIPALITIES
        if not re.search(cfg["city_pattern"], m, re.IGNORECASE)
    ]
    foreign_pattern = r"\b(" + "|".join(re.escape(m) for m in foreign) + r")\b"
    titles = properties["title"].fillna("")
    foreign_mask = titles.str.contains(foreign_pattern, case=False, regex=True)
    own_mask = titles.str.contains(cfg["city_pattern"], case=False, regex=True)
    drop_mask = foreign_mask & ~own_mask
    if drop_mask.any():
        properties = properties.loc[~drop_mask].reset_index(drop=True)

    # Sanity clamps on parse artifacts
    properties.loc[properties["rooms"] > 15, "rooms"] = np.nan
    properties.loc[properties["bathrooms"] > 10, "bathrooms"] = np.nan
    properties.loc[properties["sqm"] < 20, "sqm"] = np.nan

    # Backfill the (almost always empty) structured neighborhood column from
    # the title text — see NEIGHBORHOODS / _extract_neighborhood above.
    needs_fill = properties["neighborhood"].isna() | properties["neighborhood"].eq("")
    properties.loc[needs_fill, "neighborhood"] = properties.loc[needs_fill, "title"].map(
        lambda t: _extract_neighborhood(t, population)
    )

    history["date"] = _to_utc(history["date"])
    history["price"] = pd.to_numeric(history["price"], errors="coerce")
    history = history.dropna(subset=["property_id", "price", "date"]).sort_values(
        ["property_id", "date"]
    ).reset_index(drop=True)

    events["event_date"] = _to_utc(events["event_date"])

    # Flag the left-censored cohort (already listed when scraping started):
    # their first_seen is the scrape start, so days-online is a lower bound.
    first_day = properties["first_seen"].min().floor("D")
    properties["preexisting"] = (properties["first_seen"].dt.floor("D") == first_day).astype(int)

    return {"properties": properties, "history": history, "events": events, "runs": runs}


# ---------------------------------------------------------------------------
# Relist detection -> effective first_seen (true days-on-market)
# ---------------------------------------------------------------------------

def add_effective_age(properties: pd.DataFrame) -> pd.DataFrame:
    """
    Detect re-listings: a 'new' listing that matches a recently delisted one
    (same rooms, ~same sqm, ~same price, similar title) inherits the old
    listing's first_seen. Adds columns: effective_first_seen, relisted_from,
    relist_count, days_online_effective.
    """
    props = properties.copy()
    props["effective_first_seen"] = props["first_seen"]
    props["relisted_from"] = None
    props["relist_count"] = 0

    inactive = props[props["status"] == "inactive"]
    active = props[props["status"] == "active"]

    inact_records = inactive[
        ["property_id", "title", "price", "sqm", "rooms", "first_seen", "last_seen"]
    ].to_dict("records")

    for idx, row in active.iterrows():
        if pd.isna(row.get("price")) or pd.isna(row.get("sqm")):
            continue
        best = None
        for old in inact_records:
            if old["property_id"] == row["property_id"]:
                continue
            if pd.isna(old["price"]) or pd.isna(old["sqm"]) or pd.isna(old["last_seen"]):
                continue
            gap_days = (row["first_seen"] - old["last_seen"]).days
            if gap_days < -1 or gap_days > RELIST_LOOKBACK_DAYS:
                continue
            if not pd.isna(row.get("rooms")) and not pd.isna(old["rooms"]) and row["rooms"] != old["rooms"]:
                continue
            if abs(row["sqm"] - old["sqm"]) > RELIST_SQM_TOL:
                continue
            if abs(row["price"] - old["price"]) / max(old["price"], 1) > RELIST_PRICE_TOL:
                continue
            if _titles_contradict(row.get("title"), old.get("title")):
                continue
            sim = _title_sim(row.get("title"), old.get("title"))
            if sim < TITLE_SIM_THRESHOLD:
                continue
            if best is None or old["first_seen"] < best["first_seen"]:
                best = old
        if best is not None:
            props.at[idx, "effective_first_seen"] = min(row["first_seen"], best["first_seen"])
            props.at[idx, "relisted_from"] = best["property_id"]
            props.at[idx, "relist_count"] = 1

    props["days_online_effective"] = (
        (NOW_UTC - props["effective_first_seen"]).dt.total_seconds() / 86400
    ).round(1)
    return props


# ---------------------------------------------------------------------------
# Cross-source clustering (dedup + multi-agency signal)
# ---------------------------------------------------------------------------

def cluster_listings(properties: pd.DataFrame, active_only: bool = True) -> pd.DataFrame:
    """
    Group listings that are almost surely the same physical property published
    on several portals/agencies. Adds: cluster_id, cluster_size, n_sources,
    n_agency_sources, multi_listed (>=2 distinct agency sources OR >=3 sources).

    IMPORTANT: multiplicity is a *signal* (non-exclusive mandate -> motivated
    seller), not just noise to drop.
    """
    props = properties.copy()
    pool = props[props["status"] == "active"] if active_only else props
    pool = pool.dropna(subset=["price", "sqm"])

    parent: Dict = {pid: pid for pid in pool["property_id"]}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    records = pool[["property_id", "source", "title", "price", "sqm", "rooms"]].to_dict("records")
    # blocking by rooms + sqm bucket keeps this O(n) in practice
    blocks: Dict[Tuple, List[dict]] = {}
    for rec in records:
        rooms_key = int(rec["rooms"]) if not pd.isna(rec["rooms"]) else -1
        for bucket in {int(rec["sqm"] // 10), int((rec["sqm"] + CLUSTER_SQM_TOL) // 10),
                       int((rec["sqm"] - CLUSTER_SQM_TOL) // 10)}:
            blocks.setdefault((rooms_key, bucket), []).append(rec)

    seen_pairs = set()
    for block in blocks.values():
        for i in range(len(block)):
            for j in range(i + 1, len(block)):
                a, b = block[i], block[j]
                key = tuple(sorted((a["property_id"], b["property_id"])))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                if abs(a["sqm"] - b["sqm"]) > CLUSTER_SQM_TOL:
                    continue
                if abs(a["price"] - b["price"]) / max(a["price"], b["price"]) > CLUSTER_PRICE_TOL:
                    continue
                # hard veto: contradictory type/street can never be the same
                # unit, even at identical price (625k round prices collide)
                if _titles_contradict(a["title"], b["title"]):
                    continue
                same_price = a["price"] == b["price"]
                if not same_price and _title_sim(a["title"], b["title"]) < TITLE_SIM_THRESHOLD:
                    continue
                union(a["property_id"], b["property_id"])

    cluster_ids = {pid: find(pid) for pid in parent}
    props["cluster_id"] = props["property_id"].map(cluster_ids).fillna(props["property_id"])

    grp = props.groupby("cluster_id").agg(
        cluster_size=("property_id", "size"),
        n_sources=("source", "nunique"),
        n_agency_sources=("source", lambda s: s[~s.isin(PORTAL_SOURCES)].nunique()),
    )
    props = props.merge(grp, left_on="cluster_id", right_index=True, how="left")
    props["multi_listed"] = (
        (props["n_agency_sources"] >= 2) | (props["n_sources"] >= 3)
    ).astype(int)
    # canonical row per cluster: prefer the portal listing with richest data
    props["is_cluster_canonical"] = (
        props.sort_values(["cluster_id", "days_online_effective" if "days_online_effective" in props else "first_seen"],
                          ascending=[True, False])
        .groupby("cluster_id").cumcount().eq(0).astype(int)
    )
    return props


# ---------------------------------------------------------------------------
# Daily panel reconstruction
# ---------------------------------------------------------------------------

def build_daily_panel(
    properties: pd.DataFrame,
    history: pd.DataFrame,
    events: pd.DataFrame,
    bridge_days: int = BRIDGE_DAYS,
) -> pd.DataFrame:
    """
    Reconstruct, for every calendar day since scraping started, which
    properties were actively listed and at what prevailing asking price.

    This is the *correct* base for market-level daily aggregates. (Grouping
    price_history by day is biased: it only contains prices on days they
    changed.)

    Returns a long DataFrame: day (UTC midnight), property_id, price.
    Short inactive gaps (<= bridge_days) that later reactivate are bridged.
    """
    props = properties.copy()
    start_day = props["first_seen"].min().floor("D")
    end_day = NOW_UTC.floor("D")

    ev = events[events["event_type"].isin(["inactive", "reactivated"])].copy()
    ev["day"] = ev["event_date"].dt.floor("D")
    ev = ev.sort_values(["property_id", "event_date"])
    ev_by_pid: Dict[str, List[Tuple[pd.Timestamp, str]]] = {}
    for pid, grp in ev.groupby("property_id"):
        ev_by_pid[pid] = list(zip(grp["day"], grp["event_type"]))

    intervals: List[Tuple[str, pd.Timestamp, pd.Timestamp]] = []
    for row in props.itertuples(index=False):
        pid = row.property_id
        fs = row.first_seen.floor("D")
        segments: List[List[pd.Timestamp]] = [[fs, None]]
        for day, etype in ev_by_pid.get(pid, []):
            if etype == "inactive":
                if segments and segments[-1][1] is None:
                    segments[-1][1] = day
            else:  # reactivated
                if segments and segments[-1][1] is not None and (day - segments[-1][1]).days <= bridge_days:
                    segments[-1][1] = None  # bridge the noise gap
                elif not segments or segments[-1][1] is not None:
                    segments.append([day, None])
        if segments and segments[-1][1] is None:
            if row.status == "active":
                segments[-1][1] = end_day
            else:
                segments[-1][1] = max(row.last_seen.floor("D"), segments[-1][0])
        for seg_start, seg_end in segments:
            if seg_end is None:
                seg_end = end_day
            if seg_end >= seg_start:
                intervals.append((pid, seg_start, seg_end))

    frames = []
    for pid, s, e in intervals:
        days = pd.date_range(s, e, freq="D", tz="UTC")
        frames.append(pd.DataFrame({"day": days, "property_id": pid}))
    panel = pd.concat(frames, ignore_index=True)

    hist = history.rename(columns={"date": "ts"}).sort_values("ts")
    panel = panel.sort_values("day")
    panel = pd.merge_asof(
        panel, hist, left_on="day", right_on="ts", by="property_id", direction="backward"
    ).drop(columns=["ts"])

    # price fallback: first known price applies from listing start
    first_price = props.set_index("property_id")["price_first_seen"]
    panel["price"] = panel["price"].fillna(panel["property_id"].map(first_price))
    panel = panel[(panel["day"] >= start_day) & (panel["day"] <= end_day)]
    return panel.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Per-listing features
# ---------------------------------------------------------------------------

def build_features(data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Merge every per-listing analytical feature into one DataFrame."""
    props = add_effective_age(data["properties"])
    props = cluster_listings(props)
    history, events = data["history"], data["events"]

    def _hist_stats(grp: pd.DataFrame) -> pd.Series:
        grp = grp.sort_values("date")
        diffs = grp["price"].diff()
        cuts = diffs[diffs < 0]
        last_cut_ts = grp.loc[diffs < 0, "date"].max()
        return pd.Series({
            "n_price_changes": int(diffs.fillna(0).ne(0).sum()),
            "n_cuts": int((diffs < 0).sum()),
            "n_increases": int((diffs > 0).sum()),
            "total_cut_amount": float(-cuts.sum()) if len(cuts) else 0.0,
            "history_max_price": grp["price"].max(),
            "days_since_last_cut": (NOW_UTC - last_cut_ts).days if pd.notna(last_cut_ts) else np.nan,
        })

    hist_stats = (
        history.groupby("property_id", group_keys=False)
        .apply(_hist_stats, include_groups=False)
        .reset_index()
    )
    feats = props.merge(hist_stats, on="property_id", how="left")
    for col in ["n_price_changes", "n_cuts", "n_increases", "total_cut_amount"]:
        feats[col] = feats[col].fillna(0)

    react = (
        events[events["event_type"] == "reactivated"]
        .groupby("property_id").size().rename("n_reactivations")
    )
    feats = feats.merge(react, on="property_id", how="left")
    feats["n_reactivations"] = feats["n_reactivations"].fillna(0).astype(int)

    feats["price_per_sqm"] = feats["price"] / feats["sqm"]
    feats["cum_discount_pct"] = np.where(
        feats["price_first_seen"].gt(0),
        (feats["price_first_seen"] - feats["price"]) / feats["price_first_seen"] * 100,
        np.nan,
    ).round(2)

    # Motivated-seller keyword flags from title + description
    raw_text = feats["title"].fillna("") + " " + feats.get("description", pd.Series("", index=feats.index)).fillna("")
    text = raw_text.map(_norm_text)
    flags = []
    for name, pattern in MOTIVATED_PATTERNS.items():
        col = f"kw_{name}"
        feats[col] = text.str.contains(pattern, regex=True).astype(int)
        flags.append(col)
    feats["kw_any_motivated"] = feats[flags].max(axis=1)

    # Home-quality signals from free text: only available where enrich_details.py
    # has fetched a real description (currently a minority of listings) — treat
    # these as informational, NOT folded into quality_score, so un-enriched
    # (but possibly excellent) listings aren't penalized for missing data.
    quality_flags = []
    for name, pattern in QUALITY_TEXT_PATTERNS.items():
        col = f"kw_{name}"
        feats[col] = text.str.contains(pattern, regex=True).astype(int)
        quality_flags.append(col)

    orientation_raw = text.str.extract(_ORIENTATION_RE, expand=False)
    feats["orientation"] = feats["orientation"].where(
        feats["orientation"].notna() & feats["orientation"].ne(""),
        orientation_raw.map(lambda v: _ORIENTATION_CANON.get(v) if pd.notna(v) else None),
    )

    feats["kw_near_transit"] = text.str.contains(_TRANSIT_PATTERN, regex=True).astype(int)
    # _WALK_MIN_RE has 2 capture groups (minutes, station-word) so str.extract
    # always returns a 2-column DataFrame here regardless of expand=.
    walk_extracted = text.str.extract(_WALK_MIN_RE)
    feats["walk_min_to_station"] = pd.to_numeric(walk_extracted[0], errors="coerce")

    street_raw = feats["title"].fillna("").str.extract(_STREET_RE, expand=False)
    feats["street"] = street_raw.str.strip()

    feats["community_fee_eur_mo"] = raw_text.str.extract(_FEE_RE, expand=False).map(_parse_eu_amount)
    feats["ibi_eur_yr"] = raw_text.str.extract(_IBI_RE, expand=False).map(_parse_eu_amount)

    def _insights_line(row) -> str:
        # NB: bool(float('nan')) is True in Python, so a bare "if row.get(...)"
        # on a possibly-NaN extracted value (orientation/street) would silently
        # append the literal string "nan" — always check pd.notna() for those.
        parts = []
        if pd.notna(row.get("orientation")):
            parts.append(f"orientación {row['orientation']}")
        if row.get("kw_exterior"):
            parts.append("exterior")
        if row.get("kw_luminous"):
            parts.append("luminoso")
        if row.get("kw_views"):
            parts.append("vistas")
        if row.get("kw_renovated"):
            parts.append("reformado/a estrenar")
        if row.get("kw_garden"):
            parts.append("jardín")
        if row.get("kw_storage"):
            parts.append("trastero")
        if row.get("kw_quiet"):
            parts.append("tranquilo")
        if row.get("kw_near_transit"):
            wm = row.get("walk_min_to_station")
            parts.append(f"cerca FGC/tren ({wm:.0f} min)" if pd.notna(wm) else "cerca FGC/tren")
        if pd.notna(row.get("street")):
            parts.append(str(row["street"]))
        return " · ".join(parts)

    feats["text_insights"] = feats.apply(_insights_line, axis=1)

    return feats


# ---------------------------------------------------------------------------
# Survival analysis (Kaplan-Meier, no external deps)
# ---------------------------------------------------------------------------

def kaplan_meier(features: pd.DataFrame, exclude_preexisting: bool = True) -> pd.DataFrame:
    """
    Product-limit estimate of P(listing still active after t days).
    Delisted rows are events (duration = last_seen - effective_first_seen);
    active rows are right-censored at today. The pre-existing cohort (already
    listed when scraping began) is excluded by default: its durations are
    lower bounds and would bias survival downward.
    """
    df = features.copy()
    if exclude_preexisting and "preexisting" in df:
        df = df[df["preexisting"] == 0]
    start = df["effective_first_seen"] if "effective_first_seen" in df else df["first_seen"]
    end = df["last_seen"].where(df["status"] == "inactive", NOW_UTC)
    durations = ((end - start).dt.total_seconds() / 86400).clip(lower=0.5)
    observed = (df["status"] == "inactive").astype(int)

    tbl = pd.DataFrame({"t": durations.round(0), "e": observed}).dropna()
    if tbl.empty:
        return pd.DataFrame(columns=["t", "survival"])
    tbl = tbl.groupby("t").agg(events=("e", "sum"), n=("e", "size")).sort_index()
    at_risk = tbl["n"][::-1].cumsum()[::-1]
    surv = (1 - tbl["events"] / at_risk).cumprod()
    return pd.DataFrame({"t": surv.index.to_numpy(), "survival": surv.to_numpy()})


def survival_at(km: pd.DataFrame, days: float) -> float:
    """S(days): probability a typical listing is still on the market after `days`."""
    if km.empty or pd.isna(days):
        return np.nan
    below = km[km["t"] <= days]
    return float(below["survival"].iloc[-1]) if len(below) else 1.0


def staleness_percentile(km: pd.DataFrame, days: float) -> float:
    """1 - S(days): share of comparable listings that had already delisted by `days`.
    High value = this listing has outlived most of the market = seller stress."""
    s = survival_at(km, days)
    return float(1 - s) if not np.isnan(s) else np.nan


# ---------------------------------------------------------------------------
# Hedonic pricing model ("what should it cost given its characteristics")
# ---------------------------------------------------------------------------

HEDONIC_NUM = ["log_sqm", "rooms", "bathrooms", "has_pool", "has_ac", "terrace", "elevator", "parking"]


def fit_hedonic(features: pd.DataFrame) -> Dict:
    """
    Ridge regression of log(asking price) on characteristics, trained on all
    listings (active + delisted) with sane price/sqm. Adds nothing fancy —
    the point is an interpretable 'fair asking value' per listing and its
    residual: residual_pct > 0 means priced above what its features justify.
    """
    from sklearn.linear_model import Ridge

    df = features.copy()
    pool = df[
        df["price"].between(150_000, 2_000_000)
        & df["sqm"].between(30, 800)
    ].copy()
    pool["log_sqm"] = np.log(pool["sqm"])
    for col in ["rooms", "bathrooms"]:
        pool[col] = pool[col].fillna(pool[col].median())
    for col in ["has_pool", "has_ac", "terrace", "elevator", "parking"]:
        pool[col] = pool[col].fillna(0)

    X = pool[HEDONIC_NUM].to_numpy(dtype=float)
    y = np.log(pool["price"].to_numpy(dtype=float))
    model = Ridge(alpha=1.0)
    model.fit(X, y)
    r2 = model.score(X, y)

    # score every row that has the inputs
    scored = df[df["sqm"].between(30, 800)].copy()
    scored["log_sqm"] = np.log(scored["sqm"])
    for col in ["rooms", "bathrooms"]:
        scored[col] = scored[col].fillna(pool[col].median())
    for col in ["has_pool", "has_ac", "terrace", "elevator", "parking"]:
        scored[col] = scored[col].fillna(0)
    fair = np.exp(model.predict(scored[HEDONIC_NUM].to_numpy(dtype=float)))
    out = pd.Series(np.nan, index=df.index, name="hedonic_fair_price")
    out.loc[scored.index] = fair
    df["hedonic_fair_price"] = out.round(0)
    df["hedonic_residual_pct"] = ((df["price"] - df["hedonic_fair_price"]) / df["hedonic_fair_price"] * 100).round(2)
    return {"model": model, "r2": r2, "n_train": len(pool), "features": df}


# ---------------------------------------------------------------------------
# Market-level daily indicators
# ---------------------------------------------------------------------------

def compute_market_daily(
    panel: pd.DataFrame,
    features: pd.DataFrame,
    band: Tuple[int, int] = SEARCH_BAND,
) -> pd.DataFrame:
    """
    Daily market indicators inside a price band, from the reconstructed panel:
      inventory, median price, median eur/m2, new listings, confirmed
      delistings, cut breadth, trailing-28d absorption and months of supply.
    """
    meta = features.set_index("property_id")[["sqm", "price_first_seen", "effective_first_seen", "status", "last_seen"]]
    p = panel.join(meta, on="property_id")
    p = p[p["price"].between(band[0], band[1])]
    p["ppsqm"] = p["price"] / p["sqm"]
    p["discounted"] = (p["price"] < p["price_first_seen"]).astype(int)

    daily = p.groupby("day").agg(
        inventory=("property_id", "nunique"),
        median_price=("price", "median"),
        mean_price=("price", "mean"),
        median_ppsqm=("ppsqm", "median"),
        cut_breadth=("discounted", "mean"),
    )

    firsts = features[features["price_first_seen"].between(band[0], band[1])].copy()
    new_by_day = firsts.groupby(firsts["effective_first_seen"].dt.floor("D")).size()
    delisted = features[(features["status"] == "inactive") & features["price"].between(band[0], band[1])]
    del_by_day = delisted.groupby(delisted["last_seen"].dt.floor("D")).size()

    daily["new_listings"] = new_by_day.reindex(daily.index).fillna(0)
    daily["delistings"] = del_by_day.reindex(daily.index).fillna(0)
    daily["delistings_28d"] = daily["delistings"].rolling(28, min_periods=7).sum()
    monthly_absorption = daily["delistings_28d"] / 28 * 30
    daily["months_of_supply"] = (daily["inventory"] / monthly_absorption.replace(0, np.nan)).round(2)
    daily["median_ppsqm_7d"] = daily["median_ppsqm"].rolling(7, min_periods=1).median()
    daily["median_price_7d"] = daily["median_price"].rolling(7, min_periods=1).median()
    daily["mean_price_7d"] = daily["mean_price"].rolling(7, min_periods=1).mean()
    return daily.reset_index()


def _slope_per_30d(series: pd.Series, window: int = 28) -> float:
    s = series.dropna().tail(window)
    if len(s) < max(7, window // 3):
        return np.nan
    x = np.arange(len(s), dtype=float)
    coef = np.polyfit(x, s.to_numpy(dtype=float), 1)[0]
    return float(coef * 30)


def market_timing_verdict(
    market_daily: pd.DataFrame,
    baselines: Optional[Dict] = None,
    population: str = "sant_cugat",
) -> Dict:
    """
    Traffic-light snapshot: is the market softening (buy signal) or tightening?
    Combines your scraped panel with the notary closing-price cycle position.
    """
    md = market_daily.set_index("day")
    signals: Dict[str, Dict] = {}

    inv_slope = _slope_per_30d(md["inventory"])
    signals["inventory_trend"] = {
        "valor_30d": None if np.isnan(inv_slope) else round(inv_slope, 1),
        "lectura": "subiendo = se relaja el mercado (bueno para comprar)" if inv_slope > 0.5
        else ("bajando = se tensiona el mercado" if inv_slope < -0.5 else "estable"),
        "buyer_friendly": bool(inv_slope > 0.5),
    }

    pp_slope = _slope_per_30d(md["median_ppsqm_7d"])
    last_pp = md["median_ppsqm_7d"].dropna().iloc[-1] if md["median_ppsqm_7d"].notna().any() else np.nan
    signals["asking_ppsqm_trend"] = {
        "eur_m2_actual": None if np.isnan(last_pp) else round(last_pp),
        "variacion_30d": None if np.isnan(pp_slope) else round(pp_slope, 1),
        "buyer_friendly": bool(pp_slope < 0),
    }

    mos = md["months_of_supply"].dropna()
    mos_now = mos.iloc[-1] if len(mos) else np.nan
    mos_slope = _slope_per_30d(md["months_of_supply"])
    history_days = int((md.index.max() - md.index.min()).days) if len(md) else 0
    # With < ~5 months of history this metric is unreliable: absorption is
    # measured from confirmed delistings (delist != sold, and early-history
    # denominators are tiny). Mark it low-confidence and keep it OUT of the
    # buyer score instead of painting a red light.
    mos_reliable = history_days >= 150
    signals["months_of_supply"] = {
        "meses_actual": None if np.isnan(mos_now) else round(mos_now, 1),
        "variacion_30d": None if np.isnan(mos_slope) else round(mos_slope, 2),
        "lectura": "> 6 meses = mercado comprador, < 4 = mercado vendedor (regla general)",
        "confianza": "ok" if mos_reliable else f"baja ({history_days}d de historia; hacen falta ~150d)",
        "buyer_friendly": (
            bool((not np.isnan(mos_now) and mos_now > 6) or (not np.isnan(mos_slope) and mos_slope > 0.3))
            if mos_reliable else None
        ),
    }

    cb = md["cut_breadth"].dropna()
    cb_now = cb.iloc[-1] if len(cb) else np.nan
    cb_slope = _slope_per_30d(md["cut_breadth"])
    signals["cut_breadth"] = {
        "pct_actual": None if np.isnan(cb_now) else round(float(cb_now), 3),
        "variacion_30d": None if np.isnan(cb_slope) else round(float(cb_slope), 3),
        "buyer_friendly": bool(not np.isnan(cb_slope) and cb_slope > 0.01),
    }

    if baselines and POPULATIONS[population]["notariado_keys"]:
        muni = baselines[POPULATIONS[population]["notariado_keys"][0]]
        yoy = muni["yoy_price_change_pct"]
        last_years = sorted(yoy)[-3:]
        signals["closing_price_cycle"] = {
            "variacion_anual_pct_3y": {y: yoy[y] for y in last_years},
            "precio_medio_cierre_12m": muni["last_12m"]["mean_eur_m2"],
            "lectura": (
                "Los precios de cierre notarial están en máximos y acelerando; "
                "esperar ha costado ~7-11%/año recientemente. Una caída como la de 2023 "
                "(-1,7%) es posible pero históricamente ha sido leve y pasajera."
            ),
            "buyer_friendly": False,
        }

    friendly = [s["buyer_friendly"] for s in signals.values() if s["buyer_friendly"] is not None]
    score = sum(friendly) / len(friendly) if friendly else np.nan
    verdict = (
        "VENTANA FAVORABLE AL COMPRADOR: varios indicadores se están relajando — negocia con firmeza."
        if score >= 0.6 else
        "MERCADO MIXTO: tensionado en general; la palanca de negociación existe solo en el stock estancado o sobrevalorado."
        if score >= 0.3 else
        "MERCADO DE VENDEDOR: actúa rápido en lo bien tasado; la palanca de negociación solo está en la cola de anuncios estancados."
    )
    return {"signals": signals, "buyer_score": None if np.isnan(score) else round(score, 2), "verdict": verdict}


# ---------------------------------------------------------------------------
# Notary (closing price) baselines
# ---------------------------------------------------------------------------

def load_notariado(project_dir: Path = PROJECT_DIR) -> Dict:
    path = project_dir / "notariado_baselines.json"
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def gap_analysis(
    features: pd.DataFrame,
    baselines: Dict,
    population: str = "sant_cugat",
    band: Tuple[int, int] = SEARCH_BAND,
) -> Optional[pd.DataFrame]:
    """
    Asking vs closing gap: median asking eur/m2 of your scraped active stock in
    the band vs notary closing eur/m2 (municipal + zips). The gap is an upper
    bound on total negotiation margin (part of it is composition: what sells
    is not what is listed).
    """
    keys = POPULATIONS[population]["notariado_keys"]
    if not keys:
        return None
    active = features[(features["status"] == "active") & features["price"].between(*band)]
    asking_ppsqm = active["price_per_sqm"].median()
    asking_mean_amount = active["price"].mean()

    rows = []
    for key in keys:
        base = baselines[key]
        closing = base["last_12m"]["mean_eur_m2"]
        med = base.get("annual_median_eur_m2", {})
        latest_median = med[max(med)] if med else None
        rows.append({
            "zone": key,
            "closing_mean_eur_m2_12m": closing,
            "closing_median_eur_m2_latest_year": latest_median,
            "asking_median_eur_m2_band": round(asking_ppsqm) if not np.isnan(asking_ppsqm) else None,
            "asking_premium_vs_closing_pct": round((asking_ppsqm / closing - 1) * 100, 1) if closing else None,
            "closing_mean_amount_eur_12m": base["last_12m"]["mean_amount_eur"],
            "asking_mean_amount_band": round(asking_mean_amount) if not np.isnan(asking_mean_amount) else None,
            "transactions_12m": base["last_12m"]["transactions"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Affordability (your budget)
# ---------------------------------------------------------------------------

@dataclass
class FinancialProfile:
    monthly_net_income: float = 6_500.0
    savings: float = 160_000.0
    extra_cash_if_needed: float = 30_000.0   # e.g. stretch + family help, only if needed
    monthly_savings: float = 3_300.0         # to compute "feasible in N months"
    annual_rate_pct: float = 2.0
    term_years: int = 30
    comfortable_payment: float = 1_500.0
    max_payment: float = 1_700.0
    stretch_payment: float = 1_800.0     # only for a truly exceptional home
    itp_rate: float = 0.10               # Catalonia second-hand transfer tax
    fees_rate: float = 0.015             # notary + registry + gestoria + valuation
    max_ltv: float = 0.80


def load_profile(project_dir: Path = PROJECT_DIR) -> FinancialProfile:
    """Load the budget profile from budget_config.json (falls back to defaults)."""
    path = project_dir / "budget_config.json"
    if not path.exists():
        return FinancialProfile()
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    fields = {f for f in FinancialProfile.__dataclass_fields__}
    return FinancialProfile(**{k: v for k, v in raw.items() if k in fields})


def monthly_payment(principal: float, annual_rate_pct: float, years: int) -> float:
    r = annual_rate_pct / 100 / 12
    n = years * 12
    if r == 0:
        return principal / n
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def max_loan_for_payment(payment: float, annual_rate_pct: float, years: int) -> float:
    r = annual_rate_pct / 100 / 12
    n = years * 12
    if r == 0:
        return payment * n
    return payment * ((1 + r) ** n - 1) / (r * (1 + r) ** n)


def required_rate_pct(principal: float, payment: float, years: int) -> float:
    """The annual rate at which `principal` costs exactly `payment`/month (bisection)."""
    if principal <= 0:
        return 0.0
    if monthly_payment(principal, 0.0, years) > payment:
        return 0.0  # not even at 0% — the loan itself is too big
    lo, hi = 0.0, 12.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if monthly_payment(principal, mid, years) > payment:
            hi = mid
        else:
            lo = mid
    return round(lo, 2)


def affordability(price: float, profile: FinancialProfile = FinancialProfile(), ltv: Optional[float] = None) -> Dict:
    """
    Full cash/payment picture for one purchase price, with a *gradual* verdict.

    Two independent constraints:
      - PAYMENT: the mortgage at the given LTV must fit your monthly caps.
      - CASH: down payment (1 - ltv) + purchase costs must fit your savings.
    Cash shortfalls are graded: covered by extra cash if needed -> "con_extras";
    reachable by saving at your current rate -> "en_N_meses"; otherwise the
    payment itself is the problem -> "fuera_de_alcance".

    `ltv` overrides the profile's max LTV (e.g. 0.90 for a 90% mortgage —
    strong income profiles can get it, usually at a slightly higher rate).
    """
    ltv = ltv if ltv is not None else profile.max_ltv
    costs = price * (profile.itp_rate + profile.fees_rate)
    loan_at_max_ltv = price * ltv
    payment_at_max_ltv = monthly_payment(loan_at_max_ltv, profile.annual_rate_pct, profile.term_years)

    # The loan you may actually take is capped by BOTH the bank's LTV and your
    # payment ceiling. When the LTV loan's cuota exceeds your stretch payment,
    # the fix is a SMALLER loan (i.e. lower effective LTV) — which just needs
    # more cash. So every price is reachable by saving; the question is months.
    loan_cap_by_payment = max_loan_for_payment(profile.stretch_payment, profile.annual_rate_pct, profile.term_years)
    loan_allowed = min(loan_at_max_ltv, loan_cap_by_payment)
    required_cash = price + costs - loan_allowed
    ltv_at_feasible = loan_allowed / price if price else np.nan

    # loan you actually need given your savings (never above loan_allowed)
    cash_constrained_loan = max(0.0, price + costs - profile.savings)
    ltv_needed = cash_constrained_loan / price if price else np.nan
    payment_cash_constrained = monthly_payment(cash_constrained_loan, profile.annual_rate_pct, profile.term_years)

    cash_shortfall = max(0.0, required_cash - profile.savings)
    shortfall_after_extras = max(0.0, cash_shortfall - profile.extra_cash_if_needed)
    months_to_feasible = (
        int(np.ceil(shortfall_after_extras / profile.monthly_savings))
        if shortfall_after_extras > 0 and profile.monthly_savings > 0
        else 0
    )
    # months of saving to cover the shortfall WITHOUT touching the extra cash
    months_to_feasible_no_extras = (
        int(np.ceil(cash_shortfall / profile.monthly_savings))
        if cash_shortfall > 0 and profile.monthly_savings > 0
        else 0
    )
    # cuota today if you can buy now (all savings in), else the cuota you'd pay
    # once feasible (loan_allowed — at most your stretch payment)
    effective_payment = (
        payment_cash_constrained if cash_shortfall == 0
        else monthly_payment(loan_allowed, profile.annual_rate_pct, profile.term_years)
    )
    effort_rate = effective_payment / profile.monthly_net_income

    if cash_shortfall == 0:
        if effective_payment <= profile.comfortable_payment:
            verdict = "comfortable"
        elif effective_payment <= profile.max_payment:
            verdict = "ok"
        else:
            verdict = "stretch"
    elif cash_shortfall <= profile.extra_cash_if_needed:
        verdict = "con_extras"                # doable now if you tap the extra cash
    elif months_to_feasible <= 24:
        verdict = f"en_{months_to_feasible}_meses"
    else:
        verdict = "fuera_de_alcance"          # >2 years of saving away — negotiate price/rate instead

    return {
        "price": price,
        "purchase_costs": round(costs),
        # cash to buy at the standard LTV (may be less than required_cash if the
        # payment cap forces a smaller loan)
        "cash_needed_at_80ltv": round(price - loan_at_max_ltv + costs),
        "required_cash_for_feasible": round(required_cash),
        "cash_shortfall": round(cash_shortfall),
        "months_to_feasible": months_to_feasible,
        "months_to_feasible_no_extras": months_to_feasible_no_extras,
        "ltv_at_feasible_pct": round(ltv_at_feasible * 100, 1),
        "loan_needed_with_savings": round(cash_constrained_loan),
        "ltv_needed_with_savings_pct": round(ltv_needed * 100, 1),
        "monthly_payment_needed": round(effective_payment),
        "monthly_payment_at_80ltv": round(payment_at_max_ltv),
        "effort_rate_pct": round(effort_rate * 100, 1),
        "verdict": verdict,
    }


def max_affordable_price(
    profile: FinancialProfile = FinancialProfile(),
    payment: Optional[float] = None,
    include_extras: bool = False,
) -> Dict:
    """Invert the problem: the max price your payment cap + savings support."""
    pay = payment or profile.max_payment
    savings = profile.savings + (profile.extra_cash_if_needed if include_extras else 0)
    loan_cap = max_loan_for_payment(pay, profile.annual_rate_pct, profile.term_years)
    # price bound by payment given savings: price + costs - savings <= loan_cap
    price_by_payment = (loan_cap + savings) / (1 + profile.itp_rate + profile.fees_rate)
    # price bound by LTV: price*(1-ltv) + costs <= savings
    price_by_ltv = savings / (1 - profile.max_ltv + profile.itp_rate + profile.fees_rate)
    # payment cap also limits the loan at max LTV: price*ltv <= loan_cap
    price_by_loan_at_ltv = loan_cap / profile.max_ltv
    binding = min(price_by_payment, price_by_ltv, price_by_loan_at_ltv)
    return {
        "payment_used": pay,
        "includes_extras": include_extras,
        "savings_used": round(savings),
        "max_loan": round(loan_cap),
        "max_price_by_payment_and_savings": round(min(price_by_payment, 10_000_000)),
        "max_price_by_ltv_and_savings": round(price_by_ltv),
        "binding_max_price": round(binding),
    }


def scenario_paths(price: float, profile: FinancialProfile = FinancialProfile()) -> Dict:
    """
    For one price, evaluate every lever you could pull and name the easiest
    path that unlocks it:
      1. nothing (fits with savings at standard 80% LTV)
      2. use the extra cash
      3. a 90% mortgage (keeps cash in pocket — but the bigger loan raises the
         payment, so it only works while the cuota still fits; banks also tend
         to price >80% LTV slightly worse)
      4. keep saving N months
      5. a better mortgage rate (computed: the rate that makes the cuota fit)
      6. negotiate the price down to your ceiling
    """
    s80 = affordability(price, profile, ltv=0.80)
    s90 = affordability(price, profile, ltv=0.90)
    cash = profile.savings
    cash_ext = profile.savings + profile.extra_cash_if_needed

    paths: List[str] = []
    if s80["cash_shortfall"] == 0 and s80["monthly_payment_needed"] <= profile.max_payment:
        camino = "✅ entra ya (80% LTV, solo ahorros)"
    elif s80["cash_shortfall"] == 0 and s80["monthly_payment_needed"] <= profile.stretch_payment:
        camino = "✅ entra ya al 80%, pero con cuota exigente"
    else:
        cuota80 = s80["monthly_payment_at_80ltv"]
        cuota90 = s90["monthly_payment_at_80ltv"]  # key = "at the LTV used" (here 90%)
        payment_bound = cuota80 > profile.stretch_payment
        if s80["cash_shortfall"] <= profile.extra_cash_if_needed and not payment_bound:
            paths.append(f"usar extras (faltan {s80['cash_shortfall']/1000:.0f}k)")
        if cuota90 <= profile.stretch_payment:
            if s90["cash_needed_at_80ltv"] <= cash:
                paths.append(f"hipoteca 90% (cash ok, cuota {cuota90:,.0f} €)".replace(",", "."))
            elif s90["cash_needed_at_80ltv"] <= cash_ext:
                paths.append(f"hipoteca 90% + extras (cuota {cuota90:,.0f} €)".replace(",", "."))
        if s80["months_to_feasible_no_extras"]:
            if payment_bound:
                paths.append(
                    f"ahorrar {s80['months_to_feasible']} meses con extras "
                    f"(baja el LTV a {s80['ltv_at_feasible_pct']:.0f}% para mantener cuota ≤ {profile.stretch_payment:,.0f} €)".replace(",", ".")
                )
            else:
                paths.append(f"ahorrar {s80['months_to_feasible_no_extras']} meses (sin extras)")
        if payment_bound:
            rate_needed = required_rate_pct(price * 0.80, profile.stretch_payment, profile.term_years)
            if 0 < rate_needed < profile.annual_rate_pct:
                paths.append(f"tipo ≤ {rate_needed:.2f}% (hoy asumes {profile.annual_rate_pct}%)")
        if not paths or (s80["months_to_feasible"] or 0) > 24:
            ceiling = max_affordable_price(profile, payment=profile.stretch_payment, include_extras=True)
            paths.append(f"negociar el precio a ≤ {ceiling['binding_max_price']/1000:.0f}k")
        camino = " · o ".join(paths[:3])

    return {
        "price": price,
        "cash_80": s80["cash_needed_at_80ltv"],
        "cuota_80": s80["monthly_payment_at_80ltv"],
        "cash_90": s90["cash_needed_at_80ltv"],
        "cuota_90": s90["monthly_payment_at_80ltv"],
        "months_no_extras": s80["months_to_feasible_no_extras"],
        "camino": camino,
        "verdict": s80["verdict"],
    }


def scenario_table(prices: List[float], profile: FinancialProfile = FinancialProfile()) -> pd.DataFrame:
    return pd.DataFrame([scenario_paths(p, profile) for p in prices])


# ---------------------------------------------------------------------------
# Offer calculator ("how low can we go")
# ---------------------------------------------------------------------------

def estimate_offer(
    row: pd.Series,
    km: pd.DataFrame,
    market_verdict: Optional[Dict] = None,
    gap_df: Optional[pd.DataFrame] = None,
    profile: FinancialProfile = FinancialProfile(),
) -> Dict:
    """
    Estimate negotiation margin for one listing and translate it into an
    opening offer, a realistic target and a walk-away price.

    The margin heuristic is transparent and additive; every component is
    reported in `rationale`. Calibrated on your own data (delisted stock cut
    a median ~3% before leaving; stale tail carries much more) plus the
    notary asking-vs-closing gap.
    """
    price = row.get("price")
    if price is None or np.isnan(price):
        return {}
    # Rationale is a full checklist in Spanish: every lever we examined, with
    # its real value — "✓" if it adds margin, "✗" if checked but not present.
    # This is what you read out loud in the negotiation.
    margin = 3.0
    rationale = ["✓ base 3% — descuento mediano que acaban aceptando los anuncios que se retiran del mercado"]

    days = row.get("days_online_effective")
    stale_pct = staleness_percentile(km, days)
    if not np.isnan(stale_pct):
        if stale_pct >= 0.9:
            margin += 6; rationale.append(f"✓ lleva {days:.0f} días publicado — ha durado más que el {stale_pct:.0%} de anuncios comparables (+6%)")
        elif stale_pct >= 0.75:
            margin += 4; rationale.append(f"✓ lleva {days:.0f} días publicado — más que el {stale_pct:.0%} de comparables (+4%)")
        elif stale_pct >= 0.5:
            margin += 2; rationale.append(f"✓ lleva {days:.0f} días publicado — más que el {stale_pct:.0%} de comparables (+2%)")
        else:
            rationale.append(f"✗ días online: solo {days:.0f} días (percentil {stale_pct:.0%}) — anuncio aún fresco, sin palanca (+0)")

    n_cuts = int(row.get("n_cuts", 0) or 0)
    if n_cuts:
        add = min(1.5 * n_cuts, 4)
        margin += add
        cum = row.get("cum_discount_pct")
        cum_txt = f", un {cum:.1f}% acumulado" if cum is not None and not np.isnan(cum) and cum > 0 else ""
        rationale.append(f"✓ ya ha bajado el precio {n_cuts} vez/veces{cum_txt} — vendedor que baja y no vende tiene más recorrido (+{add:.1f}%)")
    else:
        rationale.append("✗ bajadas de precio: ninguna observada todavía (+0)")
    dslc = row.get("days_since_last_cut")
    if dslc is not None and not np.isnan(dslc) and dslc <= 21:
        margin += 1; rationale.append(f"✓ última bajada hace solo {dslc:.0f} días — vendedor activamente nervioso (+1%)")

    if row.get("multi_listed"):
        n_src = int(row.get("n_sources", 0) or 0)
        margin += 2; rationale.append(f"✓ publicado en {n_src} fuentes/agencias a la vez — mandato no exclusivo, agencias compitiendo (+2%)")
    else:
        rationale.append("✗ multi-agencia: solo lo vemos en una fuente (+0)")

    if row.get("relist_count"):
        margin += 2; rationale.append("✓ es un relistado (retirado y republicado para resetear el contador) — lleva más tiempo del que aparenta (+2%)")
    else:
        rationale.append("✗ relistado: no detectado (+0)")
    n_react = int(row.get("n_reactivations", 0) or 0)
    if n_react >= 2:
        margin += 1; rationale.append(f"✓ {n_react} desactivaciones/reactivaciones — historial inestable (+1%)")
    elif n_react == 1:
        rationale.append("✗ reactivaciones: solo 1 (poco concluyente, puede ser ruido del scraping) (+0)")
    else:
        rationale.append("✗ reactivaciones: ninguna (+0)")

    if row.get("kw_any_motivated"):
        kws = [k.replace("kw_", "") for k in ("kw_inheritance", "kw_urgency", "kw_negotiable", "kw_opportunity",
                                              "kw_renovation", "kw_investor", "kw_divorce_or_sale_urgency")
               if row.get(k)]
        margin += 2; rationale.append(f"✓ el texto del anuncio sugiere vendedor motivado ({', '.join(kws)}) (+2%)")
    else:
        rationale.append("✗ texto motivado: sin señales tipo herencia/urge/negociable (+0)")

    resid = row.get("hedonic_residual_pct")
    if resid is not None and not np.isnan(resid):
        if resid > 5:
            add = min(0.5 * resid, 6)
            margin += add
            rationale.append(f"✓ pide un {resid:.0f}% por encima de su valor de mercado (modelo hedónico) (+{add:.1f}%)")
        elif resid < -5:
            rationale.append(f"✗ sobreprecio: ya pide un {abs(resid):.0f}% por DEBAJO de su valor de mercado — buen precio, poco recorrido extra (+0)")
        else:
            rationale.append("✗ sobreprecio: precio en línea con su valor de mercado (+0)")

    if gap_df is not None and len(gap_df):
        premium = gap_df["asking_premium_vs_closing_pct"].iloc[0]
        ppsqm = row.get("price_per_sqm")
        closing = gap_df["closing_mean_eur_m2_12m"].iloc[0]
        if ppsqm and closing and not np.isnan(ppsqm):
            own_premium = (ppsqm / closing - 1) * 100
            if own_premium > (premium or 0) + 15:
                margin += 2
                rationale.append(f"✓ su €/m² está un {own_premium:.0f}% por encima del cierre medio de zona (notariado) (+2%)")

    if market_verdict and market_verdict.get("buyer_score") is not None and market_verdict["buyer_score"] >= 0.6:
        margin += 1; rationale.append("✓ el mercado en tu banda se está enfriando (buyer score alto) (+1%)")

    margin = min(margin, 18.0)

    # Offer ladder (all derived from the same margin estimate):
    #   aggressive = full margin      -> anchor low; expect a counter
    #   target     = 60% of margin    -> where the data says the deal lands
    #   prudent    = 35% of margin    -> high acceptance probability
    #   walkaway   = 25% of margin    -> never pay above this (capped at HARD_MAX)
    opening = round_to(price * (1 - margin / 100))
    target = round_to(price * (1 - 0.6 * margin / 100))
    prudent = round_to(price * (1 - 0.35 * margin / 100))
    walkaway = min(round_to(price * (1 - 0.25 * margin / 100)), HARD_MAX_PRICE)
    afford = affordability(price, profile)
    # affordability at the TARGET offer: an asking price out of reach can still
    # be a fine deal if the negotiation lands where the data says it should
    afford_target = affordability(target, profile)
    # and at the WALK-AWAY price (④): the best case you'd ever pay
    afford_walkaway = affordability(walkaway, profile)

    # What comparable stock actually CLOSES at (hedonic asking-market value,
    # deflated by the zone's asking-vs-closing premium). Reference anchor for
    # the negotiation, not an offer by itself.
    est_closing_value = None
    fair = row.get("hedonic_fair_price")
    if gap_df is not None and len(gap_df) and fair is not None and not np.isnan(fair):
        premium = gap_df["asking_premium_vs_closing_pct"].iloc[0]
        if premium is not None:
            est_closing_value = round_to(fair / (1 + premium / 100))

    if margin >= 12:
        bucket = "ruthless (open 12-18% below asking)"
    elif margin >= 8:
        bucket = "firm (open ~8-12% below)"
    elif margin >= 5:
        bucket = "standard (open ~5-8% below)"
    else:
        bucket = "act fast (well-priced; open <5% below)"

    return {
        "est_margin_pct": round(margin, 1),
        "offer_opening": opening,
        "offer_target": target,
        "offer_prudent": prudent,
        "offer_walkaway": walkaway,
        "est_closing_value": est_closing_value,
        "bucket": bucket,
        "affordability_verdict": afford["verdict"],
        "months_to_feasible": afford["months_to_feasible"],
        "affordability_target_verdict": afford_target["verdict"],
        "months_to_feasible_target": afford_target["months_to_feasible"],
        "cash_needed_at_target": afford_target["required_cash_for_feasible"],
        "affordability_walkaway_verdict": afford_walkaway["verdict"],
        "months_to_feasible_walkaway": afford_walkaway["months_to_feasible"],
        "cash_needed_at_walkaway": afford_walkaway["required_cash_for_feasible"],
        "monthly_payment_needed": afford["monthly_payment_needed"],
        "monthly_payment_at_target": afford_target["monthly_payment_needed"],
        "rationale": "; ".join(rationale),
    }


def add_offer_columns(
    features: pd.DataFrame,
    km: pd.DataFrame,
    market_verdict: Optional[Dict] = None,
    gap_df: Optional[pd.DataFrame] = None,
    profile: FinancialProfile = FinancialProfile(),
) -> pd.DataFrame:
    """Vector wrapper: append estimate_offer() outputs for every row."""
    out = features.copy()
    offers = out.apply(
        lambda r: pd.Series(estimate_offer(r, km, market_verdict, gap_df, profile)),
        axis=1,
    )
    overlap = [c for c in offers.columns if c in out.columns]
    return pd.concat([out.drop(columns=overlap), offers], axis=1)


# ---------------------------------------------------------------------------
# Ranking (used by rank_offer_candidates.ipynb)
# ---------------------------------------------------------------------------

def _pct_rank(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    ranked = values.rank(pct=True)
    return ranked.fillna(0.5)


# --- Shared score components ---------------------------------------------
# Extracted so BOTH tables carry BOTH dimensions: the Excelencia table can
# show a listing's negotiation leverage, and the Top-40 leverage table can
# show a listing's quality. Percentile ranks are computed on whatever df is
# passed, so each call is self-consistent within its own row set.

def _quality_component(df: pd.DataFrame) -> pd.Series:
    """Quality-of-the-home score (size, rooms, baths, amenities, fair pricing)."""
    residual_clamped = df["hedonic_residual_pct"].clip(-35, 40)
    bath_fill = df["bathrooms"].fillna(df["bathrooms"].median())
    return (
        0.28 * _pct_rank(df["sqm"].clip(upper=350))
        + 0.14 * _pct_rank(df["rooms"])
        + 0.10 * _pct_rank(bath_fill)
        + 0.14 * df["has_pool"].fillna(0)
        + 0.08 * df["has_ac"].fillna(0)
        + 0.08 * df["terrace"].fillna(0)
        + 0.06 * df["elevator"].fillna(0)
        + 0.06 * df["parking"].fillna(0)
        + 0.06 * (1 - _pct_rank(residual_clamped))
    ).clip(0, 1)


def _value_component(df: pd.DataFrame) -> pd.Series:
    """Cheap-for-what-it-is score (hedonic residual + €/m² + size)."""
    residual_clamped = df["hedonic_residual_pct"].clip(-35, 50)
    return (
        0.5 * (1 - _pct_rank(residual_clamped))
        + 0.3 * (1 - _pct_rank(df["price_per_sqm"]))
        + 0.2 * _pct_rank(df["sqm"].clip(upper=350))
    ).clip(0, 1)


def _leverage_component(df: pd.DataFrame) -> pd.Series:
    """Negotiation-leverage score (staleness, cuts, multiplicity, relist, keywords)."""
    return (
        0.35 * _pct_rank(df["days_online_effective"])
        + 0.25 * _pct_rank(df["n_cuts"])
        + 0.15 * df["multi_listed"]
        + 0.10 * df["relist_count"].clip(0, 1)
        + 0.15 * df["kw_any_motivated"]
    ).clip(0, 1)


def score_candidates(features: pd.DataFrame, profile: FinancialProfile = FinancialProfile()) -> pd.DataFrame:
    """
    Final ranking score over the SEARCH band:
      value:      hedonic residual (cheap for what it is) + €/m²
      leverage:   staleness, cuts, multiplicity, relist, keywords
      fit:        affordability of the *target* offer price, size, similarity
      reno bonus: 400-500k with renovation keywords or low €/m² -> value-add play
    """
    df = features.copy()
    df = df[df["status"] == "active"]
    df = df[df["price"].between(*SEARCH_BAND)]
    # dedupe: one row per physical property (keep canonical)
    df = df[df["is_cluster_canonical"] == 1]

    value = _value_component(df)
    leverage = _leverage_component(df)
    target_payment = df["price"].map(lambda p: affordability(p, profile)["monthly_payment_needed"])
    payment_fit = 1 - ((target_payment - profile.comfortable_payment).clip(lower=0) / 600).clip(0, 1)
    in_target = df["price"].between(*TARGET_BAND).astype(float)
    reno_bonus = (
        df["price"].between(*RENO_BAND)
        & ((df["kw_renovation"] == 1) | (df["hedonic_residual_pct"] < -10))
    ).astype(float)

    df["value_score"] = value.round(3)
    df["leverage_score"] = leverage.round(3)
    df["quality_score"] = _quality_component(df).round(3)  # so the leverage table can also show quality
    df["payment_fit"] = payment_fit.round(3)
    df["monthly_payment_needed"] = target_payment
    df["reno_opportunity"] = reno_bonus
    df["final_score"] = (
        0.35 * value + 0.30 * leverage + 0.20 * payment_fit + 0.10 * in_target + 0.10 * reno_bonus
    ).round(4)
    return df.sort_values("final_score", ascending=False)


def score_quality(features: pd.DataFrame) -> pd.DataFrame:
    """
    "Excelencia" lens: what's the best AVAILABLE property right now, regardless
    of how negotiable it is. Deliberately the mirror image of score_candidates():
    it does NOT reward staleness, price cuts, multi-agency listing or motivated-
    seller wording — a genuinely great, fairly priced home shows none of those,
    it just sells. Weights size, rooms, bathrooms, amenities and being fairly
    priced RELATIVE TO OTHER ASKING PRICES (hedonic residual).

    Stays in the full SEARCH_BAND (400-700k), not the tighter 500-600k target:
    asking prices in this market run ~16-28% above notary closing across the
    board, so that gap is systemic market inflation, not a per-listing red
    flag — narrowing the band would filter out good homes for being priced
    like everything else around them.
    """
    df = features.copy()
    df = df[df["status"] == "active"]
    df = df[df["price"].between(*SEARCH_BAND)]
    df = df[df["is_cluster_canonical"] == 1]
    df = df[df["sqm"].between(35, 400)]  # drop parse-artifact outliers (e.g. plot size as sqm)

    df["quality_score"] = _quality_component(df).round(3)
    # also carry the negotiation dimension so the Excelencia table can show,
    # per excellent home, how much leverage / value it also has
    df["value_score"] = _value_component(df).round(3)
    df["leverage_score"] = _leverage_component(df).round(3)
    return df.sort_values("quality_score", ascending=False)


def score_stuck_excellence(
    quality_df: pd.DataFrame,
    km: pd.DataFrame,
    min_days: float = 30,
    overprice_threshold: float = 10.0,
    quality_quantile: float = 0.5,
) -> pd.DataFrame:
    """
    The intersection you actually want for a ruthless offer: a GOOD property
    (above-median quality_score) that is priced meaningfully above comparable
    ASKING prices (hedonic_residual_pct > overprice_threshold) and has been
    sitting for a while (>= min_days) — i.e. the market has already had a
    chance to buy it at this price and hasn't. Unlike score_quality(), being
    stale here is the point, not something to ignore.

    Pass `quality_df` AFTER add_offer_columns() so n_cuts/cum_discount_pct/
    offer_opening etc. are already attached — this function only adds the
    staleness percentile and filters, it doesn't recompute offers.

    "Tracking" this list needs no new machinery: re-running the report each
    day re-reads n_cuts / days_since_last_cut / cum_discount_pct straight from
    the persistent DB, so if a listing here starts cutting price, you'll see
    it change here day over day for as long as it stays active.
    """
    df = quality_df.copy()
    df["staleness_pctile"] = df["days_online_effective"].map(lambda d: staleness_percentile(km, d))
    quality_gate = pd.to_numeric(df["quality_score"], errors="coerce").quantile(quality_quantile)
    stuck = df[
        (pd.to_numeric(df["quality_score"], errors="coerce") >= quality_gate)
        & (df["days_online_effective"] >= min_days)
        & (df["hedonic_residual_pct"] > overprice_threshold)
    ].copy()
    return stuck.sort_values(["hedonic_residual_pct", "days_online_effective"], ascending=[False, False])


def build_enrich_shortlist(
    quality: pd.DataFrame,
    stuck: pd.DataFrame,
    ranked: pd.DataFrame,
    n_quality: int = 15,
    n_stuck: int = 15,
    n_leverage: int = 15,
    only_unenriched: bool = True,
) -> pd.DataFrame:
    """
    Union the property_ids worth spending your limited (and slightly risky)
    detail-page budget on: the best available homes, the ones worth a
    ruthless offer, and the top negotiation-leverage candidates — deduped,
    with the reason(s) each one made the cut. Feeds directly into
    enrich_details.py via its `--property-ids @<file>` option.

    Selection is: take the top-N of each list first, THEN (if `only_unenriched`)
    drop the ones whose detail page has already been visited (enriched_at set).
    So a listing that is, say, #5 by leverage but already enriched is correctly
    excluded from the work queue, and once the whole top-N is enriched the
    shortlist naturally shrinks to only genuinely new entrants — the "only
    enrich the new ones going forward" guardrail. Uses enriched_at, NOT
    description length, so an agency listing with a short-but-complete
    description doesn't loop forever.
    """
    picks = []
    for df, reason, n in [(quality, "top_calidad", n_quality),
                          (stuck, "estancado", n_stuck),
                          (ranked, "top_palanca", n_leverage)]:
        if df is None or df.empty:
            continue
        sub = df.head(n)
        if only_unenriched and "enriched_at" in sub.columns:
            sub = sub[sub["enriched_at"].isna()]
        for pid, title, price in sub[["property_id", "title", "price"]].itertuples(index=False):
            picks.append({"property_id": pid, "title": title, "price": price, "reason": reason})
    if not picks:
        return pd.DataFrame(columns=["property_id", "title", "price", "reasons"])
    combined = pd.DataFrame(picks)
    agg = combined.groupby("property_id").agg(
        title=("title", "first"),
        price=("price", "first"),
        reasons=("reason", lambda s: ", ".join(sorted(set(s)))),
    ).reset_index()
    return agg.sort_values("price")


def write_enrich_shortlist(shortlist: pd.DataFrame, population: str, project_dir: Path = PROJECT_DIR) -> Path:
    """Write property_ids (one per line, with a human-readable comment) for
    enrich_details.py's `--property-ids @<file>` mode."""
    path = project_dir / f"{population}_enrich_shortlist.txt"
    lines = [f"# Shortlist de enriquecimiento — {population} — regenerado en cada build_report.py"]
    for _, row in shortlist.iterrows():
        lines.append(f"# {row['title']} | {row['price']:.0f}€ | {row['reasons']}")
        lines.append(str(row["property_id"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
