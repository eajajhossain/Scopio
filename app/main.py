import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import admin, analytics, assistant, auth, businesses, outreach, reminders, search_jobs
from app.core.config import settings
from app.workers.queue import close_pool

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
_DEV_SECRET = "dev-secret-change-me-in-production"


async def _ensure_columns() -> None:
    """Idempotently add columns that post-date some existing databases.

    init.sql only runs on a fresh DB, so for an already-created database we add
    newer columns here (as the privileged admin role). Never blocks startup.
    """
    from sqlalchemy import text

    from app.core.db import admin_session

    try:
        async with admin_session() as db:
            await db.execute(
                text("ALTER TABLE app_user ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ")
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — a missing migration must not stop the app
        logger.warning("startup column check skipped: %s", exc)


async def _bootstrap_admin() -> None:
    """Ensure the admin login from .env (ADMIN_EMAIL/ADMIN_PASSWORD) exists.

    Makes `.env` the private source of truth for the admin account: created on first
    boot, and its password kept in sync on later boots. The password is hashed before
    it touches the database — never stored in plaintext. No-op if either var is unset.
    """
    import uuid

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.core.security import hash_password, verify_password
    from app.models.tenant import Tenant
    from app.models.user import AppUser

    email = settings.admin_email.strip().lower()
    password = settings.admin_password
    if not email or not password:
        return
    try:
        async with SessionLocal() as db:
            user = (
                await db.execute(select(AppUser).where(AppUser.email == email))
            ).scalar_one_or_none()
            if user is None:
                tenant = Tenant(id=uuid.uuid4(), name="Admin", company_name="Admin")
                db.add(tenant)
                await db.flush()
                db.add(AppUser(
                    id=uuid.uuid4(), tenant_id=tenant.id, email=email,
                    full_name="Admin", password_hash=hash_password(password), role="owner",
                ))
                await db.commit()
                logger.info("bootstrap admin created: %s", email)
            elif not verify_password(password, user.password_hash or ""):
                # Keep .env authoritative — sync the DB password to match.
                user.password_hash = hash_password(password)
                user.suspended_at = None   # never leave the owner locked out
                await db.commit()
                logger.info("bootstrap admin password synced from .env: %s", email)
    except Exception as exc:  # noqa: BLE001 — a bootstrap hiccup must not stop the app
        logger.warning("bootstrap admin skipped: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast in production if the signing secret was never changed.
    if settings.environment == "production" and settings.secret_key == _DEV_SECRET:
        raise RuntimeError("Set a strong SECRET_KEY env var before running in production.")
    if settings.secret_key == _DEV_SECRET:
        logger.warning("Using the default dev SECRET_KEY — set SECRET_KEY before deploying.")
    await _ensure_columns()
    await _bootstrap_admin()
    yield
    await close_pool()


app = FastAPI(
    title="Scopio API",
    version="0.1.0",
    description="AI Sales Outreach Platform — Phase 1: Discovery",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


app.include_router(auth.router)
app.include_router(search_jobs.router)
app.include_router(businesses.router)
app.include_router(reminders.router)
app.include_router(outreach.router)
app.include_router(analytics.router)
app.include_router(admin.router)
app.include_router(assistant.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}


# Dashboard at "/" (mounted last so it doesn't shadow API routes above).
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
