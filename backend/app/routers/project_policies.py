"""V6.0 project-scoped Project Policy endpoints.

Policies are immutable by version: a new version is a new document that
supersedes the previous one. Every lookup is owner-scoped: a missing or foreign
policy is 404 and a domain/validation failure (including a foreign project) is
400.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import (
    POLICY_STATUSES,
    ProjectPolicyCreate,
    ProjectPolicyUpdate,
    ProjectPolicyVersionCreate,
)
from ..services import prompt_assets as service
from ..services.prompt_assets import (
    POLICY_DOMAIN_CATALOG,
    PromptAssetError,
    PromptAssetNotFound,
)

router = APIRouter(prefix="/project-policies", tags=["project-policies"])


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
async def project_policy_catalog(user: dict = Depends(get_current_user)):
    return {
        "domains": [dict(entry) for entry in POLICY_DOMAIN_CATALOG],
        "statuses": list(POLICY_STATUSES),
    }


@router.get("")
async def list_project_policies(
    project_id: str,
    domain: Optional[str] = None,
    include_versions: bool = False,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.list_policies(
            _owner(user), project_id, domain=domain, include_versions=include_versions
        )
    except PromptAssetError as exc:
        raise _translate(exc)


@router.post("")
async def create_project_policy(body: ProjectPolicyCreate, user: dict = Depends(get_current_user)):
    try:
        return await service.create_policy(_owner(user), body.model_dump())
    except PromptAssetError as exc:
        raise _translate(exc)


@router.get("/{policy_id}/versions")
async def list_project_policy_versions(policy_id: str, user: dict = Depends(get_current_user)):
    try:
        return await service.list_policy_versions(_owner(user), policy_id)
    except PromptAssetError as exc:
        raise _translate(exc)


@router.post("/{policy_id}/versions")
async def create_project_policy_version(
    policy_id: str,
    body: ProjectPolicyVersionCreate,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.create_policy_version(
            _owner(user), policy_id, body.content, body.description
        )
    except PromptAssetError as exc:
        raise _translate(exc)


@router.get("/{policy_id}")
async def get_project_policy(policy_id: str, user: dict = Depends(get_current_user)):
    try:
        return await service.get_policy(_owner(user), policy_id)
    except PromptAssetError as exc:
        raise _translate(exc)


@router.patch("/{policy_id}")
async def update_project_policy(
    policy_id: str, body: ProjectPolicyUpdate, user: dict = Depends(get_current_user)
):
    try:
        return await service.update_policy_status(_owner(user), policy_id, body.status)
    except PromptAssetError as exc:
        raise _translate(exc)
