"""Shared side-effects of one agent reply (status transitions + auto-reminder).

Used by BOTH delivery paths so they never drift:
- the autonomous inbox loop (reply sent immediately), and
- HITL review mode (reply queued as a draft; side-effects applied on approval).
"""
import logging
from zoneinfo import ZoneInfo

from app.services.reminders.service import create_reminder, due_in_days, tenant_tz

logger = logging.getLogger(__name__)


async def apply_reply_outcome(session, tenant_id: str, conv, biz, result: dict) -> str:
    """Apply the agent's structured result (intent / set_reminder / callback_days)
    to the conversation + business, creating the follow-up reminder when the owner
    agreed to a call. Returns extra text to append to the outgoing reply (the
    scheduling confirmation + meeting link), or "". Does NOT commit.
    """
    intent = result.get("intent")
    if result.get("set_reminder") and conv.reminder_id is None:
        tz_name = await tenant_tz(session, tenant_id)
        due = due_in_days(result.get("callback_days"), tz_name)
        reminder = await create_reminder(
            session, tenant_id=tenant_id, user_id=None, business_id=str(biz.id),
            due_at=due, channel=conv.channel,
            note="Owner agreed to a call — reminder set by Scopio AI", commit=False,
        )
        conv.reminder_id = reminder.id
        conv.status = "callback_scheduled"
        biz.status = "callback_scheduled"
        local = due.astimezone(ZoneInfo(tz_name))
        extra = (
            f"\n\nGreat — I've put us down for a call on "
            f"{local.strftime('%a %d %b, around %I:%M %p')} ({tz_name})."
        )
        if reminder.meeting_url:
            extra += f" Here's the link to join then: {reminder.meeting_url}"
        return extra
    if intent == "not_interested":
        conv.status = "not_interested"
        biz.status = "not_interested"
    elif intent == "interested" and conv.status == "active":
        conv.status = "interested"
        if biz.status in ("discovered", "contacted"):
            biz.status = "interested"
    return ""
