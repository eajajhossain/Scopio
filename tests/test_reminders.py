from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.outreach.agent import _callback_days_from_text
from app.services.reminders.calendar_invite import build_ics
from app.services.reminders.meeting_link import mint_meeting_url
from app.services.reminders.service import due_in_days

IST = ZoneInfo("Asia/Kolkata")


def test_due_in_days_is_future_and_at_default_hour():
    due = due_in_days(3, "Asia/Kolkata")
    assert due.tzinfo is not None
    assert due > datetime.now(ZoneInfo("UTC"))
    local = due.astimezone(IST)
    assert local.hour == settings.reminder_default_hour   # scheduled at the local default hour


def test_due_in_days_offset_matches():
    today = datetime.now(IST).date()
    due = due_in_days(5, "Asia/Kolkata").astimezone(IST).date()
    assert (due - today).days == 5


def test_due_in_days_defaults_when_unknown():
    today = datetime.now(IST).date()
    due = due_in_days(None, "Asia/Kolkata").astimezone(IST).date()
    assert (due - today).days == settings.reminder_default_days


def test_due_in_days_ignores_nonpositive():
    today = datetime.now(IST).date()
    due = due_in_days(0, "Asia/Kolkata").astimezone(IST).date()
    assert (due - today).days == settings.reminder_default_days


def test_callback_days_from_text():
    assert _callback_days_from_text("call me tomorrow") == 1
    assert _callback_days_from_text("maybe next week") == 7
    assert _callback_days_from_text("in a couple of days") == 2
    assert _callback_days_from_text("sounds good") is None


def test_mint_meeting_url_is_unique_jitsi_room():
    a = mint_meeting_url()
    b = mint_meeting_url()
    assert a.startswith(settings.jitsi_base_url)
    assert "/scopio-" in a
    assert a != b   # each reminder gets its own room


def test_build_ics_is_valid_event_with_alarm_and_join():
    start = datetime(2026, 7, 1, 4, 30, tzinfo=ZoneInfo("UTC"))
    ics = build_ics(summary="Call with Tasty Cafe", start=start,
                    join_url="https://meet.jit.si/scopio-abc",
                    now=datetime(2026, 6, 22, tzinfo=ZoneInfo("UTC")))
    assert "BEGIN:VCALENDAR" in ics and "END:VCALENDAR" in ics
    assert "DTSTART:20260701T043000Z" in ics       # the call time, in UTC
    assert "DTEND:20260701T050000Z" in ics         # +30 min window
    assert "LOCATION:https://meet.jit.si/scopio-abc" in ics
    assert "BEGIN:VALARM" in ics and "TRIGGER:-PT15M" in ics  # device reminds 15 min before
