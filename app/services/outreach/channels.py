"""Channel adapters behind one interface.

Default is PreviewChannel: it records the message but does NOT actually send to a
real person — safe for development and demos. Real providers (WhatsApp Business
API, Twilio SMS, SES/SendGrid email) implement the same `send()` and are enabled
only once credentials + opt-in/compliance are in place.
"""
import logging
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import quote

import aiosmtplib

from app.core.phone import wa_number as _wa_number  # international wa.me normalization

logger = logging.getLogger(__name__)


async def send_email(*, host: str, port: int, sender: str, password: str,
                     to: str, subject: str, body: str) -> None:
    """Send a real email via SMTP (e.g. Gmail with an app password). Raises on failure."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    use_tls = port == 465          # 465 = implicit TLS, 587 = STARTTLS
    await aiosmtplib.send(
        msg, hostname=host, port=port, username=sender, password=password,
        start_tls=not use_tls, use_tls=use_tls, timeout=30,
    )


async def verify_smtp(*, host: str, port: int, sender: str, password: str) -> None:
    """Log in to the SMTP server to validate credentials. Raises on failure.

    `connect()` handles the TLS handshake itself: implicit TLS on 465, or STARTTLS on
    587 (`start_tls=True`). We must NOT then call `starttls()` again — that raises
    "Connection already using TLS". This mirrors how `send_email` connects.
    """
    use_tls = port == 465
    client = aiosmtplib.SMTP(hostname=host, port=port, timeout=30)
    await client.connect(use_tls=use_tls, start_tls=not use_tls)
    await client.login(sender, password)
    await client.quit()


def whatsapp_link(phone: str, text: str) -> str | None:
    """A click-to-chat link that opens WhatsApp with the message pre-filled."""
    num = _wa_number(phone)
    if not num:
        return None
    return f"https://wa.me/{num}?text={quote(text)}"


def mailto_link(email: str, subject: str, body: str) -> str:
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"


@dataclass(slots=True)
class SendResult:
    mode: str          # "preview" or "live"
    channel: str
    to: str | None
    ok: bool = True
    detail: str = ""


class ChannelAdapter(Protocol):
    name: str
    async def send(self, to: str | None, body: str) -> SendResult: ...


class PreviewChannel:
    """Logs the message instead of sending it. The safe default."""

    def __init__(self, channel: str):
        self.name = channel

    async def send(self, to: str | None, body: str) -> SendResult:
        logger.info("[PREVIEW %s -> %s] %s", self.name, to, body[:120])
        return SendResult(mode="preview", channel=self.name, to=to,
                          detail="Preview only — not actually sent. Configure a provider to send live.")


def get_channel(channel: str) -> ChannelAdapter:
    # Real adapters (whatsapp/email/sms) slot in here when configured; preview for now.
    return PreviewChannel(channel)


def contact_for_channel(business, channel: str) -> str | None:
    if channel == "email":
        return business.email
    return business.phone  # whatsapp / sms
