from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "dev" (auth optional, falls back to demo tenant) or "production" (auth required).
    environment: str = "dev"
    # Comma-separated allowed CORS origins (e.g. "https://app.scopio.com"). Empty = same-origin only.
    cors_origins: str = ""

    database_url: str = "postgresql+asyncpg://scopio:scopio@localhost:5432/scopio"
    redis_url: str = "redis://localhost:6379/0"

    # OpenStreetMap (free). Self-host to remove rate limits.
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"

    # Optional 2nd discovery source (free, no card): https://myprojects.geoapify.com
    # Merges with OSM results. Same interface fits TomTom/HERE later.
    geoapify_api_key: str | None = None
    geoapify_url: str = "https://api.geoapify.com/v2/places"
    # Nominatim policy requires an identifying User-Agent with contact info.
    http_user_agent: str = "Scopio/0.1 (contact: ramiz@codeday.org)"

    area_cache_ttl_days: int = 30

    # --- Phase 2: AI enrichment ---
    # If ANTHROPIC_API_KEY is set, the Claude extractor is used; otherwise a free
    # regex/heuristic extractor runs so the pipeline works without a key.
    anthropic_api_key: str | None = None
    enrichment_model: str = "claude-opus-4-8"
    enrichment_max_businesses: int = 25   # cost guard: cap per enrichment/research job
    enrichment_fetch_timeout: float = 15.0
    # Cooldown: a business enriched within this many days is skipped on the next
    # Enrich click, so each click advances through fresh businesses instead of
    # re-reading the same sites (slow, and they rarely change). 0 disables.
    enrichment_recheck_days: int = 14

    # Free / OpenAI-compatible LLM (Groq by default; also fits Gemini, Ollama,
    # OpenRouter — just change base_url + model). Used when no Anthropic key is set.
    groq_api_key: str | None = None       # env GROQ_API_KEY (free at console.groq.com)
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "llama-3.3-70b-versatile"      # enrichment (background, low volume)
    # Per-request timeout for the LLM. Cloud APIs are fast (40s is plenty); a local
    # CPU model reading a full webpage is much slower, so raise this (e.g. 180) for Ollama.
    llm_timeout: float = 40.0
    # Conversation agent uses a faster model with much higher free-tier rate limits,
    # so live chat doesn't get throttled into repetitive fallbacks.
    outreach_model: str = "llama-3.1-8b-instant"
    # The chat agent ALWAYS uses a capable cloud chat model (its own endpoint), so it
    # keeps working even when enrichment (llm_base_url) is pointed at a local model.
    outreach_base_url: str = "https://api.groq.com/openai/v1"

    # Phase 4c: find a website for businesses OSM doesn't have one for.
    # Uses Brave Search API if BRAVE_API_KEY is set (official), else a best-effort
    # no-key DuckDuckGo fallback. Set false to disable web-search discovery.
    enable_web_search: bool = True
    brave_api_key: str | None = None
    web_search_max: int = 30              # cap searches per enrichment job

    # --- Deep research agent (LangGraph + Tavily) ---
    # When a Tavily key is set and this is enabled, each candidate is researched by
    # the LangGraph deep agent (Tavily web search + reading the business's own site)
    # instead of the single website-read extractor. Without a key it falls back to
    # the website-read path, so nothing breaks before the key is provided.
    tavily_api_key: str | None = None     # env TAVILY_API_KEY (key arrives later)
    enable_deep_research: bool = True
    deep_research_max_searches: int = 4   # Tavily searches per business (loop cap)

    # --- Phase 3: follow-up reminders ---
    # When the owner agrees to a call but gives no timeframe, remind this many days out.
    reminder_default_days: int = 2
    reminder_default_hour: int = 10       # local hour (per tenant tz) to schedule the call at
    # Free, no-auth video room base. Each reminder mints a unique room both sides join.
    jitsi_base_url: str = "https://meet.jit.si"
    # Region used to interpret bare local numbers (no country code). Numbers that
    # already include a country code dial correctly in any country regardless.
    phone_default_region: str = "IN"

    # --- Autonomous inbound email agent ---
    # Poll each tenant's connected inbox (IMAP) for customer replies and let the AI
    # agent respond automatically. Uses the same email + app-password the tenant
    # connected for sending. Disable to turn off the auto-reply loop.
    inbox_poll_enabled: bool = True
    inbox_fetch_limit: int = 20           # max new messages processed per poll per tenant
    inbox_reply_max_turns: int = 12       # stop auto-replying once a thread gets this long

    # --- Outreach / AI sales agent ---
    outreach_sender_name: str = "Eajaj"
    outreach_company_name: str = "Scopio"
    outreach_bulk_max: int = 25            # cap per one-click bulk-outreach run
    # What the sales agent is selling — keep honest; shown to the model as context.
    outreach_services: str = (
        "AI solutions for local businesses: a 24/7 AI assistant that answers customer "
        "questions on WhatsApp/website, automated appointment booking, and follow-up "
        "automation that recovers missed leads — set up for you, no technical work needed."
    )

    # --- Observability (Langfuse) ---
    # When both keys are set, every LLM call and agent run is traced to Langfuse
    # (cloud free tier or self-hosted): per-run waterfalls, token counts, latency.
    # Without keys, tracing is a complete no-op — zero overhead, no extra deps used.
    langfuse_public_key: str | None = None   # env LANGFUSE_PUBLIC_KEY (pk-lf-…)
    langfuse_secret_key: str | None = None   # env LANGFUSE_SECRET_KEY (sk-lf-…)
    langfuse_host: str = "https://cloud.langfuse.com"

    # Token signing secret — override in production via SECRET_KEY env var.
    # Also keys the at-rest encryption of stored SMTP passwords (rotating it
    # means users reconnect their email).
    secret_key: str = "dev-secret-change-me-in-production"
    # Auth tokens expire after this many days (user logs in again).
    token_ttl_days: int = 7
    # Per-IP rate limit for the auth endpoints (login/register/connect_email):
    # this many requests per window. Backed by Redis; fails open if Redis is down.
    auth_rate_limit: int = 10
    auth_rate_window_seconds: int = 300

    # Dev fallback identity (used when no auth token is presented)
    dev_tenant_id: str = "00000000-0000-0000-0000-000000000001"
    dev_user_id: str = "00000000-0000-0000-0000-000000000002"

    # --- Admin (platform-owner) dashboard ---
    # Comma-separated emails granted the cross-tenant admin view. In dev, the demo
    # user is also treated as admin for convenience; in production only these emails.
    admin_emails: str = ""
    # Bootstrap admin: if both are set, the app ensures this login exists on startup
    # (created if missing; password kept in sync). This account is always an admin.
    # Lives only in .env (gitignored) — the private source of truth for your login.
    admin_email: str = ""
    admin_password: str = ""
    # Privileged DB URL used ONLY by admin reads — connects as the superuser so it can
    # see every tenant's rows (bypasses RLS). Never exposed to normal requests.
    admin_database_url: str = "postgresql+asyncpg://scopio:scopio@localhost:5432/scopio"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
