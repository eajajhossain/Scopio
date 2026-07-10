"""De-duplication key: same business name in the same ~150m geocell == one business."""
import pygeohash
from slugify import slugify

# precision 7 geohash ≈ 153m × 153m cell
_GEOHASH_PRECISION = 7


def dedup_key(name: str, lat: float | None, lng: float | None) -> str:
    name_slug = slugify(name or "") or "unknown"
    if lat is None or lng is None:
        return f"{name_slug}_nogeo"
    cell = pygeohash.encode(lat, lng, precision=_GEOHASH_PRECISION)
    return f"{name_slug}_{cell}"


def area_geohash(lat: float, lng: float, precision: int = 6) -> str:
    """Coarser geohash (~1.2km cell) used as the area-cache key."""
    return pygeohash.encode(lat, lng, precision=precision)


def merge_by_dedup_key(businesses: list[dict]) -> list[dict]:
    """Collapse a batch so each dedup_key appears once, merging contact fields.

    Required before INSERT ... ON CONFLICT: Postgres rejects a batch that contains
    the same conflict key twice ("cannot affect row a second time"). Dense areas
    routinely produce in-batch duplicates (a place mapped as both node and way).
    """
    merged: dict[str, dict] = {}
    for b in businesses:
        key = b["dedup_key"]
        if key in merged:
            existing = merged[key]
            for field in ("phone", "email", "website", "address"):
                if not existing.get(field) and b.get(field):
                    existing[field] = b[field]
        else:
            merged[key] = dict(b)
    return list(merged.values())
