"""Admin access control: who is allowed into the cross-tenant dashboard."""
import app.core.deps as deps
from app.core.deps import Identity, email_is_admin, is_admin_identity


def test_email_is_admin_matches_configured_list(monkeypatch):
    monkeypatch.setattr(deps.settings, "admin_emails", "boss@x.com, owner@y.com")
    assert email_is_admin("BOSS@x.com")          # case-insensitive
    assert email_is_admin("owner@y.com")
    assert not email_is_admin("random@z.com")
    assert not email_is_admin(None)


def test_email_is_admin_empty_list_denies(monkeypatch):
    monkeypatch.setattr(deps.settings, "admin_emails", "")
    assert not email_is_admin("anyone@x.com")


async def test_dev_user_is_admin_in_dev(monkeypatch):
    monkeypatch.setattr(deps.settings, "environment", "dev")
    ident = Identity(tenant_id=deps.settings.dev_tenant_id, user_id=deps.settings.dev_user_id)
    assert await is_admin_identity(ident) is True   # dev convenience, no DB needed


async def test_non_admin_denied_in_production(monkeypatch):
    monkeypatch.setattr(deps.settings, "environment", "production")
    monkeypatch.setattr(deps.settings, "admin_emails", "")
    # A non-UUID user id short-circuits to False before any DB lookup.
    ident = Identity(tenant_id="t", user_id="not-a-uuid")
    assert await is_admin_identity(ident) is False
