import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReminderCreate(BaseModel):
    business_id: uuid.UUID
    due_at: datetime
    channel: str | None = Field(default=None, pattern="^(whatsapp|email|call)$")
    note: str | None = Field(default=None, max_length=1000)


class ReminderUpdate(BaseModel):
    due_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)
    status: str | None = Field(default=None, pattern="^(pending|done|cancelled)$")


class ReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    due_at: datetime
    timezone: str
    channel: str | None
    business_name: str | None
    contact: str | None
    meeting_url: str | None
    google_calendar_url: str | None = None
    note: str | None
    status: str
    created_at: datetime


class ReminderListOut(BaseModel):
    items: list[ReminderOut]
    total: int
