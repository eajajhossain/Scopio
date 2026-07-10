"""Minimal IMAP reader (stdlib only) for pulling unread customer replies.

`imaplib` is blocking, so callers run `fetch_unseen` via `asyncio.to_thread`. Fetching a
message with RFC822 marks it `\\Seen`, which is our dedup: each reply is processed once.
"""
import email
import imaplib
import logging
import re
from dataclasses import dataclass
from email.header import decode_header
from email.utils import parseaddr

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InboundEmail:
    from_email: str
    subject: str
    body: str
    message_id: str


# Lines that mark the start of quoted history in a reply — we cut everything from here.
_QUOTE_MARKERS = (
    re.compile(r"^\s*On .+ wrote:\s*$"),                 # Gmail / Apple
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.I),
    re.compile(r"^\s*_{5,}\s*$"),                          # Outlook divider
    re.compile(r"^\s*From:\s.+", re.I),                    # forwarded header block
    re.compile(r"^\s*Sent from my \w+", re.I),             # mobile signature
)


def strip_quoted(text: str) -> str:
    """Return just the new reply text, dropping quoted history and signatures."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    for line in lines:
        if line.lstrip().startswith(">"):            # quoted line → stop
            break
        if line.strip() == "--":                     # signature delimiter → stop
            break
        if any(m.match(line) for m in _QUOTE_MARKERS):
            break
        out.append(line)
    return "\n".join(out).strip()[:4000]


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _plain_body(msg: email.message.Message) -> str:
    """Prefer the text/plain part; fall back to a crude HTML strip."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        # no plain part — take the first html and strip tags
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                return re.sub(r"<[^>]+>", " ", html)
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def fetch_unseen(host: str, port: int, user: str, password: str, limit: int = 20) -> list[InboundEmail]:
    """Connect over IMAPS, read UNSEEN messages (marking them read), return parsed replies.

    Blocking — call via asyncio.to_thread. Returns [] and logs on any connection error.
    """
    out: list[InboundEmail] = []
    try:
        conn = imaplib.IMAP4_SSL(host, port)
    except Exception as exc:  # noqa: BLE001
        logger.warning("IMAP connect failed (%s:%s): %s", host, port, exc)
        return out
    try:
        conn.login(user, password)
        conn.select("INBOX")
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return out
        ids = data[0].split()[:limit]
        for num in ids:
            typ, msg_data = conn.fetch(num, "(RFC822)")   # RFC822 fetch marks \Seen
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            from_email = parseaddr(msg.get("From", ""))[1].lower().strip()
            if not from_email:
                continue
            out.append(
                InboundEmail(
                    from_email=from_email,
                    subject=_decode(msg.get("Subject")),
                    body=strip_quoted(_plain_body(msg)),
                    message_id=(msg.get("Message-ID") or "").strip(),
                )
            )
    except Exception as exc:  # noqa: BLE001 — never let a bad inbox crash the poller
        logger.warning("IMAP read failed for %s: %s", user, exc)
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    return out
