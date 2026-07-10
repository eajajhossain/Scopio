
import re

import phonenumbers
from phonenumbers import PhoneNumberType

_MOBILE_TYPES = {PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE}


def normalize_phone(raw: str | None, default_region: str = "IN") -> tuple[str | None, bool]:
    """Return (E.164 digits without '+', is_mobile). (None, False) if unparseable/invalid."""
    if not raw:
        return (None, False)
    first = re.split(r"[;,/]", raw)[0].strip()
    try:
        num = phonenumbers.parse(first, default_region)
    except phonenumbers.NumberParseException:
        return (None, False)
    if not phonenumbers.is_valid_number(num):
        return (None, False)
    e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    is_mobile = phonenumbers.number_type(num) in _MOBILE_TYPES
    return (e164.lstrip("+"), is_mobile)


def wa_number(raw: str | None, default_region: str = "IN") -> str | None:
    """Digits for a wa.me link (international), or None."""
    return normalize_phone(raw, default_region)[0]


def e164(raw: str | None, default_region: str = "IN") -> str | None:
    """Full international dialable number with '+', e.g. '+919876543210'.

    Lets the click-to-call link reach a number in ANY country. Numbers already
    carrying a country code (+1, +44, +91, …) work worldwide regardless of region.
    """
    digits = normalize_phone(raw, default_region)[0]
    return f"+{digits}" if digits else None


def is_mobile(raw: str | None, default_region: str = "IN") -> bool:
    """True if the number is a mobile (so WhatsApp can reach it)."""
    return normalize_phone(raw, default_region)[1]
