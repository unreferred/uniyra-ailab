"""V6.10 Scorecards & Certification Handoff extension tests.

Offline and deterministic: every record is inserted directly through the
repository under a throwaway ``pytest-`` owner purged by the ``as_user`` fixture.
The suite pins the additive V6.10 contract on top of the V5.10 readiness engine:

* candidates may reference frozen agent / prompt / context-pack versions and
  drift analyses as evidence (owner-scoped, same-project);
* the evidence bundle resolves those references into tolerant snapshots and
  surfaces unresolved canonical drift failures;
* readiness adds ``*_versions_frozen`` and ``no_unresolved_high_drift`` criteria
  and still NEVER emits a CERTIFIED state and never certifies automatically;
* the scorecard builder adds agent-evaluation dimensions where missing stays
  ``not_available`` (never zero) and the V6.9 ``drift`` dimension is preserved.
"""
from __future__ import annotations

import json
import uuid

from app import repository as repo
from app.deps import get_current_user
from app.models import new_id, now_iso
from app.services import certification as certification_service
from app.services import scorecards as scorecard_service

from _support import new_owner_id, purge_owner

API = "/api"
CERT = f"{API}/certification"


# ------------------------------------------------------------------ seeding
def _seed(
    client,
    owner,
    *,
    agent_status="frozen",
    prompt_status="frozen",
    context_pack_status="frozen",
    with_scorecard=True,
    drift_failure=None,
    security_failure=False,
    quality_failure=False,
    project_id=None,
):
    """Insert one complete V6.10 evidence chain and return its ids."""

    async def _run():
        now = now_iso()
        created_project = project_id is None
        pid = project_id or new_id()
        if created_project:
            await repo.projects.insert({
                "id": pid, "owner_id": owner, "name": "pytest-v610-project",
                "description": "", "tags": [], "status": "active",
                "created_at": now, "updated_at": now,
            })

        dataset_id = new_id()
        await repo.datasets.insert({
            "id": dataset_id, "owner_id": owner, "project_id": pid,
            "name": "pytest-v610-dataset", "description": "", "tags": [],
            "created_at": now, "updated_at": now,
        })
        dataset_version_id = new_id()
        await repo.dataset_versions.insert({
            "id": dataset_version_id, "owner_id": owner, "project_id": pid,
            "dataset_id": dataset_id, "version": 1, "status": "frozen",
            "description": "", "case_count": 1, "content_hash": "sha256:dataset",
            "frozen_at": now, "created_at": now, "updated_at": now,
        })

        rubric_id = new_id()
        await repo.rubrics.insert({
            "id": rubric_id, "owner_id": owner, "name": "pytest-v610-rubric",
            "description": "", "dimensions": [], "threshold": None,
            "scoring_scale": "0-1", "version": 1,
            "created_at": now, "updated_at": now,
        })

        experiment_id = new_id()
        await repo.experiments.insert({
            "id": experiment_id, "owner_id": owner, "project_id": pid,
            "title": "pytest-v610-experiment", "status": "COMPLETED",
            "experiment_type": "benchmark", "protocol_hash": "protocol-hash",
            "configuration_hash": "configuration-hash",
            "dataset_id": dataset_id, "dataset_version_id": dataset_version_id,
            "created_at": now, "updated_at": now,
        })
        run_id = new_id()
        await repo.runs.insert({
            "id": run_id, "owner_id": owner, "project_id": pid,
            "experiment_id": experiment_id, "execution_kind": "benchmark",
            "configuration_id": new_id(), "dataset_case_id": new_id(),
            "model_id": "pytest-model", "status": "success",
            "normalized_response": "ok", "created_at": now,
        })
        evaluation_id = new_id()
        await repo.evaluations.insert({
            "id": evaluation_id, "owner_id": owner, "project_id": pid,
            "experiment_id": experiment_id, "run_id": run_id,
            "evaluation_type": "rule", "evaluator_type": "rule",
            "status": "completed",
            "result": {"outcome": "PASS", "passed": True, "score": 1.0},
            "provenance": {
                "evaluator_version": "v5.1.0", "schema_version": 2,
                "rubric_id": rubric_id, "rubric_version": 1,
            },
            "created_at": now,
        })

        scorecard_id = new_id()
        if with_scorecard:
            await repo.scorecards.insert({
                "id": scorecard_id, "owner_id": owner, "project_id": pid,
                "run_id": run_id, "experiment_id": experiment_id,
                "builder_version": "v5.8.0", "scope": "run",
                "composite": {"available": False},
                "created_at": now, "updated_at": now,
            })

        benchmark_plan_id = new_id()
        await repo.benchmark_plans.insert({
            "id": benchmark_plan_id, "owner_id": owner, "project_id": pid,
            "experiment_id": experiment_id, "status": "completed",
            "plan_hash": "plan-hash", "total_planned_calls": 1,
            "created_at": now, "updated_at": now,
        })

        # --- V6.10 build artifacts -------------------------------------
        agent_id = new_id()
        await repo.agents.insert({
            "id": agent_id, "owner_id": owner, "project_id": pid,
            "name": "pytest-v610-agent", "status": "active",
            "description": "", "created_at": now, "updated_at": now,
        })
        prompt_id = new_id()
        await repo.prompts.insert({
            "id": prompt_id, "owner_id": owner, "project_id": pid,
            "name": "pytest-v610-prompt", "status": "draft",
            "description": "", "purpose": "",
            "created_at": now, "updated_at": now,
        })
        prompt_version_id = new_id()
        await repo.prompt_versions.insert({
            "id": prompt_version_id, "owner_id": owner, "project_id": pid,
            "prompt_id": prompt_id, "prompt_spec_id": prompt_id,
            "version": 1, "status": prompt_status,
            "content": "Answer the task.", "content_hash": "sha256:prompt",
            "created_at": now, "updated_at": now,
        })
        agent_version_id = new_id()
        await repo.agent_versions.insert({
            "id": agent_version_id, "owner_id": owner, "project_id": pid,
            "agent_id": agent_id, "version": 1, "status": agent_status,
            "model_id": "pytest-model", "prompt_version_id": prompt_version_id,
            "content_hash": "sha256:agent", "frozen_at": now,
            "created_at": now, "updated_at": now,
        })

        context_pack_id = new_id()
        await repo.context_packs.insert({
            "id": context_pack_id, "owner_id": owner, "project_id": pid,
            "name": "pytest-v610-pack", "status": "draft",
            "created_at": now, "updated_at": now,
        })
        context_pack_version_id = new_id()
        await repo.context_pack_versions.insert({
            "id": context_pack_version_id, "owner_id": owner, "project_id": pid,
            "pack_id": context_pack_id, "version": 1, "status": context_pack_status,
            "items": [{"source_type": "PROMPT", "ref_id": prompt_id}],
            "content_hash": "sha256:cp",
            "created_at": now, "updated_at": now,
        })

        drift_analysis_id = new_id()
        await repo.drift_analyses.insert({
            "id": drift_analysis_id, "owner_id": owner, "project_id": pid,
            "scope": "architecture", "version": "v6.9.0",
            "run_id": run_id, "experiment_id": experiment_id,
            "findings": [{
                "id": new_id(), "drift_type": "ARCHITECTURE_DRIFT",
                "category": "architecture_drift", "severity": "high",
                "message": "parallel subsystem", "source": "deterministic",
                "evidence": {"path": "app/services/legacy/billing.py"},
                "provenance": {"check_id": "architecture.parallel_subsystem"},
            }],
            "summary": {"total": 1, "deterministic_count": 1, "judge_derived_count": 0},
            "notes": "", "created_at": now, "schema_version": 1,
        })

        if security_failure:
            await repo.failures.insert({
                "id": f"pytest-failure-{uuid.uuid4().hex}",
                "owner_id": owner, "project_id": pid,
                "run_id": run_id, "experiment_id": experiment_id,
                "failure_domain": "SECURITY", "taxonomy_code": "SECURITY.TOOL_ACCESS",
                "severity": "high", "status": "OPEN",
                "title": "pytest security failure", "created_at": now,
            })
        if quality_failure:
            await repo.failures.insert({
                "id": f"pytest-failure-{uuid.uuid4().hex}",
                "owner_id": owner, "project_id": pid,
                "run_id": run_id, "experiment_id": experiment_id,
                "failure_domain": "QUALITY", "taxonomy_code": "QUALITY.WRONG_ANSWER",
                "severity": "high", "status": "OPEN",
                "title": "pytest quality failure", "created_at": now,
            })
        if drift_failure:
            await repo.failures.insert({
                "id": f"pytest-failure-{uuid.uuid4().hex}",
                "owner_id": owner, "project_id": pid,
                "run_id": run_id, "experiment_id": experiment_id,
                "failure_domain": drift_failure.get("failure_domain", "QUALITY"),
                "taxonomy_code": drift_failure.get("taxonomy_code", "QUALITY.ARCHITECTURE_DRIFT"),
                "severity": drift_failure.get("severity", "high"),
                "status": drift_failure.get("status", "OPEN"),
                "title": "pytest drift failure", "created_at": now,
            })

        return {
            "project_id": pid,
            "dataset_id": dataset_id,
            "dataset_version_id": dataset_version_id,
            "rubric_id": rubric_id,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "evaluation_id": evaluation_id,
            "scorecard_id": scorecard_id,
            "benchmark_plan_id": benchmark_plan_id,
            "agent_id": agent_id,
            "agent_version_id": agent_version_id,
            "prompt_id": prompt_id,
            "prompt_version_id": prompt_version_id,
            "context_pack_id": context_pack_id,
            "context_pack_version_id": context_pack_version_id,
            "drift_analysis_id": drift_analysis_id,
        }

    return client.portal.call(_run)


def _payload(ids, *, include_scorecard=True, environment=None, subject=None,
             evidence_overrides=None):
    evidence = {
        "experiment_ids": [ids["experiment_id"]],
        "run_ids": [ids["run_id"]],
        "evaluation_ids": [ids["evaluation_id"]],
        "dataset_version_ids": [ids["dataset_version_id"]],
        "rubric_ids": [ids["rubric_id"]],
        "benchmark_plan_ids": [ids["benchmark_plan_id"]],
        "agent_version_ids": [ids["agent_version_id"]],
        "prompt_version_ids": [ids["prompt_version_id"]],
        "context_pack_version_ids": [ids["context_pack_version_id"]],
        "drift_analysis_ids": [ids["drift_analysis_id"]],
    }
    if include_scorecard:
        evidence["scorecard_ids"] = [ids["scorecard_id"]]
    if evidence_overrides:
        evidence.update(evidence_overrides)
    return {
        "project_id": ids["project_id"],
        "label": f"pytest-v610-candidate-{uuid.uuid4().hex[:6]}",
        "subject": subject if subject is not None else {
            "type": "agent",
            "agent_version_id": ids["agent_version_id"],
            "label": "Pytest Agent",
        },
        "evidence": evidence,
        "environment": environment if environment is not None else {
            "runner_version": "1", "os": "linux",
        },
        "notes": "pytest v6.10 candidate",
    }


def _create(client, payload):
    r = client.post(f"{CERT}/candidates", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _readiness(client, candidate_id):
    r = client.post(f"{CERT}/candidates/{candidate_id}/readiness")
    assert r.status_code == 200, r.text
    return r.json()


def _criterion(result, key):
    return next(item for item in result["criteria"] if item["key"] == key)


def _bundle(client, owner, candidate_id):
    async def _resolve():
        candidate = await repo.certification_candidates.get(candidate_id, owner)
        return await certification_service.resolve_evidence_bundle(owner, candidate)

    return client.portal.call(_resolve)


def _foreign_user():
    return {
        "id": f"pytest-foreign-{uuid.uuid4().hex}",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }


# ================================================================== 1
def test_candidate_with_build_artifacts_starts_draft_and_never_certified(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner)
    created = _create(app_client, _payload(ids))

    assert created["state"] == "DRAFT"
    assert created["automatic_certification"] is False
    assert created["readiness"] is None
    assert created["subject"]["type"] == "agent"
    # All four new evidence keys survive normalization.
    for key in ("agent_version_ids", "prompt_version_ids",
                "context_pack_version_ids", "drift_analysis_ids"):
        assert created["evidence"][key] == [ids[{
            "agent_version_ids": "agent_version_id",
            "prompt_version_ids": "prompt_version_id",
            "context_pack_version_ids": "context_pack_version_id",
            "drift_analysis_ids": "drift_analysis_id",
        }[key]]]
    assert "CERTIFIED" not in json.dumps(created)
    assert "CERTIFIED" not in certification_service.CANDIDATE_STATES


# ================================================================== 2
def test_fully_frozen_evidence_is_ready_for_certification(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner)
    created = _create(app_client, _payload(ids))
    result = _readiness(app_client, created["id"])

    assert result["state"] == "READY_FOR_CERTIFICATION"
    assert result["ready"] is True
    assert result["certification_performed"] is False
    assert result["automatic_certification"] is False
    assert all(item["passed"] for item in result["criteria"])
    assert _criterion(result, "agent_versions_frozen")["passed"] is True
    assert _criterion(result, "prompt_versions_frozen")["passed"] is True
    assert _criterion(result, "context_pack_versions_frozen")["passed"] is True
    assert _criterion(result, "no_unresolved_high_drift")["passed"] is True


# ================================================================== 3
def test_draft_agent_version_not_ready(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, agent_status="draft")
    created = _create(app_client, _payload(ids))
    result = _readiness(app_client, created["id"])

    assert result["ready"] is False
    assert result["state"] == "NOT_READY"
    criterion = _criterion(result, "agent_versions_frozen")
    assert criterion["passed"] is False
    assert "non-frozen agent version" in criterion["detail"]
    assert ids["agent_version_id"] in criterion["detail"]


# ================================================================== 4
def test_draft_prompt_version_not_ready(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, prompt_status="draft")
    created = _create(app_client, _payload(ids))
    result = _readiness(app_client, created["id"])

    assert result["ready"] is False
    assert result["state"] == "NOT_READY"
    criterion = _criterion(result, "prompt_versions_frozen")
    assert criterion["passed"] is False
    assert ids["prompt_version_id"] in criterion["detail"]


# ================================================================== 5
def test_unresolved_high_drift_blocks(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, drift_failure={
        "taxonomy_code": "QUALITY.ARCHITECTURE_DRIFT", "severity": "high",
    })
    created = _create(app_client, _payload(ids))
    result = _readiness(app_client, created["id"])

    assert result["ready"] is False
    assert result["state"] == "BLOCKED"
    criterion = _criterion(result, "no_unresolved_high_drift")
    assert criterion["passed"] is False
    assert "high/critical drift" in criterion["detail"]

    bundle = _bundle(app_client, owner, created["id"])
    assert bundle["unresolved_drift_failures"], "expected a canonical drift failure"
    entry = bundle["unresolved_drift_failures"][0]
    assert entry["taxonomy_code"] == "QUALITY.ARCHITECTURE_DRIFT"
    assert entry["severity"] == "high"

    # An info/low drift failure does not fail the high-drift criterion.
    low_ids = _seed(app_client, owner, drift_failure={
        "taxonomy_code": "QUALITY.LOGIC_DRIFT", "severity": "low",
    })
    low_created = _create(app_client, _payload(low_ids))
    low_result = _readiness(app_client, low_created["id"])
    assert _criterion(low_result, "no_unresolved_high_drift")["passed"] is True
    assert low_result["state"] == "READY_FOR_CERTIFICATION"


# ================================================================== 6
def test_unresolved_security_failure_blocks(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, security_failure=True)
    created = _create(app_client, _payload(ids))
    result = _readiness(app_client, created["id"])

    assert result["state"] == "BLOCKED"
    assert result["ready"] is False
    assert _criterion(result, "no_unresolved_security_failures")["passed"] is False
    assert any(f["failure_domain"] == "SECURITY" for f in result["unresolved_failures"])


# ================================================================== 7
def test_missing_scorecard_not_ready(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, with_scorecard=False)
    created = _create(app_client, _payload(ids, include_scorecard=False))
    result = _readiness(app_client, created["id"])

    assert result["ready"] is False
    assert result["state"] == "NOT_READY"
    criterion = _criterion(result, "scorecards_present")
    assert criterion["passed"] is False
    assert "scorecards referenced=0" in criterion["detail"]


# ================================================================== 8
def test_evidence_bundle_resolves_build_artifacts(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, drift_failure={
        "taxonomy_code": "QUALITY.PROMPT_DRIFT", "severity": "medium",
    })
    created = _create(app_client, _payload(ids))
    bundle = _bundle(app_client, owner, created["id"])

    agent_version = next(
        item for item in bundle["agent_versions"] if item["id"] == ids["agent_version_id"]
    )
    assert agent_version["exists"] is True
    assert agent_version["agent_id"] == ids["agent_id"]
    assert agent_version["version"] == 1
    assert agent_version["status"] == "frozen"
    assert agent_version["model_id"] == "pytest-model"
    assert agent_version["prompt_version_id"] == ids["prompt_version_id"]
    assert agent_version["content_hash"] == "sha256:agent"

    prompt_version = bundle["prompt_versions"][0]
    assert prompt_version["prompt_id"] == ids["prompt_id"]
    assert prompt_version["prompt_spec_id"] == ids["prompt_id"]
    assert prompt_version["status"] == "frozen"
    assert prompt_version["content_hash"] == "sha256:prompt"

    context_pack_version = bundle["context_pack_versions"][0]
    assert context_pack_version["pack_id"] == ids["context_pack_id"]
    assert context_pack_version["status"] == "frozen"
    assert context_pack_version["item_count"] == 1

    drift = bundle["drift_analyses"][0]
    assert drift["scope"] == "architecture"
    assert drift["findings_count"] == 1
    assert drift["summary"]["total"] == 1
    assert drift["created_at"]

    # Missing references resolve tolerantly rather than raising.
    missing = "pytest-missing-" + uuid.uuid4().hex
    async def _missing():
        candidate = {
            "owner_id": owner,
            "project_id": ids["project_id"],
            "evidence": {"agent_version_ids": [missing]},
        }
        return await certification_service.resolve_evidence_bundle(owner, candidate)
    missing_bundle = app_client.portal.call(_missing)
    assert missing_bundle["agent_versions"][0]["exists"] is False
    assert missing_bundle["completeness"]["complete"] is False


# ================================================================== 9
def test_owner_and_project_isolation(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner)
    created = _create(app_client, _payload(ids))

    # Foreign-owner reference: an artifact owned by someone else is not visible.
    foreign = new_owner_id()
    try:
        foreign_ids = _seed(app_client, foreign)
        foreign_ref = app_client.post(f"{CERT}/candidates", json={
            "project_id": ids["project_id"],
            "label": "foreign-ref",
            "subject": {"type": "agent"},
            "evidence": {"agent_version_ids": [foreign_ids["agent_version_id"]]},
            "environment": {"runner_version": "1"},
        })
        assert foreign_ref.status_code == 400, foreign_ref.text
    finally:
        app_client.portal.call(purge_owner, foreign)

    # Same-owner cross-project reference is rejected.
    other = _seed(app_client, owner)
    cross = app_client.post(f"{CERT}/candidates", json={
        "project_id": ids["project_id"],
        "label": "cross-project",
        "subject": {"type": "agent"},
        "evidence": {"agent_version_ids": [other["agent_version_id"]]},
        "environment": {"runner_version": "1"},
    })
    assert cross.status_code == 400, cross.text

    # A foreign user cannot read this owner's candidate.
    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = _foreign_user
    try:
        detail = app_client.get(f"{CERT}/candidates/{created['id']}")
        assert detail.status_code == 404, detail.text
        readiness = app_client.post(f"{CERT}/candidates/{created['id']}/readiness")
        assert readiness.status_code == 404, readiness.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = original


# ================================================================== 10
def test_no_endpoint_ever_returns_certified(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner)
    created = _create(app_client, _payload(ids))

    detail = app_client.get(f"{CERT}/candidates/{created['id']}")
    assert detail.status_code == 200, detail.text
    assert "CERTIFIED" not in json.dumps(detail.json())
    assert detail.json()["readiness"]["state"] in certification_service.CANDIDATE_STATES

    readiness = _readiness(app_client, created["id"])
    assert readiness["state"] in certification_service.CANDIDATE_STATES
    assert readiness["state"] != "CERTIFIED"
    assert "CERTIFIED" not in json.dumps(readiness)
    assert readiness["certification_performed"] is False

    listing = app_client.get(f"{CERT}/candidates")
    assert listing.status_code == 200, listing.text
    assert "CERTIFIED" not in json.dumps(listing.json())

    policy = app_client.get(f"{CERT}/policy").json()
    assert "CERTIFIED" not in json.dumps(policy)
    assert "CERTIFIED" not in certification_service.CANDIDATE_STATES


# ================================================================== 11
def test_scorecard_agent_evaluation_dimensions(app_client, as_user):
    owner = as_user["id"]
    project_id = "pytest-project-" + uuid.uuid4().hex

    async def _seed():
        now = now_iso()
        await repo.projects.insert({
            "id": project_id, "owner_id": owner, "name": "pytest-v610-sc",
            "description": "", "tags": [], "status": "active",
            "created_at": now, "updated_at": now,
        })
        run_id = new_id()
        await repo.runs.insert({
            "id": run_id, "owner_id": owner, "project_id": project_id,
            "status": "success", "normalized_response": "ok",
            "created_at": now,
        })
        return run_id

    run_id = app_client.portal.call(_seed)

    def _eval(evaluator_keys=None, *, outcome="PASS", raw_result=None,
              evaluator_type="rule", scores=None):
        outcome_passed = {"PASS": True, "FAIL": False, "NOT_EVALUATED": None}[outcome]
        doc = {
            "id": new_id(), "owner_id": owner, "project_id": project_id,
            "run_id": run_id, "evaluator_type": evaluator_type,
            "evaluation_type": evaluator_type, "eval_type": evaluator_type,
            "status": "completed" if outcome != "NOT_EVALUATED" else "not_evaluated",
            "scores": scores or {},
            "findings": [],
            "result": {
                "outcome": outcome, "status": "completed",
                "passed": outcome_passed,
                "score": 1.0 if outcome == "PASS" else (0.0 if outcome == "FAIL" else None),
            },
            "created_at": now_iso(),
        }
        if evaluator_keys:
            doc["evaluator_keys"] = list(evaluator_keys)
        if raw_result is not None:
            doc["raw_result"] = raw_result
        return doc

    docs = [
        # Top-level evaluator_keys shape: one PASS, one FAIL -> 0.5.
        _eval(["agent_policy_compliance"], outcome="PASS"),
        _eval(["agent_policy_compliance"], outcome="FAIL"),
        # raw_result.evaluators shape: pass -> 1.0.
        _eval(["agent_invariant_compliance"], outcome="PASS", raw_result={
            "evaluators": [{
                "evaluator_key": "agent_invariant_compliance",
                "status": "completed", "passed": True, "score": 1.0,
            }]
        }),
        _eval(["agent_context_usage"], outcome="PASS"),
        # assertions rule sub-result failure -> deterministic 0.0.
        _eval(["assertions"], outcome="FAIL", raw_result={
            "evaluators": [{
                "evaluator_key": "assertions",
                "status": "completed", "passed": False, "score": 0.0,
            }]
        }),
    ]

    async def _insert():
        for doc in docs:
            await repo.evaluations.insert(doc)

    app_client.portal.call(_insert)

    body = app_client.get(f"{API}/scorecards/run/{run_id}")
    assert body.status_code == 200, body.text
    dims = {d["key"]: d for d in body.json()["dimensions"]}

    assert dims["policy_compliance"]["state"] == "available"
    assert dims["policy_compliance"]["value"] == 0.5
    assert dims["policy_compliance"]["source"] == "deterministic"
    assert dims["policy_compliance"]["deterministic"] is True

    assert dims["invariant_compliance"]["state"] == "available"
    assert dims["invariant_compliance"]["value"] == 1.0

    assert dims["context_usage"]["state"] == "available"
    assert dims["context_usage"]["value"] == 1.0

    assert dims["task_completion"]["state"] == "available"
    assert dims["task_completion"]["value"] == 0.0
    assert dims["task_completion"]["source"] == "deterministic"

    # The V6.9 drift dimension is preserved and this run has a measured 0.0.
    assert dims["drift"]["state"] == "available"

    # The additive dimensions are registered on the public builder contract.
    for key in ("policy_compliance", "invariant_compliance",
                "task_completion", "context_usage"):
        assert key in scorecard_service.DIMENSION_ORDER

    # A judge-only task_completion score is labelled judge_derived.
    async def _seed_judge_run():
        jr_id = new_id()
        now = now_iso()
        await repo.runs.insert({
            "id": jr_id, "owner_id": owner, "project_id": project_id,
            "status": "success", "normalized_response": "ok", "created_at": now,
        })
        await repo.evaluations.insert({
            "id": new_id(), "owner_id": owner, "project_id": project_id,
            "run_id": jr_id, "evaluator_type": "judge_model",
            "evaluation_type": "judge_model", "eval_type": "ai_judge",
            "status": "completed", "scores": {"task_completion": 0.8},
            "findings": [],
            "result": {"outcome": "PASS", "status": "completed", "passed": True, "score": 0.8},
            "created_at": now_iso(),
        })
        return jr_id

    judge_run_id = app_client.portal.call(_seed_judge_run)
    judge_dims = {
        d["key"]: d
        for d in app_client.get(f"{API}/scorecards/run/{judge_run_id}").json()["dimensions"]
    }
    assert judge_dims["task_completion"]["state"] == "available"
    assert judge_dims["task_completion"]["value"] == 0.8
    assert judge_dims["task_completion"]["source"] == "judge_derived"
    assert judge_dims["task_completion"]["judge_derived"] is True

    # A run with no evaluations keeps every new dimension unavailable (never 0).
    async def _seed_empty_run():
        empty_id = new_id()
        await repo.runs.insert({
            "id": empty_id, "owner_id": owner, "project_id": project_id,
            "status": "success", "normalized_response": "ok", "created_at": now_iso(),
        })
        return empty_id

    empty_run_id = app_client.portal.call(_seed_empty_run)
    empty_dims = {
        d["key"]: d
        for d in app_client.get(f"{API}/scorecards/run/{empty_run_id}").json()["dimensions"]
    }
    for key in ("policy_compliance", "invariant_compliance", "task_completion", "context_usage"):
        assert empty_dims[key]["state"] == "not_available", key
        assert empty_dims[key]["value"] is None, key
        assert empty_dims[key]["value"] != 0, key
    assert empty_dims["drift"]["state"] == "not_available"
