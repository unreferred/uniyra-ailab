"""V5.8 scorecard endpoints.

Read paths build scorecards live from run/evaluation/failure records; the POST
path persists an owner-scoped scorecard document. Every resource lookup is
owner-scoped: a missing record or a cross-project reference maps to 404 and a
domain validation failure maps to 400.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_current_user
from ..models import ScorecardBuildRequest
from ..services import scorecards as scorecard_service
from ..services.scorecards import ScorecardError, ScorecardNotFound

router = APIRouter(prefix="/scorecards", tags=["scorecards"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


@router.post("")
async def create_scorecard(
    body: ScorecardBuildRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await scorecard_service.create_scorecard(_owner(user), body.model_dump())
    except ScorecardNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScorecardError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("")
async def list_scorecards(
    project_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    try:
        return await scorecard_service.list_scorecards(
            _owner(user),
            project_id=project_id,
            experiment_id=experiment_id,
            run_id=run_id,
            limit=limit,
            offset=offset,
        )
    except ScorecardNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScorecardError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/run/{run_id}")
async def get_run_scorecard(
    run_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return await scorecard_service.build_run_scorecard(_owner(user), run_id)
    except ScorecardNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScorecardError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/experiment/{experiment_id}/compare")
async def get_comparison_scorecard(
    experiment_id: str,
    baseline_configuration_id: str = Query(...),
    candidate_configuration_id: str = Query(...),
    user: dict = Depends(get_current_user),
):
    try:
        return await scorecard_service.build_comparison_scorecard(
            _owner(user),
            experiment_id,
            baseline_configuration_id,
            candidate_configuration_id,
        )
    except ScorecardNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScorecardError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/experiment/{experiment_id}")
async def get_experiment_scorecard(
    experiment_id: str,
    configuration_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    try:
        return await scorecard_service.build_experiment_scorecard(
            _owner(user), experiment_id, configuration_id
        )
    except ScorecardNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScorecardError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{scorecard_id}")
async def get_scorecard(
    scorecard_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return await scorecard_service.get_scorecard(_owner(user), scorecard_id)
    except ScorecardNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ScorecardError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
