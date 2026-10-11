"""V4.2.4 knowledge document contract: canonical ids, truthful state, counts.

Regression coverage for:
* canonical document identifier (``id``) exposure and chunk foreign keys;
* reindex summary vs document shapes;
* clearing stale failure codes on success and on detach;
* persisted collection counters agreeing between list and detail, including
  after ``soft_delete_project_file``; and
* recovering a detached document by re-attaching its project file.
"""
from __future__ import annotations

import base64

import pytest

from app import config
from app import repository as repo


PHASE4 = (
    "project_files", "knowledge_collections", "knowledge_documents",
    "knowledge_chunks", "project_activity",
)


@pytest.fixture(autouse=True)
def _purge_phase4(app_client, as_user):
    yield

    async def _do():
        from app.database import db
        for name in PHASE4:
            await db[name].delete_many({"owner_id": as_user["id"]})

    app_client.portal.call(_do)


@pytest.fixture
def _tmp_storage(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(config, "ATTACHMENT_STORAGE_DIR", str(root))
    return root


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _project(client, name="v424-contract"):
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _collection(client, project_id, name):
    r = client.post("/api/knowledge", json={"project_id": project_id, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, project_id, filename, data, mime):
    r = client.post(
        f"/api/projects/{project_id}/files/upload",
        files={"files": (filename, data, mime)},
    )
    assert r.status_code == 200, r.text
    return r.json()["files"][0]


def _attach(client, collection_id, file_ids):
    r = client.post(f"/api/knowledge/{collection_id}/documents", json={"project_file_ids": file_ids})
    assert r.status_code == 200, r.text
    return r.json()


def _index(client, collection_id):
    r = client.post(f"/api/knowledge/{collection_id}/index")
    assert r.status_code == 200, r.text
    return r.json()


def _detail(client, collection_id):
    r = client.get(f"/api/knowledge/{collection_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _listed(client, project_id, collection_id):
    r = client.get(f"/api/knowledge?project_id={project_id}")
    assert r.status_code == 200, r.text
    return next(c for c in r.json() if c["id"] == collection_id)


def _docs(client, collection_id):
    r = client.get(f"/api/knowledge/{collection_id}/documents")
    assert r.status_code == 200, r.text
    return r.json()


def _doc(client, collection_id, document_id):
    return next(d for d in _docs(client, collection_id) if d["id"] == document_id)


def _chunks(client, collection_id):
    r = client.get(f"/api/knowledge/{collection_id}/chunks")
    assert r.status_code == 200, r.text
    return r.json()


def _searchable_chunk_count(client, collection_id):
    return sum(
        1 for c in _chunks(client, collection_id)
        if c.get("active") and c.get("index_status") == "ready"
    )


def _force_fields(app_client, owner_id, document_id, patch):
    async def _do():
        await repo.knowledge_documents.update(document_id, owner_id, dict(patch))
    app_client.portal.call(_do)


def _raw_document(app_client, owner_id, document_id):
    async def _do():
        return await repo.knowledge_documents.get(document_id, owner_id)
    return app_client.portal.call(_do)


def test_a_canonical_id_contract_and_chunk_fk(app_client, as_user, _tmp_storage):
    project = _project(app_client, "v424-contract-a")
    collection = _collection(app_client, project["id"], "canonical")
    file_doc = _upload(
        app_client, project["id"], "canon.txt",
        b"canonical identifier contract probe body", "text/plain",
    )
    attached = _attach(app_client, collection["id"], [file_doc["id"]])
    _index(app_client, collection["id"])

    documents = _docs(app_client, collection["id"])
    assert documents
    for document in documents:
        assert isinstance(document.get("id"), str) and document["id"].strip()
        assert "_id" not in document
    assert attached[0]["id"] == documents[0]["id"]

    chunks = _chunks(app_client, collection["id"])
    assert chunks
    document_ids = {d["id"] for d in documents}
    for chunk in chunks:
        assert "_id" not in chunk
        assert chunk.get("document_id") in document_ids
    assert attached[0]["id"] in {c["document_id"] for c in chunks}


def test_b_reindex_returns_summary_and_keeps_canonical_id(app_client, as_user, _tmp_storage):
    project = _project(app_client, "v424-contract-b")
    collection = _collection(app_client, project["id"], "summary")
    file_doc = _upload(
        app_client, project["id"], "summary.txt",
        b"summary shape reindex body content", "text/plain",
    )
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]
    _index(app_client, collection["id"])
    before = _doc(app_client, collection["id"], document["id"])

    response = app_client.post(
        f"/api/knowledge/{collection['id']}/documents/{document['id']}/reindex"
    )
    assert response.status_code == 200, response.text
    summary = response.json()
    assert set(summary) == {"processed", "ready", "failed", "errors"}
    assert summary["processed"] == 1 and summary["ready"] == 1 and summary["failed"] == 0
    for leaked in ("id", "status", "chunk_count", "version", "error", "error_code"):
        assert leaked not in summary

    after = _doc(app_client, collection["id"], document["id"])
    assert after["id"] == before["id"]
    assert after["status"] == "ready"
    assert after["version"] == before["version"] + 1


def test_d_ready_document_is_clean_present_and_counts_agree(app_client, as_user, _tmp_storage):
    project = _project(app_client, "v424-contract-d")
    collection = _collection(app_client, project["id"], "ready")
    file_doc = _upload(
        app_client, project["id"], "ready.txt",
        b"ready durable searchable chunk content", "text/plain",
    )
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]
    _index(app_client, collection["id"])

    stored = _doc(app_client, collection["id"], document["id"])
    assert stored["status"] == "ready"
    assert stored.get("error") is None
    assert stored.get("error_code") is None
    assert stored.get("error_message") is None
    assert stored.get("chunk_count", 0) >= 1

    detail = _detail(app_client, collection["id"])
    listed = _listed(app_client, project["id"], collection["id"])
    assert detail["doc_count"] == listed["doc_count"] == 1
    assert detail["chunk_count"] == listed["chunk_count"] >= 1
    assert detail["chunk_count"] == _searchable_chunk_count(app_client, collection["id"])

    chunks = _chunks(app_client, collection["id"])
    assert any(c["document_id"] == document["id"] for c in chunks)


def test_e_detach_clears_stale_failure_state_and_refreshes_counts(app_client, as_user, _tmp_storage):
    project = _project(app_client, "v424-contract-e")
    collection = _collection(app_client, project["id"], "detach")
    file_doc = _upload(
        app_client, project["id"], "detach.txt",
        b"detach clears stale failure state body", "text/plain",
    )
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]
    _index(app_client, collection["id"])

    ready = _doc(app_client, collection["id"], document["id"])
    assert ready["status"] == "ready"
    assert ready["status"] != "detached"

    # Simulate a legacy row left behind by the old setdefault-clearing bug.
    _force_fields(
        app_client, as_user["id"], document["id"],
        {"error": None, "error_code": "KNOWLEDGE_INDEX_ERROR", "error_message": None},
    )

    response = app_client.delete(f"/api/knowledge/{collection['id']}/documents/{document['id']}")
    assert response.status_code == 200, response.text
    detached = response.json()
    assert detached["id"] == document["id"]
    assert detached["status"] == "detached"
    assert detached.get("error") is None
    assert detached.get("error_code") is None
    assert detached.get("error_message") is None
    assert detached.get("chunk_count") == 0

    stored = _raw_document(app_client, as_user["id"], document["id"])
    assert stored["error"] is None
    assert stored["error_code"] is None
    assert stored["error_message"] is None

    detail = _detail(app_client, collection["id"])
    listed = _listed(app_client, project["id"], collection["id"])
    assert detail["doc_count"] == listed["doc_count"] == 0
    assert detail["chunk_count"] == listed["chunk_count"] == 0
    assert _searchable_chunk_count(app_client, collection["id"]) == 0


def test_f_reindex_state_transitions_never_report_detached(app_client, as_user, _tmp_storage):
    project = _project(app_client, "v424-contract-f")
    collection = _collection(app_client, project["id"], "states")
    file_doc = _upload(
        app_client, project["id"], "states.txt",
        b"reindex state transition probe body", "text/plain",
    )
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]
    _index(app_client, collection["id"])
    before = _doc(app_client, collection["id"], document["id"])

    response = app_client.post(
        f"/api/knowledge/{collection['id']}/documents/{document['id']}/reindex"
    )
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"processed", "ready", "failed", "errors"}
    after = _doc(app_client, collection["id"], document["id"])
    assert after["id"] == before["id"]
    assert after["status"] == "ready"
    assert after["version"] == before["version"] + 1

    # A processing/failed row must surface truthfully, never as detached.
    for forced in ("processing", "failed"):
        _force_fields(app_client, as_user["id"], document["id"], {"status": forced})
        observed = _doc(app_client, collection["id"], document["id"])
        assert observed["status"] == forced
        assert observed["status"] != "detached"

    _force_fields(app_client, as_user["id"], document["id"], {"status": "pending"})
    assert _index(app_client, collection["id"])["ready"] == 1
    assert _doc(app_client, collection["id"], document["id"])["status"] == "ready"


def test_g_counts_agree_after_reindex_and_after_file_delete(app_client, as_user, _tmp_storage):
    project = _project(app_client, "v424-contract-g")
    collection = _collection(app_client, project["id"], "counts")
    file_doc = _upload(
        app_client, project["id"], "counts.txt",
        b"count agreement reindex searchable body", "text/plain",
    )
    _attach(app_client, collection["id"], [file_doc["id"]])
    _index(app_client, collection["id"])
    document = _docs(app_client, collection["id"])[0]

    response = app_client.post(
        f"/api/knowledge/{collection['id']}/documents/{document['id']}/reindex"
    )
    assert response.status_code == 200, response.text
    detail = _detail(app_client, collection["id"])
    listed = _listed(app_client, project["id"], collection["id"])
    searchable = _searchable_chunk_count(app_client, collection["id"])
    assert detail["doc_count"] == listed["doc_count"] == 1
    assert detail["chunk_count"] == listed["chunk_count"] == searchable >= 1

    deleted = app_client.delete(f"/api/projects/{project['id']}/files/{file_doc['id']}")
    assert deleted.status_code == 200, deleted.text
    detail_after = _detail(app_client, collection["id"])
    listed_after = _listed(app_client, project["id"], collection["id"])
    assert detail_after["doc_count"] == listed_after["doc_count"] == 0
    assert detail_after["chunk_count"] == listed_after["chunk_count"] == 0
    assert _searchable_chunk_count(app_client, collection["id"]) == 0

    stored = _doc(app_client, collection["id"], document["id"])
    assert stored["status"] == "detached"
    assert stored.get("error_code") is None


def test_h_detached_document_recovers_by_reattaching_same_file(app_client, as_user, _tmp_storage):
    project = _project(app_client, "v424-contract-h")
    collection = _collection(app_client, project["id"], "recovery")
    file_doc = _upload(
        app_client, project["id"], "recovery.txt",
        b"recovery reactivation searchable body content", "text/plain",
    )
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]
    _index(app_client, collection["id"])
    assert _doc(app_client, collection["id"], document["id"])["status"] == "ready"

    detached = app_client.delete(f"/api/knowledge/{collection['id']}/documents/{document['id']}")
    assert detached.status_code == 200, detached.text
    assert _doc(app_client, collection["id"], document["id"])["status"] == "detached"

    reattached = _attach(app_client, collection["id"], [file_doc["id"]])
    assert len(reattached) == 1
    recovered = reattached[0]
    assert recovered["id"] == document["id"]
    assert recovered["status"] == "pending"
    assert recovered["processing_status"] == "pending"
    assert recovered["ingestion_status"] == "pending"
    assert recovered.get("error") is None
    assert recovered.get("error_code") is None
    assert recovered.get("error_message") is None
    assert recovered.get("chunk_count") == 0

    listed = _listed(app_client, project["id"], collection["id"])
    assert listed["doc_count"] == 1
    assert listed["chunk_count"] == 0

    summary = _index(app_client, collection["id"])
    assert summary["ready"] == 1 and summary["failed"] == 0
    final = _doc(app_client, collection["id"], document["id"])
    assert final["status"] == "ready"
    assert final["chunk_count"] >= 1
