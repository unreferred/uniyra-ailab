"""V5.7 red team API tests.

Everything is offline: red-team cases are ordinary dataset version cases with a
``red_team`` spec, evaluated through the shared rule-evaluator engine on runs
inserted directly. All records belong to throwaway ``pytest-`` owners purged by
the ``as_user`` fixture. Coverage: fixture materialization, deterministic PASS /
FAIL boundaries, knowledge prompt injection, citation fabrication, cross-project
rejection, credential exclusion and the per-case summary shape/isolation.
"""
from __future__ import annotations

import json
import uuid

from app import repository as repo
from app import services
from app.deps import get_current_user
from app.models import now_iso
from app.services import execution as execution_module
from app.services import red_team_fixtures as fixtures
from app.services import redaction

from _support import adapter_factory, create_model, create_provider, result_success

API = "/api"


def _install(monkeypatch, executor, streamer=None):
    factory = adapter_factory(executor, streamer)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)


def _make_project(client, name):
    r = client.post(f"{API}/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_models(client, owner, count, pricing=True):
    async def _setup():
        provider_id = await create_provider(owner)
        ids = []
        for index in range(count):
            ids.append(await create_model(
                owner,
                provider_id=provider_id,
                display_name=f"pytest-rt-model-{index}-{uuid.uuid4().hex[:6]}",
                model_identifier=f"pytest/{uuid.uuid4().hex}",
                input_price_per_1m=1.0 if pricing else None,
                output_price_per_1m=2.0 if pricing else None,
            ))
        return ids

    return client.portal.call(_setup)


def _materialize(client, project_id, **extra):
    payload = {"project_id": project_id}
    payload.update(extra)
    r = client.post(f"{API}/red-team/fixtures", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _case_by_category(cases, category):
    match = [c for c in cases if (c.get("red_team") or {}).get("category") == category]
    assert match, f"missing fixture category {category!r}"
    return match[0]


def _configuration_id(client, experiment_id):
    r = client.get(f"{API}/experiments/{experiment_id}")
    assert r.status_code == 200, r.text
    configurations = r.json().get("configurations") or []
    assert configurations, "red team experiment has no configurations"
    return configurations[0]["id"]


def _insert_run(owner, experiment, configuration_id, dataset_case_id, **overrides):
    run = {
        "id": f"pytest-rt-run-{uuid.uuid4().hex}",
        "owner_id": owner,
        "project_id": experiment.get("project_id"),
        "experiment_id": experiment["id"],
        "execution_kind": "benchmark",
        "configuration_id": configuration_id,
        "dataset_case_id": dataset_case_id,
        "repetition_index": 0,
        "attempt_index": 0,
        "model_id": (experiment.get("model_ids") or [None])[0],
        "status": "success",
        "normalized_response": "",
        "raw_response": "",
        "tool_call_count": None,
        "tool_names": None,
        "tool_count_source": None,
        "knowledge_provenance": {},
        "trace_id": None,
        "latency_ms": 1,
        "estimated_cost": None,
        "cost_available": False,
        "created_at": now_iso(),
    }
    run.update(overrides)
    return run


def _insert_runs(client, runs):
    async def _insert():
        for run in runs:
            await repo.runs.insert(run)

    client.portal.call(_insert)


def _evaluate(client, experiment_id, **body):
    r = client.post(f"{API}/experiments/{experiment_id}/evaluate", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _summary(client, experiment_id):
    r = client.get(f"{API}/red-team/{experiment_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _summary_case(summary, case_id):
    match = [row for row in summary["cases"] if row["case_id"] == case_id]
    assert match, "summary is missing the case"
    return match[0]


# ================================================================== 1
def test_materialize_fixtures_dataset_version_cases_and_categories(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, f"pytest-rt-fixtures-{uuid.uuid4().hex[:6]}")
    body = _materialize(app_client, project_id)

    dataset, version, cases = body["dataset"], body["version"], body["cases"]
    assert dataset["project_id"] == project_id
    assert dataset["name"] == fixtures.RED_TEAM_FIXTURE_NAME
    assert version["status"] == "frozen"
    assert version["content_hash"]
    assert version["case_count"] == 5
    assert len(cases) == 5
    assert [c["ordering"] for c in cases] == [0, 1, 2, 3, 4]

    for case in cases:
        assert case["metadata"]["red_team_fixture"] is True
        assert case["metadata"].get("untrusted_source")
        spec = case["red_team"]
        assert isinstance(spec, dict) and spec
        assert spec.get("category")
        assert spec.get("expected_behavior")
        assert spec.get("expected_boundary")
        assert spec.get("deterministic_assertions")

    categories = {c["red_team"]["category"] for c in cases}
    assert categories == {
        "knowledge_injection",
        "citation_fabrication",
        "forbidden_tool_request",
        "cross_project_data_attempt",
        "credential_extraction_attempt",
    }
    assert set(body["categories"]) == categories
    assert body["fixture_version"] == fixtures.RED_TEAM_FIXTURES_VERSION
    assert body["experiment"] is None

    # ``include`` filters by category.
    filtered = _materialize(
        app_client, project_id, include=["forbidden_tool_request"]
    )
    assert [c["red_team"]["category"] for c in filtered["cases"]] == [
        "forbidden_tool_request"
    ]

    # Supplying model_ids creates a READY red_team experiment over the version.
    models = _make_models(app_client, owner, 1)
    with_models = _materialize(app_client, project_id, model_ids=models)
    experiment = with_models["experiment"]
    assert experiment is not None
    assert experiment["experiment_type"] == "red_team"
    assert experiment["status"] == "READY"
    assert experiment["dataset_version_id"] == with_models["version"]["id"]


# ================================================================== 2
def test_red_team_deterministic_pass_holds_boundary(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, f"pytest-rt-pass-{uuid.uuid4().hex[:6]}")
    models = _make_models(app_client, owner, 1)
    body = _materialize(app_client, project_id, model_ids=models)
    experiment = body["experiment"]
    configuration_id = _configuration_id(app_client, experiment["id"])
    case = _case_by_category(body["cases"], "forbidden_tool_request")

    run = _insert_run(
        owner, experiment, configuration_id, case["id"],
        normalized_response="I answered from my own knowledge.",
        tool_call_count=0,
        tool_names=[],
        tool_count_source="run",
    )
    _insert_runs(app_client, [run])

    evaluation = _evaluate(
        app_client, experiment["id"], run_id=run["id"],
        evaluator_type="rule", evaluator_keys=["red_team"],
    )
    assert evaluation["result"]["outcome"] == "PASS"
    assert evaluation["result"]["passed"] is True

    summary = _summary(app_client, experiment["id"])
    row = _summary_case(summary, case["id"])
    assert row["category"] == "forbidden_tool_request"
    assert row["boundary_held"] is True
    assert summary["summary"]["boundaries_held"] >= 1


# ================================================================== 3
def test_red_team_deterministic_fail_is_tool_or_instruction_not_runtime(
    app_client, as_user
):
    owner = as_user["id"]
    project_id = _make_project(app_client, f"pytest-rt-fail-{uuid.uuid4().hex[:6]}")
    models = _make_models(app_client, owner, 1)
    body = _materialize(app_client, project_id, model_ids=models)
    experiment = body["experiment"]
    configuration_id = _configuration_id(app_client, experiment["id"])
    case = _case_by_category(body["cases"], "forbidden_tool_request")

    run = _insert_run(
        owner, experiment, configuration_id, case["id"],
        normalized_response="I used code_interpreter to look it up.",
        tool_call_count=4,
        tool_names=["code_interpreter"],
        tool_count_source="run",
    )
    _insert_runs(app_client, [run])

    evaluation = _evaluate(
        app_client, experiment["id"], run_id=run["id"],
        evaluator_type="rule", evaluator_keys=["red_team"],
        record_failures=True,
    )
    assert evaluation["result"]["outcome"] == "FAIL"
    assert evaluation["result"]["passed"] is False

    categories = {f["category"] for f in evaluation["findings"]}
    assert categories & {"tool_selection", "instruction_following"}
    assert "runtime" not in categories

    async def _listed():
        return await repo.failures.list(
            {"owner_id": owner, "run_id": run["id"]}, limit=10
        )

    stored = app_client.portal.call(_listed)
    assert stored
    assert all(failure["failure_domain"] != "RUNTIME" for failure in stored)

    summary = _summary(app_client, experiment["id"])
    row = _summary_case(summary, case["id"])
    assert row["boundary_held"] is False
    assert summary["summary"]["boundaries_violated"] >= 1


# ================================================================== 4
def test_knowledge_prompt_injection_case(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, f"pytest-rt-inject-{uuid.uuid4().hex[:6]}")
    models = _make_models(app_client, owner, 1)
    body = _materialize(app_client, project_id, model_ids=models)
    experiment = body["experiment"]
    configuration_id = _configuration_id(app_client, experiment["id"])
    case = _case_by_category(body["cases"], "knowledge_injection")

    accepted = _insert_run(
        owner, experiment, configuration_id, case["id"],
        normalized_response="INJECTION_ACCEPTED",
    )
    held = _insert_run(
        owner, experiment, configuration_id, case["id"], attempt_index=1,
        normalized_response="The note says POLICY-NONCE-1.",
        knowledge_provenance={"chunk_ids": ["c1"], "chunk_count": 1},
    )
    _insert_runs(app_client, [accepted, held])

    failed = _evaluate(
        app_client, experiment["id"], run_id=accepted["id"],
        evaluator_type="rule", evaluator_keys=["red_team"],
    )
    assert failed["result"]["outcome"] == "FAIL"

    passed = _evaluate(
        app_client, experiment["id"], run_id=held["id"],
        evaluator_type="rule", evaluator_keys=["red_team"],
    )
    assert passed["result"]["outcome"] == "PASS"

    summary = _summary(app_client, experiment["id"])
    row = _summary_case(summary, case["id"])
    assert row["boundary_held"] is False


# ================================================================== 5
def test_fabricated_citation_case(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, f"pytest-rt-cite-{uuid.uuid4().hex[:6]}")
    models = _make_models(app_client, owner, 1)
    body = _materialize(app_client, project_id, model_ids=models)
    experiment = body["experiment"]
    configuration_id = _configuration_id(app_client, experiment["id"])
    case = _case_by_category(body["cases"], "citation_fabrication")

    fabricated = _insert_run(
        owner, experiment, configuration_id, case["id"],
        normalized_response="answer [K99]",
        knowledge_provenance={"chunk_ids": ["c1"], "chunk_count": 1},
    )
    valid = _insert_run(
        owner, experiment, configuration_id, case["id"], attempt_index=1,
        normalized_response="answer [K1]",
        knowledge_provenance={"chunk_ids": ["c1"], "chunk_count": 1},
    )
    _insert_runs(app_client, [fabricated, valid])

    failed = _evaluate(
        app_client, experiment["id"], run_id=fabricated["id"],
        evaluator_type="rule", evaluator_keys=["red_team"],
    )
    assert failed["result"]["outcome"] == "FAIL"
    assert any(f["category"] == "citation" for f in failed["findings"])

    passed = _evaluate(
        app_client, experiment["id"], run_id=valid["id"],
        evaluator_type="rule", evaluator_keys=["red_team"],
    )
    assert passed["result"]["outcome"] == "PASS"


# ================================================================== 6
def test_cross_project_dataset_rejected_and_foreign_summary_404(
    app_client, as_user
):
    owner = as_user["id"]
    project_a = _make_project(app_client, f"pytest-rt-a-{uuid.uuid4().hex[:6]}")
    project_b = _make_project(app_client, f"pytest-rt-b-{uuid.uuid4().hex[:6]}")
    models = _make_models(app_client, owner, 1)
    body = _materialize(app_client, project_a, model_ids=models)

    r = app_client.post(f"{API}/red-team/experiments", json={
        "project_id": project_b,
        "dataset_id": body["dataset"]["id"],
        "dataset_version_id": body["version"]["id"],
        "model_ids": models,
    })
    assert r.status_code == 400, r.text
    assert "different project" in r.json()["detail"].lower()

    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: {
        "id": f"pytest-foreign-{uuid.uuid4().hex}",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }
    try:
        foreign = app_client.get(
            f"{API}/red-team/{body['experiment']['id']}"
        )
        assert foreign.status_code == 404, foreign.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = original


# ================================================================== 7
def test_credential_exclusion_in_evaluation_and_summary(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, f"pytest-rt-cred-{uuid.uuid4().hex[:6]}")
    models = _make_models(app_client, owner, 1)
    body = _materialize(app_client, project_id, model_ids=models)
    experiment = body["experiment"]
    configuration_id = _configuration_id(app_client, experiment["id"])
    case = _case_by_category(body["cases"], "credential_extraction_attempt")

    secret = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX"
    run = _insert_run(
        owner, experiment, configuration_id, case["id"],
        normalized_response=f"Here is the key: {secret}",
        tool_call_count=0,
        tool_names=[],
        tool_count_source="run",
    )
    _insert_runs(app_client, [run])

    evaluation = _evaluate(
        app_client, experiment["id"], run_id=run["id"],
        evaluator_type="rule", evaluator_keys=["red_team"],
        record_failures=True,
    )
    assert evaluation["result"]["outcome"] == "FAIL"

    async def _load_evaluation():
        return await repo.evaluations.get(evaluation["id"], owner)

    stored = app_client.portal.call(_load_evaluation)
    assert stored is not None
    assert secret not in json.dumps(stored, default=str)

    summary = _summary(app_client, experiment["id"])
    assert secret not in json.dumps(summary, default=str)

    # Where secret-shaped material is surfaced, the redaction layer replaces it.
    assert redaction.redact_text(secret) == redaction.REDACTED


# ================================================================== 8
def test_red_team_summary_shape_and_isolation(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, f"pytest-rt-sum-{uuid.uuid4().hex[:6]}")
    models = _make_models(app_client, owner, 1)
    body = _materialize(app_client, project_id, model_ids=models)
    experiment = body["experiment"]

    summary = _summary(app_client, experiment["id"])
    assert summary["experiment"]["id"] == experiment["id"]
    assert summary["experiment"]["project_id"] == project_id
    assert summary["experiment"]["dataset_version_id"] == body["version"]["id"]
    assert summary["summary"]["cases_total"] == 5
    for key in (
        "cases_total",
        "boundaries_held",
        "boundaries_violated",
        "not_evaluated",
        "case_evaluations",
    ):
        assert key in summary["summary"]
    assert summary["summary"]["boundaries_held"] == 0
    assert summary["summary"]["boundaries_violated"] == 0
    assert summary["summary"]["not_evaluated"] == 5
    assert summary["red_team_version"]

    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: {
        "id": f"pytest-foreign-{uuid.uuid4().hex}",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }
    try:
        foreign = app_client.get(f"{API}/red-team/{experiment['id']}")
        assert foreign.status_code == 404, foreign.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = original


# ================================================================== 9
def test_execute_red_team_runs_pipeline_and_traces(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, f"pytest-rt-exec-{uuid.uuid4().hex[:6]}")
    models = _make_models(app_client, owner, 1)
    body = _materialize(app_client, project_id, model_ids=models)
    experiment = body["experiment"]

    def executor(model, req):
        return result_success("I cannot help with that request.")

    _install(monkeypatch, executor)

    r = app_client.post(f"{API}/red-team/experiments/{experiment['id']}/execute")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["experiment_id"] == experiment["id"]
    assert payload["plan_id"]
    assert payload["trace_id"] and payload["span_id"]
    assert payload["summary"]["runs_total"] == 5
    assert payload["summary"]["evaluations"] == 5
    assert set(payload["summary"]["boundaries"]) == {
        "held", "violated", "not_evaluated"
    }

    async def _load():
        spans = await repo.trace_spans.list(
            {"owner_id": owner, "trace_id": payload["trace_id"]},
            sort_field="ordering", descending=False, limit=100,
        )
        trace = await repo.traces.get(payload["trace_id"], owner)
        return spans, trace

    spans, trace = app_client.portal.call(_load)
    assert trace is not None
    assert trace["trace_type"] == "red_team"
    assert any(span["span_type"] == "RED_TEAM_EXECUTION" for span in spans)
