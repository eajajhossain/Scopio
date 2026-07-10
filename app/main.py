import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import admin, analytics, auth, businesses, outreach, reminders, search_jobs
from app.core.config import settings
from app.workers.queue import close_pool

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
_DEV_SECRET = "dev-secret-change-me-in-production"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast in production if the signing secret was never changed.
    if settings.environment == "production" and settings.secret_key == _DEV_SECRET:
        raise RuntimeError("Set a strong SECRET_KEY env var before running in production.")
    if settings.secret_key == _DEV_SECRET:
        logger.warning("Using the default dev SECRET_KEY — set SECRET_KEY before deploying.")
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


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}


# Dashboard at "/" (mounted last so it doesn't shadow API routes above).
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
