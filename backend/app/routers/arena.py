from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import ArenaRequest
from ..services import run_arena
from ..services import attachments as attachments_service

router = APIRouter(prefix="/arena", tags=["arena"])


@router.post("/run")
async def arena_run(body: ArenaRequest, user: dict = Depends(get_current_user)):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    if not (1 <= len(body.model_ids) <= 4):
        raise HTTPException(status_code=400, detail="Select between 1 and 4 models")
    for mid in body.model_ids:
        if not await repo.models.get(mid, user["id"]):
            raise HTTPException(status_code=400, detail=f"Model {mid} not found")
    try:
        attachment_docs = await attachments_service.validate_attachment_ids(
            user["id"], body.project_id, list(body.attachment_ids or []))
    except attachments_service.AttachmentError as err:
        raise HTTPException(
            status_code=getattr(err, "status_code", 400) or 400,
            detail=getattr(err, "message", None) or str(err),
        )
    if body.knowledge_collection_ids:
        for collection_id in body.knowledge_collection_ids:
            doc = await repo.knowledge_collections.get(collection_id, user["id"])
            if not doc or doc.get("project_id") != body.project_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Knowledge collection '{collection_id}' is not in this project",
                )
    try:
        result = await run_arena(
            owner_id=user["id"], project_id=body.project_id, task=body.task,
            system_prompt=body.system_prompt, model_ids=body.model_ids,
            params=body.params.model_dump(exclude_none=True), blind=body.blind, title=body.title,
            attachments=attachment_docs,
            attachment_ids=list(body.attachment_ids or []),
            knowledge_collection_ids=list(body.knowledge_collection_ids or []),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result
