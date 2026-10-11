"""Canonical agentic-streaming event protocol (V4.2.3.8).

This module is the single source of truth for streaming event type names and
event construction. The stream is an *ephemeral live projection* of the
canonical execution loop (:func:`app.services.execution.execute_model`); the
persisted execution record stays authoritative. Nothing here imports application
code, keeping the protocol stdlib-only and cycle-free.

Two distinct layers share this stream, and they must not be conflated:

* A **MODEL TOOL CALL** is a tool the model itself emitted. It is announced by
  ``TOOL_CALL_STARTED``/``TOOL_CALL_DELTA``/``TOOL_CALL_ARGUMENTS``/
  ``TOOL_CALL_COMPLETED`` and executed via the canonical ``ToolCall`` record,
  whose outcome is reported by ``TOOL_RESULT_STARTED``/
  ``TOOL_RESULT_COMPLETED``/``TOOL_RESULT_FAILED``. These carry the model's
  ``provider_tool_call_id``.

* An **INTERNAL TOOL OPERATION** is a sub-operation performed *inside* a single
  composite model tool call (for example ``web_research`` fanning out to search
  and fetch steps). Internal operations are reported by the ``SEARCH_*`` /
  ``FETCH_*`` events carrying ``internal=True``, an ``operation_id``, an
  ``operation_kind``, and the parent's ``provider_tool_call_id``. Internal
  operations are NEVER separate model ToolCalls and never appear in the
  model-facing tool list.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

__all__ = [
    # lifecycle
    "RUN_STARTED", "RUN_COMPLETED", "RUN_FAILED", "RUN_CANCELLED",
    # model
    "MODEL_STARTED", "MODEL_REASONING_DELTA", "MODEL_OUTPUT_DELTA",
    "MODEL_COMPLETED", "MODEL_CALL_TIMEOUT",
    # tool call
    "TOOL_CALL_STARTED", "TOOL_CALL_DELTA", "TOOL_CALL_ARGUMENTS",
    "TOOL_CALL_COMPLETED",
    # tool result
    "TOOL_RESULT_STARTED", "TOOL_RESULT_COMPLETED", "TOOL_RESULT_FAILED",
    # search / fetch (internal tool operations of composite tool calls)
    "SEARCH_STARTED", "SEARCH_COMPLETED",
    "FETCH_STARTED", "FETCH_COMPLETED", "FETCH_FAILED",
    "COMPOSITE_OPERATION_EVENT_TYPES",
    # finalization
    "FINALIZATION_STARTED", "FINALIZATION_DELTA", "FINALIZATION_COMPLETED",
    "FINALIZATION_EMPTY",
    # groups
    "LIFECYCLE_EVENT_TYPES", "MODEL_EVENT_TYPES", "TOOL_CALL_EVENT_TYPES",
    "TOOL_RESULT_EVENT_TYPES", "SEARCH_FETCH_EVENT_TYPES",
    "FINALIZATION_EVENT_TYPES", "ALL_EVENT_TYPES", "TERMINAL_EVENT_TYPES",
    "TOOL_KINDS", "RUN_TOOL_OUTCOMES", "RUN_STATUSES", "CORRELATION_KEYS",
    # helpers
    "event", "is_terminal", "sse_frame", "tool_result_payload",
]

# --------------------------------------------------------------------------
# Lifecycle phase
# --------------------------------------------------------------------------
RUN_STARTED = "RUN_STARTED"        # run accepted; stream opens
RUN_COMPLETED = "RUN_COMPLETED"    # run finished successfully (terminal)
RUN_FAILED = "RUN_FAILED"          # run ended in error (terminal)
RUN_CANCELLED = "RUN_CANCELLED"    # run cancelled by client/server (terminal)

# --------------------------------------------------------------------------
# Model phase (one agentic round's model call)
# --------------------------------------------------------------------------
MODEL_STARTED = "MODEL_STARTED"                     # provider call dispatched
MODEL_REASONING_DELTA = "MODEL_REASONING_DELTA"     # streamed reasoning token(s)
MODEL_OUTPUT_DELTA = "MODEL_OUTPUT_DELTA"           # streamed answer token(s)
MODEL_COMPLETED = "MODEL_COMPLETED"                 # provider call settled
MODEL_CALL_TIMEOUT = "MODEL_CALL_TIMEOUT"           # watchdog aborted the call (non-terminal)

# --------------------------------------------------------------------------
# Tool-call phase (provider tool_call projected into canonical form)
# --------------------------------------------------------------------------
TOOL_CALL_STARTED = "TOOL_CALL_STARTED"         # provider tool_call announced
TOOL_CALL_DELTA = "TOOL_CALL_DELTA"             # streamed raw arguments fragment
TOOL_CALL_ARGUMENTS = "TOOL_CALL_ARGUMENTS"     # parsed arguments snapshot
TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"     # tool_call fully assembled

# --------------------------------------------------------------------------
# Tool-result phase (canonical ToolCall execution)
# --------------------------------------------------------------------------
TOOL_RESULT_STARTED = "TOOL_RESULT_STARTED"     # execution funnel entered
TOOL_RESULT_COMPLETED = "TOOL_RESULT_COMPLETED"  # tool succeeded
TOOL_RESULT_FAILED = "TOOL_RESULT_FAILED"       # tool failed (structured error)

# --------------------------------------------------------------------------
# Search / fetch sub-span phase (nested under a tool result)
# --------------------------------------------------------------------------
SEARCH_STARTED = "SEARCH_STARTED"       # search provider call dispatched
SEARCH_COMPLETED = "SEARCH_COMPLETED"   # search provider call returned
FETCH_STARTED = "FETCH_STARTED"         # fetch provider call dispatched
FETCH_COMPLETED = "FETCH_COMPLETED"     # fetch provider call returned
FETCH_FAILED = "FETCH_FAILED"           # fetch provider call failed

# --------------------------------------------------------------------------
# Finalization phase (single forced tools-free answer)
# --------------------------------------------------------------------------
FINALIZATION_STARTED = "FINALIZATION_STARTED"       # forced final call begins
FINALIZATION_DELTA = "FINALIZATION_DELTA"           # streamed final token(s)
FINALIZATION_COMPLETED = "FINALIZATION_COMPLETED"   # final answer complete
FINALIZATION_EMPTY = "FINALIZATION_EMPTY"           # final call produced no visible answer

# Grouped views (ordered by phase).
LIFECYCLE_EVENT_TYPES: Tuple[str, ...] = (
    RUN_STARTED, RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED,
)
MODEL_EVENT_TYPES: Tuple[str, ...] = (
    MODEL_STARTED, MODEL_REASONING_DELTA, MODEL_OUTPUT_DELTA, MODEL_COMPLETED,
    MODEL_CALL_TIMEOUT,
)
TOOL_CALL_EVENT_TYPES: Tuple[str, ...] = (
    TOOL_CALL_STARTED, TOOL_CALL_DELTA, TOOL_CALL_ARGUMENTS, TOOL_CALL_COMPLETED,
)
TOOL_RESULT_EVENT_TYPES: Tuple[str, ...] = (
    TOOL_RESULT_STARTED, TOOL_RESULT_COMPLETED, TOOL_RESULT_FAILED,
)
SEARCH_FETCH_EVENT_TYPES: Tuple[str, ...] = (
    SEARCH_STARTED, SEARCH_COMPLETED,
    FETCH_STARTED, FETCH_COMPLETED, FETCH_FAILED,
)
FINALIZATION_EVENT_TYPES: Tuple[str, ...] = (
    FINALIZATION_STARTED, FINALIZATION_DELTA, FINALIZATION_COMPLETED,
    FINALIZATION_EMPTY,
)
ALL_EVENT_TYPES: Tuple[str, ...] = (
    LIFECYCLE_EVENT_TYPES
    + MODEL_EVENT_TYPES
    + TOOL_CALL_EVENT_TYPES
    + TOOL_RESULT_EVENT_TYPES
    + SEARCH_FETCH_EVENT_TYPES
    + FINALIZATION_EVENT_TYPES
)

# Terminal event types end the stream.
TERMINAL_EVENT_TYPES = frozenset({RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED})

# Tool kinds recognised by the canonical execution loop. ``research`` is the
# composite kind: one model tool call that internally fans out to search/fetch
# operations (see ``COMPOSITE_OPERATION_EVENT_TYPES``).
TOOL_KINDS: Tuple[str, ...] = ("search", "fetch", "research", "generic")

# Internal-operation event types: these carry ``internal=True`` plus
# ``operation_id``, ``operation_kind`` and the parent's ``provider_tool_call_id``
# in their payload. They describe sub-steps of a single composite tool call and
# are never separate model ToolCalls.
COMPOSITE_OPERATION_EVENT_TYPES: Tuple[str, ...] = (
    SEARCH_STARTED,
    SEARCH_COMPLETED,
    FETCH_STARTED,
    FETCH_COMPLETED,
    FETCH_FAILED,
)

# Run-level ``tool_outcome`` values (partial success is still ``status=success``).
RUN_TOOL_OUTCOMES: Tuple[str, ...] = ("SUCCESS", "PARTIAL_TOOL_FAILURE", "TOOL_FAILURE")

# Run-level ``status`` values (``cancelled`` is added in V4.2.3.7).
RUN_STATUSES: Tuple[str, ...] = ("success", "error", "cancelled")

# Correlation keys every event may carry (all optional except ``type``).
CORRELATION_KEYS: Tuple[str, ...] = (
    "run_id",
    "trace_id",
    "conversation_id",
    "round_index",
    "model_call_id",
    "tool_call_id",
    "provider_tool_call_id",
)


def event(
    event_type: str,
    *,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    round_index: Optional[int] = None,
    model_call_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    provider_tool_call_id: Optional[str] = None,
    **payload: Any,
) -> Dict[str, Any]:
    """Build a plain, JSON-serializable stream event dict.

    The returned mapping always carries ``"type"`` first, then the correlation
    keys that were explicitly provided (``None`` values are omitted), then the
    caller's ``payload`` keys.
    """
    evt: Dict[str, Any] = {"type": event_type}
    correlations = {
        "run_id": run_id,
        "trace_id": trace_id,
        "conversation_id": conversation_id,
        "round_index": round_index,
        "model_call_id": model_call_id,
        "tool_call_id": tool_call_id,
        "provider_tool_call_id": provider_tool_call_id,
    }
    for key, value in correlations.items():
        if value is not None:
            evt[key] = value
    evt.update(payload)
    return evt


def is_terminal(evt: Dict[str, Any]) -> bool:
    """Return True when ``evt`` is one of the terminal event types."""
    if not isinstance(evt, dict):
        return False
    return evt.get("type") in TERMINAL_EVENT_TYPES


def sse_frame(evt: Dict[str, Any]) -> str:
    """Encode ``evt`` as a Server-Sent Events ``data:`` frame."""
    return (
        "data: "
        + json.dumps(evt, default=str, separators=(",", ":"))
        + "\n\n"
    )


def tool_result_payload(tool_call_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Build the model-facing structured tool result for a ToolCall document.

    Success (``execution_status == "success"``) yields
    ``{"success": True, "result": <output>}``; anything else yields
    ``{"success": False, "error": {...}}`` with the ToolCall error ``code`` and
    ``message`` plus any optional ``url`` / ``timeout_seconds`` / ``provider`` /
    ``query`` fields. Read defensively: missing keys never raise.
    """
    doc = tool_call_doc if isinstance(tool_call_doc, dict) else {}
    if doc.get("execution_status") == "success":
        return {"success": True, "result": doc.get("output")}

    error = doc.get("error")
    if not isinstance(error, dict):
        error = {}
    details = error.get("details")
    if not isinstance(details, dict):
        details = {}
    out_error: Dict[str, Any] = {
        "code": error.get("code") or "TOOL_EXECUTION_FAILED",
        "message": error.get("message") or "Tool execution failed",
    }
    for key in ("url", "timeout_seconds", "provider", "query"):
        value = error.get(key)
        if value is None:
            value = details.get(key)
        if value is None:
            value = doc.get(key)
        if value is not None:
            out_error[key] = value
    return {"success": False, "error": out_error}
