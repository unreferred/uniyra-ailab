"""Query-time retrieval over knowledge collections.

Pipeline: verify collections -> group by embedding model -> resolve one
provider per group -> embed the query -> :func:`vector_store.search` per
group -> merge, sort descending, apply ``top_k``. Empty result sets are a
normal outcome (``result_count`` 0), not an error.

Provenance snapshots (:func:`retrieval_provenance`) pin the exact chunk ids,
scores, ordering, embedding models and document versions behind a payload so
a Run stays explainable after a later reindex changes the vectors.
"""
import hashlib
import time
from typing import Any, Dict, List, Optional

from .. import config
from .. import repository as repo
from .embeddings import embedding_fingerprint, validate_embedding_response
from .embeddings import EmbeddingError, resolve_embedding_provider
from .vector_store import search as vector_search


class RetrievalError(ValueError):
    """Raised for retrieval contract violations. Carries an HTTP code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _query_hash(query: str) -> str:
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()


async def retrieve(
    owner_id: str,
    project_id: Optional[str],
    query: str,
    collection_ids: List[str],
    top_k: int = 5,
    score_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Retrieve the top chunks for ``query`` across owned collections."""
    started = time.perf_counter()
    text = (query or "").strip()
    if not text:
        raise RetrievalError("Query text is required")
    try:
        limit = max(1, min(int(top_k), 50))
    except (TypeError, ValueError):
        limit = 5
    threshold: Optional[float] = None
    if score_threshold is not None:
        try:
            threshold = float(score_threshold)
        except (TypeError, ValueError):
            raise RetrievalError("score_threshold must be a number")

    ids = [str(cid) for cid in (collection_ids or []) if str(cid)]
    if not ids and project_id:
        owned = await repo.knowledge_collections.list(
            {"owner_id": owner_id, "project_id": str(project_id), "status": {"$ne": "deleted"}},
            limit=500,
        )
        ids = [doc.get("id") for doc in owned if doc.get("id")]
    if not ids:
        raise RetrievalError("At least one knowledge collection is required")

    if project_id:
        project = await repo.projects.get(str(project_id), owner_id)
        if not project:
            raise RetrievalError("Project not found", status_code=404)

    collections: List[Dict[str, Any]] = []
    for collection_id in ids:
        collection = await repo.knowledge_collections.get(str(collection_id), owner_id)
        if not collection or collection.get("status") == "deleted":
            raise RetrievalError("Knowledge collection not found", status_code=404)
        if project_id and collection.get("project_id") != str(project_id):
            raise RetrievalError("Knowledge collection does not belong to this project")
        collections.append(collection)
    project_ids = {str(collection.get("project_id")) for collection in collections}
    if not project_id and len(project_ids) > 1:
        raise RetrievalError("project_id is required when collections span projects")
    effective_project_id = str(project_id or next(iter(project_ids)))

    # Group collections by embedding model so one query embedding serves each
    # group (vectors from different models are not comparable).
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for collection in collections:
        embedding_config = dict(collection.get("embedding_config") or {})
        model_key = str(embedding_config.get("model") or getattr(config, "EMBEDDING_MODEL", "local-hashing-v1"))
        provider_name = str(embedding_config.get("provider") or getattr(config, "EMBEDDING_PROVIDER", "local")).strip().lower()
        dimension = int(embedding_config.get("dimension") or getattr(config, "EMBEDDING_DIM", 256))
        version = str(embedding_config.get("version") or getattr(config, "EMBEDDING_VERSION", "embedding-v1"))
        embedding_config.update({"provider": provider_name, "dimension": dimension, "version": version})
        embedding_config["fingerprint"] = embedding_fingerprint(provider_name, model_key, dimension, version)
        collection["embedding_config"] = embedding_config
        groups.setdefault(embedding_config["fingerprint"], []).append(collection)

    merged: List[Dict[str, Any]] = []
    models_used: List[str] = []
    doc_cache: Dict[str, Dict[str, Any]] = {}
    for model_key, group in groups.items():
        representative = group[0]
        try:
            provider = await resolve_embedding_provider(
                owner_id, dict(representative.get("embedding_config") or {})
            )
        except EmbeddingError as exc:
            raise RetrievalError(exc.message, status_code=exc.status_code) from exc
        provider_name = getattr(provider, "name", None) or model_key
        try:
            vectors = await provider.embed([text])
        except EmbeddingError as exc:
            raise RetrievalError(exc.message, status_code=exc.status_code) from exc
        vectors = validate_embedding_response(
            vectors, 1, int(representative["embedding_config"].get("dimension") or 0)
        )
        if not vectors or not vectors[0] or not any(vectors[0]):
            continue
        models_used.append(provider_name)
        group_ids = [str(collection.get("id")) for collection in group]
        names = {str(collection.get("id")): str(collection.get("name") or "") for collection in group}
        hits = await vector_search(
            owner_id, group_ids, vectors[0], limit, score_threshold=threshold,
            project_id=effective_project_id,
            embedding_config={
                "embedding_fingerprint": representative["embedding_config"].get("fingerprint"),
                "embedding_model": representative["embedding_config"].get("model"),
                "embedding_dim": representative["embedding_config"].get("dimension"),
                "embedding_version": representative["embedding_config"].get("version"),
            },
        )
        for hit in hits:
            chunk = hit.get("chunk") or {}
            document_id = str(chunk.get("document_id") or "")
            document = doc_cache.get(document_id)
            if document is None:
                document = await repo.knowledge_documents.get(document_id, owner_id) or {}
                doc_cache[document_id] = document
            if (
                str(document.get("project_id")) != effective_project_id
                or str(document.get("collection_id")) not in group_ids
            ):
                continue
            # A document whose latest attempt failed must not hide its last
            # committed index: the active chunk below is only returned when it
            # belongs to the document's currently-published generation. A
            # detached document has no active generation and is excluded.
            if document.get("status") == "detached":
                continue
            if str(chunk.get("index_generation") or "") != str(document.get("index_generation") or ""):
                continue
            if not chunk.get("active") or chunk.get("index_status") != "ready":
                continue
            chunk_metadata = dict(chunk.get("metadata") or {})
            try:
                if int(chunk_metadata.get("document_version") or 0) != int(document.get("version") or 0):
                    continue
            except (TypeError, ValueError):
                continue
            merged.append(
                {
                    "collection_id": str(chunk.get("collection_id") or ""),
                    "collection_name": names.get(str(chunk.get("collection_id") or ""), ""),
                    "document_id": document_id,
                    "document_version": int(
                        chunk_metadata.get("document_version") or document.get("version") or 1
                    ),
                    "chunk_id": str(chunk.get("id") or ""),
                    "chunk_index": chunk.get("chunk_index"),
                    "content": str(chunk.get("content") or ""),
                    "score": float(hit.get("score") or 0.0),
                    "token_estimate": chunk.get("token_estimate"),
                    "source": {
                        "project_file_id": document.get("project_file_id"),
                        "attachment_id": document.get("attachment_id"),
                        "display_name": document.get("display_name"),
                    },
                    "metadata": chunk_metadata,
                }
            )

    merged.sort(key=lambda entry: entry["score"], reverse=True)
    results = merged[:limit]
    latency_ms = int((time.perf_counter() - started) * 1000)
    retrieval_config = {"top_k": limit, "score_threshold": threshold}
    return {
        "query": text,
        "query_hash": _query_hash(text),
        "collection_ids": ids,
        "top_k": limit,
        "score_threshold": threshold,
        "results": results,
        "result_count": len(results),
        "latency_ms": latency_ms,
        "embedding_models": sorted(set(models_used)),
        "retrieval_config": retrieval_config,
    }


def build_retrieved_context_text(results: List[Dict[str, Any]], max_chars: int = 12000) -> str:
    """Format ranked chunks with ``[source]`` headers for prompt injection."""
    try:
        cap = max(1, int(max_chars))
    except (TypeError, ValueError):
        cap = 12000
    blocks: List[str] = []
    used = 0
    for rank, result in enumerate(results or [], start=1):
        source = result.get("source") or {}
        label = (
            source.get("display_name")
            or result.get("collection_name")
            or "unknown source"
        )
        # Only expose parser-known location data to the model. IDs and scores
        # remain in provenance metadata, while the chunk index is the only
        # locator guaranteed by the ingestion pipeline.
        marker = f"[K{rank}]"
        locator = result.get("chunk_index")
        header = f"{marker} source: {label}; locator: chunk {locator}"
        block = f"{header}\n{result.get('content') or ''}".strip()
        if used + len(block) > cap:
            remaining = cap - used
            if remaining > len(header) + 16:
                block = block[:remaining].rstrip() + "\n[truncated]"
                blocks.append(block)
            break
        blocks.append(block)
        used += len(block) + 2
    return "<knowledge_evidence>\n" + "\n\n".join(blocks) + "\n</knowledge_evidence>" if blocks else ""


def retrieval_provenance(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshot the evidence behind a retrieval payload for Run persistence."""
    data = dict(payload or {})
    results = list(data.get("results") or [])
    return {
        "query_hash": data.get("query_hash"),
        "file_ids": list(dict.fromkeys(
            str((item.get("source") or {}).get("project_file_id"))
            for item in results
            if (item.get("source") or {}).get("project_file_id")
        )),
        "chunk_count": len(results),
        "estimated_tokens": sum(int(item.get("token_estimate") or 0) for item in results),
        "collection_ids": list(data.get("collection_ids") or []),
        "chunk_ids": [str(item.get("chunk_id")) for item in results],
        "scores": [float(item.get("score") or 0.0) for item in results],
        "ordering": list(range(1, len(results) + 1)),
        "retrieval_config": dict(data.get("retrieval_config") or {}),
        "embedding_models": list(data.get("embedding_models") or []),
        "document_versions": {
            str(item.get("chunk_id")): item.get("document_version") for item in results
        },
    }


def retrieved_context_component(results: List[Dict[str, Any]], provenance: Dict[str, Any]) -> Dict[str, Any]:
    """Build a RETRIEVED_CONTEXT component (context import kept lazy)."""
    from .context import estimate_tokens, make_component

    content = build_retrieved_context_text(results)
    token_count, _method = estimate_tokens(content)
    return make_component(
        "RETRIEVED_CONTEXT",
        content,
        ordering=60,
        token_count=int(token_count),
        metadata=dict(provenance or {}),
    )
