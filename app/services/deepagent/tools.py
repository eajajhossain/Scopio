"""Tools the deep research agent can call: web search (Tavily) and website reading.

Both fail soft — they return empty results rather than raising, so a flaky search or a
dead website degrades the research instead of crashing the enrichment batch.
"""
import logging

from app.core.config import settings
from app.services.enrichment.fetcher import fetch_site_text
from app.services.enrichment.websearch import is_business_site

logger = logging.getLogger(__name__)


def tavily_available() -> bool:
    return bool(settings.tavily_api_key)


async def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Web search via Tavily. Returns [{title, url, content}]; [] if unavailable/failed."""
    if not tavily_available():
        return []
    try:
        # Lazy import so the app loads even if the SDK isn't installed yet.
        from tavily import AsyncTavilyClient

        client = AsyncTavilyClient(api_key=settings.tavily_api_key)
        resp = await client.search(query=query, max_results=max_results, search_depth="basic")
        results = resp.get("results", []) if isinstance(resp, dict) else []
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
            for r in results
        ]
    except Exception as exc:  # noqa: BLE001 — search is best-effort
        logger.warning("tavily search failed for %r: %s", query, exc)
        return []


async def read_website(url: str) -> str:
    """Fetch cleaned text from a business's own site. '' on failure."""
    if not url:
        return ""
    try:
        return await fetch_site_text(url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("read_website failed for %s: %s", url, exc)
        return ""


def first_business_site(results: list[dict]) -> str | None:
    """Pick the first result URL that looks like the business's OWN site (not a directory)."""
    for r in results:
        url = r.get("url", "")
        if url.startswith("http") and is_business_site(url):
            return url
    return None
