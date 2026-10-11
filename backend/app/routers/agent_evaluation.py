"""V6.7 Agent & Prompt Evaluation HTTP surface.

Routes live under ``/agent-evaluation``. All work is delegated to
:mod:`app.services.agent_evaluation`, which reuses the canonical V5 evaluation
framework. This router only maps bodies onto service calls and translates
domain errors (:class:`AgentEvaluationNotFound` -> 404,
:class:`AgentEvaluationError` -> 400).
"""
from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import AgentEvaluationRequest
from ..services import agent_evaluation as service
from ..services.agent_evaluation import AgentEvaluationError, AgentEvaluationNotFound

router = APIRouter(prefix="/agent-evaluation", tags=["agent-evaluation"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: AgentEvaluationError) -> HTTPException:
    if isinstance(exc, AgentEvaluationNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/catalog")
async def catalog():
    return await service.catalog()


@router.post("/runs/{run_id}")
async def evaluate_agent_run(
    run_id: str,
    body: AgentEvaluationRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.evaluate_agent_run(_owner(user), run_id, body.model_dump())
    except AgentEvaluationError as exc:
        raise _translate(exc)


@router.get("/runs/{run_id}")
async def list_run_agent_evaluations(
    run_id: str,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.list_run_agent_evaluations(
            _owner(user), run_id, limit=limit, offset=offset
        )
    except AgentEvaluationError as exc:
        raise _translate(exc)
