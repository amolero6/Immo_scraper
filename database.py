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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = Path("immo_scraper.db")

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

def init_db(db_path: Optional[Path] = None) -> None:
    """
    Create the database and tables if they do not yet exist.

    Args:
        db_path: Override the default DB path (useful for testing).
    """
    global DB_PATH
    if db_path is not None:
        DB_PATH = db_path

    logger.info("Initialising database at '%s'", DB_PATH)
    with _get_conn() as conn:
        conn.executescript(_DDL)
        _ensure_columns(conn)
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
            "SELECT price, status FROM properties WHERE property_id = ?",
            (property_id,),
        ).fetchone()

        if existing is None:
            # ---- INSERT ----
            conn.execute(
                """
                INSERT INTO properties
                    (property_id, source, title, url, price, rooms, bathrooms,
                     sqm, has_pool, has_ac, orientation, property_type, operation,
                     city, district, neighborhood, postal_code, latitude, longitude,
                     energy_rating, year_built, floor, terrace, elevator, parking,
                     is_favourite, similarity_score, similarity_profile, first_seen,
                     last_seen, status)
                VALUES
                    (:property_id, :source, :title, :url, :price, :rooms, :bathrooms,
                     :sqm, :has_pool, :has_ac, :orientation, :property_type,
                     :operation, :city, :district, :neighborhood, :postal_code,
                     :latitude, :longitude, :energy_rating, :year_built, :floor,
                     :terrace, :elevator, :parking, :is_favourite,
                     :similarity_score, :similarity_profile, :first_seen,
                     :last_seen, 'active')
                """,
                {
                    "property_id": property_id,
                    "source": prop.get("source", "unknown"),
                    "title": prop.get("title"),
                    "url": prop.get("url"),
                    "price": prop.get("price"),
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
            if prop.get("price") is not None:
                _append_price_history(conn, property_id, prop["price"], now)
            logger.debug("Inserted new property '%s'.", property_id)
            return "inserted"

        # ---- UPDATE ----
        old_price = existing["price"]
        new_price = prop.get("price")

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


def mark_inactive(active_ids: list) -> int:
    """
    Set ``status = 'inactive'`` for every property whose ``property_id``
    is **not** in *active_ids*.

    Call this at the end of each scraping run, passing the full list of IDs
    seen in that run.

    Args:
        active_ids: List of property IDs observed in the current run.

    Returns:
        Number of properties marked inactive.
    """
    if not active_ids:
        logger.warning(
            "mark_inactive called with an empty list – no properties will be deactivated."
        )
        return 0

    placeholders = ",".join("?" * len(active_ids))
    now = _now()

    with _get_conn() as conn:
        cursor = conn.execute(
            f"""
            UPDATE properties
            SET status    = 'inactive',
                last_seen = ?
            WHERE status = 'active'
              AND property_id NOT IN ({placeholders})
            """,
            [now] + list(active_ids),
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
    conn.execute(
        "INSERT INTO price_history (property_id, price, date) VALUES (?, ?, ?)",
        (property_id, price, date),
    )


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add new columns to an existing database if they are missing."""
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(properties)").fetchall()
    }
    column_statements = {
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
