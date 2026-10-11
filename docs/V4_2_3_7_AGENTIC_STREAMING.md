# V4.2.3.7 Agentic Streaming + Fetch Resilience

This document defines the canonical agentic streaming event protocol introduced
in OmniLab V4.2.3.7. The protocol is implemented in
`backend/app/services/stream_events.py`; the persisted execution record produced
by `backend/app/services/execution.py` (`execute_model`) remains authoritative.

---

## 1. Principle: the stream is a live projection, never a second execution path

The stream is an **ephemeral live projection of the canonical execution loop**.
There is exactly one execution path — `execute_model()` in
`backend/app/services/execution.py` — and the streamed events are emitted from
the same loop as it runs. The stream does not execute models or tools, does not
decide budgets, and does not alter results.

Consequences:

- **Execution is authoritative.** The persisted run/ToolCall documents are the
  source of truth. Events are a best-effort, real-time view; a dropped or
  reordered frame never changes what actually executed.
- **No separate path.** Any future SSE router (workspace) and the frontend
  consume the same `stream_events` constants and `event()` builder — they must
  not invent their own event names or a parallel execution flow.
- **Single source of truth for names/construction.** `stream_events.py` owns the
  event type names, the correlation keys and the `event()` / `sse_frame()` /
  `tool_result_payload()` helpers. The module is stdlib-only so it can be
  imported by the execution loop and the router without import cycles.

---

## 2. Event types and fields

All events are plain JSON-serializable dicts built by
`stream_events.event(event_type, **correlation, **payload)`. The `type` field is
always present. Every field below other than `type` is optional and is only
emitted when it has a non-`None` value.

Correlation keys (see section 4):

| Key | Meaning |
| --- | --- |
| `run_id` | Run record id (`execute_model` run). |
| `trace_id` | Trace id shared by every span in the run. |
| `conversation_id` | Conversation/message thread id. |
| `round_index` | Agentic loop round (0-based) the event belongs to. |
| `model_call_id` | Id of the `MODEL_CALL` span for the round. |
| `tool_call_id` | Internal canonical `ToolCall` record id. |
| `provider_tool_call_id` | Provider/LLM `call.id` for the tool call. |

Event catalogue (`backend/app/services/stream_events.py`):

| Event type | Group / phase | Payload fields | Correlation |
| --- | --- | --- | --- |
| `RUN_STARTED` | Lifecycle | — | `run_id`, `trace_id`, `conversation_id` |
| `RUN_COMPLETED` | Lifecycle (terminal) | final run summary fields | `run_id`, `trace_id`, `conversation_id` |
| `RUN_FAILED` | Lifecycle (terminal) | `error` | `run_id`, `trace_id`, `conversation_id` |
| `RUN_CANCELLED` | Lifecycle (terminal) | — | `run_id`, `trace_id`, `conversation_id` |
| `MODEL_STARTED` | Model | `model_id` (optional) | `run_id`, `trace_id`, `round_index`, `model_call_id` |
| `MODEL_REASONING_DELTA` | Model | `delta` | `run_id`, `trace_id`, `round_index`, `model_call_id` |
| `MODEL_OUTPUT_DELTA` | Model | `delta` | `run_id`, `trace_id`, `round_index`, `model_call_id` |
| `MODEL_COMPLETED` | Model | `finish_reason`, usage fields | `run_id`, `trace_id`, `round_index`, `model_call_id` |
| `TOOL_CALL_STARTED` | Tool call | `tool_name` | `run_id`, `trace_id`, `round_index`, `model_call_id`, `provider_tool_call_id` |
| `TOOL_CALL_DELTA` | Tool call | `delta` (raw argument fragment) | `run_id`, `round_index`, `model_call_id`, `provider_tool_call_id` |
| `TOOL_CALL_ARGUMENTS` | Tool call | `arguments` (parsed) | `run_id`, `round_index`, `model_call_id`, `provider_tool_call_id` |
| `TOOL_CALL_COMPLETED` | Tool call | `tool_name`, `arguments` | `run_id`, `round_index`, `model_call_id`, `provider_tool_call_id` |
| `TOOL_RESULT_STARTED` | Tool result | `tool_name`, `kind` | `run_id`, `trace_id`, `round_index`, `tool_call_id`, `provider_tool_call_id` |
| `TOOL_RESULT_COMPLETED` | Tool result | `result` (structured success payload) | `run_id`, `trace_id`, `round_index`, `tool_call_id`, `provider_tool_call_id` |
| `TOOL_RESULT_FAILED` | Tool result | `error` (`code`, `message`, optionals) | `run_id`, `trace_id`, `round_index`, `tool_call_id`, `provider_tool_call_id` |
| `SEARCH_STARTED` | Search/Fetch | `query`, `provider` | `run_id`, `trace_id`, `round_index`, `tool_call_id` |
| `SEARCH_COMPLETED` | Search/Fetch | `query`, `count`, `provider` | `run_id`, `trace_id`, `round_index`, `tool_call_id` |
| `FETCH_STARTED` | Search/Fetch | `url`, `provider`, `egress_mode` | `run_id`, `trace_id`, `round_index`, `tool_call_id` |
| `FETCH_COMPLETED` | Search/Fetch | `url`, `final_url`, `provider` | `run_id`, `trace_id`, `round_index`, `tool_call_id` |
| `FETCH_FAILED` | Search/Fetch | `error` (`code`, `message`, `url`, `timeout_seconds`, `provider`) | `run_id`, `trace_id`, `round_index`, `tool_call_id` |
| `FINALIZATION_STARTED` | Finalization | `finalization_reason` | `run_id`, `trace_id`, `round_index`, `model_call_id` |
| `FINALIZATION_DELTA` | Finalization | `delta` | `run_id`, `trace_id`, `round_index`, `model_call_id` |
| `FINALIZATION_COMPLETED` | Finalization | `model_call_id` | `run_id`, `trace_id`, `round_index`, `model_call_id` |

Terminal event types end the stream:

```python
TERMINAL_EVENT_TYPES = frozenset({RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED})
is_terminal(event) -> bool
```

---

## 3. Ordered happy-path event sequence (agentic run with tools)

A successful run that performs one search, one fetch, then a normal answer:

```text
RUN_STARTED
  MODEL_STARTED
  MODEL_OUTPUT_DELTA            (… repeated)
  TOOL_CALL_STARTED
  TOOL_CALL_DELTA               (… repeated)
  TOOL_CALL_ARGUMENTS
  TOOL_CALL_COMPLETED
  MODEL_COMPLETED
  TOOL_RESULT_STARTED
    SEARCH_STARTED
    SEARCH_COMPLETED
  TOOL_RESULT_COMPLETED
  MODEL_STARTED
  MODEL_OUTPUT_DELTA            (… repeated)
  TOOL_CALL_STARTED             (fetch tool call)
  TOOL_CALL_ARGUMENTS
  TOOL_CALL_COMPLETED
  MODEL_COMPLETED
  TOOL_RESULT_STARTED
    FETCH_STARTED
    FETCH_COMPLETED
  TOOL_RESULT_COMPLETED
  MODEL_STARTED
  MODEL_OUTPUT_DELTA            (… repeated)
  MODEL_COMPLETED
RUN_COMPLETED
```

When the loop forces a single tools-free final answer, the final rounds are
wrapped by `FINALIZATION_STARTED` / `FINALIZATION_DELTA` /
`FINALIZATION_COMPLETED` before `RUN_COMPLETED` (matching the
`forced_finalization` / `finalization_reason` fields in `execution.py`).
`tool_call_id` is only known once the canonical `ToolCall` document exists, so
`TOOL_CALL_*` events carry `provider_tool_call_id` while `TOOL_RESULT_*` events
carry both ids.

---

## 4. Trace correlation

| Field | Value |
| --- | --- |
| `run_id` | The run record produced by `execute_model` (also present on every `ToolCall`). |
| `trace_id` | Trace id shared by all spans of the run (see `backend/app/services/tracing.py`). |
| `conversation_id` | Conversation/message thread the run belongs to. |
| `round_index` | 0-based agentic loop round. |
| `model_call_id` | The `MODEL_CALL` span id for the round (`round_index` + model call). |
| `tool_call_id` | Internal canonical `ToolCall.id` (`_tool_call_doc` sets `id = new_id()`). |
| `provider_tool_call_id` | The LLM's `call.id` threaded into the `ToolCall` (`provider_tool_call_id` field). |

The two tool-call id spaces are deliberately distinct and never conflated:
`tool_call_id` is OmniLab's internal record id; `provider_tool_call_id` is the
provider's id (`execution.py` records `provider_tool_call_id or call_id` in
`tool_rounds`).

---

## 5. Timeout hierarchy

Timeouts are three distinct layers, each owned by a different concern:

| Layer | Valve / setting | Applies to |
| --- | --- | --- |
| Provider connection | `provider_connection_timeout` | Establishing/awaiting the LLM provider connection for a model call. |
| Fetch request | `fetch_request_timeout` | A single fetch provider HTTP request (`StandardFetchProvider` / `TinyFishFetchProvider` in `backend/app/services/fetch_providers.py`). |
| Agent tool budget | `agent_tool_budget` | Whole-run wall-clock/tool budget across agentic rounds in `execute_model`. |

The **effective per-request timeout is constrained by a server ceiling**: a
caller-configured valve may lower a timeout but can never raise it above the
hard server-side maximum. This mirrors the existing valve-ceiling convention in
`backend/app/services/valves.py` (a hard `app.config` ceiling wins over any
caller value).

---

## 6. Partial-success semantics

A run's `status` describes the run; tool failures are reported separately so a
run that gathered usable evidence despite failed tools can still succeed.

- `run.status` may stay `"success"` while the run field `tool_outcome` is
  `"PARTIAL_TOOL_FAILURE"` (`RUN_TOOL_OUTCOMES = ("SUCCESS",
  "PARTIAL_TOOL_FAILURE", "TOOL_FAILURE")`).
- Counters are recorded on the run: `tool_success_count`, `tool_failure_count`,
  `fetch_timeout_count`, `search_timeout_count`.
- On the stream, individual tool problems surface as `TOOL_RESULT_FAILED` (and
  `FETCH_FAILED` for fetch sub-spans) with a structured `error` payload built by
  `tool_result_payload()`: `{"success": False, "error": {"code", "message",
  optional "url"/"timeout_seconds"/"provider"/"query"}}`. Successful tools emit
  `{"success": True, "result": <output>}`.

This lets the frontend show degraded-but-useful runs without inventing a
non-canonical run status.

---

## 7. Cancellation

Cancellation ends the stream with `RUN_CANCELLED` and persists the run with
`run.status = "cancelled"` (`RUN_STATUSES = ("success", "error", "cancelled")`).
Before V4.2.3.7 the execution loop only produced `success`/`error`; `cancelled`
is added by this milestone. `RUN_CANCELLED` is a terminal event, so consumers
stop reading the stream exactly as they do after `RUN_COMPLETED` / `RUN_FAILED`,
and the partial events already emitted remain a valid (non-authoritative)
projection of the interrupted run.

---

## Files

- `backend/app/services/stream_events.py` — protocol constants and helpers.
- `backend/app/services/execution.py` — canonical execution loop the stream projects.
- `backend/app/services/tools.py` — `ToolCall` documents (`execution_status`, `output`, `error`).
- `backend/tests/test_v4237_stream_events.py` — pure unit tests for this module.
