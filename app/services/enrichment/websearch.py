"""Find a business's official website via an allowed web-search API.

Brave Search API (if BRAVE_API_KEY set) is the official, robust path. Without a
key, a best-effort DuckDuckGo HTML query is used (no key, may be rate-limited).
Neither scrapes Google. Social/directory domains are skipped so we return the
business's OWN site, which the enrichment step can then read.
"""
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Domains that are NOT a business's own site (socials + directories/aggregators).
# We only treat the business's OWN website as a readable source; directory pages
# are skipped (their ToS varies and the data is second-hand).
_DENY = {
    # social
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "linkedin.com", "pinterest.com",
    # search / maps / encyclopaedia
    "google.com", "google.co.in", "maps.google.com", "wikipedia.org",
    # directories / aggregators / listing sites
    "justdial.com", "indiamart.com", "sulekha.com", "yelp.com", "tripadvisor.com",
    "tripadvisor.in", "zomato.com", "swiggy.com", "yellowpages.in", "magicpin.in",
    "practo.com", "99acres.com", "yappe.in", "doctar.in", "medindia.net",
    "hexahealth.com", "bankbranchin.com", "cybo.com", "exportersindia.com",
    "worldorgs.com", "infomint.co.in", "bajajfinservhealth.in", "bookmyshow.com",
    "lybrate.com", "sehat.com", "drdata.in", "clinicspots.com", "credihealth.com",
    "tradeindia.com", "indiacom.com", "asklaila.com", "grotal.com", "fundoodata.com",
    "getblood.in", "friendtoall.com", "bloodbank.in", "nearbuy.com", "dialindia.com",
}


def is_business_site(url: str) -> bool:
    """True if the URL looks like a business's own site (not a social/directory)."""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    host = host[4:] if host.startswith("www.") else host
    if not host:
        return False
    return not any(host == d or host.endswith("." + d) for d in _DENY)


def build_query(name: str, locality: str | None) -> str:
    return f"{name} {locality}".strip() if locality else name.strip()


async def _brave(query: str) -> str | None:
    headers = {"X-Subscription-Token": settings.brave_api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 5},
            headers=headers,
        )
        resp.raise_for_status()
        for item in resp.json().get("web", {}).get("results", []):
            url = item.get("url")
            if url and is_business_site(url):
                return url
    return None


def _decode_ddg_href(href: str) -> str:
    """DDG result links are often /l/?uddg=<encoded-url> redirects."""
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return href


async def _ddg(query: str) -> str | None:
    headers = {"User-Agent": settings.http_user_agent}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        resp.raise_for_status()
        html = resp.text
    for href in re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', html):
        url = _decode_ddg_href(href)
        if url.startswith("http") and is_business_site(url):
            return url
    return None


async def find_website(name: str, locality: str | None = None) -> str | None:
    query = build_query(name, locality)
    try:
        if settings.brave_api_key:
            return await _brave(query)
        return await _ddg(query)
    except httpx.HTTPError as exc:
        logger.warning("web search failed for %r: %s", query, exc)
        return None
