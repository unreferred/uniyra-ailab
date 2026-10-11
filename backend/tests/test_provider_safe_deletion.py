"""Provider safe-deletion lifecycle tests (FIX-02).

Covers:
 1. Deleting a provider with zero, one, and multiple linked models succeeds.
 2. Deletion is a tombstone: provider record and linked models survive with
    preserved identity; historical runs/messages remain readable.
 3. Deleted providers cannot execute (PROVIDER_UNAVAILABLE) and keep no
    decryptable API key material.
 4. Active lists/selectors exclude tombstoned providers and their models.
 5. Repeated delete is idempotent; cross-owner delete is impossible.
 6. Other providers remain unaffected.
"""
from __future__ import annotations

import uuid

from conftest import call


API = "/api"


def _provider(client=None, name="OpenRouter", api_key="sk-test-safe-delete-0001"):
    r = client.post(f"{API}/providers", json={
        "name": name, "provider_type": "openai_compatible",
        "base_url": "https://93.184.216.34/v1", "api_key": api_key,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _model(client, provider_id, identifier="m-1", display="Model 1"):
    r = client.post(f"{API}/models", json={
        "provider_id": provider_id, "display_name": display,
        "model_identifier": identifier,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_delete_provider_with_zero_models(app_client, as_user):
    p = _provider(app_client)
    r = app_client.delete(f"{API}/providers/{p['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert p["id"] not in {x["id"] for x in app_client.get(f"{API}/providers").json()}


def test_delete_provider_with_linked_models_deactivates_them(app_client, as_user):
    p = _provider(app_client, api_key="sk-test-safe-delete-0002")
    m1 = _model(app_client, p["id"], "m-a", "Model A")
    m2 = _model(app_client, p["id"], "m-b", "Model B")
    r = app_client.delete(f"{API}/providers/{p['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["linked_models_deactivated"] == 2

    # Tombstone is stored (soft delete), never a hard row removal.
    from app import repository as repo
    doc = call(app_client, repo.providers.get, p["id"], as_user["id"])
    assert doc is not None
    assert doc["deleted_at"]
    assert doc["enabled"] is False
    # Key material is revoked; nothing decryptable is retained.
    assert not doc.get("api_key_enc")

    # Active lists exclude the deleted provider and its models.
    assert p["id"] not in {x["id"] for x in app_client.get(f"{API}/providers").json()}
    remaining_ids = {m["id"] for m in app_client.get(f"{API}/models").json()}
    assert m1["id"] not in remaining_ids
    assert m2["id"] not in remaining_ids

    # Linked model registrations survive with preserved identity.
    m1_doc = call(app_client, repo.models.get, m1["id"], as_user["id"])
    assert m1_doc is not None
    assert m1_doc["display_name"] == "Model A"
    assert m1_doc["provider_id"] == p["id"]
    assert m1_doc["deleted_at"]
    assert m1_doc["enabled"] is False


def test_deleted_provider_cannot_execute(app_client, as_user):
    p = _provider(app_client, api_key="sk-test-safe-delete-0003")
    m = _model(app_client, p["id"], "m-exec", "Exec Model")
    r = app_client.delete(f"{API}/providers/{p['id']}")
    assert r.status_code == 200
    # Direct execution through the canonical funnel fails gracefully.
    r2 = app_client.post(f"{API}/models/{m['id']}/test")
    assert r2.status_code == 400
    assert "Provider not found" in r2.json()["detail"]
    # Registry lookup paths treat tombstones as gone.
    assert app_client.get(f"{API}/providers/{p['id']}/models").status_code == 404
    assert app_client.post(f"{API}/providers/{p['id']}/test").status_code == 404
    assert app_client.patch(f"{API}/providers/{p['id']}", json={"name": "x"}).status_code == 404


def test_execution_resolves_deleted_provider_as_unavailable(app_client, as_user):
    from app.services.execution import execute_model
    from app.services import repo as repo_ns
    p = _provider(app_client, api_key="sk-test-safe-delete-0004")
    m = _model(app_client, p["id"], "m-funnel", "Funnel Model")
    app_client.delete(f"{API}/providers/{p['id']}")
    run = call(app_client, execute_model, as_user["id"], m["id"],
               "You are a test.", [{"role": "user", "content": "hi"}], {})
    assert run["status"] == "error"
    assert run["error"]["code"] == "PROVIDER_UNAVAILABLE"
    # The attempted run is still persisted (never silently dropped).
    stored = call(app_client, repo_ns.runs.get, run["id"], as_user["id"])
    assert stored is not None


def test_repeated_delete_is_idempotent(app_client, as_user):
    p = _provider(app_client, api_key="sk-test-safe-delete-0005")
    assert app_client.delete(f"{API}/providers/{p['id']}").status_code == 200
    r = app_client.delete(f"{API}/providers/{p['id']}")
    assert r.status_code == 200
    assert r.json().get("already_deleted") is True


def test_cross_owner_delete_cannot_touch_other_owner(app_client, as_user):
    p = _provider(app_client, api_key="sk-test-safe-delete-0006")
    other = dict(as_user)
    other["id"] = f"other-owner-{uuid.uuid4().hex}"
    from app.deps import get_current_user
    app_client.app.dependency_overrides[get_current_user] = lambda: other
    try:
        r = app_client.delete(f"{API}/providers/{p['id']}")
        assert r.status_code == 404
    finally:
        app_client.app.dependency_overrides[get_current_user] = lambda: as_user
    # Original owner still sees their provider intact.
    assert p["id"] in {x["id"] for x in app_client.get(f"{API}/providers").json()}


def test_other_providers_unaffected_by_deletion(app_client, as_user):
    p_keep = _provider(app_client, name="Keep", api_key="sk-test-safe-delete-0007")
    p_del = _provider(app_client, name="Del", api_key="sk-test-safe-delete-0008")
    m_keep = _model(app_client, p_keep["id"], "m-keep", "Keep Model")
    _model(app_client, p_del["id"], "m-del", "Del Model")
    assert app_client.delete(f"{API}/providers/{p_del['id']}").status_code == 200
    providers = {x["id"]: x for x in app_client.get(f"{API}/providers").json()}
    assert p_keep["id"] in providers
    assert p_del["id"] not in providers
    models = {m["id"] for m in app_client.get(f"{API}/models").json()}
    assert m_keep["id"] in models


def test_history_readable_after_provider_deletion(app_client, as_user):
    from app import repository as repo

    p = _provider(app_client, api_key="sk-test-safe-delete-0009")
    m = _model(app_client, p["id"], "m-hist", "Hist Model")
    project = app_client.post(f"{API}/projects", json={"name": "hist"}).json()
    conv = app_client.post(f"{API}/conversations", json={
        "project_id": project["id"], "default_model_id": m["id"],
    }).json()
    run_doc = {
        "id": uuid.uuid4().hex, "owner_id": as_user["id"],
        "project_id": project["id"], "conversation_id": conv["id"],
        "provider_id": p["id"], "provider_name": p["name"],
        "provider_type": p["provider_type"],
        "model_id": m["id"], "model_name": m["display_name"],
        "model_identifier": m["model_identifier"],
        "status": "success", "created_at": "2026-01-01T00:00:00+00:00",
    }
    call(app_client, repo.runs.insert, run_doc)
    app_client.delete(f"{API}/providers/{p['id']}")
    # Historical run remains fully readable; snapshots carry the identity.
    stored = call(app_client, repo.runs.get, run_doc["id"], as_user["id"])
    assert stored is not None
    assert stored["provider_name"] == p["name"]
    assert stored["model_name"] == m["display_name"]
    # The conversation itself is untouched.
    conv_doc = call(app_client, repo.conversations.get, conv["id"], as_user["id"])
    assert conv_doc is not None
    assert conv_doc["default_model_id"] == m["id"]
