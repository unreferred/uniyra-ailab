from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import RubricCreate, RubricUpdate, new_id, now_iso

router = APIRouter(prefix="/rubrics", tags=["rubrics"])

_VERSIONED_FIELDS = ("dimensions", "threshold", "scoring_scale", "description")


async def _insert_rubric_version(rubric: Dict[str, Any], created_by: str, now: str) -> Dict[str, Any]:
    snapshot = {
        "id": new_id(),
        "owner_id": rubric.get("owner_id"),
        "rubric_id": rubric.get("id"),
        "version": rubric.get("version") or 1,
        "name": rubric.get("name"),
        "description": rubric.get("description") or "",
        "dimensions": rubric.get("dimensions") or [],
        "threshold": rubric.get("threshold"),
        "scoring_scale": rubric.get("scoring_scale"),
        "created_at": now,
        "created_by": created_by,
    }
    await repo.rubric_versions.insert(snapshot)
    return snapshot


@router.get("")
async def list_rubrics(user: dict = Depends(get_current_user)):
    return await repo.rubrics.list({"owner_id": user["id"]}, sort_field="created_at", descending=False)


@router.post("")
async def create_rubric(body: RubricCreate, user: dict = Depends(get_current_user)):
    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": user["id"],
        "seeded": False,
        "name": body.name,
        "description": body.description,
        "dimensions": [d.model_dump() for d in body.dimensions],
        "threshold": body.threshold,
        "scoring_scale": body.scoring_scale,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    await repo.rubrics.insert(doc)
    await _insert_rubric_version(doc, user["id"], now)
    return doc


@router.patch("/{rubric_id}")
async def update_rubric(rubric_id: str, body: RubricUpdate, user: dict = Depends(get_current_user)):
    existing = await repo.rubrics.get(rubric_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Rubric not found")

    data = body.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {}
    if data.get("name") is not None:
        patch["name"] = data["name"]
    if data.get("description") is not None:
        patch["description"] = data["description"]
    if data.get("dimensions") is not None:
        patch["dimensions"] = data["dimensions"]
    if "threshold" in data:
        patch["threshold"] = data["threshold"]
    if data.get("scoring_scale") is not None:
        patch["scoring_scale"] = data["scoring_scale"]

    changed = False
    for field in _VERSIONED_FIELDS:
        if field in data and data.get(field) != existing.get(field):
            changed = True
            break

    now = now_iso()
    patch["updated_at"] = now
    if changed:
        patch["version"] = int(existing.get("version") or 1) + 1

    updated = await repo.rubrics.update(rubric_id, user["id"], patch)
    if changed:
        await _insert_rubric_version(updated or {**existing, **patch}, user["id"], now)
    return updated or {**existing, **patch}


@router.get("/{rubric_id}/versions")
async def list_rubric_versions(rubric_id: str, user: dict = Depends(get_current_user)):
    if not await repo.rubrics.get(rubric_id, user["id"]):
        raise HTTPException(status_code=404, detail="Rubric not found")
    return await repo.rubric_versions.list(
        {"owner_id": user["id"], "rubric_id": rubric_id},
        sort_field="version",
        descending=False,
        limit=500,
    )


@router.delete("/{rubric_id}")
async def delete_rubric(rubric_id: str, user: dict = Depends(get_current_user)):
    if not await repo.rubrics.get(rubric_id, user["id"]):
        raise HTTPException(status_code=404, detail="Rubric not found")
    await repo.rubrics.delete(rubric_id, user["id"])
    return {"ok": True}
