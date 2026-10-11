"""Best-effort trace / span recorder for OmniLab observability.

Tracing is telemetry: recording must never break the work it observes. Every
persistence call here swallows exceptions and returns the in-memory document,
so a missing or unavailable database degrades to a no-op rather than a failed
request.

A duration is only ever derived from a caller-supplied ``time.perf_counter()``
reading. When no reading is passed, ``duration_ms`` stays ``None`` -- a
fabricated timing would be worse than no timing at all.
"""
import sys
import time
from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..models import SPAN_TYPES, TRACE_TYPES, new_id, now_iso


def _repo():
    """Resolve the repository through the services package namespace.

    Mirrors ``execution._repo`` so tests that monkeypatch
    ``app.services.repo`` observe tracing writes as well.
    """
    pkg = sys.modules.get(__package__)
    return getattr(pkg, "repo", repo) if pkg is not None else repo


def _normalize_trace_type(trace_type: Any) -> str:
    value = str(trace_type) if trace_type is not None else ""
    return value if value in TRACE_TYPES else "system"


def _normalize_span_type(span_type: Any) -> str:
    value = str(span_type) if span_type is not None else ""
    return value if value in SPAN_TYPES else "SYSTEM"


async def start_trace(
    owner_id: str,
    trace_type: str,
    *,
    project_id: Optional[str] = None,
    root_run_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    council_id: Optional[str] = None,
    dataset_case_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    status: str = "running",
    started_at: Optional[str] = None,
) -> dict:
    """Create a Trace document and persist it best-effort. Never raises."""
    doc = {
        "id": trace_id or new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "trace_type": _normalize_trace_type(trace_type),
        "root_run_id": root_run_id,
        "experiment_id": experiment_id,
        "conversation_id": conversation_id,
        "council_id": council_id,
        "dataset_case_id": dataset_case_id,
        "status": status,
        "started_at": started_at or now_iso(),
        "completed_at": None,
        "total_duration_ms": None,
        "span_count": 0,
        "metadata": dict(metadata or {}),
        "schema_version": 1,
    }
    try:
        await _repo().traces.insert(doc)
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass
    return doc


async def start_span(
    owner_id: str,
    trace_id: str,
    span_type: str,
    name: str,
    *,
    parent_span_id: Optional[str] = None,
    run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    status: str = "running",
    started_at: Optional[str] = None,
    ordering: Optional[int] = None,
) -> dict:
    """Create a TraceSpan and bump the parent trace's span_count. Never raises."""
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "trace_id": trace_id,
        "parent_span_id": parent_span_id,
        "span_type": _normalize_span_type(span_type),
        "name": name,
        "status": status,
        "started_at": started_at or now_iso(),
        "completed_at": None,
        "duration_ms": None,
        "run_id": run_id,
        "metadata": dict(metadata or {}),
        "structured_error": None,
        "ordering": (
            ordering if ordering is not None else int(time.time() * 1000) % 2_000_000_000
        ),
    }
    try:
        await _repo().trace_spans.insert(doc)
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass

    try:
        trace = await _repo().traces.get(trace_id, owner_id)
        if trace is not None:
            await _repo().traces.update(
                trace_id,
                owner_id,
                {"span_count": int(trace.get("span_count", 0)) + 1},
            )
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass
    return doc


async def finish_span(
    span: Dict[str, Any],
    *,
    status: str = "completed",
    metadata: Optional[Dict[str, Any]] = None,
    structured_error: Optional[Dict[str, Any]] = None,
    started_perf: Optional[float] = None,
) -> dict:
    """Finalize a span in memory and persist the update best-effort."""
    span["status"] = status
    span["completed_at"] = now_iso()
    if started_perf is not None:
        span["duration_ms"] = int((time.perf_counter() - started_perf) * 1000)
    if metadata:
        merged = dict(span.get("metadata") or {})
        merged.update(metadata)
        span["metadata"] = merged
    if structured_error is not None:
        span["structured_error"] = structured_error

    try:
        await _repo().trace_spans.update(
            span["id"],
            span["owner_id"],
            {
                "status": span["status"],
                "completed_at": span["completed_at"],
                "duration_ms": span.get("duration_ms"),
                "metadata": span.get("metadata") or {},
                "structured_error": span.get("structured_error"),
            },
        )
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass
    return span


async def finish_trace(
    trace: Dict[str, Any],
    *,
    status: str = "completed",
    metadata: Optional[Dict[str, Any]] = None,
    started_perf: Optional[float] = None,
) -> dict:
    """Finalize a trace in memory and persist the update best-effort."""
    trace["status"] = status
    trace["completed_at"] = now_iso()
    if started_perf is not None:
        trace["total_duration_ms"] = int((time.perf_counter() - started_perf) * 1000)
    if metadata:
        merged = dict(trace.get("metadata") or {})
        merged.update(metadata)
        trace["metadata"] = merged

    try:
        await _repo().traces.update(
            trace["id"],
            trace["owner_id"],
            {
                "status": trace["status"],
                "completed_at": trace["completed_at"],
                "total_duration_ms": trace.get("total_duration_ms"),
                "metadata": trace.get("metadata") or {},
            },
        )
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass
    return trace


def span_types() -> tuple:
    """The canonical set of valid span types."""
    return SPAN_TYPES


def span_span_types() -> tuple:
    """Backwards-compatible alias for :func:`span_types`."""
    return SPAN_TYPES


async def list_spans(owner_id: str, trace_id: str) -> List[dict]:
    """All spans for a trace, ordered by (ordering, started_at)."""
    try:
        spans = await _repo().trace_spans.list(
            {"owner_id": owner_id, "trace_id": trace_id},
            sort_field="ordering",
            descending=False,
            limit=10000,
        )
    except Exception:  # pragma: no cover - telemetry is best-effort
        return []
    return sorted(
        spans,
        key=lambda s: (s.get("ordering") or 0, s.get("started_at") or ""),
    )


async def get_trace(owner_id: str, trace_id: str) -> Optional[dict]:
    """Fetch a trace by id, or None when absent/unavailable."""
    try:
        return await _repo().traces.get(trace_id, owner_id)
    except Exception:  # pragma: no cover - telemetry is best-effort
        return None
