"""V5 evaluation read + finding-promotion endpoints.

Every path is owner-scoped: evaluation detail, filtered listing and promoting a
persisted evaluation finding into a FailureRecord. Domain validation errors map
to HTTP 400 and missing owner-scoped records map to HTTP 404.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import repository as repo
from ..deps import get_current_user
from ..services import evaluation as evaluation_service
from ..services.evaluation import EvaluationError, EvaluationNotFound

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


class PromoteFindingRequest(BaseModel):
    finding_index: int
    title: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    category: Optional[str] = None


@router.get("")
async def list_evaluations(
    run_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    owner_id = user["id"]
    query = {"owner_id": owner_id}
    if run_id:
        if not await repo.runs.get(run_id, owner_id):
            raise HTTPException(status_code=404, detail="Run not found")
        query["run_id"] = run_id
    if experiment_id:
        if not await repo.experiments.get(experiment_id, owner_id):
            raise HTTPException(status_code=404, detail="Experiment not found")
        query["experiment_id"] = experiment_id
    if project_id:
        if not await repo.projects.get(project_id, owner_id):
            raise HTTPException(status_code=404, detail="Project not found")
        query["project_id"] = project_id

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    total = await repo.evaluations.count(query)
    items = await repo.evaluations.list(
        query, sort_field="created_at", descending=True, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/{evaluation_id}")
async def get_evaluation(evaluation_id: str, user: dict = Depends(get_current_user)):
    try:
        return await evaluation_service.get_evaluation(user["id"], evaluation_id)
    except EvaluationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except EvaluationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{evaluation_id}/findings")
async def promote_finding(
    evaluation_id: str,
    body: PromoteFindingRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await evaluation_service.promote_finding(
            user["id"], evaluation_id, body.model_dump()
        )
    except EvaluationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except EvaluationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
