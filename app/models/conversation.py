import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

conversation_status_enum = ENUM(
    # "meeting_booked" is legacy (kept so old rows still read); new flow uses "callback_scheduled".
    "active", "interested", "callback_scheduled", "meeting_booked", "not_interested", "closed",
    name="conversation_status", create_type=False,
)


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(conversation_status_enum, nullable=False, default="active")
    transcript: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Working-memory scratchpad: {"facts": [...]} — facts the agent learned in THIS
    # conversation (survives the capped transcript window). See outreach/memory.py.
    memory: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reminder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reminder.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
