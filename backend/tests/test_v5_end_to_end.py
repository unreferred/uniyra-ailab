"""V5 end-to-end acceptance tests (Research Quality).

These complement the V5 unit/API suites by driving the real HTTP surface with
offline scripted adapters: a structured judge evaluation (including malformed
output and the tools-off guarantee), knowledge-marker provenance, knowledge-OFF
isolation, forbidden tool-call policy, run immutability and truthful
missing-telemetry handling.

Everything is offline; no live provider, Docker container or network egress is
involved. All records belong to throwaway ``pytest-`` owners purged by the
``as_user`` fixture.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app import providers as providers_module
from app import repository as repo
from app import services
from app.models import now_iso
from app.services import execution as execution_module
from app.services import regression_fixtures as fixtures
from app.services import tracing

from _support import (
    adapter_factory,
    create_model,
    create_provider,
    result_success,
)

API = "/api"
NONCE = fixtures.FIXTURE_NONCE

JUDGE_RESPONSE = json.dumps({
    "dimensions": {
        "correctness": {"score": 9, "rationale": "correct"},
        "grounding": {"score": 8, "rationale": "grounded"},
    },
    "findings": [
        {"category": "grounding", "severity": "low", "message": "minor citation gap"},
    ],
    "overall_result": "pass",
    "confidence": 0.9,
})


# ------------------------------------------------------------------ helpers
def _install(monkeypatch, executor, streamer=None):
    factory = adapter_factory(executor, streamer)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


def _make_project(client, name):
    r = client.post(f"{API}/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_models(client, owner, count):
    async def _setup():
        provider_id = await create_provider(owner)
        ids = []
        for index in range(count):
            ids.append(await create_model(
                owner,
                provider_id=provider_id,
                display_name=f"pytest-v5e2e-{index}-{uuid.uuid4().hex[:6]}",
                model_identifier=f"pytest/v5e2e/{uuid.uuid4().hex}",
                input_price_per_1m=1.0,
                output_price_per_1m=2.0,
            ))
        return provider_id, ids

    return client.portal.call(_setup)


def _model_docs(client, owner, model_ids):
    async def _load():
        return [await repo.models.get(model_id, owner) for model_id in model_ids]

    return client.portal.call(_load)


def _make_dataset(client, project_id, cases):
    r = client.post(
        f"{API}/datasets",
        json={"project_id": project_id, "name": f"pytest-v5e2e-{uuid.uuid4().hex[:6]}"},
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
        for key in (
            "assertions",
            "tool_policy",
            "knowledge_policy",
            "grounding_expectations",
            "reference_answer",
            "metadata",
        ):
            if key in case:
                payload[key] = case[key]
        r = client.post(
            f"{API}/datasets/{dataset_id}/versions/{version['id']}/cases", json=payload
        )
        assert r.status_code == 200, r.text
    r = client.post(f"{API}/datasets/{dataset_id}/versions/{version['id']}/freeze")
    assert r.status_code == 200, r.text
    return dataset_id, version["id"]


def _cases(client, owner, version_id):
    async def _load():
        return await repo.dataset_version_cases.list(
            {"owner_id": owner, "dataset_version_id": version_id},
            sort_field="ordering",
            descending=False,
            limit=100,
        )

    return client.portal.call(_load)


def _make_experiment(client, project_id, model_ids, dataset_id, version_id):
    configs = [
        {"label": f"cfg-{index}", "model_id": model_id, "max_tokens": 100, "order": index}
        for index, model_id in enumerate(model_ids)
    ]
    body = {
        "project_id": project_id,
        "title": f"pytest-v5e2e-{uuid.uuid4().hex[:6]}",
        "experiment_type": "benchmark",
        "protocol": {
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "repetitions": 1,
        },
        "configurations": configs,
    }
    r = client.post(f"{API}/experiments", json=body)
    assert r.status_code == 200, r.text
    exp = r.json()
    r = client.post(f"{API}/experiments/{exp['id']}/ready")
    assert r.status_code == 200, r.text
    return r.json()


def _run_doc(owner, **overrides):
    run = {
        "id": f"pytest-run-{uuid.uuid4().hex}",
        "owner_id": owner,
        "project_id": None,
        "experiment_id": None,
        "execution_kind": "single",
        "configuration_id": None,
        "dataset_case_id": None,
        "repetition_index": 0,
        "attempt_index": 0,
        "model_id": None,
        "status": "success",
        "normalized_response": "",
        "raw_response": "",
        "latency_ms": 1,
        "created_at": now_iso(),
    }
    run.update(overrides)
    return run


def _insert_run(client, run):
    client.portal.call(repo.runs.insert, run)
    return run


def _snapshot_run(client, owner, run_id):
    async def _get():
        return await repo.runs.get(run_id, owner)

    return client.portal.call(_get)


def _start_trace(client, owner, project_id, run_id=None):
    async def _start():
        return await tracing.start_trace(
            owner, "benchmark", project_id=project_id, root_run_id=run_id
        )

    return client.portal.call(_start)


def _make_rubric(client, dimensions=None):
    r = client.post(f"{API}/rubrics", json={
        "name": f"pytest-rubric-{uuid.uuid4().hex[:6]}",
        "dimensions": dimensions or [
            {"key": "correctness", "label": "Correctness", "description": "correct?",
             "max_score": 10, "weight": 1.0},
            {"key": "grounding", "label": "Grounding", "description": "grounded?",
             "max_score": 10, "weight": 1.0},
        ],
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _evaluate_experiment(client, experiment_id, **body):
    r = client.post(f"{API}/experiments/{experiment_id}/evaluate", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _evaluate_run(client, run_id, **body):
    r = client.post(f"{API}/runs/{run_id}/evaluate", json={"run_id": run_id, **body})
    assert r.status_code == 200, r.text
    return r.json()


def _judge_executor(judge_ident, judge_text, *, capture=None):
    def executor(model, req):
        if capture is not None:
            capture.append((model.get("model_identifier"), list(req.tools or [])))
        if model.get("model_identifier") == judge_ident:
            return result_success(judge_text)
        return result_success("generic candidate output")

    return executor


# ================================================================== 1
def test_judge_structured_result_end_to_end(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5e2e-judge")
    provider_id, models = _make_models(app_client, owner, 2)
    candidate_model, judge_model = models
    candidate_doc, judge_doc = _model_docs(app_client, owner, models)
    judge_ident = judge_doc["model_identifier"]

    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {"input": "What is 2+2?", "expected": "4"},
    ])
    exp = _make_experiment(app_client, project_id, models, dataset_id, version_id)
    configuration_id = exp["configurations"][0]["id"]
    case = _cases(app_client, owner, version_id)[0]
    rubric_id = _make_rubric(app_client)

    _install(monkeypatch, _judge_executor(judge_ident, JUDGE_RESPONSE))

    run = _insert_run(app_client, _run_doc(
        owner,
        project_id=project_id,
        experiment_id=exp["id"],
        execution_kind="benchmark",
        configuration_id=configuration_id,
        dataset_case_id=case["id"],
        model_id=candidate_model,
        normalized_response="The answer is 4.",
    ))
    before = _snapshot_run(app_client, owner, run["id"])

    passed = _evaluate_experiment(
        app_client, exp["id"], run_id=run["id"], evaluator_type="judge_model",
        judge_model_id=judge_model, rubric_id=rubric_id, threshold=0.5,
    )
    assert passed["status"] == "completed"
    assert passed["result"]["outcome"] == "PASS"
    assert passed["result"]["passed"] is True
    assert passed["result"]["threshold"] == pytest.approx(0.5)
    assert passed["result"]["confidence"] == pytest.approx(0.9)
    assert passed["parsed_score"] == pytest.approx(0.85)
    assert passed["scores"]["correctness"] == pytest.approx(0.9)
    assert passed["scores"]["grounding"] == pytest.approx(0.8)
    assert passed["findings"], "judge findings must be parsed"
    assert passed["findings"][0]["category"] == "grounding"
    assert passed["findings"][0]["message"] == "minor citation gap"
    assert passed["judge"]["tools_enabled"] is False
    assert passed["judge"]["model_name"] == judge_doc["display_name"]
    assert passed["judge"]["model_identifier"] == judge_ident
    assert passed["judge"]["provider_id"] == provider_id
    assert passed["usage"]["input_tokens"] is not None
    assert passed["usage"]["output_tokens"] is not None

    after = _snapshot_run(app_client, owner, run["id"])
    assert before == after, "evaluation must not mutate the source run"

    # The same structured result with a stricter threshold must FAIL.
    failed = _evaluate_experiment(
        app_client, exp["id"], run_id=run["id"], evaluator_type="judge_model",
        judge_model_id=judge_model, rubric_id=rubric_id, threshold=0.95,
    )
    assert failed["status"] == "completed"
    assert failed["result"]["outcome"] == "FAIL"
    assert failed["result"]["passed"] is False
    assert failed["parsed_score"] == pytest.approx(0.85)
    assert _snapshot_run(app_client, owner, run["id"]) == before


# ================================================================== 2
def test_malformed_judge_output_end_to_end(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5e2e-judge-bad")
    _, judge_models = _make_models(app_client, owner, 1)
    judge_model = judge_models[0]
    (judge_doc,) = _model_docs(app_client, owner, [judge_model])
    rubric_id = _make_rubric(app_client)

    _install(monkeypatch, _judge_executor(
        judge_doc["model_identifier"],
        "The answer looks pretty good overall, I would score it highly.",
    ))

    run = _insert_run(app_client, _run_doc(
        owner, project_id=project_id, normalized_response="The answer is 4.",
    ))

    evaluation = _evaluate_run(
        app_client, run["id"], evaluator_type="judge_model",
        judge_model_id=judge_model, rubric_id=rubric_id,
    )
    assert evaluation["status"] == "error"
    assert evaluation["result"]["outcome"] == "ERROR"
    assert evaluation["result"]["outcome"] != "PASS"
    assert evaluation["result"]["passed"] is None
    assert evaluation["parsed_score"] is None


# ================================================================== 3
def test_judge_tools_are_off(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5e2e-judge-tools")
    _, judge_models = _make_models(app_client, owner, 1)
    judge_model = judge_models[0]
    (judge_doc,) = _model_docs(app_client, owner, [judge_model])
    judge_ident = judge_doc["model_identifier"]
    rubric_id = _make_rubric(app_client)

    capture = []
    _install(monkeypatch, _judge_executor(judge_ident, JUDGE_RESPONSE, capture=capture))

    run = _insert_run(app_client, _run_doc(
        owner, project_id=project_id, normalized_response="The answer is 4.",
    ))
    evaluation = _evaluate_run(
        app_client, run["id"], evaluator_type="judge_model",
        judge_model_id=judge_model, rubric_id=rubric_id, threshold=0.5,
    )
    assert evaluation["status"] == "completed"

    judge_calls = [tools for ident, tools in capture if ident == judge_ident]
    assert judge_calls, "the judge model must have been called"
    assert all(not tools for tools in judge_calls), "judge request must carry no tools"
    assert evaluation["judge"]["tools_enabled"] is False

    async def _judge_run_doc():
        return await repo.runs.find_one(
            {"owner_id": owner, "execution_kind": "judge"}
        )

    judge_run = app_client.portal.call(_judge_run_doc)
    assert judge_run is not None
    assert not any(
        call.get("tool_name")
        for call in (judge_run.get("tool_loop") or [])
        if isinstance(call, dict)
    )


# ================================================================== 4
def test_knowledge_marker_provenance_end_to_end(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5e2e-knowledge")
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {
            "input": "Summarize the document.",
            "expected": "",
            "knowledge_policy": {"require_valid_markers": True, "expect_retrieval": True},
        },
    ])
    case = _cases(app_client, owner, version_id)[0]

    trace = _start_trace(app_client, owner, project_id)
    valid = _insert_run(app_client, _run_doc(
        owner,
        project_id=project_id,
        dataset_case_id=case["id"],
        trace_id=trace["id"],
        normalized_response="Answer uses [K1].",
        knowledge_enabled=True,
        knowledge_provenance={"chunk_ids": ["c1"], "chunk_count": 1, "ordering": [1]},
    ))
    fabricated = _insert_run(app_client, _run_doc(
        owner,
        project_id=project_id,
        dataset_case_id=case["id"],
        trace_id=trace["id"],
        normalized_response="Answer uses [K99].",
        knowledge_enabled=True,
        knowledge_provenance={"chunk_ids": ["c1"], "chunk_count": 1, "ordering": [1]},
    ))

    passed = _evaluate_run(
        app_client, valid["id"], evaluator_type="rule",
        evaluator_keys=["knowledge_grounding"],
    )
    assert passed["result"]["outcome"] == "PASS"
    assert passed["evidence"]["knowledge_chunk_ids"] == ["c1"]

    failed = _evaluate_run(
        app_client, fabricated["id"], evaluator_type="rule",
        evaluator_keys=["knowledge_grounding"],
    )
    assert failed["result"]["outcome"] == "FAIL"
    assert failed["findings"], "a fabricated marker must produce a finding"
    assert any(
        finding["category"] in ("citation", "grounding")
        and "fabricated" in finding["message"]
        for finding in failed["findings"]
    )
    assert failed["evidence"]["knowledge_chunk_ids"] == ["c1"]


# ================================================================== 5
def test_knowledge_off_assertion_end_to_end(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5e2e-knowledge-off")
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {
            "input": "Answer without using the Knowledge collection.",
            "expected": "",
            "assertions": [{"type": "output_not_contains", "value": NONCE}],
            "knowledge_policy": {
                "knowledge_expected": False,
                "require_no_knowledge": True,
            },
        },
    ])
    case = _cases(app_client, owner, version_id)[0]

    off = _insert_run(app_client, _run_doc(
        owner,
        project_id=project_id,
        dataset_case_id=case["id"],
        normalized_response="This is a plain answer with no retrieved evidence.",
        knowledge_enabled=False,
    ))
    retrieved = _insert_run(app_client, _run_doc(
        owner,
        project_id=project_id,
        dataset_case_id=case["id"],
        normalized_response="This is a plain answer with no retrieved evidence.",
        knowledge_enabled=True,
        knowledge_provenance={"chunk_ids": ["c1", "c2"], "chunk_count": 2},
    ))

    passed = _evaluate_run(app_client, off["id"], evaluator_type="rule")
    assert passed["status"] == "completed"
    assert passed["result"]["outcome"] == "PASS"

    failed = _evaluate_run(app_client, retrieved["id"], evaluator_type="rule")
    assert failed["result"]["outcome"] == "FAIL"
    assert any(
        finding["category"] == "grounding"
        and finding["message"] == "unexpected_knowledge_retrieval"
        for finding in failed["findings"]
    )


# ================================================================== 6
def test_forbidden_tool_call_violation_end_to_end(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5e2e-tools")
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {
            "input": "Answer without tools.",
            "expected": "",
            "tool_policy": {"allow_tools": False, "expected_tool_call_count": 0},
        },
    ])
    case = _cases(app_client, owner, version_id)[0]

    violating = _insert_run(app_client, _run_doc(
        owner,
        project_id=project_id,
        dataset_case_id=case["id"],
        normalized_response="done",
        tool_call_count=4,
        tool_failure_count=0,
        tool_success_count=4,
    ))
    app_client.portal.call(repo.tool_calls.insert, {
        "id": f"pytest-toolcall-{uuid.uuid4().hex}",
        "owner_id": owner,
        "project_id": project_id,
        "run_id": violating["id"],
        "trace_id": None,
        "tool_name": "code_interpreter",
        "execution_status": "success",
        "created_at": now_iso(),
    })
    clean = _insert_run(app_client, _run_doc(
        owner,
        project_id=project_id,
        dataset_case_id=case["id"],
        normalized_response="4",
        tool_call_count=0,
        tool_failure_count=0,
        tool_success_count=0,
    ))

    failed = _evaluate_run(
        app_client, violating["id"], evaluator_type="rule",
        evaluator_keys=["tool_policy"],
    )
    assert failed["result"]["outcome"] == "FAIL"
    categories = {finding["category"] for finding in failed["findings"]}
    assert categories & {"instruction_following", "tool_selection"}
    assert not (categories & {"grounding", "citation"}), (
        "a tool-policy violation must not be reported as a knowledge failure"
    )

    passed = _evaluate_run(
        app_client, clean["id"], evaluator_type="rule",
        evaluator_keys=["tool_policy"],
    )
    assert passed["result"]["outcome"] == "PASS"


# ================================================================== 7
def test_failed_judge_evaluation_does_not_mutate_run(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5e2e-immutable-judge")
    _, judge_models = _make_models(app_client, owner, 1)
    judge_model = judge_models[0]
    (judge_doc,) = _model_docs(app_client, owner, [judge_model])
    rubric_id = _make_rubric(app_client)

    _install(monkeypatch, _judge_executor(
        judge_doc["model_identifier"], "not structured at all",
    ))

    run = _insert_run(app_client, _run_doc(
        owner, project_id=project_id, normalized_response="The answer is 4.",
    ))
    before = _snapshot_run(app_client, owner, run["id"])

    evaluation = _evaluate_run(
        app_client, run["id"], evaluator_type="judge_model",
        judge_model_id=judge_model, rubric_id=rubric_id,
    )
    assert evaluation["status"] == "error"
    assert evaluation["result"]["outcome"] == "ERROR"

    after = _snapshot_run(app_client, owner, run["id"])
    assert before == after, "a failed evaluation must not mutate the source run"


# ================================================================== 8
def test_missing_tool_telemetry_is_not_zero(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5e2e-missing-telemetry")
    dataset_id, version_id = _make_dataset(app_client, project_id, [
        {
            "input": "Answer without tools.",
            "expected": "",
            "tool_policy": {"expected_tool_call_count": 0},
        },
    ])
    case = _cases(app_client, owner, version_id)[0]

    run = _insert_run(app_client, _run_doc(
        owner,
        project_id=project_id,
        dataset_case_id=case["id"],
        normalized_response="4",
    ))
    assert "tool_call_count" not in run
    assert run.get("trace_id") is None

    evaluation = _evaluate_run(
        app_client, run["id"], evaluator_type="rule",
        evaluator_keys=["tool_policy"],
    )
    assert evaluation["status"] == "not_evaluated"
    assert evaluation["result"]["outcome"] == "NOT_EVALUATED"
    assert evaluation["result"]["passed"] is None
    assert evaluation["evidence"]["tool_call_count"] is None
    tool_violation_categories = {"instruction_following", "tool_selection", "tool_failure"}
    assert not any(
        finding.get("category") in tool_violation_categories
        for finding in evaluation["findings"]
    ), "missing telemetry must never be reported as a tool violation"
