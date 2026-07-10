# Scopio — Phase 4 Design: Multi-Source Aggregation Engine (the "moat")

> **Goal:** get as close to Google-Maps-level completeness as possible **using only free,
> legally-usable sources** — no scraping of Google or any ToS-protected site.
>
> **Honest ceiling (read first):** this engine makes any business with *some* online presence
> (a website, a social page, a directory listing) rich and complete. It **cannot** match Google
> for businesses with **zero online footprint** — that data exists only in Google's paid database.
> So: "much closer to Google," not "equal to Google." Anyone who promises free + complete + legal
> is doing one of those three things falsely.

---

## 1. The core principle

**AI does not invent data — it gathers, reads, and merges it from sources that permit it.**

Google Maps is forbidden as a source (its ToS bans automated extraction). So we aggregate the
sources that *are* allowed, then use an LLM to read the messy results into clean fields. The
intelligence is in **collection + extraction + resolution**, not scraping a forbidden source.

```
                         ┌──────────────────────────────────────────┐
   address ──▶ Discovery │  OSM / Overpass (base list of businesses)  │
                         └───────────────────┬────────────────────────┘
                                             │  list of businesses (sparse)
                                             ▼
        ┌────────────────────  AGGREGATION ENGINE  ───────────────────────┐
        │  For each business, run all enabled SourceProviders in parallel: │
        │                                                                  │
        │   ① WebsiteSource     read the business's OWN site (LLM)   FREE  │
        │   ② WikidataSource    open knowledge base (SPARQL)         FREE  │
        │   ③ WebSearchSource   find a missing website, then ①    free key │
        │   ④ DirectorySource   Yelp / Foursquare Places          free key │
        │   ⑤ GooglePlaces      official Places API                  PAID  │
        │                                                                  │
        │              ▼ each returns a partial BusinessProfile            │
        │   ┌──────────────────────────────────────────────────────────┐ │
        │   │ Entity Resolution: match the same business across sources  │ │
        │   │ Merge: field-by-field, preferring higher-trust sources     │ │
        │   │ Score: data completeness + source-confidence per field     │ │
        │   └──────────────────────────────────────────────────────────┘ │
        └───────────────────────────────┬─────────────────────────────────┘
                                         ▼
                          enriched, scored business profile
                          (phone, email, hours, website, socials, logo, …)
```

---

## 2. The `SourceProvider` interface

Every source — free or paid, now or later — implements one small contract. The engine doesn't
care which sources exist; it runs whichever are enabled (by config/keys) and merges results.

```python
@dataclass
class BusinessProfile:
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    address: str | None = None
    opening_hours: str | None = None
    description: str | None = None
    socials: dict[str, str] = field(default_factory=dict)   # {facebook, instagram, ...}
    logo_url: str | None = None
    extra: dict = field(default_factory=dict)               # source-specific spillover
    field_confidence: dict[str, float] = field(default_factory=dict)

class SourceProvider(Protocol):
    name: str
    trust: float                       # 0..1 — how authoritative this source is
    enabled: bool                      # gated by config / API keys
    async def fetch(self, business: BusinessInput) -> BusinessProfile | None: ...
```

`business` carries what we already know (name, lat/lng, website?) so each source can look itself up.

---

## 3. The sources (in priority/trust order)

| # | Source | Cost | Legal basis | What it adds | Status |
|---|---|---|---|---|---|
| ① | **WebsiteSource** | **free** | the business's *own* site | phone, email, hours, description, socials, logo (LLM-read) | **building now** |
| ② | **WikidataSource** | **free** | open data (CC0), public SPARQL API | website, socials, description for listed businesses | next (free, no key) |
| ③ | **WebSearchSource** | free tier | Brave/Bing Search API (allowed) | finds a missing website → feeds ① | needs a free search key |
| ④ | **DirectorySource** | free tier | Yelp Fusion / Foursquare APIs (allowed) | phone, hours, ratings, photos | needs a free API key |
| ⑤ | **GooglePlacesSource** | **paid** | official Places API | the most complete data | drop-in when a key exists |

> **Why these and not Justdial/Google scraping?** Each source above either has an **API that permits
> programmatic use** or is **open data**. Justdial/IndiaMART/Google forbid automated extraction —
> using them would be the same trap we rejected. The engine is built **source-by-source, each
> respecting its own rules.**

### ① WebsiteSource — the biggest free win (implemented first)
The business's own website is fair game and information-rich. We already fetch it for phone/email;
Phase 4 deepens it: one LLM pass extracts a **full profile** — phone, email, opening hours, a short
description, social links, and a logo (`og:image`). For any business with a website, this alone
produces a near-Google-quality listing, for free.

### ③ WebSearchSource — closing the "no website in OSM" gap
Many businesses have a website that OSM just didn't record. A search query
(`"<name> <locality>"`) via an **allowed** search API (Brave has a free tier) finds the official
site, which then flows through ①. This is how we reach businesses OSM left bare — **legally**,
because search APIs permit automated queries (unlike scraping Google Maps).

---

## 4. Entity resolution & merge

Multiple sources describe the same business in slightly different ways. The engine must decide
"these are the same place" and merge them without conflict.

- **Match key:** normalized name + geocell (the same `dedup_key` we already use), plus fuzzy
  name match and phone-number match across sources.
- **Merge rule (field-by-field):** for each field, take the value from the **highest-`trust`
  source that has it**; record which source won in `field_confidence`. Never overwrite a
  high-trust value with a low-trust one.
- **Completeness score:** fraction of target fields filled, weighted by source trust. Surfaces
  "how complete is this listing" to the UI and prioritizes outreach to well-formed leads.

---

## 5. Data model additions

- **`business.details` (JSONB)** — the merged rich profile (hours, description, socials, logo,
  per-field source/confidence). Keeps the relational columns (phone/email/website) as the
  fast-query fields; everything else lives in `details`. *(One additive column — implemented now.)*
- Later: a `business_source_fact` table if we want full provenance per field (which source gave
  which value, when). Deferred until we have ≥3 live sources.

---

## 6. Execution & cost control

- Sources run **in parallel per business** (asyncio), bounded by a concurrency cap.
- **Free sources always on; keyed/paid sources only if their key is set** (`enabled`).
- Per-tenant **monthly budget guard** for any paid source (Google), same pattern as enrichment.
- Results cached on the business (`details` + `enriched_at`); re-aggregate only on demand or after a TTL.

---

## 7. Phased rollout

| Step | Sources live | Result |
|---|---|---|
| **4a (now)** | ① WebsiteSource (deep profile) | Businesses with a website become rich/complete, free |
| 4b | + ② Wikidata | Notable businesses gain website/socials/description, free, no key |
| 4c | + ③ WebSearch (free key) | Reach businesses with no website in OSM |
| 4d | + ④ Yelp/Foursquare (free key) | Add ratings/photos/hours where covered |
| 4e | + ⑤ Google Places (paid) | Optional drop-in for maximum completeness |

---

## 8. Honest coverage expectation

| Business has… | Free engine result |
|---|---|
| Website (in OSM or found via search) | **Near-complete** — phone, email, hours, description, socials, logo |
| Only a social page | Partial — name, maybe phone/hours from the page |
| Listed on Wikidata / Yelp / Foursquare | Partial-to-good, depending on coverage |
| **No online presence at all** | **Name + location only** — no free source has more |

This is the realistic shape of "as close to Google as free + legal allows." The paid
GooglePlacesSource (⑤) is the only way to fully close the last gap, and it's already a drop-in.

---

*Implementation starts with **4a — WebsiteSource deep profile**: extend the LLM extractor to pull
the full profile, store it in `business.details`, and surface it in the dashboard.*
