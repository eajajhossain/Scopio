"""Turn the owner's own services into a *target profile* — which kinds of businesses
are good leads for them.

This is the brain that makes discovery context-aware: a cup/packaging supplier should
search cafes and restaurants, a dental-supply rep should search clinics, and so on. The
LLM maps free-text services onto OpenStreetMap tag values (so Overpass can filter to just
those categories) plus human-readable labels and Tavily seed keywords for the deep agent.

Degrades gracefully: with no LLM key (or on any parse error) it returns an empty profile,
which callers treat as "search everything" — exactly today's broad behaviour.
"""
import json
import logging
from dataclasses import dataclass, field

from app.core import llm
from app.core.config import settings

logger = logging.getLogger(__name__)

# OSM tag keys we let the profiler target. These line up with the clauses Overpass
# builds (see discovery/overpass.py). Values are matched as a regex alternation.
_ALLOWED_KEYS = ("amenity", "shop", "tourism", "leisure", "craft", "office", "healthcare")


@dataclass(slots=True)
class TargetProfile:
    target_business_types: list[str] = field(default_factory=list)  # human labels for the UI
    osm_filters: dict[str, list[str]] = field(default_factory=dict)  # {osm_key: [values]}
    tavily_keywords: list[str] = field(default_factory=list)         # deep-agent seed queries
    rationale: str = ""

    @property
    def is_empty(self) -> bool:
        """True when there's nothing to narrow by → discovery stays broad (search all)."""
        return not any(self.osm_filters.get(k) for k in _ALLOWED_KEYS)

    def to_dict(self) -> dict:
        return {
            "target_business_types": self.target_business_types,
            "osm_filters": self.osm_filters,
            "tavily_keywords": self.tavily_keywords,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "TargetProfile":
        data = data or {}
        raw = data.get("osm_filters") or {}
        osm_filters = {
            k: [str(v) for v in raw.get(k, []) if v]
            for k in _ALLOWED_KEYS
            if raw.get(k)
        }
        return cls(
            target_business_types=[str(t) for t in (data.get("target_business_types") or [])],
            osm_filters=osm_filters,
            tavily_keywords=[str(k) for k in (data.get("tavily_keywords") or [])],
            rationale=str(data.get("rationale") or ""),
        )


_SYSTEM = (
    "You help a B2B sales tool decide which local businesses are good LEADS for a seller, "
    "given what that seller offers. You map the seller's offering to the kinds of nearby "
    "businesses that would BUY from them, expressed as OpenStreetMap tag values so a map "
    "query can find exactly those. Think about who actually needs the product/service."
)

_INSTRUCTIONS = (
    "Return ONLY a JSON object with these keys:\n"
    '  "target_business_types": array of short human-readable labels (e.g. "cafes", '
    '"bakeries", "restaurants") — the businesses most likely to buy.\n'
    '  "osm_filters": object mapping OpenStreetMap keys to arrays of tag VALUES. Allowed '
    f"keys: {', '.join(_ALLOWED_KEYS)}. Use real OSM values, e.g. "
    '{"amenity": ["cafe","restaurant","fast_food"], "shop": ["bakery","convenience"]}. '
    "Only include keys that apply; keep it focused (the businesses that would actually buy).\n"
    '  "tavily_keywords": array of 2-4 web-search phrases to research such a business '
    '(e.g. "cafe contact email owner").\n'
    '  "rationale": one short sentence on why these are the right targets.\n'
    "No prose, no markdown — just the JSON object."
)


async def derive_target_profile(services: str, company: str | None = None) -> TargetProfile:
    """Derive who to target from the seller's services. Empty profile if no LLM/parse fails."""
    services = (services or "").strip()
    if not services or not llm.llm_available():
        return TargetProfile()
    user = (
        f"Seller company: {company or 'a local B2B seller'}\n"
        f"What they offer / sell:\n{services}\n\n{_INSTRUCTIONS}"
    )
    try:
        content = await llm.chat(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            json_mode=True,
            model=settings.outreach_model,
            max_tokens=500,
        )
        profile = TargetProfile.from_dict(json.loads(content))
        logger.info(
            "target profile: types=%s filters=%s",
            profile.target_business_types, profile.osm_filters,
        )
        return profile
    except Exception as exc:  # noqa: BLE001 — never let profiling block discovery
        logger.warning("target profiling failed, using broad search: %s", exc)
        return TargetProfile()
