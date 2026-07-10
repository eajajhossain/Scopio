"""Outreach suites.

- outreach: agent.respond on labeled reply scenarios. intent / set_reminder /
  callback_days are structured outputs, so they're scored as plain classification
  accuracy — no judge needed. The reply text itself goes to the LLM judge.
- openings: generate_opening per channel. Compliance checks are deterministic
  (opt-out line present, business name mentioned, no template placeholders,
  channel length limits); quality goes to the judge.
"""
import asyncio
import json
import re
from pathlib import Path

from app.services.outreach import agent
from app.services.outreach.playbook import SenderContext
from evals.judge import judge_message
from evals.scoring import score_outreach_case

_DATASETS = Path(__file__).parent / "datasets"
_CONCURRENCY = 1   # serial: each case = agent call + judge call; free-tier TPM is tight
_CASE_PAUSE = 2.0  # seconds between cases, spreads token usage across TPM windows

# Fixed persona for every eval run — scores must not drift because a tenant profile changed.
_CTX = SenderContext(
    sender_name="Akash",
    company_name="LeadPilot",
    services=(
        "We set up a 24/7 AI assistant for small businesses that answers customer "
        "messages, takes bookings/orders automatically, and follows up with leads — "
        "no technical work needed on the business's side."
    ),
)

_MAX_LEN = {"sms": 400, "whatsapp": 700, "email": 1600}


def _load(name: str, limit: int | None) -> list[dict]:
    path = _DATASETS / name
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return cases[:limit] if limit else cases


async def run_conversations(limit: int | None = None) -> dict:
    cases = _load("outreach.jsonl", limit)
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def one(case: dict) -> dict:
        async with sem:
            predicted = await agent.respond(case["business_info"], case["transcript"], "email", _CTX)
            try:
                quality = await judge_message(
                    predicted["reply"], case["business_info"],
                    context="Mid-conversation reply. Last message from the owner: "
                            + case["transcript"][-1]["text"],
                )
            except Exception as exc:  # noqa: BLE001 — a judge failure shouldn't sink the suite
                quality = {"error": str(exc)}
            await asyncio.sleep(_CASE_PAUSE)
        marks = score_outreach_case(case["expected"], predicted)
        return {"id": case["id"], **marks, "predicted": predicted, "quality": quality}

    results = await asyncio.gather(*(one(c) for c in cases))
    n = len(results)
    judged = [r["quality"] for r in results if "error" not in r["quality"]]
    return {
        "suite": "outreach",
        "n_cases": n,
        "intent_accuracy": sum(r["intent_ok"] for r in results) / n,
        "reminder_accuracy": sum(r["reminder_ok"] for r in results) / n,
        "callback_days_accuracy": sum(r["days_ok"] for r in results) / n,
        "judge": _judge_rollup(judged),
        "cases": results,
    }


async def run_openings(limit: int | None = None) -> dict:
    cases = _load("openings.jsonl", limit)
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def one(case: dict) -> dict:
        channel, info = case["channel"], case["business_info"]
        async with sem:
            message = await agent.generate_opening(info, channel, _CTX)
            try:
                quality = await judge_message(
                    message, info, context=f"FIRST cold outreach message, channel={channel}."
                )
            except Exception as exc:  # noqa: BLE001
                quality = {"error": str(exc)}
            await asyncio.sleep(_CASE_PAUSE)
        checks = {
            "optout_present": "reply stop" in message.lower(),
            "names_business": info["name"].split()[0].lower() in message.lower(),
            "no_placeholders": not re.search(r"\[[A-Za-z _]+\]|\{\{", message),
            "length_ok": len(message) <= _MAX_LEN[channel],
        }
        return {"id": case["id"], "checks": checks, "message": message, "quality": quality}

    results = await asyncio.gather(*(one(c) for c in cases))
    n = len(results)
    judged = [r["quality"] for r in results if "error" not in r["quality"]]
    check_keys = ["optout_present", "names_business", "no_placeholders", "length_ok"]
    return {
        "suite": "openings",
        "n_cases": n,
        **{k: sum(r["checks"][k] for r in results) / n for k in check_keys},
        "judge": _judge_rollup(judged),
        "cases": results,
    }


def _judge_rollup(judged: list[dict]) -> dict | None:
    if not judged:
        return None
    n = len(judged)
    return {
        "n_judged": n,
        "personalized_avg": sum(q["personalized"] for q in judged) / n,
        "relevant_avg": sum(q["relevant"] for q in judged) / n,
        "clear_cta_rate": sum(q["clear_cta"] for q in judged) / n,
        "honest_rate": sum(q["honest"] for q in judged) / n,
    }
