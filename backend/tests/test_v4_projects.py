"""OmniLab V4 project defaults + conversation inheritance (offline API tests).

Covers: project create with defaults, PATCH /settings round-trip, conversation
inheritance of project defaults, request-level override precedence,
cross-project file attach rejection, and legacy-project backward compatibility.
"""
from __future__ import annotations

import pytest

from app import config, repository as repo
from app.services import project_manager as project_manager_service

from _support import create_model, create_provider


"""Phase 4 collections purge for V4 suites (purge_owner predates them)."""

_V4_PHASE4_COLLECTIONS = ["project_files", "knowledge_collections", "knowledge_documents", "knowledge_chunks", "tool_versions", "tool_credentials", "code_executions", "project_activity"]


@pytest.fixture(autouse=True)
def _v4_purge_phase4(app_client, as_user):
    """Purge Phase 4 collections (not covered by _support.purge_owner)."""
    yield

    async def _do():
        from app.database import db

        for coll in _V4_PHASE4_COLLECTIONS:
            await db[coll].delete_many({"owner_id": as_user["id"]})

    app_client.portal.call(_do)



@pytest.fixture
def _tmp_storage(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(config, "ATTACHMENT_STORAGE_DIR", str(root))
    return root


def _create_project(app_client, **payload):
    body = {"name": "v4-project"}
    body.update(payload)
    r = app_client.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _create_model(app_client, owner):
    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(owner, provider_id=provider_id)

    return app_client.portal.call(_setup)


def _create_http_tool(app_client, project_id, name):
    body = {
        "project_id": project_id,
        "name": name,
        "version": {
            "tool_type": "http",
            "input_schema": {"type": "object"},
            "permissions": [],
            "implementation_config": {
                "method": "GET",
                "url_template": "https://93.184.216.34/v1/items",
            },
        },
    }
    r = app_client.post("/api/tools", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _create_collection(app_client, project_id, name):
    r = app_client.post(
        "/api/knowledge", json={"project_id": project_id, "name": name}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_project_create_with_defaults(app_client, as_user):
    owner = as_user["id"]
    model_id = _create_model(app_client, owner)
    project = _create_project(
        app_client,
        default_model_id=model_id,
        default_system_instruction="Be concise.",
        default_temperature=0.2,
        default_max_tokens=512,
    )
    assert project["default_model_id"] == model_id
    assert project["default_system_instruction"] == "Be concise."
    assert project["default_temperature"] == 0.2
    assert project["default_max_tokens"] == 512


def test_settings_round_trip_persists(app_client, as_user):
    owner = as_user["id"]
    model_id = _create_model(app_client, owner)
    project = _create_project(app_client)
    patch = {
        "default_model_id": model_id,
        "default_system_instruction": "Answer briefly.",
        "default_temperature": 0.3,
        "default_top_p": 0.9,
        "default_context_strategy": "RECENT_MESSAGES",
        "default_retrieval_options": {"top_k": 3},
    }
    r = app_client.patch(
        f"/api/projects/{project['id']}/settings", json=patch
    )
    assert r.status_code == 200, r.text
    for key, value in patch.items():
        assert r.json()[key] == value
    fetched = app_client.get(f"/api/projects/{project['id']}")
    assert fetched.status_code == 200, fetched.text
    for key, value in patch.items():
        assert fetched.json()[key] == value


def test_conversation_inherits_project_defaults(app_client, as_user):
    owner = as_user["id"]
    model_id = _create_model(app_client, owner)
    project = _create_project(app_client)
    tool = _create_http_tool(app_client, project["id"], "inherit_tool")
    collection = _create_collection(app_client, project["id"], "inherit-kb")
    settings = {
        "default_model_id": model_id,
        "default_system_instruction": "Project voice.",
        "default_temperature": 0.4,
        "default_enabled_tool_ids": [tool["id"]],
        "default_knowledge_collection_ids": [collection["id"]],
    }
    r = app_client.patch(
        f"/api/projects/{project['id']}/settings", json=settings
    )
    assert r.status_code == 200, r.text

    r = app_client.post("/api/conversations", json={"project_id": project["id"]})
    assert r.status_code == 200, r.text
    conv = r.json()
    assert conv["system_prompt"] == "Project voice."
    assert conv["default_model_id"] == model_id
    assert conv["temperature"] == 0.4
    assert conv["enabled_tool_ids"] == [tool["id"]]
    assert conv["knowledge_collection_ids"] == [collection["id"]]

    fetched = app_client.get(f"/api/conversations/{conv['id']}")
    assert fetched.status_code == 200, fetched.text
    detail = fetched.json()
    assert detail["system_prompt"] == "Project voice."
    assert detail["default_model_id"] == model_id
    assert tool["id"] in detail["enabled_tool_ids"]
    assert collection["id"] in detail["knowledge_collection_ids"]


def test_conversation_request_override_wins(app_client, as_user):
    project = _create_project(
        app_client,
        default_system_instruction="Project voice.",
        default_temperature=0.7,
    )
    r = app_client.post(
        "/api/conversations",
        json={
            "project_id": project["id"],
            "system_prompt": "Request voice.",
            "temperature": 0.1,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["system_prompt"] == "Request voice."
    assert r.json()["temperature"] == 0.1

    # Layered precedence, unit level: request > conversation > project > default.
    resolved = project_manager_service.resolve_effective_config(
        {"default_system_instruction": "P", "default_temperature": 0.7},
        {"system_prompt": "C", "temperature": 0.5},
        {"system_prompt": "R"},
    )
    assert resolved["effective"]["system_prompt"] == "R"
    assert resolved["sources"]["system_prompt"] == "request"
    assert resolved["effective"]["temperature"] == 0.5
    assert resolved["sources"]["temperature"] == "conversation"


def test_cross_project_file_attach_rejected(app_client, as_user, _tmp_storage):
    project_a = _create_project(app_client, name="proj-a")
    project_b = _create_project(app_client, name="proj-b")
    r = app_client.post(
        f"/api/projects/{project_a['id']}/files/upload",
        files={
            "files": (
                "note.txt",
                b"cross-project isolation probe content",
                "text/plain",
            )
        },
    )
    assert r.status_code == 200, r.text
    file_a = r.json()["files"][0]
    collection_b = _create_collection(app_client, project_b["id"], "coll-b")
    r = app_client.post(
        f"/api/knowledge/{collection_b['id']}/documents",
        json={"project_file_ids": [file_a["id"]]},
    )
    assert 400 <= r.status_code < 500, r.text


def test_legacy_project_without_defaults_still_converses(app_client, as_user):
    owner = as_user["id"]

    async def _insert_legacy():
        from app.models import new_id

        legacy_id = new_id()
        await repo.projects.insert(
            {
                "id": legacy_id,
                "owner_id": owner,
                "name": "legacy",
                "description": "",
                "tags": [],
                "status": "active",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
        return legacy_id

    legacy_id = app_client.portal.call(_insert_legacy)
    r = app_client.post(
        "/api/conversations",
        json={"project_id": legacy_id, "title": "legacy conv"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == legacy_id


def test_conversation_inherits_context_strategy(app_client, as_user):
    """Omitted context_strategy inherits the project default (not model FULL)."""
    project = _create_project(app_client)
    r = app_client.patch(
        f"/api/projects/{project['id']}/settings",
        json={"default_context_strategy": "RECENT_MESSAGES"},
    )
    assert r.status_code == 200, r.text
    r = app_client.post("/api/conversations", json={"project_id": project["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["context_strategy"] == "RECENT_MESSAGES"
    r = app_client.post(
        "/api/conversations",
        json={"project_id": project["id"], "context_strategy": "FULL"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["context_strategy"] == "FULL"
    r = app_client.post(
        "/api/conversations",
        json={"project_id": project["id"], "context_strategy": "NOPE"},
    )
    assert r.status_code == 400, r.text
