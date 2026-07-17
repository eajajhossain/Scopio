import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=120)          # the sender's name (e.g. Akash)
    company_name: str = Field(min_length=1, max_length=160)
    services: str = Field(min_length=3, max_length=150000)   # what they offer (~20k words)


class LoginIn(BaseModel):
    email: str
    password: str


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    company_name: str | None = Field(default=None, max_length=160)
    services: str | None = Field(default=None, max_length=150000)  # ~20k words


class ConnectEmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    app_password: str = Field(min_length=4, max_length=200)
    # Optional: auto-derived from the email's provider when omitted. Supplied only
    # from the UI's "Advanced" panel for custom domains / self-hosted servers.
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)


class EmailProviderOut(BaseModel):
    detected: bool
    name: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    app_password_url: str | None = None
    note: str | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    company_name: str | None
    services: str | None
    email_connected: bool = False
    is_admin: bool = False
    last_login_at: datetime | None = None


class AuthOut(BaseModel):
    token: str
    user: UserOut
