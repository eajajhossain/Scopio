"""LLM-as-judge for free-text agent output (reply / opening quality).

Structured fields are scored exactly in scoring.py — the judge only covers what
can't be string-matched: personalization, relevance, tone, honesty. Temperature 0
and a JSON rubric keep it as repeatable as an LLM judge gets. Caveat (stated in
the scorecard): the judge is the same model family as the agent, so treat judge
scores as a regression signal, not an absolute grade.
"""
import json

from app.core import llm

_JUDGE_SYSTEM = (
    "You are a strict evaluator of B2B cold-outreach messages. Score the MESSAGE "
    "sent to the business below. Respond ONLY with a JSON object: "
    '{"personalized": 1-5 (5 = clearly written for THIS business type/name, 1 = fully generic), '
    '"relevant": 1-5 (does it pitch benefits this business would actually care about?), '
    '"clear_cta": true|false (does it propose a concrete next step, e.g. a short call?), '
    '"honest": true|false (false if it invents results, fake numbers, false urgency, or pressure), '
    '"issues": "one short sentence, or empty string"}'
)


async def judge_message(message: str, business_info: dict, context: str) -> dict:
    """Score one outbound message. Raises on LLM failure (caller decides)."""
    user = (
        f"Business being contacted: {json.dumps(business_info, ensure_ascii=False)}\n"
        f"Context: {context}\n\n"
        f"MESSAGE:\n{message}"
    )
    content = await llm.chat(
        [{"role": "system", "content": _JUDGE_SYSTEM}, {"role": "user", "content": user}],
        json_mode=True,
        temperature=0.0,
        max_tokens=200,
    )
    data = json.loads(content)
    return {
        "personalized": int(data.get("personalized") or 0),
        "relevant": int(data.get("relevant") or 0),
        "clear_cta": bool(data.get("clear_cta")),
        "honest": bool(data.get("honest")),
        "issues": str(data.get("issues") or ""),
    }
