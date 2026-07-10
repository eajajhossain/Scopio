"""Context-aware targeting: services -> TargetProfile (with graceful fallback)."""
import json

import app.services.targeting as tg
from app.services.targeting import TargetProfile, derive_target_profile


def test_from_dict_sanitizes_and_reports_empty():
    empty = TargetProfile.from_dict(None)
    assert empty.is_empty
    assert empty.osm_filters == {}

    profile = TargetProfile.from_dict(
        {
            "target_business_types": ["cafes", "bakeries"],
            "osm_filters": {"amenity": ["cafe", "restaurant"], "shop": ["bakery"], "bogus_key": ["x"]},
            "tavily_keywords": ["cafe contact email"],
            "rationale": "they buy cups",
        }
    )
    assert not profile.is_empty
    assert profile.osm_filters == {"amenity": ["cafe", "restaurant"], "shop": ["bakery"]}
    assert "bogus_key" not in profile.osm_filters  # only allowed OSM keys survive
    assert profile.target_business_types == ["cafes", "bakeries"]


async def test_derive_falls_back_without_llm(monkeypatch):
    monkeypatch.setattr(tg.llm, "llm_available", lambda: False)
    profile = await derive_target_profile("we sell coffee cups", "CupCo")
    assert profile.is_empty  # no LLM -> broad search


async def test_derive_parses_llm_json(monkeypatch):
    monkeypatch.setattr(tg.llm, "llm_available", lambda: True)

    async def fake_chat(messages, **kwargs):
        return json.dumps(
            {
                "target_business_types": ["cafes", "restaurants"],
                "osm_filters": {"amenity": ["cafe", "restaurant"]},
                "tavily_keywords": ["cafe owner email"],
                "rationale": "cafes buy disposable cups",
            }
        )

    monkeypatch.setattr(tg.llm, "chat", fake_chat)
    profile = await derive_target_profile("disposable coffee cups & lids", "CupCo")
    assert profile.osm_filters == {"amenity": ["cafe", "restaurant"]}
    assert "cafes" in profile.target_business_types


async def test_derive_bad_json_falls_back(monkeypatch):
    monkeypatch.setattr(tg.llm, "llm_available", lambda: True)

    async def bad_chat(messages, **kwargs):
        return "not json at all"

    monkeypatch.setattr(tg.llm, "chat", bad_chat)
    profile = await derive_target_profile("anything", "Co")
    assert profile.is_empty  # parse failure -> broad search, never crashes
