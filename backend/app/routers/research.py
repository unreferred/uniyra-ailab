"""Research sessions and their project-scoped links.

Every linked resource must belong to the same owner *and* the same project as
the research session. The detail endpoint re-filters linked lists by project so
a stale cross-project link can never surface.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import ResearchCreate, ResearchUpdate, new_id, now_iso

router = APIRouter(prefix="/research", tags=["research"])

# Patch field -> (owning repository, human label)
_LINK_FIELDS = {
    "related_experiment_ids": (repo.experiments, "experiment"),
    "related_artifact_ids": (repo.artifacts, "artifact"),
    "related_conversation_ids": (repo.conversations, "conversation"),
    "related_council_ids": (repo.councils, "council"),
    "related_finding_ids": (repo.findings, "finding"),
    "related_dataset_ids": (repo.datasets, "dataset"),
    "related_run_ids": (repo.runs, "run"),
    "related_dataset_version_ids": (repo.dataset_versions, "dataset_version"),
    "related_benchmark_plan_ids": (repo.benchmark_plans, "benchmark_plan"),
}


async def _validate_links(owner_id: str, project_id: Optional[str], patch: Dict[str, Any]) -> None:
    for field, (repository, label) in _LINK_FIELDS.items():
        ids = patch.get(field)
        if ids is None:
            continue
        for ref_id in ids:
            if not ref_id:
                continue
            ref = await repository.get(ref_id, owner_id)
            if not ref:
                raise HTTPException(status_code=400, detail=f"Referenced {label} {ref_id} not found")
            ref_project = ref.get("project_id")
            if ref_project is not None and ref_project != project_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Referenced {label} {ref_id} belongs to a different project",
                )


@router.get("")
async def list_research(project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"owner_id": user["id"]}
    if project_id:
        query["project_id"] = project_id
    return await repo.research_sessions.list(query, sort_field="updated_at")


@router.post("")
async def create_research(body: ResearchCreate, user: dict = Depends(get_current_user)):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    doc = {
        "id": new_id(), "owner_id": user["id"], "project_id": body.project_id,
        "title": body.title, "research_question": body.research_question, "notes": body.notes,
        "tags": body.tags or [],
        "status": "active", "sources": [], "related_experiment_ids": [], "related_artifact_ids": [],
        "related_conversation_ids": [], "related_council_ids": [], "related_finding_ids": [],
        "related_dataset_ids": [], "related_run_ids": [],
        "related_dataset_version_ids": [], "related_benchmark_plan_ids": [],
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await repo.research_sessions.insert(doc)
    return doc


@router.get("/{research_id}")
async def get_research(research_id: str, user: dict = Depends(get_current_user)):
    doc = await repo.research_sessions.get(research_id, user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Research session not found")
    project_id = doc.get("project_id")
    base: Dict[str, Any] = {"owner_id": user["id"]}
    if project_id:
        base["project_id"] = project_id
    exps = await repo.experiments.list(dict(base), sort_field="created_at")
    arts = await repo.artifacts.list(dict(base), sort_field="created_at")
    convs = await repo.conversations.list(dict(base), sort_field="created_at")
    councils = await repo.councils.list(dict(base), sort_field="created_at")
    findings = await repo.findings.list(dict(base), sort_field="created_at")
    datasets = await repo.datasets.list(dict(base), sort_field="created_at")
    versions = await repo.dataset_versions.list(dict(base), sort_field="created_at")
    plans = await repo.benchmark_plans.list(dict(base), sort_field="created_at")
    linked_exps = [e for e in exps if e["id"] in doc.get("related_experiment_ids", [])]
    linked_arts = [a for a in arts if a["id"] in doc.get("related_artifact_ids", [])]
    linked_convs = [c for c in convs if c["id"] in doc.get("related_conversation_ids", [])]
    linked_councils = [c for c in councils if c["id"] in doc.get("related_council_ids", [])]
    linked_findings = [f for f in findings if f["id"] in doc.get("related_finding_ids", [])]
    linked_datasets = [d for d in datasets if d["id"] in doc.get("related_dataset_ids", [])]
    linked_versions = [v for v in versions if v["id"] in doc.get("related_dataset_version_ids", [])]
    linked_plans = [p for p in plans if p["id"] in doc.get("related_benchmark_plan_ids", [])]
    return {**doc, "linked_experiments": linked_exps, "linked_artifacts": linked_arts,
            "linked_conversations": linked_convs, "linked_councils": linked_councils,
            "linked_findings": linked_findings, "linked_datasets": linked_datasets,
            "linked_dataset_versions": linked_versions, "linked_benchmark_plans": linked_plans}


@router.patch("/{research_id}")
async def update_research(research_id: str, body: ResearchUpdate, user: dict = Depends(get_current_user)):
    existing = await repo.research_sessions.get(research_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Research session not found")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "sources" in patch:
        patch["sources"] = [s if isinstance(s, dict) else s.model_dump() for s in patch["sources"]]
    await _validate_links(user["id"], existing.get("project_id"), patch)
    patch["updated_at"] = now_iso()
    return await repo.research_sessions.update(research_id, user["id"], patch)


@router.delete("/{research_id}")
async def delete_research(research_id: str, user: dict = Depends(get_current_user)):
    if not await repo.research_sessions.get(research_id, user["id"]):
        raise HTTPException(status_code=404, detail="Research session not found")
    await repo.research_sessions.delete(research_id, user["id"])
    return {"ok": True}
