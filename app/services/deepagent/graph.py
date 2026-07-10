"""The deep research agent as a small LangGraph state machine.

Flow:  find_site → read_site → search ⟲ (loop up to the search cap) → synthesize → END

- find_site: if we don't have the business's website, web-search for it.
- read_site: read the business's own site (highest-signal source).
- search:    run one Tavily query, accumulate the snippets into a corpus.
- synthesize: one LLM pass over the whole corpus → contacts + rich profile.

Returns an `ExtractionResult` (reusing the enrichment schema/mapping) so the pipeline's
write-back is unchanged. Everything degrades to a website-only read when Tavily is absent.
"""
import json
import logging
from typing import TypedDict

from app.core import llm, telemetry
from app.core.config import settings
from app.services.deepagent import tools
from app.services.enrichment.extractor import (
    ExtractionResult,
    _result_from_profile,
)

logger = logging.getLogger(__name__)

_CORPUS_MAX = 7000  # chars fed to the model — enough for contacts/profile, bounds tokens

_SYNTH_SYSTEM = (
    "You are a business researcher. From the collected text (the business's own website "
    "plus web-search snippets about it), extract the business's public profile. Return ONLY "
    "what is clearly supported by the text; use null for anything not stated. NEVER invent "
    "or guess — especially phone numbers and emails. Prefer details from the business's OWN "
    "website over third-party snippets. Respond ONLY with a JSON object with keys: phone, "
    "email, opening_hours, description (one short factual sentence), address (string|null "
    "each), socials (object with optional facebook/instagram/twitter/youtube/linkedin URL "
    "strings), and confidence (0..1 that the contact details truly belong to this business). "
    "No prose, no markdown."
)


class ResearchState(TypedDict, total=False):
    name: str
    category: str | None
    locality: str | None
    website: str | None
    queries: list[str]        # remaining search queries
    corpus: list[str]         # gathered text chunks
    searches_used: int
    result: ExtractionResult


def deep_research_available() -> bool:
    """True when the deep agent can actually run (needs both an LLM and a Tavily key)."""
    return llm.llm_available() and tools.tavily_available()


def _seed_queries(name: str, locality: str | None, keywords: list[str] | None) -> list[str]:
    place = f" {locality}" if locality else ""
    if keywords:
        return [f"{name}{place} {kw}" for kw in keywords]
    return [
        f"{name}{place} contact phone email",
        f"{name}{place} official website opening hours",
    ]


# --- graph nodes -------------------------------------------------------------

async def _find_site(state: ResearchState) -> ResearchState:
    if state.get("website"):
        return {}
    place = f" {state['locality']}" if state.get("locality") else ""
    results = await tools.tavily_search(f"{state['name']}{place} official website", max_results=5)
    site = tools.first_business_site(results)
    used = state.get("searches_used", 0) + 1
    # Keep any snippets we already pulled so a failed site-find still adds signal.
    corpus = list(state.get("corpus", []))
    corpus.extend(r["content"] for r in results if r.get("content"))
    return {"website": site, "searches_used": used, "corpus": corpus}


async def _read_site(state: ResearchState) -> ResearchState:
    if not state.get("website"):
        return {}
    text = await tools.read_website(state["website"])
    if not text:
        return {}
    corpus = list(state.get("corpus", []))
    corpus.append(f"[Business website: {state['website']}]\n{text}")
    return {"corpus": corpus}


async def _search(state: ResearchState) -> ResearchState:
    queries = list(state.get("queries", []))
    if not queries:
        return {}
    query = queries.pop(0)
    results = await tools.tavily_search(query, max_results=5)
    corpus = list(state.get("corpus", []))
    corpus.extend(f"[{r['title']}] {r['content']}" for r in results if r.get("content"))
    return {"queries": queries, "corpus": corpus, "searches_used": state.get("searches_used", 0) + 1}


def _route_after_search(state: ResearchState) -> str:
    if state.get("queries") and state.get("searches_used", 0) < settings.deep_research_max_searches:
        return "search"
    return "synthesize"


async def _synthesize(state: ResearchState) -> ResearchState:
    corpus = "\n\n".join(state.get("corpus", [])).strip()[:_CORPUS_MAX]
    if not corpus:
        return {"result": ExtractionResult(note="deep agent: no data gathered")}
    user = (
        f"Business name: {state['name']}\n"
        f"Category: {state.get('category') or 'unknown'}\n"
        f"Location hint: {state.get('locality') or 'unknown'}\n\n"
        f"Collected text:\n{corpus}\n\n"
        "Extract the business's full public profile as JSON."
    )
    try:
        content = await llm.chat(
            [
                {"role": "system", "content": _SYNTH_SYSTEM},
                {"role": "user", "content": user},
            ],
            json_mode=True,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            max_tokens=800,
            timeout=settings.llm_timeout,
        )
        data = json.loads(content)
    except Exception as exc:  # noqa: BLE001 — fall back to an empty result, never crash the batch
        logger.warning("deep agent synthesis failed for %s: %s", state["name"], exc)
        return {"result": ExtractionResult(note=f"deep agent error: {exc}")}
    return {"result": _result_from_profile(data, note=f"deepagent:{settings.llm_model}")}


async def synthesize_from_corpus(
    name: str,
    corpus: list[str],
    category: str | None = None,
    locality: str | None = None,
) -> ExtractionResult:
    """Run ONLY the synthesis step over an already-gathered corpus.

    Public entry point for the eval harness (evals/): lets us score the extraction
    LLM against frozen corpora with known ground truth, hermetically (no Tavily,
    no website fetches) and therefore repeatably.
    """
    out = await _synthesize(
        {"name": name, "category": category, "locality": locality, "corpus": corpus}
    )
    return out.get("result") or ExtractionResult(note="deep agent: no result")


_GRAPH = None


def _build_graph():
    """Compile the LangGraph state machine once (lazy — LangGraph imported on first use)."""
    global _GRAPH
    if _GRAPH is not None:
        return _GRAPH
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(ResearchState)
    g.add_node("find_site", _find_site)
    g.add_node("read_site", _read_site)
    g.add_node("search", _search)
    g.add_node("synthesize", _synthesize)
    g.add_edge(START, "find_site")
    g.add_edge("find_site", "read_site")
    g.add_edge("read_site", "search")
    g.add_conditional_edges("search", _route_after_search, {"search": "search", "synthesize": "synthesize"})
    g.add_edge("synthesize", END)
    _GRAPH = g.compile()
    return _GRAPH


async def research_business(
    name: str,
    category: str | None = None,
    locality: str | None = None,
    website: str | None = None,
    keywords: list[str] | None = None,
) -> ExtractionResult:
    """Run the deep research agent for one business and return an ExtractionResult."""
    graph = _build_graph()
    initial: ResearchState = {
        "name": name,
        "category": category,
        "locality": locality,
        "website": website,
        "queries": _seed_queries(name, locality, keywords),
        "corpus": [],
        "searches_used": 0,
    }
    # One trace per researched business: the find→read→search→synthesize waterfall
    # (and the synthesis LLM call, with token usage) lands under this span.
    with telemetry.span(
        "deep-research",
        input={"name": name, "category": category, "locality": locality, "website": website},
    ) as s:
        final = await graph.ainvoke(initial)
        result = final.get("result")
        if result is None:
            result = ExtractionResult(note="deep agent: no result")
        # Surface the website the agent found so the caller can persist it.
        if final.get("website") and not website:
            result.details.setdefault("website", final["website"])
        if s:
            s.update(output={
                "phone": result.phone, "email": result.email,
                "confidence": result.confidence, "note": result.note,
                "searches_used": final.get("searches_used", 0),
            })
    return result
