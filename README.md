<p align="center">
  <img src="logo-uniyra.png" alt="Uniyra AI Lab logo" width="160" />
</p>

<h1 align="center">Uniyra AI Lab</h1>

<p align="center">
  <strong>Self-hosted AI Research &amp; Development Workstation</strong>
</p>

<p align="center">
  Run ad-hoc multi-model Arenas, define reproducible benchmark Experiments,
  judge results with rubrics, and preserve every execution as an immutable Run
  with real telemetry and evidence-backed Findings.
</p>

<p align="center">
  <a href="#-license-and-trademarks"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <a href="https://github.com/unreferred/uniyra-ailab/releases"><img alt="Stable releases" src="https://img.shields.io/badge/releases-stable-2ea44f.svg"></a>
  <a href="https://github.com/unreferred/uniyra-ailab/pkgs/container/uniyra-ailab-frontend"><img alt="GHCR images" src="https://img.shields.io/badge/images-ghcr.io-2088FF.svg"></a>
  <a href="#quick-start"><img alt="Docker" src="https://img.shields.io/badge/docker-supported-2496ED.svg"></a>
</p>

---

## Quick Start (prebuilt, Docker only)

```bash
git clone https://github.com/unreferred/uniyra-ailab.git
cd uniyra-ailab
./install.sh
```

Then open **http://localhost:8080** and register the first account.

Optional custom port:

```bash
APP_PORT=3012 ./install.sh
```

Requirements: Docker Engine 24+ with the Compose v2 plugin.

What `install.sh` does:

- **Pulls the latest approved stable GHCR images** (`ghcr.io/unreferred/uniyra-ailab-{frontend,backend}`), resolves the concrete release version from the images' OCI labels, verifies the frontend/backend pair matches, and **pins** that version in `.env` and `.ailab_version`. It never builds development code from `main`.
- Creates `.env` from `.env.example` if missing (never overwrites your values) and generates `JWT_SECRET` / `ENCRYPTION_KEY` only if empty.
- If an authenticated GHCR pull fails with stale/expired credentials from a previous `docker login ghcr.io`, it **automatically falls back to an anonymous pull** of the two public Uniyra images, using a temporary credential-free Docker config. Your real Docker credentials are never read, modified, or deleted.
- Never deletes volumes and never silently picks a different version.

Updates, explicit version pinning, rollback, and backups: [docs/PREBUILT_INSTALL.md](docs/PREBUILT_INSTALL.md).

> **This is installation, not development.** The commands above run published stable images only. To develop or patch from source, see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (local dev) or [docs/DOCKER_INSTALL.md](docs/DOCKER_INSTALL.md) (build the stack locally with Docker).

## Core Features

- **Arena** — send the same task to 2–4 models at once, optionally **blind** (model identity is hidden server-side until an explicit, audited reveal).
- **Experiment** — reproducible protocols: ordered configurations, optional frozen dataset version, repetition counts, all pinned by content hashes.
- **Benchmark** — a dataset × configuration × repetition execution matrix with plan previews, cost estimates (only when pricing is known), confirmation thresholds, bounded concurrency, resumable/idempotent runs, and cooperative cancellation.
- **Immutable Runs** — exactly one append-only Run per execution attempt (success or structured failure), with pricing and configuration snapshots captured before execution. Unknown tokens, rates, or costs stay unavailable and are never coerced to zero.
- **Evaluation & Findings** — judgments (deterministic, human, or LLM judge) stored separately from Runs; human conclusions linked to supporting evidence.
- **Workspace Platform (V4)** — Project Files, Knowledge collections (local-first retrieval), versioned custom tools (HTTP/OpenAPI/code-interpreter) with an encrypted credential vault, and a Docker-sandboxed code interpreter.
- **Web research** — a composite `web_research` tool with batch fetch, provider quota valves, and an agent activity timeline.
- **BYOK providers** — bring your own OpenAI-compatible endpoints; keys are encrypted server-side and SSRF-protected.

Data lineage: `Project → Research → Experiment → Run → Evaluation → Finding → Artifact`.

## Research Workflow

1. **Explore** in an Arena: compare candidate models on the same task, blind or open.
2. **Formalize** the winning approach as an Experiment: ordered configurations, frozen dataset version, repetitions.
3. **Execute** as a Benchmark: plan preview → confirmation → bounded, resumable, idempotent runs.
4. **Judge** with deterministic checks, human votes, or LLM-judge evaluations.
5. **Conclude** with Findings backed by linked evidence, preserved as immutable history.

Experiment lifecycle: `DRAFT → READY → RUNNING → COMPLETED/FAILED → ARCHIVED`. Configuration and protocol edits are allowed only in `DRAFT`; `READY` freezes snapshots and hashes.

## Deployment Architecture

| Service    | Role                                             | Exposure |
|------------|--------------------------------------------------|----------|
| `frontend` | nginx — the **only** public entrypoint           | `http://localhost:APP_PORT` |
| `backend`  | FastAPI (uvicorn) behind the nginx `/api/` proxy | internal network only |
| `mongo`    | MongoDB 7.0 data store                           | internal network only, no published port |

The browser calls a single origin; nginx proxies `/api/...` to the backend, preserving cookies, CSRF, CORS, and streamed responses. Data lives in named volumes (`mongo_data`, `attachments`). The same architecture is used by the prebuilt GHCR install and the local source build — see [docs/PREBUILT_INSTALL.md](docs/PREBUILT_INSTALL.md) and [docs/DOCKER_INSTALL.md](docs/DOCKER_INSTALL.md).

Internally, the backend is strictly layered (`routers → services → providers/repository → database`), and every model call — Workspace, Arena, Judge, Council, and the benchmark runner — flows through a single execution funnel, so telemetry, pricing snapshots, and immutable Runs are recorded identically for success and failure. Details: [docs/V4_WORKSPACE_PLATFORM.md](docs/V4_WORKSPACE_PLATFORM.md).

## Security & Limitations

Implemented security behavior:

- **Blind Arena is genuinely blind server-side**; reveals are explicit, audited, and duplicate winner votes are rejected.
- **Structured execution errors** with stable categories; failures are persisted as failure Runs, never dropped.
- **Provider SSRF protection**: http/https only; embedded credentials, private/loopback/link-local/metadata IPs and `localhost` are rejected by default; cross-host redirects are disabled; credentials are never forwarded. Local dev override: `PROVIDER_ALLOW_LOCAL_URLS=true`.
- **Auth/session**: no default credentials, no password reset on startup; opt-in admin seeding only; refresh-token `jti` rotation/revocation; CSRF double-submit tokens; explicit CORS origins; rate-limited auth endpoints.
- **Real project boundary**: `owner_id` + `project_id` are validated on links, attachments, reads, updates, and deletes.

Known limitations (documented honestly, not hidden):

- **MongoDB has no authentication.** It is unreachable from the host/LAN, but any container attached to the Compose `internal` network has full read/write access to all data. Never add a `ports:` mapping to `mongo`.
- **Code interpreter is disabled by default in Docker.** Enabling it requires mounting the host Docker socket, which is root-equivalent on the host — single-user, trusted hosts only.
- **Plain HTTP entrypoint** by default; terminate TLS in a reverse proxy and set `COOKIE_SECURE=true` and `CORS_ORIGINS`.
- Full lists: [docs/DOCKER_INSTALL.md](docs/DOCKER_INSTALL.md) and [docs/PREBUILT_INSTALL.md](docs/PREBUILT_INSTALL.md), plus the audit in [docs/SECURITY_PREPUBLIC_AUDIT.md](docs/SECURITY_PREPUBLIC_AUDIT.md).

**Explicitly not built (planned/deferred):** Agent Lab, Routing, Certification, MCP/browser/SSH/general-Docker tools, Orchestrator, AI Scientist, distributed workers, and the full Prompt Lab (immutable Prompt/PromptVersion identity exists; the Prompt Lab itself is deferred). Docker exists only as the code-interpreter sandbox boundary, not as a user-facing tool family.

## Documentation

| Document | Contents |
|---|---|
| [docs/PREBUILT_INSTALL.md](docs/PREBUILT_INSTALL.md) | Prebuilt GHCR install, version pinning, updates, rollback, publishing model |
| [docs/DOCKER_INSTALL.md](docs/DOCKER_INSTALL.md) | Docker-from-source install, backup/restore, proxy/TLS, limitations |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local development, environment variables, troubleshooting |
| [docs/V4_WORKSPACE_PLATFORM.md](docs/V4_WORKSPACE_PLATFORM.md) | Workspace Platform: files, knowledge, tools, credential vault, sandbox |
| [docs/V4_2_3_8_WEB_RESEARCH.md](docs/V4_2_3_8_WEB_RESEARCH.md) | Composite `web_research` tool, batch fetch, agent activity timeline |
| [docs/V4_2_3_14_RESEARCH_BREADTH.md](docs/V4_2_3_14_RESEARCH_BREADTH.md) | Research breadth defaults, ceilings, and project overrides |
| [docs/SECURITY_PREPUBLIC_AUDIT.md](docs/SECURITY_PREPUBLIC_AUDIT.md) | Pre-public security audit notes |
| [TRADEMARKS.md](TRADEMARKS.md) | Uniyra trademark policy |

## License & Trademarks

Uniyra AI Lab is published by **Unreferred** and licensed under the
[Apache License 2.0](LICENSE).

- Forks, modifications, and commercial use are permitted under Apache-2.0, without publisher approval, subject to the license conditions (keep copyright and license notices, state significant changes, etc.).
- **Brand names and logos are not covered by that grant.** The "Uniyra" name and the official logo are trademarks of Unreferred and are not automatically licensed for unrelated branding or endorsement. See [TRADEMARKS.md](TRADEMARKS.md).
- Unofficial forks are not affiliated with or endorsed by Unreferred and may not use the official Uniyra branding in a way that implies otherwise.
- Applicable third-party notices: [NOTICE](NOTICE).
