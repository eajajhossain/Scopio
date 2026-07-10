
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal, tenant_session
from app.core.security import parse_token


@dataclass
class Identity:
    tenant_id: str
    user_id: str


async def get_identity(authorization: str | None = Header(default=None)) -> Identity:
    if authorization and authorization.lower().startswith("bearer "):
        data = parse_token(authorization[7:].strip())
        if data:
            return Identity(tenant_id=data["tid"], user_id=data["uid"])
    # In production a valid token is required; in dev we fall back to the demo tenant.
    if settings.environment == "production":
        raise HTTPException(status_code=401, detail="Authentication required.")
    return Identity(tenant_id=settings.dev_tenant_id, user_id=settings.dev_user_id)


async def get_tenant_id(ident: Identity = Depends(get_identity)) -> str:
    return ident.tenant_id


async def get_user_id(ident: Identity = Depends(get_identity)) -> str:
    return ident.user_id


async def set_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Set the RLS tenant GUC on a session (session-level so it survives commits)."""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, false)"), {"tid": tenant_id}
    )
    await session.commit()


async def get_db(
    ident: Identity = Depends(get_identity),
) -> AsyncIterator[AsyncSession]:
    """Yield a session scoped to the caller's tenant (Postgres RLS enforces it).

    Uses a connection pinned with the tenant GUC so multi-commit handlers can't have
    a later statement land on a pooled connection without the tenant set.
    """
    async with tenant_session(ident.tenant_id) as session:
        yield session


async def get_raw_db() -> AsyncIterator[AsyncSession]:
    """Unscoped session for auth (tenant/app_user tables have no RLS)."""
    async with SessionLocal() as session:
        yield session


# --- Admin (platform-owner) access ------------------------------------------

def admin_email_set() -> set[str]:
    return {e.strip().lower() for e in (settings.admin_emails or "").split(",") if e.strip()}


def email_is_admin(email: str | None) -> bool:
    return bool(email and email.lower() in admin_email_set())


async def is_admin_identity(ident: Identity) -> bool:
    """True if this identity may use the admin dashboard.

    Dev convenience: the demo/dev user is admin outside production. Otherwise the
    caller's account email must be in ADMIN_EMAILS.
    """
    if settings.environment != "production" and ident.user_id == settings.dev_user_id:
        return True
    # Lazy import avoids a model import at module load.
    from app.models.user import AppUser

    try:
        uid = uuid.UUID(ident.user_id)
    except (ValueError, AttributeError):
        return False
    async with SessionLocal() as session:
        email = (
            await session.execute(select(AppUser.email).where(AppUser.id == uid))
        ).scalar_one_or_none()
    return email_is_admin(email)


async def require_admin(ident: Identity = Depends(get_identity)) -> Identity:
    """FastAPI dependency: 403 unless the caller is a platform admin."""
    if not await is_admin_identity(ident):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return ident
