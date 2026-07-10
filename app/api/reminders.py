import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_tenant_id, get_user_id
from app.schemas.reminder import (
    ReminderCreate,
    ReminderListOut,
    ReminderOut,
    ReminderUpdate,
)
from app.services.reminders.calendar_invite import build_ics
from app.services.reminders.service import (
    create_reminder,
    delete_reminder,
    get_reminder,
    list_reminders,
    update_reminder,
)

router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.post("", response_model=ReminderOut, status_code=status.HTTP_201_CREATED)
async def add_reminder(
    body: ReminderCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    return await create_reminder(
        db, tenant_id, user_id, str(body.business_id),
        due_at=body.due_at, channel=body.channel, note=body.note,
    )


@router.get("", response_model=ReminderListOut)
async def get_reminders(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(default=None, pattern="^(pending|done|cancelled)$"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Reminders for the current tenant, soonest-due first (RLS-scoped)."""
    rows, total = await list_reminders(db, status=status, limit=limit, offset=offset)
    return ReminderListOut(items=[ReminderOut.model_validate(r) for r in rows], total=total)


@router.patch("/{reminder_id}", response_model=ReminderOut)
async def edit_reminder(
    reminder_id: uuid.UUID,
    body: ReminderUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await update_reminder(
        db, str(reminder_id), due_at=body.due_at, note=body.note, status=body.status
    )


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_reminder(reminder_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await delete_reminder(db, str(reminder_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{reminder_id}/invite.ics")
async def reminder_invite(reminder_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Downloadable calendar invite — opening it adds the call to any device's calendar."""
    r = await get_reminder(db, str(reminder_id))
    ics = build_ics(
        summary=f"Call with {r.business_name or 'business'}",
        start=r.due_at,
        join_url=r.meeting_url,
        attendee_email=r.contact if r.contact and "@" in r.contact else None,
    )
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="scopio-call.ics"'},
    )
