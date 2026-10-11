"""V6.15 PRD readiness / Living PRD HTTP surface.

Routes live under ``/planning-state`` next to the decision ledger and planning
graph. Work is delegated to :mod:`app.services.planning_prd`; this router only
maps query params/bodies onto service calls and translates domain errors
(:class:`PlanningPrdNotFound` -> 404, :class:`PlanningPrdError` -> 400). Static
routes are declared before the dynamic ``/prd/versions/{version_id}`` route, and
``/prd/compare`` is declared before it as well, so none is shadowed.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import PrdApproveRequest, PrdGenerateRequest
from ..services import planning_prd as service
from ..services.planning_prd import PlanningPrdError, PlanningPrdNotFound

router = APIRouter(prefix="/planning-state", tags=["planning-prd"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: PlanningPrdError) -> HTTPException:
    if isinstance(exc, PlanningPrdNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/readiness")
async def get_prd_readiness(
    project_id: str,
    conversation_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.get_readiness(
            _owner(user), project_id, conversation_id=conversation_id
        )
    except PlanningPrdError as exc:
        raise _translate(exc)


@router.get("/prd/catalog")
async def planning_prd_catalog(user: dict = Depends(get_current_user)):
    return service.catalog()


@router.post("/prd/generate")
async def generate_prd(
    body: PrdGenerateRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.compile_prd(
            _owner(user), body.project_id, body.conversation_id
        )
    except PlanningPrdError as exc:
        raise _translate(exc)


@router.get("/prd/versions")
async def list_prd_versions(
    project_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await service.list_prd_versions(_owner(user), project_id)
    except PlanningPrdError as exc:
        raise _translate(exc)


# NOTE: ``/prd/compare`` is declared before the dynamic ``/prd/versions/{...}``.
@router.get("/prd/compare")
async def compare_prd_versions(
    project_id: str,
    from_version_id: str,
    to_version_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.compare_prd_versions(
            _owner(user), project_id, from_version_id, to_version_id
        )
    except PlanningPrdError as exc:
        raise _translate(exc)


@router.get("/prd/versions/{version_id}")
async def get_prd_version(
    version_id: str,
    project_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.get_prd_version(
            _owner(user), version_id, project_id=project_id
        )
    except PlanningPrdError as exc:
        raise _translate(exc)


@router.post("/prd/versions/{version_id}/approve")
async def approve_prd_version(
    version_id: str,
    body: PrdApproveRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.approve_prd_version(
            _owner(user), version_id, project_id=body.project_id
        )
    except PlanningPrdError as exc:
        raise _translate(exc)
