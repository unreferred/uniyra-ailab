"""Environment / configuration boundary. All process config is read here."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")


def _env_bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _split_csv(name: str) -> list:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
JWT_SECRET = os.environ["JWT_SECRET"]
ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"]
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

# Admin seeding is an explicit opt-in. There are deliberately NO default
# credentials baked into the source: an admin is only created when
# ENABLE_ADMIN_SEED is true AND both ADMIN_EMAIL and ADMIN_PASSWORD are set.
ENV = os.environ.get("OMNILAB_ENV", "development").strip().lower()
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL") or None
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or None
ENABLE_ADMIN_SEED = _env_bool("ENABLE_ADMIN_SEED", "false")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 60 * 12
REFRESH_TOKEN_DAYS = 7

# ---------------------------------------------------------------- cookies
# Cookies default to Secure + SameSite=Lax, which keeps plain-http localhost
# development working while remaining safe for production. Cross-site
# deployments must set COOKIE_SAMESITE=none, which forces Secure=true.
COOKIE_SECURE = _env_bool("COOKIE_SECURE", "true")
_samesite = os.environ.get("COOKIE_SAMESITE", "lax").strip().lower()
if _samesite not in ("lax", "strict", "none"):
    _samesite = "lax"
if _samesite == "none":
    COOKIE_SECURE = True
COOKIE_SAMESITE = _samesite
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN") or None

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

# ---------------------------------------------------------------- CORS
# Explicit origin list only — never a wildcard when credentials are allowed.
# In production only CORS_ORIGINS / FRONTEND_URL are honoured; development
# additionally allows the local React ports on both loopback hostnames plus
# Tailscale (100.64.0.0/10, *.ts.net) and RFC1918 LAN origins so one backend
# serves both localhost and http://<tailscale-ip>:<port> without per-IP config.
CORS_ORIGINS = _split_csv("CORS_ORIGINS")


def _add_origin(origin: str) -> None:
    origin = (origin or "").strip().rstrip("/")
    if origin and origin != "*" and origin not in CORS_ORIGINS:
        CORS_ORIGINS.append(origin)


_add_origin(FRONTEND_URL)
if ENV != "production":
    for _port in ("3000", "3002", "3003"):
        _add_origin(f"http://localhost:{_port}")
        _add_origin(f"http://127.0.0.1:{_port}")

# Regex (string form) for dev-time LAN/Tailscale origins. Used both by the
# Starlette CORSMiddleware (allow_origin_regex) and the CSRF origin check so
# the two stay in sync. Production leaves this as None (explicit list only).
CORS_ORIGIN_REGEX: str | None = None
if ENV != "production":
    # localhost, loopback, Tailscale CGNAT 100.x, RFC1918 LAN, *.ts.net
    CORS_ORIGIN_REGEX = (
        r"https?://"
        r"(localhost|127\.0\.0\.1"
        r"|100\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|30|31)\.\d{1,3}\.\d{1,3}"
        r"|[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?\.ts\.net"
        r")(:\d+)?"
    )


def is_origin_allowed(origin: str) -> bool:
    """True when an Origin header value is permitted (explicit or dev-LAN)."""
    if not origin:
        return False
    normalized = origin.strip().rstrip("/")
    if normalized in CORS_ORIGINS:
        return True
    if CORS_ORIGIN_REGEX is None:
        return False
    import re as _re
    try:
        return _re.fullmatch(CORS_ORIGIN_REGEX, normalized) is not None
    except Exception:
        return False

# ---------------------------------------------------------------- provider URL safety (SSRF)
# Local-development override for provider URLs. When true, loopback/private
# targets are permitted for local gateways (Ollama, LiteLLM, vLLM, ...); cloud
# metadata addresses remain blocked unless explicitly allowlisted. Production
# default is false (strict).
PROVIDER_ALLOW_LOCAL_URLS = _env_bool("PROVIDER_ALLOW_LOCAL_URLS", "false")
# Comma-separated host or host:port entries that permit otherwise-blocked
# provider targets (including cloud metadata) on purpose.
PROVIDER_URL_ALLOWLIST = _split_csv("PROVIDER_URL_ALLOWLIST")
# Permit private (RFC1918 / unique-local) provider targets without opening up
# loopback or link-local addresses.
PROVIDER_URL_ALLOW_PRIVATE = _env_bool("PROVIDER_URL_ALLOW_PRIVATE", "false")


# ---------------------------------------------------------------- benchmark runner
# Phase 2 (part B): bounded, resumable benchmark execution. All values are
# additive and safe defaults; env overrides use the same simple parsing style.
BENCHMARK_CONCURRENCY = int(os.environ.get("BENCHMARK_CONCURRENCY", "3"))
BENCHMARK_MAX_CONCURRENCY = int(os.environ.get("BENCHMARK_MAX_CONCURRENCY", "10"))
BENCHMARK_CONFIRM_THRESHOLD = int(os.environ.get("BENCHMARK_CONFIRM_THRESHOLD", "50"))
BENCHMARK_MAX_PLANNED_CALLS = int(os.environ.get("BENCHMARK_MAX_PLANNED_CALLS", "2000"))
BENCHMARK_MAX_RETRIES = int(os.environ.get("BENCHMARK_MAX_RETRIES", "1"))
BENCHMARK_RETRY_BACKOFF_SECONDS = float(
    os.environ.get("BENCHMARK_RETRY_BACKOFF_SECONDS", "1.0")
)
RUNNER_VERSION = "benchmark-runner/1.0.0"
SCHEMA_VERSION = 1

# ---------------------------------------------------------------- Phase 3 tracing / context / tools
ATTACHMENT_STORAGE_DIR = os.environ.get("ATTACHMENT_STORAGE_DIR") or str(ROOT_DIR / "data" / "attachments")
MAX_ATTACHMENT_SIZE_MB = int(os.environ.get("MAX_ATTACHMENT_SIZE_MB", "20"))
MAX_ATTACHMENTS_PER_MESSAGE = int(os.environ.get("MAX_ATTACHMENTS_PER_MESSAGE", "10"))
MAX_TOTAL_ATTACHMENT_SIZE_MB = int(os.environ.get("MAX_TOTAL_ATTACHMENT_SIZE_MB", "50"))
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "5"))
MAX_CONSECUTIVE_NO_PROGRESS_ROUNDS = int(os.environ.get("MAX_CONSECUTIVE_NO_PROGRESS_ROUNDS","2"))
MAX_FINALIZATION_ATTEMPTS = int(os.environ.get("MAX_FINALIZATION_ATTEMPTS", "2"))

# ---------------------------------------------------------------- Phase 4.2.3.13 finalization token budget
# V4.2.3.13: forced finalization gets its OWN output budget, separate from
# ordinary continuation, so a reasoning-capable model cannot spend the whole
# completion budget on hidden reasoning and leave zero room for the visible
# answer. These are emergency/runtime synthesis budgets, NOT user quota.
#   * FINALIZATION_MAX_OUTPUT_TOKENS: attempt-1 forced-finalization budget.
#   * FINALIZATION_RECOVERY_MAX_OUTPUT_TOKENS: recovery (attempt-2) budget,
#     deliberately larger so a LENGTH-truncated first attempt has fresh room.
#   * FINALIZATION_PARTIAL_MIN_CHARS: minimum visible answer length below which
#     a `finish_reason="length"` response is treated as an unusable fragment.
#   * FINALIZATION_EVIDENCE_VIEW_MODE: "auto" | "full" | "evidence".
#   * FINALIZATION_EVIDENCE_VIEW_THRESHOLD_TOKENS: "auto" switches to the
#     bounded evidence projection above this estimated input size.
#   * FINALIZATION_EVIDENCE_VIEW_MAX_CHARS: hard cap on the projection blob.
#   * FINALIZATION_REASONING_EFFORT: effort requested during finalization ONLY
#     when the model explicitly declares reasoning_effort capability.
FINALIZATION_MAX_OUTPUT_TOKENS = int(os.environ.get("FINALIZATION_MAX_OUTPUT_TOKENS", "8192"))
FINALIZATION_RECOVERY_MAX_OUTPUT_TOKENS = int(os.environ.get("FINALIZATION_RECOVERY_MAX_OUTPUT_TOKENS", "16384"))
FINALIZATION_PARTIAL_MIN_CHARS = int(os.environ.get("FINALIZATION_PARTIAL_MIN_CHARS", "400"))
FINALIZATION_EVIDENCE_VIEW_MODE = os.environ.get("FINALIZATION_EVIDENCE_VIEW_MODE", "auto").strip().lower()
FINALIZATION_EVIDENCE_VIEW_THRESHOLD_TOKENS = int(os.environ.get("FINALIZATION_EVIDENCE_VIEW_THRESHOLD_TOKENS", "20000"))
FINALIZATION_EVIDENCE_VIEW_MAX_CHARS = int(os.environ.get("FINALIZATION_EVIDENCE_VIEW_MAX_CHARS", "24000"))
FINALIZATION_REASONING_EFFORT = os.environ.get("FINALIZATION_REASONING_EFFORT", "low").strip().lower()
MAX_SEARCH_CALLS_PER_RUN = int(os.environ.get("MAX_SEARCH_CALLS_PER_RUN","30"))
MAX_FETCH_CALLS_PER_RUN = int(os.environ.get("MAX_FETCH_CALLS_PER_RUN","100"))
MAX_FETCH_URLS_PER_RUN = int(os.environ.get("MAX_FETCH_URLS_PER_RUN", os.environ.get("MAX_FETCH_CALLS_PER_RUN","100")))
MAX_TOTAL_TOOL_CALLS_PER_RUN = int(os.environ.get("MAX_TOTAL_TOOL_CALLS_PER_RUN","150"))
# ---------------------------------------------------------------- Phase 4.2.3.7 fetch resilience
# Formal timeout hierarchy (three distinct layers):
#   1. PROVIDER_CONNECTION_TIMEOUT_SECONDS: provider-level connection/
#      streaming ceiling (adapter-owned, not user-configurable).
#   2. FETCH_REQUEST_TIMEOUT_MAX_SECONDS / SEARCH_REQUEST_TIMEOUT_MAX_SECONDS:
#      protected server ceiling for an individual URL fetch / search request.
#      Provider valves may configure below these, never above.
#   3. AGENT_TOOL_BUDGET_SECONDS: whole agentic tool-run wall-clock budget
#      (enforced by the execution loop).
PROVIDER_CONNECTION_TIMEOUT_SECONDS = int(os.environ.get("PROVIDER_CONNECTION_TIMEOUT_SECONDS", "120"))
FETCH_REQUEST_TIMEOUT_MAX_SECONDS = int(os.environ.get("FETCH_REQUEST_TIMEOUT_MAX_SECONDS", "60"))
SEARCH_REQUEST_TIMEOUT_MAX_SECONDS = int(os.environ.get("SEARCH_REQUEST_TIMEOUT_MAX_SECONDS", "60"))
AGENT_TOOL_BUDGET_SECONDS = int(os.environ.get("AGENT_TOOL_BUDGET_SECONDS", "180"))
# ---------------------------------------------------------------- Phase 4.2.3.12 model-call watchdog
# Additive server-side model-call watchdog (V4.2.3.12). These are the fourth
# timeout layer and are distinct from PROVIDER_CONNECTION_TIMEOUT_SECONDS
# (which is the per-request httpx ceiling owned by the adapter):
#   * MODEL_CONNECTION_TIMEOUT_SECONDS: the provider did not START a usable
#     response (no streamed event arrived at all).
#   * MODEL_IDLE_TIMEOUT_SECONDS: the stream started but no meaningful event
#     arrived within this gap; every event resets the idle clock.
#   * MODEL_CALL_MAX_DURATION_SECONDS: emergency wall-clock ceiling for one
#     model call regardless of streaming activity.
# Values are server-side config only: no LLM-provider Valve schema exists, so
# they are NOT exposed as provider/project valves.
MODEL_CONNECTION_TIMEOUT_SECONDS = int(os.environ.get("MODEL_CONNECTION_TIMEOUT_SECONDS", "60"))
MODEL_IDLE_TIMEOUT_SECONDS = int(os.environ.get("MODEL_IDLE_TIMEOUT_SECONDS", "120"))
MODEL_CALL_MAX_DURATION_SECONDS = int(os.environ.get("MODEL_CALL_MAX_DURATION_SECONDS", "600"))

# ---------------------------------------------------------------- Phase 4.2.3.8 batch fetch
# Additive limits for OmniLab V4.2.3.8 web_research. These do NOT change any
# existing single-URL fetch behaviour or output contract.
MAX_BATCH_FETCH_URLS = int(os.environ.get("MAX_BATCH_FETCH_URLS", "5"))
FETCH_BATCH_CONCURRENCY = int(os.environ.get("FETCH_BATCH_CONCURRENCY", "2"))
# MAX_RESEARCH_CALLS_PER_RUN is an emergency-only circuit breaker, NOT the
# practical research throttle: TinyFish provider quota (search RPM/hourly and
# fetch URLs per minute/day) is the intended practical limit. The composite
# ``web_research`` call count rarely reaches this ceiling in Lab mode.
MAX_RESEARCH_CALLS_PER_RUN = int(os.environ.get("MAX_RESEARCH_CALLS_PER_RUN", "30"))

# ---------------------------------------------------- Phase 4.2.3.14 research breadth
# Research breadth policy: how many search candidates and fetch URLs one
# web_research operation may logically inspect. DISTINCT from provider batch
# capacity (MAX_BATCH_FETCH_URLS / fetch valve max_urls) and from provider
# quota. The hard logical ceiling is 10 because web_research cannot return
# more than 10 search results today.
RESEARCH_DEFAULT_MAX_RESULTS = int(os.environ.get("RESEARCH_DEFAULT_MAX_RESULTS", "5"))
RESEARCH_DEFAULT_MAX_FETCHES = int(os.environ.get("RESEARCH_DEFAULT_MAX_FETCHES", "5"))
RESEARCH_MAX_RESULTS_CEILING = int(os.environ.get("RESEARCH_MAX_RESULTS_CEILING", "10"))
RESEARCH_MAX_FETCHES_CEILING = int(os.environ.get("RESEARCH_MAX_FETCHES_CEILING", "10"))

RESERVED_OUTPUT_TOKENS = int(os.environ.get("RESERVED_OUTPUT_TOKENS", "1024"))
CONTEXT_ESTIMATOR = os.environ.get("CONTEXT_ESTIMATOR", "tiktoken").strip().lower()
TRACE_SCHEMA_VERSION = 1


# ---------------------------------------------------------------- Phase 4: workspace platform
# Project files / knowledge
KNOWLEDGE_DEFAULT_CHUNK_SIZE = int(os.environ.get("KNOWLEDGE_DEFAULT_CHUNK_SIZE", "800"))
KNOWLEDGE_DEFAULT_CHUNK_OVERLAP = int(os.environ.get("KNOWLEDGE_DEFAULT_CHUNK_OVERLAP", "100"))
KNOWLEDGE_MAX_CHUNKS_PER_COLLECTION = int(os.environ.get("KNOWLEDGE_MAX_CHUNKS_PER_COLLECTION", "5000"))
KNOWLEDGE_MAX_CHUNK_CHARS = int(os.environ.get("KNOWLEDGE_MAX_CHUNK_CHARS", "4000"))
KNOWLEDGE_RETRIEVAL_TOP_K = int(os.environ.get("KNOWLEDGE_RETRIEVAL_TOP_K", "5"))
# Knowledge is opt-out at the policy layer; retrieval still requires selected
# collections, so existing conversations remain inert until they select one.
KNOWLEDGE_ENABLED = _env_bool("KNOWLEDGE_ENABLED", "true")
KNOWLEDGE_MAX_DOCUMENTS_PER_COLLECTION = int(os.environ.get("KNOWLEDGE_MAX_DOCUMENTS_PER_COLLECTION", "200"))
KNOWLEDGE_PARSER_VERSION = os.environ.get("KNOWLEDGE_PARSER_VERSION", "attachment-parser-v1")
KNOWLEDGE_CHUNKER_VERSION = os.environ.get("KNOWLEDGE_CHUNKER_VERSION", "fixed-paragraph-v1")

# Embeddings (provider-neutral; local hashing default so knowledge works offline)
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "local").strip().lower()  # local | openai_compatible
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "local-hashing-v1")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "256"))
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "16"))
EMBEDDING_TIMEOUT_SECONDS = int(os.environ.get("EMBEDDING_TIMEOUT_SECONDS", "30"))
EMBEDDING_VERSION = os.environ.get("EMBEDDING_VERSION", "embedding-v1")

# Custom HTTP tools
TOOL_HTTP_TIMEOUT_SECONDS = int(os.environ.get("TOOL_HTTP_TIMEOUT_SECONDS", "15"))
TOOL_HTTP_MAX_RESPONSE_BYTES = int(os.environ.get("TOOL_HTTP_MAX_RESPONSE_BYTES", "1048576"))
TOOL_HTTP_MAX_REDIRECTS = int(os.environ.get("TOOL_HTTP_MAX_REDIRECTS", "0"))
TOOL_HTTP_MAX_URL_LENGTH = int(os.environ.get("TOOL_HTTP_MAX_URL_LENGTH", "2048"))
OPENAPI_MAX_SPEC_BYTES = int(os.environ.get("OPENAPI_MAX_SPEC_BYTES", "2097152"))

# Code interpreter (Docker sandbox). Disabled => tool reports unavailable.
CODE_INTERPRETER_ENABLED = _env_bool("CODE_INTERPRETER_ENABLED", "true")
CODE_INTERPRETER_RUNTIME = os.environ.get("CODE_INTERPRETER_RUNTIME", "docker").strip().lower()
CODE_INTERPRETER_IMAGE = os.environ.get("CODE_INTERPRETER_IMAGE", "omnilab-code-interpreter:latest")
CODE_INTERPRETER_TIMEOUT_SECONDS = int(os.environ.get("CODE_INTERPRETER_TIMEOUT_SECONDS", "30"))
CODE_INTERPRETER_MEMORY_MB = int(os.environ.get("CODE_INTERPRETER_MEMORY_MB", "512"))
CODE_INTERPRETER_CPUS = float(os.environ.get("CODE_INTERPRETER_CPUS", "1.0"))
CODE_INTERPRETER_PIDS_LIMIT = int(os.environ.get("CODE_INTERPRETER_PIDS_LIMIT", "128"))
CODE_INTERPRETER_TMPFS_MB = int(os.environ.get("CODE_INTERPRETER_TMPFS_MB", "64"))
CODE_INTERPRETER_MAX_OUTPUT_BYTES = int(os.environ.get("CODE_INTERPRETER_MAX_OUTPUT_BYTES", "204800"))
CODE_INTERPRETER_MAX_FILES = int(os.environ.get("CODE_INTERPRETER_MAX_FILES", "10"))
CODE_INTERPRETER_MAX_FILE_BYTES = int(os.environ.get("CODE_INTERPRETER_MAX_FILE_BYTES", "5242880"))
CODE_INTERPRETER_MAX_EXECUTIONS_PER_RUN = int(os.environ.get("CODE_INTERPRETER_MAX_EXECUTIONS_PER_RUN", "5"))
CODE_INTERPRETER_MAX_CODE_CHARS = int(os.environ.get("CODE_INTERPRETER_MAX_CODE_CHARS", "100000"))

PROJECT_FILE_MAX_BYTES = int(os.environ.get("PROJECT_FILE_MAX_BYTES", str(MAX_ATTACHMENT_SIZE_MB * 1024 * 1024)))

# ---------------------------------------------------------------- Provider quota (TinyFish account limits) --
# TinyFish provider/account quota limits (Lab mode). These are the real
# provider limits by default and are the PRIMARY practical throttle. They are
# exposed as provider valves and may be LOWERED by the operator, never raised
# above the protected ceilings below.
TINYFISH_SEARCH_RPM_LIMIT = int(os.environ.get("TINYFISH_SEARCH_RPM_LIMIT", "30"))
TINYFISH_SEARCH_HOURLY_LIMIT = int(os.environ.get("TINYFISH_SEARCH_HOURLY_LIMIT", "500"))
TINYFISH_FETCH_URLS_PER_MINUTE_LIMIT = int(os.environ.get("TINYFISH_FETCH_URLS_PER_MINUTE_LIMIT", "150"))
TINYFISH_FETCH_URLS_PER_DAY_LIMIT = int(os.environ.get("TINYFISH_FETCH_URLS_PER_DAY_LIMIT", "1000"))
# Protected server hard ceilings, comfortably above the provider defaults.
TINYFISH_SEARCH_RPM_CEILING = int(os.environ.get("TINYFISH_SEARCH_RPM_CEILING", "120"))
TINYFISH_SEARCH_HOURLY_CEILING = int(os.environ.get("TINYFISH_SEARCH_HOURLY_CEILING", "5000"))
TINYFISH_FETCH_URLS_PER_MINUTE_CEILING = int(os.environ.get("TINYFISH_FETCH_URLS_PER_MINUTE_CEILING", "500"))
TINYFISH_FETCH_URLS_PER_DAY_CEILING = int(os.environ.get("TINYFISH_FETCH_URLS_PER_DAY_CEILING", "10000"))
