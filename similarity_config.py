"""
similarity_config.py
--------------------
User-editable reference profiles for similarity scoring.

Treat this file as the curated list of desired or favorite properties. Each
profile should describe a house or flat you like, and the matcher will compare
new listings against all of them and keep the best score.

The score is intentionally a weighted similarity index, not a literal PCA.
That keeps the behaviour transparent and easy to tune when some fields are
missing.
"""

FAVORITE_PROFILES = [
    {
        "name": "Casa familiar Sant Cugat",
        "is_favourite": True,
        "property_type": "house",
        "city": "Sant Cugat del Vallès",
        "district": "Valldoreix",
        "target_price": 650_000,
        "price_tolerance_pct": 0.25,
        "target_rooms": 4,
        "rooms_tolerance": 1,
        "target_bathrooms": 2,
        "bathrooms_tolerance": 1,
        "target_sqm": 140,
        "sqm_tolerance_pct": 0.25,
        "required_location_terms": ["sant cugat", "valldoreix", "mirasol", "mira-sol"],
        "preferred_terms": ["casa", "adosada", "unifamiliar"],
        "preferred_features": {"has_pool": True, "has_ac": True},
        "preferred_sources": ["idealista", "local"],
        "max_distance_km": 5,
    },
    {
        "name": "Piso amplio Cerdanyola",
        "is_favourite": True,
        "property_type": "flat",
        "city": "Cerdanyola del Vallès",
        "district": "Bellaterra",
        "target_price": 450_000,
        "price_tolerance_pct": 0.20,
        "target_rooms": 3,
        "rooms_tolerance": 1,
        "target_bathrooms": 2,
        "bathrooms_tolerance": 1,
        "target_sqm": 100,
        "sqm_tolerance_pct": 0.20,
        "required_location_terms": ["cerdanyola", "bellaterra", "serra parera"],
        "preferred_terms": ["piso", "ático", "duplex"],
        "preferred_features": {"has_ac": True},
        "preferred_sources": ["idealista", "local"],
        "max_distance_km": 6,
    },
]

# Global alert threshold after the best similarity profile is calculated.
MIN_SIMILARITY_SCORE = 65

# Hard location gate for alerts. If the listing text does not mention one of
# these terms, it is ignored even if the score is high.
ALERT_LOCATION_TERMS = [
    "sant cugat",
    "cerdanyola",
    "valldoreix",
    "mirasol",
    "bellaterra",
    "serra parera",
]