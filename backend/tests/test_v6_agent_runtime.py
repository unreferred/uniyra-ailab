"""V6.5 Agent Runtime API tests.

Everything runs offline against scripted adapters and throwaway ``pytest-``
owners purged after each test. Coverage: a frozen agent version resolves into
the canonical ``execute_model`` runtime and snapshots provenance, draft agent
versions are rejected, runtime policy is applied only where honestly supported
and reported otherwise, tool/knowledge/context policies are honored, ownership
isolation holds, the agent trace carries the new span types, and the existing
runtime regression path is unaffected.
"""
from __future__ import annotations

import uuid

from app import repository as repo
from app import services
from app.deps import get_current_user
from app.services import execution as execution_module
from app.services import tracing
from app.services.agent_runtime import resolve_runtime_policy

from _support import (
    ScriptedAdapter,
    create_model,
    create_project,
    create_provider,
    new_id,
    new_owner_id,
    now_iso,
    purge_owner,
    result_success,
)

API = "/api"


# ------------------------------------------------------------------ fake adapter
class RuntimeAdapter(ScriptedAdapter):
    """Scripted adapter that can opt into tool calling / reasoning effort."""

    supports_model_discovery = False
    supports_tools = False
    supports_vision = False
    supports_reasoning_effort = False

    def __init__(self, executor, *, tools=False, reasoning=False):
        super().__init__(executor)
        self.supports_tools = bool(tools)
        self.supports_reasoning_effort = bool(reasoning)


def _install(monkeypatch, executor, *, tools=False, reasoning=False):
    factory = lambda provider: RuntimeAdapter(executor, tools=tools, reasoning=reasoning)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)


# ------------------------------------------------------------------ fixtures helpers
def _project(app_client, name="pytest-v65"):
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


def _agent_version(app_client, agent_id, model_id, **overrides):
    payload = {"model_id": model_id}
    payload.update(overrides)
    return app_client.post(f"{API}/agents/{agent_id}/versions", json=payload)


def _freeze_agent_version(app_client, agent_id, version_id):
    r = app_client.post(f"{API}/agents/{agent_id}/versions/{version_id}/freeze")
    assert r.status_code == 200, r.text
    return r.json()


def _frozen_agent(app_client, owner, project_id, *, model_capabilities=None, **overrides):
    """Model + frozen prompt version + agent + frozen agent version."""
    model_id = _make_model(app_client, owner, capabilities=model_capabilities)
    _, prompt_version_id = _frozen_prompt_version(app_client, project_id)
    agent = _agent(app_client, project_id)
    r = _agent_version(
        app_client, agent["id"], model_id,
        prompt_version_id=prompt_version_id, **overrides,
    )
    assert r.status_code == 200, r.text
    version = _freeze_agent_version(app_client, agent["id"], r.json()["id"])
    return {
        "agent": agent,
        "version": version,
        "model_id": model_id,
        "prompt_version_id": prompt_version_id,
    }


def _run(app_client, project_id, agent, version, task="do the thing"):
    return app_client.post(f"{API}/agent-runtime/run", json={
        "project_id": project_id,
        "agent_id": agent["id"],
        "agent_version_id": version["id"],
        "task": task,
    })


def _seed_tool(app_client, owner, project_id, *, name="pytest-calc"):
    tool_id = new_id()

    async def _seed():
        now = now_iso()
        await repo.tools.insert({
            "id": tool_id,
            "owner_id": owner,
            "project_id": project_id,
            "name": name,
            "display_name": name,
            "tool_type": "function",
            "implementation": "builtin:calculator",
            "enabled": True,
            "scope": "owner",
            "permissions": ["calculator.execute"],
            "created_at": now,
            "updated_at": now,
        })

    app_client.portal.call(_seed)
    return tool_id


def _seed_pack_version(app_client, owner, project_id, *, frozen=True):
    r = app_client.post(f"{API}/context-lab/packs", json={
        "project_id": project_id,
        "name": f"pack-{uuid.uuid4().hex[:8]}",
    })
    assert r.status_code == 200, r.text
    pack_id = r.json()["id"]
    r = app_client.post(f"{API}/context-lab/packs/{pack_id}/versions", json={
        "description": "pytest pack",
        "items": [
            {"key": "rule_a", "title": "Rule A", "source_type": "POLICY",
             "order": 1, "content": "Always be deterministic."},
        ],
    })
    assert r.status_code == 200, r.text
    version_id = r.json()["id"]
    if frozen:
        r = app_client.post(
            f"{API}/context-lab/packs/{pack_id}/versions/{version_id}/freeze"
        )
        assert r.status_code == 200, r.text
    return pack_id, version_id


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
def test_run_success_and_provenance_snapshot(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid)
    _install(monkeypatch, lambda model, req: result_success("agent answer"))

    r = _run(app_client, pid, bundle["agent"], bundle["version"])
    assert r.status_code == 200, r.text
    body = r.json()
    run = body["run"]

    assert run["status"] == "success"
    assert run["execution_kind"] == "agent"
    assert run["context"] == "workspace"
    assert run["agent_id"] == bundle["agent"]["id"]
    assert run["agent_version_id"] == bundle["version"]["id"]
    assert run["prompt_version_id"] == bundle["prompt_version_id"]
    assert run["compiled_prompt_id"] == body["compiled_prompt_id"]
    assert run["compiled_prompt_id"]
    assert run["runtime_policy_applied"]["applied"] == {}
    assert body["output_contract"] == {"format": "text"}

    snapshot = run["agent_version_snapshot"]
    assert snapshot["id"] == bundle["version"]["id"]
    assert snapshot["status"] == "frozen"
    assert snapshot["model_id"] == bundle["model_id"]
    assert snapshot["content_hash"]

    # Trace is agent-typed and carries both new span types.
    trace_id = body["trace_id"]
    assert trace_id
    trace = app_client.portal.call(repo.traces.get, trace_id, owner)
    assert trace is not None and trace["trace_type"] == "agent"

    span_types = [span["span_type"] for span in _spans(app_client, owner, trace_id)]
    assert "AGENT_CONFIGURATION" in span_types
    assert "AGENT_EXECUTION" in span_types

    execution_span = next(
        s for s in _spans(app_client, owner, trace_id)
        if s["span_type"] == "AGENT_EXECUTION"
    )
    assert execution_span["metadata"]["run_id"] == run["id"]
    assert execution_span["metadata"]["status"] == "success"
    assert execution_span["duration_ms"] is not None


# ================================================================== 2
def test_draft_agent_version_rejected(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    model_id = _make_model(app_client, owner)
    _, prompt_version_id = _frozen_prompt_version(app_client, pid)
    agent = _agent(app_client, pid)
    draft = _agent_version(
        app_client, agent["id"], model_id, prompt_version_id=prompt_version_id
    ).json()
    assert draft["status"] == "draft"
    _install(monkeypatch, lambda model, req: result_success("nope"))

    r = _run(app_client, pid, agent, draft)
    assert r.status_code == 400, r.text
    assert "frozen" in r.json()["detail"].lower()


# ================================================================== 3
def test_runtime_policy_applied_and_unsupported(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid, runtime_policy={
        "temperature": 0.3,
        "max_output_tokens": 128,
        "max_cost": 1.5,
        "max_tool_calls": 3,
        "max_runtime_seconds": 30,
    })
    _install(monkeypatch, lambda model, req: result_success("policy answer"))

    r = _run(app_client, pid, bundle["agent"], bundle["version"])
    assert r.status_code == 200, r.text
    run = r.json()["run"]

    assert run["params"]["temperature"] == 0.3
    assert run["params"]["max_tokens"] == 128

    resolution = run["runtime_policy_applied"]
    assert resolution["applied"]["temperature"] == 0.3
    assert resolution["applied"]["max_output_tokens"] == 128
    assert resolution["params"]["max_tokens"] == 128

    unsupported = {entry["field"]: entry["reason"] for entry in resolution["unsupported"]}
    assert unsupported == {
        "max_cost": "not_supported_by_runtime",
        "max_tool_calls": "not_supported_by_runtime",
        "max_runtime_seconds": "not_supported_by_runtime",
    }
    for field in ("max_cost", "max_tool_calls", "max_runtime_seconds"):
        assert field not in resolution["applied"]
        assert field not in run["params"]


# ================================================================== 3b
def test_reasoning_effort_only_when_supported(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)

    # Model declares support AND adapter declares support -> applied.
    supported = _frozen_agent(
        app_client, owner, pid,
        model_capabilities={"reasoning_effort": ["low", "medium", "high"]},
        runtime_policy={"reasoning_effort": "low"},
    )
    _install(monkeypatch, lambda model, req: result_success("reasoned"),
             reasoning=True)
    r = _run(app_client, pid, supported["agent"], supported["version"])
    assert r.status_code == 200, r.text
    resolution = r.json()["run"]["runtime_policy_applied"]
    assert resolution["applied"]["reasoning_effort"] == "low"
    assert resolution["params"]["reasoning_effort"] == "low"

    # Adapter does NOT support it -> honest unsupported, never applied.
    unsupported = _frozen_agent(
        app_client, owner, pid,
        model_capabilities={"reasoning_effort": ["low", "medium", "high"]},
        runtime_policy={"reasoning_effort": "low"},
    )
    _install(monkeypatch, lambda model, req: result_success("reasoned"),
             reasoning=False)
    r = _run(app_client, pid, unsupported["agent"], unsupported["version"])
    assert r.status_code == 200, r.text
    resolution = r.json()["run"]["runtime_policy_applied"]
    assert "reasoning_effort" not in resolution["applied"]
    assert "reasoning_effort" not in resolution["params"]
    assert {"field": "reasoning_effort", "reason": "not_supported_by_model"} in (
        resolution["unsupported"]
    )


# ================================================================== 4
def test_tool_policy_empty_and_enabled(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)

    empty = _frozen_agent(app_client, owner, pid, tool_policy={"allowed_tool_ids": []})
    _install(monkeypatch, lambda model, req: result_success("no tools"))
    r = _run(app_client, pid, empty["agent"], empty["version"])
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["enabled_tool_ids"] == []
    assert run["tool_policy_applied"]["enabled_tool_ids"] == []
    span_types = [s["span_type"] for s in _spans(app_client, owner, r.json()["trace_id"])]
    assert "TOOL_CALL" not in span_types

    tool_id = _seed_tool(app_client, owner, pid)
    enabled = _frozen_agent(
        app_client, owner, pid,
        model_capabilities={"tools": True},
        tool_policy={"allowed_tool_ids": [tool_id], "denied_tool_ids": []},
    )
    _install(monkeypatch, lambda model, req: result_success("with tools"), tools=True)
    r = _run(app_client, pid, enabled["agent"], enabled["version"])
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert tool_id in run["enabled_tool_ids"]
    assert tool_id in run["tool_policy_applied"]["enabled_tool_ids"]


# ================================================================== 5
def test_knowledge_policy_off_and_collection(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)

    off = _frozen_agent(
        app_client, owner, pid,
        knowledge_policy={"knowledge_enabled": False},
    )
    _install(monkeypatch, lambda model, req: result_success("no knowledge"))
    r = _run(app_client, pid, off["agent"], off["version"])
    assert r.status_code == 200, r.text
    assert r.json()["run"]["knowledge_enabled"] is False

    r = app_client.post(f"{API}/knowledge", json={
        "project_id": pid, "name": f"kb-{uuid.uuid4().hex[:8]}",
    })
    assert r.status_code == 200, r.text
    collection_id = r.json()["id"]

    on = _frozen_agent(
        app_client, owner, pid,
        knowledge_policy={
            "knowledge_enabled": True,
            "allowed_collection_ids": [collection_id],
        },
    )
    _install(monkeypatch, lambda model, req: result_success("with knowledge"))
    r = _run(app_client, pid, on["agent"], on["version"])
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["knowledge_enabled"] is True
    assert collection_id in run["knowledge_collection_ids"]
    assert collection_id in run["knowledge_policy_applied"]["allowed_collection_ids"]


# ================================================================== 6
def test_context_packs_frozen_and_draft(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    pack_id, frozen_version_id = _seed_pack_version(app_client, owner, pid, frozen=True)

    bundle = _frozen_agent(
        app_client, owner, pid,
        context_policy={"context_pack_version_ids": [frozen_version_id]},
    )
    _install(monkeypatch, lambda model, req: result_success("packed"))
    r = _run(app_client, pid, bundle["agent"], bundle["version"])
    assert r.status_code == 200, r.text
    applied = r.json()["run"]["context_policy_applied"]
    assert frozen_version_id in applied["context_pack_version_ids"]
    assert any(
        ref["pack_version_id"] == frozen_version_id for ref in applied["context_packs"]
    )

    # A draft pack version is not a reproducible input -> 400.
    model_id = _make_model(app_client, owner)
    _, prompt_version_id = _frozen_prompt_version(app_client, pid)
    agent = _agent(app_client, pid)
    _, draft_version_id = _seed_pack_version(app_client, owner, pid, frozen=False)
    draft_agent_version = _agent_version(
        app_client, agent["id"], model_id,
        prompt_version_id=prompt_version_id,
        context_policy={"context_pack_version_ids": [draft_version_id]},
    ).json()
    _freeze_agent_version(app_client, agent["id"], draft_agent_version["id"])

    r = _run(app_client, pid, agent, draft_agent_version)
    assert r.status_code == 400, r.text
    assert "frozen" in r.json()["detail"].lower()


# ================================================================== 7
def test_ownership_isolation(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid)
    _install(monkeypatch, lambda model, req: result_success("isolation"))

    # Foreign/unknown agent -> 404.
    unknown = app_client.post(f"{API}/agent-runtime/run", json={
        "project_id": pid,
        "agent_id": new_id(),
        "agent_version_id": bundle["version"]["id"],
        "task": "x",
    })
    assert unknown.status_code == 404, unknown.text

    # A version id that does not belong to the requested agent -> 404.
    other_agent = _agent(app_client, pid)
    mismatch = app_client.post(f"{API}/agent-runtime/run", json={
        "project_id": pid,
        "agent_id": other_agent["id"],
        "agent_version_id": bundle["version"]["id"],
        "task": "x",
    })
    assert mismatch.status_code == 404, mismatch.text

    # Unowned/foreign project -> 400.
    foreign_user_id = new_owner_id()
    foreign_project = app_client.portal.call(create_project, foreign_user_id)
    try:
        foreign_pid = app_client.post(f"{API}/agent-runtime/run", json={
            "project_id": foreign_project,
            "agent_id": bundle["agent"]["id"],
            "agent_version_id": bundle["version"]["id"],
            "task": "x",
        })
        assert foreign_pid.status_code == 400, foreign_pid.text
    finally:
        app_client.portal.call(purge_owner, foreign_user_id)

    # A foreign user cannot see or run this owner's agent.
    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: _foreign_user()
    try:
        r = app_client.post(f"{API}/agent-runtime/run", json={
            "project_id": pid,
            "agent_id": bundle["agent"]["id"],
            "agent_version_id": bundle["version"]["id"],
            "task": "x",
        })
        assert r.status_code == 404, r.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = original

    # Foreign prompt version bound to an otherwise-owned agent version -> 400.
    model_id = _make_model(app_client, owner)
    agent = _agent(app_client, pid)
    foreign_pv_id = new_id()
    version_id = new_id()

    async def _seed_foreign_prompt():
        now = now_iso()
        await repo.agent_versions.insert({
            "id": version_id,
            "owner_id": owner,
            "project_id": pid,
            "agent_id": agent["id"],
            "version": 1,
            "status": "frozen",
            "model_id": model_id,
            "prompt_version_id": foreign_pv_id,
            "context_policy": {}, "tool_policy": {}, "knowledge_policy": {},
            "runtime_policy": {}, "output_contract": {"format": "text"},
            "evaluation_profile": {}, "routing": {"mode": "fixed"},
            "content_hash": "x", "metadata": {}, "created_at": now,
            "updated_at": now, "frozen_at": now, "deprecated_at": None,
        })

    app_client.portal.call(_seed_foreign_prompt)
    r = app_client.post(f"{API}/agent-runtime/run", json={
        "project_id": pid,
        "agent_id": agent["id"],
        "agent_version_id": version_id,
        "task": "x",
    })
    assert r.status_code == 400, r.text


# ================================================================== 8
def test_existing_execution_runtime_unaffected(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    model_id = _make_model(app_client, owner)
    _install(monkeypatch, lambda model, req: result_success("legacy answer"))

    async def _run_direct():
        return await execution_module.execute_model(
            owner,
            model_id,
            "system",
            [{"role": "user", "content": "hello"}],
            {"temperature": 0.1},
            project_id=pid,
        )

    run = app_client.portal.call(_run_direct)
    assert run["status"] == "success"
    assert run["execution_kind"] == "workspace"
    assert run["normalized_response"] == "legacy answer"


# ================================================================== 9
def test_list_and_get_agent_runs(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _project(app_client)
    bundle = _frozen_agent(app_client, owner, pid, runtime_policy={"temperature": 0.2})
    _install(monkeypatch, lambda model, req: result_success("listed"))

    created = _run(app_client, pid, bundle["agent"], bundle["version"]).json()
    run_id = created["run_id"]

    listed = app_client.get(
        f"{API}/agent-runtime/runs",
        params={"agent_version_id": bundle["version"]["id"]},
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] >= 1
    assert body["items"][0]["id"] == run_id
    assert body["items"][0]["agent_version_id"] == bundle["version"]["id"]

    detail = app_client.get(f"{API}/agent-runtime/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["run_id"] == run_id
    assert body["compiled_prompt"]["id"] == created["compiled_prompt_id"]
    assert body["runtime_policy_applied"]["applied"]["temperature"] == 0.2
    assert body["trace"]["id"] == created["trace_id"]


# ================================================================== 10
def test_resolve_runtime_policy_reports_truthfully():
    # Pure-helper checks independent of the HTTP surface.
    resolution = resolve_runtime_policy(
        {"max_iterations": 4, "max_input_tokens": 2048},
        model={}, enabled_tool_ids=[], requested_max_context_tokens=None,
    )
    assert resolution["applied"]["max_tool_rounds"] == 4
    assert resolution["applied"]["max_context_tokens"] == 2048
    assert resolution["params"] == {}
    assert resolution["unsupported"] == []

    fallback = resolve_runtime_policy(
        {}, model={}, enabled_tool_ids=[], requested_max_context_tokens=999,
    )
    assert fallback["applied"]["max_context_tokens"] == 999

    absent = resolve_runtime_policy(
        {}, model={}, enabled_tool_ids=[], requested_max_context_tokens=None,
    )
    assert absent == {"applied": {}, "unsupported": [], "params": {}}
