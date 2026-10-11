"""Findings: validated statements backed by project evidence.

Evidence links may only point at resources the owner holds *within the same
project* as the finding. Missing/foreign references stay tolerant (`exists:
false`) so the UI never crashes on a stale link, but they are never resolved
into a navigable route across a project boundary.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import (
    FINDING_STATUSES,
    FindingCreate,
    FindingLinkRequest,
    FindingPromotionRequest,
    FindingUpdate,
    new_id,
    now_iso,
)
from ..services import finding_promotion

router = APIRouter(prefix="/findings", tags=["findings"])

# Evidence type -> owning repository. Unknown types are tolerated.
_EVIDENCE_REPOS = {
    "experiment": repo.experiments,
    "research": repo.research_sessions,
    "artifact": repo.artifacts,
    "run": repo.runs,
    "evaluation": repo.evaluations,
    "council": repo.councils,
    "dataset": repo.datasets,
    "conversation": repo.conversations,
    "dataset_version": repo.dataset_versions,
    "benchmark_result": repo.experiment_configs,
    "failure": repo.failures,
    "failure_record": repo.failures,
    "regression": repo.regression_runs,
    "regression_run": repo.regression_runs,
    "scorecard": repo.scorecards,
    "certification_candidate": repo.certification_candidates,
    "dataset_version_case": repo.dataset_version_cases,
    "rubric": repo.rubrics,
    # Additive (V6.9 Drift & Failure Analysis).
    "drift_analysis": repo.drift_analyses,
}


def _evidence_dict(entry: Any) -> Dict[str, Any]:
    return entry if isinstance(entry, dict) else entry.model_dump()


def _visible(ref: Optional[Dict[str, Any]], project_id: Optional[str]) -> bool:
    if not ref:
        return False
    return ref.get("project_id") is None or ref.get("project_id") == project_id


async def _validate_evidence_project(owner_id: str, project_id: Optional[str], evidence: List[Any]) -> None:
    """Reject any evidence reference that resolves to a different project.

    A reference that is missing (or belongs to another owner, and therefore is
    invisible under `owner_id`) is tolerated here and reported as exists=False
    at read time.
    """
    for entry in evidence or []:
        item = _evidence_dict(entry)
        etype = (item.get("type") or "").strip()
        ref_id = item.get("ref_id")
        if not etype or not ref_id:
            continue
        repository = _EVIDENCE_REPOS.get(etype)
        if repository is None:
            continue
        ref = await repository.get(ref_id, owner_id)
        if not ref:
            continue
        ref_project = ref.get("project_id")
        if ref_project is not None and ref_project != project_id:
            raise HTTPException(
                status_code=400,
                detail=f"Evidence {etype} {ref_id} belongs to a different project",
            )


async def _resolve_evidence(owner_id: str, project_id: Optional[str], evidence: List[Any]) -> List[Dict[str, Any]]:
    """Attach a resolvable route + title for each evidence link (navigation).
    Never fails on unknown/missing/cross-project links -- marks exists=False."""
    resolved = []
    for entry in evidence or []:
        e = _evidence_dict(entry)
        item = {**e, "exists": False, "route": None, "title": e.get("label", "")}
        try:
            etype = (e.get("type") or "")
            ref_id = e.get("ref_id")
            repository = _EVIDENCE_REPOS.get(etype)
            ref = await repository.get(ref_id, owner_id) if repository and ref_id else None
            if _visible(ref, project_id):
                if etype == "experiment":
                    item.update(exists=True, route=f"/experiments/{ref['id']}", title=ref.get("title", ""))
                elif etype == "research":
                    item.update(exists=True, route=f"/research/{ref['id']}", title=ref.get("title", ""))
                elif etype == "artifact":
                    item.update(exists=True, route="/artifacts", title=ref.get("title", ""))
                elif etype == "run":
                    item.update(exists=True, route=None,
                                title=ref.get("model_name") or ref.get("id", ""))
                elif etype == "evaluation":
                    exp_id = ref.get("experiment_id")
                    item.update(exists=True, route=f"/experiments/{exp_id}" if exp_id else None,
                                title=f"Evaluation {ref.get('id', '')[:8]}")
                elif etype == "council":
                    item.update(exists=True, route="/council", title=ref.get("title", ""))
                elif etype == "dataset":
                    item.update(exists=True, route="/datasets", title=ref.get("name", ""))
                elif etype == "conversation":
                    item.update(exists=True, route="/workspace", title=ref.get("title", ""))
                elif etype == "dataset_version":
                    dataset_id = ref.get("dataset_id")
                    item.update(
                        exists=True,
                        route=f"/datasets/{dataset_id}" if dataset_id else "/datasets",
                        title=f"v{ref.get('version')} - {ref.get('description', '')}".strip(" -"),
                    )
                elif etype == "benchmark_result":
                    exp_id = ref.get("experiment_id")
                    item.update(
                        exists=True,
                        route=f"/experiments/{exp_id}" if exp_id else None,
                        title=ref.get("label") or f"Benchmark {ref.get('id', '')[:8]}",
                    )
                elif etype in ("failure", "failure_record"):
                    item.update(
                        exists=True,
                        route="/failures",
                        title=ref.get("title") or f"Failure {ref.get('id', '')[:8]}",
                    )
                elif etype in ("regression", "regression_run"):
                    exp_id = ref.get("experiment_id")
                    item.update(
                        exists=True,
                        route=f"/experiments/{exp_id}" if exp_id else None,
                        title=ref.get("name") or ref.get("label") or "Regression",
                    )
                elif etype == "scorecard":
                    item.update(
                        exists=True,
                        route=None,
                        title=ref.get("label") or f"Scorecard {ref.get('id', '')[:8]}",
                    )
                elif etype == "certification_candidate":
                    item.update(
                        exists=True,
                        route=None,
                        title=ref.get("label") or f"Candidate {ref.get('id', '')[:8]}",
                    )
                elif etype == "dataset_version_case":
                    dataset_id = ref.get("dataset_id")
                    item.update(
                        exists=True,
                        route=f"/datasets/{dataset_id}" if dataset_id else "/datasets",
                        title=(ref.get("input_text") or "")[:80] or "Dataset case",
                    )
                elif etype == "rubric":
                    item.update(
                        exists=True,
                        route="/rubrics",
                        title=ref.get("name") or "Rubric",
                    )
                elif etype == "drift_analysis":
                    item.update(
                        exists=True,
                        route="/drift",
                        title=f"Drift analysis {ref.get('id', '')[:8]}",
                    )
        except Exception:
            pass
        resolved.append(item)
    return resolved


@router.get("")
async def list_findings(project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"owner_id": user["id"]}
    if project_id:
        query["project_id"] = project_id
    return await repo.findings.list(query, sort_field="updated_at")


@router.post("")
async def create_finding(body: FindingCreate, user: dict = Depends(get_current_user)):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    evidence = [e.model_dump() for e in body.evidence]
    await _validate_evidence_project(user["id"], body.project_id, evidence)
    doc = {
        "id": new_id(), "owner_id": user["id"], "project_id": body.project_id,
        "title": body.title, "statement": body.statement, "description": body.description,
        "confidence": body.confidence,
        "status": body.status, "notes": body.notes, "tags": body.tags or [],
        "evidence": evidence,
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await repo.findings.insert(doc)
    return doc


@router.get("/{finding_id}")
async def get_finding(finding_id: str, user: dict = Depends(get_current_user)):
    doc = await repo.findings.get(finding_id, user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Finding not found")
    return {
        **doc,
        "promotions": doc.get("promotions") or [],
        "resolved_evidence": await _resolve_evidence(
            user["id"], doc.get("project_id"), doc.get("evidence", [])),
    }


@router.patch("/{finding_id}")
async def update_finding(finding_id: str, body: FindingUpdate, user: dict = Depends(get_current_user)):
    existing = await repo.findings.get(finding_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Finding not found")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "status" in patch and patch["status"] not in FINDING_STATUSES:
        raise HTTPException(status_code=400, detail="Unsupported finding status")
    if "evidence" in patch:
        evidence = [_evidence_dict(e) for e in patch["evidence"]]
        await _validate_evidence_project(user["id"], existing.get("project_id"), evidence)
        patch["evidence"] = evidence
    patch["updated_at"] = now_iso()
    return await repo.findings.update(finding_id, user["id"], patch)


@router.post("/{finding_id}/promote/regression")
async def promote_finding_regression(
    finding_id: str, body: FindingPromotionRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await finding_promotion.promote_to_regression_case(
            user["id"], finding_id, body.model_dump()
        )
    except finding_promotion.FindingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except finding_promotion.FindingPromotionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{finding_id}/promote/red-team")
async def promote_finding_red_team(
    finding_id: str, body: FindingPromotionRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await finding_promotion.promote_to_red_team_case(
            user["id"], finding_id, body.model_dump()
        )
    except finding_promotion.FindingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except finding_promotion.FindingPromotionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{finding_id}/link")
async def link_finding_evidence(
    finding_id: str, body: FindingLinkRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await finding_promotion.link_finding(
            user["id"], finding_id, body.model_dump()
        )
    except finding_promotion.FindingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except finding_promotion.FindingPromotionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{finding_id}")
async def delete_finding(finding_id: str, user: dict = Depends(get_current_user)):
    if not await repo.findings.get(finding_id, user["id"]):
        raise HTTPException(status_code=404, detail="Finding not found")
    await repo.findings.delete(finding_id, user["id"])
    return {"ok": True}
