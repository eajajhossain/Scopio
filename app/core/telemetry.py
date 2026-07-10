"""Optional agent observability via Langfuse — a complete no-op without keys.

Two primitives, used at the two altitudes that matter:
- `span(name)`: wraps one agent run (a deep-research job, an outreach turn) so
  everything inside lands in one trace with a waterfall view.
- `record_generation(...)`: called by the shared LLM brain (app/core/llm.py) for
  every chat completion — model, messages, output, token usage, latency. Nests
  under the active span automatically (OTel context propagation).

Design rule: telemetry must never break the product. Every Langfuse call is
wrapped; on any failure we log at debug and disable ourselves for the process.
"""
import logging
from contextlib import contextmanager

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None
_broken = False  # set on first Langfuse failure so we stop retrying every call


def enabled() -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key) and not _broken


def _get_client():
    global _client, _broken
    if not enabled():
        return None
    if _client is None:
        try:
            from langfuse import Langfuse

            _client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        except Exception as exc:  # noqa: BLE001 — observability must never break the app
            logger.warning("Langfuse unavailable, tracing disabled: %s", exc)
            _broken = True
            return None
    return _client


@contextmanager
def span(name: str, *, input: dict | None = None, metadata: dict | None = None):
    """Trace one agent run. Yields the span (or None when tracing is off) so the
    caller can attach the final output: `if s: s.update(output=...)`."""
    client = _get_client()
    if client is None:
        yield None
        return
    try:
        cm = client.start_as_current_observation(
            as_type="span", name=name, input=input, metadata=metadata
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("langfuse span failed: %s", exc)
        yield None
        return
    with cm as s:
        yield s


def record_generation(
    *,
    name: str,
    model: str,
    messages: list[dict],
    output: str | None,
    usage: dict | None = None,
    latency_ms: float | None = None,
    error: str | None = None,
) -> None:
    """Record one LLM chat completion (called from app/core/llm.py)."""
    client = _get_client()
    if client is None:
        return
    try:
        gen = client.start_observation(name=name, as_type="generation")
        usage_details = None
        if usage:
            usage_details = {
                "input": int(usage.get("prompt_tokens") or 0),
                "output": int(usage.get("completion_tokens") or 0),
            }
        gen.update(
            model=model,
            input={"messages": messages},
            output=output if error is None else f"ERROR: {error}",
            usage_details=usage_details,
            metadata={"latency_ms": round(latency_ms, 1) if latency_ms is not None else None},
            level="ERROR" if error else "DEFAULT",
        )
        gen.end()
    except Exception as exc:  # noqa: BLE001
        logger.debug("langfuse generation record failed: %s", exc)
