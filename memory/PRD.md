# OmniLab — AI R&D Laboratory (PRD)

## Original problem statement
Build a production-quality, desktop-first AI R&D Laboratory V1 — the foundation of a larger
AI Research & Development OS. It helps researchers compare models, run multi-model + blind
Arenas, judge results with rubrics, and preserve reproducible Experiments/Runs/Findings.
Data lineage: Project → Research → Experiment → Run → Response → Evaluation → Finding → Artifact.
No placeholders, no dead nav, real telemetry only. Portable for later export to VS Code / Tauri.

## User decisions (locked)
- LLM execution: BOTH Emergent Universal Key (seeded, zero-config) AND functional BYOK
  OpenAI-compatible provider registry. Universal is one provider among many; feature code
  never depends on it. Keys server-side only.
- Auth: JWT email/password, httpOnly cookies, backend-enforced ownership; OAuth-ready.
- Persistence: MongoDB (only managed DB here) behind a strict Repository/service abstraction
  so it can be swapped to PostgreSQL later. Explicit UUID ids, FK-style refs, indexes.
- Frontend: strict-intent TypeScript on CRA (reusing shadcn .jsx primitives via allowJs).
- Theme: light default + dark toggle.
- Scope: Phases 1–4 first (this milestone).

## Architecture
routers → services package → repository → database (only module touching the Mongo driver).
The services package covers execution/pricing/arena/judge/council/experiments/datasets/
benchmark/evaluation, and there is ONE canonical execution funnel (`execute_model` /
`stream_execution`) used by Workspace, Arena, Judge, Council and the benchmark runner.
Runs & Evaluations are append-only. Blind mapping preserved server-side (reveal is an explicit,
audited call). Provider/Model/Role/Judge remain separate concepts. Pricing, configuration,
dataset-version content and protocol/configuration hashes are snapshotted for reproducibility;
unknown token/cost values are never coerced to zero.

## Personas
- AI researcher / evaluation engineer comparing models and preserving evidence.

## Implemented (Phases 1–4 + Phase 1 hardening + Phase 2)
Phase 1 foundations: auth (register/login/logout/me/refresh, bcrypt, cookies, brute-force
lockout, opt-in admin seed), app shell, projects CRUD + cascade delete, provider registry
(emergent_universal + openai_compatible) with encrypted keys + connection test, model registry
with enable/disable + capabilities/tags/cost, rubrics, Workspace (conversations, params, markdown,
telemetry, save-as-artifact), Arena (2–4 parallel models, partial-failure tolerance), Blind Arena,
AI Judge, human evaluation, experiments/runs persistence + re-run/clone, artifacts, overview.

Phase 1 hardening:
- Layering split into routers → services package → repository → database with one canonical
  execution funnel (`execute_model` / `stream_execution`).
- Immutable snapshots: pricing captured before execution; model/provider/params/prompt frozen at
  READY; dataset version `content_hash`; protocol/configuration hashes. Unknown token counts, rates
  and costs stay unavailable and are never falsified as zero.
- Structured execution errors (AUTH/RATE_LIMIT/TIMEOUT/PROVIDER/MODEL_UNAVAILABLE/VALIDATION/
  NETWORK/MALFORMED_RESPONSE/INTERNAL/CANCELLED) persisted as failure Runs.
- Blind Arena genuinely blind server-side: pre-reveal responses omit model/provider identity and
  `blind_map`; reveal requires an explicit backend call and is audited in `reveal_events`;
  duplicate human winner votes are rejected unless intentionally replaced.
- Provider SSRF protection (scheme/credentials/private/loopback/link-local/metadata checks,
  cross-host redirects blocked, credentials never forwarded) with a local-development override
  that never permits cloud metadata IPs.
- Auth/session: no default credentials, no startup password reset; opt-in admin seed via
  ENABLE_ADMIN_SEED + ADMIN_EMAIL/ADMIN_PASSWORD; refresh-token jti rotation/revocation in
  `auth_sessions`; CSRF double-submit cookie; explicit CORS origins; rate limiting; TTL cleanup.
- Model-enabled enforcement across Workspace/Arena/Judge/Council/benchmark; project is a real
  boundary (owner_id + project_id validated for links/attachments/reads/updates/deletes).

Phase 2 — experiment protocol, datasets, benchmarks, evaluation:
- Experiment lifecycle DRAFT → READY → RUNNING → COMPLETED/FAILED → ARCHIVED with ordered
  configurations that snapshot model/provider/pricing/prompt identity and emit protocol +
  configuration hashes. Legacy Arena experiments remain a legacy experiment type.
- Dataset versions: Dataset → Version → Cases; monotonic numbering, draft-only case CRUD, one-way
  freeze with a canonical `content_hash`.
- Benchmark planner + resumable runner: deterministic `plan_hash`, plan preview (cases × configs ×
  repetitions, total planned calls, cost range only when pricing is known, else unavailable),
  confirmation threshold, hard cap, bounded concurrency, per-item retries/idempotency, cancellation.
- Evaluation service: deterministic (exact_match/contains/regex/token_overlap), human and LLM-judge
  evaluators stored separately from Runs; aggregation, case drill-down, baseline/candidate
  regression comparison, and findings derived from results with validated evidence links.
- Prompt stub: immutable `Prompt` / auto-numbered `PromptVersion` identity usable from experiment
  configurations. Full Prompt Lab is deferred.
- Verified: baseline backend tests plus pricing-snapshot, provider-catalog, benchmark and
  evaluation suites; V2 smoke + frontend flows.

## Backlog (next)
- Full Prompt Lab UI (variables, rendering, side-by-side prompt comparison).
- Deep-link artifacts to a specific evidence item instead of the Artifacts list.
- Export experiments/councils to Markdown/PDF; paginate/aggregate usage_events server-side.
- Type the remaining shadcn .jsx primitives to harden the TS build.

## Deferred (explicitly NOT built)
Agent Lab, Tool Lab, Routing, Certification, MCP/browser/SSH/Docker tools, Red Team, Orchestrator,
AI Scientist, distributed workers.

## Added 2026-06 (Phases 5–7)
- AI Council: multi-role, 4 structured rounds (Independent → Cross-Critique → Revision →
  Moderator Synthesis) with presets (Executive/Technical/Product/Red Team/Custom); each
  participant response is an immutable Run; rounds stored & visualised separately.
- Research sessions: notes, sources, status, linked experiments/artifacts.
- Findings: statement, confidence, status (provisional/validated/superseded) with evidence
  links to experiments/research/artifacts and back-navigation to supporting evidence.
- Observability: real telemetry charts (latency over time, requests+failures & avg latency
  by model, cost per run) with a model filter. Cost shows unavailable for Universal models.
- Verified: iteration_2 — 4/4 backend smoke + all V2 frontend flows pass.

## Known notes
- Cost shows "n/a" for Universal models by design (pricing unavailable — never fabricated).
- Intermittent upstream "unsupported param" errors from some models are surfaced (not hidden) and
  don't discard sibling Arena results.
