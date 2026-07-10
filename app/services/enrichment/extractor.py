
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtractionResult:
    phone: str | None = None
    email: str | None = None
    confidence: float = 0.0
    note: str = ""
    # Phase 4: rich profile (opening_hours, description, address, socials{}, website)
    details: dict = field(default_factory=dict)


class Extractor(Protocol):
    name: str
    async def extract(self, business_name: str, site_text: str) -> ExtractionResult: ...


# --- Heuristic (free, no key) --------------------------------------------------
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Phone-ish: optional +country, then 7+ digits with spaces/dashes/parens.
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


class HeuristicExtractor:
    name = "heuristic"

    async def extract(self, business_name: str, site_text: str) -> ExtractionResult:
        if not site_text:
            return ExtractionResult(note="no site text")
        email_match = _EMAIL.search(site_text)
        phone_match = _PHONE.search(site_text)
        phone = phone_match.group(0).strip() if phone_match else None
        # crude validation: a real phone has 8–15 digits
        if phone and not (8 <= len(re.sub(r"\D", "", phone)) <= 15):
            phone = None
        email = email_match.group(0) if email_match else None
        found = bool(phone or email)
        return ExtractionResult(
            phone=phone,
            email=email,
            confidence=0.5 if found else 0.0,
            note="regex heuristic",
        )


# --- Shared profile schema/prompt (Phase 4: deep WebsiteSource) ---------------
_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_SOCIALS_SCHEMA = {
    "type": "object",
    "properties": {k: _NULLABLE_STR for k in ("facebook", "instagram", "twitter", "youtube", "linkedin")},
    "required": [],
    "additionalProperties": False,
}
_SCHEMA = {
    "type": "object",
    "properties": {
        "phone": _NULLABLE_STR,
        "email": _NULLABLE_STR,
        "opening_hours": _NULLABLE_STR,
        "description": _NULLABLE_STR,
        "address": _NULLABLE_STR,
        "socials": _SOCIALS_SCHEMA,
        "confidence": {"type": "number"},
    },
    "required": ["phone", "email", "confidence"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You extract a business's public details from the text of its OWN website. "
    "Return only what is clearly present; use null for anything not stated. "
    "NEVER invent or guess — especially phone numbers. Fields: phone, email, "
    "opening_hours (as written), description (one short factual sentence about what "
    "the business does), address, socials (full URLs to the business's facebook / "
    "instagram / twitter / youtube / linkedin pages). 'confidence' is your 0..1 "
    "confidence that the extracted contact details truly belong to this business."
)


def _result_from_profile(data: dict, note: str) -> ExtractionResult:
    """Map a parsed model JSON object into an ExtractionResult (+ details)."""
    socials = {k: v for k, v in (data.get("socials") or {}).items() if v}
    details: dict = {}
    for key in ("opening_hours", "description", "address"):
        if data.get(key):
            details[key] = data[key]
    if socials:
        details["socials"] = socials
    return ExtractionResult(
        phone=data.get("phone") or None,
        email=data.get("email") or None,
        confidence=float(data.get("confidence") or 0.0),
        note=note,
        details=details,
    )


class ClaudeExtractor:
    name = "claude"

    def __init__(self) -> None:
        # Lazy import so the module loads without the SDK installed.
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.enrichment_model

    async def extract(self, business_name: str, site_text: str) -> ExtractionResult:
        if not site_text:
            return ExtractionResult(note="no site text")
        prompt = (
            f"Business name: {business_name}\n\n"
            f"Website text:\n{site_text}\n\n"
            "Extract the business's full public profile."
        )
        try:
            import anthropic

            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            )
        except anthropic.APIError as exc:
            logger.warning("Claude extraction failed: %s", exc)
            return ExtractionResult(note=f"claude error: {exc}")

        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return ExtractionResult(note="unparseable model output")
        return _result_from_profile(data, note=f"claude:{self._model}")


_JSON_SYSTEM = (
    _SYSTEM
    + ' Respond ONLY with a JSON object with keys: phone, email, opening_hours, '
    + 'description, address (string|null each), socials (object with optional '
    + 'facebook/instagram/twitter/youtube/linkedin URL strings), and confidence '
    + "(number). No prose, no markdown."
)


class OpenAICompatExtractor:
    """Works with any OpenAI-compatible chat API: Groq, Gemini, Ollama, OpenRouter.

    Point it at a provider via settings.llm_base_url / llm_model / groq_api_key.
    """

    name = "groq"

    def __init__(self) -> None:
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.groq_api_key
        self.model = settings.llm_model

    async def extract(self, business_name: str, site_text: str) -> ExtractionResult:
        if not site_text:
            return ExtractionResult(note="no site text")
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 800,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _JSON_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Business name: {business_name}\n\nWebsite text:\n{site_text}\n\n"
                        "Extract the business's full public profile as JSON."
                    ),
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", json=body, headers=headers
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            logger.warning("LLM extraction failed (%s): %s", self.model, exc)
            return ExtractionResult(note=f"llm error: {exc}")

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return ExtractionResult(note="unparseable model output")
        return _result_from_profile(data, note=f"llm:{self.model}")


def get_extractor() -> Extractor:
    """Pick the best available extractor: Claude > free LLM (Groq) > heuristic."""
    if settings.anthropic_api_key:
        try:
            return ClaudeExtractor()
        except Exception as exc:  # noqa: BLE001 — never let setup kill the worker
            logger.error("Claude extractor unavailable: %s", exc)
    if settings.groq_api_key:
        return OpenAICompatExtractor()
    return HeuristicExtractor()
