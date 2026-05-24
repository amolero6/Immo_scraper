"""
scraper_apify.py
----------------
Apify SDK template for scraping Idealista via a remote Actor.

The actor used here is 'canadesk/idealista-scraper' (community actor).
Swap the actor name for any other Idealista actor you have access to.

Environment variables (loaded from .env via python-dotenv):
  APIFY_API_TOKEN  – Your Apify API token (found in Apify Console → Settings)
"""
from __future__ import annotations

import logging
import os
from typing import List, Dict

from apify_client import ApifyClient
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APIFY_API_TOKEN: str = os.getenv("APIFY_API_TOKEN", "")

# Apify actor that scrapes Idealista.
# Swap for the actor ID you want to use; format is "username/actor-name".
IDEALISTA_ACTOR_ID: str = "canadesk/idealista-scraper"

# Actor input – adjust search parameters to your target area and criteria.
# Refer to the actor's documentation in Apify Console for supported fields.
ACTOR_INPUT: Dict = {
    # Starting URLs for the search; use Idealista's search result URL.
    "startUrls": [
        {
            "url": (
                "https://www.idealista.com/venta-viviendas/sant-cugat-del-valles-barcelona/"
                "con-mas-de-3-habitaciones,mas-de-2-banos/"
            )
        },
        {
            "url": (
                "https://www.idealista.com/venta-viviendas/cerdanyola-del-valles-barcelona/"
                "con-mas-de-3-habitaciones,mas-de-2-banos/"
            )
        },

    ],
    # Maximum number of listings to retrieve per run
    "maxItems": 200,
    # Proxy – Apify Residential proxies help bypass Datadome
    "proxyConfiguration": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
}


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def scrape_idealista() -> List[Dict]:
    """
    Run the Idealista Apify actor and return a list of normalised property dicts.

    Returns:
        List of property dictionaries ready to be upserted into the database.
        Returns an empty list if the actor fails or returns no results.

    Raises:
        EnvironmentError: If ``APIFY_API_TOKEN`` is not set.
    """
    if not APIFY_API_TOKEN:
        raise EnvironmentError(
            "APIFY_API_TOKEN is not set. Add it to your .env file."
        )

    client = ApifyClient(APIFY_API_TOKEN)

    logger.info("Starting Apify actor '%s' …", IDEALISTA_ACTOR_ID)
    run = client.actor(IDEALISTA_ACTOR_ID).call(run_input=ACTOR_INPUT)

    if not run:
        logger.error("Actor run returned None – check your API token and actor ID.")
        return []

    run_id = run.get("id", "?")
    status = run.get("status", "?")
    logger.info("Actor run '%s' finished with status: %s", run_id, status)

    if status != "SUCCEEDED":
        logger.warning(
            "Actor did not succeed (status=%s). Results may be incomplete.", status
        )

    # Fetch all items from the actor's default dataset
    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        logger.error("No dataset ID in actor run response.")
        return []

    raw_items = list(
        client.dataset(dataset_id).iterate_items()
    )
    logger.info("Retrieved %d raw items from dataset '%s'.", len(raw_items), dataset_id)

    return [_normalise(item) for item in raw_items if _normalise(item)]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise(item: Dict) -> Dict | None:
    """
    Map a raw Apify actor output item to the schema used by the database.

    The field names below are based on the 'canadesk/idealista-scraper' actor.
    Adjust the key names if you switch to a different actor.
    """
    try:
        url: str = item.get("url") or item.get("propertyUrl", "")
        if not url:
            return None

        # Build a stable ID from the Idealista property ID embedded in the URL
        # e.g. https://www.idealista.com/inmueble/12345678/
        import re
        id_match = re.search(r"/inmueble/(\d+)/", url)
        property_id = f"idealista_{id_match.group(1)}" if id_match else f"idealista_{hash(url)}"

        price_raw = item.get("price") or item.get("priceInfo", {}).get("amount")
        price = _to_float(price_raw)

        rooms_raw = item.get("rooms") or item.get("roomNumber")
        rooms = int(rooms_raw) if rooms_raw is not None else None

        baths_raw = item.get("bathrooms") or item.get("bathNumber")
        bathrooms = int(baths_raw) if baths_raw is not None else None

        sqm_raw = item.get("size") or item.get("constructedArea")
        sqm = int(sqm_raw) if sqm_raw is not None else None

        property_type = item.get("propertyType") or item.get("property_type") or item.get("type")
        operation = item.get("operation") or item.get("dealType")
        city = item.get("city") or item.get("town") or item.get("municipality")
        district = item.get("district") or item.get("area")
        neighborhood = item.get("neighborhood") or item.get("zone")
        postal_code = item.get("postalCode") or item.get("zipCode")
        latitude = _to_float(item.get("latitude") or item.get("lat"))
        longitude = _to_float(item.get("longitude") or item.get("lng") or item.get("lon"))
        energy_rating = item.get("energyRating") or item.get("energy_label")
        year_built = _to_int(item.get("yearBuilt") or item.get("builtYear") or item.get("constructionYear"))
        floor = item.get("floor")
        terrace = _to_bool(item.get("terrace"))
        elevator = _to_bool(item.get("elevator"))
        parking = _to_bool(item.get("parking") or item.get("garage"))

        # Pool / AC detection – Idealista may expose these as features or in
        # the full description text
        features: list = item.get("features", []) or []
        description: str = (item.get("description") or "").lower()
        has_pool = (
            any("piscina" in str(f).lower() for f in features)
            or "piscina" in description
        )
        has_ac = (
            any("aire" in str(f).lower() for f in features)
            or "aire acondicionado" in description
        )

        orientation = item.get("orientation") or item.get("cardOrientation")

        return {
            "property_id": property_id,
            "source": "idealista",
            "title": item.get("title") or item.get("address", ""),
            "url": url,
            "price": price,
            "rooms": rooms,
            "bathrooms": bathrooms,
            "sqm": sqm,
            "has_pool": has_pool,
            "has_ac": has_ac,
            "orientation": orientation,
            "property_type": property_type,
            "operation": operation,
            "city": city,
            "district": district,
            "neighborhood": neighborhood,
            "postal_code": postal_code,
            "latitude": latitude,
            "longitude": longitude,
            "energy_rating": energy_rating,
            "year_built": year_built,
            "floor": floor,
            "terrace": terrace,
            "elevator": elevator,
            "parking": parking,
        }
    except Exception as exc:
        logger.warning("Could not normalise Apify item: %s | item=%s", exc, item)
        return None


def _to_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_bool(value: object) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip().lower()
    return int(text in {"1", "true", "yes", "y", "si", "sí"})
