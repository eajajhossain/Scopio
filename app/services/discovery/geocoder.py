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


class NominatimGeocoder:
    def __init__(self, base_url: str | None = None, user_agent: str | None = None):
        self.base_url = (base_url or settings.nominatim_url).rstrip("/")
        self.user_agent = user_agent or settings.http_user_agent

    async def geocode(self, address: str) -> GeoPoint:
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
        return GeoPoint(
            lat=float(top["lat"]),
            lng=float(top["lon"]),
            display_name=top.get("display_name", address),
        )
