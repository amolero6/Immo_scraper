"""
database.py
-----------
SQLite database module for the Immo Scraper project.

Manages two tables:
  - properties     : one row per property (upsert on each run)
  - price_history  : append-only log of every price change
"""

import logging
import sqlite3
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = Path("immo_scraper.db")
MAX_REASONABLE_PRICE = 100_000_000.0

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS properties (
    property_id  TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    title        TEXT,
    url          TEXT,
    price        REAL,
    price_first_seen REAL,
    rooms        INTEGER,
    bathrooms    INTEGER,
    sqm          INTEGER,
    has_pool     INTEGER DEFAULT 0,   -- 0/1 (SQLite has no BOOLEAN)
    has_ac       INTEGER DEFAULT 0,
    orientation  TEXT,
    property_type TEXT,
    operation    TEXT,
    city         TEXT,
    district     TEXT,
    neighborhood TEXT,
    postal_code  TEXT,
    latitude     REAL,
    longitude    REAL,
    energy_rating TEXT,
    year_built   INTEGER,
    floor        TEXT,
    terrace      INTEGER DEFAULT 0,
    elevator     INTEGER DEFAULT 0,
    parking      INTEGER DEFAULT 0,
    is_favourite INTEGER DEFAULT 0,
    similarity_score INTEGER,
    similarity_profile TEXT,
    first_seen   TEXT NOT NULL,       -- ISO-8601 datetime
    last_seen    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT NOT NULL REFERENCES properties(property_id),
    price       REAL NOT NULL,
    date        TEXT NOT NULL         -- ISO-8601 datetime
);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextmanager
def _get_conn():
    """Yield a sqlite3 connection with row_factory and WAL mode enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(db_path: Optional[Path] = None, population: Optional[str] = None) -> None:
    """
    Create the database and tables if they do not yet exist.

    Args:
        db_path: Override the default DB path (useful for testing).
        population: Population name (e.g. "sant_cugat", "sant_quirze", "cerdanyola").
                   Sant Cugat uses "immo_scraper.db"; others use "immo_scraper_{population}.db".
                   Ignored if db_path is explicitly provided.
    """
    global DB_PATH
    if db_path is not None:
        DB_PATH = db_path
    elif population is not None:
        # Sant Cugat uses the default DB path; other populations get their own
        if population == "sant_cugat":
            DB_PATH = Path("immo_scraper.db")
        else:
            DB_PATH = Path(f"immo_scraper_{population}.db")
    else:
        # Default to Sant Cugat if neither is specified
        DB_PATH = Path("immo_scraper.db")

    logger.info("Initialising database at '%s'", DB_PATH)
    with _get_conn() as conn:
        conn.executescript(_DDL)
        _ensure_columns(conn)
        _ensure_price_first_seen(conn)
        _repair_invalid_price_first_seen(conn)
    logger.info("Database ready.")


def upsert_property(prop: Dict) -> str:
    """
    Insert a new property or update an existing one.

    If the property already exists:
      - ``last_seen`` is updated to *now*.
      - ``status`` is reset to ``'active'``.
      - All other mutable fields are refreshed.
      - If the price changed, the old price is recorded in ``price_history``.

    Args:
        prop: Dictionary with keys matching the ``properties`` columns.
              ``property_id`` and ``source`` are mandatory.

    Returns:
        ``'inserted'`` or ``'updated'`` to indicate what happened.
    """
    now = _now()
    property_id = prop["property_id"]

    with _get_conn() as conn:
        existing = conn.execute(
            "SELECT price, price_first_seen, status FROM properties WHERE property_id = ?",
            (property_id,),
        ).fetchone()

        new_price = _normalize_price(prop.get("price"))

        if existing is None:
            # ---- INSERT ----
            conn.execute(
                """
                INSERT INTO properties
                    (property_id, source, title, url, price, rooms, bathrooms,
                     price_first_seen, sqm, has_pool, has_ac, orientation,
                     property_type, operation, city, district, neighborhood,
                     postal_code, latitude, longitude, energy_rating, year_built,
                     floor, terrace, elevator, parking, is_favourite,
                     similarity_score, similarity_profile, first_seen, last_seen,
                     status)
                VALUES
                    (:property_id, :source, :title, :url, :price, :rooms, :bathrooms,
                     :price_first_seen, :sqm, :has_pool, :has_ac, :orientation,
                     :property_type, :operation, :city, :district, :neighborhood,
                     :postal_code, :latitude, :longitude, :energy_rating,
                     :year_built, :floor, :terrace, :elevator, :parking,
                     :is_favourite, :similarity_score, :similarity_profile,
                     :first_seen, :last_seen, 'active')
                """,
                {
                    "property_id": property_id,
                    "source": prop.get("source", "unknown"),
                    "title": prop.get("title"),
                    "url": prop.get("url"),
                    "price": new_price,
                    "price_first_seen": new_price,
                    "rooms": prop.get("rooms"),
                    "bathrooms": prop.get("bathrooms"),
                    "sqm": prop.get("sqm"),
                    "has_pool": int(bool(prop.get("has_pool", False))),
                    "has_ac": int(bool(prop.get("has_ac", False))),
                    "orientation": prop.get("orientation"),
                    "property_type": prop.get("property_type"),
                    "operation": prop.get("operation"),
                    "city": prop.get("city"),
                    "district": prop.get("district"),
                    "neighborhood": prop.get("neighborhood"),
                    "postal_code": prop.get("postal_code"),
                    "latitude": prop.get("latitude"),
                    "longitude": prop.get("longitude"),
                    "energy_rating": prop.get("energy_rating"),
                    "year_built": prop.get("year_built"),
                    "floor": prop.get("floor"),
                    "terrace": int(bool(prop.get("terrace", False))),
                    "elevator": int(bool(prop.get("elevator", False))),
                    "parking": int(bool(prop.get("parking", False))),
                    "is_favourite": int(bool(prop.get("is_favourite", False))),
                    "similarity_score": prop.get("similarity_score"),
                    "similarity_profile": prop.get("similarity_profile"),
                    "first_seen": now,
                    "last_seen": now,
                },
            )
            # Record the initial price in history
            if new_price is not None:
                _append_price_history(conn, property_id, new_price, now)
            logger.debug("Inserted new property '%s'.", property_id)
            return "inserted"

        # ---- UPDATE ----
        old_price = _normalize_price(existing["price"])
        existing_first_seen = _normalize_price(existing["price_first_seen"])
        healed_first_seen = _heal_price_first_seen(existing_first_seen, new_price)

        conn.execute(
            """
            UPDATE properties
            SET source      = :source,
                title       = :title,
                url         = :url,
                price       = :price,
                rooms       = :rooms,
                bathrooms   = :bathrooms,
                sqm         = :sqm,
                has_pool    = :has_pool,
                has_ac      = :has_ac,
                orientation = :orientation,
                property_type = :property_type,
                operation   = :operation,
                city        = :city,
                district    = :district,
                neighborhood = :neighborhood,
                postal_code = :postal_code,
                latitude    = :latitude,
                longitude   = :longitude,
                energy_rating = :energy_rating,
                year_built  = :year_built,
                floor       = :floor,
                terrace     = :terrace,
                elevator    = :elevator,
                parking     = :parking,
                is_favourite = :is_favourite,
                similarity_score = :similarity_score,
                similarity_profile = :similarity_profile,
                price_first_seen = :price_first_seen,
                last_seen   = :last_seen,
                status      = 'active'
            WHERE property_id = :property_id
            """,
            {
                "property_id": property_id,
                "source": prop.get("source", "unknown"),
                "title": prop.get("title"),
                "url": prop.get("url"),
                "price": new_price,
                "price_first_seen": healed_first_seen,
                "rooms": prop.get("rooms"),
                "bathrooms": prop.get("bathrooms"),
                "sqm": prop.get("sqm"),
                "has_pool": int(bool(prop.get("has_pool", False))),
                "has_ac": int(bool(prop.get("has_ac", False))),
                "orientation": prop.get("orientation"),
                "property_type": prop.get("property_type"),
                "operation": prop.get("operation"),
                "city": prop.get("city"),
                "district": prop.get("district"),
                "neighborhood": prop.get("neighborhood"),
                "postal_code": prop.get("postal_code"),
                "latitude": prop.get("latitude"),
                "longitude": prop.get("longitude"),
                "energy_rating": prop.get("energy_rating"),
                "year_built": prop.get("year_built"),
                "floor": prop.get("floor"),
                "terrace": int(bool(prop.get("terrace", False))),
                "elevator": int(bool(prop.get("elevator", False))),
                "parking": int(bool(prop.get("parking", False))),
                "is_favourite": int(bool(prop.get("is_favourite", False))),
                "similarity_score": prop.get("similarity_score"),
                "similarity_profile": prop.get("similarity_profile"),
                "last_seen": now,
            },
        )

        # Record price history only when the price actually changed
        if new_price is not None and new_price != old_price:
            _append_price_history(conn, property_id, new_price, now)
            logger.info(
                "Price change detected for '%s': %s → %s",
                property_id,
                old_price,
                new_price,
            )

        logger.debug("Updated property '%s'.", property_id)
        return "updated"


def mark_inactive(active_ids: list, skip_sources: Optional[list] = None) -> int:
    """
    Set ``status = 'inactive'`` for every property whose ``property_id``
    is **not** in *active_ids* and whose source is not excluded.

    Call this at the end of each scraping run, passing the full list of IDs
    seen in that run. If a scraper failed or returned 0 results, pass its
    source name in ``skip_sources`` so its historical rows are not marked
    inactive by accident.

    Args:
        active_ids: List of property IDs observed in the current run.

    Returns:
        Number of properties marked inactive.
    """
    if not active_ids and not skip_sources:
        logger.warning(
            "mark_inactive called with an empty list – no properties will be deactivated."
        )
        return 0

    now = _now()
    conditions = ["status = 'active'"]
    params = [now]

    if active_ids:
        placeholders = ",".join("?" * len(active_ids))
        conditions.append(f"property_id NOT IN ({placeholders})")
        params.extend(active_ids)

    if skip_sources:
        source_placeholders = ",".join("?" * len(skip_sources))
        conditions.append(f"source NOT IN ({source_placeholders})")
        params.extend(list(skip_sources))

    with _get_conn() as conn:
        cursor = conn.execute(
            f"""
            UPDATE properties
            SET status    = 'inactive',
                last_seen = ?
            WHERE {' AND '.join(conditions)}
            """,
            params,
        )
        count = cursor.rowcount

    if count:
        logger.info("Marked %d propert(y/ies) as inactive.", count)
    return count


def get_property(property_id: str) -> Optional[Dict]:
    """
    Fetch a single property by its ID.

    Returns:
        A dictionary of column values, or ``None`` if not found.
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM properties WHERE property_id = ?", (property_id,)
        ).fetchone()
    return dict(row) if row else None


def get_price_history(property_id: str) -> list:
    """
    Return all price-history entries for a property, oldest first.

    Returns:
        List of dicts with ``price`` and ``date`` keys.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT price, date FROM price_history WHERE property_id = ? ORDER BY date",
            (property_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append_price_history(conn: sqlite3.Connection, property_id: str, price: int, date: str) -> None:
    normalized = _normalize_price(price)
    if normalized is None:
        return
    conn.execute(
        "INSERT INTO price_history (property_id, price, date) VALUES (?, ?, ?)",
        (property_id, normalized, date),
    )


def _normalize_price(value) -> Optional[float]:
    """Return a sane finite price or None if the value is invalid/outlier."""
    if value is None:
        return None
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p):
        return None
    if p <= 0:
        return None
    if p > MAX_REASONABLE_PRICE:
        return None
    return p


def _heal_price_first_seen(existing_first_seen: Optional[float], new_price: Optional[float]) -> Optional[float]:
    """Keep first seen price when plausible; otherwise heal it using current price."""
    if existing_first_seen is None:
        return new_price
    if new_price is None:
        return existing_first_seen

    ratio = existing_first_seen / new_price if new_price else None
    if ratio is None:
        return existing_first_seen

    # If the original value is disproportionately far from the current value,
    # it is usually a parse artifact (concatenated numbers, inf, etc.).
    if ratio > 50 or ratio < 0.02:
        return new_price

    return existing_first_seen


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add new columns to an existing database if they are missing."""
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(properties)").fetchall()
    }
    column_statements = {
        "price_first_seen": "ALTER TABLE properties ADD COLUMN price_first_seen REAL",
        "property_type": "ALTER TABLE properties ADD COLUMN property_type TEXT",
        "operation": "ALTER TABLE properties ADD COLUMN operation TEXT",
        "city": "ALTER TABLE properties ADD COLUMN city TEXT",
        "district": "ALTER TABLE properties ADD COLUMN district TEXT",
        "neighborhood": "ALTER TABLE properties ADD COLUMN neighborhood TEXT",
        "postal_code": "ALTER TABLE properties ADD COLUMN postal_code TEXT",
        "latitude": "ALTER TABLE properties ADD COLUMN latitude REAL",
        "longitude": "ALTER TABLE properties ADD COLUMN longitude REAL",
        "energy_rating": "ALTER TABLE properties ADD COLUMN energy_rating TEXT",
        "year_built": "ALTER TABLE properties ADD COLUMN year_built INTEGER",
        "floor": "ALTER TABLE properties ADD COLUMN floor TEXT",
        "terrace": "ALTER TABLE properties ADD COLUMN terrace INTEGER DEFAULT 0",
        "elevator": "ALTER TABLE properties ADD COLUMN elevator INTEGER DEFAULT 0",
        "parking": "ALTER TABLE properties ADD COLUMN parking INTEGER DEFAULT 0",
        "is_favourite": "ALTER TABLE properties ADD COLUMN is_favourite INTEGER DEFAULT 0",
        "similarity_score": "ALTER TABLE properties ADD COLUMN similarity_score INTEGER",
        "similarity_profile": "ALTER TABLE properties ADD COLUMN similarity_profile TEXT",
    }

    for column_name, statement in column_statements.items():
        if column_name not in existing_columns:
            conn.execute(statement)


def _ensure_price_first_seen(conn: sqlite3.Connection) -> None:
    """Backfill price_first_seen for rows created before the column existed."""
    conn.execute(
        """
        UPDATE properties
        SET price_first_seen = (
            SELECT ph.price
            FROM price_history ph
            WHERE ph.property_id = properties.property_id
            ORDER BY ph.date ASC, ph.id ASC
            LIMIT 1
        )
        WHERE price_first_seen IS NULL
          AND EXISTS (
              SELECT 1
              FROM price_history ph
              WHERE ph.property_id = properties.property_id
          )
        """
    )
    conn.execute(
        """
        UPDATE properties
        SET price_first_seen = price
        WHERE price_first_seen IS NULL
          AND price IS NOT NULL
        """
    )


def _repair_invalid_price_first_seen(conn: sqlite3.Connection) -> None:
        """Repair clearly broken first-seen prices from historical parser artifacts."""
        conn.execute(
                """
                UPDATE properties
                SET price_first_seen = price
                WHERE price IS NOT NULL
                    AND (
                            price_first_seen IS NULL
                            OR price_first_seen <= 0
                            OR price_first_seen > ?
                            OR price_first_seen > price * 50
                            OR price_first_seen < price * 0.02
                    )
                """,
                (MAX_REASONABLE_PRICE,),
        )
