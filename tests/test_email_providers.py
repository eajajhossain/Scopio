"""Auto-detection of SMTP settings from an email address (easy email connect)."""
from app.services.outreach.email_providers import detect, domain_of, resolve


def test_detect_known_providers():
    assert detect("akash@gmail.com").smtp_host == "smtp.gmail.com"
    assert detect("akash@gmail.com").smtp_port == 587
    assert detect("me@yahoo.com").smtp_host == "smtp.mail.yahoo.com"
    assert detect("me@yahoo.com").smtp_port == 465          # Yahoo uses implicit TLS
    assert detect("me@outlook.com").name == "Outlook"
    assert detect("me@hotmail.com").name == "Outlook"       # alias domain
    assert detect("me@icloud.com").smtp_host == "smtp.mail.me.com"


def test_detect_is_case_insensitive():
    assert detect("Akash@Gmail.COM").key == "gmail"


def test_detect_unknown_domain():
    assert detect("owner@mycompany.co") is None
    assert detect("not-an-email") is None


def test_detect_provider_has_app_password_link():
    p = detect("x@gmail.com")
    assert p.app_password_url.startswith("https://")
    assert p.note                                            # human guidance present


def test_domain_of():
    assert domain_of("a@b.com") == "b.com"
    assert domain_of("A@B.COM") == "b.com"
    assert domain_of("nope") == ""


def test_resolve_auto_derives_for_known_provider():
    host, port = resolve("akash@gmail.com")
    assert (host, port) == ("smtp.gmail.com", 587)


def test_resolve_explicit_override_wins():
    # Advanced settings: user typed a custom server → that beats auto-detection.
    host, port = resolve("akash@gmail.com", host="smtp.custom.com", port=2525)
    assert (host, port) == ("smtp.custom.com", 2525)


def test_resolve_unknown_needs_manual():
    # Unknown domain + nothing supplied → caller must ask for host/port.
    assert resolve("owner@mycompany.co") == (None, None)
    # …but a custom domain still works once the user fills Advanced settings.
    assert resolve("owner@mycompany.co", host="mail.mycompany.co", port=587) == (
        "mail.mycompany.co", 587,
    )
