"""Eval runner: `python -m evals [--suite all|extraction|outreach|openings]`.

Prints a scorecard and appends a JSON snapshot to evals/history/ so scores can
be diffed across prompt/model changes. Needs GROQ_API_KEY in the environment or
.env (run from the repo root). Exits non-zero with --gate if any headline metric
falls below its threshold (usable as a CI quality gate).
"""
import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.core import llm
from app.core.config import settings
from evals import extraction_eval, outreach_eval

_HISTORY = Path(__file__).parent / "history"

# --gate thresholds: intentionally below the observed baseline so the gate
# catches regressions without flaking on normal LLM variance.
_GATES = {
    ("extraction", "phone", "precision"): 0.8,
    ("extraction", "email", "precision"): 0.8,
    ("outreach", "intent_accuracy", None): 0.75,
    ("outreach", "reminder_accuracy", None): 0.75,
}


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}" if v <= 1 else f"{v:.2f}"


def _print_scorecard(report: dict) -> None:
    print(f"\n=== Scopio eval scorecard · model={report['model']} · {report['ran_at']} ===")
    ex = report.get("extraction")
    if ex:
        print(f"\n[extraction] {ex['n_cases']} frozen-corpus cases (incl. hallucination traps)")
        for f in ("phone", "email", "socials"):
            t = ex[f]
            print(f"  {f:<8} precision {_fmt(t['precision'])}  recall {_fmt(t['recall'])}  "
                  f"hallucination {_fmt(t['hallucination_rate'])}")
        print(f"  hours    loose-match {_fmt(ex['opening_hours_loose_acc'])}")
        print(f"  address  loose-match {_fmt(ex['address_loose_acc'])}")
    ot = report.get("outreach")
    if ot:
        print(f"\n[outreach] {ot['n_cases']} labeled reply scenarios")
        print(f"  intent accuracy        {_fmt(ot['intent_accuracy'])}")
        print(f"  set_reminder accuracy  {_fmt(ot['reminder_accuracy'])}")
        print(f"  callback_days accuracy {_fmt(ot['callback_days_accuracy'])}")
        if ot.get("judge"):
            j = ot["judge"]
            print(f"  judge: personalized {j['personalized_avg']:.1f}/5 · relevant "
                  f"{j['relevant_avg']:.1f}/5 · CTA {_fmt(j['clear_cta_rate'])} · "
                  f"honest {_fmt(j['honest_rate'])}")
    op = report.get("openings")
    if op:
        print(f"\n[openings] {op['n_cases']} cold-opening cases")
        print(f"  opt-out line present   {_fmt(op['optout_present'])}   (compliance)")
        print(f"  names the business     {_fmt(op['names_business'])}")
        print(f"  no placeholders        {_fmt(op['no_placeholders'])}")
        print(f"  channel length ok      {_fmt(op['length_ok'])}")
        if op.get("judge"):
            j = op["judge"]
            print(f"  judge: personalized {j['personalized_avg']:.1f}/5 · relevant "
                  f"{j['relevant_avg']:.1f}/5 · CTA {_fmt(j['clear_cta_rate'])} · "
                  f"honest {_fmt(j['honest_rate'])}")
    print()


def _check_gates(report: dict) -> list[str]:
    failures = []
    for (suite, key, sub), threshold in _GATES.items():
        block = report.get(suite)
        if not block:
            continue
        value = block[key][sub] if sub else block[key]
        if value is not None and value < threshold:
            failures.append(f"{suite}.{key}{'.' + sub if sub else ''} = {value:.2f} < {threshold}")
    return failures


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run Scopio agent evals.")
    parser.add_argument("--suite", default="all",
                        choices=["all", "extraction", "outreach", "openings"])
    parser.add_argument("--limit", type=int, default=None, help="cap cases per suite (smoke run)")
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 if any headline metric is below its threshold")
    args = parser.parse_args()

    if not llm.llm_available():
        print("GROQ_API_KEY not configured — evals need the LLM. Add it to .env and rerun.")
        return 2

    report: dict = {
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": settings.outreach_model,
    }
    if args.suite in ("all", "extraction"):
        print("running extraction suite…")
        report["extraction"] = await extraction_eval.run(args.limit)
    if args.suite in ("all", "outreach"):
        print("running outreach suite…")
        report["outreach"] = await outreach_eval.run_conversations(args.limit)
    if args.suite in ("all", "openings"):
        print("running openings suite…")
        report["openings"] = await outreach_eval.run_openings(args.limit)

    _HISTORY.mkdir(exist_ok=True)
    stamp = report["ran_at"].replace(":", "-").replace("+00-00", "Z")
    out = _HISTORY / f"scorecard-{stamp}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    _print_scorecard(report)
    print(f"saved: {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")

    if args.gate:
        failures = _check_gates(report)
        if failures:
            print("\nGATE FAILED:\n  " + "\n  ".join(failures))
            return 1
        print("gates: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
