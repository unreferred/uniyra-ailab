"""V5.8 Research Quality Scorecard tests.

Offline and deterministic: every record is created by the test through the API
or direct repository inserts on the client's portal loop, and all owners are
throwaway ``pytest-`` ids purged by the ``as_user`` fixture. Coverage mirrors the
V5.8 contract: missing-is-not-zero, NOT_EVALUATED vs unavailable, provenance
labels, evidence links, the absence of a global score without an explicit
versioned policy, persistence/listing, owner/project isolation, comparison
classifications and policy-gated composites.
"""
from __future__ import annotations

import uuid

import pytest

from app import repository as repo
from app.deps import get_current_user
from app.models import now_iso
from app.services import scorecards as scorecard_service
from app.services.scorecards import ScorecardError

from _support import create_model, create_provider

API = "/api"


# ------------------------------------------------------------------ helpers
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
                display_name=f"pytest-sc-model-{index}-{uuid.uuid4().hex[:6]}",
                model_identifier=f"pytest/{uuid.uuid4().hex}",
            ))
        return ids

    return client.portal.call(_setup)


def _make_experiment(client, owner, project_id, model_ids):
    configs = [
        {"label": f"cfg-{index}", "model_id": model_id, "max_tokens": 100, "order": index}
        for index, model_id in enumerate(model_ids)
    ]
    r = client.post(f"{API}/experiments", json={
        "project_id": project_id,
        "title": f"pytest-scorecard-{uuid.uuid4().hex[:6]}",
        "experiment_type": "benchmark",
        "protocol": {},
        "configurations": configs,
    })
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
        "model_id": None,
        "dataset_case_id": None,
        "repetition_index": 0,
        "attempt_index": 0,
        "status": "success",
        "normalized_response": "answer",
        "latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost": None,
        "cost_available": False,
        "tool_call_count": None,
        "tool_failure_count": None,
        "source_count": None,
        "evidence_item_count": None,
        "created_at": now_iso(),
    }
    run.update(overrides)
    return run


def _eval_doc(owner, run_id, evaluator_type="deterministic", **overrides):
    doc = {
        "id": f"pytest-eval-{uuid.uuid4().hex}",
        "owner_id": owner,
        "run_id": run_id,
        "project_id": None,
        "experiment_id": None,
        "configuration_id": None,
        "evaluator_type": evaluator_type,
        "evaluation_type": evaluator_type,
        "eval_type": evaluator_type,
        "method": None,
        "status": "completed",
        "findings": [],
        "scores": {},
        "metrics": {},
        "evidence": {},
        "result": {
            "outcome": "PASS",
            "status": "completed",
            "passed": True,
            "score": None,
            "reason": None,
        },
        "created_at": now_iso(),
    }
    doc.update(overrides)
    return doc


def _seed(client, docs):
    async def _insert():
        for collection, doc in docs:
            await collection.insert(doc)

    client.portal.call(_insert)


def _all_dimensions(body):
    return {dim["key"]: dim for dim in body["dimensions"]}


# ================================================================== 1
def test_missing_is_not_zero(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-sc-missing")
    run = _run_doc(owner, project_id=project_id, status="running", normalized_response=None)
    _seed(app_client, [(repo.runs, run)])

    r = app_client.get(f"{API}/scorecards/run/{run['id']}")
    assert r.status_code == 200, r.text
    body = r.json()

    assert len(body["dimensions"]) == len(scorecard_service.DIMENSION_ORDER)
    dims = _all_dimensions(body)
    for key in scorecard_service.DIMENSION_ORDER:
        dimension = dims[key]
        assert dimension["state"] == "not_available", key
        assert dimension["value"] is None, key
        # A missing observation must never surface as zero.
        assert dimension["value"] != 0, key

    # Same verdict from the live service builder.
    live = app_client.portal.call(scorecard_service.build_run_scorecard, owner, run["id"])
    assert all(d["value"] is None for d in live["dimensions"])
    assert live["composite"]["available"] is False


# ================================================================== 2
def test_not_evaluated_is_distinct_from_unavailable(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-sc-not-evaluated")
    run = _run_doc(owner, project_id=project_id)
    evaluation = _eval_doc(
        owner,
        run["id"],
        evaluator_type="deterministic",
        method="exact_match",
        evaluator={"type": "deterministic", "key": "exact_match"},
        status="not_evaluated",
        scores={},
        result={
            "outcome": "NOT_EVALUATED",
            "status": "not_evaluated",
            "passed": None,
            "score": None,
            "reason": "run_failed",
        },
    )
    _seed(app_client, [(repo.runs, run), (repo.evaluations, evaluation)])

    body = app_client.get(f"{API}/scorecards/run/{run['id']}").json()
    dims = _all_dimensions(body)

    correctness = dims["correctness"]
    assert correctness["state"] == "not_evaluated"
    assert correctness["value"] is None
    assert correctness["value"] != 0
    assert correctness["not_evaluated_count"] == 1

    # A dimension with nothing observable stays unavailable, not not_evaluated.
    assert dims["completeness"]["state"] == "not_available"

    assert body["evaluation_outcomes"]["NOT_EVALUATED"] == 1
    assert body["not_evaluated_evidence"]
    assert body["not_evaluated_evidence"][0]["evaluation_id"] == evaluation["id"]


# ================================================================== 3
def test_source_labels_deterministic_and_judge_derived(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-sc-sources")

    rule_run = _run_doc(owner, project_id=project_id)
    rule_eval = _eval_doc(
        owner,
        rule_run["id"],
        evaluator_type="rule",
        method="rule",
        scores={"grounding": 1.0},
        result={
            "outcome": "PASS",
            "status": "completed",
            "passed": True,
            "score": 1.0,
            "reason": None,
        },
    )

    judge_run = _run_doc(owner, project_id=project_id)
    judge_eval = _eval_doc(
        owner,
        judge_run["id"],
        evaluator_type="judge_model",
        method="judge_model",
        scores={"correctness": 0.9, "completeness": 0.8},
        result={
            "outcome": "PASS",
            "status": "completed",
            "passed": True,
            "score": 0.85,
            "reason": None,
        },
    )
    _seed(app_client, [
        (repo.runs, rule_run),
        (repo.runs, judge_run),
        (repo.evaluations, rule_eval),
        (repo.evaluations, judge_eval),
    ])

    rule_body = app_client.get(f"{API}/scorecards/run/{rule_run['id']}").json()
    grounding = _all_dimensions(rule_body)["grounding"]
    assert grounding["state"] == "available"
    assert grounding["value"] == pytest.approx(1.0)
    assert grounding["source"] == "deterministic"
    assert grounding["deterministic"] is True
    assert grounding["judge_derived"] is False

    judge_body = app_client.get(f"{API}/scorecards/run/{judge_run['id']}").json()
    judge_dims = _all_dimensions(judge_body)
    correctness = judge_dims["correctness"]
    assert correctness["value"] == pytest.approx(0.9)
    assert correctness["source"] == "judge_derived"
    assert correctness["judge_derived"] is True
    assert correctness["deterministic"] is False

    completeness = judge_dims["completeness"]
    assert completeness["value"] == pytest.approx(0.8)
    assert completeness["source"] == "judge_derived"
    assert completeness["judge_derived"] is True

    # No judge key for source_quality -> unavailable, never fabricated.
    assert judge_dims["source_quality"]["state"] == "not_available"
    assert judge_dims["source_quality"]["value"] is None


# ================================================================== 4
def test_dimension_evidence_links_to_evaluation_and_run(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-sc-evidence")
    run = _run_doc(owner, project_id=project_id)
    evaluation = _eval_doc(
        owner,
        run["id"],
        evaluator_type="deterministic",
        method="exact_match",
        scores={"correctness": 1.0},
        result={
            "outcome": "PASS",
            "status": "completed",
            "passed": True,
            "score": 1.0,
            "reason": None,
        },
    )
    _seed(app_client, [(repo.runs, run), (repo.evaluations, evaluation)])

    body = app_client.get(f"{API}/scorecards/run/{run['id']}").json()
    correctness = _all_dimensions(body)["correctness"]
    refs = {(ref["type"], ref["ref_id"]) for ref in correctness["evidence"]}
    assert ("evaluation", evaluation["id"]) in refs
    assert ("run", run["id"]) in refs
    assert correctness["sample_size"] == 1
    assert correctness["explanation"]

    assert body["evidence"]["run_id"] == run["id"]
    assert evaluation["id"] in body["evidence"]["evaluation_ids"]


# ================================================================== 5
def test_no_global_score_without_policy(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-sc-no-composite")
    run = _run_doc(
        owner,
        project_id=project_id,
        latency_ms=123,
        input_tokens=10,
        output_tokens=20,
        source_count=4,
        tool_call_count=0,
        tool_failure_count=0,
    )
    _seed(app_client, [(repo.runs, run)])

    body = app_client.get(f"{API}/scorecards/run/{run['id']}").json()
    composite = body["composite"]
    assert composite["available"] is False
    assert composite["reason"] == "no_scoring_policy"
    assert composite["value"] is None
    assert composite["policy"] is None
    unavailable = {
        d["key"]
        for d in body["dimensions"]
        if d["state"] != "available" or d["value"] is None
    }
    assert set(composite["missing_dimensions"]) == unavailable
    # Telemetry dimensions are available here, so the composite still refuses to
    # invent a global score.
    assert "latency" not in composite["missing_dimensions"]
    assert composite["dimensions_used"] == []

    for forbidden in ("score", "overall_score", "total_score", "weighted_score"):
        assert forbidden not in body
    assert body.get("value") is None


# ================================================================== 6
def test_persist_read_and_list(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-sc-persist")
    run = _run_doc(owner, project_id=project_id, latency_ms=50, status="success")
    _seed(app_client, [(repo.runs, run)])

    created = app_client.post(f"{API}/scorecards", json={
        "project_id": project_id,
        "scope": "run",
        "run_id": run["id"],
        "notes": "pytest note",
    })
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["owner_id"] == owner
    assert body["project_id"] == project_id
    assert body["scope"] == "run"
    assert body["run_id"] == run["id"]
    assert body["notes"] == "pytest note"

    detail = app_client.get(f"{API}/scorecards/{body['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == body["id"]

    listed = app_client.get(f"{API}/scorecards", params={"run_id": run["id"]})
    assert listed.status_code == 200, listed.text
    listing = listed.json()
    assert listing["total"] >= 1
    assert all(item["run_id"] == run["id"] for item in listing["items"])

    by_project = app_client.get(f"{API}/scorecards", params={"project_id": project_id})
    assert by_project.status_code == 200, by_project.text
    assert by_project.json()["total"] >= 1

    # Building emits a best-effort SCORECARD_BUILD span on a system trace.
    assert body.get("trace_id")
    async def _spans():
        return await repo.trace_spans.list(
            {"owner_id": owner, "trace_id": body["trace_id"], "span_type": "SCORECARD_BUILD"},
            limit=10,
        )
    spans = app_client.portal.call(_spans)
    assert len(spans) == 1

    # Unknown project filter is owner-scoped and 404s.
    missing = app_client.get(
        f"{API}/scorecards", params={"project_id": f"pytest-missing-{uuid.uuid4().hex}"}
    )
    assert missing.status_code == 404


# ================================================================== 7
def test_project_and_owner_isolation(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-sc-isolation")
    run = _run_doc(owner, project_id=project_id)
    _seed(app_client, [(repo.runs, run)])
    created = app_client.post(f"{API}/scorecards", json={
        "project_id": project_id,
        "scope": "run",
        "run_id": run["id"],
    })
    assert created.status_code == 200, created.text
    scorecard_id = created.json()["id"]

    # Same-owner cross-project reference is rejected.
    other_project = _make_project(app_client, "pytest-sc-isolation-other")
    cross = app_client.post(f"{API}/scorecards", json={
        "project_id": other_project,
        "scope": "run",
        "run_id": run["id"],
    })
    assert cross.status_code == 400, cross.text

    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: {
        "id": f"pytest-foreign-{uuid.uuid4().hex}",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }
    try:
        detail = app_client.get(f"{API}/scorecards/{scorecard_id}")
        assert detail.status_code == 404, detail.text

        live = app_client.get(f"{API}/scorecards/run/{run['id']}")
        assert live.status_code == 404, live.text

        build = app_client.post(f"{API}/scorecards", json={
            "project_id": project_id,
            "scope": "run",
            "run_id": run["id"],
        })
        assert build.status_code == 404, build.text

        listing = app_client.get(f"{API}/scorecards", params={"project_id": project_id})
        assert listing.status_code == 404, listing.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = original


# ================================================================== 8
def test_comparison_deltas_and_classifications(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-sc-compare")
    models = _make_models(app_client, owner, 2)
    experiment = _make_experiment(app_client, owner, project_id, models)
    baseline_id = experiment["configurations"][0]["id"]
    candidate_id = experiment["configurations"][1]["id"]

    baseline_run = _run_doc(
        owner,
        project_id=project_id,
        experiment_id=experiment["id"],
        configuration_id=baseline_id,
        execution_kind="benchmark",
        latency_ms=100,
        input_tokens=10,
        output_tokens=20,
        estimated_cost=0.01,
        cost_available=True,
        source_count=2,
        tool_call_count=0,
        tool_failure_count=0,
    )
    candidate_run = _run_doc(
        owner,
        project_id=project_id,
        experiment_id=experiment["id"],
        configuration_id=candidate_id,
        execution_kind="benchmark",
        latency_ms=50,
        input_tokens=8,
        output_tokens=12,
        estimated_cost=0.005,
        cost_available=True,
        source_count=3,
        tool_call_count=0,
        tool_failure_count=0,
    )
    _seed(app_client, [(repo.runs, baseline_run), (repo.runs, candidate_run)])

    r = app_client.get(
        f"{API}/scorecards/experiment/{experiment['id']}/compare",
        params={
            "baseline_configuration_id": baseline_id,
            "candidate_configuration_id": candidate_id,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "comparison"
    dims = {row["key"]: row for row in body["dimensions"]}

    assert dims["latency"]["delta"] == pytest.approx(-50.0)
    assert dims["latency"]["classification"] == "improved"
    assert dims["token_use"]["delta"] == pytest.approx(-10.0)
    assert dims["token_use"]["classification"] == "improved"
    assert dims["cost"]["classification"] == "improved"
    assert dims["research_breadth"]["delta"] == pytest.approx(1.0)
    assert dims["research_breadth"]["classification"] == "improved"
    assert dims["runtime_reliability"]["classification"] == "unchanged"

    # A dimension missing on at least one side is unknown, never zero/unchanged.
    completeness = dims["completeness"]
    assert completeness["baseline"] is None and completeness["candidate"] is None
    assert completeness["delta"] is None
    assert completeness["classification"] == "unknown"

    assert body["significance"] is None
    assert body["significance_note"] == "no statistical significance claim"
    for row in body["dimensions"]:
        assert row["significance"] is None
        assert row["significance_note"] == "no statistical significance claim"

    # Missing configuration is rejected, not silently compared.
    bad = app_client.get(
        f"{API}/scorecards/experiment/{experiment['id']}/compare",
        params={
            "baseline_configuration_id": "pytest-missing-baseline",
            "candidate_configuration_id": candidate_id,
        },
    )
    assert bad.status_code == 404, bad.text

    # The experiment scorecard aggregates the same runs per configuration.
    exp_body = app_client.get(f"{API}/scorecards/experiment/{experiment['id']}").json()
    assert exp_body["scope"] == "experiment"
    assert len(exp_body["configurations"]) == 2
    overall_latency = exp_body["overall"]["dimension_map"]["latency"]
    assert overall_latency["state"] == "available"
    assert overall_latency["value"] == pytest.approx(75.0)
    assert overall_latency["sample_size"] == 2

    # An experiment-scope scorecard also persists and reads back.
    persisted = app_client.post(f"{API}/scorecards", json={
        "project_id": project_id,
        "scope": "experiment",
        "experiment_id": experiment["id"],
    })
    assert persisted.status_code == 200, persisted.text
    stored = persisted.json()
    assert stored["scope"] == "experiment"
    assert stored["experiment_id"] == experiment["id"]
    read_back = app_client.get(f"{API}/scorecards/{stored['id']}")
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["id"] == stored["id"]


# ================================================================== 9
def test_composite_only_with_explicit_versioned_policy(app_client, as_user):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-sc-policy")
    run = _run_doc(owner, project_id=project_id, latency_ms=42, status="success",
                   tool_call_count=0, tool_failure_count=0, source_count=1)
    _seed(app_client, [(repo.runs, run)])

    policy = {
        "id": "pytest-scoring-policy",
        "version": 3,
        "weights": {"latency": 1.0, "cost": 2.0},
    }
    body = app_client.portal.call(
        scorecard_service.build_run_scorecard, owner, run["id"], policy
    )
    composite = body["composite"]
    assert composite["available"] is True
    assert composite["reason"] is None
    assert composite["value"] == pytest.approx(42.0)
    assert composite["policy"]["id"] == "pytest-scoring-policy"
    assert composite["policy"]["version"] == 3
    assert composite["policy"]["weights"] == {"latency": 1.0, "cost": 2.0}
    assert composite["dimensions_used"] == ["latency"]
    assert "cost" in composite["missing_dimensions"]
    assert "grounding" not in composite["dimensions_used"]

    # An incomplete policy is a domain error, not a silent default.
    with pytest.raises(ScorecardError):
        app_client.portal.call(
            scorecard_service.build_run_scorecard, owner, run["id"], {"id": "p", "version": 1}
        )
    with pytest.raises(ScorecardError):
        app_client.portal.call(
            scorecard_service.build_run_scorecard,
            owner,
            run["id"],
            {"id": "p", "version": 1, "weights": {"not_a_dimension": 1.0}},
        )
