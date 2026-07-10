import uuid
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.config import settings
from app.core.phone import e164 as _e164  # international dialable (+E.164)
from app.core.phone import is_mobile as _is_mobile  # international mobile detection


def _favicon(website: str | None) -> str | None:
    """Free logo/icon for a business from its website domain (no API key)."""
    if not website:
        return None
    try:
        url = website if "://" in website else "https://" + website
        netloc = urlparse(url).netloc.split(":")[0]
    except ValueError:
        return None
    return f"https://icons.duckduckgo.com/ip3/{netloc}.ico" if netloc else None


class BusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category: str | None
    address: str | None
    lat: float | None
    lng: float | None
    phone: str | None
    email: str | None
    website: str | None
    source: str
    status: str
    fit_score: float | None
    confidence: float | None
    enriched_at: datetime | None
    created_at: datetime
    details: dict | None = None   # rich profile: hours, description, socials, address
    # internal: full OSM tag set, used to derive image/hours; not serialized
    raw: dict | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_contact(self) -> bool:
        return bool(self.phone or self.email)

    @computed_field
    @property
    def whatsappable(self) -> bool:
        """True if the phone looks like a mobile (so WhatsApp tap-to-send will reach it)."""
        return _is_mobile(self.phone, settings.phone_default_region)

    @computed_field
    @property
    def phone_e164(self) -> str | None:
        """Full international number for click-to-call — dials in any country."""
        return _e164(self.phone, settings.phone_default_region)

    @computed_field
    @property
    def enriched(self) -> bool:
        return self.enriched_at is not None

    @computed_field
    @property
    def image_url(self) -> str | None:
        raw = self.raw or {}
        # Prefer a real photo if OSM has one, else fall back to the website favicon.
        return raw.get("image") or _favicon(self.website)

    @computed_field
    @property
    def opening_hours(self) -> str | None:
        # Prefer the AI-enriched value, fall back to the OSM tag.
        return (self.details or {}).get("opening_hours") or (self.raw or {}).get("opening_hours")


class BusinessUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None


class BusinessListOut(BaseModel):
    items: list[BusinessOut]
    total: int
