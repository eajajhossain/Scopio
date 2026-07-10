
import logging

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.db import tenant_session
from app.services.discovery.pipeline import run_discovery
from app.services.enrichment.pipeline import run_enrichment
from app.services.inbox.service import poll_all_inboxes, poll_one_tenant
from app.services.outreach.service import bulk_outreach

logging.basicConfig(level=logging.INFO)


async def run_discovery_job(ctx: dict, job_id: str, tenant_id: str) -> None:
    """Queue task: scope a session to the tenant, then run the discovery pipeline."""
    async with tenant_session(tenant_id) as session:
        await run_discovery(session, job_id)


async def run_enrichment_job(
    ctx: dict, tenant_id: str, job_id: str | None = None, limit: int | None = None
) -> None:
    """Queue task: scope a session to the tenant, then enrich phone-less businesses."""
    async with tenant_session(tenant_id) as session:
        await run_enrichment(session, job_id, limit)


async def run_bulk_outreach_job(
    ctx: dict, tenant_id: str, user_id: str, job_id: str
) -> None:
    """Queue task: message every contactable, not-yet-contacted business in a job."""
    async with tenant_session(tenant_id) as session:
        await bulk_outreach(session, tenant_id, user_id, job_id)


async def run_inbox_poll_job(ctx: dict) -> None:
    """Cron task: poll every tenant's inbox and let the agent auto-reply to new replies."""
    await poll_all_inboxes()


async def run_inbox_poll_tenant_job(ctx: dict, tenant_id: str) -> None:
    """On-demand: poll a single tenant's inbox now (triggered from the dashboard)."""
    await poll_one_tenant(tenant_id)


class WorkerSettings:
    functions = [
        run_discovery_job, run_enrichment_job, run_bulk_outreach_job,
        run_inbox_poll_job, run_inbox_poll_tenant_job,
    ]
    # Every 2 minutes: check all connected inboxes so the agent answers replies hands-free.
    cron_jobs = [cron(run_inbox_poll_job, minute=set(range(0, 60, 2)), run_at_startup=False)]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # A deep-research batch runs several web searches per business, so a batch can run for
    # many minutes. Without this, ARQ's 300s default would cancel it mid-batch.
    job_timeout = 3600
