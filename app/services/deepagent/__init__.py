"""LangGraph deep research agent: given a business, gather its public profile from
Tavily web search + its own website, and synthesize contacts + details with the LLM.

Replaces the single website-read extractor as the enrichment brain when a Tavily key is
configured. `research_business(...)` returns an `ExtractionResult`, so the enrichment
pipeline's write-back path is unchanged.
"""
from app.services.deepagent.graph import deep_research_available, research_business

__all__ = ["research_business", "deep_research_available"]
