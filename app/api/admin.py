"""Platform-admin dashboard API — cross-tenant visibility for the owner.

Every route is gated by `require_admin` and reads through the privileged `admin_session`
(superuser → bypasses Row-Level Security), so it can see all tenants at once. Normal,
tenant-scoped routes are unaffected.
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.core.db import admin_session
from app.core.deps import Identity, email_is_admin, require_admin
from app.models.business import Business
from app.models.conversation import Conversation
from app.models.reminder import Reminder
from app.models.search_job import SearchJob
from app.models.tenant import Tenant
from app.models.user import AppUser

router = APIRouter(prefix="/admin", tags=["admin"])


def can_moderate(target_email: str | None) -> bool:
    """An admin may moderate anyone EXCEPT another admin (incl. themselves) — this
    stops an admin from locking the owner out of the admin dashboard."""
    return not email_is_admin(target_email)


@router.get("/overview")
async def overview(_: Identity = Depends(require_admin)) -> dict:
    """Headline totals across the whole platform."""
    async with admin_session() as db:
        async def _count(model) -> int:
            return (await db.execute(select(func.count()).select_from(model))).scalar_one()

        return {
            "tenants": await _count(Tenant),
            "users": await _count(AppUser),
            "businesses": await _count(Business),
            "searches": await _count(SearchJob),
            "conversations": await _count(Conversation),
            "reminders": await _count(Reminder),
        }


@router.get("/users")
async def users(_: Identity = Depends(require_admin)) -> dict:
    """Every account: identity, company, login activity, and search count."""
    async with admin_session() as db:
        searches = (
            select(SearchJob.created_by, func.count().label("n"))
            .group_by(SearchJob.created_by)
            .subquery()
        )
        rows = (
            await db.execute(
                select(
                    AppUser.id, AppUser.email, AppUser.full_name, AppUser.role,
                    AppUser.created_at, AppUser.last_login_at, AppUser.login_count,
                    AppUser.suspended_at,
                    Tenant.company_name, func.coalesce(searches.c.n, 0).label("searches"),
                )
                .join(Tenant, Tenant.id == AppUser.tenant_id)
                .join(searches, searches.c.created_by == AppUser.id, isouter=True)
                .order_by(AppUser.last_login_at.desc().nulls_last(), AppUser.created_at.desc())
            )
        ).all()
    return {
        "items": [
            {
                "id": str(r.id), "email": r.email, "name": r.full_name, "role": r.role,
                "company": r.company_name,
                "joined": r.created_at.isoformat() if r.created_at else None,
                "last_login": r.last_login_at.isoformat() if r.last_login_at else None,
                "login_count": r.login_count, "searches": r.searches,
                "suspended": r.suspended_at is not None,
                # an admin account can't be moderated — the UI hides its buttons
                "can_moderate": can_moderate(r.email),
            }
            for r in rows
        ],
        "total": len(rows),
    }


async def _set_suspended(user_id: uuid.UUID, suspend: bool) -> dict:
    """Suspend (block login) or reactivate an account. Admin accounts are protected."""
    async with admin_session() as db:
        user = (
            await db.execute(select(AppUser).where(AppUser.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        if suspend and not can_moderate(user.email):
            raise HTTPException(status_code=400, detail="Admin accounts can't be suspended.")
        user.suspended_at = datetime.now(UTC) if suspend else None
        await db.commit()
        return {"id": str(user.id), "email": user.email,
                "suspended": user.suspended_at is not None}


@router.post("/users/{user_id}/suspend")
async def suspend_user(user_id: uuid.UUID, _: Identity = Depends(require_admin)) -> dict:
    """Block an account from logging in (reversible)."""
    return await _set_suspended(user_id, True)


@router.post("/users/{user_id}/reactivate")
async def reactivate_user(user_id: uuid.UUID, _: Identity = Depends(require_admin)) -> dict:
    """Lift a suspension — the account can log in again."""
    return await _set_suspended(user_id, False)


@router.get("/searches")
async def searches(
    _: Identity = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """Recent searches across all tenants: who, where, what they targeted, results, when."""
    async with admin_session() as db:
        rows = (
            await db.execute(
                select(
                    SearchJob.raw_address, SearchJob.center_lat, SearchJob.center_lng,
                    SearchJob.radius_m, SearchJob.target_profile, SearchJob.status,
                    SearchJob.result_count, SearchJob.created_at,
                    AppUser.email, Tenant.company_name,
                )
                .join(Tenant, Tenant.id == SearchJob.tenant_id, isouter=True)
                .join(AppUser, AppUser.id == SearchJob.created_by, isouter=True)
                .order_by(SearchJob.created_at.desc())
                .limit(limit)
            )
        ).all()
    return {
        "items": [
            {
                "location": r.raw_address,
                "lat": r.center_lat, "lng": r.center_lng, "radius_m": r.radius_m,
                "targeting": (r.target_profile or {}).get("target_business_types") or [],
                "status": r.status, "results": r.result_count,
                "when": r.created_at.isoformat() if r.created_at else None,
                "by": r.email, "company": r.company_name,
            }
            for r in rows
        ],
        "total": len(rows),
    }
