"""V6.15 Structured Planning Graph HTTP surface.

Routes live under ``/planning-state/graph`` plus ``/planning-state/impact``. The
graph is a derived view over the V6.14 decision ledger; this router only maps
bodies onto :mod:`app.services.planning_graph` calls and translates domain
errors (:class:`PlanningGraphNotFound` -> 404, :class:`PlanningGraphError` ->
400). Static routes are declared before the dynamic ``/graph/nodes/{node_id}``
route so ``/graph``, ``/graph/sync`` and ``/graph/catalog`` are never shadowed.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import (
    PlanningGraphSyncRequest,
    PlanningImpactRequest,
    PlanningReviewClear,
)
from ..services import planning_graph as service
from ..services.planning_graph import PlanningGraphError, PlanningGraphNotFound

router = APIRouter(prefix="/planning-state", tags=["planning-graph"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: PlanningGraphError) -> HTTPException:
    if isinstance(exc, PlanningGraphNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/graph")
async def get_planning_graph(
    project_id: str,
    conversation_id: Optional[str] = None,
    sync: bool = True,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.get_graph(
            _owner(user), project_id, conversation_id=conversation_id, sync=sync
        )
    except PlanningGraphError as exc:
        raise _translate(exc)


@router.post("/graph/sync")
async def sync_planning_graph(
    body: PlanningGraphSyncRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.sync_graph(
            _owner(user), body.project_id, conversation_id=body.conversation_id
        )
    except PlanningGraphError as exc:
        raise _translate(exc)


@router.get("/graph/catalog")
async def planning_graph_catalog(user: dict = Depends(get_current_user)):
    return service.catalog()


@router.post("/impact")
async def analyze_planning_impact(
    body: PlanningImpactRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.analyze_impact(
            _owner(user),
            body.project_id,
            decision_id=body.decision_id,
            node_id=body.node_id,
        )
    except PlanningGraphError as exc:
        raise _translate(exc)


# NOTE: static routes above precede the dynamic ``/graph/nodes/{node_id}`` route.
@router.get("/graph/nodes/{node_id}")
async def get_planning_graph_node(
    node_id: str,
    project_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.get_node(_owner(user), node_id, project_id=project_id)
    except PlanningGraphError as exc:
        raise _translate(exc)


@router.post("/graph/nodes/{node_id}/clear-review")
async def clear_planning_graph_review(
    node_id: str,
    body: PlanningReviewClear,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.clear_review(_owner(user), body.project_id, node_id)
    except PlanningGraphError as exc:
        raise _translate(exc)
