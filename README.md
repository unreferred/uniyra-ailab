# Uniyra AI Lab

A desktop-first AI Research & Development workstation: run ad-hoc multi-model
Arenas, define reproducible benchmark Experiments, judge results with rubrics,
and preserve every execution as an immutable Run with real telemetry and
evidence-backed Findings.

## Stack
- Frontend: React 19 + TypeScript (Create React App / CRACO), Tailwind + shadcn/ui
- Backend: FastAPI (layered: routers -> services package -> repository -> MongoDB)
- DB: MongoDB behind a persistence-agnostic Repository abstraction (swappable to Postgres later)
- LLM execution: provider abstraction with the adapter
  - `openai_compatible` - BYOK OpenAI-compatible endpoints (keys encrypted server-side)

## Core concepts
- **Arena** - ad-hoc exploration: the same task sent to 2-4 models at once, optionally blind.
- **Experiment** - a reproducible protocol: ordered configurations plus an optional frozen
  dataset version and a repetition count, pinned by hashes.
- **Benchmark** - a dataset x configuration x repetition execution matrix.
- **Run** - exactly one execution attempt (success or structured failure); append-only.
- **Evaluation** - judgment of a Run/result, stored separately from the Run
  (deterministic, human, or LLM judge).
- **Finding** - a human/research conclusion supported by linked evidence.

Data lineage: Project -> Research -> Experiment -> Run -> Evaluation -> Finding -> Artifact.

## Architecture (portability)
```
API routers (app/routers/*)              # HTTP + auth + ownership + project boundary
  -> services package (app/services/*)   # execution, pricing, arena, judge, council,
                                         # experiments, datasets, benchmark, evaluation
    -> providers (app/providers.py)      # ProviderAdapter interface + adapters
    -> repository (app/repository.py)    # collection-agnostic CRUD contract
      -> database (app/database.py)      # the ONLY module importing the Mongo driver
```

There is ONE canonical execution funnel for every model call: `execute_model`
and its streaming counterpart `stream_execution` in `app/services/execution.py`.
Workspace, Arena, Judge, Council and the benchmark runner all go through it, so
telemetry, pricing snapshots and exactly one immutable Run + usage_event are
recorded identically for success and failure.

- Stable UUID ids, explicit FK-style reference fields, explicit indexes.
- Runs & Evaluations are append-only (re-run/clone never overwrite history).

## Immutable snapshots & truthfulness
- **Pricing snapshots** are captured before execution; later model edits never reprice a Run.
- **Configuration snapshots** freeze model/provider identity, params and prompt version at READY.
- **Dataset version `content_hash`** pins the ordered, frozen cases used by a benchmark.
- **Protocol / configuration hashes** pin the full reproducible experiment definition.
- Unknown token counts, unknown rates and unknown costs stay unavailable and are
  never coerced to zero.

## Security behavior
- **Blind Arena is genuinely blind server-side.** Pre-reveal API responses omit
  model/provider identity and `blind_map`; reveal requires an explicit backend
  call (`POST /api/experiments/{id}/reveal`) and is audited in `reveal_events`.
  Duplicate human winner votes are rejected (409) unless intentionally replaced.
- **Structured execution errors** use stable categories: AUTH, RATE_LIMIT, TIMEOUT,
  PROVIDER, MODEL_UNAVAILABLE, VALIDATION, NETWORK, MALFORMED_RESPONSE, INTERNAL,
  CANCELLED. Failures are persisted as failure Runs; attempted calls are never dropped.
- **Provider SSRF protection:** provider URLs must be http/https; embedded credentials,
  private/loopback/link-local/metadata IPs and `localhost` are rejected
  by default; query strings are allowed and preserved while SSRF validates
  host/IP/protocol; cross-host redirects are disabled; credentials are never forwarded.
  **Local development:** set `PROVIDER_ALLOW_LOCAL_URLS=true` (and optionally
  `PROVIDER_URL_ALLOWLIST=host:port`) for Ollama/LiteLLM/other local gateways.
  Production default stays strict; the override never permits cloud metadata IPs.
- **Auth/session:** no default credentials and no password reset on startup. Admin
  creation is an explicit opt-in via `ENABLE_ADMIN_SEED=true` together with
  `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Refresh-token `jti` rotation/revocation is tracked
  server-side in `auth_sessions` (reuse revokes the token family). Unsafe cookie-auth
  requests require a CSRF double-submit token (`X-CSRF-Token`). CORS uses explicit
  origins only. Auth endpoints are rate limited (Mongo-backed, TTL-cleaned).
- **Model enabled enforcement** across Workspace/Arena/Judge/Council/benchmark:
  a disabled model or provider returns a clear, safe validation error.
- **Project is a real research boundary:** `owner_id` + `project_id` are validated for
  links, attachments, reads, updates and deletes.

## Experiment lifecycle
`DRAFT -> READY -> RUNNING -> COMPLETED/FAILED -> ARCHIVED`. Configurations and
protocol edits are only allowed while DRAFT; READY freezes snapshots and hashes.
Legacy Arena experiments remain a legacy experiment type.

## Benchmark safety
- Plan preview shows cases x configs x repetitions, total planned calls, and an
  estimated cost range **only when pricing is known** (otherwise unavailable).
- Confirmation threshold for large plans, plus a hard cap on planned calls.
- Bounded concurrency, resumable/idempotent execution (per case/config/repetition,
  with `attempt_index`), and cooperative cancellation.

## Dataset versions
Dataset -> Dataset Version -> Dataset Cases. Freezing a version makes it immutable
and records a `content_hash`. Case CRUD is allowed only on draft versions.

## Prompt stub
Prompt / PromptVersion provide immutable, auto-numbered prompt identity that
experiment configurations can reference. The full Prompt Lab is deferred.

## Workspace Platform (V4)
Project Files, Knowledge collections (local-first retrieval), versioned custom
tools (HTTP/OpenAPI/code-interpreter) with an encrypted credential vault, and a
Docker-sandboxed code interpreter. See [docs/V4_WORKSPACE_PLATFORM.md](docs/V4_WORKSPACE_PLATFORM.md)
for the Attachment vs Project File vs Knowledge Document vs Artifact split, the
Tool/ToolVersion/ToolCredential/ToolCall model, config inheritance
(Project Default -> Conversation Override -> Run Snapshot), the embedding /
vector-store replacement boundary, sandbox guarantees, the custom-tool and
OpenAPI security models, Arena fairness, asset references, and
deletion/retention semantics.

For the composite `web_research` tool, batch fetch, and the agent activity timeline, see [docs/V4_2_3_8_WEB_RESEARCH.md](docs/V4_2_3_8_WEB_RESEARCH.md). Research breadth defaults, ceilings, project overrides, and the separation of logical breadth from provider batch capacity are documented in [docs/V4_2_3_14_RESEARCH_BREADTH.md](docs/V4_2_3_14_RESEARCH_BREADTH.md).

## Deferred scope (explicitly NOT built)
Agent Lab, Routing, Certification, MCP/browser/SSH/general-Docker tools,
Orchestrator, AI Scientist, and distributed workers. The V4 Tool Lab (custom
HTTP/OpenAPI/code-interpreter tools + credential vault + sandbox execution) is
BUILT — see the Workspace Platform section above; Docker exists only as the
code-interpreter sandbox boundary, not as a user-facing tool family. The full
Prompt Lab remains deferred (Prompt / PromptVersion identity only).

## Local Development

### Prerequisites
- Node 18+ with Corepack. This repo is Yarn 1 based; use Corepack Yarn, not npm
  or a globally installed Yarn.
- Python 3.11+ with `pip` (backend is FastAPI + uvicorn).
- MongoDB is optional for setup, but required when exercising the API (see MongoDB below).

### 1. Install dependencies
```bash
corepack enable
corepack prepare yarn@1.22.22 --activate
corepack yarn --version       # must report 1.22.x
corepack yarn install
corepack yarn --cwd frontend install
# Or use the equivalent combined command: corepack yarn install:all
# NOTE: backend (Python) deps are installed separately — see step 3.
```

Root `package.json` scripts (`ailab`, private) use only local project binaries:
| Script | What it runs |
|---|---|
| `corepack yarn dev` | frontend (CRACO on :3002) + backend (the venv uvicorn on :8001) together |
| `corepack yarn dev:frontend` | local `frontend/node_modules/.bin/craco start` on :3002 |
| `corepack yarn dev:backend` | `backend/.venv/bin/uvicorn server:app --app-dir backend --host 0.0.0.0 --port 8001 --reload --reload-dir backend` |
| `corepack yarn dev:mongo` | starts an existing Docker container named `ai-lab-mongo` without creating, deleting, or resetting data |
| `corepack yarn install:all` | installs root + `frontend/` Yarn dependencies |

### 2. MongoDB
No Docker files are committed to this repo. MongoDB is optional until the API is used.
If the existing Docker container is present, start it non-destructively:
```bash
corepack yarn dev:mongo
```
Alternatively, run a local `mongod` or start MongoDB through your system service.
The backend expects `mongodb://localhost:27017` by default.
Verify with `mongosh --eval "db.runCommand({ ping: 1 })"`
(or `mongo --eval ...` with the legacy shell). The backend connects via
`MONGO_URL` (default `mongodb://localhost:27017`) and `DB_NAME`.

### 3. Backend setup & start
```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env   # then fill in values, see table below
corepack yarn dev:backend
```
Health check: `curl http://localhost:8001/api/` →
`{"service":"uniyra-ai-lab","status":"ok"}`.

### 4. Frontend setup & start
```bash
corepack yarn --cwd frontend install
corepack yarn dev:frontend   # local craco start on :3002
```
The frontend API base URL is configurable via `frontend/.env`
(see `frontend/.env.example`):
```
REACT_APP_BACKEND_URL=http://localhost:8001
PORT=3002
```
The app calls `${REACT_APP_BACKEND_URL}/api`. CRA embeds env vars at
start/build time, so after changing `frontend/.env`, restart the dev server.

### Environment variables
Backend (`backend/.env`, see `backend/.env.example`):
| Var | Required | Default / example | Notes |
|---|---|---|---|
| `MONGO_URL` | yes | `mongodb://localhost:27017` | Mongo connection string |
| `DB_NAME` | yes | `omnilab` | Mongo database name |
| `JWT_SECRET` | yes | - | JWT signing secret |
| `ENCRYPTION_KEY` | yes | - | Fernet key for provider API-key encryption at rest (see Troubleshooting for generation) |
| `FRONTEND_URL` | no | `http://localhost:3002` | allowed CORS origin (see `backend/server.py`) |
| `OMNILAB_ENV` | no | `development` | `development` \| `production`; production honours only `CORS_ORIGINS` / `FRONTEND_URL` |
| `CORS_ORIGINS` | no | - | extra allowed browser origins, comma-separated (never `*` with credentials) |
| `ENABLE_ADMIN_SEED` | no | `false` | opt-in admin creation on a fresh DB; requires both `ADMIN_EMAIL` and `ADMIN_PASSWORD` |
| `ADMIN_EMAIL` | only with seed | - | admin email; no default credential is baked into the source |
| `ADMIN_PASSWORD` | only with seed | - | admin password; no default credential is baked into the source |
| `COOKIE_SECURE` | no | `true` | `Secure` cookie flag (`false` only for plain-http local dev) |
| `COOKIE_SAMESITE` | no | `lax` | `lax` \| `strict` \| `none`; `none` forces `Secure` |
| `COOKIE_DOMAIN` | no | - | optional cookie domain, e.g. `.example.com` |
| `PROVIDER_ALLOW_LOCAL_URLS` | no | `false` | permit loopback/private provider URLs for local gateways; cloud metadata IPs stay blocked |
| `PROVIDER_URL_ALLOWLIST` | no | - | comma-separated `host[:port]` entries that explicitly bypass SSRF blocks |
| `PROVIDER_URL_ALLOW_PRIVATE` | no | `false` | permit RFC1918/unique-local targets without opening loopback |
| `BENCHMARK_CONCURRENCY` | no | `3` | benchmark worker concurrency (clamped to `BENCHMARK_MAX_CONCURRENCY`) |
| `BENCHMARK_MAX_CONCURRENCY` | no | `10` | upper clamp for benchmark concurrency |
| `BENCHMARK_MAX_PLANNED_CALLS` | no | `2000` | hard cap on planned benchmark calls |
| `BENCHMARK_CONFIRM_THRESHOLD` | no | `50` | plans above this many calls require explicit confirmation |
| `BENCHMARK_MAX_RETRIES` | no | `1` | max retry attempts per benchmark item (retryable errors only) |
| `MAX_ATTACHMENT_SIZE_MB` | no | `20` | per-file attachment limit (validated again for project files) |
| `MAX_ATTACHMENTS_PER_MESSAGE` | no | `10` | max attachments scoped to one message/conversation |
| `MAX_TOTAL_ATTACHMENT_SIZE_MB` | no | `50` | total attachment bytes scoped to one message/conversation |
| `ATTACHMENT_STORAGE_DIR` | no | `backend/data/attachments` | filesystem blob root; sandbox staging/outputs are siblings |
| `MAX_TOOL_ROUNDS` | no | `5` | max agentic tool-call rounds per model Run |
| `RESERVED_OUTPUT_TOKENS` | no | `1024` | tokens reserved for the reply when checking the context window |
| `CONTEXT_ESTIMATOR` | no | `tiktoken` | `tiktoken` (cl100k_base) with heuristic chars/4 fallback |
| `PROJECT_FILE_MAX_BYTES` | no | `MAX_ATTACHMENT_SIZE_MB * 1MiB` | max bytes for a project-file upload |
| `KNOWLEDGE_DEFAULT_CHUNK_SIZE` | no | `800` | default chars per chunk for new collections |
| `KNOWLEDGE_DEFAULT_CHUNK_OVERLAP` | no | `100` | default overlap chars (must be < chunk size) |
| `KNOWLEDGE_MAX_CHUNK_CHARS` | no | `4000` | per-chunk cap applied after splitting |
| `KNOWLEDGE_MAX_CHUNKS_PER_COLLECTION` | no | `5000` | chunk ceiling per collection (index + search scan cap) |
| `KNOWLEDGE_MAX_DOCUMENTS_PER_COLLECTION` | no | `200` | document ceiling per collection |
| `KNOWLEDGE_RETRIEVAL_TOP_K` | no | `5` | default top_k for retrieval when the turn omits it |
| `EMBEDDING_PROVIDER` | no | `local` | `local` (offline hashing) \| `openai_compatible` (BYOK) |
| `EMBEDDING_MODEL` | no | `local-hashing-v1` | default embedding model snapshot for new collections |
| `EMBEDDING_DIM` | no | `256` | local hashing dimension |
| `EMBEDDING_BATCH_SIZE` | no | `16` | texts embedded per batch |
| `EMBEDDING_TIMEOUT_SECONDS` | no | `30` | OpenAI-compatible embedding request timeout |
| `TOOL_HTTP_TIMEOUT_SECONDS` | no | `15` | default custom-tool HTTP timeout (per-tool 1-60 s) |
| `TOOL_HTTP_MAX_RESPONSE_BYTES` | no | `1048576` | default max upstream response body (per-tool `max_bytes`) |
| `TOOL_HTTP_MAX_REDIRECTS` | no | `0` | reserved; redirects are unconditionally disabled today |
| `TOOL_HTTP_MAX_URL_LENGTH` | no | `2048` | max `url_template` length |
| `OPENAPI_MAX_SPEC_BYTES` | no | `2097152` | max OpenAPI spec size (parse + fetch) |
| `PROVIDER_CONNECTION_TIMEOUT_SECONDS` | no | `120` | provider-level connection/streaming timeout ceiling (adapter-owned) |
| `FETCH_REQUEST_TIMEOUT_MAX_SECONDS` | no | `60` | server ceiling for a single URL fetch; provider valves may lower, never raise |
| `SEARCH_REQUEST_TIMEOUT_MAX_SECONDS` | no | `60` | server ceiling for a single search request; provider valves may lower, never raise |
| `AGENT_TOOL_BUDGET_SECONDS` | no | `180` | whole agentic tool-run wall-clock budget |
| `MODEL_CONNECTION_TIMEOUT_SECONDS` | no | `60` | model-call watchdog: provider did not START a usable response (no event arrived) |
| `MODEL_IDLE_TIMEOUT_SECONDS` | no | `120` | model-call watchdog: stream started but no meaningful event arrived within the gap |
| `MODEL_CALL_MAX_DURATION_SECONDS` | no | `600` | model-call watchdog: emergency whole-call wall-clock ceiling |
| `MAX_BATCH_FETCH_URLS` | no | `5` | protected **provider execution batch** ceiling: the maximum URLs one fetch-provider request may carry (also the fetch `max_urls` valve ceiling). This is provider batching, **not** research breadth — see [docs/V4_2_3_14_RESEARCH_BREADTH.md](docs/V4_2_3_14_RESEARCH_BREADTH.md). |
| `FETCH_BATCH_CONCURRENCY` | no | `2` | max in-flight per-URL requests in a Standard batch fetch |
| `MAX_RESEARCH_CALLS_PER_RUN` | no | `30` | emergency composite `web_research` ceiling (TinyFish provider quota is the practical throttle) |
| `CODE_INTERPRETER_ENABLED` | no | `true` | `false` makes code execution report unavailable |
| `CODE_INTERPRETER_RUNTIME` | no | `docker` | sandbox runtime label |
| `CODE_INTERPRETER_IMAGE` | no | `omnilab-code-interpreter:latest` | sandbox image (see `backend/docker/code-interpreter`) |
| `CODE_INTERPRETER_TIMEOUT_SECONDS` | no | `30` | wall-clock timeout per execution |
| `CODE_INTERPRETER_MEMORY_MB` | no | `512` | container RAM cap (swap pinned equal) |
| `CODE_INTERPRETER_CPUS` | no | `1.0` | container CPU cap |
| `CODE_INTERPRETER_PIDS_LIMIT` | no | `128` | container PID cap |
| `CODE_INTERPRETER_TMPFS_MB` | no | `64` | writable `/workspace` tmpfs size |
| `CODE_INTERPRETER_MAX_OUTPUT_BYTES` | no | `204800` | stdout/stderr capture cap each |
| `CODE_INTERPRETER_MAX_FILES` | no | `10` | max output files collected per execution |
| `CODE_INTERPRETER_MAX_FILE_BYTES` | no | `5242880` | max bytes per collected output file |
| `CODE_INTERPRETER_MAX_EXECUTIONS_PER_RUN` | no | `5` | max code executions per model Run |
| `CODE_INTERPRETER_MAX_CODE_CHARS` | no | `100000` | max submitted source chars |
| `PORT` | no | `8001` | backend port (used by the `yarn dev` scripts) |

Frontend (`frontend/.env`):
| Var | Required | Default | Notes |
|---|---|---|---|
| `REACT_APP_BACKEND_URL` | yes | `http://localhost:8001` | backend base URL; frontend appends `/api` |
| `PORT` | no | `3002` | CRA dev-server port |

### Expected ports
| Service | Port | URL |
|---|---|---|
| Frontend (craco) | 3002 | http://localhost:3002 |
| Backend (uvicorn) | 8001 | http://localhost:8001/api/ |
| MongoDB | 27017 | mongodb://localhost:27017 |

### Troubleshooting
- `KeyError: 'MONGO_URL'` (or `DB_NAME` / `JWT_SECRET` / `ENCRYPTION_KEY`) on
  backend start → `backend/app/config.py` reads required vars with
  `os.environ[...]` at import time. Create `backend/.env` from
  `backend/.env.example` and restart uvicorn.
- `Invalid Fernet key` / cryptography errors → generate a fresh key and set it
  as `ENCRYPTION_KEY`:
  `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `date-fns` / `react-day-picker` build errors → frontend pins
  `date-fns@3.6.0` (v4 breaks `react-day-picker@8`). Fix:
  `corepack yarn --cwd frontend install` (or verify the pin is intact) and reinstall.
- Yarn vs npm → use Corepack Yarn 1 (`yarn@1.22.x`, see the `packageManager` field
  in `frontend/package.json`). Mixing in npm creates `package-lock.json` churn
  and can break the `--cwd` script assumptions.
- CORS errors in the browser (`blocked by CORS policy`) → the backend allows
  `FRONTEND_URL` plus `http://localhost:3002` (`backend/server.py`). Set
  `FRONTEND_URL=http://localhost:3002` in `backend/.env`; if you serve the
  frontend from another origin, add it there.
- Backend `pip install` fails on `litellm` →
  `litellm` is pinned from PyPI (`backend/requirements.txt`); no Emergent-only
  packages are required.
- `ERR_CONNECTION_REFUSED` on `:8001/api/...` in the browser while the frontend
  still loads → the backend is not running. `yarn dev` now self-heals: before
  starting it reaps stale ai-lab dev processes left by a previous run (a hung
  `uvicorn --reload` that still held :8001, an orphaned `craco`, etc.), refuses
  to start if a port is owned by an unrelated service, and kills the whole
  process tree on Ctrl+C so orphans are not created in the first place.
  Backend reload watches only `backend/` (`--reload-dir backend`) so frontend
  writes no longer restart it.
- Port already in use → override the script ports with `AILAB_FRONTEND_PORT`
  and/or `AILAB_BACKEND_PORT` (these are the orchestrator's source of truth and
  take precedence over an ambient `PORT` or `frontend/.env`). Keep
  `REACT_APP_BACKEND_URL` in `frontend/.env` in sync with the backend port.

### Provider notes
- The `openai_compatible` adapter (BYOK OpenAI-compatible endpoints) works
  fully locally; keys are encrypted server-side with `ENCRYPTION_KEY`.
- The legacy `emergent_universal` provider type is no longer supported: the
  adapter, seeding, and the `emergentintegrations` dependency were removed.
  Existing MongoDB provider records with that type are left untouched but will
  fail execution; re-create them as `openai_compatible` (BYOK) providers.

## License

Uniyra AI Lab is published by **Unreferred** and licensed under the
[Apache License 2.0](LICENSE).

- Forks, modifications, and commercial use are permitted under Apache-2.0,
  without publisher approval, subject to the license conditions (keep
  copyright and license notices, state significant changes, etc.).
- Trademark rights are separate: the "Uniyra" name and the official logo are
  trademarks of Unreferred and are not granted by the license. See
  [TRADEMARKS.md](TRADEMARKS.md).
- Unofficial forks are not affiliated with or endorsed by Unreferred. They may
  not use the official Uniyra branding in a way that implies otherwise.
- Applicable third-party notices are listed in [NOTICE](NOTICE).
