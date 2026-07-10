"""Discovery pipeline: geocode -> cache check -> Overpass -> normalize -> dedup/upsert.

Runs inside the ARQ worker (see app/workers/discovery_worker.py) against a tenant-
scoped session. Each step advances the search_job status so the UI can poll progress.
"""
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.area_cache import AreaCache
from app.models.business import Business
from app.models.search_job import SearchJob
from app.models.search_job_business import SearchJobBusiness
from app.services.discovery.dedup import area_geohash, merge_by_dedup_key
from app.services.discovery.geoapify import GeoapifyClient
from app.services.discovery.geocoder import GeocoderPort, NominatimGeocoder
from app.services.discovery.normalizer import normalize_many
from app.services.discovery.overpass import OverpassClient, PlacesPort

logger = logging.getLogger(__name__)


def _chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _cache_category(category: str | None, osm_filters: dict | None) -> str | None:
    """A stable area-cache discriminator that includes the target filter.

    The area cache is keyed by (geohash, radius, category). Two sellers searching the
    same spot with different target filters must not share a cache entry, so we encode
    the filter into this key. Returns the plain category when there's no targeting.
    """
    if not osm_filters:
        return category
    sig = ";".join(
        f"{k}={','.join(sorted(str(v) for v in vals))}"
        for k, vals in sorted(osm_filters.items())
        if vals
    )
    return f"{category or ''}|{sig}" if sig else category


async def _set_status(session: AsyncSession, job: SearchJob, status: str) -> None:
    job.status = status
    await session.commit()


async def _get_cached_area(
    session: AsyncSession, geohash: str, radius_m: int, category: str | None
) -> list[dict] | None:
    stmt = select(AreaCache).where(
        AreaCache.geohash == geohash,
        AreaCache.radius_m == radius_m,
        AreaCache.category.is_(category) if category is None else AreaCache.category == category,
    )
    cache = (await session.execute(stmt)).scalar_one_or_none()
    if cache and cache.expires_at > datetime.now(UTC):
        logger.info("area_cache hit geohash=%s", geohash)
        return cache.payload
    return None


async def _store_area_cache(
    session: AsyncSession,
    geohash: str,
    radius_m: int,
    category: str | None,
    payload: list[dict],
) -> None:
    expires = datetime.now(UTC) + timedelta(days=settings.area_cache_ttl_days)
    stmt = pg_insert(AreaCache).values(
        geohash=geohash,
        radius_m=radius_m,
        category=category,
        payload=payload,
        expires_at=expires,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["geohash", "radius_m", "category"],
        set_={"payload": stmt.excluded.payload, "expires_at": expires, "fetched_at": datetime.now(UTC)},
    )
    await session.execute(stmt)
    await session.commit()


async def upsert_businesses(
    session: AsyncSession,
    tenant_id: str,
    search_job_id: str | None,
    businesses: list[dict],
) -> int:
    """Insert businesses, merging onto existing rows by (tenant_id, dedup_key).

    Existing contact fields are preserved unless the new row provides a value.
    `business.search_job_id` records the FIRST job to discover it and is not
    reassigned; the search_job_business link table records every job that found
    it (so re-discovering a business never steals it from an earlier search).
    Returns the number of businesses processed.
    """
    if not businesses:
        return 0

    # Collapse in-batch duplicates by dedup_key first (Postgres rejects an
    # INSERT ... ON CONFLICT whose VALUES repeat the same conflict key).
    def _s(v):
        # Some sources return phones/numbers as ints; VARCHAR columns need str.
        return None if v is None else str(v)

    rows = []
    for b in merge_by_dedup_key(businesses):
        rows.append(
            {
                "tenant_id": tenant_id,
                "search_job_id": search_job_id,
                "source": b.get("source", "osm"),
                "source_ref": _s(b.get("source_ref")),
                "name": _s(b["name"]),
                "category": _s(b.get("category")),
                "address": _s(b.get("address")),
                "lat": b.get("lat"),
                "lng": b.get("lng"),
                "phone": _s(b.get("phone")),
                "email": _s(b.get("email")),
                "website": _s(b.get("website")),
                "raw": b.get("raw"),
                "dedup_key": b["dedup_key"],
            }
        )
    # Insert in batches: Postgres caps a single statement at 32767 bind params.
    # business has 16 columns, so keep well under that (500 rows = 8000 params).
    for batch in _chunks(rows, 500):
        stmt = pg_insert(Business).values(batch)
        excl = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "dedup_key"],
            set_={
                "address": func.coalesce(excl.address, Business.address),
                "phone": func.coalesce(excl.phone, Business.phone),
                "email": func.coalesce(excl.email, Business.email),
                "website": func.coalesce(excl.website, Business.website),
                "updated_at": datetime.now(UTC),
            },
        )
        await session.execute(stmt)

    # Link every business (new or pre-existing) to this search job, also batched.
    if search_job_id:
        all_keys = [b["dedup_key"] for b in rows]
        ids: list = []
        for key_batch in _chunks(all_keys, 5000):
            ids.extend(
                (
                    await session.execute(
                        select(Business.id).where(Business.dedup_key.in_(key_batch))
                    )
                ).scalars().all()
            )
        for id_batch in _chunks(ids, 5000):
            link = pg_insert(SearchJobBusiness).values(
                [{"search_job_id": search_job_id, "business_id": bid} for bid in id_batch]
            ).on_conflict_do_nothing()
            await session.execute(link)

    await session.commit()
    return len(rows)


async def run_discovery(
    session: AsyncSession,
    job_id: str,
    geocoder: GeocoderPort | None = None,
    places: PlacesPort | None = None,
) -> None:
    geocoder = geocoder or NominatimGeocoder()
    places = places or OverpassClient()

    job = (
        await session.execute(select(SearchJob).where(SearchJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        logger.error("search_job %s not found (tenant GUC set?)", job_id)
        return

    # Context-aware targeting: only search the business types that suit the seller.
    osm_filters = (job.target_profile or {}).get("osm_filters") or None
    # Fold the target filter into the cache key so two sellers searching the same area
    # with different targets don't read each other's cached results.
    cache_cat = _cache_category(job.category, osm_filters)

    try:
        # 1. Geocode — skipped when GPS coords were supplied with the job.
        if job.center_lat is not None and job.center_lng is not None:
            lat, lng = job.center_lat, job.center_lng
        else:
            await _set_status(session, job, "geocoding")
            point = await geocoder.geocode(job.raw_address)
            lat, lng = point.lat, point.lng
            job.center_lat, job.center_lng = lat, lng
            await session.commit()

        # 2. Cache check / 3. Overpass query
        await _set_status(session, job, "querying")
        gh = area_geohash(lat, lng)
        cached = await _get_cached_area(session, gh, job.radius_m, cache_cat)
        if cached is not None:
            normalized = cached
        else:
            elements = await places.find_businesses(lat, lng, job.radius_m, osm_filters)
            normalized = normalize_many(elements)
            # Optional 2nd source (Geoapify) — merged in if a key is configured.
            if settings.geoapify_api_key:
                try:
                    geo = await GeoapifyClient().find_businesses(lat, lng, job.radius_m)
                    normalized.extend(geo)
                    logger.info("merged %d geoapify businesses", len(geo))
                except Exception as exc:  # noqa: BLE001 — never let a 2nd source break discovery
                    logger.warning("geoapify source failed: %s", exc)
            await _store_area_cache(session, gh, job.radius_m, cache_cat, normalized)

        # 4. Dedup + persist for this tenant
        count = await upsert_businesses(
            session, str(job.tenant_id), str(job.id), normalized
        )

        job.result_count = count
        await _set_status(session, job, "completed")
        logger.info("discovery job %s completed: %d businesses", job_id, count)

    except Exception as exc:  # noqa: BLE001 — record failure on the job
        logger.exception("discovery job %s failed", job_id)
        # The failing statement may have aborted the transaction; roll back and
        # re-fetch so the status update can commit (else the job hangs in 'querying').
        await session.rollback()
        job = (
            await session.execute(select(SearchJob).where(SearchJob.id == job_id))
        ).scalar_one_or_none()
        if job is not None:
            job.status = "failed"
            job.error = str(exc)[:1000]
            await session.commit()
