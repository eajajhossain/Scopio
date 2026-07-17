"""Business discovery via the Overpass API (OpenStreetMap).

Public Overpass endpoints are frequently overloaded (502/504/timeouts), so this
client fails over across several mirrors with retries + backoff. Returns raw OSM
elements (nodes/ways) tagged as businesses within a radius; normalization into
our `Business` shape happens in normalizer.py.
"""
import asyncio
import logging
from typing import Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class OverpassError(RuntimeError):
    pass


class PlacesPort(Protocol):
    async def find_businesses(
        self, lat: float, lng: float, radius_m: int,
        osm_filters: dict[str, list[str]] | None = None,
    ) -> list[dict]: ...


# Public mirrors, tried in order. Self-host and put yours first via OVERPASS_URL.
_DEFAULT_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# OSM amenity values that are businesses worth contacting (broad, but excludes
# non-business POIs like benches/parking/toilets).
_AMENITY_FILTER = (
    "restaurant|cafe|fast_food|bar|pub|food_court|ice_cream|"
    "bank|atm|bureau_de_change|money_transfer|"
    "pharmacy|clinic|hospital|doctors|dentist|veterinary|"
    "fuel|car_rental|car_wash|car_repair|driving_school|"
    "cinema|theatre|nightclub|internet_cafe|"
    "marketplace|fuel|coworking_space"
)
_TOURISM_FILTER = "hotel|guest_house|hostel|motel|apartment"
_LEISURE_FILTER = "fitness_centre|sports_centre|dance|amusement_arcade"


def _sanitize(values: list[str]) -> list[str]:
    """Keep only safe OSM tag values (letters/digits/underscore) for the regex clause."""
    out = []
    for v in values:
        v = str(v).strip().lower()
        if v and all(c.isalnum() or c == "_" for c in v):
            out.append(v)
    return out


def build_query(
    lat: float, lng: float, radius_m: int, osm_filters: dict[str, list[str]] | None = None
) -> str:
    """Build an Overpass QL query for businesses around a point.

    `nwr` matches nodes, ways and relations in one statement. `out center tags`
    gives a single coordinate per element.

    Without `osm_filters` the query is broad: shops, offices, services (craft),
    food/health/finance (amenity), lodging (tourism) and gyms (leisure). With
    `osm_filters` (e.g. {"amenity": ["cafe","restaurant"], "shop": ["bakery"]}) it
    targets ONLY those tag values — so discovery finds just the seller's ideal leads.
    """
    around = f"around:{radius_m},{lat},{lng}"

    clauses: list[str] = []
    if osm_filters:
        for key, values in osm_filters.items():
            safe = _sanitize(values or [])
            if safe:
                clauses.append(f'  nwr["{key}"~"{"|".join(safe)}"]({around});')

    if not clauses:
        # Broad default (no targeting / empty profile / all values sanitized away).
        clauses = [
            f'  nwr["shop"]({around});',
            f'  nwr["office"]({around});',
            f'  nwr["craft"]({around});',
            f'  nwr["healthcare"]({around});',
            f'  nwr["amenity"~"{_AMENITY_FILTER}"]({around});',
            f'  nwr["tourism"~"{_TOURISM_FILTER}"]({around});',
            f'  nwr["leisure"~"{_LEISURE_FILTER}"]({around});',
        ]

    body = "\n".join(clauses)
    return f"""
[out:json][timeout:25];
(
{body}
);
out center tags;
""".strip()


class OverpassClient:
    def __init__(
        self,
        urls: list[str] | None = None,
        user_agent: str | None = None,
        max_retries: int = 2,
        # The query itself is capped server-side at 25s ([timeout:25] in the QL), so a
        # healthy mirror answers well within this; a hung one fails over quickly
        # instead of burning 90s before the next mirror gets a chance.
        timeout: float = 35.0,
    ):
        # Honor a configured primary endpoint, then fall back to public mirrors.
        configured = (settings.overpass_url or "").strip()
        mirrors = [u for u in _DEFAULT_MIRRORS if u != configured]
        self.urls = urls or ([configured] + mirrors if configured else _DEFAULT_MIRRORS)
        self.user_agent = user_agent or settings.http_user_agent
        self.max_retries = max_retries
        self.timeout = timeout

    async def find_businesses(
        self, lat: float, lng: float, radius_m: int,
        osm_filters: dict[str, list[str]] | None = None,
    ) -> list[dict]:
        query = build_query(lat, lng, radius_m, osm_filters)
        headers = {"User-Agent": self.user_agent}
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries):
                for url in self.urls:
                    try:
                        resp = await client.post(url, data={"data": query}, headers=headers)
                        resp.raise_for_status()
                        return resp.json().get("elements", [])
                    except (httpx.HTTPError, ValueError) as exc:
                        last_error = exc
                        logger.warning(
                            "Overpass mirror failed (attempt %d): %s -> %s",
                            attempt + 1, url, exc,
                        )
                        continue
                await asyncio.sleep(2 * (attempt + 1))  # backoff between full rounds

        raise OverpassError(
            f"All Overpass mirrors failed after {self.max_retries} rounds: {last_error}"
        )
