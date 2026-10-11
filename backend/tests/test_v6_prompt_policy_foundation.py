"""V6.0 prompt / policy foundation API tests.

Covers first-class project-scoped Prompt Specs (the existing prompt store),
Prompt Blocks, immutable-by-version Project Policies and Project Invariants.
Everything runs offline against the shared ``app_client``/``as_user`` fixtures:
records belong to throwaway ``pytest-`` owners purged after each test and every
assertion exercises real persisted behavior. Ownership isolation is mandatory --
a foreign owner sees 404 for reads/writes and 400 when naming a foreign project.
"""
from __future__ import annotations

import uuid

from app.deps import get_current_user

API = "/api"


def _project(app_client, name="pytest-v6"):
    r = app_client.post(
        f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


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


def _policy(app_client, project_id, **overrides):
    payload = {
        "project_id": project_id,
        "name": f"policy-{uuid.uuid4().hex[:8]}",
        "domain": "SECURITY",
        "content": "v1",
    }
    payload.update(overrides)
    r = app_client.post(f"{API}/project-policies", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _invariant(app_client, project_id, **overrides):
    payload = {
        "project_id": project_id,
        "code": f"CODE-{uuid.uuid4().hex[:6]}",
        "domain": "SECURITY",
        "title": "Invariant",
    }
    payload.update(overrides)
    r = app_client.post(f"{API}/project-invariants", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================== 1
def test_prompt_spec_fields_persist_and_validate(app_client, as_user):
    owner = as_user["id"]
    project_id = _project(app_client, "v6-spec")

    created = app_client.post(f"{API}/prompts", json={
        "project_id": project_id,
        "name": "Bounded change spec",
        "description": "d",
        "purpose": "small safe edit",
        "status": "draft",
    })
    assert created.status_code == 200, created.text
    spec = created.json()
    assert spec["purpose"] == "small safe edit"
    assert spec["status"] == "draft"
    assert spec["created_by"] == owner

    got = app_client.get(f"{API}/prompts/{spec['id']}")
    assert got.status_code == 200, got.text
    assert got.json()["purpose"] == "small safe edit"
    assert got.json()["status"] == "draft"
    assert got.json()["created_by"] == owner

    patched = app_client.patch(f"{API}/prompts/{spec['id']}", json={"status": "deprecated"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "deprecated"

    invalid_create = app_client.post(f"{API}/prompts", json={
        "project_id": project_id, "name": "bad", "status": "bogus",
    })
    assert invalid_create.status_code == 400, invalid_create.text
    invalid_patch = app_client.patch(f"{API}/prompts/{spec['id']}", json={"status": "bogus"})
    assert invalid_patch.status_code == 400, invalid_patch.text

    original = _swap(app_client, _foreign_user())
    try:
        foreign = app_client.get(f"{API}/prompts/{spec['id']}")
        assert foreign.status_code == 404, foreign.text
    finally:
        _restore(app_client, original)


# ================================================================== 2
def test_prompt_blocks_crud_validation_and_starters(app_client, as_user):
    project_id = _project(app_client, "v6-blocks")

    catalog = app_client.get(f"{API}/prompt-blocks/catalog")
    assert catalog.status_code == 200, catalog.text
    categories = catalog.json()["categories"]
    keys = [entry["key"] for entry in categories]
    assert "bounded_scope" in keys and "preserve_security" in keys and "other" in keys
    assert all(entry["label"] and entry["description"] for entry in categories)
    assert set(catalog.json()["statuses"]) == {"active", "archived"}

    block = _block(app_client, project_id, category="bounded_scope", content="stay in scope")
    assert block["version"] == 1
    first_hash = block["content_hash"]

    patched = app_client.patch(f"{API}/prompt-blocks/{block['id']}", json={
        "content": "stay strictly in scope",
    })
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == 2
    assert patched.json()["content_hash"] != first_hash

    invalid = app_client.post(f"{API}/prompt-blocks", json={
        "project_id": project_id, "name": "bad", "category": "not_a_category",
    })
    assert invalid.status_code == 400, invalid.text

    security = _block(app_client, project_id, category="preserve_security")
    filtered = app_client.get(f"{API}/prompt-blocks", params={
        "project_id": project_id, "category": "preserve_security",
    })
    assert filtered.status_code == 200, filtered.text
    assert [item["id"] for item in filtered.json()] == [security["id"]]

    deleted = app_client.delete(f"{API}/prompt-blocks/{security['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"ok": True}
    assert app_client.get(f"{API}/prompt-blocks/{security['id']}").status_code == 404

    first = app_client.post(f"{API}/prompt-blocks/starters", json={"project_id": project_id})
    assert first.status_code == 200, first.text
    assert len(first.json()["created"]) == 9
    assert first.json()["existing"] == []

    second = app_client.post(f"{API}/prompt-blocks/starters", json={"project_id": project_id})
    assert second.status_code == 200, second.text
    assert second.json()["created"] == []
    assert len(second.json()["existing"]) == 9

    listed = app_client.get(f"{API}/prompt-blocks", params={"project_id": project_id})
    assert listed.status_code == 200, listed.text
    # the original bounded block + the 9 materialized starters
    assert len(listed.json()) == 10


# ================================================================== 3
def test_project_policies_versioning_immutability_and_listing(app_client, as_user):
    project_id = _project(app_client, "v6-policies")

    catalog = app_client.get(f"{API}/project-policies/catalog")
    assert catalog.status_code == 200, catalog.text
    assert any(entry["key"] == "SECURITY" for entry in catalog.json()["domains"])
    assert set(catalog.json()["statuses"]) == {"active", "superseded", "archived"}

    v1 = _policy(app_client, project_id, content="v1 content")
    assert v1["version"] == 1 and v1["status"] == "active"
    assert v1["family_id"] == v1["id"]

    v2_resp = app_client.post(f"{API}/project-policies/{v1['id']}/versions", json={
        "content": "v2 content", "description": "tighten",
    })
    assert v2_resp.status_code == 200, v2_resp.text
    v2 = v2_resp.json()
    assert v2["version"] == 2 and v2["status"] == "active"
    assert v2["family_id"] == v1["family_id"]
    assert v2["id"] != v1["id"]

    v1_after = app_client.get(f"{API}/project-policies/{v1['id']}")
    assert v1_after.status_code == 200, v1_after.text
    assert v1_after.json()["status"] == "superseded"
    assert v1_after.json()["content"] == "v1 content"

    default = app_client.get(f"{API}/project-policies", params={"project_id": project_id})
    assert default.status_code == 200, default.text
    assert [item["id"] for item in default.json()] == [v2["id"]]

    all_versions = app_client.get(f"{API}/project-policies", params={
        "project_id": project_id, "include_versions": True,
    })
    assert all_versions.status_code == 200, all_versions.text
    assert [item["version"] for item in all_versions.json()] == [2, 1]

    history = app_client.get(f"{API}/project-policies/{v1['id']}/versions")
    assert history.status_code == 200, history.text
    assert [item["version"] for item in history.json()] == [1, 2]

    invalid = app_client.post(f"{API}/project-policies", json={
        "project_id": project_id, "name": "bad", "domain": "NOT_A_DOMAIN",
    })
    assert invalid.status_code == 400, invalid.text

    other = _policy(app_client, project_id, content="one-off")
    archived = app_client.patch(f"{API}/project-policies/{other['id']}", json={"status": "archived"})
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"

    default_after = app_client.get(f"{API}/project-policies", params={"project_id": project_id})
    assert [item["id"] for item in default_after.json()] == [v2["id"]]

    invalid_status = app_client.patch(f"{API}/project-policies/{v2['id']}", json={"status": "bogus"})
    assert invalid_status.status_code == 400, invalid_status.text


# ================================================================== 4
def test_project_invariants_crud_and_constraints(app_client, as_user):
    project_id = _project(app_client, "v6-invariants")

    catalog = app_client.get(f"{API}/project-invariants/catalog")
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    assert any(entry["key"] == "DATA" for entry in body["domains"])
    assert set(body["severities"]) == {"info", "low", "medium", "high", "critical"}
    assert set(body["enforcement_modes"]) == {
        "informational", "prompt_enforced", "evaluation_enforced", "blocking",
    }
    assert set(body["statuses"]) == {"active", "archived"}

    inv = _invariant(
        app_client, project_id,
        code="no-secrets", domain="SECURITY", title="No secrets in prompts",
        severity="high", enforcement_mode="prompt_enforced",
    )
    assert inv["code"] == "NO-SECRETS"

    duplicate = app_client.post(f"{API}/project-invariants", json={
        "project_id": project_id, "code": "NO-SECRETS", "domain": "SECURITY",
        "title": "duplicate",
    })
    assert duplicate.status_code == 400, duplicate.text

    patched = app_client.patch(f"{API}/project-invariants/{inv['id']}", json={
        "severity": "critical", "enforcement_mode": "blocking",
    })
    assert patched.status_code == 200, patched.text
    assert patched.json()["severity"] == "critical"
    assert patched.json()["enforcement_mode"] == "blocking"

    invalid = app_client.patch(f"{API}/project-invariants/{inv['id']}", json={
        "enforcement_mode": "not_a_mode",
    })
    assert invalid.status_code == 400, invalid.text

    second = _invariant(
        app_client, project_id,
        code="tests-first", domain="TESTING", title="Tests first",
    )

    by_domain = app_client.get(f"{API}/project-invariants", params={
        "project_id": project_id, "domain": "SECURITY",
    })
    assert by_domain.status_code == 200, by_domain.text
    assert [item["id"] for item in by_domain.json()] == [inv["id"]]

    by_status = app_client.get(f"{API}/project-invariants", params={
        "project_id": project_id, "status": "active",
    })
    assert by_status.status_code == 200, by_status.text
    assert {item["id"] for item in by_status.json()} == {inv["id"], second["id"]}

    deleted = app_client.delete(f"{API}/project-invariants/{second['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"ok": True}
    assert app_client.get(f"{API}/project-invariants/{second['id']}").status_code == 404

    empty = app_client.get(f"{API}/project-invariants", params={
        "project_id": project_id, "domain": "TESTING",
    })
    assert empty.json() == []


# ================================================================== 5
def test_owner_isolation_for_all_v6_resources(app_client, as_user):
    project_id = _project(app_client, "v6-isolation")
    block = _block(app_client, project_id)
    policy = _policy(app_client, project_id)
    invariant = _invariant(app_client, project_id)

    original = _swap(app_client, _foreign_user())
    try:
        assert app_client.get(f"{API}/prompt-blocks/{block['id']}").status_code == 404
        assert app_client.patch(
            f"{API}/prompt-blocks/{block['id']}", json={"name": "stolen"}
        ).status_code == 404
        assert app_client.delete(f"{API}/prompt-blocks/{block['id']}").status_code == 404

        assert app_client.get(f"{API}/project-policies/{policy['id']}").status_code == 404
        assert app_client.get(
            f"{API}/project-policies/{policy['id']}/versions"
        ).status_code == 404
        assert app_client.post(
            f"{API}/project-policies/{policy['id']}/versions", json={"content": "x"}
        ).status_code == 404
        assert app_client.patch(
            f"{API}/project-policies/{policy['id']}", json={"status": "archived"}
        ).status_code == 404

        assert app_client.get(
            f"{API}/project-invariants/{invariant['id']}"
        ).status_code == 404
        assert app_client.patch(
            f"{API}/project-invariants/{invariant['id']}", json={"severity": "high"}
        ).status_code == 404
        assert app_client.delete(
            f"{API}/project-invariants/{invariant['id']}"
        ).status_code == 404
    finally:
        _restore(app_client, original)

    # The original owner still sees every resource untouched.
    assert app_client.get(f"{API}/prompt-blocks/{block['id']}").status_code == 200
    assert app_client.get(f"{API}/project-policies/{policy['id']}").status_code == 200
    assert app_client.get(f"{API}/project-invariants/{invariant['id']}").status_code == 200


# ================================================================== 6
def test_cross_project_creation_rejected_for_foreign_owner(app_client, as_user):
    project_id = _project(app_client, "v6-cross")

    original = _swap(app_client, _foreign_user())
    try:
        cases = (
            (f"{API}/prompts", {"name": "x"}),
            (f"{API}/prompt-blocks", {"name": "x"}),
            (f"{API}/project-policies", {"name": "x", "domain": "SECURITY"}),
            (f"{API}/project-invariants", {"code": "X", "domain": "SECURITY", "title": "x"}),
        )
        for path, payload in cases:
            r = app_client.post(path, json={**payload, "project_id": project_id})
            assert r.status_code == 400, (path, r.text)

        assert app_client.get(
            f"{API}/prompt-blocks", params={"project_id": project_id}
        ).status_code == 400
        assert app_client.get(
            f"{API}/project-policies", params={"project_id": project_id}
        ).status_code == 400
        assert app_client.get(
            f"{API}/project-invariants", params={"project_id": project_id}
        ).status_code == 400
        assert app_client.post(
            f"{API}/prompt-blocks/starters", json={"project_id": project_id}
        ).status_code == 400
    finally:
        _restore(app_client, original)
