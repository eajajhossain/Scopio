"""Scopio eval harness — measures agent quality so changes can be regression-tested.

Suites (run with `python -m evals`):
- extraction: deep-agent synthesis step vs frozen corpora with known ground truth
  (field-level precision/recall + hallucination rate, incl. trap cases).
- outreach:   conversational agent vs labeled reply scenarios (intent /
  set_reminder / callback_days exact-match accuracy) + LLM-as-judge reply quality.
- openings:   cold-opening generation (deterministic compliance checks + judge).

Each run prints a scorecard and appends a JSON snapshot to evals/history/ so
scores can be compared across prompt/model changes.
"""
