"""get_extractor() should pick: Claude > Groq (free) > heuristic, based on which keys are set."""
import app.services.enrichment.extractor as ex
from app.services.enrichment.extractor import (
    ClaudeExtractor,
    HeuristicExtractor,
    OpenAICompatExtractor,
    get_extractor,
)


def test_no_keys_uses_heuristic(monkeypatch):
    monkeypatch.setattr(ex.settings, "anthropic_api_key", None)
    monkeypatch.setattr(ex.settings, "groq_api_key", None)
    assert isinstance(get_extractor(), HeuristicExtractor)


def test_groq_key_uses_openai_compat(monkeypatch):
    monkeypatch.setattr(ex.settings, "anthropic_api_key", None)
    monkeypatch.setattr(ex.settings, "groq_api_key", "gsk_test")
    chosen = get_extractor()
    assert isinstance(chosen, OpenAICompatExtractor)
    assert chosen.name == "groq"


def test_claude_key_wins_over_groq(monkeypatch):
    monkeypatch.setattr(ex.settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(ex.settings, "groq_api_key", "gsk_test")
    # ClaudeExtractor() constructs an AsyncAnthropic client (no network) — should be chosen.
    assert isinstance(get_extractor(), ClaudeExtractor)
