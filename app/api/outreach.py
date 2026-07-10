import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_tenant_id, get_user_id  # noqa: F401
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.schemas.outreach import (
    ConversationListOut,
    ConversationOut,
    ReplyIn,
    StartOutreach,
)
from app.services.outreach import drafts as drafts_service
from app.services.outreach.service import (
    contact_link,
    handle_reply,
    send_message,
    start_conversation,
)
from app.workers.queue import enqueue_inbox_poll

router = APIRouter(prefix="/outreach", tags=["outreach"])


@router.post("/start", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def start(
    body: StartOutreach,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    return await start_conversation(db, tenant_id, user_id, str(body.business_id), body.channel)


@router.post("/send/{business_id}")
async def send(
    business_id: uuid.UUID,
    channel: str = Query(default="whatsapp", pattern="^(whatsapp|email)$"),
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    """Deliver the AI message: email auto-sends if connected; WhatsApp returns a tap link."""
    return await send_message(db, tenant_id, user_id, str(business_id), channel)


@router.get("/contact_link/{business_id}")
async def get_contact_link(
    business_id: uuid.UUID,
    channel: str = Query(default="whatsapp", pattern="^(whatsapp|email)$"),
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    """Return a click-to-send link (wa.me / mailto) with the AI message pre-filled."""
    return await contact_link(db, tenant_id, user_id, str(business_id), channel)


@router.post("/conversations/{conversation_id}/reply", response_model=ConversationOut)
async def reply(
    conversation_id: uuid.UUID,
    body: ReplyIn,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
):
    return await handle_reply(db, tenant_id, user_id, str(conversation_id), body.message)


@router.post("/poll_inbox")
async def poll_inbox(tenant_id: str = Depends(get_tenant_id)):
    """Trigger an immediate inbox check so the agent answers any new customer replies.
    (Also runs automatically every 2 minutes via the worker cron.)"""
    await enqueue_inbox_poll(tenant_id)
    return {"queued": True}


# --- Human-in-the-loop drafts (review mode) ---------------------------------

class DraftBodyIn(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class OutreachModeIn(BaseModel):
    mode: str = Field(pattern="^(review|autonomous)$")


@router.get("/drafts")
async def list_drafts(
    db: AsyncSession = Depends(get_db),
    status_filter: str = Query(default="pending", alias="status",
                               pattern="^(pending|sent|discarded)$"),
):
    """AI-written messages awaiting approval (HITL review mode)."""
    items = await drafts_service.list_drafts(db, status_filter)
    return {"items": items, "total": len(items)}


@router.patch("/drafts/{draft_id}")
async def edit_draft(
    draft_id: uuid.UUID, body: DraftBodyIn, db: AsyncSession = Depends(get_db)
):
    """Edit the message text before approving."""
    draft = await drafts_service.update_body(db, str(draft_id), body.body)
    return {"id": str(draft.id), "body": draft.body}


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """Send the draft (as edited) and record the conversation/side-effects."""
    return await drafts_service.approve(db, tenant_id, str(draft_id))


@router.post("/drafts/{draft_id}/discard")
async def discard_draft(draft_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await drafts_service.discard(db, str(draft_id))
    return {"discarded": True}


@router.get("/mode")
async def get_mode(
    db: AsyncSession = Depends(get_db), tenant_id: str = Depends(get_tenant_id)
):
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    return {"mode": (getattr(tenant, "outreach_mode", None) or "review")}


@router.patch("/mode")
async def set_mode(
    body: OutreachModeIn,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """Switch between 'review' (approve each message) and 'autonomous' (AI sends)."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    tenant.outreach_mode = body.mode
    await db.commit()
    return {"mode": body.mode}


@router.get("/conversations", response_model=ConversationListOut)
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    total = (await db.execute(select(func.count()).select_from(Conversation))).scalar_one()
    rows = (
        await db.execute(
            select(Conversation).order_by(Conversation.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return ConversationListOut(
        items=[ConversationOut.model_validate(r) for r in rows], total=total
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(conversation_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    conv = (
        await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv
