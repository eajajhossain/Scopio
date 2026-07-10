from app.core.phone import e164
from app.schemas.business import _is_mobile


def test_indian_mobiles_are_detected():
    assert _is_mobile("9007498608")        # bare 10-digit mobile
    assert _is_mobile("+91 90074 98608")   # with country code + spaces
    assert _is_mobile("09007498608")       # leading zero
    assert _is_mobile("9876543210")
    assert _is_mobile("+91 09007498608")   # country code AND trunk zero (real mobile)


def test_international_mobiles_are_detected():
    assert _is_mobile("+1 212 555 0182")   # US (mobile-capable)
    assert not _is_mobile("+44 20 7946 0958")  # London landline


def test_landlines_are_not_mobile():
    assert not _is_mobile("03325522222")   # Kolkata landline (STD code)
    assert not _is_mobile("033 2552 2222")
    assert not _is_mobile("02212345678")   # Mumbai landline


def test_edge_cases():
    assert not _is_mobile(None)
    assert not _is_mobile("")
    assert not _is_mobile("12345")         # too short
    assert not _is_mobile("1234567890")    # 10 digits but starts with 1 (not 6-9)


def test_first_of_multiple_numbers_used():
    assert _is_mobile("9007498608; 03325522222")     # first is mobile
    assert not _is_mobile("03325522222; 9007498608")  # first is landline


def test_e164_dials_any_country():
    # Numbers with a country code dial worldwide, regardless of the default region.
    assert e164("+1 212 555 0182") == "+12125550182"          # US
    assert e164("+44 20 7946 0958") == "+442079460958"        # UK
    assert e164("+61 2 9374 4000", "IN") == "+61293744000"    # Australia
    # A bare local number is interpreted in the configured default region.
    assert e164("9007498608", "IN") == "+919007498608"
    assert e164("212 555 0182", "US") == "+12125550182"
    assert e164(None) is None
    assert e164("not a phone") is None
