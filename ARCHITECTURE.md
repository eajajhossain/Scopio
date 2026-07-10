# Scopio — Architecture Plan

> **An AI Sales Outreach Platform**
> Enter any address in the world → discover local businesses → automatically reach out
> via call/message → explain AI services, answer questions → set a follow-up call reminder
> (with a video-meeting link) if interested.

This document is the original **architecture blueprint**. Phases 1–3 are now built; where the
implementation refined the plan, an **Update** note records it. The biggest such change: the
**Scheduling** service (rigid calendar booking) shipped as a lighter-weight **Reminders** service
(dated callbacks + an auto-generated Jitsi link + `.ics` invite) — see §3.5.

> **For the current, code-level picture** (full stack rationale, every file, and how the
> LangGraph deep-research agent was built), see [`TECH_GUIDE.md`](TECH_GUIDE.md). Features added
> after the original blueprint: **context-aware targeting**, the **LangGraph + Tavily deep agent**,
> GPS discovery, an **autonomous inbound-email agent** (an IMAP-polling worker that lets the AI
> hold the whole email conversation and auto-book callbacks), and a cross-tenant **admin dashboard**.

---

## 0. Locked Decisions (v1)

These were decided up front and drive the rest of the plan:

| # | Decision | Choice | Implication |
|---|---|---|---|
| 1 | **Backend stack** | **Python (FastAPI)** + Next.js frontend | ML-friendly; great for LLM/data work. Async-first. |
| 2 | **Outreach channels** | **All channels** (WhatsApp, Email, SMS, Voice) behind one common interface | Build the *channel-adapter abstraction* first; enable channels one at a time, gated by compliance. |
| 3 | **Launch region** | **India first (compliance focus); discovery + contact handling already worldwide** | Compliance = TRAI + **DND registry** + **DLT registration** + WhatsApp approved templates. Time zone IST, business-hours aware. **Update:** discovery works for any address globally, and phone/WhatsApp handling is internationalized via **libphonenumber** (`phonenumbers`) with `IN` as the default region for bare local numbers — so the product is not hardcoded to India. |
| 4 | **Business data source** | **Free & open: OpenStreetMap (Overpass API) + Nominatim** — **no Google, no scraping** | Fully free and commercially licensed. Coverage is patchy in small towns and phones are often missing — solved by the AI Enrichment Engine + manual import (see §3.2). |

> ### Why OpenStreetMap, not Google Maps
> Scraping the Google Maps app/website **violates Google's Terms of Service** (bans + legal risk + breaks
> constantly) and **cannot back a product you sell**. The **OpenStreetMap ecosystem** is the free, legal
> alternative, licensed for commercial use:
> - **Overpass API** — find all businesses within a radius/area → name, category, address, sometimes phone/website.
> - **Nominatim** — free geocoding (address → lat/long).
> - Free under fair-use limits, or **self-host** both for unlimited use.

> ### The data-completeness problem → solved by AI (phased)
> OSM is strong in cities but **sparse in small towns (e.g. Barasat)** and **phone numbers are often
> missing** — yet outreach needs contacts. The answer is **not** a single magic "find-business" model
> (an AI can't invent data that doesn't exist). The answer is an **AI-powered Aggregation & Enrichment
> Engine** that collects + reads + cleans data from many free public sources. This is the product's
> **moat** — and it is built in deliberate phases, not rushed into v1. Full design in **§3.2**.

> **"All channels" note:** we still *implement* channels one at a time, but the architecture
> (the `ChannelAdapter` interface + outreach queue) supports all from day one. Each channel is flipped on
> per-tenant/per-region via a feature flag once its compliance requirements are met (DLT for SMS,
> approved templates for WhatsApp, consent for voice).

---

## 1. Product Vision (in one line)

> A self-serve engine that finds local businesses near any address, qualifies them with an
> AI agent over phone/WhatsApp/email, and sets warm follow-up call reminders (with a video link)
> — fully automated, compliant, and observable.

### What "professional / industry-level" actually means here
For this to be something *everyone* can use (not just you, not just Barasat), it must be:

| Requirement | Why it matters |
|---|---|
| **Multi-tenant** | Many users, each with their own data, calendar, branding, and billing — isolated. |
| **Global** ✅ | Works for any address worldwide (discovery is global; phone/WhatsApp normalized via libphonenumber), not hardcoded to India. |
| **Compliant** | Cold-calling/messaging is heavily regulated (see §8). This is the #1 thing that kills these products. |
| **Observable** | Every call, message, and booking is logged, retryable, and auditable. |
| **Cost-controlled** | Maps APIs and voice AI cost real money per request — must be cached & budgeted. |
| **Human-in-the-loop** | A "kill switch" and approval gates so the AI never goes fully rogue. |

---

## 2. High-Level Architecture

```
                         ┌──────────────────────────────────────────┐
                         │              CLIENT LAYER                  │
                         │  Web App (dashboard) · Admin · Mobile      │
                         └───────────────────┬────────────────────────┘
                                             │  HTTPS / REST + WebSocket
                         ┌───────────────────▼────────────────────────┐
                         │              API GATEWAY                     │
                         │   Auth · Rate limiting · Multi-tenant routing│
                         └───────────────────┬────────────────────────┘
                                             │
   ┌──────────────┬───────────────┬─────────┴────────┬───────────────┬──────────────┐
   ▼              ▼               ▼                  ▼               ▼              ▼
┌────────┐  ┌──────────┐   ┌────────────┐    ┌────────────┐  ┌───────────┐  ┌──────────┐
│Discovery│ │Enrichment│   │  Outreach  │    │Conversation│  │ Reminders │  │   CRM /  │
│ Service │ │ Service  │   │  Engine    │    │ AI (Brain) │  │  Service  │  │ Pipeline │
└────┬────┘  └────┬─────┘   └─────┬──────┘    └─────┬──────┘  └─────┬─────┘  └────┬─────┘
     │            │               │                 │               │             │
     └────────────┴───────────────┴────────┬────────┴───────────────┴─────────────┘
                                            ▼
                         ┌──────────────────────────────────────────┐
                         │        EVENT BUS / JOB QUEUE              │
                         │   (async tasks, retries, rate limiting)   │
                         └───────────────────┬────────────────────────┘
                                             ▼
                         ┌──────────────────────────────────────────┐
                         │              DATA LAYER                    │
                         │  Postgres · Redis cache · Vector DB · Blob │
                         └──────────────────────────────────────────┘

        External: Maps/Places APIs · Telephony · WhatsApp/Email · Calendar · LLM
```

---

## 3. Core Modules (what each one does)

### 3.1 Discovery Service — "Find the *right* businesses"
**Job:** Take a location → return the businesses in that radius that are good leads for *this* seller.

- **Input:** a typed address **or one-tap GPS** (browser geolocation → lat/lng) + radius (e.g. 2 km).
- **Context-aware targeting:** before querying, an LLM reads the account's own services
  (`Tenant.services`) and derives a **target profile** — which business types are worth contacting
  (e.g. a cup/packaging supplier → cafes, bakeries, restaurants) mapped to OpenStreetMap tag filters.
  The Overpass query is then **restricted to those categories** so discovery returns relevant leads,
  not every shop in the area. No LLM key → it falls back to the broad "all businesses" search.
  (See `app/services/targeting.py` and `app/services/discovery/overpass.py::build_query`.)
- **Process:**
  1. Geocode the address → coordinates (skipped when GPS coordinates are supplied).
  2. Query **Overpass (OSM)** for the target business types in that area.
  3. Normalize results into a common `Business` shape.
  4. **De-duplicate** (same shop from multiple sources) and **cache** (don't re-query the same area
     for N days; the cache key includes the target filter so different sellers don't collide).
- **Provider options (pick by cost/coverage):**
  - **Google Places API** — best coverage, paid per request.
  - **OpenStreetMap / Overpass API** — free, weaker on phone numbers.
  - **Yelp Fusion / Foursquare / Mapbox** — regional strengths.
  - *Recommendation:* OSM as the cheap base layer, Google Places to enrich high-value leads.
- **Output:** list of `Business { name, category, address, lat/lng, phone?, website?, source }`.

### 3.2 AI Aggregation & Enrichment Engine — "Get contact + context" (THE MOAT)
**Job:** Solve the data-completeness problem — turn sparse OSM results into rich, contactable leads.
This is the part that makes Scopio better than "a person with a search engine," so it's our core IP.

**Key principle: AI does not *invent* data — it *collects, reads, and cleans* it.** There is no single
model that conjures a shop's phone number; the number exists on the shop's website, a directory, or a
social page, or not at all. The engine's intelligence is in aggregation + extraction + resolution.

**The 4 stages:**

| Stage | What it does | How |
|---|---|---|
| **1. Collect** | Gather listings for the area from *many* free public sources, not just one | OSM/Overpass base + **Tavily web search** + public business/social pages |
| **2. Extract** | A **LangGraph deep-research agent** finds the business's site, reads it, runs a few Tavily searches, then an LLM synthesizes clean fields (phone, email, hours, socials) | `app/services/deepagent/` — `find_site → read → search ⟲ → synthesize` |
| **3. Resolve** | **De-duplicate & merge** ("Maa Tara Sweets" == "Maa Tara Sweet Shop") into one record | Fuzzy match + embeddings on name/address/geo |
| **4. Score** | Rate **"good fit for AI services?"** + a **data-confidence score** | Lightweight classifier / heuristics |

- **Validate** phone numbers (format, line type) and emails (deliverability) before outreach.
- **Manual import fallback:** users can add businesses or upload a CSV to fill gaps cheaply.
- Outreach prioritizes leads with high fit + high confidence.
- **Candidate selection / batching:** each Enrich run takes a capped batch
  (`ENRICHMENT_MAX_BUSINESSES`) and **prioritizes never-tried businesses**, skipping any enriched
  within `ENRICHMENT_RECHECK_DAYS` (default 14). This makes repeated runs march through the backlog
  rather than re-grinding the same alphabetically-first rows — important because most businesses
  permanently lack an email, so a naive "missing any field" filter would re-select the same few
  forever. (See `app/services/enrichment/pipeline.py::find_candidates`.)
- **Pluggable research/extraction:** when a `TAVILY_API_KEY` is set, the **LangGraph deep agent**
  (Tavily + website read + LLM synthesis) is used. Without it, the engine falls back to the simpler
  website-read extractor, which itself picks the best available LLM: Claude > OpenAI-compatible (Groq)
  > regex, by which keys are configured. The conversation agent keeps its own fast endpoint
  (`OUTREACH_BASE_URL`) so chat stays snappy regardless of the research LLM.

> ⚠️ **Legal, per source:** each source has its own ToS/robots.txt. Justdial/Google/etc. often forbid
> automated collection — same trap as scraping Maps. The engine is built **source-by-source, respecting
> each one's rules**, which is exactly why it's phased (§9) and not a rushed v1 feature.

> **Build order:** v1 = OSM + manual import (prove the pipeline). v2 = LLM website enrichment (biggest
> bang for the buck). v3 = full multi-source aggregation + dedup + fit-scoring (the moat).

### 3.3 Outreach Engine — "Make contact"
**Job:** Reach the business over the right channel, at the right time, within limits.

- **Channels:** Voice call (AI agent), WhatsApp, SMS, Email — pluggable adapters.
- **Channel strategy:** start with the cheapest/least-intrusive (e.g. WhatsApp/email) → escalate to call only on interest or no-response, *and only where legally allowed*.
- **Scheduling rules:** respect business hours, time zones, daily caps, and **Do-Not-Contact lists**.
- **Retries & backoff:** no answer → retry later; opt-out → never contact again.
- Every attempt is a **job on the queue** so it's retryable and rate-limited.

### 3.4 Conversation AI — "The Brain"
**Job:** Hold the actual conversation — introduce, explain services, answer questions, detect interest.

- **LLM-driven agent** (latest Claude model) with a structured **system prompt + playbook**:
  - Who it is, what Scopio offers, tone, guardrails ("never promise pricing you can't honor").
  - **Tools the agent can call:** `getServiceInfo`, `setReminder` (dated callback + meeting link), `markNotInterested`, `escalateToHuman`. *(Update: shipped as the agent emitting `set_reminder` + `callback_days` in its structured reply, which the outreach service turns into a Reminder — see §3.5.)*
- **For voice:** Speech-to-Text → LLM → Text-to-Speech pipeline (or an integrated voice-AI provider).
- **Knowledge base (RAG):** your services, FAQs, pricing tiers stored in a **vector DB** so answers are grounded, not hallucinated.
- **Interest detection:** classifies the conversation → `interested / not_interested / callback / do_not_contact`.
- **Guardrails:** disclosure that it's an AI (legally required in many places), no overpromising, hand-off to human on edge cases.

### 3.5 Reminders Service — "Remember to call them" *(Update: replaces "Scheduling")*
**Job:** When interest is detected, record a dated follow-up call — no rigid calendar booking.

> **Why the pivot:** forcing a fixed slot mid-chat is high-friction and needs a calendar account.
> A **reminder** ("call them Friday ~10am") captures the intent, keeps the door open, and still
> gives both sides a way to meet — for free, with no account.

- The AI emits `set_reminder` + `callback_days` in its reply; the service creates a **Reminder**
  (due date in the tenant's timezone, channel, contact stored in international E.164 form, note).
- **Auto-mints a unique Jitsi Meet room** both sides join, and generates a standard **`.ics`**
  invite (with a 15-min device alarm) so the call lands in any calendar — Apple/Google/Outlook.
- Each reminder also exposes a **`google_calendar_url`** — a one-tap "Add to Google Calendar"
  template link (same 30-min window + Jitsi room). The `.ics` and Google-Calendar links live in the
  dashboard/API only, deliberately **not** in the owner's outreach message.
- Flips the business's CRM status to `callback_scheduled`; surfaces in the dashboard's Reminders
  panel (edit date, 🎥 join, 🔗 copy link, 📅 add-to-calendar `.ics`, 📆 Google Calendar, 📞 call now,
  mark done) with overdue flagging.
- Endpoints: `POST/GET/PATCH/DELETE /reminders` + `GET /reminders/{id}/invite.ics`; the
  `google_calendar_url` is returned as a field on every reminder.
- Built behind the same provider-agnostic idea as everything else: `JITSI_BASE_URL` can point at a
  self-hosted room server, and a full Google/Microsoft calendar sync can drop in later.

### 3.6 CRM / Pipeline — "Track everything"
**Job:** Single source of truth for every lead and its journey.

- Lead lifecycle: `discovered → enriched → contacted → interested → callback_scheduled / not_interested / do_not_contact`.
- Full **conversation transcripts**, call recordings, outcomes, and timestamps.
- Dashboard: funnel metrics, cost per lead, callbacks set / callback rate, channel performance.

---

## 4. Data Flow (the happy path)

```
1. User enters "Barasat, 700125, radius 2km" (or taps "Use my location") in the dashboard
2. Targeting → LLM reads the seller's services → target business types (e.g. cafes, bakeries)
3. Discovery → geocode (Nominatim, skipped for GPS) → Overpass query filtered to those types → cached
4. Deep research (LangGraph + Tavily) → per business: find site → read → web-search → synthesize
   contacts/profile → dedup → score → qualified leads
6. User reviews & approves the list  (human-in-the-loop gate)
7. Outreach Engine → queues jobs respecting caps + business hours + DND
8. For each lead → Conversation AI introduces Scopio over WhatsApp/call
9. Lead asks "what do you offer?" → RAG-grounded answer
10. Lead shows interest → AI sets a follow-up call reminder (set_reminder + callback_days)
11. Reminders → mints a Jitsi link + .ics invite → shares the join link in the AI's reply
12. CRM updates status to callback_scheduled → reminder shows on the dashboard, overdue-flagged
```

---

## 5. Recommended Tech Stack

| Layer | Recommendation | Notes |
|---|---|---|
| **Frontend** | Next.js (React) + TypeScript | Dashboard, SSR, good DX. |
| **Backend API** | **Python (FastAPI)** ✅ locked | ML-heavy friendly, async-first, ideal for LLM/data work. |
| **Job queue / async** | **Celery or ARQ (Redis)**, or Temporal | Python-native async queues. Outreach must be retryable + rate-limited. Temporal for long multi-step workflows. |
| **Primary DB** | PostgreSQL | Leads, tenants, bookings, transcripts. |
| **Cache** | Redis | Geocoding/Places cache, rate-limit counters, sessions. |
| **Vector DB** | pgvector (start) → Pinecone/Qdrant (scale) | RAG knowledge base for the AI agent. |
| **Object storage** | S3 / Cloudflare R2 | Call recordings, transcripts, exports. |
| **LLM** | OpenAI-compatible (Groq by default; Claude / Gemini / OpenRouter drop-in) | The brain: targeting, deep-research synthesis, and the conversation agent. |
| **Deep research agent** | **LangGraph** (orchestration) + **Tavily** (web search) | Per-business research graph; falls back to website-read when no Tavily key. |
| **Voice AI** | Twilio Voice + STT/TTS, or Vapi / Retell / Bland | Pluggable — abstract behind an interface. |
| **Messaging** | WhatsApp Business API (Meta/Twilio), SMS via Twilio, email via SES/SendGrid | |
| **Business data** | **OSM/Overpass + Nominatim** (free) ✅ locked; deep-agent enrichment for contacts | No Google/scraping. Self-host Overpass/Nominatim to remove rate limits. |
| **Calendar** | Google Calendar API, Microsoft Graph, Cal.com | |
| **Auth** | Clerk / Auth0 / Supabase Auth | Multi-tenant from day one. |
| **Infra** | Docker + a cloud (AWS/GCP/Render/Fly.io) | IaC with Terraform when you scale. |
| **Observability** | OpenTelemetry + Grafana/Sentry | Logs, traces, errors, cost dashboards. |

> **Design principle:** wrap every external provider (Places, Voice, Calendar, LLM) behind a thin
> **adapter interface**. You will swap providers as you learn costs and coverage — don't hardcode them.

---

## 6. Multi-Tenancy & Security

- **Tenant isolation:** every row carries a `tenant_id`, enforced by **Postgres Row-Level Security**.
  Each request/job sets the tenant via `SELECT set_config('app.tenant_id', …, false)` and RLS policies
  filter every query by it.
  - ⚠️ **Connection pinning (important):** the tenant GUC is connection-scoped, so a tenant-scoped unit
    of work must run **all** its statements on the **same** connection. `tenant_session()`
    (`app/core/db.py`) checks out one connection, sets the GUC on it, and binds the session to it — so
    multi-commit pipelines (discovery, enrichment, bulk outreach) keep the tenant set across commits.
    A plain pooled session is unsafe here: after the first `commit()` the connection returns to the
    pool and a later statement can land on a different connection with no tenant set — RLS then hides
    the tenant's *own* rows (the cause of the "search_job not found (tenant GUC set?)" discovery bug),
    and in the worst case could expose another tenant's connection context. Both the API (`get_db`)
    and the ARQ worker go through `tenant_session()`.
- **Secrets:** API keys per tenant stored encrypted (a vault, not env files).
- **RBAC:** owner / agent / viewer roles.
- **PII handling:** business contacts are personal data — encrypt at rest, restrict access, support deletion.
- **Rate limits & quotas:** per-tenant caps on discovery queries and outreach volume (cost + abuse control).

---

## 7. Cost Control (this makes or breaks the unit economics)

- **Cache aggressively:** geocoding and Overpass results for the same area (they rarely change).
- **Self-host Overpass/Nominatim** at scale to drop external rate limits and stay free.
- **Spend LLM tokens only on promising leads:** enrich (read websites) for high-fit businesses first.
- **Budget guards:** per-tenant monthly spend caps; stop outreach when hit.
- **Channel cost ladder:** email/WhatsApp (cheap) before voice calls (expensive).
- **Track cost-per-lead and cost-per-meeting** on the dashboard from day one.

---

## 8. ⚠️ Legal & Compliance (DO NOT SKIP — read before building outreach)

Automated calling/messaging to businesses you have no relationship with is **regulated**. Getting this
wrong = fines and platform bans. This is the single biggest risk to the product.

- **India (your location):** TRAI telemarketing rules, the **DND (Do Not Disturb) registry**, and
  **DLT registration** for commercial SMS. WhatsApp Business API requires approved message templates.
- **USA:** TCPA — autodialed/AI calls to mobiles generally need prior consent; respect Do-Not-Call.
- **EU/UK:** GDPR — lawful basis required to process contacts; honor opt-out.
- **AI disclosure:** several jurisdictions require the bot to **state that it's an AI**. Build it in.
- **Universal must-haves:**
  - Honor **opt-out / Do-Not-Contact** instantly and permanently.
  - Respect local **business hours & time zones**.
  - Keep **records/consent logs** of every contact.
  - A global **kill switch** to stop all outreach immediately.

> **Recommendation:** Start with **opt-in / inbound-friendly channels** (email, WhatsApp template
> messages) and treat fully-automated cold *voice* calls as a later, region-gated feature behind
> compliance checks. Talk to a lawyer before going live in any region.

---

## 9. Phased Roadmap (MVP → Scale)

### Phase 0 — Foundations (week 1–2)
- Repo structure, multi-tenant data model, auth, CI/CD, provider adapter interfaces (`ChannelAdapter`, data-source adapter).

### Phase 1 — Discovery MVP (the "wow" demo)
- Address in → business list out via **OSM/Overpass + Nominatim**, plus **manual CSV import** to fill gaps.
- Simple dashboard. Ugly/incomplete data is acceptable — the goal is a working end-to-end pipeline.
- *This alone is a usable product and proves the core idea.*

### Phase 2 — Outreach + Conversation + first enrichment
- Channel adapters live; **enable WhatsApp/email first** (cheapest + most compliant), behind an approval gate.
- AI agent introduces, answers FAQs (RAG), detects interest.
- **AI enrichment v2:** LLM reads each business's own website to fill missing phone/email (biggest bang for the buck).

### Phase 3 — Follow-up reminders *(built; replaces "Scheduling")*
- AI-set dated call reminders, auto-minted Jitsi meeting link + `.ics` invite, CRM write-back to
  `callback_scheduled`, dashboard Reminders panel + click-to-call. (Full calendar sync deferred.)

### Phase 4 — The Moat: full AI Aggregation & Enrichment Engine
- Multi-source collection + dedup/entity-resolution + fit-scoring (§3.2 stages 1–4), source-by-source & compliant.

### Phase 5 — Voice
- Enable AI voice calling behind compliance gates and per-region toggles (consent, DND, business hours).

### Phase 6 — Scale & Polish
- Analytics dashboard, multi-channel orchestration, A/B testing of scripts, billing, team roles.

---

## 10. Decisions Status

**Resolved (see §0):**
1. ✅ **Tech stack** — Python (FastAPI) backend + Next.js frontend.
2. ✅ **Channels** — all four behind one adapter; enable WhatsApp/email first.
3. ✅ **Region** — India first.
4. ✅ **Business data** — free OSM/Overpass + Nominatim + AI enrichment (no Google/scraping).

**Still open (decide before the relevant phase):**
- **Build vs buy voice** (Phase 5): Twilio + STT/TTS (control) vs Vapi/Retell/Bland (speed)?
- **Auth provider**: Clerk vs Auth0 vs Supabase Auth?
- **Hosting**: which cloud, and self-host Overpass/Nominatim from the start or later?

---

## 11. Risk Register (top risks, ranked)

| Risk | Impact | Mitigation |
|---|---|---|
| Regulatory violation (spam/cold-call laws) | Project-ending | Compliance-first design, opt-out, region gating, legal review |
| Maps/LLM/voice API cost runaway | High | Caching, budget caps, channel cost ladder |
| Poor data quality (wrong/missing phones) | High | Enrichment + validation + confidence scoring |
| AI says something wrong/overpromises | High | RAG grounding, guardrails, human escalation |
| Provider lock-in | Medium | Adapter pattern around every external service |
| Low answer/conversion rates | Medium | A/B test scripts, channel strategy, lead scoring |

---

*Next step: with §0 decisions locked, we design the **multi-tenant data model** and the **Phase-1 Discovery service** (OSM/Overpass + Nominatim + CSV import) in detail.*
