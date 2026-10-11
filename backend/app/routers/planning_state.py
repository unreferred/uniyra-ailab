"""V6.14 Planning Decision Memory HTTP surface.

Routes live under ``/planning-state``. All work is delegated to
:mod:`app.services.planning_state`; this router only maps bodies onto service
calls and translates domain errors (:class:`PlanningStateNotFound` -> 404,
:class:`PlanningStateError` -> 400). Static routes are declared before the
dynamic ``/{item_id}`` and ``/conflicts/{conflict_id}`` routes so ``/catalog``,
``/check-questions`` and ``/conflicts`` are never shadowed.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import (
    ConsolidatedPlanRequest,
    PlanningConflictResolve,
    PlanningIngestRequest,
    PlanningItemCreate,
    PlanningItemUpdate,
    PlanningQuestionsRequest,
    PlanningVerifyRequest,
)
from ..services import planning_state as service
from ..services.planning_state import PlanningStateError, PlanningStateNotFound

router = APIRouter(prefix="/planning-state", tags=["planning-state"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: PlanningStateError) -> HTTPException:
    if isinstance(exc, PlanningStateNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/catalog")
async def planning_state_catalog(user: dict = Depends(get_current_user)):
    return service.catalog()


@router.post("/items")
async def create_planning_item(
    body: PlanningItemCreate, user: dict = Depends(get_current_user)
):
    try:
        return await service.create_item(
            _owner(user), body.project_id, body.conversation_id, body.model_dump()
        )
    except PlanningStateError as exc:
        raise _translate(exc)


@router.post("/check-questions")
async def check_planning_questions(
    body: PlanningQuestionsRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.check_questions(
            _owner(user), body.project_id, body.conversation_id, body.questions
        )
    except PlanningStateError as exc:
        raise _translate(exc)


@router.post("/conversations/{conversation_id}/consolidated-plan")
async def consolidated_planning_plan(
    conversation_id: str,
    body: ConsolidatedPlanRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.consolidated_plan(
            _owner(user),
            body.project_id,
            conversation_id,
            include_policy_ids=body.include_policy_ids,
            include_invariant_ids=body.include_invariant_ids,
        )
    except PlanningStateError as exc:
        raise _translate(exc)


@router.post("/conversations/{conversation_id}/ingest")
async def ingest_planning_conversation(
    conversation_id: str,
    body: PlanningIngestRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.ingest_conversation(
            _owner(user), body.project_id, conversation_id, body.message_ids
        )
    except PlanningStateError as exc:
        raise _translate(exc)


@router.get("/conflicts")
async def list_planning_conflicts(
    project_id: str,
    conversation_id: str,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.list_conflicts(
            _owner(user), project_id, conversation_id, status=status
        )
    except PlanningStateError as exc:
        raise _translate(exc)


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve_planning_conflict(
    conflict_id: str,
    body: PlanningConflictResolve,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.resolve_conflict(
            _owner(user), conflict_id, body.resolution, body.value
        )
    except PlanningStateError as exc:
        raise _translate(exc)


# NOTE: static routes above precede these dynamic ``/items/{item_id}`` routes.
@router.get("")
async def get_planning_state(
    project_id: str,
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.get_state(_owner(user), project_id, conversation_id)
    except PlanningStateError as exc:
        raise _translate(exc)


@router.get("/items/{item_id}")
async def get_planning_item(item_id: str, user: dict = Depends(get_current_user)):
    try:
        return await service.get_item(_owner(user), item_id)
    except PlanningStateError as exc:
        raise _translate(exc)


@router.patch("/items/{item_id}")
async def update_planning_item(
    item_id: str, body: PlanningItemUpdate, user: dict = Depends(get_current_user)
):
    try:
        return await service.update_item(
            _owner(user), item_id, body.model_dump(exclude_unset=True)
        )
    except PlanningStateError as exc:
        raise _translate(exc)


@router.post("/items/{item_id}/reopen")
async def reopen_planning_item(item_id: str, user: dict = Depends(get_current_user)):
    try:
        return await service.reopen_item(_owner(user), item_id)
    except PlanningStateError as exc:
        raise _translate(exc)


@router.post("/items/{item_id}/verify")
async def verify_planning_item(
    item_id: str, body: PlanningVerifyRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.verify_item(
            _owner(user), item_id, body.verification_status, body.evidence
        )
    except PlanningStateError as exc:
        raise _translate(exc)
