"""V5.10 Certification readiness engine (V5 -> V7 handoff contract).

This module evaluates whether a *subject* (a model, model configuration,
prompt, agent, tool policy or routing policy) is supported by enough truthful
evidence to be **ready for** certification. It never certifies anything:

* there is no ``CERTIFIED`` state (see :data:`CANDIDATE_STATES`);
* readiness results always carry ``automatic_certification: False`` and
  ``certification_performed: False``;
* nothing here mutates runs, evaluations, datasets, scorecards or failures.

Every reference is owner-scoped and same-project where the resource carries a
``project_id``. Missing or cross-project references are rejected at candidate
creation/update time (HTTP 400) and reported tolerantly inside evidence
bundles.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import EVALUATOR_VERSION, new_id, now_iso
from . import redaction

CERTIFICATION_SCHEMA_VERSION = 1
CERTIFICATION_POLICY_VERSION = "v5.10.0"

# Never "CERTIFIED": this contract stops at readiness for a human decision.
CANDIDATE_STATES = ("DRAFT", "NOT_READY", "BLOCKED", "READY_FOR_CERTIFICATION")

SUBJECT_TYPES = (
    "model",
    "model_config",
    "prompt",
    "agent",
    "tool_policy",
    "routing_policy",
)

# Explicit, ordered readiness criteria keys. The policy is machine-consumable
# via GET /api/certification/policy so V7 can pin the exact contract it expects.
READINESS_CRITERIA = (
    "evidence_present",
    "dataset_versions_frozen",
    "no_unresolved_security_failures",
    "no_unresolved_high_quality_failures",
    "red_team_boundaries_held_or_absent",
    "scorecards_present",
    "environment_captured",
    # Additive (V6.10 Scorecards & Certification Handoff): freeze checks for
    # agent/prompt/context-pack versions and unresolved high drift failures.
    "agent_versions_frozen",
    "prompt_versions_frozen",
    "context_pack_versions_frozen",
    "no_unresolved_high_drift",
)

READINESS_POLICY: Dict[str, Any] = {
    "id": "v5-readiness-policy",
    "version": CERTIFICATION_POLICY_VERSION,
    "description": (
        "V5 readiness-for-certification policy. Passing every criterion yields "
        "READY_FOR_CERTIFICATION; a human decision is still required and this "
        "engine never emits a certified state."
    ),
    "criteria": [
        {
            "key": "evidence_present",
            "description": "At least one experiment, run and evaluation are resolvable.",
            "required": True,
        },
        {
            "key": "dataset_versions_frozen",
            "description": "At least one dataset version is referenced and every referenced version is frozen.",
            "required": True,
        },
        {
            "key": "no_unresolved_security_failures",
            "description": "No OPEN/CONFIRMED failure with failure_domain SECURITY linked to evidence runs or experiments.",
            "required": True,
        },
        {
            "key": "no_unresolved_high_quality_failures",
            "description": "No OPEN/CONFIRMED QUALITY/RESEARCH failure with high or critical severity linked to evidence.",
            "required": True,
        },
        {
            "key": "red_team_boundaries_held_or_absent",
            "description": "Referenced red-team experiments each have evaluations and zero FAIL boundary evaluations; passes when none referenced.",
            "required": True,
        },
        {
            "key": "scorecards_present",
            "description": "At least one referenced scorecard resolves.",
            "required": True,
        },
        {
            "key": "environment_captured",
            "description": "The candidate carries a non-empty captured environment block.",
            "required": True,
        },
        {
            "key": "agent_versions_frozen",
            "description": "Passes when no agent version is referenced, or every referenced agent version is frozen/deprecated.",
            "required": True,
        },
        {
            "key": "prompt_versions_frozen",
            "description": "Passes when no prompt version is referenced, or every referenced prompt version is frozen/deprecated.",
            "required": True,
        },
        {
            "key": "context_pack_versions_frozen",
            "description": "Passes when no context pack version is referenced, or every referenced context pack version is frozen/deprecated.",
            "required": True,
        },
        {
            "key": "no_unresolved_high_drift",
            "description": "No OPEN/CONFIRMED failure with a canonical QUALITY.*_DRIFT code and high/critical severity linked to evidence.",
            "required": True,
        },
    ],
}

# Evidence keys -> bundle kind name.
EVIDENCE_KINDS: Dict[str, str] = {
    "experiment_ids": "experiments",
    "run_ids": "runs",
    "evaluation_ids": "evaluations",
    "regression_ids": "regressions",
    "red_team_experiment_ids": "red_team_experiments",
    "finding_ids": "findings",
    "scorecard_ids": "scorecards",
    "dataset_version_ids": "dataset_versions",
    "rubric_ids": "rubrics",
    "benchmark_plan_ids": "benchmark_plans",
    # Additive (V6.10): frozen build artifacts referenced as evidence.
    "agent_version_ids": "agent_versions",
    "prompt_version_ids": "prompt_versions",
    "context_pack_version_ids": "context_pack_versions",
    "drift_analysis_ids": "drift_analyses",
}

_UNRESOLVED_STATUSES = ("OPEN", "CONFIRMED")
_HIGH_SEVERITIES = ("high", "critical")

# Version fallbacks for environment capture. Kept local so this module never
# imports the concurrently-developed scorecard/red-team service modules.
_RUNNER_VERSION_DEFAULT = "1"
_RED_TEAM_VERSION_DEFAULT = "v5.7.0"
_SCORECARD_BUILDER_VERSION_DEFAULT = "v5.8.0"


class CertificationError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class CertificationNotFound(CertificationError):
    """Owner-scoped certification candidate missing (HTTP 404)."""


# ------------------------------------------------------------------ helpers
def _collection_for(key: str):
    mapping = {
        "experiment_ids": repo.experiments,
        "run_ids": repo.runs,
        "evaluation_ids": repo.evaluations,
        "regression_ids": repo.regression_runs,
        "red_team_experiment_ids": repo.experiments,
        "finding_ids": repo.findings,
        "scorecard_ids": repo.scorecards,
        "dataset_version_ids": repo.dataset_versions,
        "rubric_ids": repo.rubrics,
        "benchmark_plan_ids": repo.benchmark_plans,
        # Additive (V6.10).
        "agent_version_ids": repo.agent_versions,
        "prompt_version_ids": repo.prompt_versions,
        "context_pack_version_ids": repo.context_pack_versions,
        "drift_analysis_ids": repo.drift_analyses,
    }
    return mapping[key]


def _ids(evidence: Dict[str, Any], key: str) -> List[str]:
    raw = evidence.get(key) or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        raise CertificationError(f"Evidence field '{key}' must be a list of ids")
    out: List[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise CertificationError(f"Evidence field '{key}' contains an invalid id")
        value = value.strip()
        if value not in out:
            out.append(value)
    return out


def _normalize_evidence(evidence: Dict[str, Any]) -> Dict[str, List[str]]:
    if not isinstance(evidence, dict):
        raise CertificationError("evidence must be an object of id lists")
    return {key: _ids(evidence, key) for key in EVIDENCE_KINDS}


def _validate_subject(subject: Any) -> Dict[str, Any]:
    if not isinstance(subject, dict):
        raise CertificationError("subject must be an object")
    subject_type = subject.get("type")
    if subject_type not in SUBJECT_TYPES:
        raise CertificationError(
            f"Unsupported subject.type: {subject_type!r}; expected one of {SUBJECT_TYPES}"
        )
    return dict(subject)


async def _validate_evidence(
    owner_id: str, project_id: str, evidence: Dict[str, Any]
) -> Dict[str, List[str]]:
    """Validate every reference is owner-scoped and same-project where applicable."""
    normalized = _normalize_evidence(evidence)
    for key, ids in normalized.items():
        collection = _collection_for(key)
        for ref_id in ids:
            doc = await collection.get(ref_id, owner_id)
            if not doc:
                raise CertificationError(
                    f"Evidence reference {ref_id!r} in '{key}' was not found for this owner"
                )
            resource_project = doc.get("project_id")
            if resource_project is not None and resource_project != project_id:
                raise CertificationError(
                    f"Evidence reference {ref_id!r} in '{key}' belongs to a different project"
                )
    return normalized


async def _fetch(collection, owner_id: str, ids: List[str]) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
    out: List[Tuple[str, Optional[Dict[str, Any]]]] = []
    for ref_id in ids:
        out.append((ref_id, await collection.get(ref_id, owner_id)))
    return out


def _entry(entry_id: str, fields: Tuple[str, ...], doc: Optional[Dict[str, Any]], **extra: Any) -> Dict[str, Any]:
    if doc is None:
        row: Dict[str, Any] = {"id": entry_id, "exists": False}
        row.update({field: None for field in fields})
        row.update({key: None for key in extra})
        return row
    row = {"id": entry_id, "exists": True}
    for field in fields:
        row[field] = doc.get(field)
    row.update({key: doc.get(key) for key in extra})
    return row


def _resolved(items: List[Dict[str, Any]]) -> int:
    return sum(1 for item in items if item.get("exists"))


# ------------------------------------------------------------------ bundle
async def resolve_evidence_bundle(owner_id: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve every referenced id into a readable, tolerant evidence bundle."""
    evidence = _normalize_evidence(candidate.get("evidence") or {})
    evidence_errors: List[str] = []

    async def _load(key: str):
        entries = await _fetch(_collection_for(key), owner_id, evidence.get(key, []))
        for ref_id, doc in entries:
            if doc is None:
                evidence_errors.append(f"{key}:{ref_id} not found")
        return entries

    exp_entries = await _load("experiment_ids")
    run_entries = await _load("run_ids")
    eval_entries = await _load("evaluation_ids")
    regression_entries = await _load("regression_ids")
    red_team_entries = await _load("red_team_experiment_ids")
    finding_entries = await _load("finding_ids")
    scorecard_entries = await _load("scorecard_ids")
    dataset_version_entries = await _load("dataset_version_ids")
    rubric_entries = await _load("rubric_ids")
    plan_entries = await _load("benchmark_plan_ids")
    agent_version_entries = await _load("agent_version_ids")
    prompt_version_entries = await _load("prompt_version_ids")
    context_pack_version_entries = await _load("context_pack_version_ids")
    drift_analysis_entries = await _load("drift_analysis_ids")

    experiments = [
        _entry(
            ref_id,
            ("title", "status", "experiment_type", "protocol_hash", "configuration_hash",
             "dataset_id", "dataset_version_id", "project_id"),
            doc,
        )
        for ref_id, doc in exp_entries
    ]
    runs = [
        _entry(
            ref_id,
            ("status", "model_id", "experiment_id", "configuration_id", "dataset_case_id"),
            doc,
        )
        for ref_id, doc in run_entries
    ]

    evaluations: List[Dict[str, Any]] = []
    evaluator_versions: List[Dict[str, Any]] = []
    seen_evaluators = set()
    for ref_id, doc in eval_entries:
        if doc is None:
            evaluations.append({
                "id": ref_id,
                "run_id": None,
                "evaluator_type": None,
                "outcome": None,
                "result": None,
                "provenance": {
                    "evaluator_version": None,
                    "schema_version": None,
                    "rubric_id": None,
                    "rubric_version": None,
                },
                "exists": False,
            })
            continue
        provenance = doc.get("provenance") or {}
        evaluator_version = provenance.get("evaluator_version")
        schema_version = provenance.get("schema_version")
        evaluations.append({
            "id": ref_id,
            "run_id": doc.get("run_id"),
            "evaluator_type": doc.get("evaluation_type") or doc.get("evaluator_type"),
            "outcome": (doc.get("result") or {}).get("outcome"),
            "result": doc.get("result"),
            "provenance": {
                "evaluator_version": evaluator_version,
                "schema_version": schema_version,
                "rubric_id": provenance.get("rubric_id"),
                "rubric_version": provenance.get("rubric_version"),
            },
            "exists": True,
        })
        marker = (evaluator_version, schema_version)
        if evaluator_version is not None or schema_version is not None:
            if marker not in seen_evaluators:
                seen_evaluators.add(marker)
                evaluator_versions.append({
                    "evaluator_version": evaluator_version,
                    "schema_version": schema_version,
                })

    regressions: List[Dict[str, Any]] = []
    for ref_id, doc in regression_entries:
        if doc is None:
            regressions.append({
                "id": ref_id,
                "baseline": {"configuration_id": None, "label": None},
                "candidate": {"configuration_id": None, "label": None},
                "summary": None,
                "case_counts": None,
                "exists": False,
            })
            continue
        baseline = doc.get("baseline") or {}
        candidate_block = doc.get("candidate") or {}
        regressions.append({
            "id": ref_id,
            "baseline": {
                "configuration_id": baseline.get("configuration_id"),
                "label": baseline.get("label"),
            },
            "candidate": {
                "configuration_id": candidate_block.get("configuration_id"),
                "label": candidate_block.get("label"),
            },
            "summary": doc.get("summary"),
            "case_counts": doc.get("case_counts"),
            "exists": True,
        })

    red_team_experiments = [
        _entry(ref_id, ("title", "status"), doc)
        for ref_id, doc in red_team_entries
    ]
    findings = [
        _entry(ref_id, ("title", "status", "confidence"), doc)
        for ref_id, doc in finding_entries
    ]

    scorecards: List[Dict[str, Any]] = []
    for ref_id, doc in scorecard_entries:
        if doc is None:
            scorecards.append({
                "id": ref_id,
                "builder_version": None,
                "scope": None,
                "composite_available": None,
                "exists": False,
            })
            continue
        composite = doc.get("composite") or {}
        scorecards.append({
            "id": ref_id,
            "builder_version": doc.get("builder_version"),
            "scope": doc.get("scope"),
            "composite_available": bool(composite.get("available")),
            "exists": True,
        })

    dataset_versions = [
        _entry(ref_id, ("dataset_id", "version", "status", "content_hash", "frozen_at"), doc)
        for ref_id, doc in dataset_version_entries
    ]
    rubrics = [
        _entry(ref_id, ("version",), doc)
        for ref_id, doc in rubric_entries
    ]
    benchmark_plans = [
        _entry(ref_id, ("status", "plan_hash", "total_planned_calls"), doc)
        for ref_id, doc in plan_entries
    ]

    # Unresolved failures linked to evidence runs or evidence experiments.
    run_ids = evidence.get("run_ids") or []
    experiment_ids = evidence.get("experiment_ids") or []
    unresolved_failures: List[Dict[str, Any]] = []
    if run_ids or experiment_ids:
        or_clauses: List[Dict[str, Any]] = []
        if run_ids:
            or_clauses.append({"run_id": {"$in": run_ids}})
        if experiment_ids:
            or_clauses.append({"experiment_id": {"$in": experiment_ids}})
        failure_docs = await repo.failures.list(
            {
                "owner_id": owner_id,
                "status": {"$in": list(_UNRESOLVED_STATUSES)},
                "$or": or_clauses,
            },
            sort_field="created_at",
            descending=True,
            limit=500,
        )
        unresolved_failures = [
            {
                "id": doc.get("id"),
                "run_id": doc.get("run_id"),
                "experiment_id": doc.get("experiment_id"),
                "failure_domain": doc.get("failure_domain"),
                "taxonomy_code": doc.get("taxonomy_code"),
                "severity": doc.get("severity"),
                "status": doc.get("status"),
                "title": doc.get("title"),
            }
            for doc in failure_docs
        ]

    # Additive (V6.10): resolved frozen-artifact snapshots.
    agent_versions = [
        _entry(
            ref_id,
            ("agent_id", "version", "status", "model_id", "prompt_version_id", "content_hash"),
            doc,
        )
        for ref_id, doc in agent_version_entries
    ]
    prompt_versions = [
        _entry(
            ref_id,
            ("prompt_id", "prompt_spec_id", "version", "status", "content_hash"),
            doc,
        )
        for ref_id, doc in prompt_version_entries
    ]
    context_pack_versions: List[Dict[str, Any]] = []
    for ref_id, doc in context_pack_version_entries:
        if doc is None:
            context_pack_versions.append({
                "id": ref_id,
                "pack_id": None,
                "version": None,
                "status": None,
                "content_hash": None,
                "item_count": None,
                "exists": False,
            })
            continue
        items = doc.get("items")
        context_pack_versions.append({
            "id": ref_id,
            "pack_id": doc.get("pack_id"),
            "version": doc.get("version"),
            "status": doc.get("status"),
            "content_hash": doc.get("content_hash"),
            "item_count": len(items) if isinstance(items, list) else None,
            "exists": True,
        })

    drift_analyses: List[Dict[str, Any]] = []
    for ref_id, doc in drift_analysis_entries:
        if doc is None:
            drift_analyses.append({
                "id": ref_id,
                "scope": None,
                "summary": None,
                "findings_count": None,
                "created_at": None,
                "exists": False,
            })
            continue
        findings = doc.get("findings")
        summary = doc.get("summary")
        findings_count = (
            len(findings)
            if isinstance(findings, list)
            else (summary or {}).get("total") if isinstance(summary, dict) else None
        )
        drift_analyses.append({
            "id": ref_id,
            "scope": doc.get("scope"),
            "summary": summary,
            "findings_count": findings_count,
            "created_at": doc.get("created_at"),
            "exists": True,
        })

    # Additive (V6.10): unresolved drift failures (canonical QUALITY.*_DRIFT
    # taxonomy codes) restricted to evidence runs/experiments, kept separate
    # from the general unresolved_failures list above.
    from .drift import drift_failure_codes

    unresolved_drift_failures: List[Dict[str, Any]] = []
    if run_ids or experiment_ids:
        drift_or_clauses: List[Dict[str, Any]] = []
        if run_ids:
            drift_or_clauses.append({"run_id": {"$in": run_ids}})
        if experiment_ids:
            drift_or_clauses.append({"experiment_id": {"$in": experiment_ids}})
        drift_docs = await repo.failures.list(
            {
                "owner_id": owner_id,
                "status": {"$in": list(_UNRESOLVED_STATUSES)},
                "taxonomy_code": {"$in": list(drift_failure_codes())},
                "$or": drift_or_clauses,
            },
            sort_field="created_at",
            descending=True,
            limit=500,
        )
        unresolved_drift_failures = [
            {
                "id": doc.get("id"),
                "run_id": doc.get("run_id"),
                "experiment_id": doc.get("experiment_id"),
                "taxonomy_code": doc.get("taxonomy_code"),
                "severity": doc.get("severity"),
                "status": doc.get("status"),
                "title": doc.get("title"),
            }
            for doc in drift_docs
        ]

    versions = {
        "dataset_versions": [
            {"id": item["id"], "version": item.get("version"), "content_hash": item.get("content_hash")}
            for item in dataset_versions
            if item.get("exists")
        ],
        "rubrics": [
            {"id": item["id"], "version": item.get("version")}
            for item in rubrics
            if item.get("exists")
        ],
        "evaluators": evaluator_versions,
    }

    environment = dict(candidate.get("environment") or {})
    if not environment.get("runner_version"):
        environment["runner_version"] = next(
            (item.get("runner_version") for item in experiments if item.get("runner_version")),
            _RUNNER_VERSION_DEFAULT,
        )
    if not environment.get("red_team_version"):
        environment["red_team_version"] = _RED_TEAM_VERSION_DEFAULT
    if not environment.get("scorecard_builder_version"):
        environment["scorecard_builder_version"] = next(
            (item.get("builder_version") for item in scorecards if item.get("builder_version")),
            _SCORECARD_BUILDER_VERSION_DEFAULT,
        )
    if not environment.get("evaluator_version"):
        environment["evaluator_version"] = next(
            (item.get("evaluator_version") for item in evaluator_versions if item.get("evaluator_version")),
            EVALUATOR_VERSION,
        )
    environment["certification_policy_version"] = CERTIFICATION_POLICY_VERSION
    environment["schema_version"] = CERTIFICATION_SCHEMA_VERSION
    environment = redaction.redact(environment)

    kind_lists = {
        "experiments": experiments,
        "runs": runs,
        "evaluations": evaluations,
        "regressions": regressions,
        "red_team_experiments": red_team_experiments,
        "findings": findings,
        "scorecards": scorecards,
        "dataset_versions": dataset_versions,
        "rubrics": rubrics,
        "benchmark_plans": benchmark_plans,
        "agent_versions": agent_versions,
        "prompt_versions": prompt_versions,
        "context_pack_versions": context_pack_versions,
        "drift_analyses": drift_analyses,
    }
    kind_rows: List[Dict[str, Any]] = []
    for key, kind in EVIDENCE_KINDS.items():
        items = kind_lists[kind]
        missing = [item["id"] for item in items if not item.get("exists")]
        kind_rows.append({
            "kind": kind,
            "referenced": len(evidence.get(key) or []),
            "resolved": _resolved(items),
            "missing": missing,
        })

    return {
        "experiments": experiments,
        "runs": runs,
        "evaluations": evaluations,
        "regressions": regressions,
        "red_team_experiments": red_team_experiments,
        "findings": findings,
        "scorecards": scorecards,
        "dataset_versions": dataset_versions,
        "rubrics": rubrics,
        "benchmark_plans": benchmark_plans,
        "agent_versions": agent_versions,
        "prompt_versions": prompt_versions,
        "context_pack_versions": context_pack_versions,
        "drift_analyses": drift_analyses,
        "versions": versions,
        "environment": environment,
        "unresolved_failures": unresolved_failures,
        "unresolved_drift_failures": unresolved_drift_failures,
        "completeness": {
            "kinds": kind_rows,
            "complete": all(not row["missing"] for row in kind_rows) and not evidence_errors,
        },
        "evidence_errors": evidence_errors,
    }


# ------------------------------------------------------------------ candidate lifecycle
async def create_candidate(owner_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    body = body or {}
    project_id = body.get("project_id")
    if not project_id:
        raise CertificationError("project_id is required")
    project = await repo.projects.get(project_id, owner_id)
    if not project:
        raise CertificationError("Project not found for this owner")

    label = body.get("label")
    if not isinstance(label, str) or not label.strip():
        raise CertificationError("label is required")

    subject = _validate_subject(body.get("subject") or {})
    evidence = await _validate_evidence(owner_id, project_id, body.get("evidence") or {})
    environment = redaction.safe_metadata(body.get("environment") or {})

    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "label": label.strip(),
        "subject": subject,
        "evidence": evidence,
        "environment": environment,
        "notes": body.get("notes") or "",
        "state": "DRAFT",
        "automatic_certification": False,
        "readiness": None,
        "readiness_history": [],
        "schema_version": CERTIFICATION_SCHEMA_VERSION,
        "policy_version": CERTIFICATION_POLICY_VERSION,
        "created_at": now,
        "updated_at": now,
    }
    await repo.certification_candidates.insert(doc)
    return doc


async def _red_team_criterion(
    owner_id: str, evidence: Dict[str, Any]
) -> Tuple[bool, str]:
    red_team_ids = evidence.get("red_team_experiment_ids") or []
    if not red_team_ids:
        return True, "no red-team evidence referenced"
    problems: List[str] = []
    for experiment_id in red_team_ids:
        evaluations = await repo.evaluations.list(
            {"owner_id": owner_id, "experiment_id": experiment_id},
            sort_field="created_at",
            descending=False,
            limit=1000,
        )
        if not evaluations:
            problems.append(f"{experiment_id}: no evaluations")
            continue
        failures = [
            evaluation
            for evaluation in evaluations
            if (evaluation.get("result") or {}).get("outcome") == "FAIL"
        ]
        if failures:
            problems.append(f"{experiment_id}: {len(failures)} FAIL boundary evaluation(s)")
    if problems:
        return False, "; ".join(problems)
    return True, f"{len(red_team_ids)} red-team experiment(s) held boundaries"


async def evaluate_readiness(
    owner_id: str, candidate_id: str, persist: bool = True
) -> Dict[str, Any]:
    candidate = await repo.certification_candidates.get(candidate_id, owner_id)
    if not candidate:
        raise CertificationNotFound("Certification candidate not found")

    bundle = await resolve_evidence_bundle(owner_id, candidate)
    evidence = _normalize_evidence(candidate.get("evidence") or {})

    experiments = bundle["experiments"]
    runs = bundle["runs"]
    evaluations = bundle["evaluations"]
    unresolved = bundle["unresolved_failures"]

    resolved_experiments = _resolved(experiments)
    resolved_runs = _resolved(runs)
    resolved_evaluations = _resolved(evaluations)

    criteria: List[Dict[str, Any]] = []

    evidence_present = resolved_experiments >= 1 and resolved_runs >= 1 and resolved_evaluations >= 1
    criteria.append({
        "key": "evidence_present",
        "passed": evidence_present,
        "detail": (
            f"experiments={resolved_experiments}, runs={resolved_runs}, "
            f"evaluations={resolved_evaluations}"
        ),
    })

    dataset_version_ids = evidence.get("dataset_version_ids") or []
    dataset_versions = bundle["dataset_versions"]
    if not dataset_version_ids:
        frozen_passed = False
        frozen_detail = "no dataset version referenced"
    elif any(not item.get("exists") for item in dataset_versions):
        frozen_passed = False
        frozen_detail = "referenced dataset version(s) could not be resolved"
    else:
        not_frozen = [item["id"] for item in dataset_versions if item.get("status") != "frozen"]
        if not_frozen:
            frozen_passed = False
            frozen_detail = f"non-frozen dataset version(s): {', '.join(not_frozen)}"
        else:
            frozen_passed = True
            frozen_detail = f"{len(dataset_versions)} frozen dataset version(s)"
    criteria.append({
        "key": "dataset_versions_frozen",
        "passed": frozen_passed,
        "detail": frozen_detail,
    })

    security_failures = [f for f in unresolved if f.get("failure_domain") == "SECURITY"]
    criteria.append({
        "key": "no_unresolved_security_failures",
        "passed": not security_failures,
        "detail": (
            "no unresolved SECURITY failures"
            if not security_failures
            else f"{len(security_failures)} unresolved SECURITY failure(s)"
        ),
    })

    high_quality_failures = [
        f
        for f in unresolved
        if f.get("failure_domain") in ("QUALITY", "RESEARCH")
        and f.get("severity") in _HIGH_SEVERITIES
    ]
    criteria.append({
        "key": "no_unresolved_high_quality_failures",
        "passed": not high_quality_failures,
        "detail": (
            "no unresolved high/critical quality failures"
            if not high_quality_failures
            else f"{len(high_quality_failures)} unresolved high/critical quality failure(s)"
        ),
    })

    red_team_passed, red_team_detail = await _red_team_criterion(owner_id, evidence)
    criteria.append({
        "key": "red_team_boundaries_held_or_absent",
        "passed": red_team_passed,
        "detail": red_team_detail,
    })

    scorecard_ids = evidence.get("scorecard_ids") or []
    resolved_scorecards = _resolved(bundle["scorecards"])
    scorecards_passed = bool(scorecard_ids) and resolved_scorecards >= 1
    criteria.append({
        "key": "scorecards_present",
        "passed": scorecards_passed,
        "detail": (
            f"scorecards referenced={len(scorecard_ids)}, resolved={resolved_scorecards}"
        ),
    })

    environment = candidate.get("environment") or {}
    environment_passed = bool(environment)
    criteria.append({
        "key": "environment_captured",
        "passed": environment_passed,
        "detail": (
            f"environment keys={len(environment)}" if environment_passed
            else "no environment captured"
        ),
    })

    async def _frozen_version_criterion(
        key: str, evidence_key: str, items: List[Dict[str, Any]], label: str
    ) -> Dict[str, Any]:
        referenced = evidence.get(evidence_key) or []
        if not referenced:
            return {"key": key, "passed": True, "detail": f"no {label} referenced"}
        missing = [item["id"] for item in items if not item.get("exists")]
        if missing:
            return {
                "key": key,
                "passed": False,
                "detail": f"unresolved {label}(s): {', '.join(missing)}",
            }
        not_frozen = [
            item["id"]
            for item in items
            if item.get("status") not in ("frozen", "deprecated")
        ]
        if not_frozen:
            return {
                "key": key,
                "passed": False,
                "detail": (
                    f"non-frozen {label}(s): {', '.join(not_frozen)} "
                    "(a draft build artifact cannot be certified)"
                ),
            }
        return {
            "key": key,
            "passed": True,
            "detail": f"{len(items)} frozen {label}(s)",
        }

    criteria.append(await _frozen_version_criterion(
        "agent_versions_frozen", "agent_version_ids",
        bundle.get("agent_versions") or [], "agent version",
    ))
    criteria.append(await _frozen_version_criterion(
        "prompt_versions_frozen", "prompt_version_ids",
        bundle.get("prompt_versions") or [], "prompt version",
    ))
    criteria.append(await _frozen_version_criterion(
        "context_pack_versions_frozen", "context_pack_version_ids",
        bundle.get("context_pack_versions") or [], "context pack version",
    ))

    high_drift_failures = [
        failure
        for failure in (bundle.get("unresolved_drift_failures") or [])
        if failure.get("severity") in _HIGH_SEVERITIES
    ]
    criteria.append({
        "key": "no_unresolved_high_drift",
        "passed": not high_drift_failures,
        "detail": (
            "no unresolved high/critical drift failures"
            if not high_drift_failures
            else f"{len(high_drift_failures)} unresolved high/critical drift failure(s)"
        ),
    })

    all_passed = all(criterion["passed"] for criterion in criteria)
    security_ok = next(c for c in criteria if c["key"] == "no_unresolved_security_failures")["passed"]
    quality_failures_ok = next(c for c in criteria if c["key"] == "no_unresolved_high_quality_failures")["passed"]
    high_drift_ok = next(c for c in criteria if c["key"] == "no_unresolved_high_drift")["passed"]

    if all_passed:
        state = "READY_FOR_CERTIFICATION"
    elif not (security_ok and quality_failures_ok and high_drift_ok):
        state = "BLOCKED"
    else:
        state = "NOT_READY"

    bundle_summary = {
        "complete": bundle["completeness"]["complete"],
        "kinds": bundle["completeness"]["kinds"],
        "evidence_errors": bundle["evidence_errors"],
        "counts": {
            kind: _resolved(items)
            for kind, items in {
                "experiments": experiments,
                "runs": runs,
                "evaluations": evaluations,
                "regressions": bundle["regressions"],
                "red_team_experiments": bundle["red_team_experiments"],
                "findings": bundle["findings"],
                "scorecards": bundle["scorecards"],
                "dataset_versions": dataset_versions,
                "rubrics": bundle["rubrics"],
                "benchmark_plans": bundle["benchmark_plans"],
                "agent_versions": bundle.get("agent_versions") or [],
                "prompt_versions": bundle.get("prompt_versions") or [],
                "context_pack_versions": bundle.get("context_pack_versions") or [],
                "drift_analyses": bundle.get("drift_analyses") or [],
            }.items()
        },
    }

    result = {
        "candidate_id": candidate.get("id"),
        "state": state,
        "ready": state == "READY_FOR_CERTIFICATION",
        "policy": {
            "id": READINESS_POLICY["id"],
            "version": READINESS_POLICY["version"],
        },
        "criteria": criteria,
        "unresolved_failures": unresolved,
        "bundle_summary": bundle_summary,
        "evaluated_at": now_iso(),
        "automatic_certification": False,
        "certification_performed": False,
    }

    if persist:
        history = list(candidate.get("readiness_history") or [])
        history.append(result)
        history = history[-50:]
        await repo.certification_candidates.update(candidate_id, owner_id, {
            "readiness": result,
            "readiness_history": history,
            "state": state,
            "updated_at": now_iso(),
        })
    return result


async def get_candidate(owner_id: str, candidate_id: str) -> Dict[str, Any]:
    candidate = await repo.certification_candidates.get(candidate_id, owner_id)
    if not candidate:
        raise CertificationNotFound("Certification candidate not found")
    return candidate


async def list_candidates(
    owner_id: str,
    project_id: Optional[str] = None,
    state: Optional[str] = None,
    subject_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    query: Dict[str, Any] = {"owner_id": owner_id}
    if project_id:
        query["project_id"] = project_id
    if state:
        query["state"] = state
    if subject_type:
        query["subject.type"] = subject_type
    total = await repo.certification_candidates.count(query)
    items = await repo.certification_candidates.list(
        query, sort_field="created_at", descending=True, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def update_candidate(
    owner_id: str, candidate_id: str, patch: Dict[str, Any]
) -> Dict[str, Any]:
    existing = await repo.certification_candidates.get(candidate_id, owner_id)
    if not existing:
        raise CertificationNotFound("Certification candidate not found")

    patch = patch or {}
    update: Dict[str, Any] = {}

    if patch.get("label") is not None:
        label = patch["label"]
        if not isinstance(label, str) or not label.strip():
            raise CertificationError("label must be a non-empty string")
        update["label"] = label.strip()
    if patch.get("notes") is not None:
        update["notes"] = patch["notes"]

    if patch.get("environment") is not None:
        if existing.get("state") != "DRAFT":
            raise CertificationError("Environment can only be edited while the candidate is DRAFT")
        update["environment"] = redaction.safe_metadata(patch["environment"])

    if patch.get("evidence") is not None:
        if existing.get("state") != "DRAFT":
            raise CertificationError("Evidence can only be edited while the candidate is DRAFT")
        update["evidence"] = await _validate_evidence(
            owner_id, existing.get("project_id"), patch["evidence"]
        )

    update["updated_at"] = now_iso()
    updated = await repo.certification_candidates.update(candidate_id, owner_id, update)
    return updated or {**existing, **update}
