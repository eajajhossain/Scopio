# Scopio

[![CI](https://github.com/eajajhossain/Scopio/actions/workflows/ci.yml/badge.svg)](https://github.com/eajajhossain/Scopio/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Ruff](https://img.shields.io/badge/lint-ruff-261230)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An AI Sales Outreach Platform.

Tell Scopio **what your business sells**, then enter an address (or 📍 **use your location**) →
Scopio's AI works out **which kinds of nearby businesses are your ideal leads** and discovers only
those → a **LangGraph deep-research agent** enriches each one (Tavily web search + reading the
business's own website) → after you **confirm**, the AI reaches out (WhatsApp/email) with a
personalized, business-aware message, answers questions, and **sets a follow-up call reminder**
(with an auto-generated video-meeting link) when the owner is interested.

The LLM is the **brain** throughout: it reads your services to target the right leads, and it holds
the sales conversation to capture the callback.

📐 Design docs: [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`PHASE1_DESIGN.md`](PHASE1_DESIGN.md)
🛠 Engineering guide (stack · every file · how the deep agent was built): [`TECH_GUIDE.md`](TECH_GUIDE.md)
🚀 Production guide: [`PRODUCTION.md`](PRODUCTION.md) · Deploy your own: [`DEPLOY.md`](DEPLOY.md)

---

## 🔴 Live demo

**▶ [<LIVE_DEMO_URL>](<LIVE_DEMO_URL>)** — sign in with the shared demo account:

| Email | Password |
| --- | --- |
| `demo@scopio.app` | `scopio-demo` |

![Scopio demo](docs/demo.gif)

> Enter a location → the AI targets the right business types and discovers real nearby leads
> on the map → **Deep research** enriches a business (Tavily + website read) → an outreach
> message is generated. Hosted on a single VM via Docker Compose — see [`DEPLOY.md`](DEPLOY.md).

---

## 📦 Installation guide

A complete, step-by-step setup. The Docker path is the easiest — one command brings up the
whole stack (database, queue, API, background worker). Everything works with **zero API keys**
to start; add keys later to unlock the AI features.

### 1. Prerequisites

| Tool | Why | Get it |
|---|---|---|
| **Docker Desktop** | Runs Postgres + Redis + the app in one command | https://www.docker.com/products/docker-desktop/ |
| **Git** | To clone the project | https://git-scm.com/downloads |

That's all you need for the recommended (Docker) path. Make sure **Docker Desktop is running**
(its whale icon in the tray/menubar) before you start — `docker version` should print without error.

> Windows note: Docker Desktop needs WSL 2. If `docker compose` says it can't reach the daemon,
> open the Docker Desktop app first and wait ~30s for the engine to start.

### 2. Get the code

```bash
git clone https://github.com/eajajhossain/Scopio.git
cd Scopio
```

### 3. Create your `.env`

```bash
cp .env.example .env
```

Open `.env` in any editor. **You can leave everything blank and it still runs** — but here's
what to fill in for the full experience:

| Variable | Needed for | Where to get it (all free) |
|---|---|---|
| `GROQ_API_KEY` | The AI brain (Ask Scopio, targeting, outreach, enrichment) | https://console.groq.com/keys |
| `TAVILY_API_KEY` | Deep web research + Ask Scopio's web tool | https://tavily.com |
| `GEOAPIFY_API_KEY` | Optional 2nd business-discovery source | https://myprojects.geoapify.com |
| `BRAVE_API_KEY` | Optional: find missing business websites | https://brave.com/search/api |
| `ANTHROPIC_API_KEY` | Optional paid upgrade (higher-accuracy enrichment) | https://console.anthropic.com |
| `ADMIN_EMAIL` + `ADMIN_PASSWORD` | Your **admin login** (see step 5) | you choose these |
| `SECRET_KEY` | Signing key — **required for production** | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |

`.env` is **gitignored** — your keys and admin password never get committed or published.

### 4. Start it

```bash
docker compose up --build
```

First build takes a few minutes (it installs dependencies). It starts four services:

| Service | What | URL |
|---|---|---|
| **api** | The app + dashboard | http://localhost:8000/ |
| **worker** | Background jobs (discovery, enrichment, outreach) | — |
| **db** | PostgreSQL (schema auto-loaded from `db/init.sql`) | localhost:5432 |
| **redis** | Job queue | localhost:6379 |
| **adminer** | Browse the database (dev only) | http://localhost:8080 |

To run it in the background instead: `docker compose up --build -d`. To stop: `docker compose down`.


### 6. Verify it works

- Open **http://localhost:8000/** — you should see the dashboard.
- Health check: **http://localhost:8000/health** → `{"status":"ok"}`
- API docs (all endpoints): **http://localhost:8000/docs**
- Enter an address (e.g. `Barasat, 700125`) → **Find businesses** → real businesses appear on the map.

### Running without Docker (advanced)

You'll need Python 3.11+, a running PostgreSQL, and Redis yourself. Then:

```bash
pip install -e ".[dev]"
# create the DB and load the schema:
psql "$DATABASE_URL" -f db/init.sql
# set DATABASE_URL / REDIS_URL (and any keys) in your environment, then:
uvicorn app.main:app --reload            # API + dashboard
arq app.workers.discovery_worker.WorkerSettings   # in a second terminal: the worker
```

### Running the tests

```bash
pip install -e ".[dev]"
pytest                      # pure-logic tests need no database or keys
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `failed to connect to the docker API` / daemon errors | Docker Desktop isn't running — open the app, wait ~30s, retry. |
| `port is already allocated` (5432/6379/8000) | Another Postgres/Redis/app is using that port — stop it, or change the port mapping in `docker-compose.yml`. |
| Search is slow the first time in a new area | Normal — it's the free public OpenStreetMap API. Repeat searches of the same area are cached and instant. |
| AI features do nothing / "assistant" gives basic answers | No `GROQ_API_KEY` set — add one (free) to `.env` and restart. |
| "Couldn't connect to your email" | Use an **app password** from your provider, not your normal password (the Connect-email panel links you straight to the right page). |


---

## Phase 1 — Discovery (what's built)

Address (or 📍 GPS location) in → list of real businesses out, using **free OpenStreetMap data**
(Overpass + Nominatim), with an area cache, de-duplication, and manual CSV import. **Works for any
address worldwide** (tested from Barasat to Soho, London to Brooklyn, NY). Multi-tenant from day one
(Postgres RLS).

**Context-aware targeting:** an LLM reads the account's own services and derives which business types
are good leads (e.g. a cup/packaging supplier → cafes, bakeries, restaurants), then the Overpass query
is **filtered to just those categories** so discovery returns only relevant leads. With no LLM key it
falls back to the broad "all businesses" search.

**Stack:** FastAPI · PostgreSQL · Redis · ARQ worker · SQLAlchemy 2 (async) · phonenumbers
(international phone/WhatsApp normalization) · Leaflet (interactive map).

### Self-hosting — run your own copy

Scopio is open source: clone it, add **your own** API keys, and run it on your own machine or
server (full steps in the [Installation guide](#-installation-guide) above). Each deployment is
**fully independent** — its data, users, and keys live only on the machine it runs on. There is
no phone-home and no shared backend; the project maintainer has no access to your instance, and
you have none to anyone else's.

### The dashboard (once it's running with `docker compose up`)

- **Dashboard: http://localhost:8000/** — enter an address, watch the job run, see businesses on an
  interactive map + list, and upload a CSV. (No build step; served by the API.)
  - The map opens on a **slowly spinning Earth** and plays a **cinematic fly-in** to your location
    when you search, dropping a pulse marker on the spot.
  - **Dark / light** basemap toggle (bottom-left), remembered between visits.
  - The UI is **white-labeled** — it shows friendly progress ("Searching for businesses…",
    "Enriching with AI…") and never leaks internal provider names, error traces, or status codes.
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

### Try the flow

```bash
# 1. Start a discovery job
curl -X POST http://localhost:8000/search_jobs \
  -H "Content-Type: application/json" \
  -d '{"raw_address": "Barasat, 700125", "radius_m": 2000}'
# -> { "id": "<job_id>", "status": "pending", ... }

# 2. Poll status (pending -> geocoding -> querying -> completed)
curl http://localhost:8000/search_jobs/<job_id>

# 3. List the businesses found
curl "http://localhost:8000/search_jobs/<job_id>/businesses?limit=50"

# 4. Fill gaps OSM missed (CSV: name,category,address,phone,email,website,lat,lng)
curl -X POST http://localhost:8000/search_jobs/<job_id>/import \
  -F "file=@sample_businesses.csv"

# 5. AI-enrich: read each website to fill missing phone/email (Phase 2)
curl -X POST http://localhost:8000/search_jobs/<job_id>/enrich
```

## Phase 2 — AI enrichment

For businesses that have a website but no phone/email, Scopio fetches the site and
extracts the contact details, then writes back what was missing.

The engine auto-picks the best extractor available — **Claude > Groq (free) > regex** — behind
one `Extractor` interface, so the pipeline never changes:

| Set in `.env` | Extractor used | Cost |
|---|---|---|
| nothing | regex/heuristic | free (low accuracy) |
| `GROQ_API_KEY` | **Groq** (Llama 3.3 70B, OpenAI-compatible) | **free tier** |
| `ANTHROPIC_API_KEY` | **Claude** (`claude-opus-4-8`) | paid (highest accuracy) |

**Free path (recommended):** get a key at https://console.groq.com/keys, put `GROQ_API_KEY=gsk_...`
in `.env`, then `docker compose up -d`. The same `llm_base_url` / `llm_model` settings also point the
adapter at **Gemini** or a local **Ollama** instance.

**Optional 2nd discovery source — Geoapify (free, no card):** set `GEOAPIFY_API_KEY` in `.env` to merge
Geoapify Places results with OSM. Note it's largely OSM-derived (modest gain); proprietary providers
(TomTom/HERE) plug into the same `find_businesses()` shape for genuinely new data.

The dashboard's **"✨ Enrich missing contacts"** button triggers enrichment; enriched rows get an
"AI enriched" badge. Each click processes a capped batch (`ENRICHMENT_MAX_BUSINESSES`, default 50)
and **advances through the backlog**: never-tried businesses are enriched first, and a business
enriched within the last `ENRICHMENT_RECHECK_DAYS` (default 14) is skipped — so repeated clicks reach
new businesses instead of re-reading the same sites (which is slow and rarely yields anything new).
Set `ENRICHMENT_RECHECK_DAYS=0` to always re-attempt.

**Deep research agent (LangGraph + Tavily):** when a `TAVILY_API_KEY` is set, enrichment is run by a
small **[LangGraph](https://langchain-ai.github.io/langgraph/) agent** instead of a single website
read. For each business it: finds the official site if unknown → reads that site → runs a few
**[Tavily](https://tavily.com)** web searches → synthesizes contacts + a full profile with the LLM
(`find_site → read → search ⟲ → synthesize`, capped by `DEEP_RESEARCH_MAX_SEARCHES`). Without a
Tavily key it **falls back** to the website-read extractor above — so nothing breaks before the key
is added. The chat agent stays on its own fast endpoint (`OUTREACH_BASE_URL`), independent of the
research LLM.

**Deep profile (Phase 4a):** enrichment doesn't just grab phone/email — it reads the business's own
website for a full profile (**opening hours, a description, address, social links**), stored in
`business.details` and shown in the dashboard. This is the first source of the **multi-source
aggregation engine** designed in [`PHASE4_DESIGN.md`](PHASE4_DESIGN.md) — the free, legal path toward
Google-Maps-level completeness (Wikidata, web-search-to-find-site, Yelp/Foursquare, and an optional
paid Google Places source all plug into the same `SourceProvider` interface).

**Observability (Langfuse):** set `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (free at
[cloud.langfuse.com](https://cloud.langfuse.com)) and every LLM call and agent run is traced —
per-business research waterfalls (`find_site → read → search → synthesize`), token usage, latency,
cost, and errors. Without keys, tracing is a complete no-op.

```bash
# .env
GROQ_API_KEY=gsk_...        # free; or ANTHROPIC_API_KEY=sk-ant-... ; or neither (regex)
docker compose up -d
```

## Ask Scopio — the agentic assistant over your leads

A built-in mini-chatbot (**✨ Ask AI**, top-right) that answers **any** question about the
businesses you've discovered. The LLM is the **brain, not the answer source**: for each message it
*plans* what to do, pulls the answer from **your database** of found businesses, and — when the
database isn't enough — **calls the web tool (Tavily)** to fetch a satisfying answer. It never
makes up business data.

- **Ask anything:** *"which restaurants here have no website?"*, *"who should I contact first?"*,
  *"does Cafe Roma have good reviews?"* (that one triggers a web search), *"how should I pitch the
  salons?"* — grounded in both your business context and the real leads on screen.
- **List & filter:** *"give me only the cafes without a website"* → matches the precise business
  type (from the raw OSM tags, not just the broad category), grouped into **clickable categories**
  you expand to see every business + its full details.
- **Export to Excel/CSV:** *"make an Excel with phone, email and a Google Maps link"* → a real
  `.xlsx` with **clickable Google Maps links** per business, covering the **whole** result set.
- **Chat memory + web citations:** follow-ups resolve against prior turns, and web-sourced answers
  show their source links. Endpoints: `POST /assistant/command` · `/assistant/category` ·
  `/assistant/export`. Falls back to a keyword parser with no LLM key; the web step is skipped
  without a Tavily key.

## Accounts & onboarding

Scopio is multi-user: each person registers, says **what their business offers**, and the AI
reaches out **as them**. (Business details accept up to ~20,000 words, so you can paste a full
company profile for richer targeting and outreach.)

- `POST /auth/register` (name, company, services, email, password) → creates an isolated tenant +
  user, returns a signed token. `POST /auth/login`, `GET /auth/me`, `PATCH /auth/profile`.
- Auth is stdlib-only (PBKDF2 password hashing + HMAC-signed tokens — see `app/core/security.py`);
  set `SECRET_KEY` in production.
- The dashboard shows a **login / register gate** with onboarding fields; the token is stored in
  the browser and sent on every request. (No login? A "Skip (demo)" option uses the dev tenant.)
- The AI sender identity comes from the account: register as *Akash @ NovaAI* and outreach opens
  with *"Hi, I'm Akash from NovaAI…"* pitching NovaAI's services.

### Admin dashboard (platform owner)

The admin sees an **Admin** button that opens a cross-tenant dashboard: totals (accounts, users,
businesses, searches, conversations, reminders), **all users** (with last-login + login count +
searches), and **all searches** (who searched, where, what was targeted, results) — across every
account. It reads through a privileged connection that bypasses Row-Level Security
(`app/api/admin.py`), so it's the one place isolation is intentionally lifted; every other route
stays strictly tenant-scoped. (For a raw DB view there's also **Adminer** at `http://localhost:8080`
in dev.)

**Real control, not just visibility:** the admin can **suspend or reactivate any account** — a
suspended user is instantly blocked from logging in (`POST /admin/users/{id}/suspend` ·
`/reactivate`), with per-user buttons in the dashboard. Admin accounts are protected: they can't be
suspended, so the owner can never be locked out.

**Two ways to become the admin of your deployment:**
- **Bootstrap login (easiest):** set `ADMIN_EMAIL` + `ADMIN_PASSWORD` in `.env`. On startup the app
  creates that account (if missing) and keeps its password in sync — those two lines *are* your
  admin login, private to your machine (`.env` is gitignored; the password is hashed in the DB).
- **Allow-list:** register normally in the app, then add that email to **`ADMIN_EMAILS`**
  (comma-separated). Either way, that account gets the Admin button.

Each person who self-hosts sets their *own* admin credentials for *their own* copy.

## Outreach — the AI sales agent

The heart of Scopio: an AI agent that contacts a business, holds a real conversation, and
**sets a follow-up call reminder automatically the moment the owner agrees.**

- `POST /outreach/start` — the agent writes a warm, personalized opening message with AI and opens a
  conversation (drafted in preview; actual delivery happens via the send / 🚀 Message-all flow below).
- `POST /outreach/conversations/{id}/reply` — feed the owner's reply; the agent responds, detects
  intent, and on agreement **sets a dated call reminder** with an auto-generated Jitsi meeting link —
  flipping the CRM status to `callback_scheduled` and sharing the join link in its reply.
- Dashboard: **"💬 Reach out (AI)"** on each business opens a chat panel where you can play the
  owner and watch the agent work; **"📞 Call"** opens an international click-to-dial link.

**Honest persuasion, not manipulation:** the agent is warm and compelling but never lies, fakes
results, or uses pressure tactics (see `app/services/outreach/playbook.py`) — which is both more
effective and keeps outreach legal. Every cold opening also carries a clear **opt-out line**
("Reply STOP and I won't email again") for CAN-SPAM / GDPR compliance and deliverability; a STOP
reply flips the lead to `not_interested` and the agent stops contacting them.

### Human-in-the-loop review mode (the default)

New accounts start in **review mode**: the AI writes every outreach email and inbound reply, but
**nothing is sent until you approve it**. Drafts queue under **Drafts** (top-right, with a pending
count) where you can edit the text, approve to send, or discard. Approval performs exactly what the
autonomous path would (SMTP send, conversation recording, intent/reminder side-effects) — the two
paths share one code seam (`app/services/outreach/outcome.py`) so they can't drift. Flip the
account to **autonomous** in the Drafts panel when you trust it — a realistic production rollout:
supervise first, then hand over.

### Autonomous email conversations (hands-free)

Once the account connects its email, Scopio doesn't just send the opener — it **holds the whole
conversation automatically**. A background worker (`app/services/inbox/`) polls the connected inbox
over **IMAP** every couple of minutes, matches each customer reply to its conversation, feeds it to
the same agent, and **sends the agent's response back over SMTP** — detecting intent and setting the
callback reminder on agreement, with no clicks. Use **"Check replies"** (top-right) to run a poll on
demand. Bounded by design: it only answers businesses you actually contacted, stops on
`not_interested`, and caps very long threads (`INBOX_REPLY_MAX_TURNS`); set `INBOX_POLL_ENABLED=false`
to turn it off. (WhatsApp auto-conversations need the paid WhatsApp Business Cloud API — the same
adapter seam — because personal WhatsApp has no inbound API.)

### What actually gets delivered

| Channel | Behaviour | Automatic? |
|---|---|---|
| **Email** | Connect your mailbox once (top-right) — Scopio **auto-detects your provider** (Gmail, Outlook, Yahoo, iCloud, Zoho…) and fills in the SMTP settings; you just paste an **app password** (a one-click link takes you to the right page). Then it **really sends** via `aiosmtplib`. | ✅ Fully auto-sent |
| **WhatsApp** | A `wa.me` **tap-to-send** link with the message pre-filled. Personal WhatsApp **cannot** be auto-sent (WhatsApp ToS / no free API), so you tap *Send* — the **WhatsApp Queue** lines them all up for fast one-tap blasting. | ❌ Drafted (one tap) |
| **SMS / Voice** | Not wired — needs your own Twilio / provider accounts; plug into the same `ChannelAdapter` interface. | — |

- **🚀 Message all** = for every contactable, not-yet-contacted business: auto-send the email ones (if
  email is connected) and queue the WhatsApp ones. It reports `sent` (delivered) vs `drafted` (waiting
  for your tap).
- **International by default:** phone numbers are normalized with Google's **libphonenumber**
  (`phonenumbers`), so WhatsApp links and mobile-vs-landline detection are correct **worldwide**
  (a London or NY number is handled as accurately as an Indian one). Default region is `IN` for bare
  local numbers.
- For real WhatsApp auto-send at scale, use the paid **WhatsApp Business API** behind the same adapter.

## Phase 3 — Follow-up reminders

When a lead is interested, Scopio doesn't force a rigid calendar booking — it records a
**dated call reminder** ("call Tasty Cafe on Fri") that the AI (or the user) sets, then
surfaces it in the dashboard and flips the business's CRM status to `callback_scheduled`.
Built free, no calendar account needed:

- Each reminder **auto-mints a unique [Jitsi Meet](https://meet.jit.si) room** both sides
  join at the call time, and the AI shares that link with the owner in its reply.
- `POST /reminders` — set a reminder (business, due date, channel, note). Auto-generates the
  meeting link; stores the contact in international (E.164) form.
- `GET /reminders` — the tenant's reminders, soonest-due first (RLS-scoped).
- `PATCH /reminders/{id}` — change the date, add a note, or mark `done` / `cancelled`.
- `DELETE /reminders/{id}` — remove a reminder.
- `GET /reminders/{id}/invite.ics` — download a standard **`.ics`** calendar invite (with a
  15-minute pop-up alarm) that adds the call to any device's calendar — Apple, Google, Outlook.
- Every reminder also exposes a **`google_calendar_url`** field — a one-tap "Add to Google Calendar"
  link (same call window + Jitsi room) for users who prefer Google over downloading the `.ics`.
- Dashboard: a **"⏰ Reminders"** panel (list, edit date, **🎥 Join**, **🔗 Copy link**,
  **📅 Add to calendar** (`.ics`), **📆 Google Calendar**, **📞 Call now**, mark done, delete), an
  **"⏰ Set reminder"** button on each business, and overdue reminders flagged. The AI adds one
  automatically when an owner agrees. (The calendar links live in the dashboard/API only — never in
  the owner's outreach message.)

> **Timing/format settings:** `REMINDER_DEFAULT_DAYS` (default 2) and `REMINDER_DEFAULT_HOUR`
> (default 10, in the tenant's timezone) pick the call time when the owner gives no specific date;
> `JITSI_BASE_URL` points the meeting link at a self-hosted Jitsi if you have one.

> **Auth:** real multi-tenant auth is live (register/login + HMAC-signed tokens — see *Accounts &
> onboarding* above). In development a **"Skip (demo)"** option falls back to the seeded dev tenant;
> with `ENVIRONMENT=production` every data route requires a valid token (no demo fallback).

### Run the tests

```bash
pip install -e ".[dev]"
pytest                      # pure-logic tests (normalizer, dedup) need no infra
```

### Run the agent evals

The agents are **measured, not vibes-checked** — see [`evals/README.md`](evals/README.md)
for the full methodology (frozen-corpus extraction eval with hallucination traps,
exact-match intent scoring, LLM-as-judge for message quality).

```bash
python -m evals             # full scorecard (needs GROQ_API_KEY in .env)
python -m evals --gate      # CI-style quality gate: fails if a metric regresses
```

### Notes / limits (by design, see PHASE1_DESIGN.md §B.3)
- OSM coverage is patchy in small towns and **phone numbers are often missing** — expected.
  Phase 2 adds AI website-enrichment; for now use CSV import to fill gaps.
- The public Nominatim/Overpass endpoints are rate-limited. For volume, **self-host** them
  (just point `NOMINATIM_URL` / `OVERPASS_URL` at your instances).
