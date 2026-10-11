"""Shared, blind-safe view builders for trace / run inspection.

Both the traces router and the runs router need to render a trace together with
its runs, spans and tool calls. The single invariant that matters here is that a
blind, unrevealed experiment must never leak model/provider identity through any
of those surfaces -- traces, spans, run projections or tool-call records.

Every builder is deliberately defensive: telemetry is best-effort and a missing
or unavailable database degrades to an empty view rather than an error.
"""
from typing import Any, Dict, Optional

from .. import repository as repo
from ..errors import safe_error_message
from . import tools as tools_service
from . import tracing as tracing_service


# Identity-bearing keys that must never survive into a blind projection. The
# list intentionally includes both model and provider naming as well as raw
# error envelopes (which may embed a model/provider name in their message).
IDENTITY_KEYS = (
    "model_id",
    "model_name",
    "model_identifier",
    "provider_id",
    "provider_name",
    "provider_type",
    "family",
    "api_key",
    "authorization",
    "error",
    "error_message",
)


def _redact_span(span: Dict[str, Any]) -> Dict[str, Any]:
    """Blind-safe copy of a span: keep ``run_id``, strip identity metadata.

    ``metadata`` may contain model/provider identifiers added by a future
    execution hook, so every identity key is removed. ``structured_error`` is
    reduced to its category only; the message can embed a model/provider name.
    """
    if not isinstance(span, dict):
        return {}
    redacted = dict(span)
    metadata = dict(redacted.get("metadata") or {})
    for key in IDENTITY_KEYS:
        metadata.pop(key, None)
    redacted["metadata"] = metadata
    structured_error = redacted.get("structured_error")
    if isinstance(structured_error, dict) and structured_error:
        redacted["structured_error"] = {"category": structured_error.get("category")}
    else:
        redacted["structured_error"] = None
    return redacted


async def _is_blind_unrevealed(owner_id: str, experiment_id: Optional[str]) -> bool:
    """True iff the experiment exists, is blind and has not been revealed."""
    if not experiment_id:
        return False
    try:
        experiment = await repo.experiments.get(experiment_id, owner_id)
    except Exception:  # pragma: no cover - defensive
        return False
    if not experiment:
        return False
    return bool(experiment.get("blind")) and not experiment.get("revealed_at")


def _blind_run_view(run: Dict[str, Any]) -> Dict[str, Any]:
    """Identity-free run projection for blind, unrevealed experiments."""
    if not isinstance(run, dict):
        return {}
    return {
        "id": run.get("id"),
        "label": None,
        "status": run.get("status"),
        "latency_ms": run.get("latency_ms"),
        "ttft_ms": run.get("ttft_ms"),
        "input_tokens": run.get("input_tokens"),
        "output_tokens": run.get("output_tokens"),
        "reasoning_tokens": run.get("reasoning_tokens"),
        "cache_read_tokens": run.get("cache_read_tokens"),
        "cache_write_tokens": run.get("cache_write_tokens"),
        "cache_creation_tokens": run.get("cache_creation_tokens"),
        "uncached_input_tokens": run.get("uncached_input_tokens"),
        "total_tokens": run.get("total_tokens"),
        "created_at": run.get("created_at"),
        "finish_reason": run.get("finish_reason"),
        "context": run.get("context"),
        "execution_kind": run.get("execution_kind"),
        # V4.2.3.13 additive: finalization diagnostics are non-identity
        # telemetry, so blind experiments can still expose them. Read
        # null-safely; ``finalization_telemetry`` is intentionally omitted
        # because it may carry provider-specific detail.
        "forced_finalization": run.get("forced_finalization"),
        "finalization_reason": run.get("finalization_reason"),
        "finalization_attempts": run.get("finalization_attempts"),
        "finalization_recovered": run.get("finalization_recovered"),
        "finalization_outcome": run.get("finalization_outcome"),
        "provider_finish_reason": run.get("provider_finish_reason"),
        "had_reasoning": run.get("had_reasoning"),
        "had_content": run.get("had_content"),
        "content_length": run.get("content_length"),
        "finalization_recovery_mode": run.get("finalization_recovery_mode"),
        "finalization_context_mode": run.get("finalization_context_mode"),
        "original_context_estimated_tokens": run.get("original_context_estimated_tokens"),
        "finalization_context_estimated_tokens": run.get("finalization_context_estimated_tokens"),
        "evidence_item_count": run.get("evidence_item_count"),
        "source_count": run.get("source_count"),
        "deduplicated_item_count": run.get("deduplicated_item_count"),
        "finalization_length_failure_kind": run.get("finalization_length_failure_kind"),
        "finalization_partial_answer": run.get("finalization_partial_answer"),
        "finalization_truncated": run.get("finalization_truncated"),
        "finalization_requested_max_output_tokens": run.get("finalization_requested_max_output_tokens"),
        "finalization_effective_max_output_tokens": run.get("finalization_effective_max_output_tokens"),
        # Only category-level text: raw messages embed model/provider names.
        "error_message": safe_error_message(run.get("error")) or None,
    }


def _blind_tool_call(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """Keep execution shape, blank arguments/results/errors."""
    if not isinstance(tool_call, dict):
        return {}
    redacted = dict(tool_call)
    redacted["input"] = None
    redacted["output"] = None
    redacted["error"] = None
    return redacted


def _public_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Trace copy without the server-side ``blind_map`` (if present)."""
    return {key: value for key, value in (trace or {}).items() if key != "blind_map"}


async def build_trace_view(owner_id: str, trace: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the shared trace detail payload. Never raises.

    Returns ``{"trace", "spans", "runs", "tool_calls", "blind"}``. When the
    owning experiment is blind and unrevealed, every identity-bearing value is
    stripped before it reaches the caller.
    """
    trace = trace if isinstance(trace, dict) else {}
    trace_id = trace.get("id")

    runs: Any = []
    tool_calls: Any = []
    spans: Any = []
    try:
        runs = await repo.runs.list(
            {"owner_id": owner_id, "trace_id": trace_id}, limit=100
        )
    except Exception:  # pragma: no cover - defensive
        runs = []
    try:
        tool_calls = await tools_service.list_tool_calls(owner_id, trace_id=trace_id)
    except Exception:  # pragma: no cover - defensive
        tool_calls = []
    try:
        spans = await tracing_service.list_spans(owner_id, trace_id)
    except Exception:  # pragma: no cover - defensive
        spans = []

    public_trace = _public_trace(trace)
    blind = await _is_blind_unrevealed(owner_id, trace.get("experiment_id"))

    if blind:
        metadata = dict(public_trace.get("metadata") or {})
        for key in IDENTITY_KEYS:
            metadata.pop(key, None)
        public_trace["metadata"] = metadata
        runs = [_blind_run_view(run) for run in runs]
        spans = [_redact_span(span) for span in spans]
        tool_calls = [_blind_tool_call(call) for call in tool_calls]

    return {
        "trace": public_trace,
        "spans": list(spans or []),
        "runs": list(runs or []),
        "tool_calls": list(tool_calls or []),
        "blind": bool(blind),
    }
