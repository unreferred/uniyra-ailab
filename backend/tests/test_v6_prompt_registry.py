"""V6.1 Prompt Registry & Versioning API tests.

Covers append-only draft/frozen/deprecated prompt versions over the existing
``repo.prompts`` / ``repo.prompt_versions`` collections (no parallel store),
deterministic version comparison, project-scoped block references, bounded
usage lookup, historical immutability, owner isolation and the experiment
immutability gate. Everything runs offline against throwaway ``pytest-`` owners
that are purged after each test.
"""
from __future__ import annotations

import uuid

from app import repository as repo
from app.deps import get_current_user
from app.models import new_id, now_iso

from _support import create_model, create_provider

API = "/api"


def _project(app_client, name="pytest-v61"):
    r = app_client.post(
        f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _prompt(app_client, project_id, name="pytest-prompt"):
    r = app_client.post(f"{API}/prompts", json={
        "project_id": project_id,
        "name": f"{name}-{uuid.uuid4().hex[:8]}",
        "status": "draft",
    })
    assert r.status_code == 200, r.text
    return r.json()


def _version(app_client, prompt_id, content, **overrides):
    payload = {"content": content}
    payload.update(overrides)
    r = app_client.post(f"{API}/prompts/{prompt_id}/versions", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _block(app_client, project_id, **overrides):
    payload = {
        "project_id": project_id,
        "name": f"block-{uuid.uuid4().hex[:8]}",
        "category": "other",
        "content": "body",
    }
    payload.update(overrides)
    r = app_client.post(f"{API}/prompt-blocks", json=payload)
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


# ================================================================== 1
def test_create_versions_are_append_only_drafts(app_client, as_user):
    pid = _project(app_client)
    prompt = _prompt(app_client, pid)

    v1 = _version(app_client, prompt["id"], "first content")
    assert v1["version"] == 1
    assert v1["status"] == "draft"
    assert v1["content_hash"]
    assert v1["prompt_id"] == prompt["id"]

    v2 = _version(app_client, prompt["id"], "second content")
    assert v2["version"] == 2
    assert v2["status"] == "draft"

    listed = app_client.get(f"{API}/prompts/{prompt['id']}/versions")
    assert listed.status_code == 200, listed.text
    assert [v["version"] for v in listed.json()] == [2, 1]
    assert all(v["status"] == "draft" for v in listed.json())


# ================================================================== 2
def test_edit_only_draft_and_hash_changes(app_client, as_user):
    pid = _project(app_client)
    prompt = _prompt(app_client, pid)
    v1 = _version(app_client, prompt["id"], "original")

    patched = app_client.patch(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}",
        json={"content": "edited", "variables": {"x": 1}},
    )
    assert patched.status_code == 200, patched.text
    edited = patched.json()
    assert edited["content"] == "edited"
    assert edited["variables"] == {"x": 1}
    assert edited["content_hash"] != v1["content_hash"]

    frozen = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}/freeze"
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["status"] == "frozen"
    frozen_hash = frozen.json()["content_hash"]

    after_freeze = app_client.patch(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}",
        json={"content": "should not apply"},
    )
    assert after_freeze.status_code == 400, after_freeze.text

    stored = app_client.get(f"{API}/prompts/{prompt['id']}/versions/{v1['id']}")
    assert stored.status_code == 200, stored.text
    assert stored.json()["content"] == "edited"
    assert stored.json()["content_hash"] == frozen_hash
    assert stored.json()["status"] == "frozen"


# ================================================================== 3
def test_freeze_and_deprecate_lifecycle(app_client, as_user):
    pid = _project(app_client)
    prompt = _prompt(app_client, pid)
    v1 = _version(app_client, prompt["id"], "content")

    assert v1["frozen_at"] is None and v1["deprecated_at"] is None

    frozen = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}/freeze"
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["status"] == "frozen"
    assert frozen.json()["frozen_at"]

    again = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}/freeze"
    )
    assert again.status_code == 400, again.text
    assert "already frozen" in again.json()["detail"].lower()

    deprecated = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}/deprecate"
    )
    assert deprecated.status_code == 200, deprecated.text
    assert deprecated.json()["status"] == "deprecated"
    assert deprecated.json()["deprecated_at"]

    edit_after = app_client.patch(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}",
        json={"content": "nope"},
    )
    assert edit_after.status_code == 400, edit_after.text


# ================================================================== 4
def test_clone_and_restore_create_new_drafts(app_client, as_user):
    pid = _project(app_client)
    prompt = _prompt(app_client, pid)
    v1 = _version(app_client, prompt["id"], "clone me", notes="original notes")
    app_client.post(f"{API}/prompts/{prompt['id']}/versions/{v1['id']}/freeze")
    v2 = _version(app_client, prompt["id"], "newer draft")

    cloned = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}/clone"
    )
    assert cloned.status_code == 200, cloned.text
    clone = cloned.json()
    assert clone["version"] == 3
    assert clone["status"] == "draft"
    assert clone["content"] == "clone me"
    assert clone["source_version_id"] == v1["id"]

    restored = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions/{v2['id']}/restore"
    )
    assert restored.status_code == 200, restored.text
    restore = restored.json()
    assert restore["version"] == 4
    assert restore["status"] == "draft"
    assert restore["content"] == "newer draft"
    assert restore["source_version_id"] == v2["id"]
    assert "restored from v2" in restore["notes"].lower()


# ================================================================== 5
def test_compare_versions_deterministic_diff(app_client, as_user):
    pid = _project(app_client)
    prompt = _prompt(app_client, pid)

    v1 = _version(
        app_client, prompt["id"], "alpha\nbeta\ngamma",
        system_prompt="sys1", variables={"a": 1, "b": 2},
    )
    v2 = _version(
        app_client, prompt["id"], "alpha\ngamma\ndelta",
        system_prompt="sys2", variables={"a": 1, "b": 3, "c": 9},
    )

    r = app_client.get(
        f"{API}/prompts/{prompt['id']}/versions/compare",
        params={"from_version_id": v1["id"], "to_version_id": v2["id"]},
    )
    assert r.status_code == 200, r.text
    diff = r.json()

    assert diff["from"]["id"] == v1["id"]
    assert diff["to"]["id"] == v2["id"]
    ops = {entry["op"] for entry in diff["content_diff"]}
    assert ops <= {"equal", "delete", "insert"}
    assert "delete" in ops and "insert" in ops
    assert diff["summary"]["lines_added"] == 1
    assert diff["summary"]["lines_removed"] == 1

    fields = {f["field"]: f for f in diff["fields"]}
    assert fields["system_prompt"]["changed"] is True
    assert fields["system_prompt"]["from"] == "sys1"
    assert fields["system_prompt"]["to"] == "sys2"
    assert fields["variables"]["changed"] is True
    assert diff["summary"]["fields_changed"] >= 2

    assert diff["variables"]["added"] == ["c"]
    assert diff["variables"]["removed"] == []
    assert diff["variables"]["changed"] == [{"key": "b", "from": 2, "to": 3}]
    assert diff["blocks"] == {"added": [], "removed": []}

    same = app_client.get(
        f"{API}/prompts/{prompt['id']}/versions/compare",
        params={"from_version_id": v1["id"], "to_version_id": v1["id"]},
    )
    assert same.status_code == 200, same.text
    identical = same.json()
    assert identical["summary"] == {
        "lines_added": 0, "lines_removed": 0, "fields_changed": 0,
    }
    assert all(entry["op"] == "equal" for entry in identical["content_diff"])
    assert identical["variables"] == {"added": [], "removed": [], "changed": []}


# ================================================================== 6
def test_block_references_are_project_scoped(app_client, as_user):
    pid = _project(app_client)
    other_pid = _project(app_client, "pytest-v61-other")
    prompt = _prompt(app_client, pid)
    block = _block(app_client, pid)
    foreign_block = _block(app_client, other_pid)

    created = _version(app_client, prompt["id"], "with blocks", blocks=[block["id"]])
    assert created["blocks"] == [block["id"]]

    got = app_client.get(f"{API}/prompts/{prompt['id']}/versions/{created['id']}")
    assert got.status_code == 200, got.text
    assert got.json()["blocks"] == [block["id"]]

    attach_foreign = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions",
        json={"content": "bad", "blocks": [foreign_block["id"]]},
    )
    assert attach_foreign.status_code == 400, attach_foreign.text

    attach_missing = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions",
        json={"content": "bad", "blocks": [new_id()]},
    )
    assert attach_missing.status_code == 400, attach_missing.text


# ================================================================== 7
def test_usage_lists_configs_runs_and_evaluations(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    prompt = _prompt(app_client, pid)
    v1 = _version(app_client, prompt["id"], "used content")
    frozen = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}/freeze"
    ).json()
    vid = frozen["id"]

    experiment_id = new_id()
    configuration_id = new_id()
    run_id = new_id()

    async def _seed():
        await repo.experiment_configs.insert({
            "id": configuration_id, "owner_id": owner, "project_id": pid,
            "experiment_id": experiment_id, "label": "cfg-0",
            "model_id": new_id(), "prompt_version_id": vid,
            "created_at": now_iso(),
        })
        await repo.runs.insert({
            "id": run_id, "owner_id": owner, "project_id": pid,
            "experiment_id": experiment_id, "configuration_id": configuration_id,
            "status": "success", "execution_kind": "benchmark",
            "configuration_snapshot": {"prompt_version_id": vid},
            "created_at": now_iso(),
        })
        await repo.evaluations.insert({
            "id": new_id(), "owner_id": owner, "project_id": pid,
            "experiment_id": experiment_id, "run_id": run_id,
            "outcome": "PASS", "evaluator_type": "rule", "created_at": now_iso(),
        })

    app_client.portal.call(_seed)

    r = app_client.get(f"{API}/prompts/{prompt['id']}/usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prompt_id"] == prompt["id"]
    assert body["totals"] == {"experiments": 1, "runs": 1, "evaluations": 1}

    entry = next(v for v in body["versions"] if v["id"] == vid)
    assert entry["status"] == "frozen"
    assert entry["used_by"]["experiments"] == [{
        "experiment_id": experiment_id,
        "configuration_id": configuration_id,
        "label": "cfg-0",
    }]
    runs = entry["used_by"]["runs"]
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id
    assert runs[0]["experiment_id"] == experiment_id
    assert runs[0]["status"] == "success"
    assert runs[0]["evaluations"] == [{
        "id": runs[0]["evaluations"][0]["id"],
        "outcome": "PASS",
        "evaluator_type": "rule",
    }]


# ================================================================== 8
def test_frozen_version_is_historically_immutable(app_client, as_user):
    pid = _project(app_client)
    prompt = _prompt(app_client, pid)
    v1 = _version(app_client, prompt["id"], "frozen v1 content")
    frozen = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions/{v1['id']}/freeze"
    ).json()

    v2 = _version(app_client, prompt["id"], "v2 draft content")
    edited = app_client.patch(
        f"{API}/prompts/{prompt['id']}/versions/{v2['id']}",
        json={"content": "v2 edited content"},
    )
    assert edited.status_code == 200, edited.text

    historical = app_client.get(f"{API}/prompts/{prompt['id']}/versions/{v1['id']}")
    assert historical.status_code == 200, historical.text
    assert historical.json()["content"] == "frozen v1 content"
    assert historical.json()["status"] == "frozen"
    assert historical.json()["content_hash"] == frozen["content_hash"]
    assert historical.json()["frozen_at"] == frozen["frozen_at"]

    newer = app_client.get(f"{API}/prompts/{prompt['id']}/versions/{v2['id']}")
    assert newer.json()["content"] == "v2 edited content"


# ================================================================== 9
def test_owner_isolation_for_version_endpoints(app_client, as_user):
    pid = _project(app_client)
    prompt = _prompt(app_client, pid)
    v1 = _version(app_client, prompt["id"], "private content")
    app_client.post(f"{API}/prompts/{prompt['id']}/versions/{v1['id']}/freeze")

    base = f"{API}/prompts/{prompt['id']}/versions"
    original = _swap(app_client, _foreign_user())
    try:
        assert app_client.get(base).status_code == 404
        assert app_client.get(f"{base}/{v1['id']}").status_code == 404
        assert app_client.patch(f"{base}/{v1['id']}", json={"content": "x"}).status_code == 404
        assert app_client.post(f"{base}/{v1['id']}/freeze").status_code == 404
        assert app_client.post(f"{base}/{v1['id']}/deprecate").status_code == 404
        assert app_client.post(f"{base}/{v1['id']}/clone").status_code == 404
        assert app_client.post(f"{base}/{v1['id']}/restore").status_code == 404
        assert app_client.get(
            f"{base}/compare",
            params={"from_version_id": v1["id"], "to_version_id": v1["id"]},
        ).status_code == 404
        assert app_client.post(base, json={"content": "x"}).status_code == 404
        assert app_client.get(f"{API}/prompts/{prompt['id']}/usage").status_code == 404
    finally:
        _restore(app_client, original)

    assert app_client.get(f"{base}/{v1['id']}").status_code == 200


# ================================================================== 10
def test_experiment_requires_frozen_prompt_version(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    prompt = _prompt(app_client, pid)
    model_id = _make_model(app_client, owner)

    draft = _version(app_client, prompt["id"], "draft content")

    def _create(version_id):
        return app_client.post(f"{API}/experiments", json={
            "project_id": pid,
            "title": f"pytest-exp-{uuid.uuid4().hex[:6]}",
            "configurations": [{
                "label": "cfg-0",
                "model_id": model_id,
                "prompt_version_id": version_id,
            }],
        })

    rejected = _create(draft["id"])
    assert rejected.status_code == 400, rejected.text
    assert "frozen" in rejected.json()["detail"].lower()

    frozen = app_client.post(
        f"{API}/prompts/{prompt['id']}/versions/{draft['id']}/freeze"
    ).json()
    accepted = _create(frozen["id"])
    assert accepted.status_code == 200, accepted.text
    config = accepted.json()["configurations"][0]
    assert config["prompt_version_id"] == frozen["id"]
    snapshot = config["prompt_version_snapshot"]
    assert snapshot["status"] == "frozen"
    assert snapshot["id"] == frozen["id"]
