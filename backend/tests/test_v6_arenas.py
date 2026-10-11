"""V6.8 Agent Arena & Prompt Arena API tests.

Everything runs offline against scripted adapters and throwaway ``pytest-``
owners purged by ``as_user``. Coverage: arena creation over frozen agent/prompt
versions, dispatch through the EXISTING benchmark runner + agent runtime, truthful
per-configuration metrics (unknown stays None), fixed model binding (no adaptive
routing), draft/foreign validation, and the untouched legacy benchmark path.
"""
from __future__ import annotations

import uuid

from app import repository as repo
from app import services
from app.deps import get_current_user
from app.services import benchmark as benchmark_service
from app.services import execution as execution_module

from _support import (
    adapter_factory,
    create_model,
    create_project,
    create_provider,
    new_owner_id,
    purge_owner,
    result_success,
)

API = "/api"


# ------------------------------------------------------------------ helpers
def _install(monkeypatch, executor, streamer=None):
    factory = adapter_factory(executor, streamer)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)


def _make_project(client, name="pytest-v68"):
    r = client.post(f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_models(client, owner, count, pricing=False):
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
        r = client.post(
            f"{API}/datasets/{dataset_id}/versions/{version['id']}/cases", json=payload
        )
        assert r.status_code == 200, r.text
    r = client.post(f"{API}/datasets/{dataset_id}/versions/{version['id']}/freeze")
    assert r.status_code == 200, r.text
    return dataset_id, version["id"]


def _prompt_version(client, project_id, content="Answer the task.", freeze=True):
    r = client.post(f"{API}/prompts", json={
        "project_id": project_id,
        "name": f"prompt-{uuid.uuid4().hex[:8]}",
        "status": "draft",
    })
    assert r.status_code == 200, r.text
    prompt_id = r.json()["id"]
    r = client.post(f"{API}/prompts/{prompt_id}/versions", json={"content": content})
    assert r.status_code == 200, r.text
    version_id = r.json()["id"]
    if freeze:
        r = client.post(f"{API}/prompts/{prompt_id}/versions/{version_id}/freeze")
        assert r.status_code == 200, r.text
    return prompt_id, version_id


def _frozen_agent(client, project_id, model_id, name):
    _, prompt_version_id = _prompt_version(client, project_id)
    r = client.post(f"{API}/agents", json={"project_id": project_id, "name": name})
    assert r.status_code == 200, r.text
    agent = r.json()
    r = client.post(
        f"{API}/agents/{agent['id']}/versions",
        json={"model_id": model_id, "prompt_version_id": prompt_version_id},
    )
    assert r.status_code == 200, r.text
    version_id = r.json()["id"]
    r = client.post(f"{API}/agents/{agent['id']}/versions/{version_id}/freeze")
    assert r.status_code == 200, r.text
    return r.json()


def _draft_agent_version(client, project_id, model_id):
    _, prompt_version_id = _prompt_version(client, project_id)
    r = client.post(f"{API}/agents", json={
        "project_id": project_id, "name": f"agent-{uuid.uuid4().hex[:8]}",
    })
    assert r.status_code == 200, r.text
    agent = r.json()
    r = client.post(
        f"{API}/agents/{agent['id']}/versions",
        json={"model_id": model_id, "prompt_version_id": prompt_version_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _run_benchmark(client, owner, experiment_id):
    r = client.post(f"{API}/experiments/{experiment_id}/benchmark/plan")
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]

    async def _run():
        await benchmark_service.run_benchmark(plan_id, owner)

    client.portal.call(_run)
    return plan_id


def _runs(client, owner, experiment_id):
    async def _load():
        return await repo.runs.list(
            {
                "owner_id": owner,
                "experiment_id": experiment_id,
                "execution_kind": "benchmark",
            },
            sort_field="created_at", descending=False, limit=1000,
        )

    return client.portal.call(_load)


def _foreign_user():
    return {
        "id": f"pytest-foreign-{uuid.uuid4().hex}",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }


def _unknown_adapter():
    return lambda model, req: result_success(
        "answer", input_tokens=None, output_tokens=None, latency_ms=None
    )


# ================================================================== 1
def test_agent_arena_create_run_and_summary(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _make_project(app_client)
    models = _make_models(app_client, owner, 2)
    dataset_id, version_id = _make_dataset(app_client, pid, [
        {"input": "question", "expected": "answer"},
    ])
    first = _frozen_agent(app_client, pid, models[0], "alpha")
    second = _frozen_agent(app_client, pid, models[1], "beta")

    r = app_client.post(f"{API}/arenas/agent", json={
        "project_id": pid,
        "title": "pytest-agent-arena",
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "agent_version_ids": [first["id"], second["id"]],
    })
    assert r.status_code == 200, r.text
    exp = r.json()
    assert exp["experiment_type"] == "agent_arena"
    assert exp["status"] == "READY"
    configs = exp["configurations"]
    assert len(configs) == 2
    by_agent = {c["agent_version_id"]: c for c in configs}
    assert set(by_agent) == {first["id"], second["id"]}
    # Fixed model binding: agent arena uses each version's bound model.
    assert by_agent[first["id"]]["model_id"] == models[0]
    assert by_agent[second["id"]]["model_id"] == models[1]

    _install(monkeypatch, _unknown_adapter())
    _run_benchmark(app_client, owner, exp["id"])

    runs = _runs(app_client, owner, exp["id"])
    assert len(runs) == 2
    assert {run["agent_version_id"] for run in runs} == {first["id"], second["id"]}
    for run in runs:
        assert run["status"] == "success"
        assert run["execution_kind"] == "benchmark"
        assert run["compiled_prompt_id"]
        assert run["attempt_index"] == 0

    summary = app_client.get(f"{API}/arenas/{exp['id']}")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["experiment_type"] == "agent_arena"
    assert body["experiment"]["experiment_type"] == "agent_arena"
    assert body["total_planned_calls"] == 2
    assert len(body["configurations"]) == 2
    for config in body["configurations"]:
        # Metrics honesty: a run exists, no evaluation, unknown telemetry stays None.
        assert config["run_count"] == 1
        assert config["completed"] == 1
        assert config["failed"] == 0
        assert config["avg_score"] is None
        assert config["latency_ms"]["avg"] is None
        assert config["input_tokens"] is None
        assert config["output_tokens"] is None
        assert config["total_tokens"] is None
        assert config["estimated_cost"] is None
        assert config["evaluation_outcomes"] == {
            "PASS": 0, "FAIL": 0, "ERROR": 0, "NOT_EVALUATED": 0,
        }


# ================================================================== 2
def test_prompt_arena_configurations_differ_only_by_prompt(
    app_client, as_user, monkeypatch
):
    owner = as_user["id"]
    pid = _make_project(app_client)
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, pid, [
        {"input": "q1", "expected": "a1"},
        {"input": "q2", "expected": "a2"},
    ])
    _, prompt_one = _prompt_version(app_client, pid, "Prompt variant one")
    _, prompt_two = _prompt_version(app_client, pid, "Prompt variant two")

    r = app_client.post(f"{API}/arenas/prompt", json={
        "project_id": pid,
        "title": "pytest-prompt-arena",
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "prompt_version_ids": [prompt_one, prompt_two],
        "model_id": models[0],
    })
    assert r.status_code == 200, r.text
    exp = r.json()
    assert exp["experiment_type"] == "prompt_arena"
    configs = exp["configurations"]
    assert len(configs) == 2
    assert {c["prompt_version_id"] for c in configs} == {prompt_one, prompt_two}
    assert {c["model_id"] for c in configs} == {models[0]}
    assert all(c["agent_version_id"] is None for c in configs)
    assert len({c["label"] for c in configs}) == 2

    _install(monkeypatch, lambda model, req: result_success("answer"))
    _run_benchmark(app_client, owner, exp["id"])

    summary = app_client.get(f"{API}/arenas/{exp['id']}").json()
    assert summary["experiment_type"] == "prompt_arena"
    configs = summary["configurations"]
    assert len(configs) == 2
    assert {c["prompt_version_id"] for c in configs} == {prompt_one, prompt_two}
    assert all(c["run_count"] == 2 for c in configs)
    assert len({c["label"] for c in configs}) == 2


# ================================================================== 3
def test_legacy_benchmark_runner_unaffected(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _make_project(app_client)
    models = _make_models(app_client, owner, 2)
    dataset_id, version_id = _make_dataset(app_client, pid, [
        {"input": "hello", "expected": "hi"},
    ])
    r = app_client.post(f"{API}/experiments", json={
        "project_id": pid,
        "title": f"pytest-benchmark-{uuid.uuid4().hex[:6]}",
        "experiment_type": "benchmark",
        "protocol": {
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "repetitions": 1,
        },
        "configurations": [
            {"label": "cfg-0", "model_id": models[0], "order": 0},
            {"label": "cfg-1", "model_id": models[1], "order": 1},
        ],
    })
    assert r.status_code == 200, r.text
    exp = r.json()
    r = app_client.post(f"{API}/experiments/{exp['id']}/ready")
    assert r.status_code == 200, r.text

    _install(monkeypatch, lambda model, req: result_success("hi"))
    _run_benchmark(app_client, owner, exp["id"])
    runs = _runs(app_client, owner, exp["id"])
    assert len(runs) == 2
    assert all(run.get("agent_version_id") is None for run in runs)

    results = app_client.get(f"{API}/experiments/{exp['id']}/results")
    assert results.status_code == 200, results.text
    assert results.json()["legacy"] is False


# ================================================================== 4
def test_draft_and_foreign_versions_rejected(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    pid = _make_project(app_client)
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, pid, [
        {"input": "q", "expected": "a"},
    ])

    # Draft agent version -> 400.
    draft_agent = _draft_agent_version(app_client, pid, models[0])
    r = app_client.post(f"{API}/arenas/agent", json={
        "project_id": pid,
        "title": "draft-agent",
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "agent_version_ids": [draft_agent["id"]],
    })
    assert r.status_code == 400, r.text
    assert "frozen" in r.json()["detail"].lower()

    # Draft prompt version -> 400.
    _, draft_prompt = _prompt_version(app_client, pid, "draft", freeze=False)
    r = app_client.post(f"{API}/arenas/prompt", json={
        "project_id": pid,
        "title": "draft-prompt",
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "prompt_version_ids": [draft_prompt],
        "model_id": models[0],
    })
    assert r.status_code == 400, r.text

    # Foreign project -> 400.
    foreign_owner = new_owner_id()
    foreign_project = app_client.portal.call(create_project, foreign_owner)
    try:
        r = app_client.post(f"{API}/arenas/agent", json={
            "project_id": foreign_project,
            "title": "foreign-project",
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "agent_version_ids": [_frozen_agent(app_client, pid, models[0], "gamma")["id"]],
        })
        assert r.status_code == 400, r.text
    finally:
        app_client.portal.call(purge_owner, foreign_owner)

    # Foreign owner cannot read an existing arena -> 404.
    good = _frozen_agent(app_client, pid, models[0], "delta")
    r = app_client.post(f"{API}/arenas/agent", json={
        "project_id": pid,
        "title": "owned-arena",
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "agent_version_ids": [good["id"]],
    })
    assert r.status_code == 200, r.text
    exp_id = r.json()["id"]

    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: _foreign_user()
    try:
        detail = app_client.get(f"{API}/arenas/{exp_id}")
        assert detail.status_code == 404, detail.text
        listing = app_client.get(f"{API}/arenas")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 0
    finally:
        app_client.app.dependency_overrides[get_current_user] = original


# ================================================================== 5
def test_agent_arena_fixed_model_and_no_adaptive_routing(
    app_client, as_user, monkeypatch
):
    owner = as_user["id"]
    pid = _make_project(app_client)
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, pid, [
        {"input": "q", "expected": "a"},
    ])
    agent = _frozen_agent(app_client, pid, models[0], "epsilon")

    # model_ids omitted -> the agent version's fixed model is used.
    r = app_client.post(f"{API}/arenas/agent", json={
        "project_id": pid,
        "title": "fixed-model",
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "agent_version_ids": [agent["id"]],
    })
    assert r.status_code == 200, r.text
    config = r.json()["configurations"][0]
    assert config["agent_version_id"] == agent["id"]
    assert config["model_id"] == models[0]


# ================================================================== 6
def test_catalog(app_client, as_user):
    r = app_client.get(f"{API}/arenas/catalog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["arena_types"]) == {"agent_arena", "prompt_arena"}
    assert "assertions" in body["evaluation_keys"]
    assert body["model_binding"] == "fixed"


# ================================================================== 7
def test_prompt_arena_with_agent_version_pins_prompt_override(
    app_client, as_user, monkeypatch
):
    owner = as_user["id"]
    pid = _make_project(app_client)
    models = _make_models(app_client, owner, 1)
    dataset_id, version_id = _make_dataset(app_client, pid, [
        {"input": "q", "expected": "a"},
    ])
    agent = _frozen_agent(app_client, pid, models[0], "zeta")
    _, prompt_one = _prompt_version(app_client, pid, "override one")
    _, prompt_two = _prompt_version(app_client, pid, "override two")

    r = app_client.post(f"{API}/arenas/prompt", json={
        "project_id": pid,
        "title": "pytest-prompt-arena-agent",
        "dataset_id": dataset_id,
        "dataset_version_id": version_id,
        "prompt_version_ids": [prompt_one, prompt_two],
        "model_id": models[0],
        "agent_version_id": agent["id"],
    })
    assert r.status_code == 200, r.text
    exp = r.json()
    assert {c["agent_version_id"] for c in exp["configurations"]} == {agent["id"]}

    _install(monkeypatch, lambda model, req: result_success("answer"))
    _run_benchmark(app_client, owner, exp["id"])

    runs = _runs(app_client, owner, exp["id"])
    assert len(runs) == 2
    assert all(run["agent_version_id"] == agent["id"] for run in runs)
    # Each run compiled the configuration's prompt variant, not the agent's.
    assert {run["prompt_version_id"] for run in runs} == {prompt_one, prompt_two}
    assert all(run["compiled_prompt_id"] for run in runs)
