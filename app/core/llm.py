"""Shared cloud-LLM brain: one OpenAI-compatible chat helper used across the app.

Groq by default (also fits Gemini / Ollama / OpenRouter — just change base_url + model).
This is the single place that talks to the chat API, so retry/rate-limit handling and
JSON-mode wiring live in one spot. Used by the outreach agent, the target profiler, and
the deep research agent's synthesis step.
"""
import asyncio
import logging
import time

import httpx

from app.core import telemetry
from app.core.config import settings

logger = logging.getLogger(__name__)


def llm_available() -> bool:
    """True if a chat LLM is configured (Groq/OpenAI-compatible key present)."""
    return bool(settings.groq_api_key)


async def chat(
    messages: list[dict],
    *,
    json_mode: bool = False,
    model: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 400,
    temperature: float | None = None,
    timeout: float = 40.0,
    attempts: int = 4,
) -> str:
    """Call an OpenAI-compatible chat completion and return the message content.

    Retries on HTTP 429 (free-tier rate limit) honoring `retry-after` (capped). Raises
    httpx.HTTPError on non-429 failures / exhausted retries — callers decide the fallback.
    """
    body: dict = {
        "model": model or settings.outreach_model,
        "temperature": temperature if temperature is not None else (0.3 if json_mode else 0.6),
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{(base_url or settings.outreach_base_url).rstrip('/')}/chat/completions"

    started = time.monotonic()

    def _done(data: dict) -> str:
        """Extract the content and trace the completion (model/usage/latency)."""
        content = data["choices"][0]["message"]["content"]
        telemetry.record_generation(
            name="chat", model=body["model"], messages=messages, output=content,
            usage=data.get("usage"), latency_ms=(time.monotonic() - started) * 1000,
        )
        return content

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = None
        for attempt in range(attempts):
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code == 429 and attempt < attempts - 1:
                wait = float(resp.headers.get("retry-after", 5))
                logger.info("LLM rate-limited; retrying in %.1fs", min(wait, 15))
                await asyncio.sleep(min(wait, 15))
                continue
            _raise_traced(resp, body["model"], messages, started)
            return _done(resp.json())
        # Exhausted retries — surface the last response's error.
        _raise_traced(resp, body["model"], messages, started)
        return _done(resp.json())


def _raise_traced(resp: httpx.Response, model: str, messages: list[dict], started: float) -> None:
    """raise_for_status, recording the failure to the trace first."""
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        telemetry.record_generation(
            name="chat", model=model, messages=messages, output=None,
            latency_ms=(time.monotonic() - started) * 1000, error=str(exc),
        )
        raise
