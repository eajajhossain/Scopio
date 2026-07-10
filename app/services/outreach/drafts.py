"""Human-in-the-loop outreach: queue AI-written messages for user approval.

When a tenant's `outreach_mode` is 'review' (the default for new accounts), the
AI never emails a business directly — every message lands here first, and the
user approves (optionally after editing), or discards, each one from the
dashboard. Approval performs exactly what the autonomous path would have done:
send over SMTP + record the conversation + apply intent/reminder side-effects.
"""
import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.conversation import Conversation
from app.models.draft import OutreachDraft
from app.models.tenant import Tenant
from app.services.outreach.channels import send_email
from app.services.outreach.outcome import apply_reply_outcome

logger = logging.getLogger(__name__)


def review_mode(tenant: Tenant | None) -> bool:
    """True when this tenant wants drafts reviewed before sending (HITL default)."""
    return (getattr(tenant, "outreach_mode", None) or "review") == "review"


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def queue_draft(
    session: AsyncSession,
    *,
    tenant_id: str,
    business_id: str,
    kind: str,
    channel: str,
    to_contact: str,
    subject: str | None,
    body: str,
    conversation_id: str | None = None,
    meta: dict | None = None,
    commit: bool = True,
) -> OutreachDraft:
    draft = OutreachDraft(
        tenant_id=tenant_id, business_id=business_id, conversation_id=conversation_id,
        kind=kind, channel=channel, to_contact=to_contact, subject=subject,
        body=body, meta=meta,
    )
    session.add(draft)
    if commit:
        await session.commit()
        await session.refresh(draft)
    return draft


async def list_drafts(session: AsyncSession, status: str = "pending") -> list[dict]:
    rows = (
        await session.execute(
            select(OutreachDraft, Business.name)
            .join(Business, Business.id == OutreachDraft.business_id)
            .where(OutreachDraft.status == status)
            .order_by(OutreachDraft.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(d.id), "business_id": str(d.business_id),
            "business_name": name, "kind": d.kind, "channel": d.channel,
            "to_contact": d.to_contact, "subject": d.subject, "body": d.body,
            "status": d.status, "created_at": d.created_at.isoformat(),
        }
        for d, name in rows
    ]


async def _get_pending(session: AsyncSession, draft_id: str) -> OutreachDraft:
    draft = (
        await session.execute(select(OutreachDraft).where(OutreachDraft.id == draft_id))
    ).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="draft not found")
    if draft.status != "pending":
        raise HTTPException(status_code=409, detail=f"draft already {draft.status}")
    return draft


async def update_body(session: AsyncSession, draft_id: str, body: str) -> OutreachDraft:
    draft = await _get_pending(session, draft_id)
    draft.body = body
    await session.commit()
    await session.refresh(draft)
    return draft


async def discard(session: AsyncSession, draft_id: str) -> None:
    draft = await _get_pending(session, draft_id)
    draft.status = "discarded"
    await session.commit()


async def approve(session: AsyncSession, tenant_id: str, draft_id: str) -> dict:
    """Send the (possibly edited) draft and perform the recording/side-effects the
    autonomous path would have done at send time."""
    draft = await _get_pending(session, draft_id)
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not tenant or not tenant.smtp_email or not tenant.smtp_password:
        raise HTTPException(status_code=400, detail="Connect your email first to send.")
    biz = (
        await session.execute(select(Business).where(Business.id == draft.business_id))
    ).scalar_one_or_none()
    if biz is None:
        raise HTTPException(status_code=404, detail="business no longer exists")

    body = draft.body
    if draft.kind == "reply":
        conv = (
            await session.execute(
                select(Conversation).where(Conversation.id == draft.conversation_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation no longer exists")
        # Reminder/intent side-effects happen NOW (on approval), not at draft time.
        body += await apply_reply_outcome(session, tenant_id, conv, biz, draft.meta or {})

    try:
        await send_email(
            host=tenant.smtp_host or "smtp.gmail.com", port=tenant.smtp_port or 587,
            sender=tenant.smtp_email, password=tenant.smtp_password_plain(),
            to=draft.to_contact, subject=draft.subject or f"A quick idea for {biz.name}",
            body=body,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Email send failed: {exc}") from exc

    if draft.kind == "reply":
        transcript = list(conv.transcript or [])
        transcript.append({"role": "assistant", "text": body, "ts": _now()})
        conv.transcript = transcript
        conv.updated_at = datetime.now(UTC)
    else:
        session.add(Conversation(
            tenant_id=tenant_id, business_id=biz.id, channel=draft.channel, status="active",
            transcript=[{"role": "assistant", "text": body, "ts": _now()}],
        ))
        if biz.status == "discovered":
            biz.status = "contacted"

    draft.status = "sent"
    await session.commit()
    logger.info("draft %s approved and sent to %s", draft_id, draft.to_contact)
    return {"sent": True, "to": draft.to_contact}
