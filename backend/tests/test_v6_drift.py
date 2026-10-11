"""V6.9 Drift & Failure Analysis tests.

Offline and deterministic: every record belongs to a throwaway ``pytest-``
owner purged by the ``as_user`` fixture. Coverage mirrors the V6.9 contract:
deterministic architecture/design/logic/prompt drift against observable
evidence, judge-derived provenance (and the refusal to fabricate judge findings
without a run/rubric), canonical QUALITY taxonomy, materialization into ordinary
findings, regression promotion, the additive scorecard drift dimension,
ownership isolation and immutability.
"""
from __future__ import annotations

import json
import uuid

from app import providers as providers_module
from app import repository as repo
from app import services
from app.deps import get_current_user
from app.models import now_iso
from app.services import execution as execution_module
from app.services import drift as drift_service
from app.services import failures as failure_service
from app.services import scorecards as scorecard_service
from app.services import failure_taxonomy as taxonomy

from _support import (
    ScriptedAdapter,
    create_model,
    create_provider,
    new_owner_id,
    purge_owner,
    result_success,
)

API = "/api"


# ------------------------------------------------------------------ helpers
def _project(client, name="pytest-v69"):
    r = client.post(f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _analyze(client, **body):
    r = client.post(f"{API}/drift/analyze", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _make_model(client, owner):
    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(
            owner,
            provider_id=provider_id,
            display_name=f"pytest-drift-judge-{uuid.uuid4().hex[:6]}",
            model_identifier=f"pytest/{uuid.uuid4().hex}",
        )

    return client.portal.call(_setup)


class _Adapter(ScriptedAdapter):
    supports_model_discovery = False
    supports_tools = False
    supports_vision = False


def _install(monkeypatch, executor):
    factory = lambda provider: _Adapter(executor)  # noqa: E731
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


def _run_doc(owner, **overrides):
    run = {
        "id": f"pytest-run-{uuid.uuid4().hex}",
        "owner_id": owner,
        "project_id": None,
        "experiment_id": None,
        "status": "success",
        "normalized_response": "answer",
        "messages": [{"role": "user", "content": "do the task"}],
        "trace_id": None,
        "created_at": now_iso(),
    }
    run.update(overrides)
    return run


# ================================================================== 1
def test_architecture_drift_parallel_subsystem(app_client, as_user):
    project = _project(app_client, "pytest-v69-arch")

    body = {
        "project_id": project,
        "scope": "architecture",
        "changed_files": [{"path": "app/services/legacy/billing.py"}],
        "contracts": {
            "canonical_services": [
                {"name": "billing", "paths": ["app/services/billing.py"]},
            ]
        },
    }
    doc = _analyze(app_client, **body)
    assert doc["version"] == "v6.9.0"
    assert doc["summary"]["total"] == 1
    assert doc["summary"]["deterministic_count"] == 1
    assert doc["summary"]["judge_derived_count"] == 0

    finding = doc["findings"][0]
    assert finding["drift_type"] == "ARCHITECTURE_DRIFT"
    assert finding["category"] == "architecture_drift"
    assert finding["source"] == "deterministic"
    assert finding["provenance"]["check_id"].startswith(
        "architecture.parallel_subsystem"
    )
    assert finding["provenance"]["drift_version"] == "v6.9.0"
    assert finding["evidence"]["path"] == "app/services/legacy/billing.py"
    assert finding["evidence"]["service_name"] == "billing"
    assert finding["evidence"]["declared_paths"] == ["app/services/billing.py"]

    # A change at the canonical declared path produces no architecture drift.
    clean = {
        "project_id": project,
        "scope": "architecture",
        "changed_files": [{"path": "app/services/billing.py"}],
        "contracts": body["contracts"],
    }
    clean_doc = _analyze(app_client, **clean)
    assert clean_doc["findings"] == []
    assert clean_doc["summary"]["total"] == 0


# ================================================================== 2
def test_design_drift_hardcoded_color(app_client, as_user):
    project = _project(app_client, "pytest-v69-design")

    doc = _analyze(
        app_client,
        project_id=project,
        scope="design",
        changed_files=[
            {"path": "web/Button.tsx", "hardcoded_colors": ["#ff0000"]},
        ],
        contracts={"design_tokens": ["#00ff00"]},
    )
    assert len(doc["findings"]) == 1
    finding = doc["findings"][0]
    assert finding["drift_type"] == "DESIGN_DRIFT"
    assert finding["provenance"]["check_id"].startswith(
        "design.hardcoded_design_token"
    )
    assert finding["evidence"]["value"] == "#ff0000"
    assert finding["evidence"]["expected_tokens"] == ["#00ff00"]

    # A color that IS a declared token is not drift.
    clean = _analyze(
        app_client,
        project_id=project,
        scope="design",
        changed_files=[
            {"path": "web/Button.tsx", "hardcoded_colors": ["#00ff00"]},
        ],
        contracts={"design_tokens": ["#00ff00"]},
    )
    assert clean["findings"] == []


# ================================================================== 3
def test_logic_drift_duplicated_rule_and_conflicting_enum(app_client, as_user):
    project = _project(app_client, "pytest-v69-logic")

    doc = _analyze(
        app_client,
        project_id=project,
        scope="logic",
        changed_files=[
            {"path": "app/services/tax.py", "imports_added": ["from app.tax import compute_tax"]},
            {"path": "web/tax.ts", "constants_added": ["calculateTax"]},
        ],
        contracts={
            "duplicated_rules": [
                {
                    "id": "tax-rule",
                    "backend_pattern": "compute_tax",
                    "frontend_pattern": "calculateTax",
                }
            ],
            "enums": [
                {
                    "name": "Status",
                    "backend_values": ["A", "B"],
                    "frontend_values": ["A", "C"],
                }
            ],
        },
    )
    assert doc["summary"]["by_drift_type"]["LOGIC_DRIFT"] == 2
    check_ids = {f["provenance"]["check_id"] for f in doc["findings"]}
    assert "logic.duplicated_business_rule:tax-rule" in check_ids
    assert "logic.conflicting_enum_semantics:Status" in check_ids
    assert all(f["drift_type"] == "LOGIC_DRIFT" for f in doc["findings"])


# ================================================================== 4
def test_prompt_drift_missing_invariant_and_prohibited_scope(app_client, as_user):
    project = _project(app_client, "pytest-v69-prompt")

    doc = _analyze(
        app_client,
        project_id=project,
        scope="prompt",
        prompt_context={
            "required_invariant_codes": ["INV-1", "INV-2"],
            "compiled_invariants_used": ["INV-1"],
            "required_phases": ["Analysis"],
            "prohibited_scope": ["delete production"],
            "output_text": "Final answer: we will delete production now.",
        },
    )
    assert doc["summary"]["by_drift_type"]["PROMPT_DRIFT"] == 3
    check_ids = {f["provenance"]["check_id"] for f in doc["findings"]}
    assert "prompt.missing_required_invariant:INV-2" in check_ids
    assert "prompt.required_phase_omitted:Analysis" in check_ids
    assert "prompt.prohibited_scope_changed:delete production" in check_ids
    assert all(f["drift_type"] == "PROMPT_DRIFT" for f in doc["findings"])


# ================================================================== 5
def test_judge_path_requires_provenance_and_never_fabricates(
    app_client, as_user, monkeypatch
):
    owner = as_user["id"]
    project = _project(app_client, "pytest-v69-judge")

    # Without run_id/rubric the judge is never called: no judge findings.
    no_judge = _analyze(
        app_client,
        project_id=project,
        scope="logic",
        judge_model_id=f"pytest-judge-{uuid.uuid4().hex}",
    )
    assert no_judge["summary"]["judge_derived_count"] == 0
    assert all(f["source"] != "judge_derived" for f in no_judge["findings"])

    # With a run + rubric + a mocked judge adapter, judge findings carry
    # recorded provenance.
    judge_model_id = _make_model(app_client, owner)

    r = app_client.post(f"{API}/rubrics", json={
        "name": f"pytest-drift-rubric-{uuid.uuid4().hex[:6]}",
        "dimensions": [
            {"key": "correctness", "label": "Correctness", "max_score": 10, "weight": 1.0},
        ],
        "threshold": 0.5,
    })
    assert r.status_code == 200, r.text
    rubric_id = r.json()["id"]

    async def _seed():
        experiment_id = f"pytest-exp-{uuid.uuid4().hex}"
        now = now_iso()
        await repo.experiments.insert({
            "id": experiment_id,
            "owner_id": owner,
            "project_id": project,
            "title": "drift judge experiment",
            "created_at": now,
            "updated_at": now,
        })
        run = _run_doc(owner, project_id=project, experiment_id=experiment_id)
        await repo.runs.insert(run)
        return run

    run = app_client.portal.call(_seed)

    judge_response = json.dumps({
        "dimensions": {"correctness": {"score": 3, "rationale": "drifted from intent"}},
        "findings": [
            {
                "category": "logic_drift",
                "severity": "medium",
                "message": "logic diverged between backend and frontend layers",
            }
        ],
        "overall_result": "fail",
        "confidence": 0.6,
    })
    _install(monkeypatch, lambda model, req: result_success(judge_response))

    body = _analyze(
        app_client,
        project_id=project,
        scope="logic",
        run_id=run["id"],
        judge_model_id=judge_model_id,
        rubric_id=rubric_id,
    )
    assert body["summary"]["judge_derived_count"] == 1
    judged = [f for f in body["findings"] if f["source"] == "judge_derived"]
    assert len(judged) == 1
    finding = judged[0]
    assert finding["drift_type"] == "LOGIC_DRIFT"
    assert finding["provenance"]["judge_model_id"] == judge_model_id
    assert finding["provenance"]["rubric_id"] == rubric_id
    assert finding["provenance"]["judge_evaluation_id"]
    assert finding["provenance"]["judge_run_id"]


# ================================================================== 6
def test_drift_findings_materialize_to_quality_taxonomy(app_client, as_user):
    owner = as_user["id"]
    project = _project(app_client, "pytest-v69-taxonomy")

    payloads = [
        {
            "scope": "architecture",
            "changed_files": [{"path": "app/services/legacy/billing.py"}],
            "contracts": {"canonical_services": [
                {"name": "billing", "paths": ["app/services/billing.py"]},
            ]},
        },
        {
            "scope": "design",
            "changed_files": [{"path": "web/Button.tsx", "hardcoded_colors": ["#ff0000"]}],
            "contracts": {"design_tokens": ["#00ff00"]},
        },
        {
            "scope": "logic",
            "contracts": {"enums": [
                {"name": "Status", "backend_values": ["A"], "frontend_values": ["B"]},
            ]},
        },
        {
            "scope": "prompt",
            "prompt_context": {
                "required_invariant_codes": ["INV-1"],
                "compiled_invariants_used": [],
                "output_text": "done",
            },
        },
    ]

    codes_seen = set()
    for payload in payloads:
        doc = _analyze(app_client, project_id=project, **payload)
        assert doc["findings"], payload["scope"]

        r = app_client.post(f"{API}/drift/analyses/{doc['id']}/findings")
        assert r.status_code == 200, r.text
        created = r.json()["created"]
        assert created, payload["scope"]

        drift_type = created[0]["metadata"]["drift_type"]
        code = drift_service.taxonomy_code_for_drift_type(drift_type)
        assert code is not None
        entry = taxonomy.describe(code)
        assert entry["domain"] == "QUALITY"
        assert entry["severity_scale"] == "quality"

        async def _record(dtype=drift_type, tax=code):
            return await failure_service.create_failure(
                owner,
                project,
                category=dtype,
                source="RULE",
                title=f"drift failure {dtype}",
                taxonomy_code=tax,
            )

        failure = app_client.portal.call(_record)
        assert failure["failure_domain"] == "QUALITY"
        assert failure["taxonomy_code"] == code
        assert failure["severity_scale"] == "quality"
        assert failure["failure_class"] == "quality"
        assert failure["failure_domain"] != "RUNTIME"
        codes_seen.add(code)

    assert codes_seen == set(drift_service.drift_failure_codes())


# ================================================================== 7
def test_materialize_findings_and_regression_promotion(app_client, as_user):
    project = _project(app_client, "pytest-v69-promote")
    doc = _analyze(
        app_client,
        project_id=project,
        scope="architecture",
        changed_files=[{"path": "app/services/legacy/billing.py"}],
        contracts={"canonical_services": [
            {"name": "billing", "paths": ["app/services/billing.py"]},
        ]},
    )

    first = app_client.post(f"{API}/drift/analyses/{doc['id']}/findings")
    assert first.status_code == 200, first.text
    created = first.json()["created"]
    assert len(created) == 1
    finding = created[0]
    assert finding["status"] == "provisional"
    assert "drift" in finding["tags"]
    assert finding["evidence"][0]["type"] == "drift_analysis"
    assert finding["evidence"][0]["ref_id"] == doc["id"]

    # Idempotent: a second materialization creates nothing new.
    second = app_client.post(f"{API}/drift/analyses/{doc['id']}/findings")
    assert second.status_code == 200, second.text
    assert second.json()["created"] == []
    assert len(second.json()["existing"]) == 1

    # The materialized finding resolves its drift_analysis evidence.
    detail = app_client.get(f"{API}/findings/{finding['id']}")
    assert detail.status_code == 200, detail.text
    resolved = {
        (e["type"], e["ref_id"]): e for e in detail.json()["resolved_evidence"]
    }
    assert resolved[("drift_analysis", doc["id"])]["exists"] is True

    # V5 regression promotion works on a materialized drift finding.
    r = app_client.post(f"{API}/findings/{finding['id']}/promote/regression", json={})
    assert r.status_code == 200, r.text
    promoted = r.json()
    assert promoted["promotion_type"] == "regression"
    assert promoted["version"]["status"] == "frozen"
    assert promoted["version"]["content_hash"]


# ================================================================== 8
def test_scorecard_drift_dimension_counts_observed_failures(app_client, as_user):
    owner = as_user["id"]
    project = _project(app_client, "pytest-v69-scorecard")

    async def _seed_drift_run():
        run = _run_doc(owner, project_id=project)
        await repo.runs.insert(run)
        failure = await failure_service.create_failure(
            owner,
            project,
            category="ARCHITECTURE_DRIFT",
            source="RULE",
            title="architecture drift failure",
            taxonomy_code="QUALITY.ARCHITECTURE_DRIFT",
            run_id=run["id"],
        )
        return run, failure

    run, failure = app_client.portal.call(_seed_drift_run)
    card = app_client.portal.call(scorecard_service.build_run_scorecard, owner, run["id"])
    drift_dim = card["dimension_map"]["drift"]
    assert drift_dim["state"] == "available"
    assert drift_dim["value"] == 1.0
    assert drift_dim["unit"] == "count"
    assert drift_dim["deterministic"] is True
    assert any(
        ref["type"] == "failure" and ref["ref_id"] == failure["id"]
        for ref in drift_dim["evidence"]
    )

    # A run with a measured source and zero drift failures is a real 0.0.
    async def _seed_zero_run():
        zero_run = _run_doc(owner, project_id=project)
        await repo.runs.insert(zero_run)
        await repo.evaluations.insert({
            "id": f"pytest-eval-{uuid.uuid4().hex}",
            "owner_id": owner,
            "project_id": project,
            "run_id": zero_run["id"],
            "evaluator_type": "deterministic",
            "eval_type": "deterministic",
            "status": "completed",
            "findings": [],
            "scores": {},
            "result": {"outcome": "PASS", "status": "completed", "passed": True, "score": 1.0},
            "created_at": now_iso(),
        })
        return zero_run

    zero_run = app_client.portal.call(_seed_zero_run)
    zero_card = app_client.portal.call(
        scorecard_service.build_run_scorecard, owner, zero_run["id"]
    )
    zero_dim = zero_card["dimension_map"]["drift"]
    assert zero_dim["state"] == "available"
    assert zero_dim["value"] == 0.0

    # A run with no failures and no evaluations is unknown, never fabricated 0.
    async def _seed_empty_run():
        empty_run = _run_doc(owner, project_id=project)
        await repo.runs.insert(empty_run)
        return empty_run

    empty_run = app_client.portal.call(_seed_empty_run)
    empty_card = app_client.portal.call(
        scorecard_service.build_run_scorecard, owner, empty_run["id"]
    )
    empty_dim = empty_card["dimension_map"]["drift"]
    assert empty_dim["state"] == "not_available"
    assert empty_dim["value"] is None


# ================================================================== 9
def test_ownership_isolation(app_client, as_user):
    project = _project(app_client, "pytest-v69-isolation")
    doc = _analyze(
        app_client,
        project_id=project,
        scope="architecture",
        changed_files=[{"path": "app/services/legacy/billing.py"}],
        contracts={"canonical_services": [
            {"name": "billing", "paths": ["app/services/billing.py"]},
        ]},
    )

    # Unsupported scope is a domain error, not a silent default.
    bad = app_client.post(f"{API}/drift/analyze", json={
        "project_id": project, "scope": "bogus",
    })
    assert bad.status_code == 400, bad.text

    # Missing project is a 404 (owner-scoped).
    missing = app_client.post(f"{API}/drift/analyze", json={
        "project_id": f"pytest-missing-{uuid.uuid4().hex}", "scope": "all",
    })
    assert missing.status_code == 404, missing.text

    foreign = new_owner_id()
    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: {
        "id": foreign,
        "email": f"{foreign}@example.com",
        "name": "Foreign",
        "role": "researcher",
    }
    try:
        assert app_client.get(f"{API}/drift/analyses/{doc['id']}").status_code == 404
        assert app_client.post(
            f"{API}/drift/analyses/{doc['id']}/findings"
        ).status_code == 404
        assert app_client.post(f"{API}/drift/analyze", json={
            "project_id": project, "scope": "all",
        }).status_code == 404
        assert app_client.get(
            f"{API}/drift/analyses", params={"project_id": project}
        ).status_code == 404
    finally:
        app_client.app.dependency_overrides[get_current_user] = original
        app_client.portal.call(purge_owner, foreign)


# ================================================================== 10
def test_analysis_immutability_and_catalog(app_client, as_user):
    owner = as_user["id"]
    project = _project(app_client, "pytest-v69-immutable")
    doc = _analyze(
        app_client,
        project_id=project,
        scope="design",
        changed_files=[{"path": "web/Button.tsx", "hardcoded_colors": ["#ff0000"]}],
        contracts={"design_tokens": ["#00ff00"]},
        notes="pytest note",
    )

    stored = app_client.portal.call(repo.drift_analyses.get, doc["id"], owner)
    assert stored is not None
    fetched = app_client.get(f"{API}/drift/analyses/{doc['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == stored
    assert fetched.json()["schema_version"] == 1
    assert fetched.json()["notes"] == "pytest note"

    catalog = app_client.get(f"{API}/drift/catalog")
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    assert body["scopes"] == ["all", "prompt", "design", "architecture", "logic"]
    assert set(body["drift_types"]) == set(
        ["PROMPT_DRIFT", "DESIGN_DRIFT", "ARCHITECTURE_DRIFT", "LOGIC_DRIFT"]
    )
    assert body["sources"] == ["deterministic", "judge_derived"]
    assert body["checks"]

    listed = app_client.get(f"{API}/drift/analyses", params={"project_id": project})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] >= 1
