"""V6.2 Context Lab API tests.

Covers Context Packs and versions (append-only draft/frozen/deprecated over the
existing context subsystem), deterministic pack comparison, starter-pack
materialization, budget policies and resolution, the pure preview endpoint, the
assembly inspector, run-context diffing, owner isolation and the additive-only
guarantee for the pre-existing assembler. Everything runs offline against
throwaway ``pytest-`` owners purged after each test.
"""
from __future__ import annotations

import uuid

from app import providers as providers_module
from app import repository as repo
from app.deps import get_current_user
from app.models import new_id, now_iso
from app.services import context as context_service
from app.services import context_lab

API = "/api/context-lab"

_MANDATORY = {"SYSTEM", "PROMPT", "POLICY", "INVARIANT", "USER_TASK"}


def _project(app_client, name="pytest-v62"):
    r = app_client.post(f"/api/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _pack(app_client, project_id, name="pytest-pack"):
    r = app_client.post(f"{API}/packs", json={
        "project_id": project_id,
        "name": f"{name}-{uuid.uuid4().hex[:8]}",
        "description": "pack description",
    })
    assert r.status_code == 200, r.text
    return r.json()


def _item(key, content, source_type="POLICY", *, title="", order=0):
    return {
        "key": key,
        "title": title or key,
        "source_type": source_type,
        "content": content,
        "order": order,
        "metadata": {},
    }


def _version(app_client, pack_id, items, description=""):
    r = app_client.post(f"{API}/packs/{pack_id}/versions", json={
        "description": description,
        "items": items,
    })
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


def _insert_run_and_snapshot(
    app_client, owner, project_id, run_id, components, *, excluded=None, snapshot=True
):
    async def _setup():
        await repo.runs.insert({
            "id": run_id,
            "owner_id": owner,
            "project_id": project_id,
            "status": "success",
            "context_strategy": "FULL",
            "context_hash": f"hash-{run_id}",
            "context_snapshot_id": f"snap-{run_id}",
            "created_at": now_iso(),
        })
        if not snapshot:
            return
        await repo.context_snapshots.insert({
            "id": f"snap-{run_id}",
            "owner_id": owner,
            "run_id": run_id,
            "trace_id": None,
            "project_id": project_id,
            "components": components,
            "excluded_components": excluded or [],
            "total_context_tokens": None,
            "known_token_count": sum(
                c["token_count"] for c in components if c.get("token_count") is not None
            ),
            "estimated_total_tokens": sum(
                c.get("estimated_tokens") or 0 for c in components
            ),
            "estimated_method": "heuristic:chars_div_4",
            "unknown_token_component_count": sum(
                1 for c in components if c.get("token_count") is None
            ),
            "context_hash": f"hash-{run_id}",
            "strategy": "FULL",
            "over_budget": False,
            "warnings": [],
            "schema_version": 1,
            "created_at": now_iso(),
        })

    app_client.portal.call(_setup)


def _component(component_type, content, *, ordering=0, estimated_tokens=5, **overrides):
    doc = {
        "component_type": component_type,
        "content": content,
        "ordering": ordering,
        "included": True,
        "excluded_reason": None,
        "token_count": None,
        "estimated_tokens": estimated_tokens,
        "estimated_method": "heuristic:chars_div_4",
        "truncated": False,
        "truncation_reason": None,
        "content_hash": f"hash-{component_type}-{content[:8]}",
        "source_ref": None,
        "metadata": {},
    }
    doc.update(overrides)
    return doc


# ================================================================== 1
def test_packs_versions_lifecycle_and_history(app_client, as_user):
    project_id = _project(app_client)
    pack = _pack(app_client, project_id)
    assert pack["status"] == "active"

    v1 = _version(app_client, pack["id"], [
        _item("a", "content a", "POLICY", order=1),
        _item("b", "content b", "MANUAL", order=2),
    ])
    assert v1["version"] == 1
    assert v1["status"] == "draft"
    assert v1["content_hash"]
    assert all(item["token_estimate"] > 0 for item in v1["items"])
    assert all(item["content_hash"] for item in v1["items"])

    edited = app_client.patch(
        f"{API}/packs/{pack['id']}/versions/{v1['id']}",
        json={"items": [_item("a", "edited a", "POLICY", order=1)]},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "draft"
    assert edited.json()["content_hash"] != v1["content_hash"]

    frozen = app_client.post(f"{API}/packs/{pack['id']}/versions/{v1['id']}/freeze")
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["status"] == "frozen"
    assert frozen.json()["frozen_at"]
    frozen_hash = frozen.json()["content_hash"]

    edit_frozen = app_client.patch(
        f"{API}/packs/{pack['id']}/versions/{v1['id']}",
        json={"items": [_item("a", "should not apply")]},
    )
    assert edit_frozen.status_code == 400, edit_frozen.text

    v2 = _version(app_client, pack["id"], [_item("a", "v2 draft a")])
    assert v2["version"] == 2
    assert v2["status"] == "draft"

    historical = app_client.get(f"{API}/packs/{pack['id']}/versions/{v1['id']}")
    assert historical.status_code == 200, historical.text
    assert historical.json()["status"] == "frozen"
    assert historical.json()["content_hash"] == frozen_hash
    assert historical.json()["items"][0]["content"] == "edited a"

    deprecated = app_client.post(
        f"{API}/packs/{pack['id']}/versions/{v1['id']}/deprecate"
    )
    assert deprecated.status_code == 200, deprecated.text
    assert deprecated.json()["status"] == "deprecated"

    listed = app_client.get(f"{API}/packs/{pack['id']}/versions")
    assert listed.status_code == 200, listed.text
    assert [v["version"] for v in listed.json()] == [2, 1]


# ================================================================== 2
def test_compare_pack_versions_deterministic(app_client, as_user):
    project_id = _project(app_client)
    pack = _pack(app_client, project_id)

    v1 = _version(app_client, pack["id"], [
        _item("a", "alpha", "POLICY", order=1),
        _item("b", "beta", "MANUAL", order=2),
    ])
    v2 = _version(app_client, pack["id"], [
        _item("a", "alpha changed", "POLICY", order=1, title="a renamed"),
        _item("c", "gamma", "OTHER", order=3),
    ])

    r = app_client.get(
        f"{API}/packs/{pack['id']}/versions/compare",
        params={"from_version_id": v1["id"], "to_version_id": v2["id"]},
    )
    assert r.status_code == 200, r.text
    diff = r.json()
    assert diff["from"]["id"] == v1["id"] and diff["to"]["id"] == v2["id"]
    assert diff["added"] == ["c"]
    assert diff["removed"] == ["b"]
    assert [entry["key"] for entry in diff["changed"]] == ["a"]
    changed_fields = {field["field"] for field in diff["changed"][0]["fields"]}
    assert {"content", "title"} <= changed_fields
    assert diff["summary"] == {
        "added": 1, "removed": 1, "changed": 1, "unchanged": 0,
    }

    same = app_client.get(
        f"{API}/packs/{pack['id']}/versions/compare",
        params={"from_version_id": v1["id"], "to_version_id": v1["id"]},
    )
    assert same.status_code == 200, same.text
    assert same.json()["summary"] == {
        "added": 0, "removed": 0, "changed": 0, "unchanged": 2,
    }


# ================================================================== 3
def test_materialize_starter_packs_is_idempotent(app_client, as_user):
    project_id = _project(app_client)

    r = app_client.post(f"{API}/packs/starters", json={"project_id": project_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["created"]) == 5
    assert all(entry["version"]["status"] == "frozen" for entry in body["created"])
    assert all(entry["pack"]["status"] == "active" for entry in body["created"])

    listed = app_client.get(f"{API}/packs", params={"project_id": project_id})
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 5

    again = app_client.post(f"{API}/packs/starters", json={"project_id": project_id})
    assert again.status_code == 200, again.text
    assert again.json()["created"] == []
    assert len(again.json()["existing"]) == 5


# ================================================================== 4
def test_budget_policies_crud_resolve_and_invalid_enum(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client)

    created = app_client.post(f"{API}/budget-policies", json={
        "project_id": project_id,
        "name": "policy-one",
        "max_context_tokens": 100,
        "reserve_output_tokens": 10,
        "history_budget": 5,
    })
    assert created.status_code == 200, created.text
    policy = created.json()
    assert policy["status"] == "active"
    assert policy["history_budget"] == 5

    listed = app_client.get(f"{API}/budget-policies", params={"project_id": project_id})
    assert listed.status_code == 200, listed.text
    assert [p["id"] for p in listed.json()] == [policy["id"]]

    got = app_client.get(f"{API}/budget-policies/{policy['id']}")
    assert got.status_code == 200, got.text

    patched = app_client.patch(f"{API}/budget-policies/{policy['id']}", json={
        "name": "policy-one-renamed", "status": "archived",
    })
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "policy-one-renamed"
    assert patched.json()["status"] == "archived"

    assert app_client.get(
        f"{API}/budget-policies", params={"project_id": project_id}
    ).json() == []
    assert len(app_client.get(
        f"{API}/budget-policies",
        params={"project_id": project_id, "include_archived": True},
    ).json()) == 1

    second = app_client.post(f"{API}/budget-policies", json={
        "project_id": project_id, "name": "policy-two", "max_context_tokens": 50,
    }).json()

    async def _resolve():
        by_id = await context_lab.resolve_budget_policy(owner, project_id, second["id"])
        latest = await context_lab.resolve_budget_policy(owner, project_id)
        return by_id, latest

    by_id, latest = app_client.portal.call(_resolve)
    assert by_id["id"] == second["id"]
    assert latest["id"] == second["id"]

    bad_status = app_client.post(f"{API}/budget-policies", json={
        "project_id": project_id, "name": "bad", "status": "bogus",
    })
    assert bad_status.status_code == 400, bad_status.text

    pack = _pack(app_client, project_id)
    bad_item = app_client.post(f"{API}/packs/{pack['id']}/versions", json={
        "items": [{"key": "x", "source_type": "BOGUS", "content": "c"}],
    })
    assert bad_item.status_code == 400, bad_item.text


# ================================================================== 5
def test_preview_enforces_budget_and_never_drops_mandatory(
    app_client, as_user, monkeypatch
):
    project_id = _project(app_client)
    policy = app_client.post(f"{API}/budget-policies", json={
        "project_id": project_id,
        "name": "tiny-window",
        "max_context_tokens": 200,
        "reserve_output_tokens": 10,
    }).json()

    messages = []
    for index in range(40):
        messages.append({"role": "user", "content": f"question {index} " + "x" * 200})
        messages.append({"role": "assistant", "content": f"answer {index} " + "y" * 200})
    messages.append({"role": "user", "content": "final task"})

    def _no_adapter(provider):
        raise AssertionError("preview must not call a model provider")

    monkeypatch.setattr(providers_module, "get_adapter", _no_adapter)

    r = app_client.post(f"{API}/preview", json={
        "project_id": project_id,
        "system_prompt": "you are a careful assistant",
        "messages": messages,
        "budget_policy_id": policy["id"],
        "retrieved_context": [
            {"component_type": "POLICY", "content": "never leak secrets"},
            {"component_type": "INVARIANT", "content": "owner isolation is mandatory"},
        ],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["budget_policy"]["id"] == policy["id"]
    assert body["applied_max_context_tokens"] == 200
    assert body["applied_reserved_output_tokens"] == 10

    assembly = body["assembly"]
    assert assembly["budget_policy_applied"]["id"] == policy["id"]
    assert assembly["over_budget"] is True or assembly["budget_excluded_count"] > 0

    included_types = {c["component_type"] for c in assembly["components"]}
    assert {"SYSTEM", "USER_TASK", "POLICY", "INVARIANT"} <= included_types

    budget_excluded = [
        c for c in assembly["excluded_components"] if c.get("excluded_reason") == "budget"
    ]
    assert budget_excluded, assembly["excluded_components"]
    for component in budget_excluded:
        assert component["component_type"] not in _MANDATORY

    # Per-source budgets are enforced independently of the total budget: a
    # tiny history_budget drops history even when the overall window is large.
    source_policy = app_client.post(f"{API}/budget-policies", json={
        "project_id": project_id,
        "name": "history-cap",
        "max_context_tokens": 1_000_000,
        "history_budget": 1,
    }).json()
    source_preview = app_client.post(f"{API}/preview", json={
        "project_id": project_id,
        "system_prompt": "system",
        "messages": messages,
        "budget_policy_id": source_policy["id"],
    })
    assert source_preview.status_code == 200, source_preview.text
    source_assembly = source_preview.json()["assembly"]
    assert source_assembly["over_budget"] is False
    excluded_types = {
        c["component_type"]
        for c in source_assembly["excluded_components"]
        if c.get("excluded_reason") == "budget"
    }
    assert "CONVERSATION_HISTORY" in excluded_types
    assert "SYSTEM" in {c["component_type"] for c in source_assembly["components"]}


# ================================================================== 6
def test_assembly_inspector_reports_snapshot_and_absence(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client)
    run_id = new_id()
    secret = "sk-proj-abcdefghijklmnop1234"
    _insert_run_and_snapshot(app_client, owner, project_id, run_id, [
        _component("SYSTEM", f"system prompt {secret}", ordering=0, estimated_tokens=7),
        _component("USER_TASK", "do the thing", ordering=10, estimated_tokens=3),
    ])

    r = app_client.get(f"{API}/runs/{run_id}/assembly")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["snapshot_reference"] == {"id": f"snap-{run_id}", "available": True}
    assert body["snapshot"]["id"] == f"snap-{run_id}"

    types = {c["component_type"] for c in body["components"]}
    assert {"SYSTEM", "USER_TASK"} <= types
    system_component = next(
        c for c in body["components"] if c["component_type"] == "SYSTEM"
    )
    assert system_component["source_class"] == "SYSTEM"
    # Unknown token telemetry stays unknown, never 0.
    assert system_component["token_count"] is None
    # Content is redacted on the way out.
    assert secret not in system_component["content_excerpt"]
    assert "[REDACTED]" in system_component["content_excerpt"]

    source_classes = {entry["source_class"] for entry in body["sources_summary"]}
    assert {"SYSTEM", "MANUAL"} <= source_classes
    system_summary = next(
        entry for entry in body["sources_summary"] if entry["source_class"] == "SYSTEM"
    )
    assert system_summary["count"] == 1 and system_summary["included_count"] == 1

    no_snapshot_run = new_id()
    _insert_run_and_snapshot(
        app_client, owner, project_id, no_snapshot_run, [], snapshot=False
    )
    absent = app_client.get(f"{API}/runs/{no_snapshot_run}/assembly")
    assert absent.status_code == 200, absent.text
    assert absent.json()["available"] is False
    assert absent.json()["reason"] == "no_context_snapshot"


# ================================================================== 7
def test_run_context_diff_available_and_missing(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client)
    run_a = new_id()
    run_b = new_id()
    run_c = new_id()

    _insert_run_and_snapshot(app_client, owner, project_id, run_a, [
        _component("SYSTEM", "system", ordering=0),
    ])
    _insert_run_and_snapshot(app_client, owner, project_id, run_b, [
        _component("SYSTEM", "system", ordering=0),
        _component("USER_TASK", "task", ordering=10),
    ])
    _insert_run_and_snapshot(
        app_client, owner, project_id, run_c, [], snapshot=False
    )

    r = app_client.get(f"{API}/runs/diff", params={"run_a": run_a, "run_b": run_b})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["run_a"] == run_a and body["run_b"] == run_b
    assert body["diff"]["component_diffs"]["USER_TASK"]["status"] == "added"

    missing = app_client.get(
        f"{API}/runs/diff", params={"run_a": run_a, "run_b": run_c}
    )
    assert missing.status_code == 200, missing.text
    assert missing.json()["available"] is False
    assert missing.json()["reason"] == "no_context_snapshot"


# ================================================================== 8
def test_owner_isolation_and_foreign_project(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client)
    pack = _pack(app_client, project_id)
    version = _version(app_client, pack["id"], [_item("a", "content")])
    policy = app_client.post(f"{API}/budget-policies", json={
        "project_id": project_id, "name": "owned-policy",
    }).json()
    run_id = new_id()
    _insert_run_and_snapshot(app_client, owner, project_id, run_id, [
        _component("SYSTEM", "system"),
    ])

    foreign = _foreign_user()
    foreign_project_id = new_id()

    async def _seed_foreign_project():
        await repo.projects.insert({
            "id": foreign_project_id,
            "owner_id": foreign["id"],
            "name": "foreign project",
            "description": "",
            "tags": [],
            "status": "active",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })

    async def _cleanup_foreign_project():
        await repo.projects.delete(foreign_project_id, foreign["id"])

    app_client.portal.call(_seed_foreign_project)
    try:
        # A project this owner does not own is rejected as a domain error.
        assert app_client.post(f"{API}/packs", json={
            "project_id": foreign_project_id, "name": "nope",
        }).status_code == 400
        assert app_client.post(f"{API}/budget-policies", json={
            "project_id": foreign_project_id, "name": "nope",
        }).status_code == 400

        original = _swap(app_client, foreign)
        try:
            assert app_client.get(f"{API}/packs/{pack['id']}").status_code == 404
            assert app_client.patch(
                f"{API}/packs/{pack['id']}", json={"name": "x"}
            ).status_code == 404
            assert app_client.delete(f"{API}/packs/{pack['id']}").status_code == 404
            assert app_client.get(
                f"{API}/packs/{pack['id']}/versions"
            ).status_code == 404
            assert app_client.get(
                f"{API}/packs/{pack['id']}/versions/{version['id']}"
            ).status_code == 404
            assert app_client.get(
                f"{API}/budget-policies/{policy['id']}"
            ).status_code == 404
            assert app_client.get(
                f"{API}/runs/{run_id}/assembly"
            ).status_code == 404
        finally:
            _restore(app_client, original)
    finally:
        app_client.portal.call(_cleanup_foreign_project)

    assert app_client.get(f"{API}/packs/{pack['id']}").status_code == 200
    assert app_client.get(f"{API}/runs/{run_id}/assembly").status_code == 200


# ================================================================== 9
def test_preview_without_policy_is_additive_baseline(app_client, as_user):
    owner = as_user["id"]

    async def _assemble(budget_policy=None):
        return context_service.assemble_context(
            owner,
            system_prompt="system",
            messages=[{"role": "user", "content": "task"}],
            budget_policy=budget_policy,
        )

    baseline = app_client.portal.call(_assemble)
    assert baseline.get("budget_policy_applied") is None
    assert baseline.get("budget_excluded_count", 0) == 0
    assert {c["component_type"] for c in baseline["components"]} == {
        "SYSTEM", "USER_TASK",
    }
    # source_class is annotated additively on every component.
    assert all(c["source_class"] for c in baseline["components"])
    assert all(c["source_class"] for c in baseline["excluded_components"])

    with_policy = app_client.portal.call(
        _assemble, {"max_context_tokens": 1}
    )
    assert with_policy["budget_policy_applied"] == {"max_context_tokens": 1}
    assert with_policy["over_budget"] is True
    assert "mandatory_components_exceed_budget" in with_policy["warnings"]
    # Mandatory components are never dropped.
    assert {c["component_type"] for c in with_policy["components"]} == {
        "SYSTEM", "USER_TASK",
    }
    assert with_policy["budget_excluded_count"] == 0
