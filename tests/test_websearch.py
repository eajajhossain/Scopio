from app.services.enrichment.websearch import (
    _decode_ddg_href,
    build_query,
    is_business_site,
)


def test_business_site_accepts_own_domain():
    assert is_business_site("https://www.chowman.in/menu")
    assert is_business_site("http://maatara-sweets.com")


def test_business_site_rejects_socials_and_directories():
    assert not is_business_site("https://www.facebook.com/someshop")
    assert not is_business_site("https://instagram.com/shop")
    assert not is_business_site("https://www.justdial.com/Kolkata/xyz")
    assert not is_business_site("https://www.zomato.com/kolkata/abc")
    assert not is_business_site("https://en.wikipedia.org/wiki/Foo")


def test_build_query_with_and_without_locality():
    assert build_query("Maa Tara Sweets", "Barasat, 700125") == "Maa Tara Sweets Barasat, 700125"
    assert build_query("Maa Tara Sweets", None) == "Maa Tara Sweets"


def test_decode_ddg_redirect_href():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fchowman.in%2F&rut=abc"
    assert _decode_ddg_href(href) == "https://chowman.in/"


def test_decode_ddg_direct_href():
    assert _decode_ddg_href("https://example.com/") == "https://example.com/"
