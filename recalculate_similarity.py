"""
recalculate_similarity.py
------------------------
Backfill or refresh similarity scores for existing listings.

Use this when you change the favorite profiles in similarity_config.py and want
to recompute the current best score for everything already stored in the DB.
"""

import logging

from database import init_db, get_property, upsert_property
from matching import best_similarity_match
from similarity_config import FAVORITE_PROFILES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run() -> None:
    init_db()

    # Re-score the current contents of the database by iterating over the
    # properties already stored. This keeps the score current without adding
    # any history table.
    from database import _get_conn  # local import to avoid exposing internals broadly

    with _get_conn() as conn:
        rows = conn.execute("SELECT property_id FROM properties").fetchall()

    logger.info("Recomputing similarity for %d properties.", len(rows))
    for row in rows:
        prop = get_property(row[0])
        if not prop:
            continue
        similarity = best_similarity_match(prop, FAVORITE_PROFILES)
        prop["similarity_score"] = similarity["similarity_score"]
        prop["similarity_profile"] = similarity["similarity_profile"]
        upsert_property(prop)

    logger.info("Similarity refresh complete.")


if __name__ == "__main__":
    run()