"""Tool credential vault endpoints (Custom Tool Platform).

Every response is a public view ({id, owner_id, project_id, name,
credential_type, has_value, metadata, created_at, updated_at}); secret
values are never returned. Public dicts are constructed explicitly so an
``encrypted_value`` can never leak through.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import ToolCredentialCreate, ToolCredentialUpdate
from ..services import tool_credentials as cred_service
from ..services.tool_credentials import CredentialError

router = APIRouter(prefix="/tool-credentials", tags=["tool-credentials"])


def _http_error(err: CredentialError) -> HTTPException:
    return HTTPException(
        status_code=getattr(err, "status_code", None) or 400,
        detail=getattr(err, "message", None) or str(err),
    )


def _public_or_drop(doc: dict) -> dict:
    view = {
        "id": doc.get("id"),
        "owner_id": doc.get("owner_id"),
        "project_id": doc.get("project_id"),
        "name": doc.get("name"),
        "credential_type": doc.get("credential_type"),
        "has_value": True,
        "metadata": dict(doc.get("metadata") or {}),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }
    if "encrypted_value" in view or "value" in view:
        view.pop("encrypted_value", None)
        view.pop("value", None)
    return view


@router.get("")
async def list_credentials(
    project_id: str, user: dict = Depends(get_current_user)
):
    if not await repo.projects.get(project_id, user["id"]):
        raise HTTPException(status_code=404, detail="Project not found")
    items = await cred_service.list_credentials(user["id"], project_id)
    return {"items": [_public_or_drop(item) for item in items]}


@router.post("")
async def create_credential(
    body: ToolCredentialCreate, user: dict = Depends(get_current_user)
):
    try:
        created = await cred_service.create_credential(
            user["id"],
            body.project_id,
            body.name,
            body.credential_type,
            body.value,
            dict(body.metadata or {}),
        )
    except CredentialError as exc:
        raise _http_error(exc)
    return _public_or_drop(created)


@router.get("/{cred_id}")
async def get_credential(
    cred_id: str,
    project_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    view = await cred_service.get_credential(user["id"], cred_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    if project_id is not None and view.get("project_id") != project_id:
        raise HTTPException(status_code=403, detail="Credential does not belong to this project")
    return _public_or_drop(view)


@router.patch("/{cred_id}")
async def update_credential(
    cred_id: str, body: ToolCredentialUpdate, user: dict = Depends(get_current_user)
):
    try:
        updated = await cred_service.update_credential(
            user["id"], cred_id, body.model_dump(exclude_unset=True)
        )
    except CredentialError as exc:
        raise _http_error(exc)
    return _public_or_drop(updated)


@router.delete("/{cred_id}")
async def delete_credential(cred_id: str, user: dict = Depends(get_current_user)):
    try:
        return await cred_service.delete_credential(user["id"], cred_id)
    except CredentialError as exc:
        raise _http_error(exc)
