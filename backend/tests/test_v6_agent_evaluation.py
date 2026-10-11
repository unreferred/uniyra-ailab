"""V6.7 Agent & Prompt Evaluation tests.

Everything runs offline against scripted adapters and throwaway ``pytest-``
owners purged after each test. The suite proves the V6.7 surface REUSES the V5
evaluation framework: canonical evaluation documents (``schema_version`` /
``evaluator_version``), the same evaluators module, and the same tools-disabled
judge. Deterministic, judge-derived and missing-telemetry outcomes stay
separated; ``NOT_EVALUATED`` is never promoted to a PASS.
"""
from __future__ import annotations

import json
import uuid

from app import repository as repo
from app import services
from app.deps import get_current_user
from app.models import EVALUATOR_VERSION, V5_SCHEMA_VERSION, now_iso
from app.services import execution as execution_module
from app.services import failure_taxonomy as ft
from app.services import tracing

from _support import (
    ScriptedAdapter,
    create_model,
    create_project,
    create_provider,
    new_id,
    new_owner_id,
    purge_owner,
    result_success,
)

API = "/api"


# ------------------------------------------------------------------ fake adapter
class RuntimeAdapter(ScriptedAdapter):
    supports_model_discovery = False
    supports_tools = False
    supports_vision = False

    def __init__(self, executor, *, tools=False):
        super().__init__(executor)
        self.supports_tools = bool(tools)


def _install(monkeypatch, executor, *, tools=False):
    factory = lambda provider: RuntimeAdapter(executor, tools=tools)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)


# ------------------------------------------------------------------ helpers
def _project(app_client, name="pytest-v67"):
    r = app_client.post(
        f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_model(app_client, owner, *, capabilities=None):
    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(
            owner,
            provider_id=provider_id,
            display_name=f"pytest-model-{uuid.uuid4().hex[:6]}",
            model_identifier=f"pytest/{uuid.uuid4().hex}",
            capabilities=capabilities or {},
        )

    return app_client.portal.call(_setup)


def _frozen_prompt_version(app_client, project_id, content="Answer the task."):
    r = app_client.post(f"{API}/prompts", json={
        "project_id": project_id,
        "name": f"prompt-{uuid.uuid4().hex[:8]}",
        "status": "draft",
    })
    assert r.status_code == 200, r.text
    prompt_id = r.json()["id"]
    r = app_client.post(f"{API}/prompts/{prompt_id}/versions", json={"content": content})
    assert r.status_code == 200, r.text
    version_id = r.json()["id"]
    r = app_client.post(f"{API}/prompts/{prompt_id}/versions/{version_id}/freeze")
    assert r.status_code == 200, r.text
    return prompt_id, version_id


def _agent(app_client, project_id):
    r = app_client.post(f"{API}/agents", json={
        "project_id": project_id,
        "name": f"agent-{uuid.uuid4().hex[:8]}",
    })
    assert r.status_code == 200, r.text
    return r.json()


def _frozen_agent(app_client, owner, project_id, *, model_capabilities=None, **overrides):
    model_id = _make_model(app_client, owner, capabilities=model_capabilities)
    _, prompt_version_id = _frozen_prompt_version(app_client, project_id)
    agent = _agent(app_client, project_id)
    payload = {"model_id": model_id, "prompt_version_id": prompt_version_id}
    payload.update(overrides)
    r = app_client.post(f"{API}/agents/{agent['id']}/versions", json=payload)
    assert r.status_code == 200, r.text
    version_id = r.json()["id"]
    r = app_client.post(f"{API}/agents/{agent['id']}/versions/{version_id}/freeze")
    assert r.status_code == 200, r.text
    return {
        "agent": agent,
        "version": r.json(),
        "model_id": model_id,
        "prompt_version_id": prompt_version_id,
    }


def _direct_frozen_agent(app_client, owner, project_id, *, model_id, prompt_version_id, **policies):
    """Insert a frozen agent version directly (registry binding not enforced)."""
    agent = _agent(app_client, project_id)
    version_id = new_id()
    version = {
        "id": version_id,
        "owner_id": owner,
        "project_id": project_id,
        "agent_id": agent["id"],
        "version": 1,
        "status": "frozen",
        "model_id": model_id,
        "prompt_version_id": prompt_version_id,
        "context_policy": policies.get("context_policy") or {},
        "tool_policy": policies.get("tool_policy") or {},
        "knowledge_policy": policies.get("knowledge_policy") or {},
        "runtime_policy": policies.get("runtime_policy") or {},
        "output_contract": {"format": "text"},
        "evaluation_profile": policies.get("evaluation_profile") or {},
        "routing": {"mode": "fixed"},
        "content_hash": "direct-seed",
        "metadata": policies.get("metadata") or {},
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "frozen_at": now_iso(),
        "deprecated_at": None,
    }

    async def _seed():
        await repo.agent_versions.insert(version)

    app_client.portal.call(_seed)
    return {"agent": agent, "version": version, "model_id": model_id,
            "prompt_version_id": prompt_version_id}


def _run(app_client, project_id, agent, version, task="do the thing"):
    return app_client.post(f"{API}/agent-runtime/run", json={
        "project_id": project_id,
        "agent_id": agent["id"],
        "agent_version_id": version["id"],
        "task": task,
    })


def _seed_tool(app_client, owner, project_id, *, name=None):
    tool_id = new_id()
    tool_name = name or f"pytest-tool-{uuid.uuid4().hex[:8]}"

    async def _seed():
        now = now_iso()
        await repo.tools.insert({
            "id": tool_id,
            "owner_id": owner,
            "project_id": project_id,
            "name": tool_name,
            "display_name": tool_name,
            "tool_type": "function",
            "implementation": "builtin:calculator",
            "enabled": True,
            "scope": "owner",
            "permissions": ["calculator.execute"],
            "created_at": now,
            "updated_at": now,
        })

    app_client.portal.call(_seed)
    return tool_id, tool_name


def _seed_tool_call(app_client, owner, run_id, tool_name):
    async def _seed():
        await repo.tool_calls.insert({
            "id": new_id(),
            "owner_id": owner,
            "run_id": run_id,
            "tool_name": tool_name,
            "execution_status": "success",
            "error": None,
            "duration_ms": 1,
            "created_at": now_iso(),
        })

    app_client.portal.call(_seed)


def _update_run(app_client, owner, run_id, updates):
    return app_client.portal.call(repo.runs.update, run_id, owner, updates)


def _seed_compiled_prompt(app_client, owner, project_id, *, invariants):
    prompt_id = new_id()

    async def _seed():
        await repo.compiled_prompts.insert({
            "id": prompt_id,
            "owner_id": owner,
            "project_id": project_id,
            "target": "generic_chat",
            "checksum": "seed-checksum",
            "invariants_used": list(invariants),
            "policies_used": [],
            "blocks_used": [],
            "token_estimate": 10,
            "created_at": now_iso(),
        })

    app_client.portal.call(_seed)
    return prompt_id


def _seed_context_snapshot(app_client, owner, run_id, *, over_budget, excluded=None):
    """Set the run's context snapshot fields (the collection is unique per run)."""
    fields = {
        "over_budget": over_budget,
        "strategy": "FULL",
        "components": [{"component_type": "SYSTEM", "included": True}],
        "excluded_components": list(excluded or []),
        "budget_policy_applied": None,
        "estimated_total_tokens": 9999 if over_budget else 10,
    }

    async def _seed():
        existing = await repo.context_snapshots.find_one(
            {"owner_id": owner, "run_id": run_id}
        )
        if existing:
            snapshot_id = existing["id"]
            await repo.context_snapshots.update(snapshot_id, owner, dict(fields))
        else:
            snapshot_id = new_id()
            await repo.context_snapshots.insert({
                "id": snapshot_id,
                "owner_id": owner,
                "run_id": run_id,
                "created_at": now_iso(),
                **fields,
            })
        return snapshot_id

    return app_client.portal.call(_seed)


def _evaluate(app_client, run_id, **body):
    r = app_client.post(f"{API}/agent-evaluation/runs/{run_id}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _spans(app_client, owner, trace_id):
    return app_client.portal.call(tracing.list_spans, owner, trace_id)


def _foreign_user():
    return {
        "id": f"pytest-foreign-{uuid.uuid4().hex}",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }


# ================================================================== 1
def test_agent_policy_compliance_detects_permission_denial(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    other_pid = _project(app_client, name="pytest-v67-other")
    _tool_id, _tool_name = _seed_tool(app_client, owner, other_pid, name="pytest-foreign-tool")

    model_id = _make_model(app_client, owner, capabilities={"tools": True})
    _, prompt_version_id = _frozen_prompt_version(app_client, pid)
    bundle = _direct_frozen_agent(
        app_client, owner, pid,
        model_id=model_id, prompt_version_id=prompt_version_id,
        tool_policy={"allowed_tool_ids": [_tool_id]},
    )
    _install(monkeypatch, lambda model, req: result_success("policy answer"), tools=True)

    run = _run(app_client, pid, bundle["agent"], bundle["version"]).json()["run"]
    assert "tool_foreign_project" in [
        denial.get("kind") for denial in run.get("permission_denials") or []
    ]

    body = _evaluate(
        app_client, run["id"],
        evaluator_keys=["agent_policy_compliance"], record_failures=True,
    )
    rule = body["rule_evaluation"]
    assert rule["result"]["outcome"] == "FAIL"
    assert any(f["category"] == "policy_compliance" for f in rule["findings"])

    async def _listed():
        return await repo.failures.list(
            {"owner_id": owner, "evaluation_id": rule["id"]}, limit=10
        )

    stored = app_client.portal.call(_listed)
    assert stored
    assert all(item["failure_domain"] == "QUALITY" for item in stored)
    assert all(item["failure_domain"] not in ("RUNTIME", "SECURITY") for item in stored)

    # A clean agent run (no denials) passes the same evaluator.
    clean = _frozen_agent(app_client, owner, pid)
    _install(monkeypatch, lambda model, req: result_success("clean answer"))
    clean_run = _run(app_client, pid, clean["agent"], clean["version"]).json()["run"]
    assert clean_run.get("permission_denials") == []
    clean_body = _evaluate(
        app_client, clean_run["id"], evaluator_keys=["agent_policy_compliance"]
    )
    assert clean_body["rule_evaluation"]["result"]["outcome"] == "PASS"


# ================================================================== 2
def test_agent_tool_discipline(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)

    # Empty allowlist + observed tool calls -> FAIL.
    empty = _frozen_agent(
        app_client, owner, pid,
        model_capabilities={"tools": True},
        tool_policy={"allowed_tool_ids": []},
    )
    _install(monkeypatch, lambda model, req: result_success("no tools"))
    run = _run(app_client, pid, empty["agent"], empty["version"]).json()["run"]
    _update_run(app_client, owner, run["id"], {"tool_call_count": 2})
    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_tool_discipline"])
    assert body["rule_evaluation"]["result"]["outcome"] == "FAIL"
    assert any(
        f["message"] == "tools_used_with_empty_allowlist"
        for f in body["rule_evaluation"]["findings"]
    )

    # A permitted tool actually used -> PASS.
    tool_id, tool_name = _seed_tool(app_client, owner, pid, name="pytest-allowed-tool")
    allowed = _frozen_agent(
        app_client, owner, pid,
        model_capabilities={"tools": True},
        tool_policy={"allowed_tool_ids": [tool_id]},
    )
    _install(monkeypatch, lambda model, req: result_success("with tools"), tools=True)
    run = _run(app_client, pid, allowed["agent"], allowed["version"]).json()["run"]
    _seed_tool_call(app_client, owner, run["id"], tool_name)
    _update_run(app_client, owner, run["id"], {"tool_call_count": 1})
    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_tool_discipline"])
    assert body["rule_evaluation"]["result"]["outcome"] == "PASS"


# ================================================================== 3
def test_agent_invariant_compliance(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid)
    _install(monkeypatch, lambda model, req: result_success("invariant answer"))
    run = _run(app_client, pid, bundle["agent"], bundle["version"]).json()["run"]

    run_doc = app_client.portal.call(repo.runs.get, run["id"], owner)
    snapshot = dict(run_doc.get("agent_version_snapshot") or {})
    snapshot["metadata"] = {"required_invariant_codes": ["INV_A"]}
    present_id = _seed_compiled_prompt(
        app_client, owner, pid, invariants=[{"code": "INV_A"}]
    )
    missing_id = _seed_compiled_prompt(app_client, owner, pid, invariants=[])
    _update_run(app_client, owner, run["id"], {
        "agent_version_snapshot": snapshot, "compiled_prompt_id": present_id,
    })
    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_invariant_compliance"])
    assert body["rule_evaluation"]["result"]["outcome"] == "PASS"

    _update_run(app_client, owner, run["id"], {"compiled_prompt_id": missing_id})
    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_invariant_compliance"])
    assert body["rule_evaluation"]["result"]["outcome"] == "FAIL"
    assert any(
        f["message"] == "invariant_missing:INV_A"
        for f in body["rule_evaluation"]["findings"]
    )

    # No required codes -> NOT_EVALUATED (never a fabricated pass).
    snapshot["metadata"] = {}
    _update_run(app_client, owner, run["id"], {"agent_version_snapshot": snapshot})
    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_invariant_compliance"])
    assert body["rule_evaluation"]["result"]["outcome"] == "NOT_EVALUATED"
    assert body["rule_evaluation"]["result"]["passed"] is None
    assert body["rule_evaluation"]["raw_result"]["evaluators"][0]["reason"] == (
        "no_required_invariants"
    )


# ================================================================== 4
def test_agent_context_usage(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid)
    _install(monkeypatch, lambda model, req: result_success("context answer"))
    run = _run(app_client, pid, bundle["agent"], bundle["version"]).json()["run"]

    # Missing snapshot -> NOT_EVALUATED.
    _update_run(app_client, owner, run["id"], {"context_snapshot_id": None})
    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_context_usage"])
    assert body["rule_evaluation"]["result"]["outcome"] == "NOT_EVALUATED"
    assert body["rule_evaluation"]["raw_result"]["evaluators"][0]["reason"] == (
        "context_snapshot_unavailable"
    )

    # Over-budget snapshot -> FAIL.
    snapshot_id = _seed_context_snapshot(app_client, owner, run["id"], over_budget=True)
    _update_run(app_client, owner, run["id"], {"context_snapshot_id": snapshot_id})
    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_context_usage"])
    assert body["rule_evaluation"]["result"]["outcome"] == "FAIL"
    assert any(
        f["message"] == "context_over_budget"
        for f in body["rule_evaluation"]["findings"]
    )

    # Excluded mandatory context -> FAIL.
    snapshot_id = _seed_context_snapshot(
        app_client, owner, run["id"], over_budget=False,
        excluded=[{"component_type": "POLICY", "excluded_reason": "budget"}],
    )
    _update_run(app_client, owner, run["id"], {"context_snapshot_id": snapshot_id})
    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_context_usage"])
    assert any(
        f["message"] == "mandatory_context_excluded"
        for f in body["rule_evaluation"]["findings"]
    )


# ================================================================== 5
def test_judge_derived_path_tools_disabled(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid)
    judge_model_id = _make_model(app_client, owner)

    r = app_client.post(f"{API}/rubrics", json={
        "name": f"pytest-agent-rubric-{uuid.uuid4().hex[:6]}",
        "dimensions": [
            {"key": "role_adherence", "label": "Role", "max_score": 10, "weight": 1.0},
            {"key": "scope_adherence", "label": "Scope", "max_score": 10, "weight": 1.0},
        ],
        "threshold": 0.5,
    })
    assert r.status_code == 200, r.text
    rubric_id = r.json()["id"]

    async def _judge_ident():
        doc = await repo.models.get(judge_model_id, owner)
        return doc["model_identifier"]

    judge_ident = app_client.portal.call(_judge_ident)

    def executor(model, req):
        if model.get("model_identifier") == judge_ident:
            return result_success(json.dumps({
                "dimensions": {
                    "role_adherence": {"score": 9, "rationale": "in role"},
                    "scope_adherence": {"score": 8, "rationale": "in scope"},
                },
                "findings": [],
                "overall_result": "pass",
                "confidence": 0.9,
            }))
        return result_success("agent answer")

    _install(monkeypatch, executor)
    run = _run(app_client, pid, bundle["agent"], bundle["version"]).json()["run"]

    body = _evaluate(
        app_client, run["id"],
        evaluator_keys=["agent_policy_compliance"],
        judge_model_id=judge_model_id, rubric_id=rubric_id,
    )
    judge_eval = body["judge_evaluation"]
    assert judge_eval is not None
    assert judge_eval["evaluator_type"] == "judge_model"
    assert judge_eval["eval_type"] == "ai_judge"
    assert judge_eval["provenance"]["evaluator_type"] == "judge_model"
    assert set(judge_eval["scores"]) == {"role_adherence", "scope_adherence"}
    assert judge_eval["judge"]["tools_enabled"] is False

    judge_run = app_client.portal.call(repo.runs.get, judge_eval["judge_run_id"], owner)
    assert judge_run["enabled_tool_ids"] == []
    assert (judge_run.get("max_tool_rounds") or 0) == 0


# ================================================================== 6
def test_evaluation_provenance_and_evidence(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid)
    _install(monkeypatch, lambda model, req: result_success("prov answer"))
    run = _run(app_client, pid, bundle["agent"], bundle["version"]).json()["run"]

    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_policy_compliance"])
    doc = body["rule_evaluation"]
    assert doc["agent"]["agent_version_id"] == bundle["version"]["id"]
    assert doc["agent"]["compiled_prompt_id"] == run["compiled_prompt_id"]
    assert doc["provenance"]["agent_id"] == bundle["agent"]["id"]
    assert doc["provenance"]["agent_version_id"] == bundle["version"]["id"]
    assert doc["provenance"]["compiled_prompt_id"] == run["compiled_prompt_id"]
    assert doc["provenance"]["context_snapshot_id"] == run["context_snapshot_id"]
    assert doc["evidence"]["agent_version_id"] == bundle["version"]["id"]
    assert doc["evidence"]["compiled_prompt_id"] == run["compiled_prompt_id"]
    assert doc["evidence"]["permission_denial_count"] == 0

    listed = app_client.get(f"{API}/agent-evaluation/runs/{run['id']}")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] >= 1


# ================================================================== 7
def test_agent_findings_map_to_quality_domain():
    mapping = {
        "policy_compliance": "QUALITY.POLICY_COMPLIANCE",
        "invariant_compliance": "QUALITY.INVARIANT_COMPLIANCE",
        "context_usage": "QUALITY.CONTEXT_USAGE",
        "task_completion": "QUALITY.TASK_COMPLETION",
        "scope_adherence": "QUALITY.SCOPE_ADHERENCE",
        "role_adherence": "QUALITY.ROLE_ADHERENCE",
        "drift": "QUALITY.DRIFT",
    }
    for category, code in mapping.items():
        entry = ft.classify_finding(category, message=f"agent_{category}_issue")
        assert entry["code"] == code, category
        assert entry["domain"] == "QUALITY", category
        assert entry["severity_scale"] == "quality", category
    # Security signals remain their own distinct domain.
    assert ft.classify_finding("security", message="tool access denied")["domain"] == "SECURITY"


# ================================================================== 8
def test_missing_telemetry_stays_not_evaluated(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid)
    _install(monkeypatch, lambda model, req: result_success("telemetry answer"))
    run = _run(app_client, pid, bundle["agent"], bundle["version"]).json()["run"]

    # No trace + no tool telemetry -> NOT_EVALUATED, never a fabricated PASS.
    _update_run(app_client, owner, run["id"], {"trace_id": None, "tool_call_count": None})
    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_tool_discipline"])
    assert body["rule_evaluation"]["result"]["outcome"] == "NOT_EVALUATED"
    assert body["rule_evaluation"]["result"]["passed"] is None
    assert body["rule_evaluation"]["raw_result"]["evaluators"][0]["reason"] == (
        "tool_telemetry_unavailable"
    )

    # No agent provenance -> NOT_EVALUATED.
    other = _project(app_client, name="pytest-v67-noprov")
    plain_run = {
        "id": f"pytest-run-{uuid.uuid4().hex}",
        "owner_id": owner,
        "project_id": other,
        "status": "success",
        "normalized_response": "plain",
        "created_at": now_iso(),
    }
    app_client.portal.call(repo.runs.insert, plain_run)
    r = app_client.post(f"{API}/agent-evaluation/runs/{plain_run['id']}", json={})
    assert r.status_code == 400, r.text


# ================================================================== 9
def test_ownership_isolation(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid)
    _install(monkeypatch, lambda model, req: result_success("iso answer"))
    run = _run(app_client, pid, bundle["agent"], bundle["version"]).json()["run"]

    assert app_client.post(
        f"{API}/agent-evaluation/runs/{new_id()}", json={}
    ).status_code == 404
    assert app_client.get(
        f"{API}/agent-evaluation/runs/{new_id()}"
    ).status_code == 404

    # A foreign user cannot see or evaluate this owner's run.
    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: _foreign_user()
    try:
        assert app_client.post(
            f"{API}/agent-evaluation/runs/{run['id']}", json={}
        ).status_code == 404
        assert app_client.get(
            f"{API}/agent-evaluation/runs/{run['id']}"
        ).status_code == 404
    finally:
        app_client.app.dependency_overrides[get_current_user] = original

    foreign_owner = new_owner_id()
    try:
        foreign_pid = app_client.portal.call(create_project, foreign_owner)
        foreign_run = {
            "id": f"pytest-run-{uuid.uuid4().hex}",
            "owner_id": foreign_owner,
            "project_id": foreign_pid,
            "agent_id": bundle["agent"]["id"],
            "agent_version_id": bundle["version"]["id"],
            "status": "success",
            "normalized_response": "foreign",
            "created_at": now_iso(),
        }
        app_client.portal.call(repo.runs.insert, foreign_run)
        assert app_client.post(
            f"{API}/agent-evaluation/runs/{foreign_run['id']}", json={}
        ).status_code == 404
        assert app_client.get(
            f"{API}/agent-evaluation/runs/{foreign_run['id']}"
        ).status_code == 404
    finally:
        app_client.portal.call(purge_owner, foreign_owner)


# ================================================================== 10
def test_reuses_v5_evaluation_schema(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid)
    _install(monkeypatch, lambda model, req: result_success("schema answer"))
    run = _run(app_client, pid, bundle["agent"], bundle["version"]).json()["run"]

    body = _evaluate(app_client, run["id"], evaluator_keys=["agent_policy_compliance"])
    doc = body["rule_evaluation"]
    assert doc["schema_version"] == V5_SCHEMA_VERSION
    assert doc["provenance"]["evaluator_version"] == EVALUATOR_VERSION
    assert doc["evaluator"]["implementation"] == "app.services.evaluators"

    stored = app_client.portal.call(repo.evaluations.get, doc["id"], owner)
    assert stored is not None
    assert stored["schema_version"] == V5_SCHEMA_VERSION
    assert stored["provenance"]["evaluator_version"] == EVALUATOR_VERSION

    # The catalog exposes the agent/prompt vocabulary.
    catalog = app_client.get(f"{API}/agent-evaluation/catalog")
    assert catalog.status_code == 200, catalog.text
    data = catalog.json()
    assert "agent_policy_compliance" in data["agent_rule_keys"]
    assert "role_adherence" in data["agent_dimensions"]
