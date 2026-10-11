"""Dashboard overview.

All aggregation is performed in MongoDB and is always scoped: either to the
whole owner (`scope="global"`) or to a single owner-owned project
(`scope="project"`). A global figure is never relabelled as a project figure.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..database import db  # read-only aggregation accessor (never mutated)
from ..deps import get_current_user

router = APIRouter(prefix="/overview", tags=["overview"])

FAILURE_STATUSES = ["error", "failed", "failure"]


# ------------------------------------------------------------------ helpers
def _error_message(error: Any) -> Optional[str]:
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("code")
        return str(message) if message else None
    if error:
        return str(error)
    return None


def _with_error_message(run: Dict[str, Any]) -> Dict[str, Any]:
    return {**run, "error_message": run.get("error_message") or _error_message(run.get("error"))}


def _sum_known(field: str) -> Dict[str, Any]:
    return {"$sum": {"$cond": [{"$ne": [f"${field}", None]}, f"${field}", 0]}}


def _count_known(field: str) -> Dict[str, Any]:
    return {"$sum": {"$cond": [{"$ne": [f"${field}", None]}, 1, 0]}}


def _count_unknown(field: str) -> Dict[str, Any]:
    return {"$sum": {"$cond": [{"$eq": [f"${field}", None]}, 1, 0]}}


def _usage_known_cond() -> Dict[str, Any]:
    # A run's token usage is "known" only when both sides were reported; a
    # streaming run with null token counts must not be counted as complete.
    return {"$and": [{"$ne": ["$input_tokens", None]}, {"$ne": ["$output_tokens", None]}]}


def _model_usage_pipeline(match: Dict[str, Any]) -> list:
    return [
        {"$match": match},
        {"$group": {
            "_id": {"model_name": "$model_name", "model_id": "$model_id"},
            "requests": {"$sum": 1},
            "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
            "failures": {"$sum": {"$cond": [{"$ne": ["$status", "success"]}, 1, 0]}},
            "input_tokens": _sum_known("input_tokens"),
            "output_tokens": _sum_known("output_tokens"),
            "known_usage_run_count": {"$sum": {"$cond": [_usage_known_cond(), 1, 0]}},
            "unknown_usage_run_count": {"$sum": {"$cond": [_usage_known_cond(), 0, 1]}},
            "latency_sum": _sum_known("latency_ms"),
            "latency_known": _count_known("latency_ms"),
            "known_cost_total": _sum_known("estimated_cost"),
            "unknown_cost_run_count": _count_unknown("estimated_cost"),
        }},
        {"$sort": {"requests": -1}},
    ]


def _totals_pipeline(match: Dict[str, Any]) -> list:
    return [
        {"$match": match},
        {"$group": {
            "_id": None,
            "requests": {"$sum": 1},
            "known_cost_total": _sum_known("estimated_cost"),
            "unknown_cost_run_count": _count_unknown("estimated_cost"),
        }},
    ]


def _model_usage_entry(row: Dict[str, Any]) -> Dict[str, Any]:
    ident = row.get("_id") or {}
    requests = int(row.get("requests", 0) or 0)
    unknown_cost_runs = int(row.get("unknown_cost_run_count", 0) or 0)
    latency_known = int(row.get("latency_known", 0) or 0)
    known_cost_total = round(float(row.get("known_cost_total", 0) or 0), 6)
    cost_available = requests > 0 and unknown_cost_runs == 0
    return {
        "model_name": ident.get("model_name") or ident.get("model_id") or "unknown",
        "requests": requests,
        "input_tokens": int(row.get("input_tokens", 0) or 0),
        "output_tokens": int(row.get("output_tokens", 0) or 0),
        "known_usage_run_count": int(row.get("known_usage_run_count", 0) or 0),
        "unknown_usage_run_count": int(row.get("unknown_usage_run_count", 0) or 0),
        "avg_latency_ms": round((row.get("latency_sum", 0) or 0) / latency_known) if latency_known else None,
        "estimated_cost": known_cost_total if cost_available else None,
        "known_cost_total": known_cost_total,
        "unknown_cost_run_count": unknown_cost_runs,
        "cost_available": cost_available,
        "success": int(row.get("success", 0) or 0),
        "failures": int(row.get("failures", 0) or 0),
    }


@router.get("")
async def overview(project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    uid = user["id"]
    if project_id:
        project = await repo.projects.get(project_id, uid)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        scope_match: Dict[str, Any] = {"owner_id": uid, "project_id": project_id}
    else:
        project = None
        scope_match = {"owner_id": uid}

    if project_id:
        counts = {
            "projects": 1,
            "experiments": await repo.experiments.count(dict(scope_match)),
            "runs": await repo.runs.count(dict(scope_match)),
            "artifacts": await repo.artifacts.count(dict(scope_match)),
            "models": len([m for m in await db.runs.distinct("model_id", dict(scope_match)) if m]),
            "providers": len([p for p in await db.runs.distinct("provider_id", dict(scope_match)) if p]),
        }
    else:
        counts = {
            "projects": await repo.projects.count({"owner_id": uid, "status": {"$ne": "archived"}}),
            "experiments": await repo.experiments.count({"owner_id": uid}),
            "runs": await repo.runs.count({"owner_id": uid}),
            "models": await repo.models.count({"owner_id": uid, "enabled": True}),
            "providers": await repo.providers.count({"owner_id": uid}),
            "artifacts": await repo.artifacts.count({"owner_id": uid}),
        }

    recent_experiments = await repo.experiments.list(dict(scope_match), sort_field="created_at", limit=6)
    recent_runs = [
        _with_error_message(r)
        for r in await repo.runs.list(dict(scope_match), sort_field="created_at", limit=12)
    ]
    failure_query = {**scope_match, "status": {"$in": FAILURE_STATUSES}}
    failures = [
        _with_error_message(r)
        for r in await repo.runs.list(failure_query, sort_field="created_at", limit=6)
    ]

    model_rows = await db.usage_events.aggregate(_model_usage_pipeline(dict(scope_match))).to_list(length=500)
    model_usage = [_model_usage_entry(r) for r in model_rows]

    totals_rows = await db.usage_events.aggregate(_totals_pipeline(dict(scope_match))).to_list(length=1)
    totals = totals_rows[0] if totals_rows else {}
    requests = int(totals.get("requests", 0) or 0)
    unknown_cost_runs = int(totals.get("unknown_cost_run_count", 0) or 0)
    known_cost_total = round(float(totals.get("known_cost_total", 0) or 0), 6)
    cost_available = requests > 0 and unknown_cost_runs == 0

    return {
        "counts": counts,
        "scope": "project" if project_id else "global",
        "project_id": project_id,
        "recent_experiments": recent_experiments,
        "recent_runs": recent_runs,
        "failures": failures,
        "model_usage": model_usage,
        "cost_summary": {
            "total_estimated_cost": known_cost_total if cost_available else None,
            "known_cost_total": known_cost_total,
            "unknown_cost_run_count": unknown_cost_runs,
            "cost_available": cost_available,
            "total_requests": requests,
        },
    }
