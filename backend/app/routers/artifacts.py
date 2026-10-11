from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import ArtifactCreate, ArtifactUpdate, new_id, now_iso

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("")
async def list_artifacts(project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"owner_id": user["id"]}
    if project_id:
        query["project_id"] = project_id
    return await repo.artifacts.list(query, sort_field="created_at")


@router.post("")
async def create_artifact(body: ArtifactCreate, user: dict = Depends(get_current_user)):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    doc = {"id": new_id(), "owner_id": user["id"], "version": 1,
           "created_at": now_iso(), "updated_at": now_iso(), **body.model_dump()}
    await repo.artifacts.insert(doc)
    return doc


@router.get("/{artifact_id}")
async def get_artifact(artifact_id: str, user: dict = Depends(get_current_user)):
    doc = await repo.artifacts.get(artifact_id, user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return doc


@router.patch("/{artifact_id}")
async def update_artifact(artifact_id: str, body: ArtifactUpdate, user: dict = Depends(get_current_user)):
    existing = await repo.artifacts.get(artifact_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Artifact not found")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    patch["version"] = existing.get("version", 1) + 1
    patch["updated_at"] = now_iso()
    return await repo.artifacts.update(artifact_id, user["id"], patch)


@router.delete("/{artifact_id}")
async def delete_artifact(artifact_id: str, user: dict = Depends(get_current_user)):
    if not await repo.artifacts.get(artifact_id, user["id"]):
        raise HTTPException(status_code=404, detail="Artifact not found")
    await repo.artifacts.delete(artifact_id, user["id"])
    return {"ok": True}
