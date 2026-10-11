"""Trace listing and detail endpoints (Phase 3 observability)."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import TRACE_TYPES
from ..services import trace_views
from ..services import tracing as tracing_service

router = APIRouter(prefix="/traces", tags=["traces"])


def _trace_query(
    owner_id: str,
    project_id: Optional[str] = None,
    trace_type: Optional[str] = None,
    experiment_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    council_id: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    query: Dict[str, Any] = {"owner_id": owner_id}
    if project_id:
        query["project_id"] = project_id
    if trace_type:
        query["trace_type"] = trace_type
    if experiment_id:
        query["experiment_id"] = experiment_id
    if conversation_id:
        query["conversation_id"] = conversation_id
    if council_id:
        query["council_id"] = council_id
    if status:
        query["status"] = status
    if date_from or date_to:
        started: Dict[str, Any] = {}
        if date_from:
            started["$gte"] = date_from
        if date_to:
            started["$lte"] = date_to
        query["started_at"] = started
    return query


async def _trace_summary(
    owner_id: str, trace: Dict[str, Any], blind_cache: Dict[str, bool]
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "id": trace.get("id"),
        "trace_type": trace.get("trace_type"),
        "status": trace.get("status"),
        "project_id": trace.get("project_id"),
        "experiment_id": trace.get("experiment_id"),
        "conversation_id": trace.get("conversation_id"),
        "council_id": trace.get("council_id"),
        "root_run_id": trace.get("root_run_id"),
        "started_at": trace.get("started_at"),
        "completed_at": trace.get("completed_at"),
        "total_duration_ms": trace.get("total_duration_ms"),
        "span_count": trace.get("span_count"),
    }
    root_run_id = trace.get("root_run_id")
    if not root_run_id:
        return summary
    try:
        run = await repo.runs.get(root_run_id, owner_id)
    except Exception:  # pragma: no cover - defensive
        run = None
    if not run:
        return summary

    summary["run_status"] = run.get("status")
    experiment_id = trace.get("experiment_id") or run.get("experiment_id")
    blind = False
    if experiment_id:
        if experiment_id not in blind_cache:
            blind_cache[experiment_id] = await trace_views._is_blind_unrevealed(
                owner_id, experiment_id
            )
        blind = blind_cache[experiment_id]
    if not blind:
        summary["model_name"] = run.get("model_name")
    return summary


@router.get("")
async def list_traces(
    project_id: Optional[str] = None,
    trace_type: Optional[str] = None,
    experiment_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    council_id: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    if trace_type is not None and trace_type not in TRACE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported trace_type. Supported: {list(TRACE_TYPES)}",
        )
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    query = _trace_query(
        user["id"],
        project_id=project_id,
        trace_type=trace_type,
        experiment_id=experiment_id,
        conversation_id=conversation_id,
        council_id=council_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    items = await repo.traces.list(query, limit=limit, offset=offset)
    total = await repo.traces.count(query)
    blind_cache: Dict[str, bool] = {}
    summaries = [await _trace_summary(user["id"], trace, blind_cache) for trace in items]
    return {"items": summaries, "total": total, "limit": limit, "offset": offset}


@router.get("/{trace_id}")
async def get_trace_detail(trace_id: str, user: dict = Depends(get_current_user)):
    trace = await tracing_service.get_trace(user["id"], trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return await trace_views.build_trace_view(user["id"], trace)
