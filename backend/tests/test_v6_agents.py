"""V6.4 Agent Registry & Versioning API tests.

Covers agent identity CRUD, append-only draft/frozen/deprecated version
lifecycle with a FIXED model binding, policy defaults, binding validation
against the V6.0-V6.3 assets (prompt versions, context packs / budget
policies, tools, knowledge collections, rubrics), deterministic version
comparison, bounded usage lookup, owner isolation, the fixed-routing gate and
historical immutability. Everything runs offline against throwaway ``pytest-``
owners purged after each test.
"""
from __future__ import annotations

import uuid

from app import repository as repo
from app.deps import get_current_user
from app.models import new_id, now_iso

from _support import create_model, create_provider, new_owner_id, purge_owner

API = "/api"


# ------------------------------------------------------------------ helpers
def _project(app_client, name="pytest-v64"):
    r = app_client.post(
        f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _agent(app_client, project_id, **overrides):
    payload = {"project_id": project_id, "name": f"agent-{uuid.uuid4().hex[:8]}"}
    payload.update(overrides)
    r = app_client.post(f"{API}/agents", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _agent_version(app_client, agent_id, model_id, **overrides):
    payload = {"model_id": model_id}
    payload.update(overrides)
    r = app_client.post(f"{API}/agents/{agent_id}/versions", json=payload)
    return r


def _make_model(app_client, owner):
    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(
            owner,
            provider_id=provider_id,
            display_name=f"pytest-model-{uuid.uuid4().hex[:6]}",
            model_identifier=f"pytest/{uuid.uuid4().hex}",
        )

    return app_client.portal.call(_setup)


def _prompt(app_client, project_id):
    r = app_client.post(f"{API}/prompts", json={
        "project_id": project_id,
        "name": f"prompt-{uuid.uuid4().hex[:8]}",
        "status": "draft",
    })
    assert r.status_code == 200, r.text
    return r.json()


def _prompt_version(app_client, prompt_id, content="content"):
    r = app_client.post(f"{API}/prompts/{prompt_id}/versions", json={"content": content})
    assert r.status_code == 200, r.text
    return r.json()


def _freeze_prompt_version(app_client, prompt_id, version_id):
    r = app_client.post(f"{API}/prompts/{prompt_id}/versions/{version_id}/freeze")
    assert r.status_code == 200, r.text
    return r.json()


def _foreign_user():
    return {
        "id": f"pytest-foreign-{uuid.uuid4().hex}",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }


def _swap(app_client, user):
    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: user
    return original


def _restore(app_client, original):
    app_client.app.dependency_overrides[get_current_user] = original


# ================================================================== 1
def test_agent_identity_crud(app_client, as_user):
    pid = _project(app_client)
    created = _agent(
        app_client, pid,
        role="architect", purpose="design systems",
        responsibilities=["own architecture", "review interfaces"],
        non_responsibilities=["write production code"],
    )
    assert created["status"] == "active"
    assert created["role"] == "architect"
    assert created["purpose"] == "design systems"
    assert created["responsibilities"] == ["own architecture", "review interfaces"]
    assert created["non_responsibilities"] == ["write production code"]
    assert created["created_by"] == as_user["id"]

    listed = app_client.get(f"{API}/agents", params={"project_id": pid})
    assert listed.status_code == 200, listed.text
    assert [a["id"] for a in listed.json()] == [created["id"]]

    got = app_client.get(f"{API}/agents/{created['id']}")
    assert got.status_code == 200, got.text
    assert got.json()["name"] == created["name"]

    patched = app_client.patch(f"{API}/agents/{created['id']}", json={
        "role": "reviewer", "status": "archived",
        "responsibilities": ["review changes"],
    })
    assert patched.status_code == 200, patched.text
    assert patched.json()["role"] == "reviewer"
    assert patched.json()["responsibilities"] == ["review changes"]

    archived = app_client.get(f"{API}/agents", params={"project_id": pid})
    assert archived.json() == []
    with_archived = app_client.get(
        f"{API}/agents", params={"project_id": pid, "include_archived": True}
    )
    assert [a["id"] for a in with_archived.json()] == [created["id"]]

    invalid_role = app_client.post(
        f"{API}/agents", json={"project_id": pid, "name": "bad", "role": "wizard"}
    )
    assert invalid_role.status_code == 400, invalid_role.text

    deleted = app_client.delete(f"{API}/agents/{created['id']}")
    assert deleted.status_code == 200, deleted.text
    assert app_client.get(f"{API}/agents/{created['id']}").status_code == 404


# ================================================================== 2
def test_version_defaults_and_binding_validation(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    agent = _agent(app_client, pid)
    model_id = _make_model(app_client, owner)

    version = _agent_version(app_client, agent["id"], model_id)
    assert version.status_code == 200, version.text
    version = version.json()
    assert version["version"] == 1
    assert version["status"] == "draft"
    assert version["content_hash"]
    assert version["model_id"] == model_id
    assert version["prompt_version_id"] is None
    # Canonical policy defaults are applied, not omitted.
    assert version["tool_policy"] == {
        "default_deny": True,
        "allowed_tool_ids": [],
        "denied_tool_ids": [],
        "mode": "allowlist",
    }
    assert version["knowledge_policy"]["knowledge_enabled"] is False
    assert version["knowledge_policy"]["retrieval_top_k"] == 5
    assert version["context_policy"]["strategy"] == "FULL"
    assert version["context_policy"]["context_pack_version_ids"] == []
    assert version["runtime_policy"] == {}
    assert version["output_contract"] == {"format": "text"}
    assert version["evaluation_profile"] == {}
    assert version["routing"] == {"mode": "fixed"}

    missing_model = _agent_version(app_client, agent["id"], new_id())
    assert missing_model.status_code == 400, missing_model.text
    assert "model" in missing_model.json()["detail"].lower()

    prompt = _prompt(app_client, pid)
    draft = _prompt_version(app_client, prompt["id"])
    rejected = _agent_version(
        app_client, agent["id"], model_id, prompt_version_id=draft["id"]
    )
    assert rejected.status_code == 400, rejected.text
    assert "frozen" in rejected.json()["detail"].lower()

    frozen = _freeze_prompt_version(app_client, prompt["id"], draft["id"])
    accepted = _agent_version(
        app_client, agent["id"], model_id, prompt_version_id=frozen["id"]
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["prompt_version_id"] == frozen["id"]


# ================================================================== 3
def test_freeze_deprecate_edit_clone_and_version_numbers(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    agent = _agent(app_client, pid)
    model_id = _make_model(app_client, owner)

    v1 = _agent_version(app_client, agent["id"], model_id).json()
    assert v1["frozen_at"] is None and v1["deprecated_at"] is None

    frozen = app_client.post(f"{API}/agents/{agent['id']}/versions/{v1['id']}/freeze")
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["status"] == "frozen"
    assert frozen.json()["frozen_at"]
    frozen_hash = frozen.json()["content_hash"]

    edit_frozen = app_client.patch(
        f"{API}/agents/{agent['id']}/versions/{v1['id']}",
        json={"runtime_policy": {"temperature": 0.5}},
    )
    assert edit_frozen.status_code == 400, edit_frozen.text

    v2 = _agent_version(app_client, agent["id"], model_id).json()
    assert v2["version"] == 2
    edited = app_client.patch(
        f"{API}/agents/{agent['id']}/versions/{v2['id']}",
        json={"runtime_policy": {"temperature": 0.7}},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["runtime_policy"] == {"temperature": 0.7}

    cloned = app_client.post(f"{API}/agents/{agent['id']}/versions/{v1['id']}/clone")
    assert cloned.status_code == 200, cloned.text
    clone = cloned.json()
    assert clone["version"] == 3
    assert clone["status"] == "draft"
    assert clone["source_version_id"] == v1["id"]
    assert clone["model_id"] == model_id

    deprecated = app_client.post(
        f"{API}/agents/{agent['id']}/versions/{v1['id']}/deprecate"
    )
    assert deprecated.status_code == 200, deprecated.text
    assert deprecated.json()["status"] == "deprecated"
    assert deprecated.json()["deprecated_at"]

    again = app_client.post(f"{API}/agents/{agent['id']}/versions/{v1['id']}/freeze")
    assert again.status_code == 400, again.text

    listed = app_client.get(f"{API}/agents/{agent['id']}/versions")
    assert listed.status_code == 200, listed.text
    assert [v["version"] for v in listed.json()] == [3, 2, 1]


# ================================================================== 4
def test_compare_versions_reports_fields_and_policy_diffs(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    agent = _agent(app_client, pid)
    model_a = _make_model(app_client, owner)
    model_b = _make_model(app_client, owner)

    prompt_a = _prompt(app_client, pid)
    pv_a = _freeze_prompt_version(
        app_client, prompt_a["id"],
        _prompt_version(app_client, prompt_a["id"], "prompt a")["id"],
    )
    prompt_b = _prompt(app_client, pid)
    pv_b = _freeze_prompt_version(
        app_client, prompt_b["id"],
        _prompt_version(app_client, prompt_b["id"], "prompt b")["id"],
    )

    tool_a = new_id()
    tool_b = new_id()
    tool_c = new_id()
    col_a = new_id()
    col_b = new_id()

    async def _seed_tools():
        now = now_iso()
        for tool_id in (tool_a, tool_b, tool_c):
            await repo.tools.insert({
                "id": tool_id, "owner_id": owner, "name": f"tool-{tool_id[:6]}",
                "scope": "owner", "enabled": True,
                "created_at": now, "updated_at": now,
            })
        for collection_id in (col_a, col_b):
            await repo.knowledge_collections.insert({
                "id": collection_id, "owner_id": owner, "project_id": pid,
                "name": f"collection-{collection_id[:6]}", "status": "active",
                "created_at": now, "updated_at": now,
            })

    app_client.portal.call(_seed_tools)

    v1 = _agent_version(
        app_client, agent["id"], model_a,
        prompt_version_id=pv_a["id"],
        tool_policy={"allowed_tool_ids": [tool_a], "denied_tool_ids": [tool_b]},
        knowledge_policy={"knowledge_enabled": True, "allowed_collection_ids": [col_a]},
    ).json()
    assert v1.get("id"), v1
    v2 = _agent_version(
        app_client, agent["id"], model_b,
        prompt_version_id=pv_b["id"],
        tool_policy={"allowed_tool_ids": [tool_c], "denied_tool_ids": [tool_b]},
        knowledge_policy={"knowledge_enabled": True, "allowed_collection_ids": [col_b]},
        runtime_policy={"temperature": 0.2},
    ).json()
    assert v2.get("id"), v2

    r = app_client.get(
        f"{API}/agents/{agent['id']}/versions/compare",
        params={"from_version_id": v1["id"], "to_version_id": v2["id"]},
    )
    assert r.status_code == 200, r.text
    diff = r.json()
    assert diff["from"]["id"] == v1["id"]
    assert diff["to"]["id"] == v2["id"]

    fields = {f["field"]: f for f in diff["fields"]}
    assert fields["model_id"]["changed"] is True
    assert fields["model_id"]["from"] == model_a
    assert fields["model_id"]["to"] == model_b
    assert fields["prompt_version_id"]["changed"] is True
    assert diff["summary"]["fields_changed"] >= 3

    tools = diff["policy_diff"]["tools"]
    assert tools["allowed_added"] == [tool_c]
    assert tools["allowed_removed"] == [tool_a]
    assert tools["denied_added"] == []
    assert tools["denied_removed"] == []

    knowledge = diff["policy_diff"]["knowledge"]
    assert knowledge["added"] == [col_b]
    assert knowledge["removed"] == [col_a]
    assert knowledge["changed"] == []

    assert diff["policy_diff"]["runtime"]["changed_fields"] == [
        {"field": "temperature", "from": None, "to": 0.2}
    ]

    same = app_client.get(
        f"{API}/agents/{agent['id']}/versions/compare",
        params={"from_version_id": v1["id"], "to_version_id": v1["id"]},
    )
    assert same.status_code == 200, same.text
    assert same.json()["summary"]["fields_changed"] == 0


# ================================================================== 5
def test_usage_lists_runs_evaluations_findings_scorecards(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    agent = _agent(app_client, pid)
    model_id = _make_model(app_client, owner)
    version = _agent_version(app_client, agent["id"], model_id).json()
    vid = version["id"]

    run_id = new_id()
    other_run_id = new_id()
    scorecard_id = new_id()

    async def _seed():
        now = now_iso()
        await repo.runs.insert({
            "id": run_id, "owner_id": owner, "project_id": pid,
            "experiment_id": "exp-1", "agent_version_id": vid,
            "status": "success", "created_at": now,
        })
        await repo.runs.insert({
            "id": other_run_id, "owner_id": owner, "project_id": pid,
            "agent_version_id": new_id(), "status": "success", "created_at": now,
        })
        await repo.evaluations.insert({
            "id": new_id(), "owner_id": owner, "project_id": pid,
            "run_id": run_id, "outcome": "PASS", "evaluator_type": "rule",
            "created_at": now,
        })
        await repo.failures.insert({
            "id": new_id(), "owner_id": owner, "project_id": pid,
            "run_id": run_id, "failure_domain": "correctness",
            "taxonomy_code": "WRONG_ANSWER", "severity": "high",
            "status": "OPEN", "created_at": now,
        })
        await repo.scorecards.insert({
            "id": scorecard_id, "owner_id": owner, "project_id": pid,
            "scope": "run", "run_id": run_id, "created_at": now,
        })

    app_client.portal.call(_seed)

    r = app_client.get(f"{API}/agents/{agent['id']}/versions/{vid}/usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == agent["id"]
    assert body["version_id"] == vid
    assert [run["run_id"] for run in body["runs"]] == [run_id]
    assert body["runs"][0]["experiment_id"] == "exp-1"
    assert body["runs"][0]["status"] == "success"
    assert body["evaluations"] == [{
        "id": body["evaluations"][0]["id"],
        "run_id": run_id,
        "outcome": "PASS",
        "evaluator_type": "rule",
    }]
    assert body["findings"] == [{
        "id": body["findings"][0]["id"],
        "failure_domain": "correctness",
        "taxonomy_code": "WRONG_ANSWER",
        "severity": "high",
        "status": "OPEN",
    }]
    assert body["scorecards"] == [{
        "id": scorecard_id,
        "scope": "run",
        "created_at": body["scorecards"][0]["created_at"],
    }]

    unused = _agent_version(app_client, agent["id"], model_id).json()
    empty = app_client.get(
        f"{API}/agents/{agent['id']}/versions/{unused['id']}/usage"
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["runs"] == []
    assert empty.json()["evaluations"] == []
    assert empty.json()["findings"] == []
    # No scorecards exist for a fresh project.
    fresh_pid = _project(app_client, "pytest-v64-empty")
    fresh_agent = _agent(app_client, fresh_pid)
    fresh_version = _agent_version(app_client, fresh_agent["id"], model_id).json()
    fresh = app_client.get(
        f"{API}/agents/{fresh_agent['id']}/versions/{fresh_version['id']}/usage"
    )
    assert fresh.json()["scorecards"] == []


# ================================================================== 6
def test_ownership_isolation_and_foreign_references(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    agent = _agent(app_client, pid)
    model_id = _make_model(app_client, owner)
    version = _agent_version(app_client, agent["id"], model_id).json()

    foreign = new_owner_id()
    foreign_project = new_id()

    async def _seed_foreign():
        now = now_iso()
        await repo.projects.insert({
            "id": foreign_project, "owner_id": foreign, "name": "foreign",
            "description": "", "tags": [], "status": "active",
            "created_at": now, "updated_at": now,
        })
        provider_id = await create_provider(foreign)
        fmodel = await create_model(
            foreign, provider_id=provider_id,
            display_name="foreign-model",
            model_identifier=f"pytest/{uuid.uuid4().hex}",
        )
        prompt_id = new_id()
        pv_id = new_id()
        await repo.prompts.insert({
            "id": prompt_id, "owner_id": foreign, "project_id": foreign_project,
            "name": "foreign-prompt", "status": "frozen",
            "created_at": now, "updated_at": now,
        })
        await repo.prompt_versions.insert({
            "id": pv_id, "owner_id": foreign, "project_id": foreign_project,
            "prompt_id": prompt_id, "version": 1, "status": "frozen",
            "content": "foreign content", "created_at": now, "updated_at": now,
        })
        collection_id = new_id()
        await repo.knowledge_collections.insert({
            "id": collection_id, "owner_id": foreign, "project_id": foreign_project,
            "name": "foreign-collection", "status": "active",
            "created_at": now, "updated_at": now,
        })
        tool_id = new_id()
        await repo.tools.insert({
            "id": tool_id, "owner_id": foreign, "project_id": foreign_project,
            "name": "foreign-tool", "scope": "project", "enabled": True,
            "created_at": now, "updated_at": now,
        })
        return {
            "model": fmodel, "prompt_version": pv_id,
            "collection": collection_id, "tool": tool_id,
        }

    bundle = app_client.portal.call(_seed_foreign)
    try:
        # A project this owner does not own is a domain error, not a 404 leak.
        assert app_client.post(f"{API}/agents", json={
            "project_id": foreign_project, "name": "nope",
        }).status_code == 400

        foreign_model = _agent_version(app_client, agent["id"], bundle["model"])
        assert foreign_model.status_code == 400, foreign_model.text

        foreign_prompt = _agent_version(
            app_client, agent["id"], model_id,
            prompt_version_id=bundle["prompt_version"],
        )
        assert foreign_prompt.status_code == 400, foreign_prompt.text

        foreign_collection = _agent_version(
            app_client, agent["id"], model_id,
            knowledge_policy={"allowed_collection_ids": [bundle["collection"]]},
        )
        assert foreign_collection.status_code == 400, foreign_collection.text

        foreign_tool = _agent_version(
            app_client, agent["id"], model_id,
            tool_policy={"allowed_tool_ids": [bundle["tool"]]},
        )
        assert foreign_tool.status_code == 400, foreign_tool.text

        original = _swap(app_client, _foreign_user())
        try:
            assert app_client.get(f"{API}/agents/{agent['id']}").status_code == 404
            assert app_client.patch(
                f"{API}/agents/{agent['id']}", json={"name": "x"}
            ).status_code == 404
            assert app_client.delete(f"{API}/agents/{agent['id']}").status_code == 404
            assert app_client.get(
                f"{API}/agents/{agent['id']}/versions"
            ).status_code == 404
            assert app_client.get(
                f"{API}/agents/{agent['id']}/versions/{version['id']}"
            ).status_code == 404
            assert app_client.post(
                f"{API}/agents/{agent['id']}/versions/{version['id']}/freeze"
            ).status_code == 404
            assert app_client.get(
                f"{API}/agents/{agent['id']}/versions/{version['id']}/usage"
            ).status_code == 404
            assert app_client.get(
                f"{API}/agents/{agent['id']}/versions/compare",
                params={"from_version_id": version["id"], "to_version_id": version["id"]},
            ).status_code == 404
        finally:
            _restore(app_client, original)
    finally:
        app_client.portal.call(purge_owner, foreign)

    assert app_client.get(f"{API}/agents/{agent['id']}").status_code == 200


# ================================================================== 7
def test_routing_is_fixed_only(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    agent = _agent(app_client, pid)
    model_id = _make_model(app_client, owner)
    other_model = _make_model(app_client, owner)

    adaptive = _agent_version(
        app_client, agent["id"], model_id, routing={"mode": "adaptive"}
    )
    assert adaptive.status_code == 400, adaptive.text

    fixed = _agent_version(
        app_client, agent["id"], model_id, routing={"mode": "fixed"}
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["routing"]["mode"] == "fixed"

    mismatch = _agent_version(
        app_client, agent["id"], model_id,
        routing={"mode": "fixed", "model_id": other_model},
    )
    assert mismatch.status_code == 400, mismatch.text

    matching = _agent_version(
        app_client, agent["id"], model_id,
        routing={"mode": "fixed", "model_id": model_id},
    )
    assert matching.status_code == 200, matching.text
    assert matching.json()["routing"]["model_id"] == model_id


# ================================================================== 8
def test_historical_version_unchanged_after_newer_draft(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    agent = _agent(app_client, pid)
    model_id = _make_model(app_client, owner)

    v1 = _agent_version(
        app_client, agent["id"], model_id,
        runtime_policy={"temperature": 0.1},
    ).json()
    frozen = app_client.post(
        f"{API}/agents/{agent['id']}/versions/{v1['id']}/freeze"
    ).json()

    v2 = _agent_version(app_client, agent["id"], model_id).json()
    edited = app_client.patch(
        f"{API}/agents/{agent['id']}/versions/{v2['id']}",
        json={"model_id": model_id, "runtime_policy": {"temperature": 0.9}},
    )
    assert edited.status_code == 200, edited.text

    historical = app_client.get(
        f"{API}/agents/{agent['id']}/versions/{v1['id']}"
    )
    assert historical.status_code == 200, historical.text
    assert historical.json()["status"] == "frozen"
    assert historical.json()["content_hash"] == frozen["content_hash"]
    assert historical.json()["frozen_at"] == frozen["frozen_at"]
    assert historical.json()["runtime_policy"] == {"temperature": 0.1}

    newer = app_client.get(f"{API}/agents/{agent['id']}/versions/{v2['id']}")
    assert newer.json()["runtime_policy"] == {"temperature": 0.9}


# ================================================================== 9
def test_latest_version_attached_to_agent_list_and_detail(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    agent = _agent(app_client, pid)
    model_id = _make_model(app_client, owner)

    def _assert_no_latest(item):
        assert item["latest_version"] is None
        assert item["latest_version_id"] is None
        assert item["latest_version_status"] is None
        assert item["latest_version_model_id"] is None

    listed = app_client.get(f"{API}/agents", params={"project_id": pid})
    assert listed.status_code == 200, listed.text
    item = next(a for a in listed.json() if a["id"] == agent["id"])
    _assert_no_latest(item)

    detail = app_client.get(f"{API}/agents/{agent['id']}")
    assert detail.status_code == 200, detail.text
    _assert_no_latest(detail.json())

    v1 = _agent_version(app_client, agent["id"], model_id).json()

    listed = app_client.get(f"{API}/agents", params={"project_id": pid}).json()
    item = next(a for a in listed if a["id"] == agent["id"])
    # latest_version is the highest version NUMBER (int), not an object.
    assert isinstance(item["latest_version"], int)
    assert item["latest_version"] == 1
    assert item["latest_version_id"] == v1["id"]
    assert item["latest_version_status"] == "draft"
    assert item["latest_version_model_id"] == model_id

    detail = app_client.get(f"{API}/agents/{agent['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert isinstance(body["latest_version"], int)
    assert body["latest_version"] == 1
    assert body["latest_version_id"] == v1["id"]
    assert body["latest_version_status"] == "draft"
    assert body["latest_version_model_id"] == model_id

    v2 = _agent_version(app_client, agent["id"], model_id).json()

    listed = app_client.get(f"{API}/agents", params={"project_id": pid}).json()
    item = next(a for a in listed if a["id"] == agent["id"])
    assert isinstance(item["latest_version"], int)
    assert item["latest_version"] == 2
    assert item["latest_version_id"] == v2["id"]
    assert item["latest_version_status"] == "draft"
    assert item["latest_version_model_id"] == model_id

    detail = app_client.get(f"{API}/agents/{agent['id']}").json()
    assert isinstance(detail["latest_version"], int)
    assert detail["latest_version"] == 2
    assert detail["latest_version_id"] == v2["id"]
    assert detail["latest_version_status"] == "draft"
    assert detail["latest_version_model_id"] == model_id
