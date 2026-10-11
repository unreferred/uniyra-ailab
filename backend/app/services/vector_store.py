"""Local vector index over knowledge chunks (naive scan + Python cosine).

Replacement boundary: :func:`search` loads chunk embeddings from Mongo and
scores them in Python. This is the default local index — correct and
dependency-free, but O(N) per query. Production can swap ``search`` for a
dedicated vector DB (pgvector, Qdrant, Mongo Atlas Vector Search, ...) without
changing callers: the contract is ``(owner_id, collection_ids,
query_embedding, top_k, score_threshold) -> [{chunk, score}]`` where scores
are computed cosine similarities, never fabricated.
"""
import math
from typing import Any, Dict, List, Optional

from .. import config
from .. import repository as repo


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Pure cosine similarity; zero vectors (or mismatched lengths) score 0.0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    score = dot / (norm_a * norm_b)
    # Clamp float drift so stored scores stay within [-1, 1].
    if score > 1.0:
        return 1.0
    if score < -1.0:
        return -1.0
    return score


async def upsert_chunk_embedding(
    owner_id: str,
    chunk_id: str,
    embedding: List[float],
    model: str,
    dim: int,
    embedding_version: Optional[str] = None,
    embedding_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach a computed embedding to a chunk document."""
    updated = await repo.knowledge_chunks.update(
        chunk_id,
        owner_id,
        {
            "embedding": list(embedding or []),
            "embedding_model": model,
            "embedding_dim": int(dim),
            **({"embedding_version": embedding_version} if embedding_version is not None else {}),
            **({"embedding_fingerprint": embedding_fingerprint} if embedding_fingerprint is not None else {}),
        },
    )
    return updated or {}


async def search(
    owner_id: str,
    collection_ids: List[str],
    query_embedding: List[float],
    top_k: int,
    score_threshold: Optional[float] = None,
    limit_chunks: Optional[int] = None,
    project_id: Optional[str] = None,
    embedding_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Cosine-scan chunks of ``collection_ids``; return top_k ``{chunk, score}``.

    Only chunks with a non-empty embedding participate. Scores are computed
    cosines; nothing is fabricated. ``limit_chunks`` caps the total chunks
    scanned (defaults to the configured per-collection ceiling).
    """
    ids = [str(cid) for cid in (collection_ids or []) if str(cid)]
    if not ids or not query_embedding or not any(query_embedding):
        return []
    expected = dict(embedding_config or {})
    expected_dim = int(expected.get("dimension") or 0)
    if expected_dim and len(query_embedding) != expected_dim:
        return []
    try:
        top = max(1, int(top_k))
    except (TypeError, ValueError):
        top = 5
    try:
        cap = int(limit_chunks) if limit_chunks else int(config.KNOWLEDGE_MAX_CHUNKS_PER_COLLECTION)
    except (TypeError, ValueError):
        cap = 5000
    cap = max(1, cap)

    query: Dict[str, Any] = {
        "owner_id": owner_id,
        "collection_id": {"$in": ids},
        "active": True,
        "index_status": "ready",
        "document_status": "ready",
    }
    if project_id is not None:
        query["project_id"] = str(project_id)
    for field in ("embedding_fingerprint", "embedding_model", "embedding_dim", "embedding_version"):
        if expected.get(field) is not None:
            query[field] = expected[field]
    docs = await repo.knowledge_chunks.list(query, limit=cap)
    scored: List[Dict[str, Any]] = []
    for chunk in docs:
        embedding = chunk.get("embedding") or []
        if not embedding:
            continue
        score = cosine_similarity(query_embedding, embedding)
        if score_threshold is not None:
            try:
                if score < float(score_threshold):
                    continue
            except (TypeError, ValueError):
                pass
        scored.append({"chunk": chunk, "score": float(score)})
    scored.sort(key=lambda entry: entry["score"], reverse=True)
    return scored[:top]
