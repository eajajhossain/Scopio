"""Map an email address to its SMTP settings + how to make an app password.

This is what lets a user connect their sending mailbox by typing just the address
and an app password — Scopio fills in the correct SMTP host/port automatically, so
they never have to know or type server settings. Covers the common providers that
support SMTP app passwords; an unknown domain falls back to manual host/port entry.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EmailProvider:
    key: str                 # short id (e.g. "gmail")
    name: str                # display name (e.g. "Gmail")
    smtp_host: str
    smtp_port: int
    app_password_url: str    # deep link to the provider's app-password page
    note: str                # one-line, human guidance for the UI


_GMAIL = EmailProvider(
    "gmail", "Gmail", "smtp.gmail.com", 587,
    "https://myaccount.google.com/apppasswords",
    "Turn on 2-Step Verification, then create a 16-character App Password and paste it here.",
)
_OUTLOOK = EmailProvider(
    "outlook", "Outlook", "smtp-mail.outlook.com", 587,
    "https://account.microsoft.com/security",
    "Enable 2-step verification, then create an app password under Security → Advanced.",
)
_YAHOO = EmailProvider(
    "yahoo", "Yahoo Mail", "smtp.mail.yahoo.com", 465,
    "https://login.yahoo.com/account/security/app-passwords",
    "Generate an app password (not your normal password) and paste it here.",
)
_ICLOUD = EmailProvider(
    "icloud", "iCloud Mail", "smtp.mail.me.com", 587,
    "https://account.apple.com/account/manage",
    "Turn on two-factor auth, then create an app-specific password under Sign-In & Security.",
)
_AOL = EmailProvider(
    "aol", "AOL Mail", "smtp.aol.com", 465,
    "https://login.aol.com/account/security",
    "Create an app password under Account Security and paste it here.",
)
_ZOHO = EmailProvider(
    "zoho", "Zoho Mail", "smtp.zoho.com", 465,
    "https://accounts.zoho.com/home#security/app_password",
    "Create an application-specific password and paste it here.",
)
_FASTMAIL = EmailProvider(
    "fastmail", "Fastmail", "smtp.fastmail.com", 465,
    "https://app.fastmail.com/settings/security/apppasswords",
    "Create an app password (Settings → Privacy & Security) and paste it here.",
)
_GMX = EmailProvider(
    "gmx", "GMX", "mail.gmx.com", 465,
    "https://www.gmx.com/",
    "Enable POP3/IMAP access in settings, then use your mailbox password here.",
)


# Domains → provider. Google Workspace / custom domains can't be detected from the
# domain alone, so those users fall through to manual host/port (Advanced in the UI).
_BY_DOMAIN: dict[str, EmailProvider] = {
    "gmail.com": _GMAIL, "googlemail.com": _GMAIL,
    "outlook.com": _OUTLOOK, "hotmail.com": _OUTLOOK, "live.com": _OUTLOOK,
    "msn.com": _OUTLOOK, "hotmail.co.uk": _OUTLOOK, "live.co.uk": _OUTLOOK,
    "yahoo.com": _YAHOO, "yahoo.co.uk": _YAHOO, "yahoo.in": _YAHOO,
    "ymail.com": _YAHOO, "rocketmail.com": _YAHOO,
    "icloud.com": _ICLOUD, "me.com": _ICLOUD, "mac.com": _ICLOUD,
    "aol.com": _AOL,
    "zoho.com": _ZOHO, "zohomail.com": _ZOHO,
    "fastmail.com": _FASTMAIL, "fastmail.fm": _FASTMAIL,
    "gmx.com": _GMX, "gmx.net": _GMX, "gmx.co.uk": _GMX,
}


def domain_of(email: str) -> str:
    return email.split("@")[-1].strip().lower() if email and "@" in email else ""


def detect(email: str) -> EmailProvider | None:
    """The provider for an email address, or None if the domain isn't recognized."""
    return _BY_DOMAIN.get(domain_of(email))


def resolve(
    email: str, host: str | None = None, port: int | None = None
) -> tuple[str | None, int | None]:
    """Final (host, port) to use: an explicit value always wins, else auto-detected.

    Returns (None, None) when the domain is unknown and nothing was supplied — the
    caller should then ask the user for the server details (Advanced settings).
    """
    provider = detect(email)
    resolved_host = (host or "").strip() or (provider.smtp_host if provider else None)
    resolved_port = port or (provider.smtp_port if provider else None)
    return resolved_host, resolved_port
