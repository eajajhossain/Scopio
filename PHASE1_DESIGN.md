# Scopio — Phase 1 Design: Data Model + Discovery Service

> **Scope of this doc:** the foundation we actually build first.
> 1. The **multi-tenant data model** (Postgres) — the backbone for everything.
> 2. The **Discovery Service** — address in → business list out, using free OSM/Overpass + Nominatim + CSV import.
>
> Goal of Phase 1: a working **end-to-end pipeline** (type an address → see real businesses on a dashboard).
> Data completeness is *not* the goal yet — proving the loop is. Stack: **FastAPI + PostgreSQL + Redis**.

---

## Part A — Multi-Tenant Data Model

### A.1 Principles
- **Every business row carries `tenant_id`.** Isolation enforced via Postgres **Row-Level Security (RLS)**.
- **UUID primary keys** (`uuid_generate_v4()`) — safe for multi-tenant, no guessable IDs.
- **Timestamps everywhere** (`created_at`, `updated_at`) — audit + debugging.
- **Soft deletes** (`deleted_at`) for anything user-facing (leads, businesses) — never hard-delete contact data we may need for compliance/audit.
- **Enums as Postgres enum types** for lifecycle states — DB-enforced validity.

### A.2 Entity overview

```
tenant ──< user
   │
   └──< search_job ──< business ──< lead ──< outreach_attempt   (outreach = later phase)
                          │            │
                          │            └──< conversation ──< message   (later phase)
                          │
                          └──(cache)── area_cache
```

Phase 1 builds the **bold** tables; the rest are stubs/migrations we leave room for.

| Table | Phase | Purpose |
|---|---|---|
| **`tenant`** | 1 | An account/organization (you, and every future user). |
| **`user`** | 1 | A person who logs in, belongs to a tenant. |
| **`search_job`** | 1 | One "find businesses near this address" request + its status. |
| **`business`** | 1 | A discovered business (the core entity). |
| `area_cache` | 1 | Cache of Overpass results per area, to avoid re-querying. |
| `lead` | 2 | A business a tenant decides to pursue (pipeline state). |
| `outreach_attempt` | 2 | One contact attempt over a channel. |
| `conversation` / `message` | 2 | The AI ↔ business dialogue + transcript. |
| `do_not_contact` | 2 | Opt-out / suppression list (compliance). |

### A.3 Phase-1 tables (DDL sketch)

```sql
-- ENUMS ---------------------------------------------------------------
CREATE TYPE search_status   AS ENUM ('pending','geocoding','querying','enriching','completed','failed');
CREATE TYPE business_source AS ENUM ('osm','manual_import','google_places'); -- google_places = future
CREATE TYPE user_role       AS ENUM ('owner','agent','viewer');

-- TENANT --------------------------------------------------------------
CREATE TABLE tenant (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    country     TEXT NOT NULL DEFAULT 'IN',        -- India first
    timezone    TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- USER ----------------------------------------------------------------
CREATE TABLE app_user (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id),
    email       TEXT NOT NULL UNIQUE,
    full_name   TEXT,
    role        user_role NOT NULL DEFAULT 'owner',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- SEARCH JOB ----------------------------------------------------------
CREATE TABLE search_job (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id),
    created_by   UUID NOT NULL REFERENCES app_user(id),
    raw_address  TEXT NOT NULL,                    -- "Barasat, 700125"
    center_lat   DOUBLE PRECISION,                 -- filled after geocoding
    center_lng   DOUBLE PRECISION,
    radius_m     INTEGER NOT NULL DEFAULT 2000,    -- search radius in meters
    category     TEXT,                             -- optional filter, e.g. 'retail'
    status       search_status NOT NULL DEFAULT 'pending',
    error        TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- BUSINESS ------------------------------------------------------------
CREATE TABLE business (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id),
    search_job_id UUID REFERENCES search_job(id),
    source        business_source NOT NULL,
    source_ref    TEXT,                            -- e.g. OSM "node/123456"
    name          TEXT NOT NULL,
    category      TEXT,                            -- normalized: 'restaurant','retail',...
    address       TEXT,
    lat           DOUBLE PRECISION,
    lng           DOUBLE PRECISION,
    phone         TEXT,
    email         TEXT,
    website       TEXT,
    raw           JSONB,                           -- full original payload from source
    fit_score     NUMERIC(4,3),                    -- 0..1, filled later
    confidence    NUMERIC(4,3),                    -- data-confidence, filled later
    dedup_key     TEXT,                            -- normalized name+geo, for de-dup
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ,
    UNIQUE (tenant_id, dedup_key)                  -- prevents duplicates per tenant
);
CREATE INDEX idx_business_tenant     ON business(tenant_id);
CREATE INDEX idx_business_searchjob  ON business(search_job_id);

-- AREA CACHE (avoid re-querying Overpass for the same area) ------------
CREATE TABLE area_cache (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    geohash      TEXT NOT NULL,                    -- area key (geohash of center)
    radius_m     INTEGER NOT NULL,
    category     TEXT,
    payload      JSONB NOT NULL,                   -- normalized businesses
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,             -- e.g. now() + 30 days
    UNIQUE (geohash, radius_m, category)
);
```

### A.4 Multi-tenant isolation (RLS)
```sql
ALTER TABLE business ENABLE ROW LEVEL SECURITY;
ALTER TABLE business FORCE  ROW LEVEL SECURITY;     -- applies even to the owner
CREATE POLICY tenant_isolation ON business
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```
The API sets `app.tenant_id` per request (session-level, so it survives multiple commits).

> ⚠️ **Learned during verification:** Postgres **superusers and table owners BYPASS RLS**. So the
> app must connect as a dedicated **non-superuser role** (`app_rls`) — not the `scopio` admin that
> owns the schema. We caught this because a second tenant could initially see another's data. The
> admin user runs migrations; `app_rls` runs the app and is subject to every policy.

`area_cache` is **shared across tenants** (public OSM data) — intentional, saves cost.

### A.5 search_job ↔ business is many-to-many
A business can be discovered by **multiple** searches (overlapping areas, repeat runs). Storing a
single `search_job_id` on `business` is wrong — the dedup upsert would reassign it to the latest
job and "steal" the business from earlier searches (another bug verification caught). So a link
table records every (job, business) pairing:
```sql
CREATE TABLE search_job_business (
    search_job_id UUID REFERENCES search_job(id) ON DELETE CASCADE,
    business_id   UUID REFERENCES business(id)   ON DELETE CASCADE,
    PRIMARY KEY (search_job_id, business_id)
);
```
`business.search_job_id` is kept only as "first discovered by"; listings join through the link table.

---

## Part B — Discovery Service

### B.1 Responsibility
Take an address (or coordinates) + radius → return a normalized, de-duplicated list of businesses,
persisted and cached. Runs as an **async job** because Overpass queries can take seconds.

### B.2 Flow

```
POST /search_jobs                 (user submits address)
   │
   ▼
[1] Create search_job (status=pending) → return job id immediately
   │   enqueue background task (Celery/ARQ)
   ▼
[2] GEOCODE  (status=geocoding)
   │   Nominatim: "Barasat, 700125" → (22.72, 88.48)
   │   cache geocoding results in Redis (address is stable)
   ▼
[3] CACHE CHECK
   │   geohash(center)+radius+category in area_cache & not expired?
   │      → yes: use cached payload (skip Overpass)  ──┐
   │      → no:  continue                              │
   ▼                                                   │
[4] QUERY  (status=querying)                           │
   │   Overpass API: businesses within radius          │
   │   (nodes/ways tagged shop=*, amenity=*, office=*) │
   │   store raw payload in area_cache (expires +30d)  │
   ▼                                                   │
[5] NORMALIZE  ◄───────────────────────────────────────┘
   │   map each OSM element → Business shape
   │   normalize category (OSM tags → our taxonomy)
   ▼
[6] DEDUP
   │   compute dedup_key = slug(name) + geohash(lat,lng, precision~7)
   │   upsert into business (ON CONFLICT (tenant_id,dedup_key) DO UPDATE)
   ▼
[7] COMPLETE  (status=completed, result_count=N)
```

### B.3 OSM / Overpass details
- **Geocoding:** Nominatim `https://nominatim.openstreetmap.org/search?q=...&format=json`.
  Must set a **User-Agent** identifying Scopio (Nominatim usage policy) and **rate-limit to ~1 req/s**
  on the public instance. Cache results.
- **Business query:** Overpass QL over a radius. Example (businesses within 2 km of a point):
  ```overpassql
  [out:json][timeout:25];
  (
    node["shop"](around:2000, 22.72, 88.48);
    way ["shop"](around:2000, 22.72, 88.48);
    node["amenity"~"restaurant|cafe|bank|pharmacy"](around:2000, 22.72, 88.48);
    node["office"](around:2000, 22.72, 88.48);
  );
  out center tags;
  ```
- **Tags we read:** `name`, `shop`/`amenity`/`office` (→ category), `addr:*` (→ address),
  `phone`/`contact:phone`, `website`/`contact:website`, `email`.
- **Reality check:** many results will have a name + location but **no phone** — expected. Phase 2
  enrichment fills those; for now we surface what exists and flag missing contacts in the UI.

### B.4 Category normalization
OSM has hundreds of tag values. We map them into a small internal taxonomy so the UI/filtering is sane:

| Internal category | OSM tags (examples) |
|---|---|
| `food` | amenity=restaurant, cafe, fast_food; shop=bakery |
| `retail` | shop=clothes, electronics, supermarket, hardware |
| `health` | amenity=pharmacy, clinic, doctors; shop=chemist |
| `services` | office=*, shop=laundry, beauty, hairdresser |
| `finance` | amenity=bank, atm |
| `other` | anything unmatched |

### B.5 De-duplication
- `dedup_key = slugify(name) + "_" + geohash(lat, lng, precision=7)` (~150 m cell).
- Same name in the same cell → same business → upsert (don't insert twice).
- Cross-source merge (OSM vs manual vs future Google) lands here too: prefer non-null contact fields.

### B.6 Manual CSV import (fills OSM gaps cheaply)
- `POST /search_jobs/{id}/import` with a CSV: `name,category,address,phone,email,website,lat,lng`.
- Each row → `business` with `source='manual_import'`, run through the same normalize + dedup path.
- Lets you seed Barasat businesses you know that OSM is missing — keeping Phase 1 useful immediately.

---

## Part C — API Contracts (Phase 1)

```
POST   /search_jobs
       body: { raw_address, radius_m?, category? }
       resp: { id, status: "pending" }                      # 202 Accepted, async

GET    /search_jobs/{id}
       resp: { id, status, center_lat, center_lng, radius_m, result_count, error? }

GET    /search_jobs/{id}/businesses?limit=&offset=&category=
       resp: { items: [ Business... ], total }

POST   /search_jobs/{id}/import        # multipart CSV
       resp: { imported, skipped, errors: [...] }

GET    /businesses/{id}
PATCH  /businesses/{id}                # manual edit/correct a record
DELETE /businesses/{id}                # soft delete
```

**`Business` response shape:**
```json
{
  "id": "uuid",
  "name": "Maa Tara Sweets",
  "category": "food",
  "address": "Jessore Rd, Barasat, 700125",
  "lat": 22.7211, "lng": 88.4827,
  "phone": null,
  "email": null,
  "website": null,
  "source": "osm",
  "fit_score": null,
  "confidence": null,
  "has_contact": false
}
```

---

## Part D — Suggested Project Structure (FastAPI)

```
scopio/
├─ app/
│  ├─ main.py                  # FastAPI app + router registration
│  ├─ core/                    # config, db session, auth, RLS context
│  ├─ models/                  # SQLAlchemy models (tenant, user, search_job, business, area_cache)
│  ├─ schemas/                 # Pydantic request/response models
│  ├─ api/
│  │  ├─ search_jobs.py
│  │  └─ businesses.py
│  ├─ services/
│  │  ├─ discovery/
│  │  │  ├─ geocoder.py        # Nominatim adapter (behind an interface)
│  │  │  ├─ overpass.py        # Overpass adapter (behind an interface)
│  │  │  ├─ normalizer.py      # OSM → Business, category mapping
│  │  │  ├─ dedup.py
│  │  │  └─ pipeline.py        # orchestrates the B.2 flow
│  │  └─ importer/csv_import.py
│  ├─ workers/                 # Celery/ARQ tasks (run_discovery_job)
│  └─ db/                      # Alembic migrations
├─ tests/
├─ docker-compose.yml          # postgres + redis + api + worker
└─ pyproject.toml
```

> **Adapter discipline:** `geocoder.py` and `overpass.py` implement small interfaces
> (`GeocoderPort`, `PlacesPort`). When we later add Google Places or a self-hosted Overpass,
> we add an adapter — no changes to the pipeline.

---

## Part E — Phase 1 Definition of Done

- [ ] `docker-compose up` brings up Postgres + Redis + API + worker.
- [ ] DB migrations create the Phase-1 tables with RLS on `business`.
- [ ] `POST /search_jobs` with "Barasat, 700125" returns a job; worker geocodes + queries Overpass.
- [ ] `GET /search_jobs/{id}/businesses` returns real, de-duplicated Barasat businesses.
- [ ] Area results are cached (second identical search is instant, no Overpass call).
- [ ] CSV import adds businesses and de-dups against existing.
- [ ] Multi-tenant isolation verified (tenant A cannot see tenant B's businesses).
- [ ] Basic tests for normalizer, dedup, and the search-job state machine.

---

*Next step after sign-off: scaffold the FastAPI project + docker-compose + migrations, then implement
the Discovery pipeline (geocoder → overpass → normalize → dedup) against live Barasat data.*
```
