import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Tenant(Base):
    __tablename__ = "tenant"

    def smtp_password_plain(self) -> str | None:
        """Decrypt the stored SMTP app password for use (encrypted at rest)."""
        from app.core.security import decrypt_secret  # local: avoid import cycle

        return decrypt_secret(self.smtp_password)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    company_name: Mapped[str | None] = mapped_column(String)
    services: Mapped[str | None] = mapped_column(String)
    smtp_email: Mapped[str | None] = mapped_column(String)
    smtp_password: Mapped[str | None] = mapped_column(String)
    smtp_host: Mapped[str | None] = mapped_column(String, default="smtp.gmail.com")
    smtp_port: Mapped[int | None] = mapped_column(Integer, default=587)
    country: Mapped[str] = mapped_column(String, nullable=False, default="IN")
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="Asia/Kolkata")
    # 'review' = human-in-the-loop (drafts queue for approval); 'autonomous' = AI sends directly.
    outreach_mode: Mapped[str] = mapped_column(String, nullable=False, default="review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
