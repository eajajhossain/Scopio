"""Manual CSV import to fill gaps OSM misses (especially in small towns).

Expected header: name,category,address,phone,email,website,lat,lng
Only `name` is required. Rows run through the same dedup + upsert path as OSM.
"""
import csv
import io
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.discovery.dedup import dedup_key
from app.services.discovery.pipeline import upsert_businesses

_EXPECTED = {"name", "category", "address", "phone", "email", "website", "lat", "lng"}


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _to_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_rows(content: str) -> tuple[list[dict], list[str]]:
    """Parse CSV text into normalized business dicts + a list of row errors."""
    reader = csv.DictReader(io.StringIO(content))
    businesses: list[dict] = []
    errors: list[str] = []
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"row {i}: missing name")
            continue
        lat = _to_float(row.get("lat"))
        lng = _to_float(row.get("lng"))
        businesses.append(
            {
                "source": "manual_import",
                "name": name,
                "category": (row.get("category") or "").strip() or "other",
                "address": (row.get("address") or "").strip() or None,
                "lat": lat,
                "lng": lng,
                "phone": (row.get("phone") or "").strip() or None,
                "email": (row.get("email") or "").strip() or None,
                "website": (row.get("website") or "").strip() or None,
                "dedup_key": dedup_key(name, lat, lng),
            }
        )
    return businesses, errors


async def import_csv(
    session: AsyncSession, tenant_id: str, search_job_id: str | None, content: str
) -> ImportResult:
    businesses, errors = parse_rows(content)
    imported = await upsert_businesses(session, tenant_id, search_job_id, businesses)
    return ImportResult(imported=imported, skipped=len(errors), errors=errors)
