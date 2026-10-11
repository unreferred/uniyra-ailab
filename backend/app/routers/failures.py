"""Failure Lab endpoints: create, list, inspect, aggregate and reclassify."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import repository as repo
from ..deps import get_current_user
from ..models import FailureCreateRequest, FailureUpdateRequest
from ..services import context as context_service
from ..services import failures as failure_service
from ..services import tools as tool_service
from ..services.failures import FailureError

router = APIRouter(prefix="/failures", tags=["failures"])


def _http_error(err: FailureError) -> HTTPException:
    return HTTPException(
        status_code=getattr(err, "status_code", None) or 400,
        detail=getattr(err, "message", None) or str(err),
    )


def _safe_run(run: Optional[dict]) -> Optional[dict]:
    if not isinstance(run, dict):
        return None
    return {
        key: value
        for key, value in run.items()
        if key not in ("system_prompt", "messages", "raw_response")
    }


def _safe_trace(trace: Optional[dict]) -> Optional[dict]:
    if not isinstance(trace, dict):
        return None
    return {key: value for key, value in trace.items() if key != "blind_map"}


@router.get("/aggregate")
async def aggregate_failures(
    project_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    try:
        return await failure_service.aggregate_failures(
            user["id"], project_id=project_id, date_from=date_from, date_to=date_to
        )
    except FailureError as exc:
        raise _http_error(exc)


@router.get("")
async def list_failures(
    project_id: Optional[str] = None,
    category: Optional[str] = None,
    source: Optional[str] = None,
    status: Optional[str] = None,
    failure_class: Optional[str] = None,
    failure_domain: Optional[str] = None,
    model_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    tool_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    return await failure_service.list_failures(
        user["id"],
        project_id=project_id,
        category=category,
        source=source,
        status=status,
        failure_class=failure_class,
        failure_domain=failure_domain,
        model_id=model_id,
        experiment_id=experiment_id,
        run_id=run_id,
        trace_id=trace_id,
        tool_id=tool_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


async def _failure_detail(owner_id: str, failure: Dict[str, Any]) -> Dict[str, Any]:
    run = None
    trace = None
    context_available = False
    evaluation = None
    tool_calls: List[dict] = []

    run_id = failure.get("run_id")
    if run_id:
        try:
            run = await repo.runs.get(run_id, owner_id)
        except Exception:  # pragma: no cover - defensive
            run = None
        try:
            context_available = (
                await context_service.get_context_snapshot(owner_id, run_id)
            ) is not None
        except Exception:  # pragma: no cover - defensive
            context_available = False
        try:
            tool_calls = await tool_service.list_tool_calls(owner_id, run_id=run_id)
        except Exception:  # pragma: no cover - defensive
            tool_calls = []

    trace_id = failure.get("trace_id")
    if trace_id:
        try:
            trace = await repo.traces.get(trace_id, owner_id)
        except Exception:  # pragma: no cover - defensive
            trace = None

    evaluation_id = failure.get("evaluation_id")
    if evaluation_id:
        try:
            evaluation = await repo.evaluations.find_one(
                {"owner_id": owner_id, "id": evaluation_id}
            )
        except Exception:  # pragma: no cover - defensive
            evaluation = None

    return {
        "failure": failure,
        "run": _safe_run(run),
        "trace": _safe_trace(trace),
        "context_available": context_available,
        "evaluation": evaluation,
        "tool_calls": list(tool_calls or []),
    }


@router.get("/{failure_id}")
async def get_failure_detail(failure_id: str, user: dict = Depends(get_current_user)):
    try:
        failure = await failure_service.get_failure(user["id"], failure_id)
    except FailureError as exc:
        raise _http_error(exc)
    return await _failure_detail(user["id"], failure)


@router.post("")
async def create_failure(
    body: FailureCreateRequest,
    project_id: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    owner_id = user["id"]
    resolved_project = project_id
    if body.run_id:
        run = await repo.runs.get(body.run_id, owner_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        resolved_project = run.get("project_id") or resolved_project
    else:
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail="project_id is required when run_id is not provided",
            )
        if not await repo.projects.get(project_id, owner_id):
            raise HTTPException(status_code=400, detail="Project not found")
        resolved_project = project_id
    try:
        return await failure_service.create_failure(
            owner_id,
            resolved_project,
            category=body.category,
            source=body.source,
            subcategory=body.subcategory,
            severity=body.severity,
            title=body.title,
            description=body.description,
            run_id=body.run_id,
            trace_id=body.trace_id,
            evaluation_id=body.evaluation_id,
            evidence=body.evidence,
            confidence=body.confidence,
            tags=body.tags,
            dataset_case_id=body.dataset_case_id,
            span_id=body.span_id,
            finding_id=body.finding_id,
            taxonomy_code=body.taxonomy_code,
            signals=body.signals,
        )
    except FailureError as exc:
        raise _http_error(exc)


@router.patch("/{failure_id}")
async def update_failure(
    failure_id: str,
    body: FailureUpdateRequest,
    user: dict = Depends(get_current_user),
):
    data = body.model_dump(exclude_unset=True)
    note = data.pop("note", "") or ""
    try:
        return await failure_service.reclassify_failure(
            user["id"],
            failure_id,
            category=data.get("category"),
            subcategory=data.get("subcategory"),
            source=data.get("source"),
            severity=data.get("severity"),
            status=data.get("status"),
            title=data.get("title"),
            description=data.get("description"),
            note=note,
            actor=user.get("email") or "human",
        )
    except FailureError as exc:
        raise _http_error(exc)
