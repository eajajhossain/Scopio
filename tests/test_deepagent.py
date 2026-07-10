"""Deep research agent: gathers from Tavily + website and synthesizes an ExtractionResult.

Network is fully stubbed (no Tavily key, no LLM call), so this runs offline.
"""
import pytest

pytest.importorskip("langgraph")  # graph needs langgraph; skip if not installed

from app.core import llm  # noqa: E402
from app.services.deepagent import graph, tools  # noqa: E402


async def test_research_business_synthesizes_contacts(monkeypatch):
    async def fake_search(query, max_results=5):
        return [{"title": "Tasty Cafe", "url": "https://tastycafe.example",
                 "content": "phone 020 7946 0000, email hi@tastycafe.example"}]

    async def fake_read(url):
        return "Tasty Cafe — hi@tastycafe.example — 020 7946 0000 — open Mon-Fri 9-5"

    async def fake_chat(messages, **kwargs):
        return ('{"phone":"020 7946 0000","email":"hi@tastycafe.example",'
                '"opening_hours":"Mon-Fri 9-5","description":"A cafe.","address":null,'
                '"socials":{},"confidence":0.9}')

    monkeypatch.setattr(tools, "tavily_search", fake_search)
    monkeypatch.setattr(tools, "read_website", fake_read)
    monkeypatch.setattr(tools, "first_business_site", lambda results: results[0]["url"])
    monkeypatch.setattr(llm, "chat", fake_chat)

    result = await graph.research_business("Tasty Cafe", "cafe", "Soho, London", website=None)

    assert result.phone == "020 7946 0000"
    assert result.email == "hi@tastycafe.example"
    assert result.confidence == pytest.approx(0.9)
    assert result.details.get("opening_hours") == "Mon-Fri 9-5"
    # The agent discovered a website (none was given) and surfaces it for persistence.
    assert result.details.get("website") == "https://tastycafe.example"


async def test_research_business_no_data_is_safe(monkeypatch):
    async def empty_search(query, max_results=5):
        return []

    async def empty_read(url):
        return ""

    monkeypatch.setattr(tools, "tavily_search", empty_search)
    monkeypatch.setattr(tools, "read_website", empty_read)
    monkeypatch.setattr(tools, "first_business_site", lambda results: None)

    result = await graph.research_business("Ghost Shop", None, None, website=None)
    assert result.phone is None and result.email is None  # nothing invented
