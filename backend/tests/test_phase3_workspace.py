"""Phase 3 Workspace integration: attachments through the HTTP message path,
multimodal image injection, vision prechecks, tool-enabled chat and the
conversation history contract for content blocks / legacy text.
"""
from __future__ import annotations

import base64

import pytest

from app import config
from app import providers as providers_module
from app import repository as repo
from app import services
from app.models import new_id, now_iso
from app.services import attachments as attachments_module
from app.services import context as context_service
from app.services import execution as execution_module
from app.services import tools as tools_service
from app.storage.attachments import FilesystemAttachmentStorage

from _support import (
    CapableAdapter,
    create_model,
    create_project,
    create_provider,
    result_success,
    result_with_tools,
)

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
CALCULATOR_ID = "tool-calculator-0001"


def _install(monkeypatch, executor, *, tools=False, vision=False, streamer=None):
    factory = lambda provider: CapableAdapter(  # noqa: E731
        executor, streamer, tools=tools, vision=vision
    )
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


def _ensure_calculator(app_client):
    async def _do():
        await tools_service.ensure_builtin_tools()
        await repo.tools.update(CALCULATOR_ID, None, {"enabled": True})

    app_client.portal.call(_do)


@pytest.fixture
def ws_env(app_client, as_user, monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    root.mkdir()
    storage = FilesystemAttachmentStorage(root=str(root))
    monkeypatch.setattr(attachments_module, "get_attachment_storage", lambda: storage)
    monkeypatch.setattr(config, "ATTACHMENT_STORAGE_DIR", str(root))
    owner = as_user["id"]
    project_id = app_client.portal.call(create_project, owner)
    return {"owner": owner, "project_id": project_id, "storage": storage}


def _model(app_client, owner, *, capabilities):
    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(owner, provider_id=provider_id, capabilities=capabilities)

    return app_client.portal.call(_setup)


def _upload(app_client, project_id, filename, data, mime):
    r = app_client.post(
        "/api/attachments",
        data={"project_id": project_id},
        files={"files": (filename, data, mime)},
    )
    assert r.status_code == 200, r.text
    return r.json()["attachments"][0]


def _conversation(app_client, project_id, **extra):
    body = {"project_id": project_id}
    body.update(extra)
    r = app_client.post("/api/conversations", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_send_message_with_text_attachment(app_client, ws_env, monkeypatch):
    owner = ws_env["owner"]
    project_id = ws_env["project_id"]
    model_id = _model(app_client, owner, capabilities={})
    attachment = _upload(app_client, project_id, "notes.txt", b"the secret attachment body", "text/plain")
    conversation = _conversation(app_client, project_id)

    recorded = []

    def executor(model, req):
        recorded.append(req)
        return result_success("summary")

    _install(monkeypatch, executor)

    r = app_client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={
            "model_id": model_id,
            "content": "please read this",
            "attachment_ids": [attachment["id"]],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    user_message = body["user_message"]
    assistant_message = body["assistant_message"]
    run = body["run"]

    assert run["status"] == "success"
    assert user_message["attachment_ids"] == [attachment["id"]]
    refs = [b for b in user_message["content_blocks"] if b.get("type") == "attachment_ref"]
    assert refs and refs[0]["attachment_id"] == attachment["id"]
    assert assistant_message["trace_id"] == run["trace_id"]
    assert assistant_message["run_id"] == run["id"]

    assert recorded
    last_user = [m for m in recorded[-1].messages if m["role"] == "user"][-1]
    assert isinstance(last_user["content"], str)
    assert "the secret attachment body" in last_user["content"]

    async def _snapshot():
        return await context_service.get_context_snapshot(owner, run["id"])

    snapshot = app_client.portal.call(_snapshot)
    attachments = [c for c in snapshot["components"] if c["component_type"] == "ATTACHMENTS"]
    assert len(attachments) == 1
    assert attachments[0]["source_ref"] == attachment["id"]
    assert attachments[0]["metadata"]["attachment_id"] == attachment["id"]


def test_send_message_with_image_uses_vision_model(app_client, ws_env, monkeypatch):
    owner = ws_env["owner"]
    project_id = ws_env["project_id"]
    model_id = _model(app_client, owner, capabilities={"vision": True})
    attachment = _upload(app_client, project_id, "pixel.png", PNG_1X1, "image/png")
    conversation = _conversation(app_client, project_id)

    recorded = []

    def executor(model, req):
        recorded.append(req)
        return result_success("a single pixel")

    _install(monkeypatch, executor, vision=True)

    r = app_client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={
            "model_id": model_id,
            "content": "describe this image",
            "attachment_ids": [attachment["id"]],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run"]["status"] == "success"

    last_user = [m for m in recorded[-1].messages if m["role"] == "user"][-1]
    assert isinstance(last_user["content"], list)
    image_blocks = [b for b in last_user["content"] if b.get("type") == "image"]
    assert image_blocks
    assert image_blocks[0]["mime_type"] == "image/png"
    assert image_blocks[0]["attachment_id"] == attachment["id"]
    assert image_blocks[0]["data_base64"]


def test_image_attachment_on_non_vision_model_is_rejected(app_client, ws_env, monkeypatch):
    owner = ws_env["owner"]
    project_id = ws_env["project_id"]
    model_id = _model(app_client, owner, capabilities={})
    attachment = _upload(app_client, project_id, "pixel.png", PNG_1X1, "image/png")
    conversation = _conversation(app_client, project_id)
    _install(monkeypatch, lambda model, req: result_success("nope"), vision=False)

    r = app_client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={
            "model_id": model_id,
            "content": "describe this image",
            "attachment_ids": [attachment["id"]],
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"].lower()
    assert "vision" in detail or "image" in detail


def test_send_message_with_enabled_tool(app_client, ws_env, monkeypatch):
    owner = ws_env["owner"]
    project_id = ws_env["project_id"]
    _ensure_calculator(app_client)
    model_id = _model(app_client, owner, capabilities={"tools": True})
    conversation = _conversation(app_client, project_id)

    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(
                tool_calls=[{
                    "id": "call-1",
                    "name": "calculator",
                    "arguments": {"expression": "2+2"},
                }]
            )
        return result_success("The answer is 4")

    _install(monkeypatch, executor, tools=True)

    r = app_client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={
            "model_id": model_id,
            "content": "what is 2+2?",
            "enabled_tool_ids": [CALCULATOR_ID],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run"]["status"] == "success"
    assert body["assistant_message"]["tool_call_count"] >= 1

    async def _calls():
        return await tools_service.list_tool_calls(owner, run_id=body["run"]["id"])

    calls = app_client.portal.call(_calls)
    assert calls
    assert calls[0]["execution_status"] == "success"
    assert calls[0]["output"]["result"] == 4


def test_conversation_history_renders_attachments_and_legacy_text(app_client, ws_env, monkeypatch):
    owner = ws_env["owner"]
    project_id = ws_env["project_id"]
    model_id = _model(app_client, owner, capabilities={})
    attachment = _upload(app_client, project_id, "notes.txt", b"attached content", "text/plain")
    conversation = _conversation(app_client, project_id)

    _install(monkeypatch, lambda model, req: result_success("ok"))
    r = app_client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={
            "model_id": model_id,
            "content": "read this",
            "attachment_ids": [attachment["id"]],
        },
    )
    assert r.status_code == 200, r.text

    async def _insert_legacy():
        await repo.messages.insert({
            "id": new_id(),
            "owner_id": owner,
            "conversation_id": conversation["id"],
            "project_id": project_id,
            "role": "user",
            "content": "legacy hello",
            "created_at": now_iso(),
        })

    app_client.portal.call(_insert_legacy)

    detail = app_client.get(f"/api/conversations/{conversation['id']}").json()
    messages = detail["messages"]

    attachment_turn = [
        m for m in messages if m["role"] == "user" and m.get("attachment_ids")
    ][0]
    assert attachment_turn["attachment_ids"] == [attachment["id"]]
    block_types = [b.get("type") for b in attachment_turn.get("content_blocks") or []]
    assert "attachment_ref" in block_types

    legacy = [m for m in messages if m.get("content") == "legacy hello"]
    assert len(legacy) == 1
    assert legacy[0]["content"] == "legacy hello"
