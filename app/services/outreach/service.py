
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business import Business
from app.models.conversation import Conversation
from app.models.search_job_business import SearchJobBusiness
from app.models.tenant import Tenant
from app.models.user import AppUser
from app.services.outreach import agent, drafts
from app.services.outreach.channels import (
    contact_for_channel,
    get_channel,
    mailto_link,
    send_email,
    whatsapp_link,
)
from app.services.outreach.playbook import SenderContext, default_context
from app.services.reminders.service import create_reminder, due_in_days, tenant_tz

logger = logging.getLogger(__name__)


def _business_info(biz: Business) -> dict:
    """What the agent needs to understand the TARGET business and tailor the pitch."""
    details = biz.details or {}
    return {
        "name": biz.name,
        "category": biz.category,
        "description": details.get("description"),
    }


async def _sender_context(
    session: AsyncSession, tenant_id: str, user_id: str | None
) -> SenderContext:
    """Build the AI's identity from the account: the user's name + their company/services."""
    ctx = default_context()
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant and tenant.company_name:
        ctx.company_name = tenant.company_name
    if tenant and tenant.services:
        ctx.services = tenant.services
    if user_id:
        user = (
            await session.execute(select(AppUser).where(AppUser.id == user_id))
        ).scalar_one_or_none()
        if user and user.full_name:
            ctx.sender_name = user.full_name
    return ctx


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _get_business(session: AsyncSession, business_id: str) -> Business:
    biz = (
        await session.execute(select(Business).where(Business.id == business_id))
    ).scalar_one_or_none()
    if biz is None:
        raise HTTPException(status_code=404, detail="business not found")
    return biz


async def start_conversation(
    session: AsyncSession, tenant_id: str, user_id: str | None, business_id: str, channel: str
) -> Conversation:
    biz = await _get_business(session, business_id)
    to = contact_for_channel(biz, channel)
    if not to:
        raise HTTPException(
            status_code=400,
            detail=f"{biz.name} has no {channel} contact — enrich it first or pick another channel.",
        )
    ctx = await _sender_context(session, tenant_id, user_id)
    opening = await agent.generate_opening(_business_info(biz), channel, ctx)
    await get_channel(channel).send(to, opening)  # preview/record

    conv = Conversation(
        tenant_id=tenant_id,
        business_id=biz.id,
        channel=channel,
        status="active",
        transcript=[{"role": "assistant", "text": opening, "ts": _now()}],
    )
    session.add(conv)
    if biz.status == "discovered":
        biz.status = "contacted"
    await session.commit()
    await session.refresh(conv)
    return conv


async def handle_reply(
    session: AsyncSession, tenant_id: str, user_id: str | None,
    conversation_id: str, business_message: str,
) -> Conversation:
    conv = (
        await session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    biz = await _get_business(session, str(conv.business_id))

    transcript = list(conv.transcript or [])
    transcript.append({"role": "business", "text": business_message, "ts": _now()})

    ctx = await _sender_context(session, tenant_id, user_id)
    result = await agent.respond(_business_info(biz), transcript, conv.channel, ctx)
    reply_text = result["reply"]
    intent = result["intent"]

    # The moment the owner agrees to a call, set a follow-up reminder (once) so the
    # AI remembers the date to call them — no calendar/meeting needed.
    if result.get("set_reminder") and conv.reminder_id is None:
        tz_name = await tenant_tz(session, tenant_id)
        due = due_in_days(result.get("callback_days"), tz_name)
        reminder = await create_reminder(
            session, tenant_id=tenant_id, user_id=user_id, business_id=str(biz.id),
            due_at=due, channel=conv.channel,
            note="Owner agreed to a call — reminder set by Scopio AI", commit=False,
        )
        conv.reminder_id = reminder.id
        conv.status = "callback_scheduled"
        local = due.astimezone(ZoneInfo(tz_name))
        reply_text += (
            f"\n\n✅ Perfect — I've noted you down for a call on "
            f"{local.strftime('%a %d %b, around %I:%M %p')} ({tz_name}). "
        )
        if reminder.meeting_url:
            # Share the same video room both sides join at the call time.
            reply_text += f"Here's our meeting link to join then: {reminder.meeting_url} "
        reply_text += "I'll reach out then. Talk soon!"
    else:
        if intent == "not_interested":
            conv.status = "not_interested"
            biz.status = "not_interested"
        elif intent == "interested":
            conv.status = "interested"
            if biz.status in ("discovered", "contacted"):
                biz.status = "interested"

    transcript.append({"role": "assistant", "text": reply_text, "ts": _now()})
    conv.transcript = transcript
    await get_channel(conv.channel).send(contact_for_channel(biz, conv.channel), reply_text)
    await session.commit()
    await session.refresh(conv)
    return conv


async def _opening_message(session, tenant_id, user_id, biz, channel) -> str:
    """Reuse the business's drafted opening if any, else generate a fresh one."""
    conv = (
        await session.execute(
            select(Conversation)
            .where(Conversation.business_id == biz.id)
            .order_by(Conversation.created_at.desc())
        )
    ).scalars().first()
    if conv and conv.transcript:
        msg = next((t["text"] for t in conv.transcript if t["role"] == "assistant"), None)
        if msg:
            return msg
    ctx = await _sender_context(session, tenant_id, user_id)
    return await agent.generate_opening(_business_info(biz), channel, ctx)


async def contact_link(
    session: AsyncSession, tenant_id: str, user_id: str | None,
    business_id: str, channel: str,
) -> dict:
    """Build a click-to-send link (wa.me / mailto) with the AI message pre-filled."""
    biz = await _get_business(session, business_id)
    to = contact_for_channel(biz, channel)
    if not to:
        raise HTTPException(status_code=400, detail=f"{biz.name} has no {channel} contact.")
    message = await _opening_message(session, tenant_id, user_id, biz, channel)
    if channel == "email":
        link = mailto_link(to, f"A quick idea for {biz.name}", message)
    else:
        link = whatsapp_link(to, message)
        if not link:
            raise HTTPException(status_code=400, detail="Couldn't build a WhatsApp link from that number.")
    return {"channel": channel, "to": to, "message": message, "link": link}


async def send_message(
    session: AsyncSession, tenant_id: str, user_id: str | None,
    business_id: str, channel: str,
) -> dict:
    """Deliver the message. Email auto-sends if connected; WhatsApp returns a tap-to-send link."""
    biz = await _get_business(session, business_id)
    to = contact_for_channel(biz, channel)
    if not to:
        raise HTTPException(status_code=400, detail=f"{biz.name} has no {channel} contact.")
    message = await _opening_message(session, tenant_id, user_id, biz, channel)

    if channel == "email":
        tenant = (
            await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if not tenant or not tenant.smtp_email or not tenant.smtp_password:
            # Not connected → fall back to a mailto link (user sends manually).
            return {"sent": False, "channel": "email", "to": to,
                    "link": mailto_link(to, f"A quick idea for {biz.name}", message),
                    "message": message,
                    "note": "Connect your email to send automatically."}
        if drafts.review_mode(tenant):
            # Human-in-the-loop: queue for approval instead of sending.
            draft = await drafts.queue_draft(
                session, tenant_id=tenant_id, business_id=str(biz.id), kind="opening",
                channel="email", to_contact=to, subject=f"A quick idea for {biz.name}",
                body=message,
            )
            return {"sent": False, "queued": True, "draft_id": str(draft.id),
                    "channel": "email", "to": to, "message": message,
                    "note": "Draft queued — approve it under Drafts to send."}
        try:
            await send_email(
                host=tenant.smtp_host or "smtp.gmail.com", port=tenant.smtp_port or 587,
                sender=tenant.smtp_email, password=tenant.smtp_password_plain(),
                to=to, subject=f"A quick idea for {biz.name}", body=message,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Email send failed: {exc}") from exc
        # Record it as a sent conversation.
        session.add(Conversation(
            tenant_id=tenant_id, business_id=biz.id, channel="email", status="active",
            transcript=[{"role": "assistant", "text": message, "ts": _now()}],
        ))
        if biz.status == "discovered":
            biz.status = "contacted"
        await session.commit()
        return {"sent": True, "channel": "email", "to": to, "message": message}

    # WhatsApp: cannot auto-send from a personal account → tap-to-send link.
    link = whatsapp_link(to, message)
    if not link:
        raise HTTPException(status_code=400, detail="Couldn't build a WhatsApp link from that number.")
    return {"sent": False, "channel": "whatsapp", "to": to, "link": link, "message": message}


async def whatsapp_queue(
    session: AsyncSession, tenant_id: str, user_id: str | None, job_id: str,
    limit: int = 60,
) -> list[dict]:
    """Build a tap-through queue: each WhatsApp-able business + a pre-filled wa.me link.

    WhatsApp can't auto-send from a personal number, so the user taps Send on each —
    this just queues them up with the message ready, fast to blast through.
    """
    from app.schemas.business import _is_mobile  # mobile detection (local import avoids cycle)

    businesses = (
        await session.execute(
            select(Business)
            .join(SearchJobBusiness, SearchJobBusiness.business_id == Business.id)
            .where(
                SearchJobBusiness.search_job_id == job_id,
                Business.deleted_at.is_(None),
                Business.phone.is_not(None),
            )
            .limit(limit)
        )
    ).scalars().all()
    out = []
    for biz in businesses:
        if not _is_mobile(biz.phone):
            continue
        message = await _opening_message(session, tenant_id, user_id, biz, "whatsapp")
        link = whatsapp_link(biz.phone, message)
        if link:
            out.append({"business_id": str(biz.id), "name": biz.name,
                        "to": biz.phone, "link": link, "message": message})
    return out


# --- One-click bulk outreach -------------------------------------------------

def _bulk_candidate_stmt(job_id: str):
    """Businesses in a job that HAVE a contact and haven't been contacted yet."""
    return (
        select(Business)
        .join(SearchJobBusiness, SearchJobBusiness.business_id == Business.id)
        .where(
            SearchJobBusiness.search_job_id == job_id,
            Business.deleted_at.is_(None),
            Business.status == "discovered",
            or_(Business.phone.is_not(None), Business.email.is_not(None)),
        )
    )


async def count_bulk_candidates(session: AsyncSession, job_id: str) -> int:
    rows = (await session.execute(_bulk_candidate_stmt(job_id))).scalars().all()
    return len(rows)


async def bulk_outreach(
    session: AsyncSession, tenant_id: str, user_id: str | None, job_id: str,
    limit: int | None = None,
) -> dict:
    """For each contactable, not-yet-contacted business: email-auto-send if the business
    has an email and the account's email is connected; otherwise draft a conversation
    (WhatsApp tap-to-send, or email mailto fallback)."""
    cap = limit or settings.outreach_bulk_max
    businesses = (
        await session.execute(_bulk_candidate_stmt(job_id).limit(cap))
    ).scalars().all()
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    can_email = bool(tenant and tenant.smtp_email and tenant.smtp_password)
    ctx = await _sender_context(session, tenant_id, user_id)

    review = drafts.review_mode(tenant)
    sent = 0
    drafted = 0
    queued = 0
    for biz in businesses:
        try:
            if biz.email and can_email:
                msg = await agent.generate_opening(_business_info(biz), "email", ctx)
                if review:
                    # Human-in-the-loop: queue every message for one-click approval.
                    await drafts.queue_draft(
                        session, tenant_id=tenant_id, business_id=str(biz.id),
                        kind="opening", channel="email", to_contact=biz.email,
                        subject=f"A quick idea for {biz.name}", body=msg,
                    )
                    queued += 1
                    continue
                await send_email(
                    host=tenant.smtp_host or "smtp.gmail.com", port=tenant.smtp_port or 587,
                    sender=tenant.smtp_email, password=tenant.smtp_password_plain(),
                    to=biz.email, subject=f"A quick idea for {biz.name}", body=msg,
                )
                session.add(Conversation(
                    tenant_id=tenant_id, business_id=biz.id, channel="email", status="active",
                    transcript=[{"role": "assistant", "text": msg, "ts": _now()}],
                ))
                if biz.status == "discovered":
                    biz.status = "contacted"
                await session.commit()
                sent += 1
            else:
                channel = "whatsapp" if biz.phone else "email"
                await start_conversation(session, tenant_id, user_id, str(biz.id), channel)
                drafted += 1
        except Exception as exc:  # noqa: BLE001 — one failure shouldn't stop the batch
            logger.warning("bulk outreach failed for %s: %s", biz.id, exc)
    logger.info("bulk outreach: sent=%d queued=%d drafted=%d (job %s)",
                sent, queued, drafted, job_id)
    return {"sent": sent, "queued": queued, "drafted": drafted}
