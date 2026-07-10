
import html
import logging
import re

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_MAX_CHARS = 6000  # enough for contact info; keeps token cost down


def html_to_text(raw: str) -> str:
    no_scripts = _SCRIPT_STYLE.sub(" ", raw)
    no_tags = _TAG.sub(" ", no_scripts)
    text = html.unescape(no_tags)
    return _WS.sub(" ", text).strip()[:_MAX_CHARS]


def _normalize_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


async def fetch_site_text(website: str) -> str:
    """Return cleaned text from the homepage (+ a /contact guess). '' on failure."""
    base = _normalize_url(website).rstrip("/")
    headers = {"User-Agent": settings.http_user_agent}
    chunks: list[str] = []
    async with httpx.AsyncClient(
        timeout=settings.enrichment_fetch_timeout, follow_redirects=True, headers=headers
    ) as client:
        for url in (base, f"{base}/contact", f"{base}/contact-us"):
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and "html" in resp.headers.get("content-type", ""):
                    chunks.append(html_to_text(resp.text))
            except httpx.HTTPError as exc:
                logger.debug("fetch failed for %s: %s", url, exc)
        if not chunks:
            return ""
    return html_to_text("  ".join(chunks)) if chunks else ""
