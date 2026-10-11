"""V6.4 Agent Registry HTTP surface.

Routes live under ``/agents``. All work is delegated to
:mod:`app.services.agents`; this router only maps bodies onto the service calls
and translates domain errors (:class:`AgentNotFound` -> 404,
:class:`AgentError` -> 400). Static routes (``/catalog``) and the
``.../versions/compare`` route are declared before their dynamic siblings so
they are never shadowed.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import (
    AgentCreate,
    AgentUpdate,
    AgentVersionCreate,
    AgentVersionUpdate,
)
from ..services import agents as agents_service
from ..services.agents import AgentError, AgentNotFound

router = APIRouter(prefix="/agents", tags=["agents"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: AgentError) -> HTTPException:
    if isinstance(exc, AgentNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# ------------------------------------------------------------------ catalog
@router.get("/catalog")
async def agents_catalog(user: dict = Depends(get_current_user)):
    return agents_service.catalog()


# ------------------------------------------------------------------ agents
@router.get("")
async def list_agents(
    project_id: str,
    include_archived: bool = False,
    user: dict = Depends(get_current_user),
):
    try:
        return await agents_service.list_agents(
            _owner(user), project_id, include_archived=include_archived
        )
    except AgentError as exc:
        raise _translate(exc)


@router.post("")
async def create_agent(body: AgentCreate, user: dict = Depends(get_current_user)):
    try:
        return await agents_service.create_agent(_owner(user), body.model_dump())
    except AgentError as exc:
        raise _translate(exc)


@router.get("/{agent_id}")
async def get_agent(agent_id: str, user: dict = Depends(get_current_user)):
    try:
        return await agents_service.get_agent(_owner(user), agent_id)
    except AgentError as exc:
        raise _translate(exc)


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: str, body: AgentUpdate, user: dict = Depends(get_current_user)
):
    try:
        return await agents_service.update_agent(
            _owner(user), agent_id, body.model_dump(exclude_unset=True)
        )
    except AgentError as exc:
        raise _translate(exc)


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, user: dict = Depends(get_current_user)):
    try:
        await agents_service.delete_agent(_owner(user), agent_id)
        return {"ok": True}
    except AgentError as exc:
        raise _translate(exc)


# ------------------------------------------------------------------ versions
@router.get("/{agent_id}/versions")
async def list_agent_versions(
    agent_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await agents_service.list_versions(_owner(user), agent_id)
    except AgentError as exc:
        raise _translate(exc)


@router.post("/{agent_id}/versions")
async def create_agent_version(
    agent_id: str,
    body: AgentVersionCreate,
    user: dict = Depends(get_current_user),
):
    try:
        agent = await agents_service.get_agent(_owner(user), agent_id)
        return await agents_service.create_version(
            _owner(user), agent, body.model_dump()
        )
    except AgentError as exc:
        raise _translate(exc)


# NOTE: declared before ``/versions/{version_id}`` or it would be shadowed.
@router.get("/{agent_id}/versions/compare")
async def compare_agent_versions(
    agent_id: str,
    from_version_id: str,
    to_version_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        agent = await agents_service.get_agent(_owner(user), agent_id)
        return await agents_service.compare_versions(
            _owner(user), agent, from_version_id, to_version_id
        )
    except AgentError as exc:
        raise _translate(exc)


@router.get("/{agent_id}/versions/{version_id}")
async def get_agent_version(
    agent_id: str, version_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await agents_service.get_version(_owner(user), agent_id, version_id)
    except AgentError as exc:
        raise _translate(exc)


@router.patch("/{agent_id}/versions/{version_id}")
async def update_agent_version(
    agent_id: str,
    version_id: str,
    body: AgentVersionUpdate,
    user: dict = Depends(get_current_user),
):
    try:
        agent = await agents_service.get_agent(_owner(user), agent_id)
        return await agents_service.update_draft(
            _owner(user), agent, version_id, body.model_dump(exclude_unset=True)
        )
    except AgentError as exc:
        raise _translate(exc)


@router.post("/{agent_id}/versions/{version_id}/freeze")
async def freeze_agent_version(
    agent_id: str, version_id: str, user: dict = Depends(get_current_user)
):
    try:
        agent = await agents_service.get_agent(_owner(user), agent_id)
        return await agents_service.freeze_version(_owner(user), agent, version_id)
    except AgentError as exc:
        raise _translate(exc)


@router.post("/{agent_id}/versions/{version_id}/deprecate")
async def deprecate_agent_version(
    agent_id: str, version_id: str, user: dict = Depends(get_current_user)
):
    try:
        agent = await agents_service.get_agent(_owner(user), agent_id)
        return await agents_service.deprecate_version(_owner(user), agent, version_id)
    except AgentError as exc:
        raise _translate(exc)


@router.post("/{agent_id}/versions/{version_id}/clone")
async def clone_agent_version(
    agent_id: str, version_id: str, user: dict = Depends(get_current_user)
):
    try:
        agent = await agents_service.get_agent(_owner(user), agent_id)
        return await agents_service.clone_version(_owner(user), agent, version_id)
    except AgentError as exc:
        raise _translate(exc)


@router.get("/{agent_id}/versions/{version_id}/usage")
async def agent_version_usage(
    agent_id: str, version_id: str, user: dict = Depends(get_current_user)
):
    try:
        agent = await agents_service.get_agent(_owner(user), agent_id)
        return await agents_service.usage(_owner(user), agent, version_id)
    except AgentError as exc:
        raise _translate(exc)
