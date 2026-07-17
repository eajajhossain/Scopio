# Scopio — Production / Industry-Readiness Guide

This documents what's been hardened for production and the honest checklist of what
still needs doing before a real public launch. Scopio is a **solid, production-ready
foundation** — not yet a battle-tested SaaS. This guide is the gap list.

## Run it in production

```bash
# 1. Create .env from .env.example and set real values (see below)
cp .env.example .env

# 2. Generate a strong secret
python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> SECRET_KEY

# 3. Launch with the production overlay (4 API workers, no reload, restart policy)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Required env in production (the app refuses to start without a real `SECRET_KEY`):
`ENVIRONMENT=production`, `SECRET_KEY`, `POSTGRES_PASSWORD`, `APP_RLS_PASSWORD`.

Admin login: set `ADMIN_EMAIL` + `ADMIN_PASSWORD` (bootstraps/keeps your owner account in sync on
startup, password hashed at rest) and/or `ADMIN_EMAILS` (allow-list). Use a strong password.

## ✅ What's already done

- **Multi-tenant isolation** via Postgres Row-Level Security, enforced through a
  dedicated non-superuser DB role (`app_rls`). The tenant context is pinned to a single
  connection per request/job (`tenant_session`, see ARCHITECTURE.md §6) so multi-commit
  pipelines can't leak onto a pooled connection without the tenant set.
- **Auth**: register/login, PBKDF2-hashed passwords, HMAC-signed tokens.
- **`ENVIRONMENT=production`** requires a valid auth token on every data route (no demo
  fallback) and **refuses to boot with the default dev `SECRET_KEY`**.
- **Security headers** (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`)
  and configurable **CORS** (`CORS_ORIGINS`).
- **Graceful degradation**: works with no AI/discovery keys; external calls retry and
  fall back; one bad record never aborts a batch.
- **Scale-tested**: batched inserts (handles 5,000+ businesses), data-type coercion,
  multi-mirror Overpass failover, LLM rate-limit retries.
- **Worldwide-ready**: discovery works for any global address; phone/WhatsApp/click-to-call handling
  is internationalized via Google's **libphonenumber** (`phonenumbers`) — correct mobile-vs-landline
  detection, `wa.me` links, and E.164 `tel:` dial links per country (default region configurable via
  `PHONE_DEFAULT_REGION`, `IN` for bare local numbers).
- **Follow-up reminders**: the AI sets a dated call reminder on agreement, auto-mints a free **Jitsi
  Meet** link both sides join, and serves a standard **`.ics`** invite (with a device alarm) plus a
  one-tap **"Add to Google Calendar"** link (`google_calendar_url`) — no calendar account required.
  Surfaced in the dashboard with overdue flagging.
- **White-labeled UI**: no internal provider names (e.g. the LLM provider), raw error traces, or
  HTTP status codes are ever surfaced to end users; failures show friendly messages and are logged
  server-side only.
- **Admin control**: owner-only cross-tenant dashboard with the power to **suspend / reactivate
  any account** (admin accounts shielded from lockout); the owner login can be bootstrapped from
  `.env` (`ADMIN_EMAIL`/`ADMIN_PASSWORD`, hashed at rest).
- **Ask Scopio (agentic assistant)**: answers any question about the leads — the LLM plans, pulls
  answers from the database, and falls back to the **Tavily web tool** when needed; lists/filters by
  precise business type and exports the full set to Excel/CSV with clickable Google Maps links.
- **Easy email onboarding**: SMTP provider auto-detected from the address (Gmail/Outlook/Yahoo/
  iCloud/Zoho…); the user pastes only an app password, stored **encrypted at rest** (Fernet).
- **Tests + CI**: pytest suite + ruff lint, run on every push (`.github/workflows/ci.yml`).
- **Production compose**: multiple workers, no source mount, restart policy, secrets from env.

## 🔲 Before a real public launch (the honest gap list)

**Security**
- [x] **Encrypt the stored SMTP app-password at rest** — done: Fernet (`cryptography`) keyed from
      `SECRET_KEY`, `enc$`-prefixed, legacy plaintext rows still read (`app/core/security.py`).
- [x] **Auth-token expiry** — done: tokens carry an `exp` claim (`token_ttl_days`, default 7) and
      legacy expiry-less tokens are rejected.
- [x] **Rate-limit the auth endpoints** (login/register/connect_email) — done: Redis fixed-window
      per-IP (`app/core/ratelimit.py`), fails open if Redis is down. *(Outreach endpoints not yet limited.)*
- [ ] Move all secrets to a real **secrets manager** (AWS Secrets Manager / Vault), not `.env`
      (note: rotating `SECRET_KEY` invalidates encrypted SMTP passwords → users reconnect email).
- [ ] Put the API behind **HTTPS/TLS** (reverse proxy: Caddy/Nginx/Traefik) — tokens must never travel
      plaintext. *(The `DEPLOY.md` runbook includes an optional Caddy auto-TLS step.)*
- [ ] Rotate the dev `app_rls` / Postgres passwords; least-privilege review.

**Data & infra**
- [ ] **Managed Postgres** (RDS/Cloud SQL) with automated **backups** + PITR; run schema via real **migrations** (Alembic) instead of `init.sql`.
- [ ] Self-host **Overpass + Nominatim** (or pay for capacity) — public endpoints are rate-limited and unreliable at volume.
- [ ] Object storage for any future file artifacts; Redis persistence/HA for the job queue.

**Observability**
- [ ] Structured logging + request IDs, error tracking (Sentry), metrics/tracing (OpenTelemetry), uptime + cost dashboards.

**Compliance (critical for outreach — see ARCHITECTURE.md §8)**
- [ ] **Email**: unsubscribe link + honor opt-outs (CAN-SPAM / GDPR); a `do_not_contact` suppression list.
- [ ] **WhatsApp**: only via the official Business API with approved templates + opt-in (never personal-number automation).
- [ ] **India**: TRAI / DND / DLT registration before any SMS; respect business hours & timezones.
- [ ] Per-tenant **budget caps** for paid APIs; audit log of every message sent.

**Product polish**
- [ ] Pagination on large result sets in the UI; background-job status surfaced to the user.
- [ ] Optional per-tenant calendar sync (Google Calendar / Microsoft) layered on the reminders
      service, plus a public base URL so the `.ics` invite link is reachable by remote owners.
- [ ] Email-deliverability setup (SPF/DKIM/DMARC on the sending domain).

## Bottom line
The architecture, security model, and core flows are production-grade. The remaining
items are the standard "make it a real hosted SaaS" work — secrets management,
encryption, managed data stores, observability, and outreach compliance. Tackle the
**Security** and **Compliance** sections first; they're the launch blockers.
