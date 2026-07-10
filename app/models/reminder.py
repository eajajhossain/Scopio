import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

reminder_status_enum = ENUM(
    "pending", "done", "cancelled", name="reminder_status", create_type=False
)


class Reminder(Base):
    """A follow-up the AI (or user) wants to remember: 'call this business on this date'."""

    __tablename__ = "reminder"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id")
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str | None] = mapped_column(String)        # whatsapp | email | call
    business_name: Mapped[str | None] = mapped_column(String)  # denormalized for fast listing
    contact: Mapped[str | None] = mapped_column(String)        # phone/email to reach them on
    meeting_url: Mapped[str | None] = mapped_column(String)     # auto-minted Jitsi room both sides join
    note: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(reminder_status_enum, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def google_calendar_url(self) -> str:
        """One-tap 'Add to Google Calendar' link for this call (dashboard convenience)."""
        from app.services.reminders.calendar_invite import google_calendar_url

        return google_calendar_url(
            summary=f"Call with {self.business_name or 'business'}",
            start=self.due_at,
            join_url=self.meeting_url,
        )
