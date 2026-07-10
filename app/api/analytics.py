from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.models.business import Business
from app.models.conversation import Conversation
from app.models.reminder import Reminder

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _count(db: AsyncSession, stmt) -> int:
    return (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()


async def _group_counts(db: AsyncSession, column, *filters) -> dict:
    rows = (await db.execute(select(column, func.count()).where(*filters).group_by(column))).all()
    return {str(k): v for k, v in rows}


@router.get("")
async def analytics(db: AsyncSession = Depends(get_db)):
    """Funnel + key metrics for the current tenant (RLS-scoped)."""
    active = [Business.deleted_at.is_(None)]
    businesses = await _count(db, select(Business.id).where(*active))
    contactable = await _count(
        db, select(Business.id).where(*active, or_(Business.phone.is_not(None), Business.email.is_not(None)))
    )
    enriched = await _count(db, select(Business.id).where(*active, Business.enriched_at.is_not(None)))

    status = await _group_counts(db, Business.status, *active)
    # Count both the new "callback_scheduled" and legacy "meeting_booked" as a secured callback.
    s = lambda *keys: sum(status.get(k, 0) for k in keys)  # noqa: E731
    funnel = {
        "discovered": businesses,
        "contactable": contactable,
        "contacted": s("contacted", "interested", "callback_scheduled", "meeting_booked"),
        "interested": s("interested", "callback_scheduled", "meeting_booked"),
        "callbacks": s("callback_scheduled", "meeting_booked"),
    }

    conv_total = await _count(db, select(Conversation.id))
    conv_channel = await _group_counts(db, Conversation.channel)
    conv_status = await _group_counts(db, Conversation.status)

    reminders_total = await _count(db, select(Reminder.id))
    reminders_pending = await _count(db, select(Reminder.id).where(Reminder.status == "pending"))

    # Simple conversion rate: callbacks secured / businesses contacted.
    callbacks = funnel["callbacks"]
    contacted = funnel["contacted"]
    conv_rate = round(100 * callbacks / contacted, 1) if contacted else 0.0

    return {
        "businesses": businesses,
        "contactable": contactable,
        "enriched": enriched,
        "funnel": funnel,
        "lead_status": status,
        "conversations": {"total": conv_total, "by_channel": conv_channel, "by_status": conv_status},
        "reminders": {"total": reminders_total, "pending": reminders_pending},
        "conversion_rate": conv_rate,
    }
