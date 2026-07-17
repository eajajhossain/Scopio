
from app.services.outreach import agent
from app.services.outreach.playbook import (
    SenderContext,
    fallback_opening,
    system_prompt,
)

CTX = SenderContext(sender_name="Akash", company_name="NovaAI", services="AI chatbots for shops.")


def test_system_prompt_personalizes_and_has_guardrails():
    p = system_prompt(CTX)
    assert "Akash" in p and "NovaAI" in p   # introduces as the user + their company
    low = p.lower()
    assert "honest" in low
    assert "manipulat" in low                # explicitly forbids manipulation
    assert "call" in low


def test_optout_line_is_channel_aware_and_not_duplicated():
    email = agent.with_optout("Hi there, quick idea for you.", "email")
    assert "reply stop" in email.lower() and "email" in email.lower()
    wa = agent.with_optout("Hey! quick idea 👋", "whatsapp")
    assert "reply stop" in wa.lower()
    # If the message already carries an opt-out, don't add a second one.
    already = "Great offer. Not interested? Just reply STOP and I won't email you again."
    assert agent.with_optout(already, "email") == already


async def test_generated_opening_includes_optout(monkeypatch):
    # No LLM key → fallback path; opening must still carry the opt-out line.
    monkeypatch.setattr(agent.llm, "llm_available", lambda: False)
    msg = await agent.generate_opening({"name": "Tasty Cafe", "category": "food"}, "email", CTX)
    assert "reply stop" in msg.lower()


def test_fallback_opening_uses_sender_identity():
    msg = fallback_opening({"name": "Maa Tara Sweets", "category": "food"}, "whatsapp", CTX)
    assert "Maa Tara Sweets" in msg
    assert "Akash" in msg and "NovaAI" in msg   # "Hi ... I'm Akash from NovaAI"
    assert "call" in msg.lower()


def test_fallback_opening_is_tailored_by_category():
    health = fallback_opening({"name": "City Clinic", "category": "health"}, "whatsapp", CTX)
    food = fallback_opening({"name": "Tasty Cafe", "category": "food"}, "whatsapp", CTX)
    assert "patient" in health.lower()      # clinic-specific benefit
    assert "reservation" in food.lower() or "order" in food.lower()  # food-specific
    assert health != food                   # tailored, not identical


def _t(role, text):
    return {"role": role, "text": text, "ts": "now"}


def test_fallback_respond_sets_reminder_on_yes():
    r = agent._fallback_respond([_t("assistant", "Want a quick call?"),
                                 _t("business", "Yes, sounds good — call me tomorrow!")])
    assert r["set_reminder"] is True
    assert r["intent"] == "callback"
    assert r["callback_days"] == 1   # "tomorrow" -> 1 day out


def test_fallback_respond_does_not_book_on_soft_ok():
    # A soft "ok"/"sure" is NOT permission to book — the AI asks to schedule first.
    for reply in ("ok", "sure, tell me more", "yes, sounds interesting — what do you do?"):
        r = agent._fallback_respond([_t("assistant", "Want a quick call?"),
                                     _t("business", reply)])
        assert r["set_reminder"] is False, reply
        assert r["intent"] in ("interested", "question")


def test_fallback_respond_books_only_on_explicit_call_agreement():
    r = agent._fallback_respond([_t("assistant", "Shall I set up a quick call?"),
                                 _t("business", "Yes, let's schedule a call for tomorrow")])
    assert r["set_reminder"] is True
    assert r["intent"] == "callback"
    assert r["callback_days"] == 1


def test_fallback_respond_backs_off_on_no():
    r = agent._fallback_respond([_t("assistant", "Want a call?"), _t("business", "No thanks, not interested")])
    assert r["set_reminder"] is False
    assert r["intent"] == "not_interested"


def test_fallback_respond_answers_question():
    r = agent._fallback_respond([_t("assistant", "Hi!"), _t("business", "What exactly do you do?")])
    assert r["set_reminder"] is False
    assert r["intent"] == "question"
    assert len(r["reply"]) > 0


def test_fallback_varies_by_question_type():
    price = agent._fallback_respond([_t("business", "how much does it cost?")])["reply"]
    proof = agent._fallback_respond([_t("business", "do you have experience / examples?")])["reply"]
    what = agent._fallback_respond([_t("business", "what exactly do you do?")])["reply"]
    # three different question types -> three different answers (not the same canned line)
    assert len({price, proof, what}) == 3
    assert "pricing" in price.lower() or "depends" in price.lower()
    assert "example" in proof.lower() or "space" in proof.lower()
