import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    Identity,
    email_is_admin,
    get_identity,
    get_raw_db,
    is_admin_identity,
)
from app.core.ratelimit import rate_limit
from app.core.security import encrypt_secret, hash_password, make_token, verify_password
from app.models.tenant import Tenant
from app.models.user import AppUser
from app.schemas.auth import (
    AuthOut,
    ConnectEmailIn,
    LoginIn,
    ProfileIn,
    RegisterIn,
    UserOut,
)
from app.services.outreach.channels import verify_smtp

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(user: AppUser, tenant: Tenant, is_admin: bool | None = None) -> UserOut:
    return UserOut(
        id=user.id, email=user.email, name=user.full_name,
        company_name=tenant.company_name, services=tenant.services,
        email_connected=bool(tenant.smtp_email and tenant.smtp_password),
        is_admin=email_is_admin(user.email) if is_admin is None else is_admin,
        last_login_at=user.last_login_at,
    )


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit("register"))])
async def register(body: RegisterIn, db: AsyncSession = Depends(get_raw_db)):
    email = body.email.strip().lower()
    exists = (
        await db.execute(select(AppUser).where(AppUser.email == email))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    tenant = Tenant(
        id=uuid.uuid4(), name=body.company_name,
        company_name=body.company_name, services=body.services,
    )
    db.add(tenant)
    await db.flush()  # get tenant.id

    user = AppUser(
        id=uuid.uuid4(), tenant_id=tenant.id, email=email,
        full_name=body.name, password_hash=hash_password(body.password), role="owner",
    )
    db.add(user)
    await db.commit()

    token = make_token(str(user.id), str(tenant.id))
    return AuthOut(token=token, user=_user_out(user, tenant))


@router.post("/login", response_model=AuthOut,
             dependencies=[Depends(rate_limit("login"))])
async def login(body: LoginIn, db: AsyncSession = Depends(get_raw_db)):
    email = body.email.strip().lower()
    user = (
        await db.execute(select(AppUser).where(AppUser.email == email))
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one()
    # Record the login so the admin dashboard can show who signed in and when.
    user.last_login_at = datetime.now(UTC)
    user.login_count = (user.login_count or 0) + 1
    await db.commit()
    return AuthOut(token=make_token(str(user.id), str(tenant.id)), user=_user_out(user, tenant))


@router.get("/me", response_model=UserOut)
async def me(ident: Identity = Depends(get_identity), db: AsyncSession = Depends(get_raw_db)):
    user = (
        await db.execute(select(AppUser).where(AppUser.id == ident.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    return _user_out(user, tenant, is_admin=await is_admin_identity(ident))


@router.patch("/profile", response_model=UserOut)
async def update_profile(
    body: ProfileIn,
    ident: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_raw_db),
):
    user = (
        await db.execute(select(AppUser).where(AppUser.id == ident.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    if body.name is not None:
        user.full_name = body.name
    if body.company_name is not None:
        tenant.company_name = body.company_name
    if body.services is not None:
        tenant.services = body.services
    await db.commit()
    return _user_out(user, tenant)


@router.post("/connect_email", response_model=UserOut,
             dependencies=[Depends(rate_limit("connect_email"))])
async def connect_email(
    body: ConnectEmailIn,
    ident: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_raw_db),
):
    """Validate SMTP credentials (e.g. Gmail app password) and store them for auto-send."""
    # Gmail shows app passwords as "abcd efgh ijkl mnop" — pasted spaces break login.
    app_password = body.app_password.replace(" ", "")
    try:
        await verify_smtp(host=body.host, port=body.port,
                          sender=body.email.strip(), password=app_password)
    except Exception as exc:  # noqa: BLE001
        # Keep the raw SMTP error out of the UI; give actionable guidance instead.
        logger.warning("SMTP verification failed for %s: %s", body.email, exc)
        raise HTTPException(
            status_code=400,
            detail="Couldn’t connect to your email. Double-check the address and password. "
                   "For Gmail, enable 2-Step Verification and use a 16-character App Password.",
        ) from exc
    user = (
        await db.execute(select(AppUser).where(AppUser.id == ident.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    tenant.smtp_email = body.email.strip()
    tenant.smtp_password = encrypt_secret(app_password)  # encrypted at rest
    tenant.smtp_host = body.host
    tenant.smtp_port = body.port
    await db.commit()
    return _user_out(user, tenant)
