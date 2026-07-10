"""Mint a free, no-auth video room link for a callback.

Each reminder gets a unique Jitsi Meet room URL. Both the user and the customer
open the same link to join the call together at the reminder time — no account,
no scheduling provider, no card needed.
"""
import uuid

from app.core.config import settings


def mint_meeting_url() -> str:
    """A unique Jitsi room, e.g. https://meet.jit.si/scopio-a1b2c3d4e5f6."""
    room = "scopio-" + uuid.uuid4().hex[:12]
    return f"{settings.jitsi_base_url.rstrip('/')}/{room}"
