"""V6.0 project-scoped Prompt Block endpoints.

Blocks are reusable, categorized prompt fragments owned by an owner and scoped
to a single project. Every lookup is owner-scoped: a missing or foreign block is
404 and a domain/validation failure (including a foreign project) is 400.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import PROMPT_BLOCK_STATUSES, PromptBlockCreate, PromptBlockUpdate
from ..services import prompt_assets as service
from ..services.prompt_assets import (
    BLOCK_CATEGORY_CATALOG,
    PromptAssetError,
    PromptAssetNotFound,
)

router = APIRouter(prefix="/prompt-blocks", tags=["prompt-blocks"])


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
async def prompt_block_catalog(user: dict = Depends(get_current_user)):
    return {
        "categories": [dict(entry) for entry in BLOCK_CATEGORY_CATALOG],
        "statuses": list(PROMPT_BLOCK_STATUSES),
    }


@router.post("/starters")
async def create_starter_blocks(body: dict, user: dict = Depends(get_current_user)):
    project_id = (body or {}).get("project_id")
    try:
        return await service.materialize_starter_blocks(_owner(user), project_id)
    except PromptAssetError as exc:
        raise _translate(exc)


@router.get("")
async def list_prompt_blocks(
    project_id: str,
    category: Optional[str] = None,
    include_archived: bool = False,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.list_blocks(
            _owner(user), project_id, category=category, include_archived=include_archived
        )
    except PromptAssetError as exc:
        raise _translate(exc)


@router.post("")
async def create_prompt_block(body: PromptBlockCreate, user: dict = Depends(get_current_user)):
    try:
        return await service.create_block(_owner(user), body.model_dump())
    except PromptAssetError as exc:
        raise _translate(exc)


@router.get("/{block_id}")
async def get_prompt_block(block_id: str, user: dict = Depends(get_current_user)):
    try:
        return await service.get_block(_owner(user), block_id)
    except PromptAssetError as exc:
        raise _translate(exc)


@router.patch("/{block_id}")
async def update_prompt_block(
    block_id: str, body: PromptBlockUpdate, user: dict = Depends(get_current_user)
):
    try:
        return await service.update_block(
            _owner(user), block_id, body.model_dump(exclude_unset=True)
        )
    except PromptAssetError as exc:
        raise _translate(exc)


@router.delete("/{block_id}")
async def delete_prompt_block(block_id: str, user: dict = Depends(get_current_user)):
    try:
        await service.delete_block(_owner(user), block_id)
    except PromptAssetError as exc:
        raise _translate(exc)
    return {"ok": True}
