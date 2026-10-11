"""V5.7 red team endpoints.

Owner-scoped red-team fixture materialization, experiment creation over frozen
dataset versions, execution through the shared benchmark runner, listing, the
static fixture catalog and per-case boundary summaries. Domain validation maps
to HTTP 400 and missing owner-scoped records map to HTTP 404.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import repository as repo
from ..deps import get_current_user
from ..services import red_team as red_team_service
from ..services import red_team_fixtures as fixtures_service
from ..services.red_team import RedTeamError, RedTeamNotFound

router = APIRouter(prefix="/red-team", tags=["red-team"])


class RedTeamFixtureRequest(BaseModel):
    project_id: str
    name: Optional[str] = None
    model_ids: Optional[List[str]] = None
    include: Optional[List[str]] = None


class RedTeamExperimentRequest(BaseModel):
    project_id: str
    dataset_id: str
    dataset_version_id: str
    model_ids: Optional[List[str]] = None
    title: Optional[str] = None
    objective: str = ""
    repetitions: int = 1


@router.post("/fixtures")
async def create_red_team_fixtures(
    body: RedTeamFixtureRequest, user: dict = Depends(get_current_user)
):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    return await fixtures_service.materialize(
        user["id"],
        body.project_id,
        name=body.name,
        model_ids=body.model_ids,
        include=body.include,
    )


@router.post("/experiments")
async def create_red_team_experiment(
    body: RedTeamExperimentRequest, user: dict = Depends(get_current_user)
):
    try:
        return await red_team_service.create_red_team_experiment(
            user["id"],
            body.project_id,
            body.dataset_id,
            body.dataset_version_id,
            model_ids=body.model_ids,
            title=body.title,
            objective=body.objective,
            repetitions=body.repetitions,
        )
    except RedTeamNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RedTeamError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/experiments/{experiment_id}/execute")
async def execute_red_team_experiment(
    experiment_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await red_team_service.execute_red_team(user["id"], experiment_id)
    except RedTeamNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RedTeamError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("")
async def list_red_team_experiments(
    project_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    return await red_team_service.list_red_team_experiments(
        user["id"], project_id=project_id, limit=limit, offset=offset
    )


@router.get("/catalog")
async def red_team_catalog(user: dict = Depends(get_current_user)):
    return await red_team_service.red_team_fixture_catalog()


@router.get("/{experiment_id}")
async def get_red_team_experiment(
    experiment_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await red_team_service.red_team_summary(user["id"], experiment_id)
    except RedTeamNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RedTeamError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
