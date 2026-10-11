"""V5.3 Regression Engine API tests.

Everything is offline: benchmark runs are served by scripted adapters and all
records belong to throwaway ``pytest-`` owners purged by the ``as_user`` fixture.
Coverage: deterministic two-config improvement, truthful missing-vs-zero
classification, persisted listing/detail, project/owner isolation, invalid
configuration rejection, run immutability, opt-in auto-evaluation and the
absence of any magical aggregate score.
"""
from __future__ import annotations

import uuid

from app import repository as repo
from app import services
from app.deps import get_current_user
from app.services import benchmark as benchmark_service
from app.services import execution as execution_module
from app.services import regression as regression_service

from _support import (
    adapter_factory,
    create_model,
    create_provider,
    result_success,
)

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
                display_name=f"pytest-model-{index}-{uuid.uuid4().hex[:6]}",
                model_identifier=f"pytest/{uuid.uuid4().hex}",
                input_price_per_1m=1.0 if pricing else None,
                output_price_per_1m=2.0 if pricing else None,
            ))
        return ids

    return client.portal.call(_setup)


def _make_dataset(client, project_id, cases):
    r = client.post(
        f"{API}/datasets",
        json={"project_id": project_id, "name": f"pytest-ds-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code == 200, r.text
    dataset_id = r.json()["id"]
    version = client.get(f"{API}/datasets/{dataset_id}/versions").json()[0]
    for index, case in enumerate(cases):
        payload = {
            "input_text": case["input"],
            "expected_output": case.get("expected", ""),
            "ordering": case.get("ordering", index),
        }
        if "assertions" in case:
            payload["assertions"] = case["assertions"]
        r = client.post(
            f"{API}/datasets/{dataset_id}/versions/{version['id']}/cases", json=payload
        )
        assert r.status_code == 200, r.text
    r = client.post(f"{API}/datasets/{dataset_id}/versions/{version['id']}/freeze")
    assert r.status_code == 200, r.text
    return dataset_id, version["id"]


def _make_experiment(client, owner, project_id, model_ids, dataset_id, version_id,
                     repetitions=1, max_tokens=100, ready=True, protocol_extra=None):
    configs = [
        {"label": f"cfg-{index}", "model_id": model_id, "max_tokens": max_tokens, "order": index}
        for index, model_id in enumerate(model_ids)
    ]
    protocol = {
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "repetitions": repetitions,
    }
    if protocol_extra:
        protocol.update(protocol_extra)
    body = {
        "project_id": project_id,
        "title": f"pytest-experiment-{uuid.uuid4().hex[:6]}",
        "experiment_type": "benchmark",
        "protocol": protocol,
        "configurations": configs,
    }
    r = client.post(f"{API}/experiments", json=body)
    assert r.status_code == 200, r.text
    exp = r.json()
    if ready:
        r = client.post(f"{API}/experiments/{exp['id']}/ready")
        assert r.status_code == 200, r.text
        exp = r.json()
    return exp


def _model_idents(client, owner, model_ids):
    async def _idents():
        out = []
        for model_id in model_ids:
            doc = await repo.models.get(model_id, owner)
            out.append(doc["model_identifier"])
        return out

    return client.portal.call(_idents)


def _run_benchmark(client, owner, experiment_id):
    r = client.post(f"{API}/experiments/{experiment_id}/benchmark/plan")
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]

    async def _run():
        await benchmark_service.run_benchmark(plan_id, owner)

    client.portal.call(_run)
    return plan_id


def _snapshot_runs(client, owner, experiment_id):
    async def _load():
        return await repo.runs.list(
            {"owner_id": owner, "experiment_id": experiment_id},
            sort_field="created_at",
            descending=False,
            limit=1000,
        )

    return client.portal.call(_load)


def _evaluation_count(client, owner, experiment_id):
    async def _count():
        return await repo.evaluations.count(
            {"owner_id": owner, "experiment_id": experiment_id}
        )

    return client.portal.call(_count)


def _post_regression(client, experiment_id, baseline_id, candidate_id, **overrides):
    payload = {
        "baseline_configuration_id": baseline_id,
        "candidate_configuration_id": candidate_id,
        "evaluator_type": "rule",
    }
    payload.update(overrides)
    return client.post(f"{API}/experiments/{experiment_id}/regression", json=payload)


def _benchmarked_experiment(client, owner, monkeypatch, cases=None):
    """Two-config benchmark where cfg-1 answers correctly and cfg-0 does not."""
    project_id = _make_project(client, f"pytest-v53-{uuid.uuid4().hex[:6]}")
    models = _make_models(client, owner, 2)
    if cases is None:
        cases = [
            {
                "input": f"question-{index}",
                "expected": "expected",
                "assertions": [{"type": "output_contains", "value": "expected"}],
            }
            for index in range(3)
        ]
    dataset_id, version_id = _make_dataset(client, project_id, cases)
    exp = _make_experiment(client, owner, project_id, models, dataset_id, version_id)
    baseline_id = exp["configurations"][0]["id"]
    candidate_id = exp["configurations"][1]["id"]
    idents = _model_idents(client, owner, models)

    def executor(model, req):
        if model.get("model_identifier") == idents[1]:
            return result_success("the expected answer")
        return result_success("a wrong answer")

    _install(monkeypatch, executor)
    plan_id = _run_benchmark(client, owner, exp["id"])
    return {
        "owner": owner,
        "project_id": project_id,
        "experiment": exp,
        "models": models,
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "plan_id": plan_id,
    }


def _run_regression(client, scenario, **overrides):
    exp = scenario["experiment"]
    r = _post_regression(
        client, exp["id"], scenario["baseline_id"], scenario["candidate_id"], **overrides
    )
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================== 1
def test_regression_improvement_and_classifications(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    scenario = _benchmarked_experiment(app_client, owner, monkeypatch)
    body = _run_regression(app_client, scenario)

    assert body["status"] == "completed"
    assert body["summary"]["improved"] >= 1
    assert (
        body["candidate"]["metrics"]["pass_count"]
        > body["baseline"]["metrics"]["pass_count"]
    )
    assert body["baseline"]["metrics"]["pass_count"] == 0
    assert body["candidate"]["metrics"]["pass_count"] == 3

    for row in body["metrics"]:
        if row["classification"] == "unknown":
            assert row["baseline"] is None or row["candidate"] is None
        else:
            assert row["baseline"] is not None and row["candidate"] is not None
            assert row["baseline_missing"] is False
            assert row["candidate_missing"] is False


# ================================================================== 2
def test_regression_missing_vs_zero(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    scenario = _benchmarked_experiment(app_client, owner, monkeypatch)
    body = _run_regression(app_client, scenario)
    rows = {row["metric"]: row for row in body["metrics"]}

    # Absent ttft telemetry on both sides is unknown, never zero/unchanged.
    ttft = rows["avg_ttft_ms"]
    assert ttft["baseline"] is None and ttft["candidate"] is None
    assert ttft["classification"] == "unknown"
    assert ttft["classification"] != "unchanged"

    # A real, reported zero is known and classifies as unchanged.
    tools = rows["tool_call_total"]
    assert tools["baseline"] == 0 and tools["candidate"] == 0
    assert tools["baseline"] is not None and tools["candidate"] is not None
    assert tools["classification"] == "unchanged"


# ================================================================== 3
def test_regression_list_and_detail_persist(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    scenario = _benchmarked_experiment(app_client, owner, monkeypatch)
    exp = scenario["experiment"]
    created = _run_regression(app_client, scenario)

    listed = app_client.get(f"{API}/experiments/{exp['id']}/regression")
    assert listed.status_code == 200, listed.text
    listing = listed.json()
    assert listing["total"] == 1
    assert [item["id"] for item in listing["items"]] == [created["id"]]

    detail = app_client.get(
        f"{API}/experiments/{exp['id']}/regression/{created['id']}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == created["id"]
    assert detail.json()["metrics"] == created["metrics"]


# ================================================================== 4
def test_regression_owner_isolation(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    scenario = _benchmarked_experiment(app_client, owner, monkeypatch)
    exp = scenario["experiment"]
    created = _run_regression(app_client, scenario)

    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: {
        "id": f"pytest-foreign-{uuid.uuid4().hex}",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }
    try:
        listed = app_client.get(f"{API}/experiments/{exp['id']}/regression")
        assert listed.status_code == 404, listed.text
        detail = app_client.get(
            f"{API}/experiments/{exp['id']}/regression/{created['id']}"
        )
        assert detail.status_code == 404, detail.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = original


# ================================================================== 5
def test_regression_invalid_configurations_are_rejected(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    scenario = _benchmarked_experiment(app_client, owner, monkeypatch)
    exp = scenario["experiment"]

    r = _post_regression(
        app_client, exp["id"], "pytest-missing-baseline", scenario["candidate_id"]
    )
    assert r.status_code == 400, r.text
    r = _post_regression(
        app_client, exp["id"], scenario["baseline_id"], "pytest-missing-candidate"
    )
    assert r.status_code == 400, r.text


# ================================================================== 6
def test_regression_does_not_mutate_runs(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    scenario = _benchmarked_experiment(app_client, owner, monkeypatch)
    exp = scenario["experiment"]

    before = _snapshot_runs(app_client, owner, exp["id"])
    assert before, "benchmark must have produced runs"
    _run_regression(app_client, scenario)
    after = _snapshot_runs(app_client, owner, exp["id"])
    assert after == before


# ================================================================== 7
def test_auto_evaluation_opt_in(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v53-auto-eval")
    models = _make_models(app_client, owner, 2)
    cases = [
        {
            "input": f"auto-{index}",
            "expected": "expected",
            "assertions": [{"type": "output_contains", "value": "expected"}],
        }
        for index in range(2)
    ]
    dataset_id, version_id = _make_dataset(app_client, project_id, cases)

    def executor(model, req):
        return result_success("the expected answer")

    _install(monkeypatch, executor)

    enabled = _make_experiment(
        app_client, owner, project_id, models, dataset_id, version_id,
        protocol_extra={
            "evaluation": {
                "enabled": True,
                "evaluator_type": "rule",
                "evaluator_keys": ["assertions"],
            }
        },
    )
    _run_benchmark(app_client, owner, enabled["id"])
    enabled_count = _evaluation_count(app_client, owner, enabled["id"])
    assert enabled_count > 0
    assert enabled_count == len(_snapshot_runs(app_client, owner, enabled["id"]))

    disabled = _make_experiment(
        app_client, owner, project_id, models, dataset_id, version_id,
    )
    _run_benchmark(app_client, owner, disabled["id"])
    assert _evaluation_count(app_client, owner, disabled["id"]) == 0


# ================================================================== 8
def test_regression_deterministic_metrics_only(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    scenario = _benchmarked_experiment(app_client, owner, monkeypatch)
    body = _run_regression(app_client, scenario)

    assert "score" not in body
    assert {row["metric"] for row in body["metrics"]} == set(
        regression_service.METRIC_DIRECTIONS
    )
    for row in body["metrics"]:
        assert row["metric"] in regression_service.METRIC_DIRECTIONS


# ================================================================== 9
def test_regression_emits_execution_span(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    scenario = _benchmarked_experiment(app_client, owner, monkeypatch)
    created = _run_regression(app_client, scenario)

    assert created.get("trace_id")
    trace_id = created["trace_id"]

    async def _load():
        spans = await repo.trace_spans.list(
            {
                "owner_id": owner,
                "trace_id": trace_id,
                "span_type": "REGRESSION_EXECUTION",
            },
            sort_field="ordering",
            descending=False,
            limit=100,
        )
        trace = await repo.traces.get(trace_id, owner)
        persisted = await repo.regression_runs.get(created["id"], owner)
        return spans, trace, persisted

    spans, trace, persisted = app_client.portal.call(_load)
    assert len(spans) >= 1
    assert spans[0]["span_type"] == "REGRESSION_EXECUTION"
    assert trace is not None
    assert trace["trace_type"] == "system"
    assert persisted["trace_id"] == trace_id
