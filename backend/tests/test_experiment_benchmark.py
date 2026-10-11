"""Phase 2 protocol, dataset versioning, benchmark planning/runner, evaluation,
aggregation, regression comparison and pricing-snapshot integrity.

Everything runs offline against throwaway owners; the canonical execution funnel
is driven by fake adapters and no real network call is made.
"""
from __future__ import annotations

import uuid

import pytest

import app.config as config
from app import repository as repo
from app import services
from app.models import now_iso
from app.services import benchmark as benchmark_service
from app.services import datasets as dataset_service
from app.services import experiments as experiment_service
from app.services import execution as execution_module

from _support import (
    adapter_factory,
    create_model,
    create_provider,
    result_error,
    result_success,
)


def _install(monkeypatch, executor, streamer=None):
    factory = adapter_factory(executor, streamer)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)


def _make_project(client, name):
    r = client.post("/api/projects", json={"name": name})
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
    r = client.post("/api/datasets", json={"project_id": project_id, "name": f"pytest-ds-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 200, r.text
    dataset_id = r.json()["id"]
    versions = client.get(f"/api/datasets/{dataset_id}/versions")
    assert versions.status_code == 200, versions.text
    version = versions.json()[0]
    for index, case in enumerate(cases):
        r = client.post(
            f"/api/datasets/{dataset_id}/versions/{version['id']}/cases",
            json={
                "input_text": case["input"],
                "expected_output": case.get("expected", ""),
                "ordering": case.get("ordering", index),
            },
        )
        assert r.status_code == 200, r.text
    r = client.post(f"/api/datasets/{dataset_id}/versions/{version['id']}/freeze")
    assert r.status_code == 200, r.text
    return dataset_id, version["id"], r.json()


def _make_experiment(client, owner, project_id, model_ids, dataset_id, version_id,
                     repetitions=1, max_tokens=100, ready=True):
    configs = [
        {"label": f"cfg-{index}", "model_id": model_id, "max_tokens": max_tokens, "order": index}
        for index, model_id in enumerate(model_ids)
    ]
    body = {
        "project_id": project_id,
        "title": f"pytest-experiment-{uuid.uuid4().hex[:6]}",
        "experiment_type": "benchmark",
        "protocol": {
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "repetitions": repetitions,
        },
        "configurations": configs,
    }
    r = client.post("/api/experiments", json=body)
    assert r.status_code == 200, r.text
    exp = r.json()
    if ready:
        r = client.post(f"/api/experiments/{exp['id']}/ready")
        assert r.status_code == 200, r.text
        exp = r.json()
    return exp


def _insert_run(owner, experiment, configuration_id, dataset_case_id, **overrides):
    run = {
        "id": f"pytest-run-{uuid.uuid4().hex}",
        "owner_id": owner,
        "project_id": experiment.get("project_id"),
        "experiment_id": experiment["id"],
        "execution_kind": "benchmark",
        "configuration_id": configuration_id,
        "dataset_case_id": dataset_case_id,
        "repetition_index": 0,
        "attempt_index": 0,
        "model_id": experiment.get("model_ids", [None])[0],
        "status": "success",
        "normalized_response": "",
        "raw_response": "",
        "input_tokens": None,
        "output_tokens": None,
        "cached_tokens": None,
        "reasoning_tokens": None,
        "latency_ms": 1,
        "estimated_cost": None,
        "cost_available": False,
        "created_at": now_iso(),
    }
    run.update(overrides)
    return run


# ================================================================== protocol CRUD
def test_protocol_crud_hashes_and_transitions(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-protocol")
    models = _make_models(app_client, owner, 2)
    dataset_id, version_id, frozen = _make_dataset(app_client, project_id, [
        {"input": "q1", "expected": "a1"},
        {"input": "q2", "expected": "a2"},
    ])

    exp = _make_experiment(
        app_client, owner, project_id, models, dataset_id, version_id, ready=False
    )
    assert exp["status"] == "DRAFT"
    assert len(exp["configurations"]) == 2
    assert exp["configuration_hash"] is None
    assert exp["protocol_hash"] is None
    assert exp["dataset_version_hash"] is None

    # Editing while DRAFT is allowed.
    r = app_client.patch(f"/api/experiments/{exp['id']}", json={"title": "pytest-renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "pytest-renamed"

    # Invalid DRAFT -> COMPLETED transition.
    r = app_client.patch(f"/api/experiments/{exp['id']}", json={"status": "COMPLETED"})
    assert r.status_code == 400, r.text

    # READY freezes and computes every hash.
    r = app_client.post(f"/api/experiments/{exp['id']}/ready")
    assert r.status_code == 200, r.text
    ready = r.json()
    assert ready["status"] == "READY"
    assert ready["configuration_hash"]
    assert ready["protocol_hash"]
    assert ready["dataset_version_hash"] == frozen["content_hash"]
    assert ready["hashes"]["protocol_hash"] == ready["protocol_hash"]

    # Configurations are immutable after READY.
    r = app_client.patch(f"/api/experiments/{exp['id']}", json={
        "configurations": [{"label": "cfg-0", "model_id": models[0]}],
    })
    assert r.status_code == 400, r.text

    # Invalid READY -> COMPLETED transition.
    r = app_client.patch(f"/api/experiments/{exp['id']}", json={"status": "COMPLETED"})
    assert r.status_code == 400, r.text


# ================================================================== dataset freezing
def test_dataset_frozen_semantics(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-datasets")

    r = app_client.post("/api/datasets", json={"project_id": project_id, "name": "pytest-freeze"})
    assert r.status_code == 200, r.text
    dataset_id = r.json()["id"]

    versions = app_client.get(f"/api/datasets/{dataset_id}/versions").json()
    assert len(versions) == 1
    version_id = versions[0]["id"]
    assert versions[0]["version"] == 1
    assert versions[0]["status"] == "draft"
    assert versions[0]["case_count"] == 0

    case_ids = []
    for order, text in enumerate(("first", "second")):
        r = app_client.post(
            f"/api/datasets/{dataset_id}/versions/{version_id}/cases",
            json={"input_text": text, "expected_output": text.upper(), "ordering": order},
        )
        assert r.status_code == 200, r.text
        case_ids.append(r.json()["id"])

    detail = app_client.get(f"/api/datasets/{dataset_id}/versions/{version_id}").json()
    assert detail["case_count"] == 2

    r = app_client.post(f"/api/datasets/{dataset_id}/versions/{version_id}/freeze")
    assert r.status_code == 200, r.text
    frozen = r.json()
    assert frozen["status"] == "frozen"
    assert frozen["content_hash"]
    assert frozen["case_count"] == 2
    assert frozen["frozen_at"]

    # Every mutation of a frozen version is rejected.
    r = app_client.post(
        f"/api/datasets/{dataset_id}/versions/{version_id}/cases",
        json={"input_text": "third"},
    )
    assert r.status_code == 400, r.text
    r = app_client.patch(
        f"/api/datasets/version-cases/{case_ids[0]}", json={"input_text": "changed"}
    )
    assert r.status_code == 400, r.text
    r = app_client.delete(f"/api/datasets/version-cases/{case_ids[0]}")
    assert r.status_code == 400, r.text

    # New version copies frozen cases.
    r = app_client.post(
        f"/api/datasets/{dataset_id}/versions", json={"source_version_id": version_id}
    )
    assert r.status_code == 200, r.text
    new_version = r.json()
    assert new_version["version"] == 2
    assert new_version["status"] == "draft"
    assert new_version["source_version_id"] == version_id
    copied = app_client.get(
        f"/api/datasets/{dataset_id}/versions/{new_version['id']}"
    ).json()
    assert copied["case_count"] == 2
    assert [c["input_text"] for c in copied["cases"]] == ["first", "second"]

    # The hash is deterministic for identical content.
    async def _hash():
        cases = await repo.dataset_version_cases.list(
            {"owner_id": owner, "dataset_version_id": version_id},
            sort_field="ordering", descending=False, limit=100,
        )
        return experiment_service.canonical_hash(
            [dataset_service._version_payload(c) for c in cases]
        )

    assert app_client.portal.call(_hash) == frozen["content_hash"]


# ================================================================== benchmark plan
def test_benchmark_plan_deterministic_and_priced(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-plan")
    models = _make_models(app_client, owner, 2, pricing=True)
    dataset_id, version_id, _ = _make_dataset(app_client, project_id, [
        {"input": "one", "expected": "1"},
        {"input": "two", "expected": "2"},
        {"input": "three", "expected": "3"},
    ])
    exp = _make_experiment(
        app_client, owner, project_id, models, dataset_id, version_id,
        repetitions=2, max_tokens=100,
    )

    r = app_client.post(f"/api/experiments/{exp['id']}/benchmark/plan")
    assert r.status_code == 200, r.text
    plan_one = r.json()
    assert plan_one["total_planned_calls"] == 2 * 3 * 2
    assert plan_one["requires_confirmation"] is False
    assert plan_one["estimated_cost"]["available"] is True
    assert plan_one["estimated_cost"]["method"] == "heuristic_chars_div_4"
    assert plan_one["estimated_cost"]["min"] is not None
    assert plan_one["estimated_cost"]["max"] >= plan_one["estimated_cost"]["min"]

    r = app_client.post(f"/api/experiments/{exp['id']}/benchmark/plan")
    assert r.status_code == 200
    plan_two = r.json()
    assert plan_two["plan_hash"] == plan_one["plan_hash"]

    monkeypatch.setattr(config, "BENCHMARK_CONFIRM_THRESHOLD", 5)
    r = app_client.post(f"/api/experiments/{exp['id']}/benchmark/plan")
    assert r.status_code == 200
    plan_three = r.json()
    assert plan_three["requires_confirmation"] is True

    # Unknown pricing keeps the estimate unavailable rather than fabricating it.
    unknown_model = _make_models(app_client, owner, 1, pricing=False)
    exp_unknown = _make_experiment(
        app_client, owner, project_id, unknown_model, dataset_id, version_id,
        repetitions=1, max_tokens=100,
    )
    r = app_client.post(f"/api/experiments/{exp_unknown['id']}/benchmark/plan")
    assert r.status_code == 200, r.text
    plan_unknown = r.json()
    assert plan_unknown["estimated_cost"]["available"] is False
    assert plan_unknown["estimated_cost"]["min"] is None
    assert plan_unknown["estimated_cost"]["max"] is None
    assert plan_unknown["estimated_cost"]["unknown_pricing_config_count"] == 1


# ================================================================== runner
def test_benchmark_runner_idempotent_resume_and_partial_failure(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-runner")
    models = _make_models(app_client, owner, 2, pricing=True)
    dataset_id, version_id, _ = _make_dataset(app_client, project_id, [
        {"input": "hello", "expected": "hi"},
    ])
    exp = _make_experiment(
        app_client, owner, project_id, models, dataset_id, version_id,
        repetitions=1, max_tokens=50,
    )

    async def _idents():
        docs = [await repo.models.get(mid, owner) for mid in models]
        return [doc["model_identifier"] for doc in docs]

    good_ident, bad_ident = app_client.portal.call(_idents)

    def executor(model, req):
        if model.get("model_identifier") == bad_ident:
            return result_error(category="PROVIDER", message="Provider request failed", retryable=True)
        return result_success(input_tokens=10, output_tokens=5)

    _install(monkeypatch, executor)
    monkeypatch.setattr(config, "BENCHMARK_RETRY_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(config, "BENCHMARK_MAX_RETRIES", 1)

    r = app_client.post(f"/api/experiments/{exp['id']}/benchmark/plan")
    plan_id = r.json()["id"]

    async def _run():
        await benchmark_service.run_benchmark(plan_id, owner)

    app_client.portal.call(_run)

    async def _runs():
        return await repo.runs.list(
            {"owner_id": owner, "experiment_id": exp["id"], "execution_kind": "benchmark"},
            sort_field="created_at", descending=False, limit=100,
        )

    async def _plan():
        return await repo.benchmark_plans.get(plan_id, owner)

    runs = app_client.portal.call(_runs)
    plan = app_client.portal.call(_plan)
    assert plan["status"] == "completed"
    assert len(runs) == 3  # 1 good + 2 retry attempts for the failing config
    assert plan["progress"]["completed"] == 1
    assert plan["progress"]["failed"] == 1
    assert plan["progress"]["pending"] == 0

    good_runs = [run for run in runs if run["model_id"] == models[0]]
    bad_runs = [run for run in runs if run["model_id"] == models[1]]
    assert len(good_runs) == 1 and good_runs[0]["status"] == "success"
    assert len(bad_runs) == 2 and all(run["status"] == "error" for run in bad_runs)
    assert sorted(run["attempt_index"] for run in bad_runs) == [0, 1]
    identities = {(run["dataset_case_id"], run["configuration_id"], run["repetition_index"]) for run in bad_runs}
    assert len(identities) == 1

    # Terminal plan: a second invocation adds nothing.
    app_client.portal.call(_run)
    assert len(app_client.portal.call(_runs)) == 3

    # Resume from a non-terminal status still skips completed items.
    async def _reopen():
        await repo.benchmark_plans.update(plan_id, owner, {"status": "planned"})

    app_client.portal.call(_reopen)
    app_client.portal.call(_run)
    assert len(app_client.portal.call(_runs)) == 3
    resumed = app_client.portal.call(_plan)
    assert resumed["progress"]["pending"] == 0


# ================================================================== case evaluation
def test_case_evaluation_deterministic_human_and_judge(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-eval")
    models = _make_models(app_client, owner, 1, pricing=True)
    judge_model = _make_models(app_client, owner, 1, pricing=True)[0]
    dataset_id, version_id, _ = _make_dataset(app_client, project_id, [
        {"input": "What is 2+2?", "expected": "4"},
        {"input": "Name a capital", "expected": "capital"},
        {"input": "animal", "expected": "the cat sat"},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]

    async def _cases():
        return await repo.dataset_version_cases.list(
            {"owner_id": owner, "dataset_version_id": version_id},
            sort_field="ordering", descending=False, limit=100,
        )

    async def _judge_ident():
        doc = await repo.models.get(judge_model, owner)
        return doc["model_identifier"]

    cases = app_client.portal.call(_cases)
    judge_ident = app_client.portal.call(_judge_ident)

    def executor(model, req):
        if model.get("model_identifier") == judge_ident:
            return result_success('```json\n{"score": 0.8, "rationale": "looks right"}\n```', input_tokens=11, output_tokens=7)
        return result_success()

    _install(monkeypatch, executor)

    run_docs = {
        "exact": _insert_run(owner, exp, configuration_id, cases[0]["id"], normalized_response="4"),
        "contains": _insert_run(owner, exp, configuration_id, cases[1]["id"],
                                normalized_response="The capital of France is Paris"),
        "overlap": _insert_run(owner, exp, configuration_id, cases[2]["id"],
                               normalized_response="the cat sat on the mat"),
        "failed": _insert_run(owner, exp, configuration_id, cases[0]["id"],
                              attempt_index=1, status="error", normalized_response=None),
    }

    async def _insert_all():
        for doc in run_docs.values():
            await repo.runs.insert(doc)

    app_client.portal.call(_insert_all)

    def _evaluate(run_id, **body):
        payload = {"run_id": run_id, "evaluator_type": "deterministic", "method": "exact_match"}
        payload.update(body)
        r = app_client.post(f"/api/experiments/{exp['id']}/evaluate", json=payload)
        assert r.status_code == 200, r.text
        return r.json()

    exact = _evaluate(run_docs["exact"]["id"])
    assert exact["parsed_score"] == 1.0
    assert exact["raw_result"]["passed"] is True

    contains = _evaluate(run_docs["contains"]["id"], method="contains")
    assert contains["parsed_score"] == 1.0

    overlap = _evaluate(run_docs["overlap"]["id"], method="token_overlap")
    assert overlap["parsed_score"] == pytest.approx(0.6)

    failed = _evaluate(run_docs["failed"]["id"], method="exact_match")
    assert failed["raw_result"]["computable"] is False

    human = _evaluate(run_docs["exact"]["id"], evaluator_type="human", score=75)
    assert human["parsed_score"] == pytest.approx(0.75)

    # LLM judge: fake adapter returns parseable JSON; its cost is tracked
    # separately from the candidate run.
    judge = _evaluate(
        run_docs["exact"]["id"], evaluator_type="llm_judge", judge_model_id=judge_model,
    )
    assert judge["parsed_score"] == pytest.approx(0.8)
    assert judge["judge_run_id"]
    assert judge["judge_model_snapshot"]["model_identifier"] == judge_ident
    assert judge["usage"]["cost"] is not None
    assert judge["usage"]["cost_available"] is True

    async def _judge_run():
        return await repo.runs.get(judge["judge_run_id"], owner)

    judge_run = app_client.portal.call(_judge_run)
    assert judge_run["context"] == "judge"
    assert judge_run["parent_run_id"] == run_docs["exact"]["id"]
    assert judge_run["estimated_cost"] == judge["usage"]["cost"]
    assert run_docs["exact"].get("estimated_cost") is None


# ================================================================== aggregation
def test_aggregation_truthful_nulls_and_case_pagination(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-agg")
    models = _make_models(app_client, owner, 1, pricing=True)
    dataset_id, version_id, _ = _make_dataset(app_client, project_id, [
        {"input": "a", "expected": "a"},
        {"input": "b", "expected": "b"},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]

    async def _cases():
        return await repo.dataset_version_cases.list(
            {"owner_id": owner, "dataset_version_id": version_id},
            sort_field="ordering", descending=False, limit=100,
        )

    cases = app_client.portal.call(_cases)

    async def _insert():
        await repo.runs.insert(_insert_run(
            owner, exp, configuration_id, cases[0]["id"],
            input_tokens=100, output_tokens=50, cost_available=True, estimated_cost=0.001,
        ))
        await repo.runs.insert(_insert_run(
            owner, exp, configuration_id, cases[1]["id"],
            input_tokens=None, output_tokens=None, cost_available=False, estimated_cost=None,
        ))

    app_client.portal.call(_insert)

    r = app_client.get(f"/api/experiments/{exp['id']}/results")
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["legacy"] is False
    config_summary = summary["configurations"][0]
    assert config_summary["attempted_runs"] == 2
    assert config_summary["completed"] == 2
    assert config_summary["total_cases"] == 2
    assert config_summary["total_input_tokens"] == 100
    assert config_summary["total_output_tokens"] == 50
    assert config_summary["known_usage_run_count"] == 1
    assert config_summary["unknown_usage_run_count"] == 1
    assert config_summary["known_cost_total"] == pytest.approx(0.001)
    assert config_summary["unknown_cost_run_count"] == 1
    # Unknown evaluation cost makes the combined total unavailable, not zero.
    assert config_summary["total_experiment_cost"] is None

    r = app_client.get(
        f"/api/experiments/{exp['id']}/results",
        params={"include_cases": "true", "limit": 1, "offset": 0},
    )
    page0 = r.json()["cases"]
    assert page0["total"] == 2
    assert len(page0["items"]) == 1
    assert page0["items"][0]["dataset_case_id"] == cases[0]["id"]

    r = app_client.get(
        f"/api/experiments/{exp['id']}/results",
        params={"include_cases": "true", "limit": 1, "offset": 1},
    )
    page1 = r.json()["cases"]
    assert len(page1["items"]) == 1
    assert page1["items"][0]["dataset_case_id"] == cases[1]["id"]


# ================================================================== regression compare
def test_regression_compare_classifications(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-compare")
    models = _make_models(app_client, owner, 2, pricing=True)
    dataset_id, version_id, _ = _make_dataset(app_client, project_id, [
        {"input": "c1", "expected": "1"},
        {"input": "c2", "expected": "2"},
        {"input": "c3", "expected": "3"},
        {"input": "c4", "expected": "4"},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    baseline_id = exp["configurations"][0]["id"]
    candidate_id = exp["configurations"][1]["id"]

    async def _cases():
        return await repo.dataset_version_cases.list(
            {"owner_id": owner, "dataset_version_id": version_id},
            sort_field="ordering", descending=False, limit=100,
        )

    cases = app_client.portal.call(_cases)

    async def _seed():
        for index, case in enumerate(cases):
            await repo.runs.insert(_insert_run(
                owner, exp, baseline_id, case["id"], normalized_response=f"base-{index}",
            ))
        for index, case in enumerate(cases[:3]):
            await repo.runs.insert(_insert_run(
                owner, exp, candidate_id, case["id"], normalized_response=f"cand-{index}",
            ))
        baseline_scores = [0.5, 0.6, 0.7, 0.4]
        candidate_scores = [0.7, 0.5, 0.7]
        for index, case in enumerate(cases):
            base_run = await repo.runs.find_one({
                "owner_id": owner, "experiment_id": exp["id"],
                "configuration_id": baseline_id, "dataset_case_id": case["id"],
            })
            await repo.evaluations.insert({
                "id": f"pytest-eval-{uuid.uuid4().hex}", "owner_id": owner,
                "experiment_id": exp["id"], "run_id": base_run["id"],
                "configuration_id": baseline_id, "parsed_score": baseline_scores[index],
                "evaluator_type": "human", "created_at": now_iso(),
            })
        for index, case in enumerate(cases[:3]):
            cand_run = await repo.runs.find_one({
                "owner_id": owner, "experiment_id": exp["id"],
                "configuration_id": candidate_id, "dataset_case_id": case["id"],
            })
            await repo.evaluations.insert({
                "id": f"pytest-eval-{uuid.uuid4().hex}", "owner_id": owner,
                "experiment_id": exp["id"], "run_id": cand_run["id"],
                "configuration_id": candidate_id, "parsed_score": candidate_scores[index],
                "evaluator_type": "human", "created_at": now_iso(),
            })

    app_client.portal.call(_seed)

    r = app_client.get(
        f"/api/experiments/{exp['id']}/compare",
        params={"baseline": baseline_id, "candidate": candidate_id},
    )
    assert r.status_code == 200, r.text
    comparison = r.json()
    assert comparison["case_counts"] == {
        "improved": 1, "regressed": 1, "unchanged": 1, "incomparable_error": 1,
    }
    assert len(comparison["changed_cases"]) == 2

    # No statistical-significance / confidence fields may be invented.
    keys = set()

    def _walk(value):
        if isinstance(value, dict):
            keys.update(value.keys())
            for item in value.values():
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(comparison)
    assert "significance" not in keys
    assert "confidence" not in keys
    assert "p_value" not in keys


# ================================================================== snapshot integrity
def test_historical_pricing_snapshot_is_immutable(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-pricing")
    models = _make_models(app_client, owner, 1, pricing=True)
    dataset_id, version_id, _ = _make_dataset(app_client, project_id, [
        {"input": "hello", "expected": "hi"},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id, max_tokens=50)
    model_id = exp["configurations"][0]["model_id"]

    def executor(model, req):
        return result_success(input_tokens=1000, output_tokens=500)

    _install(monkeypatch, executor)

    r = app_client.post(f"/api/experiments/{exp['id']}/benchmark/plan")
    plan_id = r.json()["id"]

    async def _run():
        await benchmark_service.run_benchmark(plan_id, owner)

    app_client.portal.call(_run)

    async def _run_doc():
        return await repo.runs.find_one({
            "owner_id": owner, "experiment_id": exp["id"],
            "execution_kind": "benchmark", "status": "success",
        })

    run = app_client.portal.call(_run_doc)
    assert run is not None
    snapshot = run["pricing_snapshot"]
    cost = run["estimated_cost"]
    assert snapshot["input_price_per_1m"] == 1.0
    assert snapshot["output_price_per_1m"] == 2.0
    assert cost == pytest.approx(0.002)

    r = app_client.patch(f"/api/models/{model_id}", json={
        "input_price_per_1m": 999.0,
        "output_price_per_1m": 999.0,
    })
    assert r.status_code == 200, r.text
    updated_model = r.json()
    assert updated_model["input_price_per_1m"] == 999.0

    async def _reload():
        return await repo.runs.get(run["id"], owner)

    reloaded = app_client.portal.call(_reload)
    assert reloaded["pricing_snapshot"] == snapshot
    assert reloaded["estimated_cost"] == cost

def test_aggregation_known_execution_and_evaluation_cost(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-agg-cost")
    models = _make_models(app_client, owner, 1, pricing=True)
    dataset_id, version_id, _ = _make_dataset(app_client, project_id, [
        {"input": "a", "expected": "a"},
        {"input": "b", "expected": "b"},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]

    async def _cases():
        return await repo.dataset_version_cases.list(
            {"owner_id": owner, "dataset_version_id": version_id},
            sort_field="ordering", descending=False, limit=100,
        )

    cases = app_client.portal.call(_cases)

    async def _seed():
        run = _insert_run(
            owner, exp, configuration_id, cases[0]["id"],
            input_tokens=100, output_tokens=50, cost_available=True, estimated_cost=0.01,
        )
        await repo.runs.insert(run)
        await repo.evaluations.insert({
            "id": f"pytest-eval-{uuid.uuid4().hex}", "owner_id": owner,
            "experiment_id": exp["id"], "project_id": project_id,
            "run_id": run["id"], "configuration_id": configuration_id,
            "evaluator_type": "llm_judge", "parsed_score": 0.9,
            "usage": {"input_tokens": 5, "output_tokens": 2, "cost": 0.005, "cost_available": True},
            "created_at": now_iso(),
        })

    app_client.portal.call(_seed)

    summary = app_client.get(f"/api/experiments/{exp['id']}/results").json()
    config = summary["configurations"][0]
    assert config["execution_cost_total"] == pytest.approx(0.01)
    assert config["evaluation_cost_total"] == pytest.approx(0.005)
    assert config["total_experiment_cost"] == pytest.approx(0.015)
    assert config["avg_score"] == pytest.approx(0.9)
