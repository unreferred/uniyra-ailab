"""V6.0 project-scoped Project Invariant endpoints.

Invariants are owner+project scoped rules with a unique active code per project.
Every lookup is owner-scoped: a missing or foreign invariant is 404 and a
domain/validation/duplicate-code failure (including a foreign project) is 400.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import (
    INVARIANT_ENFORCEMENT_MODES,
    INVARIANT_SEVERITIES,
    INVARIANT_STATUSES,
    ProjectInvariantCreate,
    ProjectInvariantUpdate,
)
from ..services import prompt_assets as service
from ..services.prompt_assets import (
    POLICY_DOMAIN_CATALOG,
    PromptAssetError,
    PromptAssetNotFound,
)

router = APIRouter(prefix="/project-invariants", tags=["project-invariants"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: PromptAssetError) -> HTTPException:
    if isinstance(exc, PromptAssetNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/catalog")
async def project_invariant_catalog(user: dict = Depends(get_current_user)):
    return {
        "domains": [dict(entry) for entry in POLICY_DOMAIN_CATALOG],
        "severities": list(INVARIANT_SEVERITIES),
        "enforcement_modes": list(INVARIANT_ENFORCEMENT_MODES),
        "statuses": list(INVARIANT_STATUSES),
    }


@router.get("")
async def list_project_invariants(
    project_id: str,
    domain: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.list_invariants(
            _owner(user), project_id, domain=domain, status=status
        )
    except PromptAssetError as exc:
        raise _translate(exc)


@router.post("")
async def create_project_invariant(
    body: ProjectInvariantCreate, user: dict = Depends(get_current_user)
):
    try:
        return await service.create_invariant(_owner(user), body.model_dump())
    except PromptAssetError as exc:
        raise _translate(exc)


@router.get("/{invariant_id}")
async def get_project_invariant(invariant_id: str, user: dict = Depends(get_current_user)):
    try:
        return await service.get_invariant(_owner(user), invariant_id)
    except PromptAssetError as exc:
        raise _translate(exc)


@router.patch("/{invariant_id}")
async def update_project_invariant(
    invariant_id: str,
    body: ProjectInvariantUpdate,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.update_invariant(
            _owner(user), invariant_id, body.model_dump(exclude_unset=True)
        )
    except PromptAssetError as exc:
        raise _translate(exc)


@router.delete("/{invariant_id}")
async def delete_project_invariant(invariant_id: str, user: dict = Depends(get_current_user)):
    try:
        await service.delete_invariant(_owner(user), invariant_id)
    except PromptAssetError as exc:
        raise _translate(exc)
    return {"ok": True}
