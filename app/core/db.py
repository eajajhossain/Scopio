from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Privileged engine for the admin dashboard only: connects as the DB superuser, so it
# sees every tenant's rows (bypasses Row-Level Security). Used exclusively behind the
# require_admin gate — never for normal, tenant-scoped requests.
admin_engine = create_async_engine(settings.admin_database_url, pool_pre_ping=True)
AdminSessionLocal = async_sessionmaker(admin_engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session and closes it afterward."""
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def admin_session() -> AsyncIterator[AsyncSession]:
    """A privileged (RLS-bypassing) session for admin cross-tenant reads."""
    async with AdminSessionLocal() as session:
        yield session


@asynccontextmanager
async def tenant_session(tenant_id: str) -> AsyncIterator[AsyncSession]:
    """A session pinned to ONE connection that carries the RLS tenant GUC.

    The GUC is connection-scoped (set_config is_local=false) and committed, so it
    survives transaction boundaries. By binding the session to a single checked-out
    connection, every commit in a multi-step pipeline stays on that same connection
    and keeps the tenant set. A plain pooled session loses this: after the first
    commit the connection returns to the pool and a later statement can run on a
    different connection that never had the tenant set, so RLS hides the tenant's
    own rows (e.g. discovery's 'search_job not found').
    """
    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tid, false)"), {"tid": tenant_id}
        )
        await conn.commit()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
