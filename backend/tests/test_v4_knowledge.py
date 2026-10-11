"""OmniLab V4 knowledge CRUD, indexing, retrieval and isolation (offline API tests)."""
from __future__ import annotations

import base64

import pytest

from app import config
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


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

DOC_A_QUASAR = (
    "Quasar nebula astrophysics stellar parallax redshift luminosity accretion "
    "disk event horizon singularity cosmos telescope observatory. "
) * 8
DOC_B_BREAD = (
    "Sourdough fermentation gluten hydration starter levain crumb crust proofing "
    "kneading autolyse scoring bakery oven flour. "
) * 8


@pytest.fixture
def _tmp_storage(monkeypatch, tmp_path):
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(config, "ATTACHMENT_STORAGE_DIR", str(root))
    return root


def _project(app_client, name="v4-kb"):
    r = app_client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _collection(app_client, project_id, name):
    r = app_client.post(
        "/api/knowledge", json={"project_id": project_id, "name": name}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _upload(app_client, project_id, filename, data, mime):
    r = app_client.post(
        f"/api/projects/{project_id}/files/upload",
        files={"files": (filename, data, mime)},
    )
    assert r.status_code == 200, r.text
    return r.json()["files"][0]


def _attach(app_client, collection_id, file_ids):
    r = app_client.post(
        f"/api/knowledge/{collection_id}/documents",
        json={"project_file_ids": file_ids},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _index(app_client, collection_id):
    r = app_client.post(f"/api/knowledge/{collection_id}/index")
    assert r.status_code == 200, r.text
    return r.json()


def test_collection_crud_and_duplicate_name_409(app_client, as_user):
    project = _project(app_client)
    created = _collection(app_client, project["id"], "research")
    assert created["name"] == "research"

    r = app_client.post(
        "/api/knowledge",
        json={"project_id": project["id"], "name": "research"},
    )
    assert r.status_code == 409, r.text

    fetched = app_client.get(f"/api/knowledge/{created['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == created["id"]

    r = app_client.patch(
        f"/api/knowledge/{created['id']}", json={"description": "renamed"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "renamed"

    r = app_client.delete(f"/api/knowledge/{created['id']}")
    assert r.status_code == 200, r.text


def test_upload_attach_index_produces_embedded_chunks(
    app_client, as_user, _tmp_storage
):
    project = _project(app_client)
    file_doc = _upload(
        app_client, project["id"], "notes.txt",
        b"alpha beta gamma knowledge indexing probe", "text/plain",
    )
    collection = _collection(app_client, project["id"], "kb-index")
    docs = _attach(app_client, collection["id"], [file_doc["id"]])
    assert docs[0]["status"] == "pending"

    summary = _index(app_client, collection["id"])
    assert summary["ready"] == 1 and summary["failed"] == 0

    r = app_client.get(f"/api/knowledge/{collection['id']}/chunks")
    assert r.status_code == 200, r.text
    chunks = r.json()
    assert chunks, "indexing must produce chunks"
    assert all(chunk.get("content") for chunk in chunks)
    # Embeddings are excluded from list output by design (shape contract).
    assert all("embedding" not in chunk for chunk in chunks)


def test_search_returns_ranked_results_with_sources(
    app_client, as_user, _tmp_storage
):
    project = _project(app_client)
    file_doc = _upload(
        app_client, project["id"], "guide.txt",
        DOC_A_QUASAR.encode(), "text/plain",
    )
    collection = _collection(app_client, project["id"], "kb-search")
    _attach(app_client, collection["id"], [file_doc["id"]])
    _index(app_client, collection["id"])

    r = app_client.post(
        "/api/knowledge/search",
        json={
            "query": "quasar parallax redshift",
            "collection_ids": [collection["id"]],
            "top_k": 5,
        },
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["result_count"] >= 1
    scores = [hit["score"] for hit in payload["results"]]
    assert scores == sorted(scores, reverse=True), "results must be score-desc"
    for hit in payload["results"]:
        assert hit["chunk_id"] and hit["content"]
        assert hit["source"].get("display_name")
        assert hit["collection_id"] == collection["id"]


def test_score_ordering_prefers_matching_document(
    app_client, as_user, _tmp_storage
):
    # Two docs with disjoint vocabularies; the query shares vocabulary with A
    # only, so the top hit must come from document A under cosine ranking.
    project = _project(app_client)
    file_a = _upload(
        app_client, project["id"], "astro.txt",
        DOC_A_QUASAR.encode(), "text/plain",
    )
    file_b = _upload(
        app_client, project["id"], "bread.txt",
        DOC_B_BREAD.encode(), "text/plain",
    )
    collection = _collection(app_client, project["id"], "kb-rank")
    _attach(app_client, collection["id"], [file_a["id"], file_b["id"]])
    _index(app_client, collection["id"])

    r = app_client.post(
        "/api/knowledge/search",
        json={
            "query": "quasar parallax redshift accretion",
            "collection_ids": [collection["id"]],
            "top_k": 5,
        },
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) >= 2
    assert results[0]["source"].get("display_name") == "astro.txt"


def test_empty_retrieval_returns_zero_results(app_client, as_user):
    project = _project(app_client)
    collection = _collection(app_client, project["id"], "kb-empty")
    r = app_client.post(
        "/api/knowledge/search",
        json={
            "query": "anything at all",
            "collection_ids": [collection["id"]],
            "top_k": 5,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["result_count"] == 0
    assert r.json()["results"] == []


def test_project_isolation_other_owner_collection_invisible(
    app_client, as_user, _tmp_storage
):
    project = _project(app_client)
    file_doc = _upload(
        app_client, project["id"], "secret.txt",
        b"owner isolation probe", "text/plain",
    )
    collection = _collection(app_client, project["id"], "kb-private")
    _attach(app_client, collection["id"], [file_doc["id"]])
    _index(app_client, collection["id"])

    other = {
        "id": "pytest-other-owner",
        "email": "pytest-other-owner@example.com",
        "name": "Other",
        "role": "researcher",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    from app.deps import get_current_user

    app_client.app.dependency_overrides[get_current_user] = lambda: other
    try:
        r = app_client.get(f"/api/knowledge/{collection['id']}")
        assert r.status_code == 404, r.text
        r = app_client.post(
            "/api/knowledge/search",
            json={"query": "isolation", "collection_ids": [collection["id"]]},
        )
        assert r.status_code == 404, r.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = (
            lambda: as_user
        )


def test_reindex_bumps_version_and_replaces_chunks(
    app_client, as_user, _tmp_storage
):
    # Evidence semantic: reindex deletes a document's old chunks and inserts
    # fresh chunk rows while bumping document.version. A provenance snapshot
    # taken before reindex therefore still references the OLD chunk ids
    # alongside the version they were pinned at (run explainability after
    # vectors change). Assert: version incremented + old ids gone.
    project = _project(app_client)
    file_doc = _upload(
        app_client, project["id"], "versioned.txt",
        b"rivers mountains valleys reindex provenance probe text", "text/plain",
    )
    collection = _collection(app_client, project["id"], "kb-reindex")
    attached = _attach(app_client, collection["id"], [file_doc["id"]])
    assert attached[0]["version"] == 1

    _index(app_client, collection["id"])
    before = app_client.get(f"/api/knowledge/{collection['id']}/chunks").json()
    docs = app_client.get(f"/api/knowledge/{collection['id']}/documents").json()
    old_chunk_ids = {chunk["id"] for chunk in before}
    old_version = docs[0]["version"]
    assert old_version == 2  # first index bumps 1 -> 2

    # Simulate an old provenance snapshot pinned before a reindex.
    provenance_snapshot = {
        "chunk_ids": sorted(old_chunk_ids),
        "document_versions": {cid: old_version for cid in old_chunk_ids},
    }

    # Force the document back to pending so the indexer reprocesses it
    # (the indexer only retries pending/failed docs; ready docs are skipped).
    async def _reset_doc():
        from app import repository as repo

        await repo.knowledge_documents.update(
            docs[0]["id"], as_user["id"], {"status": "pending"}
        )

    app_client.portal.call(_reset_doc)
    _index(app_client, collection["id"])

    after_docs = app_client.get(
        f"/api/knowledge/{collection['id']}/documents"
    ).json()
    after_chunks = app_client.get(
        f"/api/knowledge/{collection['id']}/chunks"
    ).json()
    new_chunk_ids = {chunk["id"] for chunk in after_chunks}
    assert after_docs[0]["version"] == old_version + 1
    assert old_chunk_ids.isdisjoint(new_chunk_ids), (
        "reindex must replace chunks (old ids retired)"
    )
    # The old snapshot is preserved as plain data: it still names the old
    # chunk ids at the old version even though they no longer resolve.
    assert provenance_snapshot["chunk_ids"]
    assert all(
        provenance_snapshot["document_versions"][cid] == old_version
        for cid in provenance_snapshot["chunk_ids"]
    )


def test_indexing_failure_path_image_has_no_text(app_client, as_user, _tmp_storage):
    # Images carry no extractable text; the service marks the document
    # "failed" with the honest service error ("Attachment has no
    # extractable text") and reports it in the index summary errors list.
    project = _project(app_client)
    file_doc = _upload(
        app_client, project["id"], "photo.png", PNG_1X1, "image/png"
    )
    collection = _collection(app_client, project["id"], "kb-image")
    _attach(app_client, collection["id"], [file_doc["id"]])
    summary = _index(app_client, collection["id"])
    assert summary["ready"] == 0
    assert summary["failed"] == 1
    assert summary["errors"]
    assert "no extractable text" in summary["errors"][0]["error"]

    docs = app_client.get(f"/api/knowledge/{collection['id']}/documents").json()
    assert docs[0]["status"] == "failed"
    assert "no extractable text" in (docs[0]["error"] or "")


def test_same_owner_cross_project_collections_require_project_scope(
    app_client, as_user, _tmp_storage
):
    project_a = _project(app_client, "v4-kb-a")
    project_b = _project(app_client, "v4-kb-b")
    collection_a = _collection(app_client, project_a["id"], "a")
    collection_b = _collection(app_client, project_b["id"], "b")
    response = app_client.post(
        "/api/knowledge/search",
        json={"query": "isolation", "collection_ids": [collection_a["id"], collection_b["id"]]},
    )
    assert response.status_code == 400, response.text
    response = app_client.post(
        "/api/knowledge/search",
        json={"query": "isolation", "project_id": project_a["id"], "collection_ids": [collection_b["id"]]},
    )
    assert response.status_code == 400, response.text


def test_explicit_reindex_and_document_detach_remove_searchable_chunks(
    app_client, as_user, _tmp_storage
):
    project = _project(app_client, "v4-kb-lifecycle")
    file_doc = _upload(app_client, project["id"], "lifecycle.md", b"lifecycle searchable content", "text/markdown")
    collection = _collection(app_client, project["id"], "lifecycle")
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]
    _index(app_client, collection["id"])
    before = app_client.get(f"/api/knowledge/{collection['id']}/documents").json()[0]
    response = app_client.post(f"/api/knowledge/{collection['id']}/documents/{document['id']}/reindex")
    assert response.status_code == 200, response.text
    after = app_client.get(f"/api/knowledge/{collection['id']}/documents").json()[0]
    assert after["status"] == "ready" and after["version"] == before["version"] + 1
    response = app_client.delete(f"/api/knowledge/{collection['id']}/documents/{document['id']}")
    assert response.status_code == 200, response.text
    assert app_client.get(f"/api/knowledge/{collection['id']}/chunks").json() == []
    response = app_client.post(
        "/api/knowledge/search",
        json={"query": "lifecycle", "project_id": project["id"], "collection_ids": [collection["id"]]},
    )
    assert response.status_code == 200 and response.json()["result_count"] == 0

def test_source_file_delete_detaches_knowledge_and_cleans_chunks(
    app_client, as_user, _tmp_storage
):
    project = _project(app_client, "v4-kb-file-delete")
    file_doc = _upload(app_client, project["id"], "remove.txt", b"remove me from search", "text/plain")
    collection = _collection(app_client, project["id"], "file-delete")
    document = _attach(app_client, collection["id"], [file_doc["id"]])[0]
    _index(app_client, collection["id"])
    response = app_client.delete(f"/api/projects/{project['id']}/files/{file_doc['id']}")
    assert response.status_code == 200, response.text
    assert app_client.get(f"/api/knowledge/{collection['id']}/chunks").json() == []
    stored = app_client.get(f"/api/knowledge/{collection['id']}/documents").json()[0]
    assert stored["id"] == document["id"] and stored["ingestion_status"] == "detached"


def test_embedding_response_validation_rejects_bad_cardinality_and_dimension():
    from app.services.embeddings import EmbeddingError, validate_embedding_response

    with pytest.raises(EmbeddingError):
        validate_embedding_response([[1.0]], 2, 1)
    with pytest.raises(EmbeddingError):
        validate_embedding_response([[1.0, 2.0]], 1, 3)


def test_workspace_collection_selection_is_project_scoped(
    app_client, as_user
):
    project_a = _project(app_client, "knowledge-owner-a")
    project_b = _project(app_client, "knowledge-owner-b")
    collection_a = _collection(app_client, project_a["id"], "private-a")

    response = app_client.post(
        "/api/conversations",
        json={"project_id": project_b["id"], "knowledge_collection_ids": [collection_a["id"]]},
    )
    assert response.status_code == 400, response.text

    conversation = app_client.post(
        "/api/conversations", json={"project_id": project_b["id"]}
    ).json()
    response = app_client.patch(
        f"/api/conversations/{conversation['id']}",
        json={"knowledge_collection_ids": [collection_a["id"]]},
    )
    assert response.status_code == 400, response.text

    async def _model():
        provider_id = await create_provider(as_user["id"])
        return await create_model(as_user["id"], provider_id=provider_id)

    model_id = app_client.portal.call(_model)
    response = app_client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"model_id": model_id, "content": "do not retrieve", "knowledge_collection_ids": [collection_a["id"]]},
    )
    assert response.status_code == 400, response.text
