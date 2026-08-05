"""
database.py
-----------
SQLite database module for the Immo Scraper project.

Manages three tables:
    - properties      : one row per property (upsert on each run)
    - price_history   : append-only log of every price change
    - property_events : append-only log of listing state changes and price drops
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
    description  TEXT,
    agent        TEXT,
    missed_runs  INTEGER DEFAULT 0,
    enriched_at  TEXT,                 -- ISO-8601 datetime the detail page was visited (NULL = never)
    first_seen   TEXT NOT NULL,       -- ISO-8601 datetime
    last_seen    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date          TEXT NOT NULL,   -- ISO-8601 datetime
    source            TEXT NOT NULL,
    listings_returned INTEGER,
    status            TEXT NOT NULL DEFAULT 'ok'  -- 'ok' | 'failed' | 'empty'
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT NOT NULL REFERENCES properties(property_id),
    price       REAL NOT NULL,
    date        TEXT NOT NULL         -- ISO-8601 datetime
);

CREATE TABLE IF NOT EXISTS property_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT NOT NULL REFERENCES properties(property_id),
    event_type  TEXT NOT NULL,
    event_date  TEXT NOT NULL,
    source      TEXT,
    old_status  TEXT,
    new_status  TEXT,
    old_price   REAL,
    new_price   REAL
);

CREATE INDEX IF NOT EXISTS idx_property_events_property_date
    ON property_events(property_id, event_date);
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
        _backfill_enriched_at(conn)
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
                     similarity_score, similarity_profile, description, agent,
                     missed_runs, first_seen, last_seen, status)
                VALUES
                    (:property_id, :source, :title, :url, :price, :rooms, :bathrooms,
                     :price_first_seen, :sqm, :has_pool, :has_ac, :orientation,
                     :property_type, :operation, :city, :district, :neighborhood,
                     :postal_code, :latitude, :longitude, :energy_rating,
                     :year_built, :floor, :terrace, :elevator, :parking,
                     :is_favourite, :similarity_score, :similarity_profile,
                     :description, :agent, 0, :first_seen, :last_seen, 'active')
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
                    "description": prop.get("description"),
                    "agent": prop.get("agent"),
                    "first_seen": now,
                    "last_seen": now,
                },
            )
            _append_property_event(
                conn,
                property_id=property_id,
                event_type="inserted",
                event_date=now,
                source=prop.get("source", "unknown"),
                new_status="active",
                new_price=new_price,
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
                description = CASE
                    WHEN :description IS NOT NULL
                         AND LENGTH(COALESCE(:description, '')) > LENGTH(COALESCE(description, ''))
                    THEN :description ELSE description END,
                agent       = COALESCE(:agent, agent),
                missed_runs = 0,
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
                "description": prop.get("description"),
                "agent": prop.get("agent"),
                "last_seen": now,
            },
        )

        if existing["status"] == "inactive":
            _append_property_event(
                conn,
                property_id=property_id,
                event_type="reactivated",
                event_date=now,
                source=prop.get("source", "unknown"),
                old_status="inactive",
                new_status="active",
                new_price=new_price,
            )

        # Record price history only when the price actually changed
        if new_price is not None and new_price != old_price:
            _append_price_history(conn, property_id, new_price, now)
            _append_property_event(
                conn,
                property_id=property_id,
                event_type="price_change",
                event_date=now,
                source=prop.get("source", "unknown"),
                old_price=old_price,
                new_price=new_price,
            )
            logger.info(
                "Price change detected for '%s': %s → %s",
                property_id,
                old_price,
                new_price,
            )

        logger.debug("Updated property '%s'.", property_id)
        return "updated"


GRACE_MISSED_RUNS = 2  # consecutive runs a listing must be missing before it is marked inactive


def mark_inactive(
    active_ids: list,
    skip_sources: Optional[list] = None,
    grace: int = GRACE_MISSED_RUNS,
) -> int:
    """
    Handle properties that were **not** seen in the current run.

    Instead of flipping to inactive on a single miss (which produced lots of
    false delistings from pagination hiccups), each unseen active property
    gets its ``missed_runs`` counter incremented; only once it has been
    missing for *grace* consecutive runs is it marked ``inactive``.
    ``last_seen`` is left untouched so it always reflects the last real
    sighting (true time-on-market).

    Args:
        active_ids: List of property IDs observed in the current run.
        skip_sources: Sources whose scraper failed/under-returned this run;
            their rows are neither incremented nor deactivated.
        grace: Number of consecutive missed runs required to deactivate.

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
    params: list = []

    if active_ids:
        placeholders = ",".join("?" * len(active_ids))
        conditions.append(f"property_id NOT IN ({placeholders})")
        params.extend(active_ids)

    if skip_sources:
        source_placeholders = ",".join("?" * len(skip_sources))
        conditions.append(f"source NOT IN ({source_placeholders})")
        params.extend(list(skip_sources))

    where_clause = " AND ".join(conditions)

    with _get_conn() as conn:
        # 1. Increment the missed-run counter for every unseen active property.
        conn.execute(
            f"UPDATE properties SET missed_runs = COALESCE(missed_runs, 0) + 1 WHERE {where_clause}",
            params,
        )

        # 2. Deactivate the ones that exhausted the grace period.
        rows = conn.execute(
            f"""
            SELECT property_id, source, status
            FROM properties
            WHERE {where_clause} AND missed_runs >= ?
            """,
            params + [grace],
        ).fetchall()

        for row in rows:
            _append_property_event(
                conn,
                property_id=row["property_id"],
                event_type="inactive",
                event_date=now,
                source=row["source"],
                old_status=row["status"],
                new_status="inactive",
            )

        cursor = conn.execute(
            f"""
            UPDATE properties
            SET status = 'inactive'
            WHERE {where_clause} AND missed_runs >= ?
            """,
            params + [grace],
        )
        count = cursor.rowcount

    if count:
        logger.info("Marked %d propert(y/ies) as inactive (grace=%d).", count, grace)
    return count


def record_run(source: str, listings_returned: int, status: str = "ok") -> None:
    """Log one scraper execution so analyses can tell 'source failed' from 'listing gone'."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (run_date, source, listings_returned, status) VALUES (?, ?, ?, ?)",
            (_now(), source, listings_returned, status),
        )


def update_property_details(property_id: str, **fields) -> bool:
    """
    Update enrichment fields (description, floor, terrace, elevator, parking,
    year_built, …) for a property without touching scraping bookkeeping.

    Returns True if a row was updated.
    """
    allowed = {
        "description", "agent", "floor", "terrace", "elevator", "parking",
        "year_built", "energy_rating", "orientation", "neighborhood",
        "postal_code", "latitude", "longitude", "property_type", "sqm",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    set_clause = ", ".join(f"{col} = :{col}" for col in updates)
    updates["property_id"] = property_id
    with _get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE properties SET {set_clause} WHERE property_id = :property_id",
            updates,
        )
        return cursor.rowcount > 0


def mark_enriched(property_id: str) -> None:
    """
    Stamp that a listing's detail page has been visited, regardless of how much
    text we managed to extract. This is the source of truth for "already
    enriched" — NOT description length, because some agencies genuinely have a
    2-line description (< the old 400-char threshold), which used to make them
    look perpetually un-enriched and get re-visited every single run.
    """
    with _get_conn() as conn:
        conn.execute(
            "UPDATE properties SET enriched_at = ? WHERE property_id = ?",
            (_now(), property_id),
        )


def _backfill_enriched_at(conn: sqlite3.Connection) -> None:
    """
    One-time migration for DBs that predate the enriched_at column. Mark as
    enriched anything that was clearly detail-page visited before:
      - any source with a long (>= 400 char) description, OR
      - any NON-idealista listing with a non-empty description — the agency /
        yaencontre listing-card scrapers never set `description`, so any text
        there can only have come from a detail-page visit (this catches the
        short-but-complete agency descriptions, e.g. qgat_homes' one-liner,
        that would otherwise be re-visited forever).
    Idealista card-snippet descriptions stay NULL (card-only, still need a
    detail visit).
    """
    conn.execute(
        """
        UPDATE properties
        SET enriched_at = last_seen
        WHERE enriched_at IS NULL
          AND (
            LENGTH(COALESCE(description, '')) >= 400
            OR (source NOT LIKE 'idealista%' AND LENGTH(COALESCE(description, '')) > 0)
          )
        """
    )


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


def get_property_events(property_id: str) -> list:
    """
    Return all event-history entries for a property, oldest first.

    Returns:
        List of dicts with the event fields.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT event_type, event_date, source, old_status, new_status, old_price, new_price
            FROM property_events
            WHERE property_id = ?
            ORDER BY event_date, id
            """,
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


def _append_property_event(
    conn: sqlite3.Connection,
    property_id: str,
    event_type: str,
    event_date: str,
    source: Optional[str] = None,
    old_status: Optional[str] = None,
    new_status: Optional[str] = None,
    old_price: Optional[float] = None,
    new_price: Optional[float] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO property_events (
            property_id, event_type, event_date, source,
            old_status, new_status, old_price, new_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            property_id,
            event_type,
            event_date,
            source,
            old_status,
            new_status,
            _normalize_price(old_price),
            _normalize_price(new_price),
        ),
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
        "description": "ALTER TABLE properties ADD COLUMN description TEXT",
        "agent": "ALTER TABLE properties ADD COLUMN agent TEXT",
        "missed_runs": "ALTER TABLE properties ADD COLUMN missed_runs INTEGER DEFAULT 0",
        "enriched_at": "ALTER TABLE properties ADD COLUMN enriched_at TEXT",
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
