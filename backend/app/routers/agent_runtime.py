"""V6.5 Agent Runtime HTTP surface.

Routes live under ``/agent-runtime``. All work is delegated to
:mod:`app.services.agent_runtime`; this router only maps bodies onto service
calls and translates domain errors (:class:`AgentRuntimeNotFound` -> 404,
:class:`AgentRuntimeError` -> 400). The runtime itself reuses the canonical
``execute_model`` funnel.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import AgentRunRequest
from ..services import agent_runtime as service
from ..services.agent_runtime import AgentRuntimeError, AgentRuntimeNotFound

router = APIRouter(prefix="/agent-runtime", tags=["agent-runtime"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: AgentRuntimeError) -> HTTPException:
    if isinstance(exc, AgentRuntimeNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/run")
async def run_agent_version(
    body: AgentRunRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.run_agent_version(_owner(user), body.model_dump())
    except AgentRuntimeError as exc:
        raise _translate(exc)


@router.get("/runs")
async def list_agent_runs(
    agent_version_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.list_agent_runs(
            _owner(user),
            agent_version_id=agent_version_id,
            agent_id=agent_id,
            project_id=project_id,
            limit=limit,
            offset=offset,
        )
    except AgentRuntimeError as exc:
        raise _translate(exc)


@router.get("/runs/{run_id}")
async def get_agent_run(run_id: str, user: dict = Depends(get_current_user)):
    try:
        return await service.get_agent_run(_owner(user), run_id)
    except AgentRuntimeError as exc:
        raise _translate(exc)
