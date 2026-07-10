"""Extraction suite: deep-agent synthesis vs frozen corpora with known truth.

Hermetic with respect to the web (no Tavily, no site fetches) — only the LLM
synthesis step runs, so a score change means the prompt/model changed, not the
internet. The dataset includes trap cases (no-contact corpus, decoy phone from
a different business, wrong-city snippets) that specifically measure the agent's
discipline about NOT inventing contact details.
"""
import asyncio
import json
from pathlib import Path

from app.services.deepagent.graph import synthesize_from_corpus
from evals.scoring import ExtractionScore

_DATASET = Path(__file__).parent / "datasets" / "extraction.jsonl"
_CONCURRENCY = 1   # serial: free-tier TPM is the bottleneck; llm.chat retries 429s
_CASE_PAUSE = 2.0  # seconds between cases, spreads token usage across TPM windows


def load_cases(limit: int | None = None) -> list[dict]:
    cases = [json.loads(line) for line in _DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    return cases[:limit] if limit else cases


async def run(limit: int | None = None) -> dict:
    cases = load_cases(limit)
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def one(case: dict) -> tuple[dict, dict]:
        async with sem:
            result = await synthesize_from_corpus(
                name=case["name"],
                corpus=case["corpus"],
                category=case.get("category"),
                locality=case.get("locality"),
            )
            await asyncio.sleep(_CASE_PAUSE)
        predicted = {
            "phone": result.phone,
            "email": result.email,
            "opening_hours": result.details.get("opening_hours"),
            "address": result.details.get("address"),
            "socials": result.details.get("socials") or {},
            "confidence": result.confidence,
        }
        return case, predicted

    score = ExtractionScore()
    for case, predicted in await asyncio.gather(*(one(c) for c in cases)):
        score.score_case(case["id"], case["expected"], predicted)
    return {"suite": "extraction", "n_cases": len(cases), **score.as_dict()}
