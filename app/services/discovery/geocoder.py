"""Geocoding via Nominatim (OpenStreetMap). Free; respect the usage policy.

Wrapped behind a small interface so a different provider (or a self-hosted
Nominatim) can be swapped in without touching the pipeline.
"""
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import settings


@dataclass(slots=True)
class GeoPoint:
    lat: float
    lng: float
    display_name: str


class GeocodeError(RuntimeError):
    pass


class GeocoderPort(Protocol):
    async def geocode(self, address: str) -> GeoPoint: ...


# Addresses don't move: cache geocode hits per process so repeat searches skip
# the public-API round-trip (also kinder to Nominatim's usage policy).
_geo_cache: dict[str, GeoPoint] = {}
_GEO_CACHE_MAX = 1000


class NominatimGeocoder:
    def __init__(self, base_url: str | None = None, user_agent: str | None = None):
        self.base_url = (base_url or settings.nominatim_url).rstrip("/")
        self.user_agent = user_agent or settings.http_user_agent

    async def geocode(self, address: str) -> GeoPoint:
        key = " ".join(address.lower().split())
        if key in _geo_cache:
            return _geo_cache[key]
        params = {"q": address, "format": "jsonv2", "limit": 1}
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{self.base_url}/search", params=params, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
        if not data:
            raise GeocodeError(f"No geocoding result for address: {address!r}")
        top = data[0]
        point = GeoPoint(
            lat=float(top["lat"]),
            lng=float(top["lon"]),
            display_name=top.get("display_name", address),
        )
        if len(_geo_cache) >= _GEO_CACHE_MAX:   # simple bound, drop oldest
            _geo_cache.pop(next(iter(_geo_cache)))
        _geo_cache[key] = point
        return point
