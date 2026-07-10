import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

search_status_enum = ENUM(
    "pending", "geocoding", "querying", "enriching", "completed", "failed",
    name="search_status", create_type=False,
)


class SearchJob(Base):
    __tablename__ = "search_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    raw_address: Mapped[str] = mapped_column(String, nullable=False)
    center_lat: Mapped[float | None] = mapped_column()
    center_lng: Mapped[float | None] = mapped_column()
    radius_m: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    category: Mapped[str | None] = mapped_column(String)
    # Context-aware targeting derived from the owner's services (targeting.TargetProfile):
    # {target_business_types, osm_filters, tavily_keywords, rationale}. Null = broad search.
    target_profile: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(search_status_enum, nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(String)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
