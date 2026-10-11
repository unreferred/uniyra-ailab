"""Prompt Registry: named prompts with append-only, draft/frozen versions.

Every read/write is scoped by owner and (where present) project. Versions are
append-only: creating version N+1 never mutates version N. A version starts as
``draft``, is frozen exactly once into an immutable snapshot, and may later be
deprecated. Experiment configurations may reference a ``prompt_version_id``;
only frozen (or legacy statusless) versions may be pinned, and the resolved
content is snapshotted onto the configuration so later edits can never change a
frozen experiment.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import (
    PROMPT_SPEC_STATUSES,
    PromptCreate,
    PromptUpdate,
    PromptVersionCreate,
    PromptVersionUpdate,
    new_id,
    now_iso,
)
from ..services import prompt_registry as registry
from ..services.prompt_registry import PromptRegistryError, PromptRegistryNotFound

router = APIRouter(prefix="/prompts", tags=["prompts"])


def _translate(exc: PromptRegistryError) -> HTTPException:
    if isinstance(exc, PromptRegistryNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


async def _load_prompt(prompt_id: str, owner_id: str) -> Dict[str, Any]:
    prompt = await repo.prompts.get(prompt_id, owner_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return prompt


@router.get("")
async def list_prompts(project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query: Dict[str, Any] = {"owner_id": user["id"]}
    if project_id:
        query["project_id"] = project_id
    return await repo.prompts.list(query, sort_field="updated_at")


@router.post("")
async def create_prompt(body: PromptCreate, user: dict = Depends(get_current_user)):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    if body.status not in PROMPT_SPEC_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    now = now_iso()
    prompt = {
        "id": new_id(), "owner_id": user["id"], "project_id": body.project_id,
        "name": body.name, "description": body.description,
        "purpose": body.purpose, "status": body.status,
        "created_by": user["id"],
        "created_at": now, "updated_at": now,
    }
    await repo.prompts.insert(prompt)
    return prompt


@router.get("/{prompt_id}")
async def get_prompt(prompt_id: str, user: dict = Depends(get_current_user)):
    prompt = await _load_prompt(prompt_id, user["id"])
    count = await repo.prompt_versions.count({"owner_id": user["id"], "prompt_id": prompt_id})
    # Legacy prompt records predate the V6 spec fields; surface their defaults
    # rather than omitting the keys.
    return {
        **prompt,
        "purpose": prompt.get("purpose", ""),
        "status": prompt.get("status", "active"),
        "created_by": prompt.get("created_by", prompt.get("owner_id")),
        "versions_count": count,
    }


@router.patch("/{prompt_id}")
async def update_prompt(prompt_id: str, body: PromptUpdate, user: dict = Depends(get_current_user)):
    await _load_prompt(prompt_id, user["id"])
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "status" in patch and patch["status"] not in PROMPT_SPEC_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    patch["updated_at"] = now_iso()
    return await repo.prompts.update(prompt_id, user["id"], patch)


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: str, user: dict = Depends(get_current_user)):
    await _load_prompt(prompt_id, user["id"])
    await repo.prompt_versions.delete_many({"owner_id": user["id"], "prompt_id": prompt_id})
    await repo.prompts.delete(prompt_id, user["id"])
    return {"ok": True}


# ---------------------------------------------------------------- versions
@router.get("/{prompt_id}/versions")
async def list_prompt_versions(prompt_id: str, user: dict = Depends(get_current_user)):
    try:
        return await registry.list_versions(user["id"], prompt_id)
    except PromptRegistryError as exc:
        raise _translate(exc)


@router.post("/{prompt_id}/versions")
async def create_prompt_version(prompt_id: str, body: PromptVersionCreate,
                                user: dict = Depends(get_current_user)):
    try:
        prompt = await registry._require_prompt(user["id"], prompt_id)
        return await registry.create_version(user["id"], prompt, body.model_dump())
    except PromptRegistryError as exc:
        raise _translate(exc)


# NOTE: the static ``/versions/compare`` route MUST be declared before the
# dynamic ``/versions/{version_id}`` route or it would be shadowed by it.
@router.get("/{prompt_id}/versions/compare")
async def compare_prompt_versions(
    prompt_id: str,
    from_version_id: str,
    to_version_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        prompt = await registry._require_prompt(user["id"], prompt_id)
        return await registry.compare_versions(
            user["id"], prompt, from_version_id, to_version_id)
    except PromptRegistryError as exc:
        raise _translate(exc)


@router.get("/{prompt_id}/versions/{version_id}")
async def get_prompt_version(prompt_id: str, version_id: str,
                             user: dict = Depends(get_current_user)):
    try:
        return await registry.get_version(user["id"], prompt_id, version_id)
    except PromptRegistryError as exc:
        raise _translate(exc)


@router.patch("/{prompt_id}/versions/{version_id}")
async def update_prompt_version(prompt_id: str, version_id: str,
                                body: PromptVersionUpdate,
                                user: dict = Depends(get_current_user)):
    try:
        prompt = await registry._require_prompt(user["id"], prompt_id)
        return await registry.update_draft(
            user["id"], prompt, version_id, body.model_dump(exclude_unset=True))
    except PromptRegistryError as exc:
        raise _translate(exc)


@router.post("/{prompt_id}/versions/{version_id}/freeze")
async def freeze_prompt_version(prompt_id: str, version_id: str,
                                user: dict = Depends(get_current_user)):
    try:
        prompt = await registry._require_prompt(user["id"], prompt_id)
        return await registry.freeze_version(user["id"], prompt, version_id)
    except PromptRegistryError as exc:
        raise _translate(exc)


@router.post("/{prompt_id}/versions/{version_id}/deprecate")
async def deprecate_prompt_version(prompt_id: str, version_id: str,
                                   user: dict = Depends(get_current_user)):
    try:
        prompt = await registry._require_prompt(user["id"], prompt_id)
        return await registry.deprecate_version(user["id"], prompt, version_id)
    except PromptRegistryError as exc:
        raise _translate(exc)


@router.post("/{prompt_id}/versions/{version_id}/clone")
async def clone_prompt_version(prompt_id: str, version_id: str,
                               user: dict = Depends(get_current_user)):
    try:
        prompt = await registry._require_prompt(user["id"], prompt_id)
        return await registry.clone_version(
            user["id"], prompt, version_id, restore=False)
    except PromptRegistryError as exc:
        raise _translate(exc)


@router.post("/{prompt_id}/versions/{version_id}/restore")
async def restore_prompt_version(prompt_id: str, version_id: str,
                                 user: dict = Depends(get_current_user)):
    try:
        prompt = await registry._require_prompt(user["id"], prompt_id)
        return await registry.clone_version(
            user["id"], prompt, version_id, restore=True)
    except PromptRegistryError as exc:
        raise _translate(exc)


@router.get("/{prompt_id}/usage")
async def prompt_usage(prompt_id: str, user: dict = Depends(get_current_user)):
    try:
        prompt = await registry._require_prompt(user["id"], prompt_id)
        return await registry.usage(user["id"], prompt)
    except PromptRegistryError as exc:
        raise _translate(exc)
