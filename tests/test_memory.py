"""Agent memory: working-memory scratchpad + promotion to the lead's semantic memory."""
from types import SimpleNamespace

from app.services.outreach import agent, memory
from app.services.outreach.playbook import SenderContext, system_prompt

CTX = SenderContext(sender_name="Akash", company_name="NovaAI", services="AI chatbots for shops.")


def _conv(mem=None):
    return SimpleNamespace(memory=mem or {})


def _biz(details=None):
    return SimpleNamespace(details=details)


def test_remember_writes_working_and_semantic_memory():
    conv, biz = _conv(), _biz()
    memory.remember(conv, biz, ["Owner's name is Priya", "Closed on Mondays"])
    assert conv.memory["facts"] == ["Owner's name is Priya", "Closed on Mondays"]
    # promoted to the business so FUTURE conversations know it too
    assert biz.details["known_facts"] == ["Owner's name is Priya", "Closed on Mondays"]


def test_remember_dedups_case_insensitively_and_keeps_existing():
    conv = _conv({"facts": ["Owner's name is Priya"]})
    biz = _biz({"description": "a cafe", "known_facts": ["Owner's name is Priya"]})
    memory.remember(conv, biz, ["owner's name is priya", "Wants pricing in writing"])
    assert conv.memory["facts"] == ["Owner's name is Priya", "Wants pricing in writing"]
    assert biz.details["known_facts"] == ["Owner's name is Priya", "Wants pricing in writing"]
    assert biz.details["description"] == "a cafe"   # existing enrichment untouched


def test_remember_ignores_junk_and_caps():
    conv, biz = _conv(), _biz()
    memory.remember(conv, biz, [None, 42, "  ", {"a": 1}])
    assert conv.memory == {}            # nothing usable -> no write
    assert biz.details is None
    memory.remember(conv, biz, [f"fact {i}" for i in range(50)])
    assert len(conv.memory["facts"]) <= memory.MAX_FACTS
    # a single turn only accepts MAX_NEW_FACTS facts
    assert len(conv.memory["facts"]) == memory.MAX_NEW_FACTS


def test_fallback_respond_always_returns_new_facts_key():
    for text in ("no thanks", "yes, let's schedule a call", "ok", "how much does it cost?"):
        r = agent._fallback_respond([{"role": "business", "text": text, "ts": "now"}])
        assert r["new_facts"] == []


def test_system_prompt_handles_unknown_answers_gracefully():
    p = system_prompt(CTX).lower()
    # the LLM-as-brain rule: never invent, pivot unknowns to the call, keep the lead
    assert "guess or invent" in p
    assert "win the lead" in p
