
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.services.discovery.dedup import dedup_key

logger = logging.getLogger(__name__)

# Geoapify silently returns almost nothing when too many categories are combined in
# one request, so we query in small batches and merge the results.
_CATEGORY_GROUPS = [
    ["commercial", "catering"],
    ["healthcare", "accommodation"],
    ["service", "office"],
    ["education", "entertainment"],
    ["leisure", "activity"],
]

# Map Geoapify category prefixes to our internal taxonomy.
_PREFIX_MAP = [
    ("catering", "food"),
    ("healthcare", "health"),
    ("commercial.supermarket", "retail"),
    ("commercial", "retail"),
    ("accommodation", "hospitality"),
    ("office", "services"),
    ("service", "services"),
    ("leisure", "services"),
    ("activity", "services"),
    ("entertainment", "services"),
    ("education", "other"),
]


def classify(categories: list[str]) -> str:
    for cat in categories:
        for prefix, mapped in _PREFIX_MAP:
            if cat.startswith(prefix):
                return mapped
    return "other"


def normalize_feature(feature: dict[str, Any]) -> dict[str, Any] | None:
    props = feature.get("properties") or {}
    name = props.get("name")
    if not name:
        return None
    lat, lng = props.get("lat"), props.get("lon")
    raw = (props.get("datasource") or {}).get("raw") or {}
    contact = props.get("contact") or {}
    phone = contact.get("phone") or raw.get("phone") or raw.get("contact:phone")
    website = props.get("website") or raw.get("website") or raw.get("contact:website")
    email = raw.get("email") or raw.get("contact:email")
    return {
        "source": "geoapify",
        "source_ref": props.get("place_id"),
        "name": name,
        "category": classify(props.get("categories") or []),
        "address": props.get("formatted") or props.get("address_line2"),
        "lat": lat,
        "lng": lng,
        "phone": phone,
        "email": email,
        "website": website,
        "raw": raw or None,
        "dedup_key": dedup_key(name, lat, lng),
    }


class GeoapifyClient:
    def __init__(self, api_key: str | None = None, url: str | None = None):
        self.api_key = api_key or settings.geoapify_api_key
        self.url = url or settings.geoapify_url

    async def find_businesses(self, lat: float, lng: float, radius_m: int) -> list[dict]:
        if not self.api_key:
            return []
        circle = f"circle:{lng},{lat},{radius_m}"
        seen_ids: set = set()
        out: list[dict] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for group in _CATEGORY_GROUPS:
                params = {
                    "categories": ",".join(group),
                    "filter": circle,
                    "limit": 100,
                    "apiKey": self.api_key,
                }
                try:
                    resp = await client.get(self.url, params=params)
                    resp.raise_for_status()
                    features = resp.json().get("features", [])
                except httpx.HTTPError as exc:
                    logger.warning("geoapify group %s failed: %s", group, exc)
                    continue
                for f in features:
                    pid = (f.get("properties") or {}).get("place_id")
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)
                    norm = normalize_feature(f)
                    if norm:
                        out.append(norm)
        logger.info("geoapify returned %d businesses", len(out))
        return out
