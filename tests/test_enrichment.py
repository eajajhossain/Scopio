import pytest

from app.services.enrichment.extractor import HeuristicExtractor, _result_from_profile
from app.services.enrichment.fetcher import html_to_text


def test_result_from_profile_maps_contacts_and_details():
    data = {
        "phone": "+91 90000 11111",
        "email": "hi@shop.in",
        "opening_hours": "Mon-Sat 10-8",
        "description": "Family sweet shop since 1990.",
        "address": "Jessore Rd",
        "socials": {"facebook": "https://fb.com/shop", "instagram": None},
        "confidence": 0.85,
    }
    r = _result_from_profile(data, note="test")
    assert r.phone == "+91 90000 11111"
    assert r.email == "hi@shop.in"
    assert r.confidence == 0.85
    assert r.details["opening_hours"] == "Mon-Sat 10-8"
    assert r.details["description"].startswith("Family")
    assert r.details["socials"] == {"facebook": "https://fb.com/shop"}  # null dropped


def test_result_from_profile_handles_missing_fields():
    r = _result_from_profile({"phone": None, "email": None, "confidence": 0}, note="t")
    assert r.phone is None and r.email is None
    assert r.details == {}  # nothing to store


def test_html_to_text_strips_tags_and_scripts():
    html = """
      <html><head><style>.a{color:red}</style><script>var x=1;</script></head>
      <body><h1>Maa Tara Sweets</h1><p>Call us: +91 90000 11111</p>
      <a href="mailto:hi@maatara.in">email</a></body></html>
    """
    text = html_to_text(html)
    assert "Maa Tara Sweets" in text
    assert "+91 90000 11111" in text
    assert "color:red" not in text   # style stripped
    assert "var x" not in text       # script stripped
    assert "<" not in text           # tags gone


@pytest.mark.asyncio
async def test_heuristic_extracts_phone_and_email():
    text = "Contact ABC Store at +91 90000 11111 or sales@abcstore.in for orders."
    result = await HeuristicExtractor().extract("ABC Store", text)
    assert result.email == "sales@abcstore.in"
    assert "90000" in (result.phone or "")
    assert result.confidence > 0


@pytest.mark.asyncio
async def test_heuristic_rejects_too_short_number():
    # "12345" is not a valid phone (only 5 digits) — should not be returned.
    result = await HeuristicExtractor().extract("X", "ref code 12345 only")
    assert result.phone is None


@pytest.mark.asyncio
async def test_heuristic_empty_text():
    result = await HeuristicExtractor().extract("X", "")
    assert result.phone is None and result.email is None
    assert result.confidence == 0.0
