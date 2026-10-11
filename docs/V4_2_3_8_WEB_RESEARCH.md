# V4.2.3.8 Web Research Pipeline + Agent Timeline UX

This document defines the OmniLab V4.2.3.8 composite **`web_research`** tool
(`tool-research-0001`), the additive **batch-fetch** capability it builds on,
and the **Workspace agent activity timeline** that surfaces it. It follows
`docs/V4_2_3_SEARCH_FETCH.md` (Search + Fetch foundation) and
`docs/V4_2_3_7_AGENTIC_STREAMING.md` (stream protocol); it is **additive** and
changes no atomic tool contract.

Implementation sources: `backend/app/services/builtin_tools.py`,
`backend/app/services/fetch_providers.py`, `backend/app/services/execution.py`,
`backend/app/services/stream_events.py`, `backend/app/models.py`,
`backend/app/services/valves.py`, `backend/app/routers/workspace.py`, and the
Workspace frontend under `frontend/src/`.

> **Superseded by [V4.2.3.14](V4_2_3_14_RESEARCH_BREADTH.md) (research breadth).**
> This document records the V4.2.3.8 behavior and is kept for historical
> context. Two statements below are stale and corrected here:
>
> - `max_fetches` now defaults to **5** (not 3) and is bounded **1..10** (not
>   `1..5`); `max_results` remains default **5**, bounded `1..10`.
> - `MAX_BATCH_FETCH_URLS` **no longer clamps logical research breadth**. It is
>   now the protected **provider execution batch** ceiling only — the maximum
>   number of URLs one fetch-provider request may carry. Logical breadth is
>   bounded by the research policy ceilings (hard max 10) and the remaining run
>   budgets, and a larger breadth is chunked across provider requests.
>
> The canonical contract is [V4_2_3_14_RESEARCH_BREADTH.md](V4_2_3_14_RESEARCH_BREADTH.md).

---

## 1. Atomic vs composite: orchestration, not a new execution path

`web_search` (`tool-search-0001`) and `web_fetch` (`tool-fetch-0001`) remain the
**first-class atomic tools**. `web_research` (`tool-research-0001`) is a
**deterministic orchestration composite**: it calls the *existing* search
adapter, runs a pure URL-selection pass, then calls the *existing* fetch adapter
in one batch. There is **no model call inside `run_research`** — see
`run_research()` at `backend/app/services/builtin_tools.py:880`, whose only
awaits are `search_adapter.search(...)` and `fetch_adapter.fetch(...)`
/ `fetch_many(...)`.

```
+--------------------------------------------------------------+
|  Model (one TOOL_CALL: web_research)                          |
+------------------------------+-------------------------------+
                               v
             run_research()  (builtin_tools.py:880)  -- NO model call
                               |
             +-----------------+------------------+
             v                                    v
   existing search adapter              existing fetch adapter
   (SearchProviderRegistry)             (FetchProviderRegistry)
             |                                    |
     atomic run_search()                  atomic run_fetch() / fetch_many()
     (builtin:web_search)                 (builtin:web_fetch)
             \                                    /
              +------------ evidence bundle -----+
                               |
                    (single ToolResult the model sees)
```

**Provider != tool.** A *provider* is a credential/config adapter record
(`prov-search-tinyfish`, `prov-fetch-standard`, ...) resolved through the valve
layer. A *tool* is the model-facing function (`web_search`, `web_fetch`,
`web_research`). The composite introduces **no new provider**: it reuses the
run's effective search and fetch providers, and reports their ids in
`search_provider` / `fetch_provider`.

Composite wiring is additive in the workspace: `_tool_ids_for()` adds
`tool-research-0001` **only when both** `search_enabled` and `fetch_enabled` are
true, and it never removes the atomic tools
(`backend/app/routers/workspace.py:338-344`). The model chooses between atomic
and composite.

---

## 2. Canonical composite tool definition

Seeded in `backend/app/services/builtin_tools.py:227-340`:

| Field | Value |
| --- | --- |
| `id` | `tool-research-0001` |
| `name` | `web_research` |
| `display_name` | `Web Research` |
| `implementation` | `builtin:web_research` (maps to `run_research` via `EXECUTORS`, `builtin_tools.py:1227`) |
| `permissions` | `["search.execute", "fetch.execute"]` (`builtin_tools.py:333`) |
| `risk_level` | `medium` |
| `tags` | `["web", "search", "fetch", "research"]` |

Because neither `search.execute` nor `fetch.execute` is in
`SAFE_BUILTIN_PERMISSIONS`, the composite still follows **DEFAULT DENY**: it is
only executable when it is in the run's effective `enabled_tool_ids`
(selection-as-intent), exactly like the atomic tools.

Valve routing: `TOOL_VALVE_NAMES` maps `web_research` to the `search` provider
kind (`backend/app/services/valves.py:700-704`), so TinyFish search
configuration is **shared, never duplicated**; provider identity stays internal.

---

## 3. Input schema

`web_research.input_schema` (`builtin_tools.py:240-272`):

| Field | Type | Required | Default | Bounds |
| --- | --- | --- | --- | --- |
| `query` | string | yes | — | non-empty after trim |
| `max_results` | integer | no | `5` | `1..10` |
| `max_fetches` | integer | no | `5` | `1..10` (logical research breadth; not the provider batch cap) |
| `location` | string | no | — | regional hint; overrides the provider default |
| `language` | string | no | — | result-language hint; overrides the provider default |

`max_results` is clamped by `_clamp_int(...)` against the research
results ceiling (`builtin_tools.py:732-744, 900-905`). `max_fetches` is clamped
against the research fetch ceiling and the remaining run fetch budget; as of
V4.2.3.14 it is **no longer** clamped by `config.MAX_BATCH_FETCH_URLS`, which is
provider execution batching only (`builtin_tools.py:907-915`, superseded by
V4.2.3.14). `location`/`language` are threaded into
`search_adapter.search()` only when the adapter signature accepts them, via
`_search_option_params()` (`builtin_tools.py:661-680`).

---

## 4. Output / evidence contract (frozen)

`run_research` returns exactly the shape declared by the tool's `output_schema`
(`builtin_tools.py:273-330`) and produced at `builtin_tools.py:1196-1217`:

| Key | Shape |
| --- | --- |
| `query` | string |
| `outcome` | `"SUCCESS"` if `failed == 0`, else `"PARTIAL_SUCCESS"` |
| `search.provider` | resolved search provider id |
| `search.results` | normalized `{title,url,snippet,rank,...}` list |
| `sources[]` | success: `{url,title,status:"success",text,fetch_provider,duration_ms}`; failure: `{url,status:"timeout"|"failed",error}` |
| `summary` | `{searched,selected,fetched,failed}` |
| `internal_operations[]` | `SEARCH_OPERATION` / `FETCH_OPERATION` descriptors (section 8) |
| `internal_budget` | `{search_calls,fetch_calls,external_requests}` |
| `provider` / `search_provider` / `fetch_provider` | provider ids |
| `duration_ms` | wall-clock of the whole composite |

Failure mapping: a failed fetch whose error code is `FETCH_TIMEOUT` becomes
`status:"timeout"`, otherwise `status:"failed"`; the nested `error` carries
`{code,message}` and, when known, `timeout_seconds`
(`builtin_tools.py:1046-1057`). Success records carry
`fetch_provider` (falling back to `provider`, then the resolved adapter id) and
the per-URL `duration_ms`.

`summary.searched` = number of normalized search results, `summary.selected` =
number of URLs chosen for fetch, `summary.fetched` / `summary.failed` = success
/ non-success source counts (`builtin_tools.py:1190-1206`).

`internal_budget.external_requests = search_calls + fetch_calls`
(`builtin_tools.py:1208-1212`).

---

## 5. Deterministic URL selection — `_select_research_sources`

`_select_research_sources(results, max_fetches)` (`builtin_tools.py:782-841`) is
pure and model-free:

1. **Normalize** each candidate via `_normalize_research_url()`
   (`builtin_tools.py:747-772`): strip the fragment, lowercase scheme + host,
   preserve path/query/userinfo verbatim; drop non-strings/blanks.
2. **De-dupe** preserving first occurrence.
3. **Rank sort** by the provider `rank` ascending; a missing/invalid/non-int
   rank falls back to the original 1-based index; ties break on original order.
4. **Domain-diversity pass**: walk the sorted list taking the first URL of each
   distinct host (one per host, in rank order) until `max_fetches` is reached.
5. **Fill** any remaining slots with the next unselected URLs, still in rank
   order.

The result preserves selection order and never exceeds `max_fetches`. Hosts are
compared via `_host_of()` (`builtin_tools.py:775-779`). `max_fetches <= 0` or an
empty/`None` result list yields `[]`.

`run_research` also honors a runtime `research_budget`: if
`search_remaining <= 0` the search step is skipped, and `max_fetches` is
minimized with `fetch_remaining` (`builtin_tools.py:917-932`).

---

## 6. Batch fetch layer

The batch capability is **additive** over the single-URL `fetch()` contract.
`BaseFetchProvider` declares `SUPPORTS_BATCH_FETCH = False`
(`fetch_providers.py:295`); the read-only `supports_batch_fetch` property reads
it from the class (`fetch_providers.py:330-332`). Only TinyFish and Standard set
it to `True`.

### 6.1 Default (base) `fetch_many`

`BaseFetchProvider.fetch_many(urls)` (`fetch_providers.py:334-358`) is a
sequential, order-preserving overlay that clamps to
`config.MAX_BATCH_FETCH_URLS`, returns one record per clamped URL, marks each
success with `status="success"` plus the requested `url`, and converts any
exception to a secret-free `_per_url_failure` record. It **never raises**.

### 6.2 Standard: bounded local concurrency

`StandardFetchProvider.fetch_many` (`fetch_providers.py:409-444`) runs each URL
through the unchanged single-URL `fetch()` (full local SSRF + redirect
re-validation) under an `asyncio.Semaphore` capped at
`config.FETCH_BATCH_CONCURRENCY` (never more than the batch size), gathers with
`return_exceptions=True`, and preserves input order. Results are still
per-URL records; a failure is a record, not a batch abort.

### 6.3 TinyFish: one provider request

`TinyFishFetchProvider.fetch_many` (`fetch_providers.py:970-1170`) issues
**exactly ONE** HTTP POST for the whole batch:

```json
{ "urls": ["<url>", "..."], "format": "<valve format>", "ttl": 0 }
```

(`fetch_providers.py:1033-1044`.) The provider endpoint is validated with full
local validation (`async_validate_provider_url`), target URLs with
`async_validate_remote_target_url` (syntax/policy only, no local DNS). Invalid
targets are per-URL failures and are excluded from the request; upstream
`results[]`/`errors[]` are mapped back to input URLs; a missing response entry
becomes a `MALFORMED_RESPONSE` failure. A whole-request transport failure
(endpoint validation error, timeout, DNS, 4xx/5xx) raises `FetchError`, which
`run_research` degrades to all-failed sources (section 7).

### 6.4 Provider execution batch caps and the fetch `max_urls` valve

> **Corrected in V4.2.3.14.** `MAX_BATCH_FETCH_URLS` is **provider execution
> batching only**: it caps how many URLs one provider *request* carries. It does
> not cap logical research breadth — `web_research` may inspect up to 10 URLs and
> the provider layer chunks them across one or more requests.

- Provider execution batch cap: `BaseFetchProvider.batch_cap` resolves the
  provider's effective `max_urls` / `fetch_provider_batch_size` and clamps it to
  `config.MAX_BATCH_FETCH_URLS` (default `5`); every `fetch_many` implementation
  bounds a single provider request by `batch_cap` (`fetch_providers.py:374-395`).
- Fetch provider valves expose `max_urls` for both `tinyfish` and `standard`,
  labelled "Batch size (provider batch cap)", with
  `ceiling_attr="MAX_BATCH_FETCH_URLS"`: a project may lower it, never raise it
  above the server cap.
- `public_fetch_provider()` advertises `supports_batch_fetch` (top-level and in
  `capabilities`) without leaking secrets (`fetch_providers.py:1252, 1275, 1281`).

### 6.5 Egress trust boundaries (unchanged SSRF semantics)

`StandardFetchProvider.egress_mode == LOCAL_EGRESS` and
`TinyFishFetchProvider.egress_mode == REMOTE_EGRESS`
(`fetch_providers.py:405-407, 671-673`). Batching does not weaken SSRF:

- **LOCAL (Standard)**: OmniLab opens every connection, resolving and
  SSRF-checking each host and each redirect hop — identical to atomic
  `web_fetch`.
- **REMOTE (TinyFish)**: OmniLab only validates the provider endpoint locally
  and performs syntactic/policy validation of targets, which TinyFish fetches
  server-side. This mirrors the V4.2.3.6/V4.2.3.7 remote-fetch boundary exactly.

Search remains `REMOTE_EGRESS` (`search_providers.py:140-142`), so the composite
never resolves ordinary public hostnames locally.

---

## 7. Partial-failure semantics

One failed source never invalidates the composite result:

- `fetched = count(status == "success")`, `failed = len(sources) - fetched`;
  `outcome = "SUCCESS"` only when `failed == 0`, else `"PARTIAL_SUCCESS"`
  (`builtin_tools.py:1190-1192`). A `PARTIAL_SUCCESS` still yields a successful
  model ToolCall / ToolResult.
- **Search failure propagates.** `search_adapter.search(...)` is awaited without
  a try/except (`builtin_tools.py:960`), so a `SearchError` escapes
  `run_research` and the tool fails — no fabricated evidence.
- **Whole-batch fetch transport failure degrades to all-failed sources.** When
  `fetch_many` raises `FetchError` (endpoint validation, timeout, DNS, upstream
  error), `run_research` catches it and builds a `_per_url_failure(url, exc)`
  record for every selected URL (`builtin_tools.py:1118-1134`), producing an
  all-failed `sources[]` with `outcome="PARTIAL_SUCCESS"` rather than a tool
  crash.
- **Per-URL upstream errors** inside a TinyFish batch are already per-URL
  records, so a batch may contain a mix of success/timeout/failed sources.

---

## 8. Tool budget: one model call, internal usage folded once

A single `web_research` invocation is **ONE model tool call**: the execution loop
increments `tool_call_count` once and `research_call_count` once for it
(`execution.py:2446-2452`). The internal search + fetch usage is reported by the
executor's `internal_budget` and **folded once** into the run's shared counters
(`execution.py:2448-2461`):

```python
research_call_count += 1
search_call_count   += internal_budget["search_calls"]
fetch_call_count    += internal_budget["fetch_calls"]
external_request_count += internal_budget["external_requests"]
```

The atomic branches (`kind == "search"` / `"fetch"`) increment their counters by
one, so the composite path never double-counts. All counters are persisted on
the Run (`execution.py:2839-2842`) and copied onto the assistant message
(`workspace.py:456-457`).

**Pre-checks before any external call** (`_tool_kind()` at
`execution.py:1441-1448` resolves `web_research -> "research"`; budget gate at
`execution.py:2147-2201`):

| Valve (`config.py`) | Default | Applies to |
| --- | --- | --- |
| `MAX_RESEARCH_CALLS_PER_RUN` | `30` | composite call count (emergency-only; provider quota is the practical throttle) |
| `MAX_SEARCH_CALLS_PER_RUN` | `30` | shared search pool (atomic + composite search) |
| `MAX_FETCH_CALLS_PER_RUN` | `100` | shared fetch pool (atomic + composite per-URL fetches) |
| `MAX_TOTAL_TOOL_CALLS_PER_RUN` | `150` | total model tool calls |

> **V4.2.3.9 (Lab mode):** these per-run budgets are emergency ceilings only.
> The actual throttle is now provider quota — see
> [`V4_2_3_9_PROVIDER_QUOTA_VALVES.md`](V4_2_3_9_PROVIDER_QUOTA_VALVES.md).

For a `research` call the loop checks its own cap first, then requires
`search_remaining > 0` and `fetch_remaining > 0`; the total-call cap is checked
last and applies to every kind. On exhaustion the tool is skipped, a
`TOOL_BUDGET_EXHAUSTED` tool message is recorded, and the run finalizes — **a
composite never bypasses global limits**, and because internal usage is folded
into the shared pools, later atomic calls see the reduced budget. The executor
also receives `research_budget = {search_remaining, fetch_remaining, max_urls}`
so its own selection can be pre-trimmed (`execution.py:2432-2444`).

---

## 9. Trace hierarchy and two distinct tool layers

A composite call produces this span tree
(`_create_internal_operation_spans()` at `execution.py:1507-1559`):

```
TOOL_CALL   web_research                 (span_type=TOOL_CALL)
  ├── SEARCH_OPERATION   <query>         parent_span_id = TOOL_CALL.id
  └── FETCH_OPERATION    <host> (×N)     parent_span_id = TOOL_CALL.id
TOOL_RESULT web_research                 (span_type=TOOL_RESULT)
```

`models.SPAN_TYPES` adds `SEARCH_OPERATION` and `FETCH_OPERATION` additively
(`backend/app/models.py:856-879`); `tracing._normalize_span_type()` accepts them.
Child spans are materialized best-effort from the executor's
`internal_operations`, each with metadata `{provider, internal: True,
tool_call_id, url/final_url, egress_mode, duration_ms, provider_status,
provider_endpoint_host}` (`execution.py:1525-1557`).

**Two layers that must never be conflated** (also documented in
`stream_events.py:9-24`):

| Layer | Identified by | Correlation | Meaning |
| --- | --- | --- | --- |
| **MODEL TOOL CALL** | `TOOL_CALL_*` / `TOOL_RESULT_*` | `provider_tool_call_id`, then `tool_call_id` | A tool the model emitted; a canonical `ToolCall` record. |
| **INTERNAL TOOL OPERATION** | `SEARCH_*` / `FETCH_*` | `internal: True`, `operation_id`, `operation_kind`, parent `provider_tool_call_id` | A sub-step *inside* one composite call. Never a model ToolCall, never in the model-facing tool list. |

`stream_events.TOOL_KINDS` includes `"research"`
(`stream_events.py:139`), and `COMPOSITE_OPERATION_EVENT_TYPES`
(`stream_events.py:145-151`) groups the internal `SEARCH_*`/`FETCH_*` names. The
execution loop's `_operation_sink` projects each internal op live with
`internal=True`, `operation_id`, `operation_kind` and the parent
`provider_tool_call_id` (`execution.py:2352-2407`), while atomic search/fetch
span events are emitted by `_emit_provider_subspan_events()`
(`execution.py:1670-1708`).

Stream **event names stay in the tracing/stream layer**; the Workspace renders
human labels ("Searching web…", "Fetching 3 sources…", "Thought for 1.2s") and
never displays raw event names.

---

## 10. Reasoning segmentation

Reasoning is segmented **one entry per model call**, never merged
(`execution.py:1993-2015`, seeded at `execution.py:296`):

```python
run["reasoning_segments"].append({
    "model_call_id": model_call_id,      # distinct per model call
    "round_index": round_index,
    "phase": "finalization" if finalizing
             else ("initial" if round_index == 0 else "continuation"),
    "content": reasoning_text,
    "duration_ms": <measured>,
})
```

Phases are `initial` (round 0), `continuation` (later tool rounds) and
`finalization` (forced tools-free answer). The list is copied verbatim onto the
persisted assistant message (`workspace.py:461`), with a default of `[]`. The
Workspace timeline keys each thought by `model_call_id` (`thoughtKey()` in
`frontend/src/hooks/useRunStream.ts:125-133`), so streaming thought deltas for
different model calls become distinct timeline rows rather than one blob.

---

## 11. Workspace activity timeline UX

`RunStreamPanel` renders `RunActivityTimeline` inside the "activity panel"; the
final answer is rendered **outside** that panel as a separate card
(`data-testid="run-answer"`, `frontend/src/pages/Workspace.tsx:666-675`). The
timeline is chronological — Thought → Tool → Thought → Tool → … → Final
(`RunActivityTimeline.tsx:442-458`).

Row behaviour (`frontend/src/components/workspace/RunActivityTimeline.tsx`):

- **Completed rows collapse by default**; running rows auto-open. All rows use
  `useAutoOpen(running)` (`RunActivityTimeline.tsx:56-62`).
- **Live spinners**: running rows show an animated `Loader2`; done rows show a
  `Check` (`RunActivityTimeline.tsx:92-96`).
- **Thought rows** (`ThoughtRow`) show "Thinking…" while running, then
  "Thought for <duration>"; historical `ReasoningSegmentRow` rows render the
  persisted segments (`RunActivityTimeline.tsx:120-197`).
- **Search row** (`WebSearchRow`, `testid="run-search"`): "Searching web…" then
  "Searched web · N results · <duration>" with query/provider/result list.
- **Fetch aggregate row** (`WebFetchRow`, `testid="run-fetch"`): "Fetching N
  sources…" then "Fetched N sources · <duration>", with a per-source line.
- **Per-source status** (`SourceRow`, `testid="run-fetch-<i>"`):
  `✓ <host> <duration>` for success, `⚠ <host> timeout` for `timeout`, and
  `✗ <host> failed` for `failed` (`RunActivityTimeline.tsx:203-238`).
- **Research row** (`WebResearchRow`, `testid="run-research"`): "Researching…"
  / "Fetching f/s…" while running, then "Researched the web · fetched f/s
  sources · <duration>", expanding to query, result count and the same
  per-source rows (`RunActivityTimeline.tsx:333-385, 438`).
- **No raw internal event names** appear in the UI; only human labels/hosts.

`useRunStream.runStreamReducer` is a pure projection of the stream protocol and
ignores unknown event types. It builds thought activities per `model_call_id`,
attaches `SEARCH_*`/`FETCH_*` events to the parent tool by `toolKey()`, and for
`web_research` parses the composite result into `query`, `searchResults`,
`sources` and `summary` via `parseResearchResult()` (`useRunStream.ts:240-259,
459-461`). `FetchSourceStatus` distinguishes `running | success | failed |
timeout`.

---

## 12. TinyFish Web grouping (shared config, distinct tool identities)

`ProvidersPanel` renders one **TinyFish Web** group
(`TinyFishGroup`, `frontend/src/components/tools/ProvidersPanel.tsx:348-494`)
that presents the TinyFish search and fetch providers together:

- **One shared API key entry**: saving the key calls both
  `updateSearchProvider` and `updateFetchProvider`; the secret is stored
  server-side and never displayed.
- **Shared configuration surface**: search valves (`location`, `language`,
  `max_results`) and fetch valves (`format`, `timeout_seconds`) are shown
  read-only per tile; a `Batch fetch supported` badge is shown when either
  provider advertises `supports_batch_fetch`.
- **Distinct internal tool identities**: `web_search`, `web_fetch` and
  `web_research` stay separate model tools with separate permissions; grouping
  is a UI/config convenience only. The hint text states one TinyFish
  configuration powers Web Search, Web Fetch and Web Research.

---

## 13. Config / env additions

Added in `backend/app/config.py:159-164`:

| Var | Default | Meaning |
| --- | --- | --- |
| `MAX_BATCH_FETCH_URLS` | `5` | protected **provider execution batch** ceiling (also the `max_urls` fetch-valve ceiling); not research breadth |
| `FETCH_BATCH_CONCURRENCY` | `2` | max in-flight per-URL requests in a Standard batch fetch |
| `MAX_RESEARCH_CALLS_PER_RUN` | `30` | max composite `web_research` calls per model Run (emergency-only ceiling) |

These rows are also added to the README environment-variables table.

---

## 14. Files touched and tests

Backend:

- `backend/app/services/builtin_tools.py` — composite tool definition +
  `_select_research_sources` / `_normalize_research_url` / `_host_of` /
  `run_research`.
- `backend/app/services/fetch_providers.py` — `SUPPORTS_BATCH_FETCH`,
  `supports_batch_fetch`, base/Standard/TinyFish `fetch_many`,
  `_per_url_failure`, `public_fetch_provider` batch flag.
- `backend/app/services/execution.py` — `_tool_kind("research")`, budget
  pre-checks, `research_budget`/`_operation_sink` threading, internal-budget
  fold-in, `_create_internal_operation_spans`, `reasoning_segments`.
- `backend/app/services/stream_events.py` — `"research"` tool kind and
  `COMPOSITE_OPERATION_EVENT_TYPES`.
- `backend/app/models.py` — `SEARCH_OPERATION` / `FETCH_OPERATION` span types.
- `backend/app/config.py` — the three V4.2.3.8 limits.
- `backend/app/services/valves.py` — `web_research -> search` valve routing,
  `max_urls` valve with `MAX_BATCH_FETCH_URLS` ceiling.
- `backend/app/routers/workspace.py` — composite auto-wiring and
  assistant-message passthrough of `research_call_count` /
  `external_request_count` / `reasoning_segments`.

Frontend:

- `frontend/src/components/workspace/RunActivityTimeline.tsx` — thought /
  search / fetch / research rows, per-source status, `ReasoningSegmentList`.
- `frontend/src/components/workspace/RunStreamPanel.tsx` — activity panel shell.
- `frontend/src/hooks/useRunStream.ts` — `runStreamReducer`,
  `parseResearchResult`, source/thought state.
- `frontend/src/components/tools/ProvidersPanel.tsx` — TinyFish Web group.
- `frontend/src/pages/Workspace.tsx` — timeline inside the panel; final answer
  rendered outside it.
- `frontend/src/lib/types.ts` — `ReasoningSegment` on run/message shapes.

Backend tests (`backend/tests/`, all offline):

- `test_v4238_web_research.py` — composite definition, selection algorithm,
  `MAX_BATCH_FETCH_URLS` ceiling, partial-failure/summary/evidence contract,
  `operation_sink` ordering, atomic tools unchanged.
- `test_v4238_batch_fetch.py` — one TinyFish POST per batch, clamping,
  per-URL `errors[]`, `supports_batch_fetch` advertisement, order preservation.
- `test_v4238_execution_research.py` — `kind="research"`, internal
  `SEARCH_*`/`FETCH_*` events, internal-budget fold-in, child spans,
  reasoning segmentation, budget exhaustion skip.
- `test_v4238_research_e2e.py` — real execution loop + real
  `builtin:web_research`, mocking only the provider boundary; one model
  ToolCall, one batch fetch, partial failure, counters, reasoning segments.
- `test_v4238_workspace_research.py` — `_tool_ids_for` composite gating,
  enabled-id preservation, `_assistant_message` passthrough.
- `test_v4238_stream_contract.py` — `research` tool kind, span types,
  `COMPOSITE_OPERATION_EVENT_TYPES`, internal-op payload shape.
- `test_v4238_research_valves.py` — valve routing to the search provider and
  the protected `max_urls` valve.

Frontend tests (`frontend/src/__tests__/`):

- `webResearchTimeline.test.tsx` — Thought → `web_research` → Thought → answer
  rendered by `RunStreamPanel`.
- `webResearchTools.test.tsx` — TinyFish group, shared key, batch badge,
  `ReasoningSegmentList`.
- `workspaceRunStream.test.tsx` — reducer mapping of `web_research` result to
  search results / sources / summary.

### Orchestration efficiency (deterministic offline simulation)

The efficiency claim is a **deterministic offline orchestration simulation**,
not measured production data. Counting model tool-call round-trips: the atomic
path needs one search call plus N fetch calls (N+1 model tool calls and extra
model continuations), whereas the composite path is a single model tool call
whose internal search + fetches are non-model operations. A **live provider run
is still required** to quantify latency, token and answer-quality deltas; the
`test_reports/v4238_research_efficiency.md` artifact is **not present in this
tree** (and `tests/test_v4238_research_efficiency.py` is not present either).
When produced, that artifact is generated by `tests/test_v4238_research_efficiency.py`.

---

## 15. Deferred / remaining debt

- **Ranking Lab not built.** Selection uses the provider's `rank` plus a fixed
  domain-diversity/fill heuristic (`_select_research_sources`); there is no
  learned/relevance ranking or embedding-based reranker.
- **Live benchmark pending.** The efficiency comparison is an offline
  orchestration simulation; no live TinyFish/Standard measurement is recorded in
  the repo, and no `test_reports/v4238_research_efficiency.md` exists yet.
- **Persisted timeline only segments reasoning.** The persisted assistant
  message carries `reasoning_segments` (model thoughts) plus aggregate counters,
  but **not** the full tool/operation timeline; the rich Thought/Tool timeline
  (search row, fetch aggregate, per-source status) is a live-stream projection
  and is not reconstructed from storage on reload.
- **`SEARCH_OPERATION` label quirk.** The internal search operation's human
  `name` currently hardcodes the `"TinyFish search: {query}"` prefix
  (`builtin_tools.py:990`) even for non-TinyFish search adapters.
- Batch-fetch concurrency/caps are server-side valves only; there is no
  per-project batching UI beyond the read-only TinyFish group surfaces.

---

## Files

- `docs/V4_2_3_8_WEB_RESEARCH.md` — this document.
- `docs/V4_2_3_SEARCH_FETCH.md`, `docs/V4_2_3_7_AGENTIC_STREAMING.md` — the
  underlying atomic-tool and stream-protocol contracts this builds on.
