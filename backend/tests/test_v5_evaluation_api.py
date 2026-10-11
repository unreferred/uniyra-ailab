"""V5.1 evaluation framework API tests.

Everything is offline: model calls are served by scripted adapters and all
records belong to throwaway ``pytest-`` owners purged by the ``as_user``
fixture. The suite exercises the canonical evaluation record, rule evaluators,
structured judge evaluation, project isolation, provenance resolution, rubric
versioning and finding promotion.
"""
from __future__ import annotations

import uuid

import pytest

from app import providers as providers_module
from app import repository as repo
from app import services
from app.models import now_iso
from app.services import execution as execution_module

from _support import (
    adapter_factory,
    create_model,
    create_provider,
    new_owner_id,
    purge_owner,
    result_success,
)

API = "/api"


def _install(monkeypatch, executor, streamer=None):
    factory = adapter_factory(executor, streamer)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


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
    """Create + freeze a dataset version whose cases may carry V5 expectations."""
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
        if "tool_policy" in case:
            payload["tool_policy"] = case["tool_policy"]
        if "knowledge_policy" in case:
            payload["knowledge_policy"] = case["knowledge_policy"]
        if "reference_answer" in case:
            payload["reference_answer"] = case["reference_answer"]
        r = client.post(
            f"{API}/datasets/{dataset_id}/versions/{version['id']}/cases", json=payload
        )
        assert r.status_code == 200, r.text
    r = client.post(f"{API}/datasets/{dataset_id}/versions/{version['id']}/freeze")
    assert r.status_code == 200, r.text
    return dataset_id, version["id"]


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
    r = client.post(f"{API}/experiments", json=body)
    assert r.status_code == 200, r.text
    exp = r.json()
    if ready:
        r = client.post(f"{API}/experiments/{exp['id']}/ready")
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
        "model_id": (experiment.get("model_ids") or [None])[0],
        "status": "success",
        "normalized_response": "",
        "raw_response": "",
        "input_tokens": None,
        "output_tokens": None,
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


def _cases(client, owner, version_id):
    async def _load():
        return await repo.dataset_version_cases.list(
            {"owner_id": owner, "dataset_version_id": version_id},
            sort_field="ordering",
            descending=False,
            limit=100,
        )

    return client.portal.call(_load)


def _evaluate(client, experiment_id, **body):
    r = client.post(f"{API}/experiments/{experiment_id}/evaluate", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================== 1 + 2
def test_deterministic_pass_and_fail(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-det")
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {"input": "What is 2+2?", "expected": "4"},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]
    case = _cases(app_client, owner, version_id)[0]

    passing = _insert_run(owner, exp, configuration_id, case["id"], normalized_response="4")
    failing = _insert_run(owner, exp, configuration_id, case["id"],
                          attempt_index=1, normalized_response="5")
    _insert_runs(app_client, [passing, failing])

    passed = _evaluate(
        app_client, exp["id"], run_id=passing["id"],
        evaluator_type="deterministic", method="exact_match",
    )
    assert passed["parsed_score"] == 1.0
    assert passed["result"]["outcome"] == "PASS"
    assert passed["result"]["passed"] is True
    assert passed["evaluator_type"] == "deterministic"
    assert passed["eval_type"] == "deterministic"
    assert passed["schema_version"] == 2
    assert passed["evidence"]["run_id"] == passing["id"]

    failed = _evaluate(
        app_client, exp["id"], run_id=failing["id"],
        evaluator_type="deterministic", method="exact_match",
    )
    assert failed["parsed_score"] == 0.0
    assert failed["result"]["outcome"] == "FAIL"
    assert failed["result"]["passed"] is False
    assert failed["findings"], "a FAIL must carry a finding"
    assert failed["findings"][0]["category"] == "correctness"


# ================================================================== 3
def test_malformed_rule_evaluation_is_error_never_pass(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-rule-bad")
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {
            "input": "answer",
            "expected": "answer",
            "assertions": [{"type": "not_a_real_assertion", "value": "x"}],
        },
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]
    case = _cases(app_client, owner, version_id)[0]
    run = _insert_run(owner, exp, configuration_id, case["id"], normalized_response="answer")
    _insert_runs(app_client, [run])

    result = _evaluate(
        app_client, exp["id"], run_id=run["id"],
        evaluator_type="rule", evaluator_keys=["assertions"],
    )
    assert result["status"] == "error"
    assert result["result"]["outcome"] == "ERROR"
    assert result["result"]["passed"] is None
    assert result["parsed_score"] is None


# ================================================================== 4
def test_get_evaluation_detail_resolves_provenance(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-detail")
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {"input": "q", "expected": "a"},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]
    case = _cases(app_client, owner, version_id)[0]
    run = _insert_run(owner, exp, configuration_id, case["id"], normalized_response="a")
    _insert_runs(app_client, [run])

    created = _evaluate(
        app_client, exp["id"], run_id=run["id"],
        evaluator_type="deterministic", method="exact_match",
    )
    r = app_client.get(f"{API}/evaluations/{created['id']}")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["id"] == created["id"]
    assert detail["resolved"]["run"]["exists"] is True
    assert detail["resolved"]["run"]["id"] == run["id"]
    assert detail["resolved"]["experiment"]["exists"] is True
    assert detail["resolved"]["dataset_case"]["exists"] is True

    r = app_client.get(f"{API}/evaluations", params={"experiment_id": exp["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 1
    r = app_client.get(f"{API}/evaluations", params={"run_id": run["id"]})
    assert r.status_code == 200, r.text
    assert all(item["run_id"] == run["id"] for item in r.json()["items"])

    # Another owner's run id must not be listable.
    r = app_client.get(
        f"{API}/evaluations", params={"run_id": f"pytest-missing-{uuid.uuid4().hex}"}
    )
    assert r.status_code == 404, r.text


# ================================================================== 5
def test_project_isolation_between_owners(app_client, as_user):
    owner_a = as_user["id"]
    owner_b = new_owner_id()
    try:
        async def _seed_b():
            run = {
                "id": f"pytest-run-{uuid.uuid4().hex}",
                "owner_id": owner_b,
                "project_id": None,
                "experiment_id": None,
                "status": "success",
                "normalized_response": "secret answer",
                "created_at": now_iso(),
            }
            await repo.runs.insert(run)
            evaluation = {
                "id": f"pytest-eval-{uuid.uuid4().hex}",
                "owner_id": owner_b,
                "run_id": run["id"],
                "project_id": None,
                "experiment_id": None,
                "evaluator_type": "deterministic",
                "findings": [],
                "created_at": now_iso(),
            }
            await repo.evaluations.insert(evaluation)
            return run["id"], evaluation["id"]

        run_b, eval_b = app_client.portal.call(_seed_b)

        r = app_client.get(f"{API}/evaluations/{eval_b}")
        assert r.status_code == 404, r.text

        r = app_client.post(
            f"{API}/runs/{run_b}/evaluate",
            json={"run_id": run_b, "evaluator_type": "deterministic", "method": "exact_match"},
        )
        assert r.status_code in (400, 404), r.text

        r = app_client.get(f"{API}/runs/{run_b}/evaluations")
        assert r.status_code == 404, r.text
        assert owner_a != owner_b
    finally:
        app_client.portal.call(purge_owner, owner_b)


# ================================================================== 6
def test_rule_tool_policy_fail_and_pass(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-tools")
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {"input": "no tools please", "expected": "", "tool_policy": {"allow_tools": False}},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]
    case = _cases(app_client, owner, version_id)[0]

    violating = _insert_run(owner, exp, configuration_id, case["id"],
                            tool_call_count=3, normalized_response="done",
                            tool_failure_count=0, tool_success_count=3)
    clean = _insert_run(owner, exp, configuration_id, case["id"], attempt_index=1,
                        tool_call_count=0, normalized_response="done",
                        tool_failure_count=0, tool_success_count=0)
    _insert_runs(app_client, [violating, clean])

    failed = _evaluate(
        app_client, exp["id"], run_id=violating["id"],
        evaluator_type="rule", evaluator_keys=["tool_policy"],
    )
    assert failed["result"]["outcome"] == "FAIL"
    assert any(f["category"] == "instruction_following" for f in failed["findings"])

    passed = _evaluate(
        app_client, exp["id"], run_id=clean["id"],
        evaluator_type="rule", evaluator_keys=["tool_policy"],
    )
    assert passed["result"]["outcome"] == "PASS"


# ================================================================== 7
def test_rule_knowledge_grounding_valid_and_fabricated(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-kg")
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {
            "input": "summarize",
            "expected": "",
            "knowledge_policy": {"require_valid_markers": True},
        },
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]
    case = _cases(app_client, owner, version_id)[0]

    valid = _insert_run(
        owner, exp, configuration_id, case["id"],
        normalized_response="uses [K1]",
        knowledge_provenance={"chunk_ids": ["c1"], "chunk_count": 1},
    )
    fabricated = _insert_run(
        owner, exp, configuration_id, case["id"], attempt_index=1,
        normalized_response="uses [K99]",
        knowledge_provenance={"chunk_ids": ["c1"], "chunk_count": 1},
    )
    _insert_runs(app_client, [valid, fabricated])

    passed = _evaluate(
        app_client, exp["id"], run_id=valid["id"],
        evaluator_type="rule", evaluator_keys=["knowledge_grounding"],
    )
    assert passed["result"]["outcome"] == "PASS"

    failed = _evaluate(
        app_client, exp["id"], run_id=fabricated["id"],
        evaluator_type="rule", evaluator_keys=["knowledge_grounding"],
    )
    assert failed["result"]["outcome"] == "FAIL"
    assert any(f["category"] == "citation" for f in failed["findings"])


# ================================================================== 8
def test_missing_tool_telemetry_is_not_evaluated(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-null")
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {"input": "q", "expected": "", "tool_policy": {"expected_tool_call_count": 0}},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]
    case = _cases(app_client, owner, version_id)[0]
    run = _insert_run(owner, exp, configuration_id, case["id"], normalized_response="q")
    _insert_runs(app_client, [run])

    result = _evaluate(
        app_client, exp["id"], run_id=run["id"],
        evaluator_type="rule", evaluator_keys=["tool_policy"],
    )
    assert result["result"]["outcome"] == "NOT_EVALUATED"
    assert result["result"]["passed"] is None
    assert result["status"] == "not_evaluated"
    assert result["evidence"]["tool_call_count"] is None


# ================================================================== 9
def test_evaluation_does_not_mutate_source_run(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-immutable")
    models = _make_models(app_client, owner, 1)
    judge_model = _make_models(app_client, owner, 1)[0]

    r = app_client.post(f"{API}/rubrics", json={
        "name": f"pytest-rubric-{uuid.uuid4().hex[:6]}",
        "dimensions": [{
            "key": "correctness",
            "label": "Correctness",
            "description": "Is it correct?",
            "max_score": 10,
            "weight": 1.0,
        }],
    })
    assert r.status_code == 200, r.text
    rubric_id = r.json()["id"]

    async def _judge_ident():
        doc = await repo.models.get(judge_model, owner)
        return doc["model_identifier"]

    judge_ident = app_client.portal.call(_judge_ident)

    def executor(model, req):
        if model.get("model_identifier") == judge_ident:
            return result_success("I think this answer is probably fine overall.")
        return result_success("generic")

    _install(monkeypatch, executor)

    run = {
        "id": f"pytest-run-{uuid.uuid4().hex}",
        "owner_id": owner,
        "project_id": project_id,
        "experiment_id": None,
        "status": "success",
        "normalized_response": "The answer is 42.",
        "created_at": now_iso(),
    }
    app_client.portal.call(repo.runs.insert, run)

    async def _snapshot():
        return await repo.runs.get(run["id"], owner)

    before = app_client.portal.call(_snapshot)

    r = app_client.post(f"{API}/runs/{run['id']}/evaluate", json={
        "run_id": run["id"],
        "evaluator_type": "judge_model",
        "judge_model_id": judge_model,
        "rubric_id": rubric_id,
    })
    assert r.status_code == 200, r.text
    evaluation = r.json()
    assert evaluation["status"] == "error"
    assert evaluation["result"]["outcome"] == "ERROR"
    assert evaluation["result"]["passed"] is None
    assert evaluation["result"]["score"] is None

    after = app_client.portal.call(_snapshot)
    assert before == after, "evaluation must not mutate the source run"


# ================================================================== 10
def test_rubric_versioning_snapshots(app_client, as_user):
    owner = as_user["id"]
    r = app_client.post(f"{API}/rubrics", json={
        "name": f"pytest-versioned-{uuid.uuid4().hex[:6]}",
        "description": "v1",
        "dimensions": [{"key": "correctness", "label": "Correctness", "max_score": 10}],
        "threshold": 0.6,
        "scoring_scale": "0-1",
    })
    assert r.status_code == 200, r.text
    created = r.json()
    rubric_id = created["id"]
    assert created["version"] == 1

    r = app_client.patch(f"{API}/rubrics/{rubric_id}", json={
        "dimensions": [{"key": "correctness", "label": "Correctness", "max_score": 5}],
    })
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["version"] == 2
    assert updated["dimensions"][0]["max_score"] == 5

    versions = app_client.get(f"{API}/rubrics/{rubric_id}/versions")
    assert versions.status_code == 200, versions.text
    snapshots = versions.json()
    assert [s["version"] for s in snapshots] == [1, 2]
    assert snapshots[0]["dimensions"][0]["max_score"] == 10
    assert snapshots[1]["dimensions"][0]["max_score"] == 5
    assert all(s["owner_id"] == owner for s in snapshots)

    # Name-only change must not create a new version.
    r = app_client.patch(f"{API}/rubrics/{rubric_id}", json={"name": "renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert len(app_client.get(f"{API}/rubrics/{rubric_id}/versions").json()) == 2


# ================================================================== 11
def test_promote_finding_creates_failure_record(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-promote")
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {"input": "q", "expected": "expected-answer"},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]
    case = _cases(app_client, owner, version_id)[0]
    run = _insert_run(owner, exp, configuration_id, case["id"], normalized_response="wrong")
    _insert_runs(app_client, [run])

    evaluation = _evaluate(
        app_client, exp["id"], run_id=run["id"],
        evaluator_type="deterministic", method="exact_match",
    )
    assert evaluation["result"]["outcome"] == "FAIL"
    assert evaluation["findings"]

    r = app_client.post(
        f"{API}/evaluations/{evaluation['id']}/findings", json={"finding_index": 0}
    )
    assert r.status_code == 200, r.text
    failure = r.json()
    assert failure["evaluation_id"] == evaluation["id"]
    assert failure["run_id"] == run["id"]
    assert failure["source"] == "RULE"

    async def _listed():
        return await repo.failures.list(
            {"owner_id": owner, "evaluation_id": evaluation["id"]}, limit=10
        )

    stored = app_client.portal.call(_listed)
    assert len(stored) == 1
    assert stored[0]["id"] == failure["id"]

    # Out-of-range index is a 400, not a 500.
    r = app_client.post(
        f"{API}/evaluations/{evaluation['id']}/findings", json={"finding_index": 99}
    )
    assert r.status_code == 400


# ================================================================== 12
def test_record_failures_flag_creates_failure_records(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-record")
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {"input": "q", "expected": "expected"},
    ])
    exp = _make_experiment(app_client, owner, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]
    case = _cases(app_client, owner, version_id)[0]
    run = _insert_run(owner, exp, configuration_id, case["id"], normalized_response="wrong")
    _insert_runs(app_client, [run])

    evaluation = _evaluate(
        app_client, exp["id"], run_id=run["id"],
        evaluator_type="deterministic", method="exact_match",
        record_failures=True,
    )
    assert evaluation["result"]["outcome"] == "FAIL"

    async def _listed():
        return await repo.failures.list(
            {"owner_id": owner, "evaluation_id": evaluation["id"]}, limit=10
        )

    stored = app_client.portal.call(_listed)
    assert len(stored) == 1
    assert stored[0]["source"] == "RULE"
    assert stored[0]["run_id"] == run["id"]
    assert stored[0]["category"] == "WRONG_ANSWER"


# ================================================================== 13
def test_structured_judge_pass_via_run_endpoint(app_client, as_user, monkeypatch):
    import json as _json

    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-judge-pass")
    models = _make_models(app_client, owner, 1)
    judge_model = _make_models(app_client, owner, 1)[0]

    r = app_client.post(f"{API}/rubrics", json={
        "name": f"pytest-rubric-{uuid.uuid4().hex[:6]}",
        "dimensions": [{
            "key": "correctness",
            "label": "Correctness",
            "description": "Is it correct?",
            "max_score": 10,
            "weight": 1.0,
        }],
    })
    assert r.status_code == 200, r.text
    rubric_id = r.json()["id"]

    async def _judge_ident():
        doc = await repo.models.get(judge_model, owner)
        return doc["model_identifier"]

    judge_ident = app_client.portal.call(_judge_ident)

    def executor(model, req):
        if model.get("model_identifier") == judge_ident:
            return result_success(_json.dumps({
                "dimensions": {"correctness": {"score": 9, "rationale": "correct"}},
                "findings": [],
                "overall_result": "pass",
                "confidence": 0.9,
            }))
        return result_success("generic")

    _install(monkeypatch, executor)

    run = {
        "id": f"pytest-run-{uuid.uuid4().hex}",
        "owner_id": owner,
        "project_id": project_id,
        "experiment_id": None,
        "status": "success",
        "normalized_response": "The answer is 42.",
        "created_at": now_iso(),
    }
    app_client.portal.call(repo.runs.insert, run)

    r = app_client.post(f"{API}/runs/{run['id']}/evaluate", json={
        "run_id": run["id"],
        "evaluator_type": "judge_model",
        "judge_model_id": judge_model,
        "rubric_id": rubric_id,
    })
    assert r.status_code == 200, r.text
    evaluation = r.json()
    assert evaluation["evaluator_type"] == "judge_model"
    assert evaluation["eval_type"] == "ai_judge"
    assert evaluation["result"]["outcome"] == "PASS"
    assert evaluation["result"]["passed"] is True
    assert evaluation["parsed_score"] == pytest.approx(0.9)
    assert evaluation["judge"]["tools_enabled"] is False
    assert evaluation["judge"]["run_id"] == evaluation["judge_run_id"]
    assert evaluation["provenance"]["rubric_id"] == rubric_id

    listed = app_client.get(f"{API}/runs/{run['id']}/evaluations")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
