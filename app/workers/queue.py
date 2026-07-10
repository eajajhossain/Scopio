"""Enqueue helper for the API side. Lazily creates and reuses one ARQ pool."""
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def enqueue_discovery(job_id: str, tenant_id: str) -> None:
    pool = await get_pool()
    await pool.enqueue_job("run_discovery_job", job_id, tenant_id)


async def enqueue_enrichment(tenant_id: str, job_id: str | None = None) -> None:
    pool = await get_pool()
    await pool.enqueue_job("run_enrichment_job", tenant_id, job_id)


async def enqueue_bulk_outreach(tenant_id: str, user_id: str, job_id: str) -> None:
    pool = await get_pool()
    await pool.enqueue_job("run_bulk_outreach_job", tenant_id, user_id, job_id)


async def enqueue_inbox_poll(tenant_id: str) -> None:
    """On-demand: poll one tenant's inbox now and let the agent answer new replies."""
    pool = await get_pool()
    await pool.enqueue_job("run_inbox_poll_tenant_job", tenant_id)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
