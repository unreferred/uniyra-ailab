"""V4.2.4 knowledge indexing lifecycle: truthful errors + atomic replacement.

Regression coverage for the live defect where a successful index left
``error_code="KNOWLEDGE_INDEX_ERROR"`` on a READY document, and for the
canonical collection counters shared by the list/detail endpoints.
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


def _project(client, name="v424-lifecycle"):
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


def _searchable_chunk_count(client, collection_id):
    r = client.get(f"/api/knowledge/{collection_id}/chunks")
    assert r.status_code == 200, r.text
    return sum(
        1 for c in r.json() if c.get("active") and c.get("index_status") == "ready"
    )


def _force_status(app_client, owner_id, document_id, status):
    async def _do():
        from app import repository as repo
        await repo.knowledge_documents.update(document_id, owner_id, {"status": status})
    app_client.portal.call(_do)


def test_a_first_successful_index_has_no_stale_error(app_client, as_user, _tmp_storage):
    project = _project(app_client)
    collection = _collection(app_client, project["id"], "success")
    file_doc = _upload(app_client, project["id"], "ok.txt", b"alpha beta gamma searchable", "text/plain")
    attached = _attach(app_client, collection["id"], [file_doc["id"]])

    summary = _index(app_client, collection["id"])
    assert summary["ready"] == 1 and summary["failed"] == 0

    document = _doc(app_client, collection["id"], attached[0]["id"])
    assert document["status"] == "ready"
    assert document["error"] is None
    assert not document.get("error_code"), document.get("error_code")
    assert not document.get("error_message")

    detail = _detail(app_client, collection["id"])
    assert detail["doc_count"] == 1
    assert detail["chunk_count"] >= 1
    assert _searchable_chunk_count(app_client, collection["id"]) >= 1


def test_indexed_document_never_carries_knowledge_index_error(app_client, as_user, _tmp_storage):
    project = _project(app_client)
    collection = _collection(app_client, project["id"], "no-stale-code")
    file_doc = _upload(app_client, project["id"], "clean.txt", b"clean index content", "text/plain")
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]
    _index(app_client, collection["id"])

    stored = _doc(app_client, collection["id"], document["id"])
    assert (stored.get("error_code") or None) is None, stored.get("error_code")

    async def _raw():
        from app import repository as repo
        return await repo.knowledge_documents.get(document["id"], as_user["id"])

    raw = app_client.portal.call(_raw)
    assert raw["error_code"] is None
    assert raw["error"] is None
    assert raw["error_message"] is None


def test_b_first_index_failure_is_truthful_and_not_searchable(app_client, as_user, _tmp_storage):
    project = _project(app_client)
    collection = _collection(app_client, project["id"], "failure")
    file_doc = _upload(app_client, project["id"], "photo.png", PNG_1X1, "image/png")
    attached = _attach(app_client, collection["id"], [file_doc["id"]])

    summary = _index(app_client, collection["id"])
    assert summary["ready"] == 0 and summary["failed"] == 1
    assert summary["errors"] and summary["errors"][0].get("error")

    document = _doc(app_client, collection["id"], attached[0]["id"])
    assert document["status"] != "ready"
    assert document.get("error")
    assert document.get("error_code"), "failure must record a truthful code"
    assert _searchable_chunk_count(app_client, collection["id"]) == 0

    detail = _detail(app_client, collection["id"])
    assert detail["chunk_count"] == 0


def test_c_failed_reindex_preserves_previous_active_index(
    app_client, as_user, _tmp_storage, monkeypatch
):
    project = _project(app_client)
    collection = _collection(app_client, project["id"], "atomic")
    file_doc = _upload(app_client, project["id"], "atomic.txt", b"durable searchable body", "text/plain")
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]
    _index(app_client, collection["id"])

    before_doc = _doc(app_client, collection["id"], document["id"])
    assert before_doc["status"] == "ready"
    before_generation = before_doc["index_generation"]
    before_version = before_doc["version"]
    before_chunks = app_client.get(f"/api/knowledge/{collection['id']}/chunks").json()
    assert before_chunks

    # Fail the next attempt during provider resolution.
    import app.services.knowledge as knowledge_service
    from app.services.embeddings import EmbeddingError

    async def _resolve(*_args, **_kwargs):
        raise EmbeddingError("provider exploded")

    monkeypatch.setattr(knowledge_service, "resolve_embedding_provider", _resolve)
    _force_status(app_client, as_user["id"], document["id"], "pending")

    summary = _index(app_client, collection["id"])
    assert summary["failed"] == 1 and summary["ready"] == 0

    after_doc = _doc(app_client, collection["id"], document["id"])
    assert after_doc["status"] != "ready", "a failed attempt must not look READY"
    assert after_doc.get("error_code") == "EMBEDDING_PROVIDER_ERROR"
    assert after_doc.get("error")

    # The previous generation is still the last valid searchable index.
    assert after_doc["index_generation"] == before_generation
    assert after_doc["version"] == before_version
    after_chunks = app_client.get(f"/api/knowledge/{collection['id']}/chunks").json()
    before_ids = {c["id"] for c in before_chunks}
    after_ids = {c["id"] for c in after_chunks}
    assert before_ids <= after_ids, "previous valid chunks must remain searchable"
    assert all(c.get("active") and c.get("index_status") == "ready" for c in after_chunks)

    search = app_client.post(
        "/api/knowledge/search",
        json={"query": "durable", "project_id": project["id"], "collection_ids": [collection["id"]]},
    )
    assert search.status_code == 200
    assert search.json()["result_count"] >= 1, "previous index must still be queryable"


def test_d_successful_reindex_after_failure_clears_error_and_counts_agree(
    app_client, as_user, _tmp_storage, monkeypatch
):
    project = _project(app_client)
    collection = _collection(app_client, project["id"], "recover")
    file_doc = _upload(app_client, project["id"], "recover.txt", b"recoverable searchable body", "text/plain")
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]

    import app.services.knowledge as knowledge_service
    from app.services.embeddings import EmbeddingError

    real_resolve = knowledge_service.resolve_embedding_provider

    async def _boom(*_args, **_kwargs):
        raise EmbeddingError("transient provider outage")

    monkeypatch.setattr(knowledge_service, "resolve_embedding_provider", _boom)
    _index(app_client, collection["id"])
    failed_doc = _doc(app_client, collection["id"], document["id"])
    assert failed_doc["status"] != "ready"
    assert failed_doc.get("error_code")

    monkeypatch.setattr(knowledge_service, "resolve_embedding_provider", real_resolve)
    _force_status(app_client, as_user["id"], document["id"], "pending")
    summary = _index(app_client, collection["id"])
    assert summary["ready"] == 1 and summary["failed"] == 0

    ready_doc = _doc(app_client, collection["id"], document["id"])
    assert ready_doc["status"] == "ready"
    assert ready_doc.get("error") is None
    assert not ready_doc.get("error_code")
    assert not ready_doc.get("error_message")

    detail = _detail(app_client, collection["id"])
    listed = _listed(app_client, project["id"], collection["id"])
    assert detail["doc_count"] == listed["doc_count"] == 1
    assert detail["chunk_count"] == listed["chunk_count"] >= 1


def test_e_list_detail_and_document_rows_share_canonical_state(
    app_client, as_user, _tmp_storage
):
    project = _project(app_client)
    collection = _collection(app_client, project["id"], "canonical")
    good = _upload(app_client, project["id"], "good.txt", b"canonical searchable content", "text/plain")
    bad = _upload(app_client, project["id"], "bad.png", PNG_1X1, "image/png")
    documents = _attach(app_client, collection["id"], [good["id"], bad["id"]])
    _index(app_client, collection["id"])

    docs = _docs(app_client, collection["id"])
    non_detached = [d for d in docs if d.get("status") != "detached"]
    ready_docs = [d for d in docs if d.get("status") == "ready"]

    detail = _detail(app_client, collection["id"])
    listed = _listed(app_client, project["id"], collection["id"])

    assert detail["doc_count"] == listed["doc_count"] == len(non_detached)
    assert len(ready_docs) == 1
    assert detail["chunk_count"] == listed["chunk_count"]
    assert detail["chunk_count"] == _searchable_chunk_count(app_client, collection["id"])

    search = app_client.post(
        "/api/knowledge/search",
        json={"query": "canonical", "project_id": project["id"], "collection_ids": [collection["id"]]},
    )
    assert search.status_code == 200
    assert len(search.json()["results"]) == detail["chunk_count"]

    # Detaching updates the canonical counters consistently in both endpoints.
    app_client.delete(f"/api/knowledge/{collection['id']}/documents/{documents[0]['id']}")
    detail_after = _detail(app_client, collection["id"])
    listed_after = _listed(app_client, project["id"], collection["id"])
    assert detail_after["doc_count"] == listed_after["doc_count"] == 1
    assert detail_after["chunk_count"] == listed_after["chunk_count"] == 0
