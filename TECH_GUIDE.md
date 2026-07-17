# Scopio — Engineering Guide (stack, every file, and how the deep agent was built)

This is the definitive build document. It explains **the technology stack and *why* each
piece was chosen**, walks through **every source file**, and gives a **from-scratch recipe
for the LangGraph deep-research agent**. It's written from the actual code.

- Product overview: [`README.md`](README.md)
- System design & decisions: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- Report / interview guide: [`PROJECT_REPORT.md`](PROJECT_REPORT.md)
- Production hardening: [`PRODUCTION.md`](PRODUCTION.md)

---

## 1. What Scopio does (one paragraph)

Tell Scopio what your business sells; it reads that, decides **which kinds of nearby
businesses are good leads**, discovers them from OpenStreetMap around an address or your GPS
location, runs a **LangGraph agent** that researches each one (Tavily web search + reading
the business's own site) to fill in contacts + a profile, and then an **AI sales agent**
reaches out over email/WhatsApp and — for email — **holds the whole conversation
automatically**, booking a callback when the owner agrees. It's multi-tenant, and the owner
gets a cross-tenant **admin dashboard**.

The pipeline: **targeting → discovery → deep research → outreach → autonomous conversation →
reminder**, with the LLM as the brain throughout.

---

## 2. Technology stack — what & why

| Layer | Choice | Why this (and not the alternative) |
|---|---|---|
| **Language** | Python 3.11 | The whole problem is I/O + LLM orchestration; Python has the best LLM/agent ecosystem (LangGraph, provider SDKs) and first-class async. |
| **Web framework** | **FastAPI** | Async-first (critical — nearly every request awaits an LLM/HTTP call), automatic OpenAPI docs, Pydantic validation built in. Flask/Django are sync-first and heavier for a JSON API. |
| **Server** | Uvicorn | ASGI server FastAPI is built for; `--reload` in dev. |
| **Database** | **PostgreSQL 16** | Needs **Row-Level Security** for multi-tenancy (a hard requirement) and **JSONB** for flexible fields (`details`, `transcript`, `target_profile`). SQLite can't do RLS; a NoSQL store can't do the relational joins/analytics cleanly. |
| **ORM / driver** | **SQLAlchemy 2 (async) + asyncpg** | Async ORM so DB calls don't block the event loop; asyncpg is the fastest Postgres driver. |
| **Job queue** | **ARQ over Redis** | Discovery/enrichment/outreach are long-running and must run *outside* the request. ARQ is async-native (fits FastAPI), lightweight, and supports **cron** (used for the inbox poller). Celery is heavier and sync-first. |
| **LLM** | **OpenAI-compatible** (Groq by default) | One HTTP shape works for Groq/Claude/Gemini/OpenRouter/Ollama — swap provider via config, no code change. Groq's free tier is fast and generous. |
| **Agent framework** | **LangGraph** | The research task needs *loops and decisions* (search again? enough data?). LangGraph models that as an explicit, bounded **state machine** that's easy to reason about, cap, and unit-test — far cleaner than an ad-hoc `while` loop or a monolithic prompt. |
| **Web search** | **Tavily** | A search API **designed for agents** (clean, ranked, ToS-friendly results with content snippets) rather than scraping Google (against ToS, brittle). |
| **Discovery data** | **OpenStreetMap** (Overpass + Nominatim), optional Geoapify | Free, commercially licensed, global. No Google Places cost/ToS. |
| **Phone handling** | **libphonenumber** (`phonenumbers`) | Correct E.164 / mobile-vs-landline detection **worldwide**, not regex guesses. |
| **Email** | `aiosmtplib` (send) + stdlib `imaplib` (receive) | SMTP is the only open send protocol; IMAP lets the agent *read* replies. Both async-friendly (IMAP wrapped in a thread). |
| **Auth** | stdlib PBKDF2 + HMAC tokens (`hashlib`/`hmac`) | No heavy dependency; PBKDF2 for password hashing, HMAC-signed tokens for stateless auth. |
| **Frontend** | Single-file vanilla JS + **Leaflet** | Zero build step, served straight by the API; Leaflet is the standard free map. Keeps the repo simple and the focus on the backend/AI. |
| **Container** | Docker + docker-compose | One command brings up Postgres + Redis + API + worker (+ Adminer). |
| **DB browser** | Adminer (dev) | A web UI to inspect every table/row. |
| **Lint/format/test** | Ruff + pytest (`asyncio_mode=auto`) | Ruff is the fast all-in-one linter; pytest-asyncio for the async tests. |

**Cross-cutting design principles**

1. **Provider-agnostic adapters** — geocoder, places source, LLM, research agent, and outreach
   channel each sit behind a thin `Protocol`/interface, so any one can be swapped without
   touching the pipeline.
2. **Graceful degradation** — no key → a working fallback (regex extractor, keyword agent,
   website-read instead of deep agent). The app never hard-fails on a missing external service.
3. **Isolation by the database** — Postgres RLS enforces tenant separation, so a forgotten
   `WHERE tenant_id=…` can't leak data.

---

## 3. How a request flows

```
Browser (app/web/index.html)
   │  fetch() with Bearer token
   ▼
FastAPI (app/main.py → app/api/*.py)         ← thin controllers, validate + delegate
   │  Depends(get_db) → tenant-scoped session (RLS)     │  enqueue (ARQ/Redis)
   ▼                                                    ▼
Services (app/services/*)  ──────────────►  ARQ worker (app/workers/discovery_worker.py)
   │                                          run_discovery / run_enrichment /
   ▼                                          run_bulk_outreach / cron: inbox poll
PostgreSQL (RLS)   +   External APIs (Nominatim, Overpass, Tavily, Groq, SMTP/IMAP, Jitsi)
```

- **Interactive** requests (auth, listing, starting outreach) run in the API process on a
  **tenant-scoped** DB session.
- **Heavy/long** work (discovery, deep research, bulk outreach, inbox polling) is **enqueued**
  and runs in the **worker**, each job wrapped in `tenant_session(tenant_id)`.

---

## 4. File-by-file reference

### `app/main.py`
Assembles the FastAPI app: a `lifespan` that refuses to boot in production with the default
secret and closes the ARQ pool on shutdown, security-headers + CORS middleware, registers all
routers (`auth, search_jobs, businesses, reminders, outreach, analytics, admin`), and mounts
the single-page dashboard at `/` last so it doesn't shadow the API.

### `app/core/` — foundations
- **`config.py`** — one `pydantic-settings` `Settings` singleton (`get_settings()` is
  `lru_cache`d) read from `.env`. Holds everything tunable: environment switch, DB/Redis URLs,
  LLM endpoints, **Tavily + deep-research** settings, **inbox poll** settings, cost guards,
  discovery URLs, reminder/phone defaults, signing secret, and **admin** settings.
- **`db.py`** — the async engine + `SessionLocal`. The key function is **`tenant_session()`**:
  it pins one connection, sets the RLS GUC (`app.tenant_id`) on it, and binds the session to
  that connection so every commit in a multi-step job stays tenant-scoped (fixing a real
  "search_job not found" bug). Also defines the privileged **`admin_engine` / `admin_session`**
  (superuser, bypasses RLS) used only by the admin dashboard.
- **`deps.py`** — FastAPI dependencies: `get_identity` (parse Bearer token → tenant/user, dev
  fallback), `get_db` (tenant-scoped session), `get_raw_db` (unscoped, for auth). Plus the
  **admin gate**: `admin_email_set`, `email_is_admin`, `is_admin_identity`, `require_admin`.
- **`llm.py`** — the **shared cloud-LLM brain**: one `chat(messages, json_mode, model,
  base_url, …)` (OpenAI-compatible, with 429 retry honoring `retry-after`) used by targeting,
  the deep agent, and the outreach agent — so all LLM I/O lives in one place.
- **`phone.py`** — libphonenumber wrappers: `normalize_phone`, `e164`, `wa_number`, `is_mobile`.
- **`security.py`** — `hash_password`/`verify_password` (PBKDF2-HMAC-SHA256) and
  `make_token`/`parse_token` (HMAC-signed token — integrity, not encryption).

### `app/api/` — HTTP routes (thin controllers, delegate to services)
- **`auth.py`** — register/login/me/profile/connect_email + **`GET /email_provider`**. Login
  records `last_login_at` and **rejects suspended accounts**; responses carry `is_admin`.
  `connect_email` **auto-derives SMTP host/port from the email's provider** (see
  `services/outreach/email_providers.py`), strips app-password spaces, gives provider-aware errors.
- **`search_jobs.py`** — create a search (accepts address **or** GPS lat/lng), list businesses,
  CSV/extension import, trigger enrich, trigger bulk outreach, WhatsApp queue. (Target profile is
  now derived in the **worker**, not here, so the click returns instantly.)
- **`assistant.py`** — **Ask Scopio** (agentic mini-chatbot). `POST /assistant/command`
  (LLM plans → query leads or answer from DB + web tool), `/assistant/category` (drill into one
  category's businesses), `/assistant/export` (whole result set → `.xlsx`/`.csv` with clickable
  Google Maps links).
- **`businesses.py`** — get / patch / soft-delete a business.
- **`reminders.py`** — CRUD + `GET /{id}/invite.ics`.
- **`outreach.py`** — `/start`, `/send/{id}`, `/contact_link/{id}`, conversations,
  **`/poll_inbox`** (trigger the autonomous email check).
- **`analytics.py`** — the funnel metrics (`GET /analytics`), RLS-scoped.
- **`admin.py`** — owner-only cross-tenant dashboard (`/overview`, `/users`, `/searches`) **plus
  control actions** (`POST /users/{id}/suspend` · `/reactivate`), gated by `require_admin`, reading
  via the privileged `admin_session`. Admin accounts are shielded from suspension (`can_moderate`).

### `app/models/` — SQLAlchemy tables
`tenant` (account + SMTP creds + tz), `user` (`app_user`; role, `last_login_at`, `login_count`,
`suspended_at` — set by an admin to block login),
`search_job` (status machine + `target_profile` JSONB + center lat/lng), `business` (the lead;
`dedup_key`, `details` JSONB, `status`, `enriched_at`, soft-delete), `search_job_business`
(many-to-many link), `conversation` (JSONB `transcript`, `reminder_id`), `reminder` (due_at,
meeting URL), `area_cache` (cross-tenant OSM cache).

### `app/schemas/` — Pydantic request/response DTOs
`auth.py` (adds `is_admin`, `last_login_at`, `EmailProviderOut`; `services` accepts ~20k words),
`business.py` (computed `has_contact`, `whatsappable`, `phone_e164`, `enriched`…), `assistant.py`
(the Ask-Scopio intent + command/category/export DTOs), `outreach.py`, `reminder.py`,
`search_job.py` (adds optional `lat`/`lng` for GPS, exposes `target_profile`).

### `app/services/targeting.py` — context-aware targeting (the brain that decides *who*)
`derive_target_profile(services, company)` → `TargetProfile { target_business_types,
osm_filters, tavily_keywords, rationale }` via `llm.chat` (JSON mode), constrained to a
whitelist of OSM keys. Empty profile ⇒ broad search (never blocks discovery).

### `app/services/discovery/` — find the businesses
- **`pipeline.py`** — orchestrates geocode (skipped for GPS) → cache → Overpass (filtered by
  the target's `osm_filters`, +Geoapify) → normalize → `upsert_businesses` (batched, `COALESCE`
  merge on conflict). Cache key includes the target filter so sellers don't collide.
- **`geocoder.py`** — Nominatim behind `GeocoderPort`.
- **`overpass.py`** — Overpass QL builder + `OverpassClient` with multi-mirror failover; emits
  only the targeted tag clauses when `osm_filters` is given (values sanitized).
- **`normalizer.py`** — raw OSM elements → the common `Business` shape; `classify()`.
- **`dedup.py`** — `dedup_key`, `area_geohash`, `merge_by_dedup_key`.
- **`geoapify.py`** — optional 2nd source emitting the same shape + `dedup_key`.

### `app/services/deepagent/` — the LangGraph research agent (see §5 for the full build)
- **`graph.py`** — the compiled `StateGraph` + `research_business(...) → ExtractionResult`.
- **`tools.py`** — `tavily_search`, `read_website`, `first_business_site` (all fail-soft).

### `app/services/enrichment/` — fallback enrichment + shared schema
- **`pipeline.py`** — `find_candidates` (never-tried-first + cooldown) and `run_enrichment`,
  which routes each candidate to the **deep agent** when Tavily is set, else the website-read
  extractor.
- **`extractor.py`** — the `Extractor` Protocol + Claude/Groq/regex implementations +
  `get_extractor()`. Home of the shared `ExtractionResult`, `_SCHEMA`, `_result_from_profile`.
- **`fetcher.py`** — `fetch_site_text` (homepage + /contact, HTML→text, 6000-char cap).
- **`websearch.py`** — `find_website` (Brave/DuckDuckGo) + `is_business_site` denylist.

### `app/services/assistant.py` — Ask Scopio (agentic retrieval + answering)
The brain of the chat. `parse_command` (LLM plans → `AssistantIntent`: query vs. answer, filters,
whether to web-search), `run_command_query` / `build_query` (matches the **precise** type from raw
OSM tags, not just the broad bucket), `grouped_counts` + `list_by_category` (clickable drill-in),
`answer_with_data` (retrieve DB rows → optional **Tavily** web tool → LLM synthesizes the answer),
and `build_xlsx`/`build_csv` + `maps_link` (exports with clickable Google Maps links). Keyword
fallback with no LLM.

### `app/services/outreach/` — the AI sales agent
- **`agent.py`** — `generate_opening` (channel-aware, appends the **opt-out line** via
  `with_optout`) and `respond` (structured `{reply, intent, set_reminder, callback_days}`),
  with a full keyword fallback when no LLM.
- **`playbook.py`** — `SenderContext` + `system_prompt` (persona + honesty guardrails) +
  `fallback_opening`.
- **`channels.py`** — `ChannelAdapter` Protocol; `send_email`/`verify_smtp` (aiosmtplib),
  `whatsapp_link`, `mailto_link`. New channels (WhatsApp Cloud API, Twilio) slot in here.
- **`email_providers.py`** — maps an email address → SMTP host/port + app-password guidance
  (`detect`, `resolve`) so users connect their mailbox by typing just email + app password.
- **`service.py`** — orchestration: `start_conversation`, `handle_reply` (the auto-reminder
  seam), `send_message`, `whatsapp_queue`, `bulk_outreach`.

### `app/services/inbox/` — autonomous inbound email (agent talks to everyone)
- **`imap_client.py`** — stdlib IMAP reader (run via `asyncio.to_thread`): fetch UNSEEN, parse,
  `strip_quoted()` to keep only the new reply.
- **`service.py`** — `poll_all_inboxes` / `poll_one_tenant` → match reply → business →
  conversation, call `agent.respond`, **send the answer via SMTP**, update status + reminder.

### `app/services/reminders/` — remember to call them
`service.py` (due date in tenant tz → UTC, `create_reminder`, CRUD), `meeting_link.py` (Jitsi
room), `calendar_invite.py` (`.ics` + Google Calendar link).

### `app/services/importer/csv_import.py`
Manual CSV import through the same `dedup_key` + `upsert_businesses` path.

### `app/workers/` — background jobs
- **`queue.py`** — API-side enqueue helpers over a lazily-created ARQ Redis pool.
- **`discovery_worker.py`** — the ARQ worker: `run_discovery_job`, `run_enrichment_job`,
  `run_bulk_outreach_job`, `run_inbox_poll_job`, `run_inbox_poll_tenant_job`, plus a **cron**
  that polls inboxes every 2 minutes. `job_timeout=3600`.

### `app/web/index.html`
The single-page dashboard: refined dark UI with an SVG logo + line-icon system (no emoji),
fully responsive, a Leaflet map, GPS button, targeting chip, business list, outreach chat,
reminders, analytics, **admin panel** (owner only), and a WhatsApp send queue. `window.fetch`
is monkey-patched to attach the Bearer token.

### Top-level / infra
- **`db/init.sql`** — enums, tables, indexes, **RLS policies + FORCE RLS**, the non-superuser
  `app_rls` role, and a dev seed. Idempotent `ALTER … ADD COLUMN IF NOT EXISTS` for new columns.
- **`Dockerfile`** — `python:3.11-slim`, deps-before-source layer caching, one image for API +
  worker (`pip install -e .`).
- **`docker-compose.yml`** — db, redis, api, worker, **adminer**. **`docker-compose.prod.yml`** —
  hardened overlay.
- **`pyproject.toml`** — dependencies + ruff/pytest config.
- **`scripts/smoke_discovery.py`** — a manual discovery smoke test.
- **`tests/`** — 24 modules / 154 tests (pure-logic + stubbed-network), incl. Ask Scopio
  (planning, DB-grounded answers, web-tool), email-provider detection, and admin moderation.

---

## 5. How the deep research agent was built (from scratch)

This is the heart of the "agentic" part. Goal: given a business (maybe just a name + area),
autonomously gather its public contact details + profile the way a person would — search the
web, find the official site, read it, search again, then synthesize.

### 5.1 Why LangGraph (and not a plain loop or one big prompt)
- A single prompt can't *act* — it can't search the web or fetch a page.
- A hand-rolled `while` loop works but tangles control flow, retries, and caps together.
- **LangGraph** models the work as a **graph of nodes with a shared state** and **conditional
  edges** — so "search again vs. finish" is one explicit, testable decision, and the whole thing
  is bounded and inspectable.

### 5.2 The shape: state → tools → nodes → edges

**State** (a `TypedDict`): what flows between nodes.
```
name, category, locality, website,   # inputs
queries: list[str],                  # remaining search queries
corpus: list[str],                   # gathered text (site + snippets)
searches_used: int,                  # loop counter (for the cap)
result: ExtractionResult             # final output
```

**Tools** (`tools.py`) — plain async functions, each **fail-soft** (return empty on error so
one bad lookup never crashes a batch):
- `tavily_search(query)` → `[{title, url, content}]` (lazy-imports the Tavily client; `[]` if
  no key).
- `read_website(url)` → cleaned text (reuses `enrichment/fetcher.fetch_site_text`).
- `first_business_site(results)` → the first result that's the business's *own* site (reuses
  `enrichment/websearch.is_business_site` to skip directories/socials).

**Nodes** (each takes the state, returns the fields it changed):
1. **`find_site`** — if no `website`, run one Tavily search and pick the business's own site.
2. **`read_site`** — if there's a website, fetch its text into `corpus`.
3. **`search`** — pop one query, Tavily-search it, append snippets to `corpus`, `searches_used++`.
4. **`synthesize`** — one `llm.chat` (JSON mode) over the whole `corpus` → profile JSON →
   mapped to `ExtractionResult` via the shared `_result_from_profile` (so the enrichment
   write-back path is unchanged).

**Edges** (the control flow):
```
START → find_site → read_site → search
search ──(conditional: _route_after_search)──►  search   (loop: queries remain AND under cap)
                                             └►  synthesize
synthesize → END
```
The conditional edge is what makes it an *agent*: after each search the graph **decides** at
runtime whether to search again (more queries left and under `deep_research_max_searches`) or
move on to synthesis.

### 5.3 Wiring it in LangGraph (the essence of `graph.py`)
```python
from langgraph.graph import StateGraph, START, END

g = StateGraph(ResearchState)
g.add_node("find_site", _find_site)
g.add_node("read_site", _read_site)
g.add_node("search", _search)
g.add_node("synthesize", _synthesize)
g.add_edge(START, "find_site")
g.add_edge("find_site", "read_site")
g.add_edge("read_site", "search")
g.add_conditional_edges("search", _route_after_search,
                        {"search": "search", "synthesize": "synthesize"})
g.add_edge("synthesize", END)
graph = g.compile()          # compiled once (lazy) and reused

# entry point:
final = await graph.ainvoke(initial_state)
return final["result"]       # an ExtractionResult
```

### 5.4 The synthesis prompt (why it's trustworthy)
The synthesize node uses a strict system prompt: *"extract only what's clearly supported…
NEVER invent or guess, especially phone numbers… prefer the business's OWN site over
third-party snippets,"* and returns a fixed JSON schema plus a `confidence` score. This is why
the agent doesn't hallucinate phone numbers.

### 5.5 How it plugs into the product
`enrichment/pipeline.run_enrichment` calls `_deep_researcher()`: if `enable_deep_research` **and**
a Tavily key **and** `langgraph` imports → each candidate goes through `research_business(...)`;
otherwise it falls back to the website-read extractor. Same candidate selection, cooldown, and
write-back either way — the agent is a **drop-in upgrade**, not a rewrite.

### 5.6 How to recreate it yourself (checklist)
1. `pip install langgraph tavily-python` (add to `pyproject.toml`).
2. Add settings: `tavily_api_key`, `enable_deep_research`, `deep_research_max_searches`.
3. Write `tools.py` — Tavily search + website read + "is this the real site" filter, all
   returning safe empties on error.
4. Define the `ResearchState` TypedDict.
5. Write the 4 node functions (each returns only the state keys it changes).
6. Write `_route_after_search` (loop vs finish) and build/compile the `StateGraph`.
7. Reuse a shared result type + a "never invent" JSON schema for the synthesis step.
8. Expose `research_business(...)` and call it from your enrichment loop, behind a feature flag
   with a non-agent fallback.
9. Test it offline by monkeypatching the tools + the LLM chat (see `tests/test_deepagent.py`).

### 5.7 The other AI pieces (same principles)
- **Targeting** (`targeting.py`) — one JSON-mode LLM call maps services → OSM filters.
- **Outreach agent** (`outreach/agent.py`) — opening + a JSON `respond()` that detects intent
  and decides to book a call.
- **Autonomous inbox** (`inbox/`) — not an LLM graph but an **event loop**: IMAP ingest →
  `agent.respond()` → SMTP send, driven by an ARQ cron. This is "agentic" in the systems sense
  (perceive → decide → act on a schedule).

---

## 6. Multi-tenancy, security & the admin exception
- Every tenant-owned table has an RLS policy `USING (tenant_id = current_setting('app.tenant_id'))`;
  the app connects as the non-superuser **`app_rls`** with `FORCE ROW LEVEL SECURITY`, so
  isolation is enforced by Postgres.
- The **admin dashboard** is the one deliberate exception: it reads (and writes control actions
  like suspend/reactivate) through a **superuser** connection (`admin_session`) that bypasses RLS,
  gated behind `require_admin` (email in `ADMIN_EMAILS`, or the bootstrap `ADMIN_EMAIL`). Admin
  accounts can't be suspended. Every other route stays tenant-scoped.
- **Bootstrap admin:** if `ADMIN_EMAIL` + `ADMIN_PASSWORD` are set, `main.py::_bootstrap_admin`
  ensures that login exists on startup (created if missing; password re-synced, hashed) — so `.env`
  is the private source of truth for the owner's account.

---

## 7. Running it
```bash
cp .env.example .env      # add GROQ_API_KEY, TAVILY_API_KEY, ADMIN_EMAIL + ADMIN_PASSWORD, SECRET_KEY
docker compose up --build # db + redis + api + worker + adminer
```
(Full step-by-step setup + troubleshooting: see the Installation guide in `README.md`.)
- Dashboard: http://localhost:8000  ·  API docs: http://localhost:8000/docs
- DB browser (Adminer): http://localhost:8080  (PostgreSQL · server `db` · `scopio`/`scopio`)
- Tests: `pip install -e ".[dev]" && pytest -q`

---


