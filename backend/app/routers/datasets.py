"""Evaluation datasets: reusable input/expected-output case collections.

Every read/write is owner-scoped, and resources that carry a `project_id` are
additionally required to agree on it: a dataset cannot be linked to an
experiment (or a case attached to a dataset) from a different project.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import repository as repo
from ..deps import get_current_user
from ..models import (DatasetCaseCreate, DatasetCaseUpdate, DatasetCreate, DatasetUpdate,
                      DatasetVersionCaseCreate, DatasetVersionCaseUpdate, DatasetVersionCreate,
                      new_id, now_iso)
from ..services import datasets as dataset_service
from ..services import regression_fixtures as fixtures_service

router = APIRouter(prefix="/datasets", tags=["datasets"])


class RegressionFixtureRequest(BaseModel):
    project_id: str
    name: Optional[str] = None


def _ds(doc: dict) -> dict:
    return doc


def _same_project(left: Optional[dict], right: Optional[dict]) -> bool:
    return (left or {}).get("project_id") == (right or {}).get("project_id")


def _require_same_project(resource: Dict[str, Any], item: Optional[Dict[str, Any]], label: str) -> None:
    if not item:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    if not _same_project(resource, item):
        raise HTTPException(status_code=400, detail=f"{label} belongs to a different project")


async def _load_case(case_id: str, owner_id: str) -> Dict[str, Any]:
    case = await repo.dataset_cases.find_one({"id": case_id, "owner_id": owner_id})
    if not case:
        raise HTTPException(status_code=404, detail="Dataset case not found")
    dataset = await repo.datasets.get(case.get("dataset_id"), owner_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    _require_same_project(dataset, case, "Dataset case")
    return case


async def _load_dataset(dataset_id: str, owner_id: str) -> Dict[str, Any]:
    dataset = await repo.datasets.get(dataset_id, owner_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


async def _load_version(owner_id: str, dataset: Dict[str, Any], version_id: str) -> Dict[str, Any]:
    version = await repo.dataset_versions.get(version_id, owner_id)
    if not version or version.get("dataset_id") != dataset["id"]:
        raise HTTPException(status_code=404, detail="Dataset version not found")
    _require_same_project(dataset, version, "Dataset version")
    return version


async def _load_version_case(owner_id: str, case_id: str) -> Dict[str, Any]:
    case = await repo.dataset_version_cases.find_one({"id": case_id, "owner_id": owner_id})
    if not case:
        raise HTTPException(status_code=404, detail="Dataset version case not found")
    dataset = await repo.datasets.get(case.get("dataset_id"), owner_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    _require_same_project(dataset, case, "Dataset version case")
    return case


async def _recount_version(owner_id: str, version: Dict[str, Any]) -> int:
    total = await repo.dataset_version_cases.count(
        {"owner_id": owner_id, "dataset_version_id": version["id"]})
    await repo.dataset_versions.update(version["id"], owner_id, {
        "case_count": total, "updated_at": now_iso()})
    return total


def _draft_guard(version: Dict[str, Any]) -> None:
    try:
        dataset_service.require_draft(version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("")
async def list_datasets(project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query: Dict[str, Any] = {"owner_id": user["id"]}
    if project_id:
        query["project_id"] = project_id
    return await repo.datasets.list(query, sort_field="created_at")


@router.post("")
async def create_dataset(body: DatasetCreate, user: dict = Depends(get_current_user)):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    doc = {
        "id": new_id(), "owner_id": user["id"], "project_id": body.project_id,
        "name": body.name, "description": body.description, "tags": body.tags or [],
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await repo.datasets.insert(doc)
    return _ds(doc)


@router.post("/regression-fixtures")
async def create_regression_fixtures(body: RegressionFixtureRequest,
                                     user: dict = Depends(get_current_user)):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    return await fixtures_service.materialize(user["id"], body.project_id, name=body.name)


@router.get("/{dataset_id}")
async def get_dataset(dataset_id: str, user: dict = Depends(get_current_user)):
    doc = await repo.datasets.get(dataset_id, user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Dataset not found")
    case_query: Dict[str, Any] = {"owner_id": user["id"], "dataset_id": dataset_id}
    if doc.get("project_id"):
        case_query["project_id"] = doc["project_id"]
    cases = await repo.dataset_cases.list(case_query, sort_field="ordering", descending=False, limit=2000)
    return {**doc, "cases": cases, "case_count": len(cases)}


@router.patch("/{dataset_id}")
async def update_dataset(dataset_id: str, body: DatasetUpdate, user: dict = Depends(get_current_user)):
    if not await repo.datasets.get(dataset_id, user["id"]):
        raise HTTPException(status_code=404, detail="Dataset not found")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    patch["updated_at"] = now_iso()
    return await repo.datasets.update(dataset_id, user["id"], patch)


@router.delete("/{dataset_id}")
async def delete_dataset(dataset_id: str, user: dict = Depends(get_current_user)):
    doc = await repo.datasets.get(dataset_id, user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Dataset not found")
    case_query: Dict[str, Any] = {"owner_id": user["id"], "dataset_id": dataset_id}
    if doc.get("project_id"):
        case_query["project_id"] = doc["project_id"]
    await repo.dataset_cases.delete_many(case_query)
    version_query: Dict[str, Any] = {"owner_id": user["id"], "dataset_id": dataset_id}
    if doc.get("project_id"):
        version_query["project_id"] = doc["project_id"]
    await repo.dataset_version_cases.delete_many(
        {"owner_id": user["id"], "dataset_id": dataset_id})
    await repo.dataset_versions.delete_many(version_query)
    await repo.datasets.delete(dataset_id, user["id"])
    return {"ok": True}


@router.post("/{dataset_id}/cases")
async def add_case(dataset_id: str, body: DatasetCaseCreate, user: dict = Depends(get_current_user)):
    ds = await repo.datasets.get(dataset_id, user["id"])
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    case = {
        "id": new_id(), "owner_id": user["id"], "dataset_id": dataset_id,
        "project_id": ds.get("project_id"),
        "input_text": body.input_text, "expected_output": body.expected_output,
        "metadata": body.metadata or {}, "tags": body.tags or [],
        "ordering": body.ordering or 0,
        "asset_attachment_ids": list(body.asset_attachment_ids or []),
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await repo.dataset_cases.insert(case)
    return case


@router.get("/{dataset_id}/cases")
async def list_cases(dataset_id: str, user: dict = Depends(get_current_user)):
    ds = await repo.datasets.get(dataset_id, user["id"])
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    case_query: Dict[str, Any] = {"owner_id": user["id"], "dataset_id": dataset_id}
    if ds.get("project_id"):
        case_query["project_id"] = ds["project_id"]
    return await repo.dataset_cases.list(
        case_query, sort_field="ordering", descending=False, limit=2000)


@router.patch("/cases/{case_id}")
async def update_case(case_id: str, body: DatasetCaseUpdate, user: dict = Depends(get_current_user)):
    await _load_case(case_id, user["id"])
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    patch["updated_at"] = now_iso()
    return await repo.dataset_cases.update(case_id, user["id"], patch)


@router.delete("/cases/{case_id}")
async def delete_case(case_id: str, user: dict = Depends(get_current_user)):
    await _load_case(case_id, user["id"])
    await repo.dataset_cases.delete(case_id, user["id"])
    return {"ok": True}


@router.post("/{dataset_id}/attach-experiment")
async def attach_experiment(dataset_id: str, body: dict, user: dict = Depends(get_current_user)):
    ds = await repo.datasets.get(dataset_id, user["id"])
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    experiment_id = (body or {}).get("experiment_id")
    if not experiment_id:
        raise HTTPException(status_code=400, detail="experiment_id is required")
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    _require_same_project(ds, exp, "Experiment")
    updated = await repo.experiments.update(experiment_id, user["id"], {
        "dataset_id": dataset_id, "updated_at": now_iso(),
    })
    return {"ok": True, "dataset_id": dataset_id, "experiment": updated}


# ---------------------------------------------------------------- versions
@router.get("/{dataset_id}/versions")
async def list_dataset_versions(dataset_id: str, user: dict = Depends(get_current_user)):
    ds = await _load_dataset(dataset_id, user["id"])
    await dataset_service.ensure_draft_version(user["id"], ds)
    return await repo.dataset_versions.list(
        {"owner_id": user["id"], "dataset_id": dataset_id},
        sort_field="version", descending=True)


@router.post("/{dataset_id}/versions")
async def create_dataset_version(dataset_id: str, body: Optional[DatasetVersionCreate] = None,
                                 user: dict = Depends(get_current_user)):
    ds = await _load_dataset(dataset_id, user["id"])
    body = body or DatasetVersionCreate()
    try:
        version = await dataset_service.create_version_from(
            user["id"], ds, body.source_version_id, body.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return version


@router.get("/{dataset_id}/versions/{version_id}")
async def get_dataset_version(dataset_id: str, version_id: str, limit: int = 100,
                              offset: int = 0, user: dict = Depends(get_current_user)):
    ds = await _load_dataset(dataset_id, user["id"])
    version = await _load_version(user["id"], ds, version_id)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    cases = await dataset_service.list_cases(user["id"], version["id"], limit=limit, offset=offset)
    total = await repo.dataset_version_cases.count(
        {"owner_id": user["id"], "dataset_version_id": version["id"]})
    return {**version, "cases": cases, "case_count": total, "limit": limit, "offset": offset}


@router.post("/{dataset_id}/versions/{version_id}/cases")
async def add_dataset_version_case(dataset_id: str, version_id: str,
                                   body: DatasetVersionCaseCreate,
                                   user: dict = Depends(get_current_user)):
    ds = await _load_dataset(dataset_id, user["id"])
    version = await _load_version(user["id"], ds, version_id)
    _draft_guard(version)
    now = now_iso()
    case = {
        "id": new_id(), "owner_id": user["id"], "project_id": ds.get("project_id"),
        "dataset_id": dataset_id, "dataset_version_id": version_id,
        "input_text": body.input_text, "expected_output": body.expected_output,
        "metadata": body.metadata or {}, "tags": body.tags or [],
        "ordering": body.ordering or 0,
        "evaluation_hints": body.evaluation_hints or {},
        "asset_attachment_ids": list(body.asset_attachment_ids or []),
        "reference_answer": body.reference_answer,
        "assertions": [a.model_dump() for a in (body.assertions or [])],
        "tool_policy": body.tool_policy.model_dump(),
        "knowledge_policy": body.knowledge_policy.model_dump(),
        "grounding_expectations": body.grounding_expectations.model_dump(),
        "red_team": body.red_team.model_dump() if body.red_team else None,
        "created_at": now, "updated_at": now,
    }
    await repo.dataset_version_cases.insert(case)
    await _recount_version(user["id"], version)
    return case


@router.post("/{dataset_id}/versions/{version_id}/freeze")
async def freeze_dataset_version(dataset_id: str, version_id: str,
                                 user: dict = Depends(get_current_user)):
    ds = await _load_dataset(dataset_id, user["id"])
    version = await _load_version(user["id"], ds, version_id)
    try:
        frozen = await dataset_service.freeze_version(user["id"], version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return frozen


@router.get("/version-cases/{case_id}")
async def get_dataset_version_case(case_id: str, user: dict = Depends(get_current_user)):
    return await _load_version_case(user["id"], case_id)


@router.patch("/version-cases/{case_id}")
async def update_dataset_version_case(case_id: str, body: DatasetVersionCaseUpdate,
                                      user: dict = Depends(get_current_user)):
    case = await _load_version_case(user["id"], case_id)
    version = await _load_version(user["id"], await _load_dataset(case["dataset_id"], user["id"]),
                                  case["dataset_version_id"])
    _draft_guard(version)
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    patch["updated_at"] = now_iso()
    updated = await repo.dataset_version_cases.update(case_id, user["id"], patch)
    if "ordering" in patch:
        await _recount_version(user["id"], version)
    return updated


@router.delete("/version-cases/{case_id}")
async def delete_dataset_version_case(case_id: str, user: dict = Depends(get_current_user)):
    case = await _load_version_case(user["id"], case_id)
    ds = await _load_dataset(case["dataset_id"], user["id"])
    version = await _load_version(user["id"], ds, case["dataset_version_id"])
    _draft_guard(version)
    await repo.dataset_version_cases.delete(case_id, user["id"])
    await _recount_version(user["id"], version)
    return {"ok": True}
