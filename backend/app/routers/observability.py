"""Truthful, scalable execution observability.

Aggregations run inside MongoDB. The Repository abstraction deliberately exposes
no aggregate surface, so this module uses a narrow, read-only accessor to the
driver (`from ..database import db`). Every pipeline is hard-scoped by
`owner_id`. The only write this module issues is a single best-effort,
non-secret provider quota telemetry snapshot (provider/resource/window counters
only -- never credentials), persisted via the safe Repository abstraction.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..database import db  # read-only aggregation accessor (never mutated)
from ..deps import get_current_user
from ..models import new_id, now_iso
from ..services import provider_quota as provider_quota_service

router = APIRouter(prefix="/observability", tags=["observability"])

# Hard cap so a pathological history cannot exhaust memory when returning the
# chart series. Totals are always aggregated over the FULL filtered set.
SERIES_LIMIT = 5000

_SERIES_PROJECTION = {
    "_id": 0,
    "created_at": 1, "run_id": 1, "experiment_id": 1, "project_id": 1,
    "model_id": 1, "model_name": 1, "model_identifier": 1,
    "provider_id": 1, "provider_name": 1,
    "latency_ms": 1, "ttft_ms": 1,
    "input_tokens": 1, "output_tokens": 1, "cached_tokens": 1, "reasoning_tokens": 1,
    "estimated_cost": 1, "cost_available": 1,
    "status": 1, "context": 1, "execution_kind": 1, "streaming": 1,
}


# ------------------------------------------------------------- query builders
def _usage_match(
    owner_id: str,
    model_id: Optional[str] = None,
    project_id: Optional[str] = None,
    provider_id: Optional[str] = None,
    context: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    match: Dict[str, Any] = {"owner_id": owner_id}
    if model_id:
        match["model_id"] = model_id
    if project_id:
        match["project_id"] = project_id
    if provider_id:
        match["provider_id"] = provider_id
    if context:
        match["context"] = context
    if status:
        match["status"] = status
    if date_from or date_to:
        created: Dict[str, Any] = {}
        if date_from:
            created["$gte"] = date_from
        if date_to:
            created["$lte"] = date_to
        match["created_at"] = created
    return match


def _sum_known(field: str) -> Dict[str, Any]:
    return {"$sum": {"$cond": [{"$ne": [f"${field}", None]}, f"${field}", 0]}}


def _count_known(field: str) -> Dict[str, Any]:
    return {"$sum": {"$cond": [{"$ne": [f"${field}", None]}, 1, 0]}}


def _count_unknown(field: str) -> Dict[str, Any]:
    # Missing fields compare equal to null, so legacy events without the field
    # are correctly counted as unknown rather than as a known zero.
    return {"$sum": {"$cond": [{"$eq": [f"${field}", None]}, 1, 0]}}


def _retrieval_count_expr() -> Dict[str, Any]:
    """Metadata field a retrieval span uses for its result count.

    KNOWLEDGE_RETRIEVAL spans record ``returned_count`` (see
    ``services.evidence._span_retrieval_count``); older spans may carry
    ``chunk_count`` or the legacy ``result_count``. Prefer the field spans
    actually write, then fall back for backward compatibility.
    """
    return {"$ifNull": [
        "$metadata.returned_count",
        {"$ifNull": ["$metadata.chunk_count", "$metadata.result_count"]},
    ]}


def _group_metrics() -> Dict[str, Any]:
    return {
        "requests": {"$sum": 1},
        "failures": {"$sum": {"$cond": [{"$ne": ["$status", "success"]}, 1, 0]}},
        "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
        "latency_sum": _sum_known("latency_ms"),
        "latency_known": _count_known("latency_ms"),
        "ttft_sum": _sum_known("ttft_ms"),
        "ttft_known": _count_known("ttft_ms"),
        "input_tokens": _sum_known("input_tokens"),
        "input_tokens_known_run_count": _count_known("input_tokens"),
        "input_tokens_unknown_run_count": _count_unknown("input_tokens"),
        "output_tokens": _sum_known("output_tokens"),
        "output_tokens_known_run_count": _count_known("output_tokens"),
        "output_tokens_unknown_run_count": _count_unknown("output_tokens"),
        "cached_tokens": _sum_known("cached_tokens"),
        "cached_tokens_known_run_count": _count_known("cached_tokens"),
        "cached_tokens_unknown_run_count": _count_unknown("cached_tokens"),
        "reasoning_tokens": _sum_known("reasoning_tokens"),
        "reasoning_tokens_known_run_count": _count_known("reasoning_tokens"),
        "reasoning_tokens_unknown_run_count": _count_unknown("reasoning_tokens"),
        "known_cost_total": _sum_known("estimated_cost"),
        "unknown_cost_run_count": _count_unknown("estimated_cost"),
    }


def _totals_pipeline(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"$match": match}, {"$group": {"_id": None, **_group_metrics()}}]


def _model_pipeline(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"$match": match},
        {"$group": {
            "_id": {
                "model_id": "$model_id",
                "model_name": "$model_name",
                "provider_id": "$provider_id",
                "provider_name": "$provider_name",
            },
            **_group_metrics(),
        }},
        {"$sort": {"requests": -1}},
    ]


def _models_pipeline(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"$match": match},
        {"$group": {"_id": {"model_id": "$model_id", "model_name": "$model_name"}}},
        {"$sort": {"_id.model_name": 1}},
    ]


def _providers_pipeline(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"$match": match},
        {"$group": {"_id": {"provider_id": "$provider_id", "provider_name": "$provider_name"}}},
        {"$sort": {"_id.provider_name": 1}},
    ]


# ------------------------------------------------------------------ mapping
def _event_cost_available(event: Dict[str, Any]) -> bool:
    return bool(event.get("cost_available", True)) and event.get("estimated_cost") is not None


def _series_point(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "created_at": event.get("created_at"),
        "run_id": event.get("run_id"),
        "experiment_id": event.get("experiment_id"),
        "project_id": event.get("project_id"),
        "model_id": event.get("model_id"),
        "model_name": event.get("model_name"),
        "model_identifier": event.get("model_identifier"),
        "provider_id": event.get("provider_id"),
        "provider_name": event.get("provider_name"),
        # null means "not reported" -- never coerce to 0.
        "latency_ms": event.get("latency_ms"),
        "ttft_ms": event.get("ttft_ms"),
        "input_tokens": event.get("input_tokens"),
        "output_tokens": event.get("output_tokens"),
        "cached_tokens": event.get("cached_tokens"),
        "reasoning_tokens": event.get("reasoning_tokens"),
        "estimated_cost": event.get("estimated_cost"),
        "cost_available": _event_cost_available(event),
        "status": event.get("status"),
        "context": event.get("context"),
        "execution_kind": event.get("execution_kind"),
        "streaming": event.get("streaming"),
    }


def _totals(raw: Dict[str, Any]) -> Dict[str, Any]:
    requests = int(raw.get("requests", 0) or 0)
    failures = int(raw.get("failures", 0) or 0)
    unknown_cost_runs = int(raw.get("unknown_cost_run_count", 0) or 0)
    known_cost_total = round(float(raw.get("known_cost_total", 0) or 0), 6)
    latency_known = int(raw.get("latency_known", 0) or 0)
    ttft_known = int(raw.get("ttft_known", 0) or 0)
    # Cost is only presented as a total when EVERY event's cost is known.
    cost_available = requests > 0 and unknown_cost_runs == 0
    return {
        "requests": requests,
        "failures": failures,
        "success_rate": round((requests - failures) / requests, 4) if requests else None,
        "avg_latency_ms": round((raw.get("latency_sum", 0) or 0) / latency_known) if latency_known else None,
        "avg_ttft_ms": round((raw.get("ttft_sum", 0) or 0) / ttft_known) if ttft_known else None,
        "total_tokens": int(raw.get("input_tokens", 0) or 0) + int(raw.get("output_tokens", 0) or 0),
        "input_tokens": int(raw.get("input_tokens", 0) or 0),
        "output_tokens": int(raw.get("output_tokens", 0) or 0),
        "cached_tokens": int(raw.get("cached_tokens", 0) or 0),
        "reasoning_tokens": int(raw.get("reasoning_tokens", 0) or 0),
        "input_tokens_known_run_count": int(raw.get("input_tokens_known_run_count", 0) or 0),
        "input_tokens_unknown_run_count": int(raw.get("input_tokens_unknown_run_count", 0) or 0),
        "output_tokens_known_run_count": int(raw.get("output_tokens_known_run_count", 0) or 0),
        "output_tokens_unknown_run_count": int(raw.get("output_tokens_unknown_run_count", 0) or 0),
        "cached_tokens_known_run_count": int(raw.get("cached_tokens_known_run_count", 0) or 0),
        "cached_tokens_unknown_run_count": int(raw.get("cached_tokens_unknown_run_count", 0) or 0),
        "reasoning_tokens_known_run_count": int(raw.get("reasoning_tokens_known_run_count", 0) or 0),
        "reasoning_tokens_unknown_run_count": int(raw.get("reasoning_tokens_unknown_run_count", 0) or 0),
        "latency_known_run_count": latency_known,
        "ttft_known_run_count": ttft_known,
        "total_cost": known_cost_total if cost_available else None,
        "known_cost_total": known_cost_total,
        "unknown_cost_run_count": unknown_cost_runs,
        "cost_available": cost_available,
    }


def _model_entry(row: Dict[str, Any]) -> Dict[str, Any]:
    ident = row.get("_id") or {}
    requests = int(row.get("requests", 0) or 0)
    unknown_cost_runs = int(row.get("unknown_cost_run_count", 0) or 0)
    latency_known = int(row.get("latency_known", 0) or 0)
    known_cost_total = round(float(row.get("known_cost_total", 0) or 0), 6)
    cost_available = requests > 0 and unknown_cost_runs == 0
    return {
        "model_id": ident.get("model_id"),
        "model_name": ident.get("model_name") or ident.get("model_id") or "unknown",
        "provider_id": ident.get("provider_id"),
        "provider_name": ident.get("provider_name"),
        "requests": requests,
        "success": int(row.get("success", 0) or 0),
        "failures": int(row.get("failures", 0) or 0),
        "input_tokens": int(row.get("input_tokens", 0) or 0),
        "output_tokens": int(row.get("output_tokens", 0) or 0),
        "cached_tokens": int(row.get("cached_tokens", 0) or 0),
        "reasoning_tokens": int(row.get("reasoning_tokens", 0) or 0),
        "avg_latency_ms": round((row.get("latency_sum", 0) or 0) / latency_known) if latency_known else None,
        "estimated_cost": known_cost_total if cost_available else None,
        "known_cost_total": known_cost_total,
        "unknown_cost_run_count": unknown_cost_runs,
        "cost_available": cost_available,
    }


async def _aggregate_rows(pipeline: List[Dict[str, Any]], length: int) -> List[Dict[str, Any]]:
    cursor = db.usage_events.aggregate(pipeline)
    return await cursor.to_list(length=length)


# ------------------------------------------------------------------ endpoints
@router.get("")
async def observability(
    model_id: Optional[str] = None,
    project_id: Optional[str] = None,
    provider_id: Optional[str] = None,
    context: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """Time-ordered execution telemetry. Real usage_events only -- unavailable
    cost/tokens are null, never fabricated."""
    owner_id = user["id"]
    match = _usage_match(owner_id, model_id, project_id, provider_id, context, status, date_from, date_to)
    # Filter-option lists follow only the project scope so the dropdowns stay
    # usable while still never crossing the selected project boundary.
    scope_match = _usage_match(owner_id, project_id=project_id)

    cursor = db.usage_events.find(match, _SERIES_PROJECTION).sort("created_at", 1).limit(SERIES_LIMIT)
    events = await cursor.to_list(length=SERIES_LIMIT)
    series = [_series_point(e) for e in events]

    totals_rows = await _aggregate_rows(_totals_pipeline(match), 1)
    totals = _totals(totals_rows[0] if totals_rows else {})

    model_rows = await _aggregate_rows(_models_pipeline(scope_match), 500)
    models = [
        {"model_id": r["_id"].get("model_id"), "model_name": r["_id"].get("model_name") or r["_id"].get("model_id")}
        for r in model_rows if (r.get("_id") or {}).get("model_id")
    ]

    provider_rows = await _aggregate_rows(_providers_pipeline(scope_match), 500)
    providers = [
        {"provider_id": r["_id"].get("provider_id"), "provider_name": r["_id"].get("provider_name")}
        for r in provider_rows if (r.get("_id") or {}).get("provider_id")
    ]

    model_usage = [_model_entry(r) for r in await _aggregate_rows(_model_pipeline(match), 500)]

    tool_metrics = await _tool_metrics(owner_id, project_id)
    knowledge_metrics = await _knowledge_metrics(owner_id, project_id)

    quota_rows = provider_quota_service.snapshot()

    # Persist a safe, non-secret telemetry snapshot (best-effort). Secrets are
    # never present in quota rows; provider/resource/window counters only.
    try:
        await repo.provider_quota_telemetry.insert({
            "id": new_id(),
            "captured_at": now_iso(),
            "rows": quota_rows,
        })
    except Exception:
        pass

    return {
        "series": series,
        "models": models,
        "providers": providers,
        "totals": totals,
        "model_usage": model_usage,
        "tool_metrics": tool_metrics,
        "knowledge_metrics": knowledge_metrics,
        "provider_quota": quota_rows,
        "scope": "project" if project_id else "global",
        "project_id": project_id,
    }


async def _tool_metrics(owner_id: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    """Tool-call telemetry aggregated inside MongoDB. Missing data -> None."""
    unknown: Dict[str, Any] = {
        "total_calls": None,
        "success_rate": None,
        "failures": None,
        "avg_latency_ms": None,
        "by_tool": None,
        "code_interpreter": None,
    }
    try:
        match: Dict[str, Any] = {"owner_id": owner_id}
        if project_id:
            match["project_id"] = project_id
        pipeline = [
            {"$match": match},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "success": {"$sum": {"$cond": [{"$eq": ["$execution_status", "success"]}, 1, 0]}},
                "failures": {"$sum": {"$cond": [{"$ne": ["$execution_status", "success"]}, 1, 0]}},
                "latency_sum": _sum_known("duration_ms"),
                "latency_known": _count_known("duration_ms"),
            }},
        ]
        rows = await _aggregate_rows_raw("tool_calls", pipeline, 1)
        if not rows:
            return {**unknown, "total_calls": 0, "failures": 0, "by_tool": [],
                    "code_interpreter": {"executions": 0, "timeouts": 0, "errors": 0}}
        row = rows[0]
        total = int(row.get("total", 0) or 0)
        success = int(row.get("success", 0) or 0)
        failures = int(row.get("failures", 0) or 0)
        latency_known = int(row.get("latency_known", 0) or 0)

        by_tool_pipeline = [
            {"$match": match},
            {"$group": {
                "_id": {"tool_id": "$tool_id", "tool_name": "$tool_name"},
                "calls": {"$sum": 1},
                "failures": {"$sum": {"$cond": [{"$ne": ["$execution_status", "success"]}, 1, 0]}},
                "latency_sum": _sum_known("duration_ms"),
                "latency_known": _count_known("duration_ms"),
            }},
            {"$sort": {"calls": -1}},
        ]
        by_tool_rows = await _aggregate_rows_raw("tool_calls", by_tool_pipeline, 500)
        by_tool = [
            {
                "tool_id": (r.get("_id") or {}).get("tool_id"),
                "tool_name": (r.get("_id") or {}).get("tool_name"),
                "calls": int(r.get("calls", 0) or 0),
                "failures": int(r.get("failures", 0) or 0),
                "avg_latency_ms": (
                    round((r.get("latency_sum", 0) or 0) / int(r.get("latency_known", 0) or 0))
                    if int(r.get("latency_known", 0) or 0) else None
                ),
            }
            for r in by_tool_rows
        ]

        ci_match = dict(match)
        ci_match["$or"] = [{"tool_type": "code_interpreter"}, {"tool_name": "code_interpreter"}]
        ci_pipeline = [
            {"$match": ci_match},
            {"$group": {
                "_id": None,
                "executions": {"$sum": 1},
                "timeouts": {"$sum": {"$cond": [
                    {"$eq": ["$status", "timeout"]}, 1, 0]}},
                "errors": {"$sum": {"$cond": [
                    {"$in": ["$status", ["error", "timeout", "unavailable"]]}, 1, 0]}},
            }},
        ]
        ci_rows = await _aggregate_rows_raw("code_executions", ci_pipeline, 1)
        ci = ci_rows[0] if ci_rows else {}
        return {
            "total_calls": total,
            "success_rate": round(success / total, 4) if total else None,
            "failures": failures,
            "avg_latency_ms": (
                round((row.get("latency_sum", 0) or 0) / latency_known)
                if latency_known else None
            ),
            "by_tool": by_tool,
            "code_interpreter": {
                "executions": int(ci.get("executions", 0) or 0),
                "timeouts": int(ci.get("timeouts", 0) or 0),
                "errors": int(ci.get("errors", 0) or 0),
            },
        }
    except Exception:
        return unknown


async def _knowledge_metrics(owner_id: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    """Knowledge telemetry: retrieval spans + indexing state. Missing -> None."""
    unknown: Dict[str, Any] = {
        "retrievals": None,
        "avg_latency_ms": None,
        "empty_retrievals": None,
        "indexing_failures": None,
        "chunks_indexed": None,
    }
    try:
        span_match: Dict[str, Any] = {
            "owner_id": owner_id, "span_type": "KNOWLEDGE_RETRIEVAL",
        }
        span_pipeline = [
            {"$match": span_match},
            {"$group": {
                "_id": None,
                "retrievals": {"$sum": 1},
                "latency_sum": _sum_known("duration_ms"),
                "latency_known": _count_known("duration_ms"),
                "empty": {"$sum": {"$cond": [
                    {"$eq": [_retrieval_count_expr(), 0]}, 1, 0]}},
            }},
        ]
        span_rows = await _aggregate_rows_raw("trace_spans", span_pipeline, 1)
        span = span_rows[0] if span_rows else None

        doc_match: Dict[str, Any] = {"owner_id": owner_id}
        if project_id:
            doc_match["project_id"] = project_id
        doc_pipeline = [
            {"$match": doc_match},
            {"$group": {
                "_id": None,
                "failures": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
            }},
        ]
        doc_rows = await _aggregate_rows_raw("knowledge_documents", doc_pipeline, 1)
        chunk_pipeline = [
            {"$match": doc_match},
            {"$group": {"_id": None, "chunks": {"$sum": 1}}},
        ]
        chunk_rows = await _aggregate_rows_raw("knowledge_chunks", chunk_pipeline, 1)
        if span is None and not doc_rows and not chunk_rows:
            return {**unknown, "retrievals": 0, "empty_retrievals": 0,
                    "indexing_failures": 0, "chunks_indexed": 0}
        latency_known = int((span or {}).get("latency_known", 0) or 0)
        return {
            "retrievals": int((span or {}).get("retrievals", 0) or 0),
            "avg_latency_ms": (
                round(((span or {}).get("latency_sum", 0) or 0) / latency_known)
                if latency_known else None
            ),
            "empty_retrievals": int((span or {}).get("empty", 0) or 0),
            "indexing_failures": int((doc_rows[0].get("failures", 0) if doc_rows else 0) or 0),
            "chunks_indexed": int((chunk_rows[0].get("chunks", 0) if chunk_rows else 0) or 0),
        }
    except Exception:
        return unknown


async def _aggregate_rows_raw(collection: str, pipeline: List[Dict[str, Any]], length: int) -> List[Dict[str, Any]]:
    cursor = db[collection].aggregate(pipeline)
    return await cursor.to_list(length=length)


@router.get("/projects")
async def observability_projects(user: dict = Depends(get_current_user)):
    """Distinct projects that actually have usage for this owner, for consistent
    project filtering in the UI."""
    owner_id = user["id"]
    project_ids = [p for p in await db.usage_events.distinct("project_id", {"owner_id": owner_id}) if p]
    if not project_ids:
        return []
    docs = await repo.projects.list({"owner_id": owner_id, "id": {"$in": project_ids}}, limit=1000)
    by_id = {d["id"]: d for d in docs}
    return [
        {"id": pid, "name": (by_id.get(pid) or {}).get("name") or pid}
        for pid in project_ids
    ]


def _error_message(error: Any) -> Optional[str]:
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("code")
        return str(message) if message else None
    if error:
        return str(error)
    return None


def _run_detail(run: Dict[str, Any]) -> Dict[str, Any]:
    error = run.get("error")
    return {
        "id": run.get("id"),
        "run_id": run.get("run_id") or run.get("id"),
        "experiment_id": run.get("experiment_id"),
        "project_id": run.get("project_id"),
        "context": run.get("context"),
        "execution_kind": run.get("execution_kind"),
        "provider_id": run.get("provider_id"),
        "provider_name": run.get("provider_name"),
        "model_id": run.get("model_id"),
        "model_name": run.get("model_name"),
        "model_identifier": run.get("model_identifier"),
        "status": run.get("status"),
        "latency_ms": run.get("latency_ms"),
        "ttft_ms": run.get("ttft_ms"),
        "input_tokens": run.get("input_tokens"),
        "output_tokens": run.get("output_tokens"),
        "cached_tokens": run.get("cached_tokens"),
        "reasoning_tokens": run.get("reasoning_tokens"),
        "cache_read_tokens": run.get("cache_read_tokens") or run.get("cached_tokens"),
        "cache_write_tokens": run.get("cache_write_tokens"),
        "cache_creation_tokens": run.get("cache_creation_tokens"),
        "uncached_input_tokens": run.get("uncached_input_tokens"),
        "total_tokens": run.get("total_tokens"),
        "reasoning_content": run.get("reasoning_content"),
        "reasoning_summary": run.get("reasoning_summary"),
        "token_usage": run.get("token_usage"),
        "cache_telemetry": run.get("cache_telemetry"),
        "reasoning_telemetry": run.get("reasoning_telemetry"),
        "cost_breakdown": run.get("cost_breakdown"),
        "pricing_snapshot": run.get("pricing_snapshot"),
        "estimated_cost": run.get("estimated_cost"),
        "cost_available": bool(run.get("cost_available", run.get("estimated_cost") is not None))
                          and run.get("estimated_cost") is not None,
        "finish_reason": run.get("finish_reason"),
        "streaming": run.get("streaming"),
        "error": error,
        "error_message": run.get("error_message") or _error_message(error),
        "created_at": run.get("created_at"),
        "trace_id": run.get("trace_id"),
    }



@router.get("/runs/{run_id}")
async def observability_run_detail(run_id: str, user: dict = Depends(get_current_user)):
    run = await repo.runs.get(run_id, user["id"])
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_detail(run)
