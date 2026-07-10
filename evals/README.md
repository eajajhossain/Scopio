# Scopio evals — how we measure agent quality

LLM agents regress silently: a prompt tweak or model swap can quietly break
extraction or make the sales agent misread intent. This harness turns "it seems
fine" into numbers that can be compared across changes.

```bash
python -m evals                 # full run (needs GROQ_API_KEY in .env)
python -m evals --suite outreach --limit 5   # quick smoke
python -m evals --gate          # exit 1 if any headline metric drops below threshold
```

Each run prints a scorecard and appends a JSON snapshot to `evals/history/`.

## Suite 1 — extraction (deep agent synthesis)

**What it tests:** the synthesize step of the LangGraph deep-research agent —
the LLM pass that turns gathered web text into a structured business profile.

**Method:** frozen research corpora with ground truth known by construction.
The suite is hermetic w.r.t. the web (no Tavily calls, no site fetches), so a
score change means the prompt/model changed — not the internet. Cases include
**hallucination traps**:

- `no-contact-trap` — corpus describes the business but contains no contact info
  → correct answer is all-null.
- `decoy-phone` — a directory snippet lists a *different* business's phone; the
  business's own site has the real one → must pick the right one.
- `wrong-city-trap` — snippets are about a same-named business in another city
  → must return nothing.

**Metrics:** field-level **precision / recall / hallucination-rate** for strict
fields (phone, email, socials — normalized before comparison: digit-suffix
matching for phones, so `+44 20…` == `020…`), loose containment match for
free-text fields (hours, address).

## Suite 2 — outreach (conversational sales agent)

**What it tests:** `agent.respond` on labeled reply scenarios (interested /
not-interested / price question / callback with a date / rude / multilingual /
multi-turn).

**Method:** the agent returns structured `intent`, `set_reminder`,
`callback_days` — so those are scored as **exact-match classification
accuracy**, no judge needed. Genuinely ambiguous turns accept multiple labels
(or `"any"`) so the score reflects real mistakes, not annotation pedantry.
The free-text reply is scored by an **LLM judge** (temperature 0, JSON rubric:
personalization, relevance, clear CTA, honesty).

## Suite 3 — openings (cold outreach generation)

Deterministic compliance checks — opt-out line present (CAN-SPAM/GDPR), business
name mentioned, no template placeholders, channel length limits — plus the same
LLM judge for quality.

## Honest caveats

- The judge is the same model family as the agent (Groq Llama), so judge scores
  are a **regression signal**, not an absolute grade. Structured-field metrics
  don't have this problem.
- LLM outputs vary run-to-run; `--gate` thresholds sit below the observed
  baseline so the gate catches real regressions without flaking on variance.
- Constructed corpora measure extraction discipline, not web-search quality —
  the find/read/search stages are exercised by integration tests and live runs.
