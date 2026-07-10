from app.services.outreach.channels import _wa_number, mailto_link, whatsapp_link


def test_wa_number_adds_india_code_to_bare_mobile():
    assert _wa_number("9007498608") == "919007498608"


def test_wa_number_strips_leading_zero_and_formatting():
    assert _wa_number("098765 43210") == "919876543210"
    assert _wa_number("+91 90074 98608") == "919007498608"


def test_wa_number_handles_country_code_and_trunk_zero():
    assert _wa_number("+91 08069028723") == "918069028723"
    assert _wa_number("+91 09007498608") == "919007498608"


def test_wa_number_uses_first_of_multiple():
    assert _wa_number("03325522222; 03325522562") == "913325522222"


def test_whatsapp_link_encodes_message():
    link = whatsapp_link("9007498608", "Hi there! Let's chat?")
    assert link.startswith("https://wa.me/919007498608?text=")
    assert "%20" in link and "%3F" in link   # space + '?' encoded


def test_mailto_link_encodes_subject_and_body():
    link = mailto_link("owner@shop.in", "A quick idea for Shop", "Hello,\nlet's talk.")
    assert link.startswith("mailto:owner@shop.in?subject=")
    assert "body=" in link and "%0A" in link  # newline encoded


def test_wa_number_empty_returns_none():
    assert _wa_number("n/a") is None
