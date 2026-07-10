import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

business_source_enum = ENUM(
    "osm", "manual_import", "google_places", "geoapify",
    name="business_source", create_type=False,
)
lead_status_enum = ENUM(
    # "meeting_booked" is legacy (kept so old rows still read); new flow uses "callback_scheduled".
    "discovered", "contacted", "interested", "callback_scheduled", "meeting_booked",
    "not_interested", "do_not_contact", name="lead_status", create_type=False,
)


class Business(Base):
    __tablename__ = "business"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), nullable=False
    )
    search_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_job.id")
    )
    source: Mapped[str] = mapped_column(business_source_enum, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str | None] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    lat: Mapped[float | None] = mapped_column()
    lng: Mapped[float | None] = mapped_column()
    phone: Mapped[str | None] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String)
    website: Mapped[str | None] = mapped_column(String)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    fit_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    dedup_key: Mapped[str] = mapped_column(String, nullable=False)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(lead_status_enum, nullable=False, default="discovered")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
