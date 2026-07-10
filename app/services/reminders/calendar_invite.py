"""Calendar invites so the BUSINESS OWNER is reminded on their own device.

We can't push a notification to a non-user's phone. Instead we hand them a
calendar event — an .ics invite (universal: Apple/Google/Outlook) and a one-tap
"Add to Google Calendar" link. Once added, the owner's own device alerts them
before the call, on any platform, in any country.
"""
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

CALL_DURATION_MIN = 30


def _utc_stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ics_escape(text: str) -> str:
    # RFC 5545 text escaping
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _window(start: datetime) -> tuple[datetime, datetime]:
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return start, start + timedelta(minutes=CALL_DURATION_MIN)


def google_calendar_url(
    *,
    summary: str,
    start: datetime,
    join_url: str | None = None,
) -> str:
    """A one-tap 'Add to Google Calendar' link for the same 30-min call window.

    Opening it pre-fills a Google Calendar event the user can save in one click —
    a convenient alternative to downloading the .ics. Used in the dashboard, not in
    the owner's outreach message.
    """
    start, end = _window(start)
    desc = f"Call with Scopio.{(' Join: ' + join_url) if join_url else ''}"
    params = {
        "action": "TEMPLATE",
        "text": summary,
        "dates": f"{_utc_stamp(start)}/{_utc_stamp(end)}",
        "details": desc,
    }
    if join_url:
        params["location"] = join_url
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def build_ics(
    *,
    summary: str,
    start: datetime,
    join_url: str | None,
    organizer_email: str = "noreply@scopio.app",
    attendee_email: str | None = None,
    now: datetime | None = None,
) -> str:
    """An RFC-5545 VEVENT the owner can open to add the call to their calendar."""
    start, end = _window(start)
    desc = f"Call with Scopio.{(' Join: ' + join_url) if join_url else ''}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Scopio//Reminders//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}@scopio",
        f"DTSTAMP:{_utc_stamp(now or datetime.now(UTC))}",
        f"DTSTART:{_utc_stamp(start)}",
        f"DTEND:{_utc_stamp(end)}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(desc)}",
        f"ORGANIZER;CN=Scopio:mailto:{organizer_email}",
    ]
    if join_url:
        lines.append(f"LOCATION:{_ics_escape(join_url)}")
    if attendee_email:
        lines.append(f"ATTENDEE;RSVP=TRUE;CN={attendee_email}:mailto:{attendee_email}")
    # A 15-minute pop-up alarm so their device reminds them before the call.
    lines += [
        "BEGIN:VALARM",
        "TRIGGER:-PT15M",
        "ACTION:DISPLAY",
        "DESCRIPTION:Upcoming call",
        "END:VALARM",
        "STATUS:CONFIRMED",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"
