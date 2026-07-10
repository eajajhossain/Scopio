-- Scopio Phase 1 schema. Runs automatically on first Postgres boot (docker-compose).
-- Enums + tables + Row-Level Security + a dev tenant/user seed.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ENUMS ---------------------------------------------------------------
CREATE TYPE search_status   AS ENUM ('pending','geocoding','querying','enriching','completed','failed');
CREATE TYPE business_source AS ENUM ('osm','manual_import','google_places','geoapify');
CREATE TYPE user_role       AS ENUM ('owner','agent','viewer');
CREATE TYPE lead_status     AS ENUM ('discovered','contacted','interested','callback_scheduled','meeting_booked','not_interested','do_not_contact');
CREATE TYPE reminder_status AS ENUM ('pending','done','cancelled');
CREATE TYPE conversation_status AS ENUM ('active','interested','callback_scheduled','meeting_booked','not_interested','closed');

-- TENANT --------------------------------------------------------------
CREATE TABLE tenant (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name         TEXT NOT NULL,
    company_name TEXT,                              -- the user's business name (sender)
    services     TEXT,                              -- what the user offers (AI sales context)
    smtp_email   TEXT,                              -- connected email for auto-send
    smtp_password TEXT,                             -- app password (encrypt in production!)
    smtp_host    TEXT DEFAULT 'smtp.gmail.com',
    smtp_port    INTEGER DEFAULT 587,
    country      TEXT NOT NULL DEFAULT 'IN',
    timezone     TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    -- 'review' = human-in-the-loop (AI queues drafts, the user approves each send);
    -- 'autonomous' = the AI sends directly. Review is the safe default for new accounts.
    outreach_mode TEXT NOT NULL DEFAULT 'review',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Idempotent for dev DBs created before HITL review mode existed.
ALTER TABLE tenant ADD COLUMN IF NOT EXISTS outreach_mode TEXT NOT NULL DEFAULT 'review';

-- USER ----------------------------------------------------------------
CREATE TABLE app_user (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id),
    email         TEXT NOT NULL UNIQUE,
    full_name     TEXT,
    password_hash TEXT,                             -- null = cannot log in (e.g. dev seed)
    role          user_role NOT NULL DEFAULT 'owner',
    last_login_at TIMESTAMPTZ,
    login_count   INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Idempotent for dev DBs created before login tracking existed.
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS login_count INTEGER NOT NULL DEFAULT 0;

-- SEARCH JOB ----------------------------------------------------------
CREATE TABLE search_job (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id),
    created_by   UUID NOT NULL REFERENCES app_user(id),
    raw_address  TEXT NOT NULL,
    center_lat   DOUBLE PRECISION,
    center_lng   DOUBLE PRECISION,
    radius_m     INTEGER NOT NULL DEFAULT 2000,
    category     TEXT,
    target_profile JSONB,
    status       search_status NOT NULL DEFAULT 'pending',
    error        TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Idempotent for dev DBs created before target_profile existed.
ALTER TABLE search_job ADD COLUMN IF NOT EXISTS target_profile JSONB;
CREATE INDEX idx_searchjob_tenant ON search_job(tenant_id);

-- BUSINESS ------------------------------------------------------------
CREATE TABLE business (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id),
    search_job_id UUID REFERENCES search_job(id),
    source        business_source NOT NULL,
    source_ref    TEXT,
    name          TEXT NOT NULL,
    category      TEXT,
    address       TEXT,
    lat           DOUBLE PRECISION,
    lng           DOUBLE PRECISION,
    phone         TEXT,
    email         TEXT,
    website       TEXT,
    raw           JSONB,
    fit_score     NUMERIC(4,3),
    confidence    NUMERIC(4,3),
    dedup_key     TEXT NOT NULL,
    enriched_at   TIMESTAMPTZ,                      -- set when AI enrichment has run
    details       JSONB,                            -- rich profile: hours, description, socials...
    status        lead_status NOT NULL DEFAULT 'discovered',  -- CRM lifecycle
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ,
    UNIQUE (tenant_id, dedup_key)
);
CREATE INDEX idx_business_tenant    ON business(tenant_id);
CREATE INDEX idx_business_searchjob ON business(search_job_id);

-- SEARCH_JOB <-> BUSINESS (many-to-many: a business can be found by many jobs) --
CREATE TABLE search_job_business (
    search_job_id UUID NOT NULL REFERENCES search_job(id) ON DELETE CASCADE,
    business_id   UUID NOT NULL REFERENCES business(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (search_job_id, business_id)
);
CREATE INDEX idx_sjb_job      ON search_job_business(search_job_id);
CREATE INDEX idx_sjb_business ON search_job_business(business_id);

-- REMINDER (a dated follow-up: "call this business on this date" — what the AI sets) --
CREATE TABLE reminder (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id      UUID NOT NULL REFERENCES tenant(id),
    business_id    UUID NOT NULL REFERENCES business(id) ON DELETE CASCADE,
    created_by     UUID REFERENCES app_user(id),
    due_at         TIMESTAMPTZ NOT NULL,             -- when to call / follow up
    timezone       TEXT NOT NULL,
    channel        TEXT,                             -- whatsapp | email | call
    business_name  TEXT,                             -- denormalized for fast listing
    contact        TEXT,                             -- phone/email to reach them on
    meeting_url    TEXT,                             -- auto-minted Jitsi room both sides join
    note           TEXT,
    status         reminder_status NOT NULL DEFAULT 'pending',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_reminder_tenant   ON reminder(tenant_id);
CREATE INDEX idx_reminder_business ON reminder(business_id);
CREATE INDEX idx_reminder_due      ON reminder(due_at);

-- CONVERSATION (the AI sales agent's dialogue with a business) --------------
CREATE TABLE conversation (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id),
    business_id  UUID NOT NULL REFERENCES business(id) ON DELETE CASCADE,
    channel      TEXT NOT NULL,                     -- email | whatsapp | sms
    status       conversation_status NOT NULL DEFAULT 'active',
    transcript   JSONB NOT NULL DEFAULT '[]',       -- [{role, text, ts}]
    reminder_id  UUID REFERENCES reminder(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_conversation_tenant   ON conversation(tenant_id);
CREATE INDEX idx_conversation_business ON conversation(business_id);

-- OUTREACH DRAFT (human-in-the-loop: AI-written messages awaiting user approval) --
CREATE TABLE outreach_draft (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenant(id),
    business_id     UUID NOT NULL REFERENCES business(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES conversation(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,                   -- opening | reply
    channel         TEXT NOT NULL,                   -- email (whatsapp is tap-to-send already)
    to_contact      TEXT NOT NULL,
    subject         TEXT,
    body            TEXT NOT NULL,                   -- user may edit before approving
    meta            JSONB,                           -- agent result (intent/set_reminder/…) for replies
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | sent | discarded
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_draft_tenant  ON outreach_draft(tenant_id);
CREATE INDEX idx_draft_status  ON outreach_draft(status);

-- AREA CACHE (shared across tenants — public OSM data) -----------------
CREATE TABLE area_cache (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    geohash      TEXT NOT NULL,
    radius_m     INTEGER NOT NULL,
    category     TEXT,
    payload      JSONB NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    UNIQUE (geohash, radius_m, category)
);

-- ROW-LEVEL SECURITY (tenant isolation backstop) ----------------------
-- The app sets `app.tenant_id` per request/transaction. Policies use it.
-- Note: a NULL/empty GUC matches nothing, so set it before querying.
ALTER TABLE business       ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_job     ENABLE ROW LEVEL SECURITY;
ALTER TABLE reminder       ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation   ENABLE ROW LEVEL SECURITY;
ALTER TABLE outreach_draft ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_business ON business
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
CREATE POLICY tenant_isolation_searchjob ON search_job
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
CREATE POLICY tenant_isolation_reminder ON reminder
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
CREATE POLICY tenant_isolation_conversation ON conversation
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
CREATE POLICY tenant_isolation_draft ON outreach_draft
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

-- The app MUST connect as a non-superuser, non-owner role, or RLS is bypassed.
-- (Postgres superusers and table owners ignore row-level security.)
-- FORCE makes the policy apply even to the owner, as defense-in-depth.
ALTER TABLE business       FORCE ROW LEVEL SECURITY;
ALTER TABLE search_job     FORCE ROW LEVEL SECURITY;
ALTER TABLE reminder       FORCE ROW LEVEL SECURITY;
ALTER TABLE conversation   FORCE ROW LEVEL SECURITY;
ALTER TABLE outreach_draft FORCE ROW LEVEL SECURITY;

CREATE ROLE app_rls LOGIN PASSWORD 'app_rls' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA public TO app_rls;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_rls;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rls;

-- DEV SEED (matches .env.example DEV_TENANT_ID / DEV_USER_ID) ----------
INSERT INTO tenant (id, name, company_name, services, country, timezone)
VALUES ('00000000-0000-0000-0000-000000000001', 'Dev Tenant', 'Scopio',
        'AI solutions for local businesses: a 24/7 AI assistant for WhatsApp/website, '
        'automated appointment booking, and follow-up automation.',
        'IN', 'Asia/Kolkata');

INSERT INTO app_user (id, tenant_id, email, full_name, role)
VALUES ('00000000-0000-0000-0000-000000000002',
        '00000000-0000-0000-0000-000000000001',
        'ramiz@codeday.org', 'Ramiz', 'owner');
