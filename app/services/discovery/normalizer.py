"""Turn raw OSM elements into our normalized Business shape + category taxonomy."""
from typing import Any

from app.services.discovery.dedup import dedup_key

# Map OSM amenity/shop/office tag values into a small internal taxonomy.
_FOOD = {"restaurant", "cafe", "fast_food", "bar", "pub", "bakery"}
_HEALTH = {"pharmacy", "clinic", "doctors", "dentist", "chemist", "hospital"}
_FINANCE = {"bank", "atm"}
_SERVICES = {"laundry", "beauty", "hairdresser", "dry_cleaning", "car_repair"}


def classify(tags: dict[str, str]) -> str:
    shop = tags.get("shop")
    amenity = tags.get("amenity")
    office = tags.get("office")

    if amenity in _FOOD or shop == "bakery":
        return "food"
    if tags.get("healthcare") or amenity in _HEALTH or shop == "chemist":
        return "health"
    if amenity in _FINANCE:
        return "finance"
    if tags.get("tourism"):
        return "hospitality"
    if (
        office
        or tags.get("craft")
        or tags.get("leisure")
        or shop in _SERVICES
        or amenity in _SERVICES
    ):
        return "services"
    if shop:
        return "retail"
    return "other"


def _coords(element: dict[str, Any]) -> tuple[float | None, float | None]:
    if "lat" in element and "lon" in element:  # node
        return element["lat"], element["lon"]
    center = element.get("center")  # way/relation
    if center:
        return center.get("lat"), center.get("lon")
    return None, None


def _address(tags: dict[str, str]) -> str | None:
    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:suburb"),
        tags.get("addr:city"),
        tags.get("addr:postcode"),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def normalize_element(element: dict[str, Any]) -> dict[str, Any] | None:
    """Return a normalized business dict, or None if the element has no usable name."""
    tags = element.get("tags") or {}
    name = tags.get("name")
    if not name:
        return None  # unnamed POIs are not useful leads

    lat, lng = _coords(element)
    return {
        "source": "osm",
        "source_ref": f"{element.get('type')}/{element.get('id')}",
        "name": name,
        "category": classify(tags),
        "address": _address(tags),
        "lat": lat,
        "lng": lng,
        "phone": (
            tags.get("phone")
            or tags.get("contact:phone")
            or tags.get("contact:mobile")
            or tags.get("mobile")
        ),
        "email": tags.get("email") or tags.get("contact:email"),
        "website": tags.get("website") or tags.get("contact:website"),
        "raw": tags,
        "dedup_key": dedup_key(name, lat, lng),
    }


def normalize_many(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for el in elements:
        norm = normalize_element(el)
        if norm:
            out.append(norm)
    return out
