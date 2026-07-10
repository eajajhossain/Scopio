import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StartOutreach(BaseModel):
    business_id: uuid.UUID
    channel: str = Field(default="whatsapp", pattern="^(whatsapp|email|sms)$")


class ReplyIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    channel: str
    status: str
    transcript: list
    reminder_id: uuid.UUID | None
    created_at: datetime


class ConversationListOut(BaseModel):
    items: list[ConversationOut]
    total: int
