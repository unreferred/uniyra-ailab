"""V5.10 certification readiness contract tests.

Offline only: every record is inserted directly via the repository under a
throwaway ``pytest-`` owner purged by the ``as_user`` fixture. The suite pins
the V5 -> V7 handoff contract: candidates start DRAFT, readiness is evaluated
explicitly, and no path ever emits a CERTIFIED state.
"""
from __future__ import annotations

import json
import uuid

from app import repository as repo
from app.deps import get_current_user
from app.models import new_id, now_iso
from app.services import certification as certification_service

API = "/api"
CERT = f"{API}/certification"


# ------------------------------------------------------------------ seeding
def _insert_failure(run_id, experiment_id, *, domain, severity):
    return {
        "id": f"pytest-failure-{uuid.uuid4().hex}",
        "owner_id": None,  # filled by caller
        "run_id": run_id,
        "experiment_id": experiment_id,
        "failure_domain": domain,
        "taxonomy_code": f"{domain}_CODE",
        "severity": severity,
        "status": "OPEN",
        "title": f"pytest {domain} failure",
        "created_at": now_iso(),
    }


def _seed(client, owner, *, dv_status="frozen", with_scorecard=True,
          security_failure=False, quality_failure=False):
    """Insert a project plus one complete evidence chain and return its ids."""

    async def _run():
        now = now_iso()
        project_id = new_id()
        await repo.projects.insert({
            "id": project_id, "owner_id": owner, "name": "pytest-cert-project",
            "description": "", "tags": [], "status": "active",
            "created_at": now, "updated_at": now,
        })

        dataset_id = new_id()
        await repo.datasets.insert({
            "id": dataset_id, "owner_id": owner, "project_id": project_id,
            "name": "pytest-cert-dataset", "description": "", "tags": [],
            "created_at": now, "updated_at": now,
        })

        dataset_version_id = new_id()
        await repo.dataset_versions.insert({
            "id": dataset_version_id, "owner_id": owner, "project_id": project_id,
            "dataset_id": dataset_id, "version": 1, "status": dv_status,
            "description": "", "case_count": 1,
            "content_hash": "sha256:pytest",
            "frozen_at": now if dv_status == "frozen" else None,
            "created_at": now, "updated_at": now,
        })

        rubric_id = new_id()
        await repo.rubrics.insert({
            "id": rubric_id, "owner_id": owner, "name": "pytest-cert-rubric",
            "description": "", "dimensions": [], "threshold": None,
            "scoring_scale": "0-1", "version": 1,
            "created_at": now, "updated_at": now,
        })

        experiment_id = new_id()
        await repo.experiments.insert({
            "id": experiment_id, "owner_id": owner, "project_id": project_id,
            "title": "pytest-cert-experiment", "status": "COMPLETED",
            "experiment_type": "benchmark", "protocol_hash": "protocol-hash",
            "configuration_hash": "configuration-hash",
            "dataset_id": dataset_id, "dataset_version_id": dataset_version_id,
            "created_at": now, "updated_at": now,
        })

        run_id = new_id()
        await repo.runs.insert({
            "id": run_id, "owner_id": owner, "project_id": project_id,
            "experiment_id": experiment_id, "execution_kind": "benchmark",
            "configuration_id": new_id(), "dataset_case_id": new_id(),
            "model_id": "pytest-model", "status": "success",
            "normalized_response": "ok", "created_at": now,
        })

        evaluation_id = new_id()
        await repo.evaluations.insert({
            "id": evaluation_id, "owner_id": owner, "project_id": project_id,
            "experiment_id": experiment_id, "run_id": run_id,
            "evaluation_type": "rule", "evaluator_type": "rule",
            "status": "completed",
            "result": {"outcome": "PASS", "passed": True, "score": 1.0},
            "provenance": {
                "evaluator_version": "v5.1.0",
                "schema_version": 2,
                "rubric_id": rubric_id,
                "rubric_version": 1,
            },
            "created_at": now,
        })

        scorecard_id = new_id()
        if with_scorecard:
            await repo.scorecards.insert({
                "id": scorecard_id, "owner_id": owner, "project_id": project_id,
                "builder_version": "v5.8.0", "scope": "experiment",
                "composite": {"available": False},
                "created_at": now, "updated_at": now,
            })

        benchmark_plan_id = new_id()
        await repo.benchmark_plans.insert({
            "id": benchmark_plan_id, "owner_id": owner, "project_id": project_id,
            "experiment_id": experiment_id, "status": "completed",
            "plan_hash": "plan-hash", "total_planned_calls": 1,
            "created_at": now, "updated_at": now,
        })

        if security_failure:
            failure = _insert_failure(run_id, experiment_id, domain="SECURITY", severity="high")
            failure["owner_id"] = owner
            await repo.failures.insert(failure)
        if quality_failure:
            failure = _insert_failure(run_id, experiment_id, domain="QUALITY", severity="high")
            failure["owner_id"] = owner
            await repo.failures.insert(failure)

        return {
            "project_id": project_id,
            "dataset_id": dataset_id,
            "dataset_version_id": dataset_version_id,
            "rubric_id": rubric_id,
            "experiment_id": experiment_id,
            "run_id": run_id,
            "evaluation_id": evaluation_id,
            "scorecard_id": scorecard_id,
            "benchmark_plan_id": benchmark_plan_id,
        }

    return client.portal.call(_run)


def _payload(ids, *, include_scorecard=True, environment=None):
    evidence = {
        "experiment_ids": [ids["experiment_id"]],
        "run_ids": [ids["run_id"]],
        "evaluation_ids": [ids["evaluation_id"]],
        "dataset_version_ids": [ids["dataset_version_id"]],
        "rubric_ids": [ids["rubric_id"]],
        "benchmark_plan_ids": [ids["benchmark_plan_id"]],
    }
    if include_scorecard:
        evidence["scorecard_ids"] = [ids["scorecard_id"]]
    return {
        "project_id": ids["project_id"],
        "label": f"pytest-candidate-{uuid.uuid4().hex[:6]}",
        "subject": {"type": "model", "model_id": "pytest-model", "label": "Pytest Model"},
        "evidence": evidence,
        "environment": environment if environment is not None else {"runner_version": "1", "os": "linux"},
        "notes": "pytest candidate",
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


# ================================================================== 1
def test_candidate_starts_draft_and_never_serializes_certified(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner)
    created = _create(app_client, _payload(ids))

    assert created["state"] == "DRAFT"
    assert created["automatic_certification"] is False
    assert created["readiness"] is None
    assert "CERTIFIED" not in json.dumps(created)
    assert "CERTIFIED" not in certification_service.CANDIDATE_STATES


# ================================================================== 2
def test_complete_evidence_is_ready_for_certification(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner)
    created = _create(app_client, _payload(ids))
    result = _readiness(app_client, created["id"])

    assert result["state"] == "READY_FOR_CERTIFICATION"
    assert result["ready"] is True
    assert result["certification_performed"] is False
    assert result["automatic_certification"] is False
    assert all(item["passed"] for item in result["criteria"])
    assert result["policy"] == {"id": "v5-readiness-policy", "version": "v5.10.0"}


# ================================================================== 3
def test_unresolved_security_failure_blocks(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, security_failure=True)
    created = _create(app_client, _payload(ids))
    result = _readiness(app_client, created["id"])

    assert result["state"] == "BLOCKED"
    assert result["ready"] is False
    assert _criterion(result, "no_unresolved_security_failures")["passed"] is False
    assert any(f["failure_domain"] == "SECURITY" for f in result["unresolved_failures"])


# ================================================================== 4
def test_unresolved_high_severity_quality_failure_not_ready(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, quality_failure=True)
    created = _create(app_client, _payload(ids))
    result = _readiness(app_client, created["id"])

    assert result["ready"] is False
    assert result["state"] != "READY_FOR_CERTIFICATION"
    assert _criterion(result, "no_unresolved_high_quality_failures")["passed"] is False


# ================================================================== 5
def test_missing_scorecard_evidence_not_ready(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, with_scorecard=False)
    created = _create(app_client, _payload(ids, include_scorecard=False))
    result = _readiness(app_client, created["id"])

    assert result["ready"] is False
    assert result["state"] == "NOT_READY"
    criterion = _criterion(result, "scorecards_present")
    assert criterion["passed"] is False
    assert "scorecards referenced=0" in criterion["detail"]


# ================================================================== 6
def test_non_frozen_dataset_version_not_ready(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, dv_status="draft")
    created = _create(app_client, _payload(ids))
    result = _readiness(app_client, created["id"])

    assert result["ready"] is False
    assert result["state"] == "NOT_READY"
    assert _criterion(result, "dataset_versions_frozen")["passed"] is False


# ================================================================== 7
def test_evidence_bundle_versions_environment_and_failures(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner, security_failure=True)
    created = _create(app_client, _payload(ids))

    async def _bundle():
        candidate = await repo.certification_candidates.get(created["id"], owner)
        return await certification_service.resolve_evidence_bundle(owner, candidate)

    bundle = app_client.portal.call(_bundle)

    assert bundle["versions"]["dataset_versions"] == [{
        "id": ids["dataset_version_id"], "version": 1, "content_hash": "sha256:pytest",
    }]
    assert bundle["versions"]["rubrics"] == [{"id": ids["rubric_id"], "version": 1}]
    assert {"evaluator_version": "v5.1.0", "schema_version": 2} in bundle["versions"]["evaluators"]
    assert bundle["environment"]["certification_policy_version"] == "v5.10.0"
    assert bundle["environment"]["scorecard_builder_version"] == "v5.8.0"
    assert bundle["completeness"]["complete"] is True
    assert bundle["unresolved_failures"]
    assert all(f["failure_domain"] == "SECURITY" for f in bundle["unresolved_failures"])


# ================================================================== 8
def test_no_automatic_certification_state_anywhere(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner)
    created = _create(app_client, _payload(ids))

    # Never calling readiness leaves the persisted state DRAFT.
    detail = app_client.get(f"{CERT}/candidates/{created['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["state"] == "DRAFT"
    assert "CERTIFIED" not in json.dumps(body)

    listing = app_client.get(f"{CERT}/candidates")
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert "CERTIFIED" not in json.dumps(listing.json())
    assert "CERTIFIED" not in certification_service.CANDIDATE_STATES


# ================================================================== 9
def test_owner_and_project_isolation(app_client, as_user):
    owner = as_user["id"]
    ids = _seed(app_client, owner)
    created = _create(app_client, _payload(ids))

    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: {
        "id": f"pytest-foreign-{uuid.uuid4().hex}",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }
    try:
        foreign = app_client.get(f"{CERT}/candidates/{created['id']}")
        assert foreign.status_code == 404, foreign.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = original

    other = _seed(app_client, owner)
    cross = app_client.post(
        f"{CERT}/candidates",
        json={
            "project_id": ids["project_id"],
            "label": "cross-project",
            "subject": {"type": "model"},
            "evidence": {"experiment_ids": [other["experiment_id"]]},
            "environment": {"runner_version": "1"},
        },
    )
    assert cross.status_code == 400, cross.text


# ================================================================== 10
def test_policy_endpoint_returns_versioned_criteria(app_client, as_user):
    r = app_client.get(f"{CERT}/policy")
    assert r.status_code == 200, r.text
    policy = r.json()
    assert policy["id"] == "v5-readiness-policy"
    assert policy["version"] == "v5.10.0"
    assert [c["key"] for c in policy["criteria"]] == list(
        certification_service.READINESS_CRITERIA
    )
    assert all(c.get("required") is True for c in policy["criteria"])
