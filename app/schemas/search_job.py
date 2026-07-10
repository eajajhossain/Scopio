import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SearchJobCreate(BaseModel):
    # Either type an address, or supply GPS lat/lng (📍 Use my location) — one is required.
    raw_address: str | None = Field(default=None, examples=["Barasat, 700125"])
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    radius_m: int = Field(default=2000, ge=100, le=20000)
    category: str | None = None


class LeadIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    category: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    lat: float | None = None
    lng: float | None = None


class ExtensionImportIn(BaseModel):
    label: str = Field(default="Extension import", max_length=200)
    source: str = "google_places"
    businesses: list[LeadIn] = Field(max_length=2000)


class SearchJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    raw_address: str
    center_lat: float | None
    center_lng: float | None
    radius_m: int
    category: str | None
    target_profile: dict | None = None
    status: str
    error: str | None
    result_count: int
    created_at: datetime
    updated_at: datetime
