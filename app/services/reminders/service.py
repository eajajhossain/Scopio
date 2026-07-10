#Follow-up reminders: the AI (and the user) remember when to call each business.

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.phone import e164
from app.models.business import Business
from app.models.reminder import Reminder
from app.models.tenant import Tenant
from app.services.reminders.meeting_link import mint_meeting_url

logger = logging.getLogger(__name__)


async def tenant_tz(session: AsyncSession, tenant_id: str) -> str:
    tz = (
        await session.execute(select(Tenant.timezone).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    return tz or "Asia/Kolkata"


def due_in_days(days: int | None, tz_name: str) -> datetime:
    """A callback date `days` from now (default if None), set to the local default hour."""
    n = days if isinstance(days, int) and days > 0 else settings.reminder_default_days
    tz = ZoneInfo(tz_name)
    local_day = (datetime.now(tz) + timedelta(days=n)).date()
    local_dt = datetime.combine(local_day, time(hour=settings.reminder_default_hour), tzinfo=tz)
    return local_dt.astimezone(UTC)


async def _get_business(session: AsyncSession, business_id: str) -> Business:
    biz = (
        await session.execute(select(Business).where(Business.id == business_id))
    ).scalar_one_or_none()
    if biz is None:
        raise HTTPException(status_code=404, detail="business not found")
    return biz


async def create_reminder(
    session: AsyncSession,
    tenant_id: str,
    user_id: str | None,
    business_id: str,
    due_at: datetime,
    channel: str | None = None,
    note: str | None = None,
    meeting_url: str | None = None,
    commit: bool = True,
) -> Reminder:
    biz = await _get_business(session, business_id)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    tz_name = await tenant_tz(session, tenant_id)
    # Store the phone in full international form so the call link reaches any country.
    contact = e164(biz.phone, settings.phone_default_region) or biz.phone or biz.email
    if channel is None:
        channel = "whatsapp" if biz.phone else ("email" if biz.email else "call")

    reminder = Reminder(
        tenant_id=tenant_id,
        business_id=business_id,
        created_by=user_id,
        due_at=due_at,
        timezone=tz_name,
        channel=channel,
        business_name=biz.name,
        contact=contact,
        meeting_url=meeting_url or mint_meeting_url(),  # both sides join this room
        note=note,
        status="pending",
    )
    session.add(reminder)
    if biz.status in ("discovered", "contacted", "interested"):
        biz.status = "callback_scheduled"   # CRM write-back
    if commit:
        await session.commit()
        await session.refresh(reminder)
    else:
        await session.flush()
    logger.info("created reminder %s for business %s due %s", reminder.id, business_id, due_at)
    return reminder


async def list_reminders(
    session: AsyncSession, status: str | None = None, limit: int = 100, offset: int = 0
) -> tuple[list[Reminder], int]:
    from sqlalchemy import func

    filters = []
    if status:
        filters.append(Reminder.status == status)
    total = (
        await session.execute(
            select(func.count()).select_from(select(Reminder).where(*filters).subquery())
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(Reminder).where(*filters).order_by(Reminder.due_at).limit(limit).offset(offset)
        )
    ).scalars().all()
    return list(rows), total


async def get_reminder(session: AsyncSession, reminder_id: str) -> Reminder:
    r = (
        await session.execute(select(Reminder).where(Reminder.id == reminder_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="reminder not found")
    return r


_get_reminder = get_reminder  # backward-compatible internal alias


async def update_reminder(
    session: AsyncSession, reminder_id: str,
    due_at: datetime | None = None, note: str | None = None, status: str | None = None,
) -> Reminder:
    r = await _get_reminder(session, reminder_id)
    if due_at is not None:
        r.due_at = due_at if due_at.tzinfo else due_at.replace(tzinfo=UTC)
    if note is not None:
        r.note = note
    if status is not None:
        r.status = status
    await session.commit()
    await session.refresh(r)
    return r


async def delete_reminder(session: AsyncSession, reminder_id: str) -> None:
    r = await _get_reminder(session, reminder_id)
    await session.delete(r)
    await session.commit()
