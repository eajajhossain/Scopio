"""Enrichment pipeline: for phone-less businesses that have a website, fetch the
site, extract contacts with the configured extractor, and write back what's missing.
"""
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business import Business
from app.models.search_job import SearchJob
from app.models.search_job_business import SearchJobBusiness
from app.services.enrichment.extractor import Extractor, get_extractor
from app.services.enrichment.fetcher import fetch_site_text
from app.services.enrichment.websearch import find_website

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentSummary:
    extractor: str
    candidates: int
    processed: int
    updated: int
    capped: bool


async def find_candidates(
    session: AsyncSession, job_id: str | None, limit: int
) -> list[Business]:
    """Businesses worth enriching.

    With web-search on, that's any business missing a contact OR a website (we'll try
    to find one). With it off, only those that already have a website to read.

    A business already enriched within the cooldown window is skipped, and never-tried
    businesses are returned first — so repeated Enrich clicks advance through the
    backlog instead of re-grinding the same alphabetically-first businesses (most of
    which permanently lack an email and so would otherwise never stop matching).
    """
    conds = [Business.deleted_at.is_(None)]
    if settings.enable_web_search:
        conds.append(
            or_(Business.phone.is_(None), Business.email.is_(None), Business.website.is_(None))
        )
    else:
        conds.append(Business.website.is_not(None))
        conds.append(or_(Business.phone.is_(None), Business.email.is_(None)))
    if settings.enrichment_recheck_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=settings.enrichment_recheck_days)
        conds.append(or_(Business.enriched_at.is_(None), Business.enriched_at < cutoff))
    stmt = select(Business).where(*conds)
    if job_id:
        stmt = stmt.join(
            SearchJobBusiness, SearchJobBusiness.business_id == Business.id
        ).where(SearchJobBusiness.search_job_id == job_id)
    # Never-tried first (NULLs), then stalest; name only as a tie-breaker.
    stmt = stmt.order_by(Business.enriched_at.asc().nulls_first(), Business.name).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def count_candidates(session: AsyncSession, job_id: str | None) -> int:
    candidates = await find_candidates(session, job_id, limit=10_000)
    return len(candidates)


def _deep_researcher():
    """Return the deep-agent research fn if it can run, else None (lazy import so a
    missing langgraph/tavily never breaks the website-read fallback)."""
    if not settings.enable_deep_research:
        return None
    try:
        from app.services.deepagent import deep_research_available, research_business
    except ImportError as exc:  # langgraph not installed
        logger.warning("deep agent unavailable (%s); using website-read enrichment", exc)
        return None
    return research_business if deep_research_available() else None


async def run_enrichment(
    session: AsyncSession,
    job_id: str | None,
    limit: int | None = None,
    extractor: Extractor | None = None,
) -> EnrichmentSummary:
    research = _deep_researcher()
    # The website-read extractor is only needed for the fallback (no Tavily) path.
    if research is None:
        extractor = extractor or get_extractor()
    cap = limit or settings.enrichment_max_businesses

    total = await count_candidates(session, job_id)
    capped = total > cap
    if capped:
        logger.info("enrichment capped: %d candidates, processing %d", total, cap)

    businesses = await find_candidates(session, job_id, limit=cap)

    # Locality hint for web search (e.g. "Barasat, 700125") from the job's address.
    locality = None
    if job_id:
        locality = (
            await session.execute(select(SearchJob.raw_address).where(SearchJob.id == job_id))
        ).scalar_one_or_none()

    engine_name = "deepagent" if research else (extractor.name if extractor else "none")
    processed = 0
    updated = 0
    searches = 0
    for biz in businesses:
        processed += 1
        if research is not None:
            # Deep agent: finds the site (if any), searches the web, reads the site.
            result = await research(biz.name, biz.category, locality, biz.website)
            found_site = result.details.pop("website", None)
            if found_site and not biz.website:
                biz.website = found_site
        else:
            # Fallback: find a website (Phase 4c) so we can read it, then extract.
            if not biz.website and settings.enable_web_search and searches < settings.web_search_max:
                searches += 1
                found = await find_website(biz.name, locality)
                if found:
                    biz.website = found
                    await session.commit()
            if not biz.website:
                biz.enriched_at = datetime.now(UTC)  # attempted; nothing to read
                await session.commit()
                continue
            text = await fetch_site_text(biz.website)
            result = await extractor.extract(biz.name, text)
        changed = False
        if result.phone and not biz.phone:
            biz.phone = result.phone
            changed = True
        if result.email and not biz.email:
            biz.email = result.email
            changed = True
        # Merge the rich profile (hours, description, socials, …) into details.
        if result.details:
            merged_details = dict(biz.details or {})
            merged_details.update(result.details)
            biz.details = merged_details
            changed = True
        if changed:
            biz.confidence = result.confidence
            updated += 1
        biz.enriched_at = datetime.now(UTC)
        await session.commit()

    logger.info(
        "enrichment done (%s): processed=%d updated=%d", engine_name, processed, updated
    )
    return EnrichmentSummary(
        extractor=engine_name,
        candidates=total,
        processed=processed,
        updated=updated,
        capped=capped,
    )
