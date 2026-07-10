"""Autonomous inbound-email loop: for each tenant with a connected inbox, pull new
customer replies, let the AI agent respond, and send the reply back over SMTP.

Reuses the existing conversation brain (`outreach.agent.respond`), sender-identity, and
reminder logic — this module only adds *ingestion* (IMAP) and *actually sending* the
agent's reply (the interactive flow records via a preview channel; here we deliver).
"""
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal, tenant_session
from app.models.business import Business
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.services.inbox.imap_client import InboundEmail, fetch_unseen
from app.services.outreach import agent, drafts
from app.services.outreach.channels import send_email
from app.services.outreach.outcome import apply_reply_outcome
from app.services.outreach.service import _business_info, _now, _sender_context

logger = logging.getLogger(__name__)

# Conversation statuses where we stop auto-replying (the lead is done or opted out).
_TERMINAL = {"not_interested", "closed"}


def imap_host_for(smtp_host: str | None) -> str:
    """Derive the IMAP host from the configured SMTP host (smtp.x → imap.x)."""
    if smtp_host and smtp_host.startswith("smtp."):
        return "imap." + smtp_host[len("smtp."):]
    return smtp_host or "imap.gmail.com"


async def poll_all_inboxes() -> int:
    """Poll every tenant that has connected email. Returns total replies handled."""
    if not settings.inbox_poll_enabled:
        return 0
    async with SessionLocal() as session:  # unscoped: the tenant table has no RLS
        tenants = (
            await session.execute(
                select(Tenant.id).where(
                    Tenant.smtp_email.is_not(None), Tenant.smtp_password.is_not(None)
                )
            )
        ).scalars().all()
    total = 0
    for tid in tenants:
        try:
            total += await poll_one_tenant(str(tid))
        except Exception as exc:  # noqa: BLE001 — one bad inbox mustn't stop the rest
            logger.warning("inbox poll failed for tenant %s: %s", tid, exc)
    if total:
        logger.info("inbox poll handled %d reply(ies) across %d tenant(s)", total, len(tenants))
    return total


async def poll_one_tenant(tenant_id: str) -> int:
    """Fetch this tenant's new replies and let the agent answer each. Returns count handled."""
    async with SessionLocal() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
    if not tenant or not tenant.smtp_email or not tenant.smtp_password:
        return 0

    host = imap_host_for(tenant.smtp_host)
    smtp_password = tenant.smtp_password_plain()  # decrypted for use (encrypted at rest)
    messages = await asyncio.to_thread(
        fetch_unseen, host, 993, tenant.smtp_email, smtp_password, settings.inbox_fetch_limit
    )
    if not messages:
        return 0

    handled = 0
    async with tenant_session(tenant_id) as session:
        ctx = await _sender_context(session, tenant_id, None)
        for msg in messages:
            try:
                if await _process(session, tenant, ctx, msg):
                    handled += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("inbound handling failed (%s): %s", msg.from_email, exc)
    return handled


async def _process(session, tenant, ctx, msg: InboundEmail) -> bool:
    """Match one reply to its conversation, get the agent's answer, and email it back."""
    if not msg.body:
        return False
    # Only converse with businesses we actually contacted (match reply sender → business).
    biz = (
        await session.execute(
            select(Business).where(
                func.lower(Business.email) == msg.from_email, Business.deleted_at.is_(None)
            )
        )
    ).scalars().first()
    if biz is None:
        return False
    conv = (
        await session.execute(
            select(Conversation)
            .where(Conversation.business_id == biz.id, Conversation.channel == "email")
            .order_by(Conversation.created_at.desc())
        )
    ).scalars().first()
    if conv is None or conv.status in _TERMINAL:
        return False
    transcript = list(conv.transcript or [])
    if len(transcript) >= settings.inbox_reply_max_turns:
        logger.info("thread with %s hit the turn cap — not auto-replying", biz.name)
        return False

    transcript.append({"role": "business", "text": msg.body, "ts": _now()})
    result = await agent.respond(_business_info(biz), transcript, "email", ctx)
    reply_text = result["reply"]
    intent = result["intent"]

    subject = msg.subject or f"A quick idea for {biz.name}"
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    if drafts.review_mode(tenant):
        # Human-in-the-loop: record the owner's message, queue the AI's reply for
        # approval. Intent/reminder side-effects are applied when the user approves.
        conv.transcript = transcript
        conv.updated_at = datetime.now(UTC)
        await drafts.queue_draft(
            session, tenant_id=str(tenant.id), business_id=str(biz.id), kind="reply",
            channel="email", to_contact=biz.email, subject=subject, body=reply_text,
            conversation_id=str(conv.id), meta=result, commit=False,
        )
        await session.commit()
        logger.info("reply to %s queued for approval (intent=%s)", biz.name, intent)
        return True

    # Autonomous mode: apply side-effects (status + auto-reminder) and send now.
    reply_text += await apply_reply_outcome(session, str(tenant.id), conv, biz, result)
    await send_email(
        host=tenant.smtp_host or "smtp.gmail.com", port=tenant.smtp_port or 587,
        sender=tenant.smtp_email, password=tenant.smtp_password_plain(),
        to=biz.email, subject=subject, body=reply_text,
    )

    transcript.append({"role": "assistant", "text": reply_text, "ts": _now()})
    conv.transcript = transcript
    conv.updated_at = datetime.now(UTC)
    await session.commit()
    logger.info("auto-replied to %s (intent=%s)", biz.name, intent)
    return True
