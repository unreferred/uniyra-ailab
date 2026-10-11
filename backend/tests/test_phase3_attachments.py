"""Phase 3 attachment ingestion: validation, storage safety, public
projection, context integration, immutability and vision gating.

All work is offline: bytes are written under a per-test ``tmp_path`` storage
root and execution uses a fake adapter installed into every ``get_adapter``
seam.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from app import config
from app import providers as providers_module
from app import services
from app.services import attachments as attachments_module
from app.services import context as context_module
from app.services import execution as execution_module
from app.services.attachments import (
    AttachmentError,
    AttachmentLimitError,
    UnsupportedAttachmentError,
)
from app.storage.attachments import FilesystemAttachmentStorage

from _support import (
    CapableAdapter,
    create_model,
    create_project,
    create_provider,
    result_success,
)

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _install(monkeypatch, executor, *, tools=False, vision=False, streamer=None):
    factory = lambda provider: CapableAdapter(  # noqa: E731
        executor, streamer, tools=tools, vision=vision
    )
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


@pytest.fixture
def attachment_env(app_client, as_user, monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    root.mkdir()
    storage = FilesystemAttachmentStorage(root=str(root))
    monkeypatch.setattr(attachments_module, "get_attachment_storage", lambda: storage)
    monkeypatch.setattr(config, "ATTACHMENT_STORAGE_DIR", str(root))
    return {"owner": as_user["id"], "storage": storage, "root": root}


def _create(app_client, owner, project_id, filename, data, declared_mime):
    async def _do():
        return await attachments_module.create_attachment(
            owner,
            project_id,
            filename=filename,
            data=data,
            declared_mime=declared_mime,
        )

    return app_client.portal.call(_do)


def _raw_doc(app_client, owner, attachment_id):
    async def _do():
        return await attachments_module.get_attachment_doc(owner, attachment_id)

    return app_client.portal.call(_do)


def test_upload_text_attachment_via_router(app_client, attachment_env):
    owner = attachment_env["owner"]
    project_id = app_client.portal.call(create_project, owner)

    r = app_client.post(
        "/api/attachments",
        data={"project_id": project_id},
        files={"files": ("notes.txt", b"hello from file", "text/plain")},
    )
    assert r.status_code == 200, r.text
    doc = r.json()["attachments"][0]
    assert doc["kind"] == "text"
    assert doc["extraction_status"] == "extracted"
    assert doc["processing_status"] == "processed"
    assert doc["sha256"] == hashlib.sha256(b"hello from file").hexdigest()
    assert doc["text_preview"] == "hello from file"
    assert doc["has_extracted_text"] is True
    assert "storage_ref" not in doc
    assert doc["owner_id"] == owner
    assert doc["project_id"] == project_id


def test_upload_image_extracts_metadata_and_hides_storage_ref(app_client, attachment_env):
    owner = attachment_env["owner"]
    root = attachment_env["root"]
    storage = attachment_env["storage"]
    project_id = app_client.portal.call(create_project, owner)

    doc = _create(app_client, owner, project_id, "pixel.png", PNG_1X1, "image/png")
    assert doc["kind"] == "image"
    assert doc["mime_type"] == "image/png"
    assert "storage_ref" not in doc
    assert doc["image_metadata"] is not None
    assert doc["image_metadata"].get("width") == 1
    assert doc["image_metadata"].get("height") == 1
    assert doc["extraction_status"] == "extracted"

    raw = _raw_doc(app_client, owner, doc["id"])
    assert raw["storage_ref"]
    stored = Path(storage.absolute_path(raw["storage_ref"]))
    assert stored.is_file()
    assert stored.read_bytes() == PNG_1X1
    assert str(root) in str(stored)


def test_unsupported_extension_is_rejected(app_client, attachment_env):
    owner = attachment_env["owner"]
    project_id = app_client.portal.call(create_project, owner)

    with pytest.raises(UnsupportedAttachmentError) as excinfo:
        _create(app_client, owner, project_id, "malware.exe", b"MZ\x90\x00\x03", "application/octet-stream")
    assert ".exe" in str(excinfo.value)
    assert excinfo.value.status_code == 400


def test_oversized_file_raises_limit_error(app_client, attachment_env, monkeypatch):
    owner = attachment_env["owner"]
    project_id = app_client.portal.call(create_project, owner)
    monkeypatch.setattr(config, "MAX_ATTACHMENT_SIZE_MB", 0.0001)

    with pytest.raises(AttachmentLimitError) as excinfo:
        _create(app_client, owner, project_id, "big.txt", b"x" * 500, "text/plain")
    assert "limit" in str(excinfo.value).lower()
    assert excinfo.value.status_code == 400


def test_path_traversal_filename_is_sanitized(app_client, attachment_env):
    owner = attachment_env["owner"]
    root = attachment_env["root"]
    storage = attachment_env["storage"]
    project_id = app_client.portal.call(create_project, owner)

    sanitized = attachments_module.sanitize_filename("../../etc/passwd")
    assert sanitized == "passwd"
    assert "/" not in sanitized and "\\" not in sanitized and ".." not in sanitized

    doc = _create(app_client, owner, project_id, "../../etc/passwd", b"totally fine", "text/plain")
    assert "/" not in doc["filename"]
    assert "\\" not in doc["filename"]
    assert ".." not in doc["filename"]

    raw = _raw_doc(app_client, owner, doc["id"])
    stored = Path(storage.absolute_path(raw["storage_ref"]))
    assert stored.is_file()
    assert root.resolve() in stored.resolve().parents
    assert stored.name.startswith(hashlib.sha256(b"totally fine").hexdigest()[:16])


def test_declared_image_with_text_payload_is_rejected(app_client, attachment_env):
    owner = attachment_env["owner"]
    project_id = app_client.portal.call(create_project, owner)

    with pytest.raises(UnsupportedAttachmentError):
        _create(app_client, owner, project_id, "notes.txt", b"definitely not a png", "image/png")


def test_cross_project_access_is_rejected(app_client, attachment_env):
    owner = attachment_env["owner"]
    project_a = app_client.portal.call(create_project, owner)
    project_b = app_client.portal.call(create_project, owner)

    doc = _create(app_client, owner, project_a, "notes.txt", b"hello", "text/plain")

    async def _fetch():
        return await attachments_module.get_attachment(owner, doc["id"], project_id=project_b)

    with pytest.raises(AttachmentError) as excinfo:
        app_client.portal.call(_fetch)
    assert excinfo.value.status_code == 404


def test_attachment_is_included_in_context_snapshot(app_client, attachment_env, as_user, monkeypatch):
    owner = attachment_env["owner"]
    project_id = app_client.portal.call(create_project, owner)

    doc = _create(app_client, owner, project_id, "notes.txt", b"the attached payload", "text/plain")
    raw = _raw_doc(app_client, owner, doc["id"])

    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(owner, provider_id=provider_id)

    model_id = app_client.portal.call(_setup)
    _install(monkeypatch, lambda model, req: result_success("read it"))

    async def _execute():
        return await execution_module.execute_model(
            owner,
            model_id,
            "system",
            [{"role": "user", "content": "summarize the attachment"}],
            {},
            project_id=project_id,
            attachments=[raw],
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "success"
    assert run["attachment_ids"] == [doc["id"]]

    async def _snapshot():
        return await context_module.get_context_snapshot(owner, run["id"])

    snapshot = app_client.portal.call(_snapshot)
    assert snapshot is not None
    assert snapshot["context_hash"]
    attachments = [c for c in snapshot["components"] if c["component_type"] == "ATTACHMENTS"]
    assert len(attachments) == 1
    component = attachments[0]
    assert component["source_ref"] == doc["id"]
    assert component["metadata"]["attachment_id"] == doc["id"]
    assert component["metadata"]["sha256"] == doc["sha256"]
    assert component["content_hash"]
    assert "the attached payload" in component["content"]


def test_referenced_attachment_is_detached_not_deleted(app_client, attachment_env):
    owner = attachment_env["owner"]
    project_id = app_client.portal.call(create_project, owner)
    doc = _create(app_client, owner, project_id, "notes.txt", b"evidence", "text/plain")
    attachment_id = doc["id"]

    async def _use():
        await attachments_module.mark_attachment_used(owner, attachment_id, "run-evidence-1")

    app_client.portal.call(_use)

    async def _delete():
        return await attachments_module.delete_attachment(owner, attachment_id)

    result = app_client.portal.call(_delete)
    assert result["detached"] is True
    assert "attachment" in result

    raw = _raw_doc(app_client, owner, attachment_id)
    assert raw is not None
    assert raw["referenced_by_run"] is True
    assert raw["detached"] is True
    assert "run-evidence-1" in raw["run_ids"]


def test_image_attachment_requires_vision_capability(app_client, attachment_env, monkeypatch):
    owner = attachment_env["owner"]
    project_id = app_client.portal.call(create_project, owner)
    doc = _create(app_client, owner, project_id, "pixel.png", PNG_1X1, "image/png")
    raw = _raw_doc(app_client, owner, doc["id"])

    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(owner, provider_id=provider_id, capabilities={})

    model_id = app_client.portal.call(_setup)
    calls = {"count": 0}

    def executor(model, req):
        calls["count"] += 1
        return result_success("should not happen")

    _install(monkeypatch, executor, vision=False)

    async def _execute():
        return await execution_module.execute_model(
            owner,
            model_id,
            "",
            [{"role": "user", "content": "what is in this image?"}],
            {},
            project_id=project_id,
            attachments=[raw],
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "error"
    assert isinstance(run["error"], dict)
    assert run["error"]["code"] == "VISION_UNSUPPORTED"
    assert run["error"]["category"] == "MODEL_UNAVAILABLE"
    assert calls["count"] == 0
