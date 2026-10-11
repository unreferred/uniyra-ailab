"""Dataset versioning: immutable frozen snapshots over ordered cases.

Versions are monotonically numbered per dataset and owner. A version starts
``draft`` (mutable) and can be frozen exactly once; after that every case and
version mutation is rejected. Freezing records a canonical content hash over
the ordered cases plus the case count so a reproducibility snapshot can pin it.

Legacy ``dataset_cases`` rows remain the source of truth for the old UI; a
lazily created version 1 copies them so nothing is lost on upgrade.
"""
from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..models import new_id, now_iso
from .experiments import canonical_hash


class DatasetVersionError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


FROZEN_MESSAGE = "Dataset version is frozen and read-only"


def _version_payload(case: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ordering": case.get("ordering", 0),
        "input_text": case.get("input_text", ""),
        "expected_output": case.get("expected_output", ""),
        "metadata": case.get("metadata") or {},
        "reference_answer": case.get("reference_answer"),
        "assertions": case.get("assertions") or [],
        "tool_policy": case.get("tool_policy") or {},
        "knowledge_policy": case.get("knowledge_policy") or {},
        "grounding_expectations": case.get("grounding_expectations") or {},
        "red_team": case.get("red_team") or {},
    }


def snapshot_of(version: Dict[str, Any]) -> Dict[str, Any]:
    """Immutable-shaped summary of a dataset version for provenance."""
    return {
        "id": version.get("id"),
        "dataset_id": version.get("dataset_id"),
        "project_id": version.get("project_id"),
        "version": version.get("version"),
        "status": version.get("status"),
        "description": version.get("description", ""),
        "case_count": version.get("case_count", 0),
        "content_hash": version.get("content_hash"),
        "source_version_id": version.get("source_version_id"),
        "created_by": version.get("created_by"),
        "created_at": version.get("created_at"),
        "updated_at": version.get("updated_at"),
        "frozen_at": version.get("frozen_at"),
    }


def require_draft(version: Dict[str, Any]) -> None:
    if version.get("status") == "frozen":
        raise DatasetVersionError(FROZEN_MESSAGE)


async def _ordered_cases(owner_id: str, version_id: str) -> List[Dict[str, Any]]:
    cases = await repo.dataset_version_cases.list(
        {"owner_id": owner_id, "dataset_version_id": version_id},
        sort_field="ordering",
        descending=False,
        limit=10000,
    )
    return sorted(
        cases,
        key=lambda c: (c.get("ordering", 0), c.get("created_at") or "", c.get("id") or ""),
    )


async def list_cases(
    owner_id: str, version_id: str, limit: int = 100, offset: int = 0
) -> List[Dict[str, Any]]:
    cases = await repo.dataset_version_cases.list(
        {"owner_id": owner_id, "dataset_version_id": version_id},
        sort_field="ordering",
        descending=False,
        limit=limit,
        offset=offset,
    )
    return cases


async def _insert_case(
    owner_id: str,
    dataset: Dict[str, Any],
    version: Dict[str, Any],
    source: Dict[str, Any],
    now: str,
) -> Dict[str, Any]:
    case = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": dataset.get("project_id"),
        "dataset_id": dataset["id"],
        "dataset_version_id": version["id"],
        "input_text": source.get("input_text", ""),
        "expected_output": source.get("expected_output", ""),
        "metadata": source.get("metadata") or {},
        "tags": source.get("tags") or [],
        "ordering": source.get("ordering", 0),
        "evaluation_hints": source.get("evaluation_hints") or {},
        "asset_attachment_ids": list(source.get("asset_attachment_ids") or []),
        "reference_answer": source.get("reference_answer"),
        "assertions": source.get("assertions") or [],
        "tool_policy": source.get("tool_policy") or {},
        "knowledge_policy": source.get("knowledge_policy") or {},
        "grounding_expectations": source.get("grounding_expectations") or {},
        "red_team": source.get("red_team") or None,
        "created_at": now,
        "updated_at": now,
    }
    await repo.dataset_version_cases.insert(case)
    return case


async def _source_cases(
    owner_id: str, dataset: Dict[str, Any], source_version_id: Optional[str]
) -> List[Dict[str, Any]]:
    if source_version_id:
        source = await repo.dataset_versions.get(source_version_id, owner_id)
        if not source or source.get("dataset_id") != dataset["id"]:
            raise DatasetVersionError("Source version was not found for this dataset")
        return await _ordered_cases(owner_id, source_version_id)
    return await repo.dataset_cases.list(
        {"owner_id": owner_id, "dataset_id": dataset["id"]},
        sort_field="ordering",
        descending=False,
        limit=10000,
    )


async def _create_version(
    owner_id: str,
    dataset: Dict[str, Any],
    number: int,
    source_version_id: Optional[str] = None,
    description: str = "",
) -> Dict[str, Any]:
    now = now_iso()
    version = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": dataset.get("project_id"),
        "dataset_id": dataset["id"],
        "version": number,
        "status": "draft",
        "description": description or "",
        "case_count": 0,
        "content_hash": None,
        "source_version_id": source_version_id,
        "created_by": owner_id,
        "created_at": now,
        "updated_at": now,
        "frozen_at": None,
    }
    sources = await _source_cases(owner_id, dataset, source_version_id)
    await repo.dataset_versions.insert(version)
    for source in sources:
        await _insert_case(owner_id, dataset, version, source, now)
    version["case_count"] = len(sources)
    await repo.dataset_versions.update(version["id"], owner_id, {"case_count": len(sources)})
    return version


async def ensure_draft_version(owner_id: str, dataset: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the dataset's newest draft version, lazily creating v1 if the
    dataset has no versions at all (backward compatibility with legacy cases)."""
    versions = await repo.dataset_versions.list(
        {"owner_id": owner_id, "dataset_id": dataset["id"]},
        sort_field="version",
        descending=True,
        limit=500,
    )
    if versions:
        return next((v for v in versions if v.get("status") == "draft"), None)
    return await _create_version(owner_id, dataset, 1)


async def create_version_from(
    owner_id: str,
    dataset: Dict[str, Any],
    source_version_id: Optional[str] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Create a new draft version numbered max+1, copying the given source
    version's cases (or the legacy dataset_cases when no source is given)."""
    latest = await repo.dataset_versions.list(
        {"owner_id": owner_id, "dataset_id": dataset["id"]},
        sort_field="version",
        descending=True,
        limit=1,
    )
    number = (latest[0].get("version", 0) + 1) if latest else 1
    return await _create_version(owner_id, dataset, number, source_version_id, description)


async def freeze_version(owner_id: str, version: Dict[str, Any]) -> Dict[str, Any]:
    """Compute the content hash + case count and lock the version forever."""
    if version.get("status") == "frozen":
        raise DatasetVersionError(FROZEN_MESSAGE)
    cases = await _ordered_cases(owner_id, version["id"])
    content_hash = canonical_hash([_version_payload(c) for c in cases])
    now = now_iso()
    updated = await repo.dataset_versions.update(version["id"], owner_id, {
        "status": "frozen",
        "content_hash": content_hash,
        "case_count": len(cases),
        "frozen_at": now,
        "updated_at": now,
    })
    return updated
