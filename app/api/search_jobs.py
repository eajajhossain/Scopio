import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db, get_tenant_id, get_user_id
from app.models.business import Business
from app.models.search_job import SearchJob
from app.models.search_job_business import SearchJobBusiness
from app.models.tenant import Tenant
from app.schemas.business import BusinessListOut, BusinessOut
from app.schemas.search_job import ExtensionImportIn, SearchJobCreate, SearchJobOut
from app.services.discovery.dedup import dedup_key
from app.services.discovery.pipeline import upsert_businesses
from app.services.enrichment.pipeline import count_candidates
from app.services.importer.csv_import import import_csv
from app.services.outreach.service import count_bulk_candidates, whatsapp_queue
from app.services.targeting import derive_target_profile
from app.workers.queue import enqueue_bulk_outreach, enqueue_discovery, enqueue_enrichment

router = APIRouter(prefix="/search_jobs", tags=["search_jobs"])


@router.post("", response_model=SearchJobOut, status_code=status.HTTP_202_ACCEPTED)
async def create_search_job(
    body: SearchJobCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    has_gps = body.lat is not None and body.lng is not None
    if not body.raw_address and not has_gps:
        raise HTTPException(
            status_code=422, detail="Provide an address or use your location (GPS)."
        )
    # A human-friendly label for the GPS case (DB requires raw_address).
    raw_address = body.raw_address or f"📍 My location ({body.lat:.4f}, {body.lng:.4f})"

    # Context-aware targeting: read the owner's services and decide which kinds of
    # businesses to search for. Empty profile → broad search (today's behaviour).
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    profile = await derive_target_profile(
        tenant.services if tenant else "", tenant.company_name if tenant else None
    )

    job = SearchJob(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        created_by=uuid.UUID(user_id),
        raw_address=raw_address,
        center_lat=body.lat if has_gps else None,
        center_lng=body.lng if has_gps else None,
        radius_m=body.radius_m,
        category=body.category,
        target_profile=profile.to_dict() if not profile.is_empty else None,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    await enqueue_discovery(str(job.id), tenant_id)
    return job


_ALLOWED_SOURCES = {"google_places", "geoapify", "osm", "manual_import"}


@router.post("/import_leads", response_model=SearchJobOut, status_code=status.HTTP_201_CREATED)
async def import_leads(
    body: ExtensionImportIn,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    """Receive leads captured by the browser extension and store them as a completed search."""
    job = SearchJob(
        id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), created_by=uuid.UUID(user_id),
        raw_address=body.label, status="completed",
    )
    db.add(job)
    await db.flush()

    src = body.source if body.source in _ALLOWED_SOURCES else "manual_import"
    rows = [
        {
            "source": src, "name": b.name, "category": b.category, "address": b.address,
            "lat": b.lat, "lng": b.lng, "phone": b.phone, "email": b.email,
            "website": b.website, "dedup_key": dedup_key(b.name, b.lat, b.lng),
        }
        for b in body.businesses if b.name
    ]
    count = await upsert_businesses(db, tenant_id, str(job.id), rows)
    job.result_count = count
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/{job_id}", response_model=SearchJobOut)
async def get_search_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = (
        await db.execute(select(SearchJob).where(SearchJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="search_job not found")
    return job


@router.get("/{job_id}/businesses", response_model=BusinessListOut)
async def list_job_businesses(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    category: str | None = None,
    contactable: bool = False,
):
    filters = [
        SearchJobBusiness.search_job_id == job_id,
        Business.deleted_at.is_(None),
    ]
    if category:
        filters.append(Business.category == category)
    if contactable:  # only businesses the user can actually reach
        filters.append(or_(Business.phone.is_not(None), Business.email.is_not(None)))

    base = (
        select(Business)
        .join(SearchJobBusiness, SearchJobBusiness.business_id == Business.id)
        .where(*filters)
    )
    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(base.order_by(Business.name).limit(limit).offset(offset))
    ).scalars().all()
    return BusinessListOut(items=[BusinessOut.model_validate(r) for r in rows], total=total)


@router.post("/{job_id}/import")
async def import_businesses_csv(
    job_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    raw = await file.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="file must be UTF-8 CSV") from None
    result = await import_csv(db, tenant_id, str(job_id), content)
    return {
        "imported": result.imported,
        "skipped": result.skipped,
        "errors": result.errors,
    }


@router.post("/{job_id}/enrich")
async def enrich_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """Queue deep AI research for this job's businesses that are missing contact info."""
    candidates = await count_candidates(db, str(job_id))
    if candidates == 0:
        return {"queued": 0, "candidates": 0,
                "message": "Every business here already has contact info — nothing to research."}
    await enqueue_enrichment(tenant_id, str(job_id))
    return {
        "queued": min(candidates, settings.enrichment_max_businesses),
        "candidates": candidates,
    }


@router.post("/{job_id}/outreach_all")
async def outreach_all(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    """One click: AI-message every business in this job that has a contact (skips the rest).

    Emails auto-send when the account's email is connected; WhatsApp-only businesses are
    drafted for tap-to-send.
    """
    candidates = await count_bulk_candidates(db, str(job_id))
    if candidates == 0:
        return {"queued": 0, "candidates": 0,
                "message": "No un-contacted businesses with a phone/email here. "
                           "Try ✨ Enrich first, or search a city for more contactable leads."}
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    email_connected = bool(tenant and tenant.smtp_email and tenant.smtp_password)
    await enqueue_bulk_outreach(tenant_id, user_id, str(job_id))
    return {
        "queued": min(candidates, settings.outreach_bulk_max),
        "candidates": candidates,
        "email_connected": email_connected,
        # 'review' = messages land in Drafts for approval; 'autonomous' = emails send directly.
        "outreach_mode": (getattr(tenant, "outreach_mode", None) or "review"),
    }


@router.get("/{job_id}/whatsapp_queue")
async def whatsapp_send_queue(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    """List WhatsApp-able businesses with pre-filled wa.me links (tap-through send queue)."""
    items = await whatsapp_queue(db, tenant_id, user_id, str(job_id))
    return {"items": items, "total": len(items)}
