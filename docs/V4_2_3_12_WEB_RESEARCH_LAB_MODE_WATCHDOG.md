# OmniLab V4.2.3.12 — Web-Research Lab Mode + Model-Call Watchdog Closure

Status: implemented. This release is strictly additive: it does **not** redesign
the canonical execution loop (`backend/app/services/execution.py`) or the
V4.2.3.11 finalization behavior. It removes one legacy throttle and adds a
model-call watchdog around provider streaming.

---

## 1. Root cause: the legacy research ceiling

`MAX_RESEARCH_CALLS_PER_RUN` was `3`. The canonical loop enforces it *before*
any external call (`budget_hit = "research_budget"`), so the 4th composite
`web_research` tool call in a run returned `TOOL_BUDGET_EXHAUSTED` even though
the provider account still had quota. That legacy throttle contradicted the
V4.2.3.9 Lab-mode model, where **provider quota is the primary practical
throttle**.

| | Old | New |
| --- | --- | --- |
| `MAX_RESEARCH_CALLS_PER_RUN` | `3` | `30` |
| Role | practical throttle | emergency-only circuit breaker |

The env override name is unchanged (`MAX_RESEARCH_CALLS_PER_RUN`). Enforcement
logic in the execution loop is untouched — only the configured value changed.
There is intentionally **no** separate "research RPM": provider quota remains
the throttle.

---

## 2. Tool budget vs provider quota (they are different layers)

Two independent accounting layers must never be conflated:

| Layer | Counts | Where |
| --- | --- | --- |
| **Model-visible tool budget** | tool invocations requested by the model. One `web_research` = **1** tool call. Provider fan-out never increments `tool_call_count`. | `execution.py` loop |
| **Provider quota** | actual outbound provider resource usage. One `web_research` = **1 search + N fetched URLs**. | adapter boundary only |

Provider quota is consumed at exactly one place per outcome:

* `search_providers.py` — `consume_search(..., 1)` for one search request.
* `fetch_providers.py` — `consume_fetch(..., 1)` for a single fetch;
  `consume_fetch(..., len(valid))` for a batch.
* `builtin_tools.py` never calls `consume_*`; `web_research` fans out to the
  search and fetch adapters, so quota is `search:1 + fetch:N` with **no double
  counting**.
* Run-local **cache/reuse** (dedup of search queries and fetch URLs) short
  circuits before the tool runs, so a reused result consumes **zero** provider
  quota.
* `StandardFetchProvider` is LOCAL and consumes **no** TinyFish quota.

These invariants are proven by `backend/tests/test_v42312_web_research_lab_mode.py`.

---

## 3. Error semantics (all codes stay distinct)

| Code | Category | Meaning |
| --- | --- | --- |
| `TOOL_BUDGET_EXHAUSTED` | `VALIDATION` | a per-run emergency tool ceiling was hit (`research_budget`, `search_budget`, `fetch_budget`, `total_budget`) |
| `PROVIDER_RATE_LIMITED` | `RATE_LIMIT` | provider/account quota window exceeded at the adapter boundary |
| `SEARCH_RATE_LIMITED` / `FETCH_RATE_LIMITED` | `RATE_LIMIT` | provider-specific search / fetch quota blocks |
| `TOOL_LOOP_NO_PROGRESS` | `VALIDATION` | repeated/identical call produced no new evidence (`exact_repeat` / `consecutive_no_progress`) |
| `TOOL_LOOP_LIMIT_REACHED` | `VALIDATION` | the model exhausted the maximum tool rounds |
| `FINAL_ANSWER_EMPTY` | `VALIDATION` | forced finalization could not produce a visible final answer |
| `MODEL_CONNECTION_TIMEOUT` | `TIMEOUT` | watchdog: provider did not start a usable response |
| `MODEL_IDLE_TIMEOUT` | `TIMEOUT` | watchdog: stream started but stalled |
| `MODEL_CALL_MAX_DURATION` | `TIMEOUT` | watchdog: emergency whole-call ceiling reached |

A provider quota block is **not** a tool-budget exhaustion, and vice versa.

---

## 4. Model-call watchdog (V4.2.3.12)

### 4.1 Module

`backend/app/services/model_watchdog.py` is pure: stdlib + `httpx` +
`app.services.stream_events` only. It deliberately does **not** import the
execution engine, so there is no import cycle.

`watch_model_stream(agen, *, connection_timeout, idle_timeout, max_duration,
clock=time.monotonic)` is an async generator that wraps a provider event stream.
Before every `__anext__` it computes the earliest applicable deadline:

* **connection** — only while no event has arrived yet (`start + connection_timeout`).
* **idle** — `last_progress + idle_timeout`; **every yielded event resets it**.
* **max_duration** — `start + max_duration` (always applies).

`__anext__` is awaited through `asyncio.wait_for`. A timeout (or a real
`httpx.TimeoutException` from the underlying stream) is re-derived onto the same
canonical kinds, so an adapter read-timeout still maps to
`MODEL_CONNECTION_TIMEOUT` / `MODEL_IDLE_TIMEOUT`, never a generic timeout.

`ModelCallTimeout` carries `kind`, `elapsed_ms`, `last_progress_age_ms`,
`had_reasoning`, `had_content`, `had_tool_call_delta`, the three configured
timeouts, an `error_code` property and a safe `message`.

### 4.2 Configuration (server-side only)

| Env var | Default | Meaning |
| --- | --- | --- |
| `MODEL_CONNECTION_TIMEOUT_SECONDS` | `60` | provider did not START a usable response |
| `MODEL_IDLE_TIMEOUT_SECONDS` | `120` | stream started but no meaningful event arrived |
| `MODEL_CALL_MAX_DURATION_SECONDS` | `600` | emergency wall-clock ceiling for one call |

These are read once per model call via a clamped `_cfg_seconds` helper. They are
**server-side config only**: no LLM-provider Valve schema exists, so they are not
provider/project valves. They are distinct from
`PROVIDER_CONNECTION_TIMEOUT_SECONDS` (the adapter-owned httpx ceiling).

### 4.3 Stream-progress definition

"Progress" means a streamed event of interest was yielded:

* reasoning: `MODEL_REASONING_DELTA` or a non-empty `reasoning_delta`.
* content: `MODEL_OUTPUT_DELTA` with a non-empty `delta` and `done != True`.
* tool call: `MODEL_TOOL_CALL_DELTA` (raw argument fragment).

### 4.4 Stream cancellation / no zombie tasks

The wrapper's `finally` always closes the underlying stream with `aclose()`,
swallowing every error. `asyncio.wait_for` cancels the in-flight `__anext__` on
timeout, and `GeneratorExit` is handled cleanly. No background tasks, supervisor
or worker is spawned — a timed-out call never leaves a provider generator
running. Cancellation (`cancel_event`) always wins: if it is set when a watchdog
timeout fires, the run raises `_RunCancelled` and emits exactly one
`RUN_CANCELLED` terminal event (never a competing `MODEL_CALL_TIMEOUT` terminal).

---

## 5. Timeout → recovery semantics

On timeout the run records a structured `TIMEOUT` error and emits a
non-terminal `MODEL_CALL_TIMEOUT` event:

* **usable evidence + continuation round (`round_index > 0`) + not already
  finalizing** → `recovery_action = "forced_finalization"`. The normal loop
  handling sees `status != "success"` and, exactly as in V4.2.3.11, sets
  `finalization_reason = "provider_error"` and runs the bounded tools-free
  finalization. A visible final answer makes the run `success`.
* **otherwise** → `recovery_action = "terminal_error"`: the run ends as a
  structured error whose `code` is the `MODEL_*` timeout code.

Incomplete tool-call fragments are never assembled or executed when a timeout
propagates: the exception is raised before fragment assembly.

---

## 6. Trace / telemetry fields

`MODEL_CALL_TIMEOUT` carries (secret-free): `run_id`, `trace_id`, `round_index`,
`model_call_id`, `provider`, `model`, `connection_timeout_seconds`,
`idle_timeout_seconds`, `max_duration_seconds`, `elapsed_ms`,
`last_progress_age_ms`, `timeout_kind`, `had_reasoning`, `had_content`,
`had_tool_call_delta`, `usable_evidence`, `recovery_action`.

The Run also gains additive telemetry: `model_call_timeouts` (a list of the
above, minus correlation ids) and `model_call_timeout_count`. The
`MODEL_CALL_TIMEOUT` type is part of `MODEL_EVENT_TYPES` / `ALL_EVENT_TYPES`
(the terminal set is unchanged).

---

## 7. Preserved V4.2.3.11 guarantees

* `_classify_turn`, the finalization block, `_finalize` semantics,
  `MAX_FINALIZATION_ATTEMPTS` and the `_FINALIZATION_*` instructions are
  unchanged.
* A provider/quota/watchdog failure after usable evidence still degrades to
  forced finalization; without evidence it stays a terminal provider error.
* Exactly one terminal lifecycle event is emitted per run
  (`RUN_COMPLETED` / `RUN_FAILED` / `RUN_CANCELLED`).

---

## 8. Tests

* `backend/tests/test_v42312_web_research_lab_mode.py` — ceiling default, long
  (15-round) research runs, call #4 not throttled, lowered emergency ceiling,
  provider quota = 1 search + N fetches (no double counting), cache/reuse free,
  StandardFetch free, `PROVIDER_RATE_LIMITED` vs `TOOL_BUDGET_EXHAUSTED`,
  no-progress, total-tool ceiling, V4.2.3.11 finalization.
* `backend/tests/test_v42312_model_watchdog.py` — connection/idle/max kinds,
  idle reset on reasoning/output/tool-call progress, incomplete fragments never
  execute, timeout→forced-finalization vs terminal error, stream close,
  cancellation single terminal, single terminal on timeout, event fields/secrets.
