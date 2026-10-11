from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import CouncilRequest
from ..services import run_council

router = APIRouter(prefix="/councils", tags=["councils"])


@router.get("")
async def list_councils(project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"owner_id": user["id"]}
    if project_id:
        query["project_id"] = project_id
    items = await repo.councils.list(query, sort_field="created_at")
    # trim heavy round payloads for the list view
    return [{k: v for k, v in c.items() if k != "rounds"} for c in items]


@router.post("/run")
async def council_run(body: CouncilRequest, user: dict = Depends(get_current_user)):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    for p in body.participants:
        if p.enabled and not await repo.models.get(p.model_id, user["id"]):
            raise HTTPException(status_code=400, detail=f"Model {p.model_id} not found")
    if not await repo.models.get(body.moderator_model_id, user["id"]):
        raise HTTPException(status_code=400, detail="Moderator model not found")
    try:
        return await run_council(
            owner_id=user["id"], project_id=body.project_id, question=body.question,
            participants=[p.model_dump() for p in body.participants],
            moderator_model_id=body.moderator_model_id, title=body.title,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{council_id}")
async def get_council(council_id: str, user: dict = Depends(get_current_user)):
    c = await repo.councils.get(council_id, user["id"])
    if not c:
        raise HTTPException(status_code=404, detail="Council not found")
    return c


@router.delete("/{council_id}")
async def delete_council(council_id: str, user: dict = Depends(get_current_user)):
    if not await repo.councils.get(council_id, user["id"]):
        raise HTTPException(status_code=404, detail="Council not found")
    # New council runs carry council_id; legacy council runs overloaded
    # experiment_id with the council id, so both are matched for cleanup.
    run_query = {
        "owner_id": user["id"],
        "$or": [{"council_id": council_id}, {"experiment_id": council_id}],
    }
    runs = await repo.runs.list(run_query, sort_field="created_at", descending=False, limit=100000)
    run_ids = [r["id"] for r in runs]
    if run_ids:
        await repo.usage_events.delete_many({"owner_id": user["id"], "run_id": {"$in": run_ids}})
    await repo.runs.delete_many(run_query)
    await repo.councils.delete(council_id, user["id"])
    return {"ok": True}
