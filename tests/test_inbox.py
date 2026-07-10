"""Autonomous inbound-email agent: reply parsing, IMAP host derivation, and the
disabled-poll short-circuit. Network/DB are not touched (pure-logic + a flag check)."""
import app.services.inbox.service as inbox_service
from app.services.inbox.imap_client import strip_quoted
from app.services.inbox.service import imap_host_for


def test_strip_quoted_drops_gmail_history():
    body = (
        "Yes, that sounds great — call me Tuesday.\n"
        "\n"
        "On Mon, 6 Jul 2026 at 10:00, Eajaj <eajaj@x.com> wrote:\n"
        "> Hi, I'm Eajaj from Scopio...\n"
        "> a quick idea for your cafe\n"
    )
    assert strip_quoted(body) == "Yes, that sounds great — call me Tuesday."


def test_strip_quoted_drops_signature_and_quotes():
    assert strip_quoted("Sure, interested!\n--\nSent from my iPhone") == "Sure, interested!"
    assert strip_quoted("Not now thanks.\n> previous message") == "Not now thanks."


def test_strip_quoted_keeps_plain_reply():
    assert strip_quoted("How much does it cost?") == "How much does it cost?"


def test_strip_quoted_empty():
    assert strip_quoted("") == ""
    assert strip_quoted(None) == ""


def test_imap_host_derivation():
    assert imap_host_for("smtp.gmail.com") == "imap.gmail.com"
    assert imap_host_for("smtp.mail.example.com") == "imap.mail.example.com"
    assert imap_host_for(None) == "imap.gmail.com"
    assert imap_host_for("mail.example.com") == "mail.example.com"  # no smtp. prefix → unchanged


async def test_poll_disabled_short_circuits(monkeypatch):
    # When the loop is disabled it must return 0 without touching IMAP or the DB.
    monkeypatch.setattr(inbox_service.settings, "inbox_poll_enabled", False)
    assert await inbox_service.poll_all_inboxes() == 0
