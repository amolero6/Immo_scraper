"""
matching.py
-----------
Similarity scoring helpers for property listings.

The score is a weighted similarity index against a curated set of favorite
properties. Missing fields are ignored rather than treated as failures.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Dict, Iterable, List, Tuple


def best_similarity_match(prop: Dict, ideal_properties: List[Dict]) -> Dict:
    """
    Compare one property against all configured ideal profiles.

    Returns a dictionary with:
      - similarity_score: integer 0-100
      - similarity_profile: name of the best matching profile
      - similarity_details: lightweight explanation of the match
    """
    if not ideal_properties:
        return {
            "similarity_score": 0,
            "similarity_profile": None,
            "similarity_details": "No ideal profiles configured",
        }

    best_result = {
        "similarity_score": 0,
        "similarity_profile": None,
        "similarity_details": "No matching profile",
    }

    for profile in ideal_properties:
        result = score_against_profile(prop, profile)
        if result["similarity_score"] > best_result["similarity_score"]:
            best_result = result

    return best_result


def score_against_profile(prop: Dict, profile: Dict) -> Dict:
    """
    Score a listing against a single ideal profile.

    The result is a weighted score between 0 and 100.
    """
    weights = {
        "price": 22,
        "rooms": 10,
        "bathrooms": 10,
        "sqm": 14,
        "property_type": 8,
        "location": 20,
        "features": 8,
        "terms": 5,
        "source": 3,
    }

    score_parts: List[Tuple[str, float]] = []

    score_parts.append(
        (
            "price",
            _score_numeric(
                prop.get("price"),
                profile.get("target_price"),
                profile.get("price_tolerance_pct", 0.25),
                0,
            ),
        )
    )
    score_parts.append(
        (
            "rooms",
            _score_numeric(
                prop.get("rooms"),
                profile.get("target_rooms"),
                0.0,
                profile.get("rooms_tolerance", 1),
            ),
        )
    )
    score_parts.append(
        (
            "bathrooms",
                from __future__ import annotations
            _score_numeric(
                prop.get("bathrooms"),
                profile.get("target_bathrooms"),
                0.0,
                profile.get("bathrooms_tolerance", 1),
            ),
        )
    )
    score_parts.append(
        (
            "sqm",
            _score_numeric(
                prop.get("sqm"),
                profile.get("target_sqm"),
                profile.get("sqm_tolerance_pct", 0.25),
                0,
            ),
        )
    )

    score_parts.append(("property_type", _score_property_type(prop, profile)))
    score_parts.append(("location", _score_location(prop, profile)))
    score_parts.append(("features", _score_features(prop, profile.get("preferred_features", {}))))
    score_parts.append(("terms", _score_terms(prop, profile.get("preferred_terms", []))))
    score_parts.append(("source", _score_source(prop, profile.get("preferred_sources", []))))

    weighted_total = 0.0
    used_weight = 0
    for key, part_score in score_parts:
        weight = weights.get(key, 0)
        if weight <= 0:
            continue
        if key == "source" and not profile.get("preferred_sources"):
            continue
        if key == "location" and not (
            profile.get("required_location_terms")
            or profile.get("city")
            or profile.get("district")
            or profile.get("neighborhood")
            or profile.get("postal_code")
            or profile.get("latitude")
            or profile.get("longitude")
        ):
            continue
        if key == "terms" and profile.get("preferred_terms") is None:
            continue
        weighted_total += part_score * weight
        used_weight += weight

    similarity_score = int(round((weighted_total / used_weight) * 100)) if used_weight else 0
    profile_name = profile.get("name") or profile.get("label") or "Unnamed profile"
    details = _build_details(score_parts, profile)

    return {
        "similarity_score": max(0, min(100, similarity_score)),
        "similarity_profile": profile_name,
        "similarity_details": details,
    }


def _score_numeric(actual: int | None, target: int | None, tolerance_pct: float, tolerance_abs: int) -> float:
    if actual is None or target is None:
        return 0.0

    tolerance = max(int(round(target * tolerance_pct)), tolerance_abs, 1)
    distance = abs(actual - target)
    if distance >= tolerance:
        return 0.0
    return 1.0 - (distance / tolerance)


def _score_property_type(prop: Dict, profile: Dict) -> float:
    expected = profile.get("property_type")
    if not expected:
        return 0.0

    actual = _normalise_text(str(prop.get("property_type") or prop.get("title") or ""))
    expected_normalised = _normalise_text(str(expected))
    if not actual:
        return 0.0
    return 1.0 if expected_normalised in actual else 0.0


def _score_location(prop: Dict, profile: Dict) -> float:
    textual_score = _score_location_terms(prop, profile)
    geo_score = _score_geo_distance(prop, profile)

    if geo_score is None:
        return textual_score

    # Bias toward geo when coordinates exist, but keep the text match in play.
    return max(textual_score * 0.6 + geo_score * 0.4, geo_score)


def _score_location_terms(prop: Dict, profile: Dict) -> float:
    terms = list(profile.get("required_location_terms", []))
    extra_terms = [profile.get("city"), profile.get("district"), profile.get("neighborhood"), profile.get("postal_code")]
    terms.extend(term for term in extra_terms if term)
    if not terms:
        return 0.0

    haystack = _normalise_text(
        " ".join(
            str(value)
            for value in [
                prop.get("title", ""),
                prop.get("url", ""),
                prop.get("source", ""),
                prop.get("orientation", ""),
                prop.get("city", ""),
                prop.get("district", ""),
                prop.get("neighborhood", ""),
                prop.get("address", ""),
                prop.get("location", ""),
            ]
            if value
        )
    )

    matched = 0
    for term in terms:
        needle = _normalise_text(str(term))
        if needle and needle in haystack:
            matched += 1
    return matched / len(terms)


def _score_geo_distance(prop: Dict, profile: Dict) -> float | None:
    profile_lat = _coerce_float(profile.get("latitude"))
    profile_lon = _coerce_float(profile.get("longitude"))
    prop_lat = _coerce_float(prop.get("latitude"))
    prop_lon = _coerce_float(prop.get("longitude"))

    if None in (profile_lat, profile_lon, prop_lat, prop_lon):
        return None

    max_distance = _coerce_float(profile.get("max_distance_km")) or 5.0
    distance = _haversine_km(profile_lat, profile_lon, prop_lat, prop_lon)
    if distance >= max_distance:
        return 0.0
    return 1.0 - (distance / max_distance)


def _score_features(prop: Dict, preferred_features: Dict) -> float:
    if not preferred_features:
        return 0.0

    matches = 0
    for key, expected in preferred_features.items():
        if prop.get(key) == expected:
            matches += 1
    return matches / len(preferred_features)


def _score_terms(prop: Dict, terms: Iterable[str]) -> float:
    terms = [term for term in terms if term]
    if not terms:
        return 0.0

    haystack = _normalise_text(
        " ".join(
            str(value)
            for value in [
                prop.get("title", ""),
                prop.get("url", ""),
                prop.get("source", ""),
                prop.get("orientation", ""),
            ]
            if value
        )
    )

    matched = 0
    for term in terms:
        needle = _normalise_text(str(term))
        if needle and needle in haystack:
            matched += 1
    return matched / len(terms)


def _score_source(prop: Dict, preferred_sources: Iterable[str]) -> float:
    preferred_sources = [source for source in preferred_sources if source]
    if not preferred_sources:
        return 0.0
    return 1.0 if prop.get("source") in preferred_sources else 0.0


def _coerce_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _build_details(score_parts: List[Tuple[str, float]], profile: Dict) -> str:
    summary = ", ".join(f"{name}={score:.2f}" for name, score in score_parts if score > 0)
    if not summary:
        summary = "no component matched"
    return f"{profile.get('name', 'Unnamed profile')}: {summary}"


def _normalise_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text.lower()).strip()