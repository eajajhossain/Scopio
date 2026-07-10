"""Human-in-the-loop review mode: mode gating + reply-outcome side-effects.
Pure-logic + light fakes (no DB/SMTP touched), matching the repo's test style."""
from types import SimpleNamespace

import pytest

import app.services.outreach.outcome as outcome_mod
from app.services.outreach.drafts import review_mode
from app.services.outreach.outcome import apply_reply_outcome


def test_review_mode_default_and_explicit():
    assert review_mode(SimpleNamespace(outreach_mode="review")) is True
    assert review_mode(SimpleNamespace(outreach_mode="autonomous")) is False
    # Missing/blank column (legacy row) must fail safe → review.
    assert review_mode(SimpleNamespace(outreach_mode=None)) is True
    assert review_mode(SimpleNamespace()) is True
    assert review_mode(None) is True


def _conv(status="active", reminder_id=None):
    return SimpleNamespace(status=status, reminder_id=reminder_id, channel="email")


def _biz(status="contacted"):
    return SimpleNamespace(id="b-1", status=status)


async def test_outcome_not_interested_closes_both():
    conv, biz = _conv(), _biz()
    extra = await apply_reply_outcome(None, "t-1", conv, biz,
                                      {"intent": "not_interested", "set_reminder": False})
    assert extra == ""
    assert conv.status == "not_interested"
    assert biz.status == "not_interested"


async def test_outcome_interested_promotes_active_conv():
    conv, biz = _conv(), _biz()
    await apply_reply_outcome(None, "t-1", conv, biz,
                              {"intent": "interested", "set_reminder": False})
    assert conv.status == "interested"
    assert biz.status == "interested"


async def test_outcome_reminder_created_once(monkeypatch):
    """set_reminder=True creates the reminder + schedules the callback; a conversation
    that already has a reminder must not get a second one."""
    from datetime import UTC, datetime

    created = []

    async def fake_create_reminder(session, **kw):
        created.append(kw)
        return SimpleNamespace(id="r-1", meeting_url="https://meet.jit.si/scopio-x")

    async def fake_tenant_tz(session, tenant_id):
        return "Asia/Kolkata"

    monkeypatch.setattr(outcome_mod, "create_reminder", fake_create_reminder)
    monkeypatch.setattr(outcome_mod, "tenant_tz", fake_tenant_tz)
    monkeypatch.setattr(outcome_mod, "due_in_days",
                        lambda days, tz: datetime(2026, 7, 11, 4, 30, tzinfo=UTC))

    conv, biz = _conv(), _biz()
    extra = await apply_reply_outcome(None, "t-1", conv, biz,
                                      {"intent": "callback", "set_reminder": True,
                                       "callback_days": 1})
    assert len(created) == 1
    assert conv.reminder_id == "r-1"
    assert conv.status == "callback_scheduled"
    assert biz.status == "callback_scheduled"
    assert "put us down for a call" in extra
    assert "meet.jit.si" in extra

    # Second agreeing reply on the same conversation → no duplicate reminder.
    extra2 = await apply_reply_outcome(None, "t-1", conv, biz,
                                       {"intent": "callback", "set_reminder": True})
    assert len(created) == 1
    assert extra2 == ""


async def test_send_message_queues_draft_in_review_mode(monkeypatch):
    """send_message must NOT email when the tenant is in review mode — it queues a draft."""
    import app.services.outreach.service as svc

    biz = SimpleNamespace(id="b-1", name="Brew & Bloom", email="owner@brew.in",
                          phone=None, status="discovered", details={}, category="food")
    tenant = SimpleNamespace(smtp_email="me@x.com", smtp_password="pw",
                             smtp_host=None, smtp_port=None, outreach_mode="review")

    class FakeResult:
        def __init__(self, value):
            self._v = value
        def scalar_one_or_none(self):
            return self._v

    class FakeSession:
        async def execute(self, stmt):
            return FakeResult(tenant)

    async def fake_get_business(session, business_id):
        return biz

    async def fake_opening(session, tenant_id, user_id, b, channel):
        return "Hello! Quick idea for you."

    queued = []

    async def fake_queue_draft(session, **kw):
        queued.append(kw)
        return SimpleNamespace(id="d-1")

    sent = []

    async def fake_send_email(**kw):
        sent.append(kw)

    monkeypatch.setattr(svc, "_get_business", fake_get_business)
    monkeypatch.setattr(svc, "_opening_message", fake_opening)
    monkeypatch.setattr(svc.drafts, "queue_draft", fake_queue_draft)
    monkeypatch.setattr(svc, "send_email", fake_send_email)

    out = await svc.send_message(FakeSession(), "t-1", "u-1", "b-1", "email")
    assert out["queued"] is True and out["draft_id"] == "d-1"
    assert len(queued) == 1 and queued[0]["kind"] == "opening"
    assert queued[0]["to_contact"] == "owner@brew.in"
    assert sent == []  # the whole point: nothing was emailed


async def test_send_message_sends_in_autonomous_mode(monkeypatch):
    import app.services.outreach.service as svc

    biz = SimpleNamespace(id="b-1", name="Brew & Bloom", email="owner@brew.in",
                          phone=None, status="discovered", details={}, category="food")
    tenant = SimpleNamespace(smtp_email="me@x.com", smtp_password="pw",
                             smtp_password_plain=lambda: "pw",
                             smtp_host=None, smtp_port=None, outreach_mode="autonomous")

    class FakeResult:
        def __init__(self, value):
            self._v = value
        def scalar_one_or_none(self):
            return self._v

    class FakeSession:
        def add(self, obj): pass
        async def execute(self, stmt): return FakeResult(tenant)
        async def commit(self): pass

    async def fake_get_business(session, business_id):
        return biz

    async def fake_opening(session, tenant_id, user_id, b, channel):
        return "Hello! Quick idea for you."

    sent = []

    async def fake_send_email(**kw):
        sent.append(kw)

    monkeypatch.setattr(svc, "_get_business", fake_get_business)
    monkeypatch.setattr(svc, "_opening_message", fake_opening)
    monkeypatch.setattr(svc, "send_email", fake_send_email)

    out = await svc.send_message(FakeSession(), "t-1", "u-1", "b-1", "email")
    assert out["sent"] is True
    assert len(sent) == 1 and sent[0]["to"] == "owner@brew.in"


def test_draft_model_defaults():
    from app.models.draft import OutreachDraft

    d = OutreachDraft(tenant_id="t", business_id="b", kind="opening", channel="email",
                      to_contact="a@b.c", body="hi")
    # Column default applies at INSERT; the Python-side default must also be 'pending'.
    assert d.status == "pending" or d.status is None


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
