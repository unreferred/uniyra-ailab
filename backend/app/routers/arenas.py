"""V6.8 Agent Arena & Prompt Arena HTTP surface.

Routes live under ``/arenas`` and only marshal bodies onto
:mod:`app.services.arena_v6`; the service builds ordinary protocol experiments
that reuse the one benchmark runner and evaluation funnel. Domain errors map to
HTTP: :class:`ArenaV6NotFound` -> 404, :class:`ArenaV6Error` -> 400.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import AgentArenaCreate, PromptArenaCreate
from ..services import arena_v6 as service
from ..services.arena_v6 import ArenaV6Error, ArenaV6NotFound

router = APIRouter(prefix="/arenas", tags=["arenas"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: ArenaV6Error) -> HTTPException:
    if isinstance(exc, ArenaV6NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/catalog")
async def arena_catalog():
    return service.catalog()


@router.post("/agent")
async def create_agent_arena(
    body: AgentArenaCreate, user: dict = Depends(get_current_user)
):
    try:
        return await service.create_agent_arena(_owner(user), body.model_dump())
    except ArenaV6Error as exc:
        raise _translate(exc)


@router.post("/prompt")
async def create_prompt_arena(
    body: PromptArenaCreate, user: dict = Depends(get_current_user)
):
    try:
        return await service.create_prompt_arena(_owner(user), body.model_dump())
    except ArenaV6Error as exc:
        raise _translate(exc)


@router.get("")
async def list_arenas(
    project_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.list_arenas(
            _owner(user), project_id=project_id, limit=limit, offset=offset
        )
    except ArenaV6Error as exc:
        raise _translate(exc)


@router.get("/{experiment_id}")
async def get_arena(
    experiment_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await service.arena_summary(_owner(user), experiment_id)
    except ArenaV6Error as exc:
        raise _translate(exc)
