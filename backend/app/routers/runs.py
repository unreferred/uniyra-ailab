"""Run inspection endpoints: context, trace and side-by-side comparison.

Paths live here without a router prefix because they share the ``/runs``
namespace with the observability router's run detail endpoint.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..errors import safe_error_message
from ..models import EvaluationRequest
from ..services import context as context_service
from ..services import evaluation as evaluation_service
from ..services import failures as failure_service
from ..services import run_cancellation
from ..services import tools as tool_service
from ..services import trace_views
from ..services import tracing as tracing_service

router = APIRouter(tags=["runs"])

_IDENTITY_KEYS = trace_views.IDENTITY_KEYS


def _run_public(run: Optional[dict], blind: bool = False) -> Optional[dict]:
    """Run copy with prompts/response omitted; identity stripped when blind."""
    if not isinstance(run, dict):
        return None
    public = {
        key: value
        for key, value in run.items()
        if key not in ("system_prompt", "messages", "raw_response")
    }
    if blind:
        for key in _IDENTITY_KEYS:
            public.pop(key, None)
        public["error_message"] = safe_error_message(run.get("error"))
    return public


def _snapshot_attachment_names(snapshot: Optional[dict]) -> List[str]:
    names: List[str] = []
    for component in (snapshot or {}).get("components") or []:
        if component.get("component_type") != "ATTACHMENTS":
            continue
        metadata = component.get("metadata") or {}
        name = metadata.get("filename") or component.get("source_ref")
        if name:
            names.append(name)
    return names


def _snapshot_tool_names(snapshot: Optional[dict]) -> List[str]:
    for component in (snapshot or {}).get("components") or []:
        if component.get("component_type") == "TOOL_DEFINITIONS":
            return list((component.get("metadata") or {}).get("tool_names") or [])
    return []


def _context_summary(run: dict, snapshot: Optional[dict]) -> dict:
    return {
        "context_hash": (snapshot or {}).get("context_hash"),
        "estimated_tokens": (snapshot or {}).get("estimated_total_tokens"),
        "provider_input_tokens": run.get("input_tokens"),
        "attachment_names": _snapshot_attachment_names(snapshot),
        "tool_names": _snapshot_tool_names(snapshot),
    }


def _tool_summary(snapshot: Optional[dict], tool_calls: List[dict]) -> dict:
    calls = [
        {
            "name": call.get("tool_name"),
            "status": call.get("execution_status"),
            "duration_ms": call.get("duration_ms"),
        }
        for call in (tool_calls or [])
        if isinstance(call, dict)
    ]
    return {"enabled": _snapshot_tool_names(snapshot), "calls": calls}


def _result_summary(run: dict) -> dict:
    return {
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
        "estimated_cost": run.get("estimated_cost"),
        "cost_available": bool(run.get("cost_available"))
        and run.get("estimated_cost") is not None,
        "finish_reason": run.get("finish_reason"),
        "token_usage": run.get("token_usage"),
        "cache_telemetry": run.get("cache_telemetry"),
        "reasoning_telemetry": run.get("reasoning_telemetry"),
        "cost_breakdown": run.get("cost_breakdown"),
    }


def _identity(run: dict, blind: bool) -> dict:
    if blind:
        return {"model": None, "provider": None}
    return {
        "model": run.get("model_name") or run.get("model_id"),
        "provider": run.get("provider_name"),
    }


def _parameter_diff(run_a: dict, run_b: dict) -> dict:
    params_a = run_a.get("params") or {}
    params_b = run_b.get("params") or {}
    changed: Dict[str, Any] = {}
    for key in set(params_a) | set(params_b):
        if params_a.get(key) != params_b.get(key):
            changed[key] = {"a": params_a.get(key), "b": params_b.get(key)}
    return changed


def _public_failure(failure: dict, blind: bool) -> dict:
    if not isinstance(failure, dict):
        return {}
    public = dict(failure)
    if blind:
        for key in _IDENTITY_KEYS + ("run_model_id", "run_model_name"):
            public.pop(key, None)
    return public


@router.get("/runs/compare")
async def compare_runs(
    run_a: str,
    run_b: str,
    user: dict = Depends(get_current_user),
):
    owner_id = user["id"]
    a = await repo.runs.get(run_a, owner_id)
    if not a:
        raise HTTPException(status_code=404, detail="Run not found")
    b = await repo.runs.get(run_b, owner_id)
    if not b:
        raise HTTPException(status_code=404, detail="Run not found")

    blind_a = await trace_views._is_blind_unrevealed(owner_id, a.get("experiment_id"))
    blind_b = await trace_views._is_blind_unrevealed(owner_id, b.get("experiment_id"))

    snapshot_a = await context_service.get_context_snapshot(owner_id, run_a)
    snapshot_b = await context_service.get_context_snapshot(owner_id, run_b)
    calls_a = await tool_service.list_tool_calls(owner_id, run_id=run_a)
    calls_b = await tool_service.list_tool_calls(owner_id, run_id=run_b)
    failures_a = (await failure_service.list_failures(owner_id, run_id=run_a)).get("items", [])
    failures_b = (await failure_service.list_failures(owner_id, run_id=run_b)).get("items", [])

    identity_a = _identity(a, blind_a)
    identity_b = _identity(b, blind_b)

    context_diff = None
    if snapshot_a is not None and snapshot_b is not None:
        try:
            context_diff = context_service.diff_contexts(snapshot_a, snapshot_b)
        except Exception:  # pragma: no cover - defensive
            context_diff = None

    return {
        "run_a": _run_public(a, blind=blind_a),
        "run_b": _run_public(b, blind=blind_b),
        "blind": {"a": bool(blind_a), "b": bool(blind_b)},
        "identity": {
            "model_a": identity_a["model"],
            "model_b": identity_b["model"],
            "provider_a": identity_a["provider"],
            "provider_b": identity_b["provider"],
        },
        "parameters": {
            "a": a.get("params") or {},
            "b": b.get("params") or {},
            "diff": _parameter_diff(a, b),
        },
        "context": {
            "a": _context_summary(a, snapshot_a),
            "b": _context_summary(b, snapshot_b),
        },
        "tools": {
            "a": _tool_summary(snapshot_a, calls_a),
            "b": _tool_summary(snapshot_b, calls_b),
        },
        "result": {
            "a": _result_summary(a),
            "b": _result_summary(b),
        },
        "failures": {
            "a": [_public_failure(f, blind_a) for f in failures_a],
            "b": [_public_failure(f, blind_b) for f in failures_b],
        },
        "context_diff": context_diff,
    }


@router.get("/runs/{run_id}/context")
async def get_run_context(run_id: str, user: dict = Depends(get_current_user)):
    owner_id = user["id"]
    run = await repo.runs.get(run_id, owner_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    snapshot = await context_service.get_context_snapshot(owner_id, run_id)
    if snapshot is None:
        return {
            "available": False,
            "reason": "legacy_run",
            "message": "Legacy run — trace unavailable",
            "run_id": run_id,
            "trace_id": run.get("trace_id"),
        }
    return {
        "available": True,
        "run_id": run_id,
        "trace_id": run.get("trace_id"),
        "snapshot": snapshot,
        "provider_input_tokens": run.get("input_tokens"),
    }


@router.get("/runs/{run_id}/trace")
async def get_run_trace(run_id: str, user: dict = Depends(get_current_user)):
    owner_id = user["id"]
    run = await repo.runs.get(run_id, owner_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    trace_id = run.get("trace_id")
    if not trace_id:
        return {
            "available": False,
            "reason": "legacy_run",
            "message": "Legacy run — trace unavailable",
            "run_id": run_id,
        }
    trace = await tracing_service.get_trace(owner_id, trace_id)
    if trace is None:
        return {
            "available": False,
            "reason": "legacy_run",
            "message": "Legacy run — trace unavailable",
            "run_id": run_id,
            "trace_id": trace_id,
        }
    view = await trace_views.build_trace_view(owner_id, trace)
    return {"available": True, **view}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user: dict = Depends(get_current_user)):
    """Cancel a live agentic stream for ``run_id`` (V4.2.3.7).

    Cancellation is process-local and ephemeral (like the stream itself): a run
    currently streaming on this event loop is stopped by setting its
    ``asyncio.Event``, which makes the execution loop persist the Run with
    ``status="cancelled"`` and emit a terminal ``RUN_CANCELLED`` frame.

    A run that is neither persisted for this user nor live in the registry is
    unknown -> 404. A persisted run that already reached a terminal status, or
    an id with no live stream, returns ``{"cancelled": false, "reason":
    "not_running"}`` with HTTP 200. This endpoint never 500s.
    """
    owner_id = user["id"]
    run = await repo.runs.get(run_id, owner_id)
    live = run_cancellation.is_registered(run_id)
    # A live stream is not persisted until it terminates, so liveness (not just
    # the persisted record) establishes that the run exists for this caller.
    if run is None and not live:
        raise HTTPException(status_code=404, detail="Run not found")

    terminal = bool(run) and run.get("status") in ("success", "error", "cancelled")
    status = (run or {}).get("status")
    cancelled = run_cancellation.cancel(run_id)
    if not cancelled or terminal:
        return {
            "run_id": run_id,
            "cancelled": False,
            "reason": "not_running",
            "status": status,
        }
    return {"run_id": run_id, "cancelled": True, "status": status}


@router.post("/runs/{run_id}/evaluate")
async def evaluate_run_endpoint(
    run_id: str,
    body: EvaluationRequest,
    user: dict = Depends(get_current_user),
):
    owner_id = user["id"]
    run = await repo.runs.get(run_id, owner_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    experiment = None
    if run.get("experiment_id"):
        experiment = await repo.experiments.get(run["experiment_id"], owner_id)
    try:
        return await evaluation_service.evaluate_run(
            owner_id,
            experiment,
            {**body.model_dump(), "run_id": run_id},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/runs/{run_id}/evaluations")
async def list_run_evaluations(
    run_id: str,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    try:
        return await evaluation_service.list_run_evaluations(
            user["id"], run_id, limit=limit, offset=offset
        )
    except evaluation_service.EvaluationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
