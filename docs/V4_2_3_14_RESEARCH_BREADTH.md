# OmniLab V4.2.3.14 — Research Breadth + Provider-Aware Fetch Closure

Status: implemented. This release closes the research-breadth defect that
produced the repeated live `max_results=5 max_fetches=3` and makes the fetch
layer provider-aware: **logical research breadth**, **provider execution
batching** and **provider quota** are now three separate concepts that are never
conflated.

This document is the canonical contract for research breadth. It supersedes the
breadth statements in [`V4_2_3_8_WEB_RESEARCH.md`](V4_2_3_8_WEB_RESEARCH.md)
(which is retained for historical context). The release is additive and
preserves every V4.2.3.7 / V4.2.3.9 / V4.2.3.11 / V4.2.3.12 / V4.2.3.13
guarantee.

---

## 1. Root cause

The old behavior conflated research breadth with provider batch capacity:

- `web_research` defaults were `max_results=5` / `max_fetches=3` in **both** the
  canonical model-visible schema (`backend/app/services/builtin_tools.py`, tool
  id `tool-research-0001`) and the `run_research` executor fallback.
- `run_research` clamped logical fetch breadth to `config.MAX_BATCH_FETCH_URLS`
  (default `5`), treating a provider execution limit as the research ceiling.

The model-visible default of `3` (with an effective cap of `5`) is exactly what
produced the repeated live `max_results=5 max_fetches=3`. No code path mentions
a default of `3` after this release.

---

## 2. Canonical research defaults

| Field | Default | Min | Max |
| --- | --- | --- | --- |
| `max_results` | `5` | `1` | `10` |
| `max_fetches` | `5` | `1` | `10` |

These are the model-visible schema values for `tool-research-0001` and the
`run_research` fallbacks. `10` is the hard logical ceiling because
`web_research` cannot return more than 10 search results today.

---

## 3. Research valve namespace

`web_research` owns **tool-local** valves via
`research_policy_schema()` in `backend/app/services/valves.py`, alongside its
provider routing (`TOOL_VALVE_NAMES` maps `web_research -> search`):

| Valve | Default | Min | Hard max |
| --- | --- | --- | --- |
| `research_default_max_results` | `5` | `1` | `10` |
| `research_default_max_fetches` | `5` | `1` | `10` |
| `research_max_results_ceiling` | `10` | `1` | `10` |
| `research_max_fetches_ceiling` | `10` | `1` | `10` |

These valves are **distinct** from:

- **provider quota valves** (e.g. TinyFish `fetch_urls_per_minute_limit` /
  `fetch_urls_per_day_limit`) — see
  [`V4_2_3_9_PROVIDER_QUOTA_VALVES.md`](V4_2_3_9_PROVIDER_QUOTA_VALVES.md); and
- **provider batch capacity** (`max_urls` / `fetch_provider_batch_size`) — the
  execution batch size, detailed in sections 6 and 7.

The hard logical ceiling (`10`) is enforced server-side; a project or global
value can never widen it.

---

## 4. Resolution hierarchy and run snapshot

Research policy resolves in a strict order:

```
Global (project_id = None)  ->  Project override  ->  immutable Run snapshot
```

The snapshot is written to `run["research_policy"]` (also surfaced at
`run["effective_tool_config"]["research"]`) and is computed once per run; later
consumers never recompute it. The reported `source` is:

| `source` | Condition |
| --- | --- |
| `PROJECT` | a project tool-valve row carries values |
| `GLOBAL` | no project values, but a global tool-valve row carries values |
| `REQUEST_DEFAULT` | neither row carries values (server defaults apply) |

The snapshot carries `default_max_results`, `default_max_fetches`,
`max_results_ceiling`, `max_fetches_ceiling`, `source` and a `config_hash`, and
contains no secret material.

---

## 5. Effective breadth formula

For one `web_research` operation:

```
requested_or_default_results = model max_results OR research_default_max_results
requested_or_default_fetches = model max_fetches OR research_default_max_fetches

effective_max_results = min(
    requested_or_default_results,
    research_max_results_ceiling,
    10,                          # hard logical ceiling
    remaining run search budget,
)

effective_max_fetches = min(
    requested_or_default_fetches,
    research_max_fetches_ceiling,
    available candidate URLs,
    remaining run fetch budget,
)
```

A model may request up to the ceiling; the effective value is the smallest of
the request/default, the configured ceiling, the hard limit and the remaining
per-run budget. The run budgets remain emergency ceilings only (see V4.2.3.9).

---

## 6. Logical breadth vs provider batching

**Logical breadth** is what one `web_research` operation intends to inspect
(1..10 URLs). **Provider batching** is how a fetch provider physically transports
those URLs. They are independent:

- `web_research` may logically request up to 10 fetch URLs.
- A fetch provider processes them in one or more outbound requests, each bounded
  by the provider batch cap.
- Example: logical breadth `10` with `provider_batch_cap = 5` produces **two**
  outbound provider requests. The model still sees **one** `web_research`
  operation that inspected 10 URLs.

Provider fan-out never increments `tool_call_count`: one `web_research` is still
one model tool call.

---

## 7. Provider execution batch cap

`BaseFetchProvider.batch_cap` (`backend/app/services/fetch_providers.py`):

1. reads the resolved provider configuration `max_urls` (or
   `fetch_provider_batch_size`), and
2. clamps it to the protected server ceiling `config.MAX_BATCH_FETCH_URLS`
   (default `5`).

The fetch valve `max_urls` is relabelled **"Batch size (provider batch cap)"**.
It is provider **execution** batching, not research breadth. A missing or
invalid value falls back to the server ceiling; the value is never `< 1`.

`StandardFetchProvider` and `TinyFishFetchProvider` both honor the resolved
batch configuration and neither can bypass the server ceiling:
`config.MAX_BATCH_FETCH_URLS` is a hard protection, not a recommendation.

---

## 8. TinyFish batch-cap audit

An audit for an authoritative TinyFish per-request batch maximum found:

- **No authoritative per-request batch maximum** is established by code,
  comments, docs, or tests.
- The documented TinyFish rate quota (150 URLs/min, 1000 URLs/day) is
  **URL-unit based**; it is **not** a per-request batch cap.
- **LIVE BATCH TEST NOT EXECUTED** — `TINYFISH_API_KEY` was not set in the
  environment (outbound network is reachable but auth-gated). No live claim about
  TinyFish's real batch maximum is made.

Recommended conservative execution default remains `5`, with automatic chunking
for larger logical breadth. Because no authoritative per-request maximum is
known, the server ceiling (`MAX_BATCH_FETCH_URLS`, default `5`) continues to
bound each provider request.

---

## 9. Provider quota behavior

`ProviderQuotaManager` remains **authoritative at outbound execution**. It is
unchanged and is not mixed into research breadth settings.

- Fetch quota counts **URL units**, not HTTP batches:
  - 1 batch of 10 URLs consumes 10 units;
  - 2 batches of 5 consume 10 units;
  - cached/reused results consume 0.
- Quota exhaustion returns the canonical **`PROVIDER_RATE_LIMITED`**, never
  `TOOL_BUDGET_EXHAUSTED`.
- There is **no pre-emptive per-research throttle**; quota is enforced at the
  adapter boundary when a request is actually made.

---

## 10. Builtin schema migration

`ensure_builtin_tools()` now migrates stale persisted builtin documents instead
of leaving them at an old schema. Migration updates **only** canonical fields:

`name`, `display_name`, `tool_type`, `implementation`, `permissions`,
`risk_level`, `tags`, `input_schema`, `output_schema`, `version`

plus a `builtin_schema_revision` marker. It **preserves**:

`description` overrides, `enabled`, `scope`, `project_id`, `owner_id`,
`created_at`, and custom metadata.

Migration is deterministic and idempotent: a document already matching the
canonical definition plus the current revision is left untouched. After restart,
a database seeded with the old default `3` / max `5` exposes the new canonical
schema (default `5`, max `10`).

---

## 11. Project override behavior

Per-project research policy is stored in the existing tool valve rows with
`target_type = "tool"`, `target_id = "tool-research-0001"`, and `project_id` set.
Resolution merges the global row (`project_id = None`) then the project row.

Example:

| Project | `research_default_max_results` | `research_default_max_fetches` | `research_max_results_ceiling` | `research_max_fetches_ceiling` |
| --- | --- | --- | --- | --- |
| Project A | `5` | `5` | `10` | `10` |
| Project B | `8` | `8` | `10` | `10` |

Workspace execution uses the **selected project's** resolved configuration and
snapshots it into the Run, so the effective values are immutable per run.

---

## 12. Observability

`run_research` returns a `"research"` telemetry block that is persisted with the
`ToolCall`:

| Field | Meaning |
| --- | --- |
| `requested_max_results` / `requested_max_fetches` | what the model explicitly asked for |
| `default_max_results` / `default_max_fetches` | resolved research policy defaults |
| `configured_max_results` / `configured_max_fetches` | resolved policy ceilings |
| `effective_max_results` / `effective_max_fetches` | final clamped breadth actually used |
| `candidate_results_returned` | normalized search results returned |
| `fetch_urls_selected` / `fetch_urls_attempted` / `fetch_urls_succeeded` / `fetch_urls_failed` | URL-selection and fetch outcome counts |
| `fetch_provider` | resolved fetch provider id |
| `provider_batch_cap` | provider execution batch size for this run |
| `provider_batch_count` | number of outbound provider batch requests |
| `provider_quota_units_consumed` | URL-unit quota consumed (0 for cache/reuse) |
| `remaining_search_budget` / `remaining_fetch_budget` | remaining per-run emergency budget |
| `research_config_source` | `PROJECT` \| `GLOBAL` \| `REQUEST_DEFAULT` |

Unavailable quota telemetry is **not fabricated**: a field is omitted/`null`
rather than guessed.

---

## 13. UI

- **Tool -> Web Research -> Configure** exposes **Default search results** and
  **Default fetch URLs**, plus an **Advanced** group with **Maximum search
  results** and **Maximum fetch URLs**. Descriptions explain the difference
  between a default and a maximum.
- **Provider -> TinyFish Fetch** shows the **batch size / provider batch cap**
  and the **150 URLs/min, 1000 URLs/day** fetch quota.
- Provider quota is **not** mixed into Web Research breadth settings; the two
  surfaces remain separate.

---

## 14. Security invariants (unchanged)

Every prior security boundary is preserved: the REMOTE vs LOCAL egress trust
boundary, SSRF protection, localhost/private/metadata blocking, credential
isolation, the permission boundary, sandbox isolation, secret encryption and
trace redaction. TinyFish target URLs remain `REMOTE_EGRESS`, and the TinyFish
endpoint is fully validated before any request.

---

## 15. Regression gate (preserved)

No regression to earlier releases:

- **V4.2.3.7** streaming protocol.
- **V4.2.3.11** final-answer reliability.
- **V4.2.3.12** research budgets / watchdog.
- **V4.2.3.13** finalization token budget / token closure.

Unchanged: `MAX_TOOL_ROUNDS`, no-progress protection, finalization attempt count
(`MAX_FINALIZATION_ATTEMPTS = 2`), watchdog timeout defaults, and all provider
quota limits.

---

## 16. Verification / acceptance

### Deterministic (offline) coverage

The change is covered by offline tests that exercise the builtin schema,
research policy resolution, provider batch caps and quota accounting with the
provider boundary mocked (no network). Relevant suites under `backend/tests/`:

- `test_v4238_web_research.py` — canonical `web_research` schema/defaults and the
  deterministic URL-selection / evidence contract.
- `test_v4238_research_valves.py` — tool-local research valve schema and the
  protected `max_urls` provider-batch valve.
- `test_v4238_batch_fetch.py` — provider batch capping and per-URL results.
- `test_v4238_execution_research.py` — research execution, internal-budget
  fold-in and per-run budget trimming.
- `test_v42312_web_research_lab_mode.py` — provider quota accounting (URL units,
  no double counting, cache/reuse free) and budget vs quota error semantics.
- `test_v42311_final_answer_reliability.py`,
  `test_v42313_finalization_token_budget.py` — finalization / token-closure
  regression gate.

These suites are deterministic, idempotent and offline.

### Live acceptance NOT executed

The following live acceptances were **not** run, and this document makes no
claim about them:

- **TinyFish live batch-cap test — NOT EXECUTED.** `TINYFISH_API_KEY` was not set
  in the environment; outbound network is reachable but auth-gated. No
  authoritative TinyFish per-request batch maximum could be established live.
- **No live provider research run** (real TinyFish search/fetch) is recorded for
  this release.
- **No live UI acceptance** against real providers is recorded.

Where a value could not be established from code, docs or tests, the release
keeps the conservative server ceiling (`MAX_BATCH_FETCH_URLS`, default `5`) and
does not fabricate a provider limit.

---

## Files

- `docs/V4_2_3_14_RESEARCH_BREADTH.md` — this document (canonical contract).
- `docs/V4_2_3_8_WEB_RESEARCH.md` — historical composite-tool contract, now
  carrying a "Superseded by V4.2.3.14" note with corrected values.
- [`V4_2_3_9_PROVIDER_QUOTA_VALVES.md`](V4_2_3_9_PROVIDER_QUOTA_VALVES.md) —
  provider quota valves and URL-unit quota semantics.
- `backend/app/services/valves.py` — `research_policy_schema` and provider fetch
  valve schemas.
- `backend/app/services/tool_valves.py` — research policy resolution and run
  snapshot.
- `backend/app/services/builtin_tools.py` — `web_research` schema and
  `run_research` executor.
- `backend/app/services/fetch_providers.py` — `batch_cap` and provider batch
  execution.
- `backend/app/services/tools.py` — `ensure_builtin_tools()` migration.
