"""V5.9 Experiment + Finding integration: finding promotion.

Covers regression and red-team promotion into NEW frozen dataset versions,
monotonic versioning, historical immutability of runs/evaluations, provenance,
finding lifecycle status validation, project isolation and evidence linking.

All records belong to throwaway ``pytest-`` owners purged by ``as_user``.
"""
from __future__ import annotations

import copy
import uuid

from app import repository as repo
from app.deps import get_current_user
from app.models import now_iso

from _support import new_owner_id, purge_owner

API = "/api"


# ------------------------------------------------------------------ helpers
def _project(client, name):
    r = client.post(f"{API}/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_dataset(client, project_id, input_text="What is the capital of France?", expected="Paris"):
    r = client.post(
        f"{API}/datasets",
        json={"project_id": project_id, "name": f"pytest-ds-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code == 200, r.text
    dataset_id = r.json()["id"]
    versions = client.get(f"{API}/datasets/{dataset_id}/versions").json()
    version_id = versions[0]["id"]
    r = client.post(
        f"{API}/datasets/{dataset_id}/versions/{version_id}/cases",
        json={"input_text": input_text, "expected_output": expected},
    )
    assert r.status_code == 200, r.text
    case = r.json()
    r = client.post(f"{API}/datasets/{dataset_id}/versions/{version_id}/freeze")
    assert r.status_code == 200, r.text
    return dataset_id, version_id, case


def _seed_chain(client, owner, project_id, *, taxonomy_code, category,
                title="The model must not call tools"):
    dataset_id, version_id, case = _make_dataset(client, project_id)

    async def _insert():
        now = now_iso()
        run = {
            "id": f"pytest-run-{uuid.uuid4().hex}",
            "owner_id": owner,
            "project_id": project_id,
            "experiment_id": None,
            "execution_kind": "benchmark",
            "dataset_case_id": case["id"],
            "dataset_version_id": version_id,
            "status": "success",
            "model_name": "pytest-model",
            "normalized_response": "wrong",
            "trace_id": None,
            "created_at": now,
        }
        await repo.runs.insert(run)
        evaluation = {
            "id": f"pytest-eval-{uuid.uuid4().hex}",
            "owner_id": owner,
            "project_id": project_id,
            "run_id": run["id"],
            "experiment_id": None,
            "dataset_case_id": case["id"],
            "evaluator_type": "rule",
            "eval_type": "rule",
            "status": "completed",
            "result": {"outcome": "FAIL"},
            "findings": [{"category": "instruction_following", "taxonomy_code": taxonomy_code}],
            "created_at": now,
        }
        await repo.evaluations.insert(evaluation)
        failure = {
            "id": f"pytest-failure-{uuid.uuid4().hex}",
            "owner_id": owner,
            "project_id": project_id,
            "run_id": run["id"],
            "evaluation_id": evaluation["id"],
            "dataset_case_id": case["id"],
            "source": "RULE",
            "category": category,
            "failure_class": "quality",
            "failure_domain": "QUALITY",
            "taxonomy_code": taxonomy_code,
            "title": title,
            "description": "regression-worthy failure",
            "evidence": {},
            "status": "OPEN",
            "created_at": now,
            "updated_at": now,
        }
        await repo.failures.insert(failure)
        return run, evaluation, failure

    run, evaluation, failure = client.portal.call(_insert)
    return {
        "dataset_id": dataset_id,
        "version_id": version_id,
        "case": case,
        "run": run,
        "evaluation": evaluation,
        "failure": failure,
    }


def _create_finding(client, project_id, chain, title="Promoted finding", statement="The model must not call tools."):
    r = client.post(f"{API}/findings", json={
        "project_id": project_id,
        "title": title,
        "statement": statement,
        "evidence": [
            {"type": "run", "ref_id": chain["run"]["id"]},
            {"type": "evaluation", "ref_id": chain["evaluation"]["id"]},
            {"type": "failure", "ref_id": chain["failure"]["id"]},
        ],
    })
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================== 1
def test_promote_to_regression_case(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client, "pytest-v59-regression")
    chain = _seed_chain(
        app_client, owner, project_id,
        taxonomy_code="QUALITY.INSTRUCTION_FOLLOWING", category="INSTRUCTION_VIOLATION",
    )
    finding = _create_finding(app_client, project_id, chain)

    r = app_client.post(f"{API}/findings/{finding['id']}/promote/regression", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["promotion_type"] == "regression"
    assert body["source_finding_id"] == finding["id"]
    assert body["version"]["status"] == "frozen"
    assert body["version"]["content_hash"]
    assert body["dataset"]["name"].startswith("Regression:")

    case = body["case"]
    assert case["metadata"]["source_finding_id"] == finding["id"]
    assert case["metadata"]["source_run_id"] == chain["run"]["id"]
    assert case["metadata"]["source_failure_id"] == chain["failure"]["id"]
    assert case["metadata"]["promotion_type"] == "regression"
    assert {"type": "no_tools", "category": "instruction_following"} in case["assertions"]
    assert case["tool_policy"] == {"allow_tools": False}
    assert case["input_text"] == chain["case"]["input_text"]
    assert case["red_team"] is None

    async def _stored():
        return await repo.dataset_version_cases.get(case["id"], owner)

    stored = app_client.portal.call(_stored)
    assert stored is not None
    assert stored["dataset_version_id"] == body["version"]["id"]


# ================================================================== 2
def test_promotion_versioning_is_monotonic_and_immutable(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client, "pytest-v59-versioning")
    chain = _seed_chain(
        app_client, owner, project_id,
        taxonomy_code="QUALITY.TOOL_SELECTION", category="TOOL_SELECTION_ERROR",
    )
    finding = _create_finding(app_client, project_id, chain)

    r = app_client.post(f"{API}/datasets", json={
        "project_id": project_id, "name": f"target-{uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    target_id = r.json()["id"]
    assert app_client.get(f"{API}/datasets/{target_id}/versions").status_code == 200

    first = app_client.post(
        f"{API}/findings/{finding['id']}/promote/regression",
        json={"dataset_id": target_id},
    )
    assert first.status_code == 200, first.text
    first = first.json()
    second = app_client.post(
        f"{API}/findings/{finding['id']}/promote/regression",
        json={"dataset_id": target_id, "source_version_id": first["version"]["id"]},
    )
    assert second.status_code == 200, second.text
    second = second.json()

    assert first["dataset"]["id"] == second["dataset"]["id"] == target_id
    assert first["version"]["version"] == 2
    assert second["version"]["version"] == 3
    assert first["case"]["id"] != second["case"]["id"]

    async def _first():
        return await repo.dataset_versions.get(first["version"]["id"], owner)

    frozen_first = app_client.portal.call(_first)
    assert frozen_first["status"] == "frozen"
    assert frozen_first["content_hash"] == first["version"]["content_hash"]


# ================================================================== 3
def test_promote_to_red_team_case(app_client, as_user):
    owner = as_user["id"]

    forbidden_project = _project(app_client, "pytest-v59-rt-forbidden")
    forbidden_chain = _seed_chain(
        app_client, owner, forbidden_project,
        taxonomy_code="QUALITY.TOOL_SELECTION", category="TOOL_SELECTION_ERROR",
    )
    forbidden_finding = _create_finding(app_client, forbidden_project, forbidden_chain)

    r = app_client.post(
        f"{API}/findings/{forbidden_finding['id']}/promote/red-team", json={}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["promotion_type"] == "red_team"
    assert body["version"]["status"] == "frozen"
    assert body["red_team"]["category"] == "forbidden_tool_request"
    assert body["red_team"]["expected_behavior"] == "no_tool_calls"
    assert {"type": "no_tools"} in body["red_team"]["deterministic_assertions"]
    assert body["case"]["red_team"]["category"] == "forbidden_tool_request"
    assert "from-finding" in body["case"]["tags"]
    assert "forbidden_tool_request" in body["case"]["tags"]
    assert body["case"]["red_team"]["expected_boundary"]

    citation_project = _project(app_client, "pytest-v59-rt-citation")
    citation_chain = _seed_chain(
        app_client, owner, citation_project,
        taxonomy_code="QUALITY.CITATION", category="HALLUCINATION",
    )
    citation_finding = _create_finding(app_client, citation_project, citation_chain)
    r = app_client.post(
        f"{API}/findings/{citation_finding['id']}/promote/red-team", json={}
    )
    assert r.status_code == 200, r.text
    citation = r.json()
    assert citation["red_team"]["category"] == "citation_fabrication"
    assert citation["red_team"]["expected_behavior"] == "cite_only_provided"
    assert {"type": "knowledge_marker_valid"} in citation["red_team"]["deterministic_assertions"]


# ================================================================== 4
def test_promotion_does_not_mutate_history(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client, "pytest-v59-immutable")
    chain = _seed_chain(
        app_client, owner, project_id,
        taxonomy_code="QUALITY.INSTRUCTION_FOLLOWING", category="INSTRUCTION_VIOLATION",
    )
    finding = _create_finding(app_client, project_id, chain)

    async def _snapshot():
        run = await repo.runs.get(chain["run"]["id"], owner)
        evaluation = await repo.evaluations.get(chain["evaluation"]["id"], owner)
        return copy.deepcopy(run), copy.deepcopy(evaluation)

    run_before, evaluation_before = app_client.portal.call(_snapshot)

    r = app_client.post(f"{API}/findings/{finding['id']}/promote/regression", json={})
    assert r.status_code == 200, r.text

    run_after, evaluation_after = app_client.portal.call(_snapshot)
    assert run_before == run_after
    assert evaluation_before == evaluation_after


# ================================================================== 5
def test_promotion_provenance_is_recorded(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client, "pytest-v59-provenance")
    chain = _seed_chain(
        app_client, owner, project_id,
        taxonomy_code="QUALITY.INSTRUCTION_FOLLOWING", category="INSTRUCTION_VIOLATION",
    )
    finding = _create_finding(app_client, project_id, chain)

    promoted = app_client.post(
        f"{API}/findings/{finding['id']}/promote/regression", json={}
    ).json()

    r = app_client.get(f"{API}/findings/{finding['id']}")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert len(detail["promotions"]) == 1
    promotion = detail["promotions"][0]
    assert promotion["type"] == "regression"
    assert promotion["dataset_version_id"] == promoted["version"]["id"]
    assert promotion["case_id"] == promoted["case"]["id"]
    assert promotion["dataset_id"] == promoted["dataset"]["id"]

    types = {(e["type"], e["ref_id"]) for e in detail["evidence"]}
    assert ("run", chain["run"]["id"]) in types
    assert ("evaluation", chain["evaluation"]["id"]) in types
    assert ("failure", chain["failure"]["id"]) in types
    assert ("dataset_version", promoted["version"]["id"]) in types

    resolved = {(e["type"], e["ref_id"]): e for e in detail["resolved_evidence"]}
    assert resolved[("run", chain["run"]["id"])]["exists"] is True
    assert resolved[("evaluation", chain["evaluation"]["id"])]["exists"] is True
    assert resolved[("failure", chain["failure"]["id"])]["exists"] is True
    assert resolved[("dataset_version", promoted["version"]["id"])]["exists"] is True


# ================================================================== 6
def test_finding_status_workflow(app_client, as_user):
    project_id = _project(app_client, "pytest-v59-status")
    r = app_client.post(f"{API}/findings", json={
        "project_id": project_id, "title": "Status lifecycle",
    })
    assert r.status_code == 200, r.text
    finding_id = r.json()["id"]

    for status in ("resolved", "reproduced", "accepted-risk", "needs-investigation"):
        r = app_client.patch(f"{API}/findings/{finding_id}", json={"status": status})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status

    r = app_client.patch(f"{API}/findings/{finding_id}", json={"status": "not-a-status"})
    assert r.status_code == 400, r.text
    assert "status" in r.json()["detail"].lower()


# ================================================================== 7
def test_project_and_owner_isolation(app_client, as_user):
    owner_a = as_user["id"]
    project_id = _project(app_client, "pytest-v59-isolation-a")
    other_project = _project(app_client, "pytest-v59-isolation-b")
    chain = _seed_chain(
        app_client, owner_a, project_id,
        taxonomy_code="QUALITY.INSTRUCTION_FOLLOWING", category="INSTRUCTION_VIOLATION",
    )
    finding = _create_finding(app_client, project_id, chain)

    foreign_experiment_id = f"pytest-exp-{uuid.uuid4().hex}"

    async def _seed_foreign_experiment():
        await repo.experiments.insert({
            "id": foreign_experiment_id,
            "owner_id": owner_a,
            "project_id": other_project,
            "title": "other project experiment",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })

    app_client.portal.call(_seed_foreign_experiment)

    r = app_client.post(f"{API}/findings/{finding['id']}/link", json={
        "type": "experiment", "ref_id": foreign_experiment_id,
    })
    assert r.status_code == 400, r.text

    other_dataset = _project(app_client, "pytest-v59-isolation-c")
    r = app_client.post(f"{API}/datasets", json={"project_id": other_dataset, "name": "other-ds"})
    other_dataset_id = r.json()["id"]
    r = app_client.post(
        f"{API}/findings/{finding['id']}/promote/regression",
        json={"dataset_id": other_dataset_id},
    )
    assert r.status_code == 400, r.text
    assert "different project" in r.json()["detail"].lower()

    owner_b = new_owner_id()
    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: {
        "id": owner_b,
        "email": f"{owner_b}@example.com",
        "name": "Foreign",
        "role": "researcher",
    }
    try:
        r = app_client.post(
            f"{API}/findings/{finding['id']}/promote/regression", json={}
        )
        assert r.status_code == 404, r.text
        r = app_client.post(f"{API}/findings/{finding['id']}/link", json={
            "type": "run", "ref_id": chain["run"]["id"],
        })
        assert r.status_code == 404, r.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = original
        app_client.portal.call(purge_owner, owner_b)


# ================================================================== 8
def test_link_evidence_and_dedupe(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client, "pytest-v59-link")
    experiment_id = f"pytest-exp-{uuid.uuid4().hex}"

    async def _seed():
        await repo.experiments.insert({
            "id": experiment_id,
            "owner_id": owner,
            "project_id": project_id,
            "title": "linked experiment",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })

    app_client.portal.call(_seed)
    finding = app_client.post(f"{API}/findings", json={
        "project_id": project_id, "title": "Linkable finding",
    }).json()

    for _ in range(2):
        r = app_client.post(f"{API}/findings/{finding['id']}/link", json={
            "type": "experiment", "ref_id": experiment_id, "label": "exp",
        })
        assert r.status_code == 200, r.text

    detail = app_client.get(f"{API}/findings/{finding['id']}").json()
    matches = [e for e in detail["evidence"]
               if e["type"] == "experiment" and e["ref_id"] == experiment_id]
    assert len(matches) == 1


# ================================================================== 9
def test_link_unknown_type_is_rejected(app_client, as_user):
    project_id = _project(app_client, "pytest-v59-link-bad")
    finding = app_client.post(f"{API}/findings", json={
        "project_id": project_id, "title": "Bad link",
    }).json()
    r = app_client.post(f"{API}/findings/{finding['id']}/link", json={
        "type": "not-a-real-type", "ref_id": "whatever",
    })
    assert r.status_code == 400, r.text
