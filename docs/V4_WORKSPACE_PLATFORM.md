# Workspace Platform (V4)

V4 adds a project workspace layer on top of the canonical execution funnel:
project files, knowledge collections with local-first retrieval, versioned
custom tools with a credential vault, and a Docker-sandboxed code interpreter.
All model calls still go through `execute_model` in
`backend/app/services/execution.py`, so telemetry, pricing snapshots and one
immutable Run + usage event are recorded identically.

## Attachment vs Project File vs Knowledge Document vs Artifact

- **Attachment** (`backend/app/services/attachments.py`) — a raw upload.
  Scoped to owner + project (+ conversation/message when known). Filenames are
  sanitized (never a storage path); MIME is sniffed from magic bytes, never
  trusted from the client declaration. Kinds: `text`, `image` (png/jpg/jpeg/
  webp, dimensions via PIL, never OCR), `pdf` (pypdf/PyPDF2 text with page
  markers, best-effort). Unsupported types (incl. SVG/script-bearing content;
  HTML decodes as plain text, never rendered as markup) are refused. A failed
  extraction still stores the bytes with a `failed` status — the source is
  never destroyed. Public projections strip `storage_ref`/physical paths;
  `extracted_text` is excluded unless explicitly requested (`has_extracted_text`
  + `text_preview` only). Limits: per-file / per-message-count / total bytes
  from `MAX_ATTACHMENT_SIZE_MB` / `MAX_ATTACHMENTS_PER_MESSAGE` /
  `MAX_TOTAL_ATTACHMENT_SIZE_MB`.
- **Project File** (`backend/app/services/project_files.py`) — a stable
  project-scoped metadata pointer over one attachment blob: `display_name`,
  `description`, `tags`, `status` (`active` | `detached` | `deleted`), plus a
  copied `mime_type` / `kind` / `size_bytes` / `sha256`. `create_from_attachment`
  requires the attachment to live under the same owner + project (missing 404,
  cross-project 403) and is idempotent per attachment while `active`.
  `upload_project_file` enforces `PROJECT_FILE_MAX_BYTES` then persists bytes
  via the attachment service. Patches touch metadata only; `file_references`
  counts knowledge documents (`project_file_id`) and runs (`attachment_ids`).
- **Knowledge Document** (`backend/app/services/knowledge.py`) — one project
  file attached to one collection: `{collection_id, project_file_id,
  attachment_id, display_name, status, content_hash, chunk_count,
  token_estimate, embedding_model, version}`. Statuses: `pending` → `processing`
  → `ready`, or `failed` (contract/extraction/chunk-cap failures) /
  `unavailable` (embedding provider failures). `version` increments on every
  reindex; old chunks of the document are deleted before new chunks land.
  Collection config edits affect future indexing only. Images carry no text
  (attach/index rejects with 422). Indexing is synchronous by design.
- **Artifact** (`backend/app/routers/artifacts.py`) — a free-form,
  user-authored record `{project_id, title, artifact_type, content, source,
  source_ref, metadata, version, model_id/model_name, run_id}`. No automatic
  linkage: `source_ref`/`run_id`/model identity are plain fields, not enforced
  foreign keys. `PATCH` bumps `version`; `DELETE` is a hard delete. Findings
  may cite artifacts as evidence (`type: "artifact"`); research links via
  `related_artifact_ids`. Code-interpreter sandbox outputs are **not**
  artifacts — they live on `code_executions.files` under
  `data/sandbox-outputs/<execution_id>/` (see below).

## Tool vs ToolVersion vs ToolCredential vs ToolCall

- **Tool** (`backend/app/services/custom_tools.py`, `tools.py`) — the mutable
  registry entry: `{name (unique per owner+project, 409 on conflict), scope
  (owner/project/global), enabled, builtin, tool_type (http|openapi|
  code_interpreter; builtins are `function`), input/output_schema mirror,
  permissions, risk_level, version (int), current_version_id, tags}`. Builtins
  (`calculator`, `datetime`, `json_transform`) are seeded, have no versions,
  and are immutable (disable instead; update/delete rejected). A global
  `code_interpreter` tool doc + immutable v1 snapshot is seeded by
  `ensure_system_tools`. Registry views (`services/tools._public_tool`, the
  single source of truth; `routers/tools._public_tool` delegates to it) report
  structured availability: disabled tools → `(False, "Tool is disabled")`;
  `code_interpreter` → the cached-only sandbox snapshot (cold cache →
  `"Code interpreter status unknown"`, never a faked online status; probe
  failures keep their safe sandbox reason; unexpected errors →
  `"Code interpreter status unavailable"`); everything else → executor
  resolvability. Listing never blocks on Docker. Model-gated states
  (`MODEL_UNSUPPORTED` / `MODEL_CAPABILITY_UNKNOWN`, via
  `providers.model_capability_status` which reports `UNKNOWN` instead of
  guessing) and project scoping (`DISABLED_BY_PROJECT`) are left to the
  frontend. `GET /code-interpreter/status` (fresh probe) and its alias
  `GET /tools/code-interpreter/status` return the same allowlisted
  `{available, reason, image, runtime, limits, policy}` payload — no docker
  daemon details, host paths, container inventory, or secrets.
- **ToolVersion** — an immutable snapshot. Every behavior change appends
  `max+1` (`{tool_id, version, version_label, tool_type, input_schema,
  output_schema, permissions, risk_level, implementation_config, source
  (manual|openapi|system), immutable: true}`); versions are never mutated in
  place. Execution always validates against the **version** snapshot, and the
  Run pins `tool_version_ids {tool_id: version_id}`. Missing snapshot → 500;
  `list_versions` returns ascending order. `http`/`openapi` configs are
  cleaned by `http_tool.validate_implementation_config`; `code_interpreter`
  configs accept only `{limits}`.
- **ToolCredential** (`backend/app/services/tool_credentials.py`) — the vault
  row `{project_id, name (unique per project, 409), credential_type
  (API_KEY|BEARER_TOKEN|BASIC_AUTH|CUSTOM_HEADER), encrypted_value (Fernet via
  `ENCRYPTION_KEY`, 8 KB max), metadata}`. The public view is `{id, owner_id,
  project_id, name, credential_type, has_value: true, metadata, timestamps}` —
  it asserts `encrypted_value`/`value` are absent. Plaintext is produced only
  server-side at call time: `resolve_credential_value` (enforces same-project,
  403 otherwise) and `resolve_secret_template` for `{{secret:NAME}}`
  (unknown name → 400 before any network call; returns
  `(resolved, sanitized)` with `[secret:NAME]` markers). `sanitize_config`
  scrubs configs for snapshots/traces. Delete is refused with 409 while any
  tool version references the credential (by id, or by same-project secret
  name).
- **ToolCall** — the persisted funnel record. Both `tools.execute_tool` and
  `custom_tools.execute_custom_tool` run validate → authorize → resolve → run
  and **always** persist a ToolCall best-effort, including validation,
  permission and not-implemented outcomes. Fields: `tool_id/name/version/
  version_id/type`, truncated input (20k chars), `validation_status`,
  `permission_status`, `execution_status`, capped output (200k chars),
  truncated error (300 chars), timings, `trace_id`/`run_id`/`project_id`,
  plus **two distinct id spaces**: internal `id` (the ToolCall record id;
  `CodeExecution.tool_call_id` references this) and `provider_tool_call_id`
  (the LLM's `call.id`, threaded from the agentic loop; `None` for
  standalone tests). `TOOL_RESULT` spans carry both ids; they are never
  conflated.
  Custom-tool authorization is explicit-enable: selecting the tool grants its
  declared permissions (`granted_permissions` defaults to the declared set; a
  smaller set denies with `TOOL_PERMISSION_DENIED`). Builtin permission check
  is default-deny except `calculator.execute`, `datetime.read`,
  `json.transform`, which are implicit.

## Model capability provenance

(`backend/app/models.py`, `backend/app/routers/registry.py`,
`backend/app/providers.py`)

- **Provenance** — `ModelRecord`/`ModelCreate`/`ModelUpdate` carry
  `capability_source` (`PROVIDER` | `VERIFIED` | `MANUAL` | `UNKNOWN`,
  default `UNKNOWN`, backward compatible). Capabilities are never fabricated:
  unknown metadata reports `UNKNOWN` via `model_capability_status`; catalog
  normalization infers tool support only from an explicit `"tools"` entry in
  `supported_parameters`, never from model names; raw provider signals
  (`supported_parameters`, architecture modality/modalities) are preserved in
  `provider_metadata` alongside the normalized view.
- **Manual override** — `PATCH /models/{id}` (auth required, owner-scoped)
  accepts `capabilities.{tools,vision,streaming,text,structured_output}`
  (unknown keys dropped, values boolean-coerced) merged over the stored map
  without wiping other keys, and sets `capability_source: "MANUAL"` unless an
  explicit valid source is given. Omitting `capabilities` leaves provenance
  untouched.
- **Refresh preservation** — `POST /models/{id}/refresh-metadata` refreshes
  pricing/modalities/provider metadata always, but a `MANUAL` record keeps its
  capabilities **and** its source; non-manual records adopt catalog
  capabilities with source `PROVIDER`.

## Project Default vs Conversation Override vs Run Snapshot

(`backend/app/services/project_manager.py`, `backend/app/routers/workspace.py`,
`backend/app/services/execution.py`)

- **Project Default** — `default_model_id`, `default_system_instruction`,
  `default_temperature/max_tokens/top_p/streaming`, `default_context_strategy
  (default "FULL")`, `default_context_options`, `default_enabled_tool_ids`,
  `default_knowledge_collection_ids`, `default_retrieval_options`.
  `resolve_project_defaults` normalizes missing values to `None`/`""`/`"FULL"`/
  `[]`/`{}` so callers see a complete shape.
- **Conversation Override** — the conversation document copies project defaults
  at creation (`conversation_fields_from_project`; a `None` override keeps the
  inherited value) and stores them under conversation field names
  (`default_model_id`, `system_prompt`, `temperature`, …, `enabled_tool_ids`,
  `knowledge_collection_ids`, `retrieval_options`). `PATCH /conversations`
  edits these per conversation.
- **Resolution** — `resolve_effective_config` merges per key:
  project → conversation → request, with per-key source attribution
  (`project` | `conversation` | `request` | `default`). `None`/empty
  (`""`/`[]`/`{}`) counts as unset and falls through — except request-level
  `enabled_tool_ids` / `knowledge_collection_ids`, where a present non-`None`
  value (even `[]`) **replaces** the inherited value (explicit clear). Note:
  the workspace send path (`_resolve_effective` in `routers/workspace.py`)
  normalizes empty request lists to `None` first, so an empty per-turn tools/
  knowledge selection there falls through to the conversation instead of
  clearing; the canonical resolver contract above applies to direct callers.
  `False`/`0` are explicit values and never fall through.
- **Run Snapshot** — `execute_model` freezes the effective inputs onto the Run:
  `attachment_ids` + `attachment_hashes` (sha256), `knowledge_collection_ids` +
  `knowledge_provenance` (see Retrieval), resolved `enabled_tool_ids` +
  `tool_version_ids`, `config_sources`/`effective_config` (when project
  defaults are passed), `context_snapshot_id` (persisted context assembly),
  `pricing_snapshot`, and for benchmarks `configuration_snapshot`. Runs stay
  append-only; re-run/clone never overwrite history.

## Knowledge Retrieval vs direct Attachment Context

- **Direct Attachment Context** — turn-scoped. Text/pdf attachments contribute
  their stored `extracted_text` to the current user turn
  (`_attachment_text_augmentation`: 200k chars/file, 400k chars total, with
  `[truncated: …]` markers); images contribute nothing as text — the execution
  funnel injects base64 image content blocks into the last user message
  (`build_image_content_block`). `ATTACHMENTS` context components carry the
  same content plus `{attachment_id, filename, mime_type, kind, size_bytes,
  sha256, processing/extraction_status}` metadata. History replay is text-only:
  prior attachment blocks are not reconstructed for history. Images are gated
  **before** any Run is created: a non-vision model with image attachments
  fails validation (`VISION_UNSUPPORTED`) in both the workspace precheck and
  the execution funnel — images are never silently dropped.
- **Knowledge Retrieval** (`backend/app/services/retrieval.py`) — collection-
  scoped semantic recall. `retrieve(owner, project, query, collection_ids,
  top_k ≤ 50, score_threshold)`: verifies ownership + project membership
  (deleted collections rejected), groups collections by embedding **model**
  (vectors from different models are not comparable — one query embedding per
  group), embeds the query, scans via `vector_store.search` per group, merges
  and sorts descending. Empty results are normal (`result_count: 0`), not an
  error. Retrieval failures never break execution: the Run records
  `knowledge_error` + a `knowledge_retrieval_unavailable` context warning and
  continues without the component. `POST /knowledge/search` exposes the same
  pipeline without creating a Run.
- **Provenance** — `retrieval_provenance` pins `{query_hash, collection_ids,
  chunk_ids, scores, ordering, retrieval_config, embedding_models,
  document_versions}` onto the Run (`knowledge_provenance`) so the evidence
  stays explainable after a later reindex. The prompt-visible
  `RETRIEVED_CONTEXT` component formats ranked chunks with
  `[source N: label (collection …, chunk …, score …)]` headers (12k-char cap).
  Per-turn `retrieval_options` (`top_k`, default `KNOWLEDGE_RETRIEVAL_TOP_K`;
  `score_threshold`) ride the same project → conversation → request
  inheritance as tools.

## Embedding / vector-store replacement boundary

(`backend/app/services/embeddings.py`, `vector_store.py`, `knowledge.py`)

- **Default: local hashing** (`EMBEDDING_PROVIDER=local`,
  `EMBEDDING_MODEL=local-hashing-v1`, `EMBEDDING_DIM=256`). Feature hashing
  over word unigrams (weight 1.0) + character trigrams (weight 0.5), SHA-256
  into `dimension` buckets (stdlib `hashlib` only), L2-normalized — real cosine
  geometry, weaker semantics than neural embeddings. Explicit offline provider
  for local use and tests; production collections should point at a neural
  provider. Empty texts embed as zero vectors.
- **Option: OpenAI-compatible** (`provider: "openai_compatible"` + collection
  `provider_id`). Calls `{base_url}/embeddings` with `{model, input}` (BYOK:
  the provider doc's `api_key_enc` is decrypted server-side, never logged).
  Base URL passes the SSRF guard; redirects disabled; 30 s timeout
  (`EMBEDDING_TIMEOUT_SECONDS`); 4 MB response cap; every vector must be a
  finite-number list of uniform dimension (else 502); empty inputs keep zero
  vectors so input order is preserved. Resolution requires the provider doc to
  be `provider_type == "openai_compatible"` and enabled. Only `local` and
  `openai_compatible` are accepted (`normalize_embedding_config` rejects the
  rest); batches embed with `EMBEDDING_BATCH_SIZE` (16).
- **Indexing** — `index_collection` marks each pending/failed/unavailable doc
  `processing`, extracts text (stored attachment extraction → pdf re-parse →
  raw decode; images 422; ~200k-char cap), chunks (`fixed` sliding windows or
  `paragraph` packing, `KNOWLEDGE_DEFAULT_CHUNK_SIZE` 800 /
  `KNOWLEDGE_DEFAULT_CHUNK_OVERLAP` 100 / `KNOWLEDGE_MAX_CHUNK_CHARS` 4000),
  embeds batches, then replaces that document's chunks (version bumped) and
  attaches vectors via `upsert_chunk_embedding`. Caps: 200 documents /
  collection (`KNOWLEDGE_MAX_DOCUMENTS_PER_COLLECTION`), 5000 chunks /
  collection (`KNOWLEDGE_MAX_CHUNKS_PER_COLLECTION`). Chunk list output ships
  `content_preview` (500 chars) and excludes `embedding` unless
  `include_embeddings=true`.
- **Where to swap** — `vector_store.search(owner_id, collection_ids,
  query_embedding, top_k, score_threshold)` is the seam: it loads chunk
  embeddings from Mongo and scores them in Python (O(N) per query — correct
  and dependency-free). Replace that function with a dedicated vector DB
  (pgvector, Qdrant, Mongo Atlas Vector Search) keeping the contract
  `(owner, collection_ids, query_embedding, top_k, threshold) →
  [{chunk, score}]` with computed cosines. To add an embedding backend, add a
  provider class behind the `EmbeddingProvider.embed` contract in
  `embeddings.py` and a branch in `resolve_embedding_provider` (+ the
  `normalize_embedding_config` allowlist). The collection `embedding_config`
  snapshot (`provider/model/dimension/provider_id`) travels with the
  collection; config edits affect future indexing only.

## Code Interpreter sandbox boundaries + image build/run

(`backend/app/services/code_interpreter.py`,
`backend/docker/code-interpreter/Dockerfile`, `backend/app/routers/code_interpreter.py`)

- **Trust boundary** — model- or user-supplied Python runs **only** inside an
  ephemeral, non-privileged Docker container. The FastAPI host never
  `exec`/`eval`/`compile`s untrusted code and never spawns a host interpreter;
  the `docker` CLI subprocess is the boundary: the host stages inputs, spawns
  `docker run`, enforces a wall-clock timeout, truncates output, and collects
  files. No host code/state directories are mounted; the Docker socket is
  never exposed.
- **Container guarantees** (`build_docker_command`) — `--network none`
  (no network at all); `--read-only` root with `tmpfs` `/workspace`
  (`CODE_INTERPRETER_TMPFS_MB`, default 64m) and `/tmp` (16m); CPU/RAM/PID
  caps from config (`CODE_INTERPRETER_MEMORY_MB` 512 /
  `CODE_INTERPRETER_CPUS` 1.0 / `CODE_INTERPRETER_PIDS_LIMIT` 128, swap pinned
  to memory); user `65532:65532`, `--cap-drop ALL`,
  `--security-opt no-new-privileges=true`, never `--privileged`; exactly two
  mounts — read-only input dir → `/workspace/input`, writable output dir →
  `/workspace/output` — both under a dedicated `sandbox-staging` sibling of
  `ATTACHMENT_STORAGE_DIR` with realpath containment; env whitelist only
  (`PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`, `MPLBACKEND` — server
  secrets never imported); the program streams to `python -` over STDIN, never
  via argv or environment.
- **Image build/run** — build from the repo root:
  `docker build -t omnilab-code-interpreter:latest
  backend/docker/code-interpreter` (`FROM python:3.11-slim`, pinned
  `numpy==1.26.4 pandas==2.2.2 matplotlib==3.8.4`, `USER 65532:65532`, no
  entrypoint — the backend always invokes `python -`). The backend builds the
  full flag set programmatically (reference command in the Dockerfile header;
  never relax the flags). `CODE_INTERPRETER_IMAGE`
  (`omnilab-code-interpreter:latest`), `CODE_INTERPRETER_RUNTIME` (`docker`),
  `CODE_INTERPRETER_ENABLED` (default `true`; `false` makes the tool report
  unavailable and persist `unavailable` executions instead of running).
- **Availability** — `check_availability` probes `docker image inspect`
  (10 s cap, 60 s cache, safe reason strings, never raises); registry views
  use the cached-only `check_availability_sync` so listing tools never blocks
  on Docker. Tool input schema is `{code (required), input_file_ids}`.
- **Execution record** — `execute_code` validates non-empty code ≤
  `CODE_INTERPRETER_MAX_CODE_CHARS` (100k; full code stored ≤ 100k chars else
  a 2k preview with `code_truncated`), stages `input_file_ids` (same-project
  attachments only, sanitized `NNN_name` staging names), enforces
  `CODE_INTERPRETER_TIMEOUT_SECONDS` (30) wall-clock, caps stdout/stderr at
  `CODE_INTERPRETER_MAX_OUTPUT_BYTES` (200k) and files at
  `CODE_INTERPRETER_MAX_FILES` (10) × `CODE_INTERPRETER_MAX_FILE_BYTES`
  (5 MB) with sha256 + mime manifest (`html`/`htm`/`svg` carry
  `content_warning: "active-content"`). Statuses: `success` / `error` /
  `timeout` / `unavailable` (never raises for sandbox outcomes; only contract
  violations raise). Ephemeral staging is always removed; durable outputs
  persist at `data/sandbox-outputs/<execution_id>/` until `delete_execution`
  removes the directory and the record. Output-file reads require an exact
  manifest-name match with realpath containment. At most
  `CODE_INTERPRETER_MAX_EXECUTIONS_PER_RUN` (5) code executions run per model
  Run; standalone tests run through `POST /tools/{id}/test` on a fresh
  `tool_test` trace. **Failure semantics**: `CodeExecution.error` is canonical
  for standalone executions; inside agentic runs, tool/code failures correlate
  `Run → ToolCall → CodeExecution → FailureRecord → Trace` (via `run_id` /
  `tool_call_id` / `provider_tool_call_id` / `trace_id`) and only the terminal
  error Run emits one SYSTEM FailureRecord (`record_execution_failure`,
  idempotent per Run — no duplicates).

## Custom tool security model

(`backend/app/services/http_tool.py`, `tool_credentials.py`, `app/urlguard.py`)

- **SSRF** — every outbound tool/embedding/OpenAPI-fetch call validates the
  target with `urlguard` at save time (static host/path shape) **and**
  immediately before the request (async DNS re-resolution to shrink the
  rebinding window; a residual TOCTOU window is documented in `urlguard.py`).
  Only `http`/`https`; embedded credentials and unresolvable hosts rejected;
  query strings are allowed and preserved while SSRF validates host/IP/protocol. Every resolved A/AAAA address must be
  public: loopback, private (RFC1918/unique-local), link-local, unspecified,
  multicast, reserved and cloud metadata (`169.254.169.254`,
  `100.100.100.200`, `fd00:ec2::254`) are blocked by default.
  `PROVIDER_ALLOW_LOCAL_URLS=true` permits loopback/private/link-local/
  unspecified for local gateways (Ollama/LiteLLM/vLLM) but **never** metadata;
  `PROVIDER_URL_ALLOW_PRIVATE=true` permits RFC1918/unique-local only;
  `PROVIDER_URL_ALLOWLIST=host[:port]` entries bypass checks explicitly
  (including metadata) on purpose. Redirects are unconditionally disabled
  (`follow_redirects=False` in `http_tool`, `openapi_tools`, `embeddings`,
  provider adapter; `SAFE_REDIRECTS = False`). Note: `TOOL_HTTP_MAX_REDIRECTS`
  is defined in config but no executor consults it — it is currently inert.
- **Secrets** — `{{secret:NAME}}` placeholders and `credential_id` references
  resolve server-side only. Order per call: resolve secrets → render
  `{param}` templates (URL-quoted; missing key → 400 naming the key, values
  never echoed; `{{` leftovers rejected) → SSRF-validate the final URL →
  send. Secret-bearing query params and URL path segments are redacted to
  `[secret]` / `[secret:NAME]` in every returned/stored view; responses return
  `{status_code, ok, headers: {content-type}?, body, truncated, latency_ms,
  url (public), method, credential_ref: {credential_id}?}` — never secret
  values. ToolCall metadata uses `sanitized_call_snapshot` (method,
  `url_sanitized` preview, `headers_sanitized`, `credential_ref`). `Host`,
  `Content-Length`, `Connection`, `Transfer-Encoding` headers are stripped
  (case-insensitively). Credential plaintext lives only in the request memory
  of `execute_http` / the embedding provider.
- **Caps** — methods allowlisted to GET/POST/PUT/PATCH/DELETE; per-call
  timeout 1–60 s (default `TOOL_HTTP_TIMEOUT_SECONDS` 15);
  `url_template` ≤ `TOOL_HTTP_MAX_URL_LENGTH` (2048); response body ≤
  per-tool `max_bytes` (default `TOOL_HTTP_MAX_RESPONSE_BYTES` 1 MiB, else
  502); non-JSON bodies decode UTF-8-replace with a 200k-char preview;
  optional dotted `response.extract_path`. Risk is advisory per method
  (GET low; POST/PUT/PATCH high; DELETE critical) — the stored `risk_level`
  is authoritative. Validation/permission failures persist a ToolCall and skip
  execution; upstream failures surface as 502 `TOOL_EXECUTION_FAILED` with the
  message truncated.

## OpenAPI trust model

(`backend/app/services/openapi_tools.py`, `backend/app/routers/tools.py`)

- **Parse** (`parse_spec`) — size cap `OPENAPI_MAX_SPEC_BYTES` (2 MiB); JSON
  when the text starts with `{`, else YAML `safe_load`; must be an object;
  **OpenAPI 3.x only** (Swagger 2.0 explicitly rejected, 422).
- **Extract** (`extract_operations`) — HTTP methods GET/POST/PUT/PATCH/DELETE
  only; local `#/` `$ref`s resolved to depth 5 (remote refs dropped as
  `{_unresolved_ref: true}`, over-deep refs stay unresolved); parameters kept
  only for `query`/`path`/`header`; `requestBody` prefers `application/json`
  else the first media type; capped at 200 operations; descriptions stripped
  of control chars and capped at 2k; `base_url` from `servers[0]` unless
  `base_url_override` pins it.
- **Fetch** (`fetch_spec_from_url`, `POST /tools/import-openapi`) — the spec
  URL is fetched server-side behind the SSRF guard with redirects disabled
  (20 s timeout); content-type must include `json`/`yaml`/`yml`/`text`; size
  and UTF-8 enforced. Either raw `spec` text or `spec_url` may be supplied.
- **Generate** (`operation_to_tool_def`, `POST /tools/generate-from-openapi`)
  — each included operation becomes a `CustomToolCreate`-compatible payload
  (query/header/body `{param}` mappings, `required` lists, `source: "openapi"`,
  tag `openapi`); default permission `http.<host>.<method>` and method-based
  risk are overridable per selection; `implementation_config` is re-validated
  through the HTTP validator (so a hostile spec cannot smuggle a method, a
  non-http URL, or a forbidden header). `preview_import` persists nothing.
- **Trust statement** — spec *text* (names, summaries, schemas) is treated as
  author-controlled input (sanitized, length-capped) but never as authority:
  every execution still passes the HTTP executor's secret resolution,
  parameter rendering, SSRF validation, method/header/timeout/size caps, and
  response extraction. Pin `base_url_override` when the spec's `servers`
  entry is not trustworthy.

## Arena fairness rules

(`backend/app/services/arena.py`, `backend/app/routers/arena.py`)

- The same task/system prompt/params go to 2–4 models in parallel through the
  canonical funnel; every competitor — success or structured failure — yields
  exactly one immutable Run.
- **Shared attachments** — the same validated attachment docs + ids reach
  every competitor; the experiment stores `attachment_ids` + `attachment_hashes`
  (sha256). Cross-project attachment ids are rejected (404).
- **Shared knowledge** — retrieval runs **once** on the task text; the same
  `RETRIEVED_CONTEXT` component + `knowledge_provenance` is injected into
  every competitor via `precomputed_retrieval` (stored on the experiment and
  on each Run). Competitors never see different evidence.
- **Modality gate** — when the shared turn carries images, every candidate
  model must report vision `SUPPORTED` **before** anything runs; otherwise the
  whole Arena aborts with a 400 naming the model. No competitor gets a head
  start on degraded input, and images are never silently dropped.
- **Blindness** — blind Arenas shuffle presentation order server-side
  (`Response A/B/…`), keep `blind_map` on the experiment document only, and
  serve identity-free views: experiments without `blind_map`/`model_ids`,
  runs whitelisted to id/label/status/content/latency/tokens/category-level
  error/`created_at`/`finish_reason` (resolution errors use
  `safe_error_message` so model/provider names cannot leak). Reveal requires
  `POST /api/experiments/{id}/reveal` and is audited in `reveal_events`;
  duplicate human winner votes are 409 unless `replace: true`; a client
  `blind_label` is advisory — the server resolves the real label from
  `blind_map`.

## Experiment asset references

- **Dataset case assets** — `DatasetCaseCreate` / `DatasetVersionCaseCreate`
  carry `asset_attachment_ids: List[str]` (plain attachment-id references).
  The benchmark planner vision-gates them: any config whose model must read a
  case with image/file assets must be vision-capable, else the plan is
  rejected naming the incompatible configurations
  (`backend/app/services/benchmark.py`). Case content itself reaches the model
  through the `DATASET_CASE` context component; asset bytes are resolved by id
  at execution time with the same owner/project validation as turn
  attachments.
- **Findings evidence** — `FindingEvidence {type, ref_id, label}` with
  `type ∈ {experiment, research, artifact, run, evaluation, council, dataset,
  conversation}` (`backend/app/models.py`). The findings router validates the
  project boundary and resolves a navigable route + title per link; missing or
  foreign references stay tolerant (`exists: false`) rather than failing the
  write. Evaluations similarly link `run_ids`/`evaluation_ids`/
  `configuration_ids`/`dataset_version_id` into evidence bundles.
- **Research links** — `related_experiment_ids / related_artifact_ids /
  related_conversation_ids / related_council_ids / related_finding_ids /
  related_dataset_ids / related_run_ids / related_dataset_version_ids /
  related_benchmark_plan_ids` are ID-link lists, not enforced joins.

## Deletion / retention semantics

- **Runs & evaluations are append-only.** No Run delete endpoint exists;
  re-run/clone never overwrite history. Deleting an experiment cascades to
  its evaluations, experiment configs, benchmark plans, runs and reveal
  events (`DELETE /api/experiments/{id}`).
- **Attachments** — hard delete by default (metadata row + stored bytes).
  When the attachment was consumed by a Run (`referenced_by_run`/`run_ids`
  via `mark_attachment_used`), deletion instead sets `detached: true` +
  `detached_at` and keeps the bytes, so Run evidence is never orphaned.
- **Project files** — soft-delete only (`status: "deleted"`); raw attachment
  bytes stay. `status="all"` lists detached + deleted.
- **Knowledge** — collections soft-delete (`status: "deleted"`); documents
  and chunks are **retained**, excluded from listing/search/retrieval by
  default (`include_deleted` opts back in). Reindex replaces a document's
  chunks outright (old chunks `delete_many` by `document_id`, version bump).
- **Tools** — delete refused with 409 while tool calls exist for the tool or
  any Run enables it; otherwise the tool row and its versions are removed.
  Builtins/global tools cannot be deleted (400). Versions are never deleted
  individually — only with their tool.
- **Credentials** — delete refused with 409 while any tool version references
  the credential (by id or same-project secret name).
- **Code executions** — `delete_execution` removes the record **and** its
  durable `data/sandbox-outputs/<execution_id>/` directory. Ephemeral
  `sandbox-staging` dirs are removed after every run regardless of outcome.
- **Project delete** (`DELETE /api/projects/{id}`) cascades experiments,
  conversations (+ messages), runs, evaluations, artifacts, councils,
  research, findings, datasets (+ versions/cases), usage/reveal events,
  prompts (+ versions), experiment/benchmark rows, project files, knowledge
  collections/documents/chunks, tools, tool versions, tool credentials, code
  executions, and project activity — plus best-effort removal of sandbox
  output dirs. The `attachments` collection is **intentionally not** in the
  cascade: raw attachment bytes keep current behavior and survive project
  deletion. There is no TTL/retention sweeper; deletions are immediate except
  for the soft states above.

## V4.1 Tooling UX: availability states + failure semantics + provenance

(Additive over V4; no sandbox flags, Docker policy, or V4 contracts changed.)

- **Availability states** — the registry `_public_tool` view reports
  `(available, availability_reason)` from one shared helper
  (`services/tools.code_interpreter_availability` + `routers/tools._public_tool`
  delegating to it): disabled tools → `(False, "Tool is disabled")`;
  `code_interpreter` → cached-only sandbox snapshot (cold cache →
  `"Code interpreter status unknown"`, never faked online; probe failures keep
  their safe sandbox reason, e.g. `"sandbox image is not available"`);
  everything else → executor resolvability. Model-gated
  (`MODEL_UNSUPPORTED` / `MODEL_CAPABILITY_UNKNOWN`) and project-scoped
  (`DISABLED_BY_PROJECT`) states are resolved frontend-side
  (`frontend/src/lib/capabilities.resolveToolGate`: project scoping → enabled
  flag → live `/code-interpreter/status` for CI → backend availability →
  model `caps.tools`), so every disabled workspace tool carries an explicit
  gate + human reason, never a bare grey checkbox.
- **Status alias** — `GET /tools/code-interpreter/status` returns the byte-
  identical allowlisted `{available, reason, image, runtime, limits, policy}`
  payload as `GET /code-interpreter/status` (fresh probe both paths; no docker
  daemon details, host paths, container inventory, or secrets).
- **Failure semantics** — standalone tool tests persist `ToolCall.error` as
  canonical (no FailureRecord — no Run exists). Inside agentic runs,
  tool/code failures correlate `Run → ToolCall → CodeExecution →
  FailureRecord → Trace` via shared `run_id` / internal `tool_call_id` /
  `provider_tool_call_id` (the LLM `call.id`, `None` for standalone) /
  `trace_id`, and only the terminal error Run emits one SYSTEM FailureRecord
  (`record_execution_failure`, idempotent per Run — no duplicates).
  `CodeExecution.tool_call_id` references the internal ToolCall `id`
  (never the provider call id); `TOOL_RESULT` spans carry both ids.
  Sandbox-unavailable executions persist `status: "unavailable"` with
  `error.category: "SANDBOX_UNAVAILABLE"` (never raise).
- **Provenance** — `capability_source` (`PROVIDER` | `VERIFIED` | `MANUAL` |
  `UNKNOWN`, default `UNKNOWN`) is never fabricated: catalog normalization
  infers tool support only from an explicit `"tools"` entry in
  `supported_parameters` (missing → `UNKNOWN`, never `UNSUPPORTED`, never from
  model names); raw signals stay in `provider_metadata`. `PATCH /models/{id}`
  with `capabilities` merges known keys and sets `MANUAL` (auth required,
  owner-scoped); `POST /models/{id}/refresh-metadata` always refreshes
  pricing/modalities/metadata but a `MANUAL` record keeps its capabilities
  **and** source.
- **Isolation boundary** — `list_tools` is owner-scoped by design (global +
  owner tools); project isolation is enforced at credential scope
  (`resolve_credential_value` 403 cross-project, per-project vault lists),
  cross-owner reads (404), attachment project checks, and workspace project
  gating (`DISABLED_BY_PROJECT`) — not by hiding same-owner tools from the
  registry list.

## Deferred scope (explicitly NOT built)

Verified against `backend/app/services` + `routers` (no such services or
routes exist; `TOOL_HTTP_MAX_REDIRECTS` is defined but no executor reads it):

- Agent Lab, Routing, Certification, Orchestrator, AI Scientist.
- MCP / browser / SSH / general-Docker tools. (Docker exists only as the
  code-interpreter sandbox boundary, not as a user-facing tool family; the
  "Red Team" string in `frontend/src/pages/Council.tsx` is a council-persona
  preset label, not a red-team service.)
- Distributed workers / async queues: benchmark execution and knowledge
  indexing are synchronous and bounded (concurrency caps, per-collection
  caps); the vector index is the local Mongo-backed cosine scan until
  `vector_store.search` is swapped for an external vector DB.
- Full Prompt Lab: `Prompt` / `PromptVersion` provide immutable,
  auto-numbered prompt identity that experiment configurations can reference;
  the lab UI/workflows around them remain deferred.
- `TOOL_HTTP_MAX_REDIRECTS`: reserved configuration key; redirects are
  unconditionally disabled today (`follow_redirects=False` everywhere).
