"""Knowledge collections, documents and chunk indexing.

Document shapes:
* collection: {id, owner_id, project_id, name, description, status,
  embedding_config (resolved snapshot incl. provider/model/dimension),
  chunking_config {strategy, chunk_size, chunk_overlap}, doc_count,
  chunk_count, created_at, updated_at}
* document: {id, owner_id, project_id, collection_id, project_file_id,
  attachment_id, display_name, status, processing_status (mirror of status),
  error, content_hash, chunk_count, token_estimate, embedding_model,
  version (int, increments on reindex), metadata, created_at, updated_at}
* chunk: {id, owner_id, project_id, collection_id, document_id, content,
  chunk_index, token_estimate, metadata, content_hash, embedding,
  embedding_model, embedding_dim, created_at}
"""
import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from .. import config
from .. import repository as repo
from ..models import KNOWLEDGE_CHUNK_STRATEGIES, new_id, now_iso
from .embeddings import EmbeddingError, embedding_fingerprint, resolve_embedding_provider, validate_embedding_response
from .vector_store import upsert_chunk_embedding

# Hard cap on extracted source text kept per document (~200k chars).
_MAX_EXTRACT_CHARS = 200_000

# Preview length for chunk list output.
_CHUNK_PREVIEW_CHARS = 500


class KnowledgeError(ValueError):
    """Raised for knowledge contract violations. Carries an HTTP code."""

    def __init__(self, message: str, status_code: int = 400, error_code: str = "KNOWLEDGE_ERROR"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code


def _estimate_tokens(text: str) -> int:
    """Token estimate via context service, with a heuristic fallback."""
    try:
        from .context import estimate_tokens
        count, _method = estimate_tokens(text or "")
        return int(count)
    except Exception:
        return max(0, len(text or "") // 4)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def default_chunking_config() -> Dict[str, Any]:
    """Chunking defaults from central config."""
    return {
        "strategy": "fixed",
        "chunk_size": _int(getattr(config, "KNOWLEDGE_DEFAULT_CHUNK_SIZE", 800), 800),
        "chunk_overlap": _int(getattr(config, "KNOWLEDGE_DEFAULT_CHUNK_OVERLAP", 100), 100),
    }


def default_embedding_config() -> Dict[str, Any]:
    """Embedding defaults from central config (offline local provider)."""
    return {
        "provider": str(getattr(config, "EMBEDDING_PROVIDER", "local")),
        "model": str(getattr(config, "EMBEDDING_MODEL", "local-hashing-v1")),
        "dimension": _int(getattr(config, "EMBEDDING_DIM", 256), 256),
    }


def normalize_embedding_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge a caller-supplied embedding config over the defaults."""
    merged = default_embedding_config()
    for key, value in (dict(raw or {})).items():
        if key in ("provider", "model", "dimension", "provider_id", "version"):
            merged[key] = value
    merged["provider"] = str(merged.get("provider") or "local").strip().lower()
    merged["model"] = str(merged.get("model") or merged["model"])
    merged["dimension"] = _int(merged.get("dimension"), merged["dimension"])
    merged["version"] = str(merged.get("version") or getattr(config, "EMBEDDING_VERSION", "embedding-v1"))
    if merged["dimension"] <= 0:
        raise KnowledgeError("Embedding dimension must be a positive integer", error_code="INVALID_EMBEDDING_CONFIG")
    if merged["provider"] not in ("local", "openai_compatible"):
        raise KnowledgeError("Unknown embedding provider", error_code="INVALID_EMBEDDING_PROVIDER")
    merged["fingerprint"] = embedding_fingerprint(
        merged["provider"], merged["model"], merged["dimension"], merged["version"]
    )
    return merged


def normalize_chunking_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate and normalize a chunking config dict."""
    merged = default_chunking_config()
    for key, value in (dict(raw or {})).items():
        if key in ("strategy", "chunk_size", "chunk_overlap"):
            merged[key] = value
    strategy = str(merged.get("strategy") or "fixed").strip().lower()
    if strategy not in KNOWLEDGE_CHUNK_STRATEGIES:
        raise KnowledgeError("Unknown chunking strategy")
    chunk_size = _int(merged.get("chunk_size"), 0)
    chunk_overlap = _int(merged.get("chunk_overlap"), 0)
    if chunk_size <= 0:
        raise KnowledgeError("chunk_size must be a positive integer")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise KnowledgeError("chunk_overlap must be >= 0 and smaller than chunk_size")
    merged["strategy"] = strategy
    merged["chunk_size"] = chunk_size
    merged["chunk_overlap"] = chunk_overlap
    return merged


# ---------------------------------------------------------------- collections
async def _require_project(owner_id: str, project_id: str) -> Dict[str, Any]:
    project = await repo.projects.get(str(project_id), owner_id)
    if not project:
        raise KnowledgeError("Project not found", status_code=404)
    return project


async def _require_collection(owner_id: str, collection_id: str) -> Dict[str, Any]:
    collection = await repo.knowledge_collections.get(str(collection_id), owner_id)
    if not collection:
        raise KnowledgeError("Knowledge collection not found", status_code=404)
    return collection


def _require_active(collection: Dict[str, Any]) -> Dict[str, Any]:
    if collection.get("status") == "deleted":
        raise KnowledgeError("Knowledge collection has been deleted", status_code=404)
    return collection


async def create_collection(owner_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a collection; names are unique per project (409 on conflict)."""
    data = dict(payload or {})
    project_id = str(data.get("project_id") or "")
    if not project_id:
        raise KnowledgeError("project_id is required")
    await _require_project(owner_id, project_id)
    name = str(data.get("name") or "").strip()
    if not name:
        raise KnowledgeError("Collection name is required")
    existing = await repo.knowledge_collections.find_one(
        {"owner_id": owner_id, "project_id": project_id, "name": name, "status": {"$ne": "deleted"}}
    )
    if existing:
        raise KnowledgeError("A knowledge collection with this name already exists", status_code=409)
    timestamp = now_iso()
    document = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "name": name,
        "description": str(data.get("description") or ""),
        "status": "active",
        "embedding_config": normalize_embedding_config(data.get("embedding_config")),
        "chunking_config": normalize_chunking_config(data.get("chunking_config")),
        "doc_count": 0,
        "chunk_count": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    return await repo.knowledge_collections.insert(document)


async def update_collection(owner_id: str, collection_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Patch name/description/configs. Config changes affect future indexing only."""
    collection = _require_active(await _require_collection(owner_id, collection_id))
    data = dict(patch or {})
    update: Dict[str, Any] = {}
    if "name" in data and data["name"] is not None:
        name = str(data["name"]).strip()
        if not name:
            raise KnowledgeError("Collection name must not be empty")
        if name != collection.get("name"):
            conflict = await repo.knowledge_collections.find_one(
                {"owner_id": owner_id, "project_id": collection.get("project_id"),
                 "name": name, "status": {"$ne": "deleted"}}
            )
            if conflict and conflict.get("id") != collection.get("id"):
                raise KnowledgeError("A knowledge collection with this name already exists", status_code=409)
        update["name"] = name
    if "description" in data and data["description"] is not None:
        update["description"] = str(data["description"] or "")
    if data.get("embedding_config") is not None:
        update["embedding_config"] = normalize_embedding_config(data.get("embedding_config"))
    if data.get("chunking_config") is not None:
        update["chunking_config"] = normalize_chunking_config(data.get("chunking_config"))
    if not update:
        return collection
    update["updated_at"] = now_iso()
    return await repo.knowledge_collections.update(collection_id, owner_id, update) or collection


async def delete_collection(owner_id: str, collection_id: str) -> Dict[str, Any]:
    """Delete searchable material while retaining document metadata."""
    collection = _require_active(await _require_collection(owner_id, collection_id))
    timestamp = now_iso()
    updated = await repo.knowledge_collections.update(
        collection_id, owner_id, {"status": "deleted", "updated_at": timestamp}
    )
    await repo.knowledge_chunks.delete_many({"owner_id": owner_id, "collection_id": collection_id})
    documents = await repo.knowledge_documents.list(
        {"owner_id": owner_id, "collection_id": collection_id}, limit=5000
    )
    for document in documents:
        await repo.knowledge_documents.update(
            document.get("id"), owner_id,
            {
                "status": "detached",
                "processing_status": "detached",
                "ingestion_status": "detached",
                "error": None,
                "error_code": None,
                "error_message": None,
                "updated_at": timestamp,
            },
        )
    await _refresh_collection_counts(owner_id, collection_id)
    return updated or collection


async def list_collections(
    owner_id: str, project_id: Optional[str] = None, include_deleted: bool = False
) -> List[Dict[str, Any]]:
    """List owned collections, excluding soft-deleted ones by default."""
    query: Dict[str, Any] = {"owner_id": owner_id}
    if project_id:
        query["project_id"] = str(project_id)
    if not include_deleted:
        query["status"] = {"$ne": "deleted"}
    return await repo.knowledge_collections.list(query, limit=500)


async def get_collection(owner_id: str, collection_id: str) -> Dict[str, Any]:
    """Return one owned collection (including soft-deleted ones)."""
    return await _require_collection(owner_id, collection_id)


async def collection_counts(owner_id: str, collection_id: str) -> Dict[str, int]:
    """Canonical live document/chunk totals for a collection.

    ``doc_count`` counts non-detached document memberships; ``chunk_count``
    counts chunks that retrieval can actually return (active + ready),
    matching the predicate in :func:`services.vector_store.search`.
    """
    base = {"owner_id": owner_id, "collection_id": str(collection_id), "status": {"$ne": "detached"}}
    return {
        "doc_count": await repo.knowledge_documents.count(dict(base)),
        "chunk_count": await repo.knowledge_chunks.count(
            {"owner_id": owner_id, "collection_id": str(collection_id), "active": True, "index_status": "ready"}
        ),
    }


async def _refresh_collection_counts(owner_id: str, collection_id: str) -> Dict[str, int]:
    """Persist canonical totals on the collection so list and detail agree."""
    counts = await collection_counts(owner_id, collection_id)
    await repo.knowledge_collections.update(
        str(collection_id),
        owner_id,
        {"doc_count": counts["doc_count"], "chunk_count": counts["chunk_count"], "updated_at": now_iso()},
    )
    return counts


# ---------------------------------------------------------------- documents
def _public_document(document: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow public copy of a knowledge document.

    Enforces the canonical identifier contract: the persisted document ``id``
    (a UUID) is the only identifier exposed, and any driver ``_id`` is
    stripped. A missing/blank canonical id is a server-side contract
    violation, not a client error.
    """
    public = {key: value for key, value in (document or {}).items() if key != "_id"}
    canonical_id = public.get("id")
    if not isinstance(canonical_id, str) or not canonical_id.strip():
        raise KnowledgeError("Knowledge document is missing its canonical id", status_code=500)
    return public


async def attach_files(
    owner_id: str, project_id: str, collection_id: str, project_file_ids: List[str]
) -> List[Dict[str, Any]]:
    """Attach project files as pending knowledge documents.

    Verifies the collection and every file live in the same project, dedupes
    an existing document for the same file, and enforces the per-collection
    document cap. A duplicate whose document is ``detached`` is reactivated
    (back to ``pending`` with cleared failure state) so it can be indexed
    again; any other duplicate is returned as-is.
    """
    collection = _require_active(await _require_collection(owner_id, collection_id))
    if collection.get("project_id") != str(project_id):
        raise KnowledgeError("Knowledge collection does not belong to this project", status_code=400)
    await _require_project(owner_id, project_id)
    file_ids = [str(fid) for fid in (project_file_ids or []) if str(fid)]
    if not file_ids:
        raise KnowledgeError("At least one project file is required")
    max_docs = _int(getattr(config, "KNOWLEDGE_MAX_DOCUMENTS_PER_COLLECTION", 200), 200)
    current = await repo.knowledge_documents.count({"owner_id": owner_id, "collection_id": collection_id})
    if current + len(file_ids) > max_docs:
        raise KnowledgeError("Collection document limit reached", status_code=400)

    attached: List[Dict[str, Any]] = []
    for file_id in file_ids:
        project_file = await repo.project_files.get(file_id, owner_id)
        if not project_file:
            raise KnowledgeError("Project file not found", status_code=404)
        if project_file.get("project_id") != str(project_id):
            raise KnowledgeError("Project file does not belong to this project", status_code=400)
        if project_file.get("status", "active") != "active":
            raise KnowledgeError("Project file is not active", status_code=409)
        duplicate = await repo.knowledge_documents.find_one(
            {"owner_id": owner_id, "collection_id": collection_id, "project_file_id": file_id}
        )
        if duplicate:
            if duplicate.get("status") == "detached":
                timestamp = now_iso()
                reactivated = await repo.knowledge_documents.update_if(
                    duplicate.get("id"), owner_id, {"status": "detached"},
                    {
                        "status": "pending",
                        "processing_status": "pending",
                        "ingestion_status": "pending",
                        "error": None,
                        "error_code": None,
                        "error_message": None,
                        "chunk_count": 0,
                        "index": {"status": "not_indexed"},
                        "updated_at": timestamp,
                    },
                )
                attached.append(reactivated or duplicate)
            else:
                attached.append(duplicate)
            continue
        timestamp = now_iso()
        document = {
            "id": new_id(),
            "owner_id": owner_id,
            "project_id": str(project_id),
            "collection_id": collection_id,
            "project_file_id": file_id,
            "attachment_id": project_file.get("attachment_id"),
            "display_name": str(
                project_file.get("display_name") or project_file.get("filename") or file_id
            ),
            "ingestion_status": "pending",
            "parser": {"name": "attachment", "version": str(getattr(config, "KNOWLEDGE_PARSER_VERSION", "attachment-parser-v1"))},
            "chunker": {"version": str(getattr(config, "KNOWLEDGE_CHUNKER_VERSION", "fixed-paragraph-v1"))},
            "index": {"status": "not_indexed"},
            "status": "pending",
            "processing_status": "pending",
            "error": None,
            "error_code": None,
            "error_message": None,
            "content_hash": None,
            "chunk_count": 0,
            "token_estimate": 0,
            "embedding_model": None,
            "version": 1,
            "metadata": {},
            "provenance": {"project_file_id": file_id, "attachment_id": project_file.get("attachment_id"), "project_id": str(project_id)},
            "last_indexed_at": None,
            "index_generation": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        attached.append(await repo.knowledge_documents.insert(document))
    if attached:
        await _refresh_collection_counts(owner_id, collection_id)
    return [_public_document(document) for document in attached]


async def get_document(owner_id: str, document_id: str) -> Dict[str, Any]:
    """Return one owned knowledge document."""
    document = await repo.knowledge_documents.get(str(document_id), owner_id)
    if not document:
        raise KnowledgeError("Knowledge document not found", status_code=404)
    return document


async def list_documents(
    owner_id: str, collection_id: str, limit: int = 100, offset: int = 0
) -> List[Dict[str, Any]]:
    """Paginate documents of an owned collection."""
    await _require_collection(owner_id, collection_id)
    try:
        safe_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        safe_limit = 100
    try:
        safe_offset = max(0, int(offset))
    except (TypeError, ValueError):
        safe_offset = 0
    documents = await repo.knowledge_documents.list(
        {"owner_id": owner_id, "collection_id": str(collection_id)},
        limit=safe_limit,
        offset=safe_offset,
    )
    return [_public_document(document) for document in documents]


def _public_chunk(chunk: Dict[str, Any], include_embeddings: bool = False) -> Dict[str, Any]:
    public = {key: value for key, value in (chunk or {}).items() if key != "_id"}
    content = str(public.get("content") or "")
    public["content_preview"] = content[:_CHUNK_PREVIEW_CHARS]
    if not include_embeddings:
        public.pop("embedding", None)
    return public


async def list_chunks(
    owner_id: str,
    collection_id: str,
    document_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    include_embeddings: bool = False,
) -> List[Dict[str, Any]]:
    """Paginate chunks; embeddings are excluded unless explicitly requested."""
    await _require_collection(owner_id, collection_id)
    try:
        safe_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        safe_limit = 100
    try:
        safe_offset = max(0, int(offset))
    except (TypeError, ValueError):
        safe_offset = 0
    query: Dict[str, Any] = {"owner_id": owner_id, "collection_id": str(collection_id), "active": True, "index_status": "ready"}
    if document_id:
        query["document_id"] = str(document_id)
    docs = await repo.knowledge_chunks.list(
        query, sort_field="chunk_index", descending=False, limit=safe_limit, offset=safe_offset
    )
    return [_public_chunk(chunk, include_embeddings=include_embeddings) for chunk in docs]


# ---------------------------------------------------------------- chunking
def chunk_text(
    text: str, chunk_size: int, chunk_overlap: int, strategy: str = "fixed"
) -> List[str]:
    """Split text into chunks (pure function).

    * ``fixed``: sliding char windows of ``chunk_size`` with ``chunk_overlap``.
    * ``paragraph``: split on blank lines, pack paragraphs up to ``chunk_size``
      chars; each new chunk carries the previous chunk's trailing
      ``chunk_overlap`` chars as overlap.
    Empty pieces are dropped and every chunk is capped at the configured
    maximum chunk chars. Raises ``KnowledgeError`` on empty input.
    """
    if not text or not text.strip():
        raise KnowledgeError("Document text is empty")
    try:
        size = int(chunk_size)
    except (TypeError, ValueError):
        raise KnowledgeError("chunk_size must be a positive integer")
    try:
        overlap = int(chunk_overlap)
    except (TypeError, ValueError):
        raise KnowledgeError("chunk_overlap must be a non-negative integer")
    if size <= 0:
        raise KnowledgeError("chunk_size must be a positive integer")
    if overlap < 0 or overlap >= size:
        raise KnowledgeError("chunk_overlap must be >= 0 and smaller than chunk_size")
    mode = str(strategy or "fixed").strip().lower()
    if mode not in KNOWLEDGE_CHUNK_STRATEGIES:
        raise KnowledgeError("Unknown chunking strategy")
    try:
        cap = max(1, int(getattr(config, "KNOWLEDGE_MAX_CHUNK_CHARS", 4000)))
    except (TypeError, ValueError):
        cap = 4000

    pieces: List[str] = []
    if mode == "fixed":
        step = max(1, size - overlap)
        index = 0
        while index < len(text):
            pieces.append(text[index:index + size])
            if index + size >= len(text):
                break
            index += step
    else:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        if not paragraphs:
            raise KnowledgeError("Document text is empty")
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= size or not current:
                current = candidate if len(candidate) <= size else paragraph
                if len(candidate) > size and current == paragraph and len(paragraph) > size:
                    # A single oversized paragraph falls back to fixed slicing.
                    step = max(1, size - overlap)
                    start = 0
                    while start < len(paragraph):
                        pieces.append(paragraph[start:start + size])
                        if start + size >= len(paragraph):
                            break
                        start += step
                    current = ""
                continue
            pieces.append(current)
            carry = current[-overlap:] if overlap else ""
            current = f"{carry}\n\n{paragraph}" if carry else paragraph
        if current:
            pieces.append(current)
        if not pieces:
            pieces = [text]

    out: List[str] = []
    for piece in pieces:
        stripped = piece.strip()
        if not stripped:
            continue
        out.append(stripped[:cap])
    if not out:
        raise KnowledgeError("Document text is empty")
    return out


# ---------------------------------------------------------------- extraction
async def extract_document_text(
    owner_id: str, project_file_doc: Dict[str, Any]
) -> Tuple[str, Dict[str, Any]]:
    """Extract indexable text for a project file ``(text, meta)``.

    Loads the backing attachment (imported lazily to avoid a service cycle)
    and prefers its stored ``extracted_text``. PDFs without stored extraction
    are parsed with pypdf/PyPDF2 when installed. Images carry no text and
    raise 422. Output is capped (~200k chars) with a truncation flag in meta.
    """
    from . import attachments as attachment_service

    project_file = dict(project_file_doc or {})
    attachment_id = project_file.get("attachment_id")
    if not attachment_id:
        raise KnowledgeError("Project file has no readable content", status_code=422)
    try:
        attachment = await attachment_service.get_attachment(
            owner_id, str(attachment_id), include_text=True
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None) or 404
        raise KnowledgeError("Attachment content could not be loaded", status_code=status) from exc

    kind = str(attachment.get("kind") or "")
    mime_type = str(attachment.get("mime_type") or "")
    if kind == "image":
        raise KnowledgeError("Attachment has no extractable text", status_code=422)

    extracted = attachment.get("extracted_text") or ""
    source = "attachment_extraction"
    pages = (attachment.get("metadata") or {}).get("pages")
    if not extracted.strip() and (kind == "pdf" or mime_type == "application/pdf"):
        data, _doc = await attachment_service.read_attachment_bytes(owner_id, str(attachment_id))
        text, status, page_count = attachment_service.process_pdf(data)
        if status == "unavailable":
            raise KnowledgeError(
                "PDF text extraction is unavailable (no PDF library installed)", status_code=422
            )
        if status == "failed" or not (text or "").strip():
            raise KnowledgeError("Attachment has no extractable text", status_code=422)
        extracted = text
        source = "pdf_parse"
        pages = page_count
    if not extracted.strip() and kind == "text":
        data, _doc = await attachment_service.read_attachment_bytes(owner_id, str(attachment_id))
        extracted = data.decode("utf-8", errors="replace")
        source = "raw_decode"
    if not extracted.strip():
        raise KnowledgeError("Attachment has no extractable text", status_code=422)

    truncated = False
    if len(extracted) > _MAX_EXTRACT_CHARS:
        extracted = extracted[:_MAX_EXTRACT_CHARS]
        truncated = True
    meta: Dict[str, Any] = {
        "source": source,
        "chars": len(extracted),
        "truncated": truncated,
        "mime_type": mime_type,
        "kind": kind,
        "attachment_id": str(attachment_id),
    }
    if pages is not None:
        meta["pages"] = pages
    return extracted, meta


# ---------------------------------------------------------------- indexing
async def _mark_document(owner_id: str, document_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Patch a document, keeping error fields truthful for the latest attempt.

    A patch carrying an actual error records the error text and (unless the
    caller supplied one) the default ``KNOWLEDGE_INDEX_ERROR`` code. A success
    patch (no error, or ``error`` explicitly ``None``) clears
    ``error``/``error_code``/``error_message`` so a prior attempt's failure
    code never lingers on a ready document. Explicit caller codes such as
    ``EMBEDDING_PROVIDER_ERROR`` are always preserved.
    """
    patch = dict(patch or {})
    patch["updated_at"] = now_iso()
    if "status" in patch and "processing_status" not in patch:
        patch["processing_status"] = patch["status"]
    if "status" in patch and "ingestion_status" not in patch:
        patch["ingestion_status"] = patch["status"]
    error_value = patch.get("error")
    has_error = error_value is not None and str(error_value).strip() != ""
    if "error" in patch:
        if has_error:
            if "error_message" not in patch:
                patch["error_message"] = error_value
            if "error_code" not in patch:
                patch["error_code"] = "KNOWLEDGE_INDEX_ERROR"
        else:
            # Success/clear: never leave a stale failure code behind.
            patch["error"] = None
            patch["error_code"] = None
            patch["error_message"] = None
    if patch.get("status") in ("failed", "unavailable") and "index" not in patch:
        patch["index"] = {"status": "failed"}
    updated = await repo.knowledge_documents.update(document_id, owner_id, patch)
    return updated or {}


async def index_collection(
    owner_id: str,
    project_id: str,
    collection_id: str,
    document_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Embed every pending/failed document of a collection.

    Per-document pipeline: mark processing -> extract text -> chunk ->
    resolve provider -> embed batches -> replace chunks (old chunks of the
    document are deleted first; the document version is bumped) -> attach
    embeddings -> mark ready with counts. ``EmbeddingError`` marks the
    document ``unavailable``; any other per-doc failure marks it ``failed``.
    A single document never aborts the run. Collection totals are refreshed.
    """
    collection = _require_active(await _require_collection(owner_id, collection_id))
    if collection.get("project_id") != str(project_id):
        raise KnowledgeError("Knowledge collection does not belong to this project", status_code=400)
    chunking = normalize_chunking_config(collection.get("chunking_config"))
    embedding_config = normalize_embedding_config(collection.get("embedding_config"))
    try:
        per_collection_cap = max(1, int(getattr(config, "KNOWLEDGE_MAX_CHUNKS_PER_COLLECTION", 5000)))
    except (TypeError, ValueError):
        per_collection_cap = 5000

    docs = await repo.knowledge_documents.list(
        {"owner_id": owner_id, "collection_id": collection_id}, limit=5000
    )
    selected = {str(value) for value in (document_ids or [])}
    retryable = [doc for doc in docs if doc.get("status") in ("pending", "failed", "unavailable") and (not selected or str(doc.get("id")) in selected)]

    processed = 0
    ready = 0
    failed = 0
    errors: List[Dict[str, Any]] = []

    for document in retryable:
        document_id = document.get("id")
        claimed = await repo.knowledge_documents.update_if(
            document_id, owner_id, {"status": document.get("status")},
            {"status": "processing", "error": None, "error_code": None, "error_message": None},
        )
        if not claimed:
            continue
        processed += 1
        generation = new_id()
        staged_chunk_ids: List[str] = []
        try:
            project_file = await repo.project_files.get(str(document.get("project_file_id") or ""), owner_id)
            if not project_file:
                raise KnowledgeError("Project file not found", status_code=404)
            text, meta = await extract_document_text(owner_id, project_file)
            chunks = chunk_text(
                text, chunking["chunk_size"], chunking["chunk_overlap"], chunking["strategy"]
            )
            try:
                provider = await resolve_embedding_provider(owner_id, embedding_config)
            except EmbeddingError as exc:
                await _mark_document(owner_id, document_id, {"status": "unavailable", "error": exc.message, "error_code": "EMBEDDING_PROVIDER_ERROR"})
                failed += 1
                errors.append({"document_id": document_id, "error": exc.message, "error_code": "EMBEDDING_PROVIDER_ERROR"})
                continue
            try:
                vectors = await provider.embed(chunks)
            except EmbeddingError as exc:
                await _mark_document(owner_id, document_id, {"status": "unavailable", "error": exc.message, "error_code": "EMBEDDING_RESPONSE_ERROR"})
                failed += 1
                errors.append({"document_id": document_id, "error": exc.message, "error_code": "EMBEDDING_RESPONSE_ERROR"})
                continue
            vectors = validate_embedding_response(vectors, len(chunks), _int(embedding_config.get("dimension"), 0))
            existing_chunks = await repo.knowledge_chunks.count(
                {"owner_id": owner_id, "collection_id": collection_id, "document_id": {"$ne": document_id}, "active": True}
            )
            if existing_chunks + len(chunks) > per_collection_cap:
                message = "Collection chunk limit reached"
                await _mark_document(owner_id, document_id, {"status": "failed", "error": message, "error_code": "KNOWLEDGE_CHUNK_LIMIT_REACHED"})
                failed += 1
                errors.append({"document_id": document_id, "error": message, "error_code": "KNOWLEDGE_CHUNK_LIMIT_REACHED"})
                continue
            version = _int(document.get("version"), 1) + 1
            model_name = getattr(provider, "name", None) or str(embedding_config.get("model"))
            dim = getattr(provider, "dimension", 0) or _int(embedding_config.get("dimension"), 0)
            timestamp = now_iso()
            for index, (content, vector) in enumerate(zip(chunks, vectors)):
                chunk_doc = {
                    "id": new_id(),
                    "owner_id": owner_id,
                    "project_id": str(project_id),
                    "project_file_id": document.get("project_file_id"),
                    "collection_id": collection_id,
                    "document_id": document_id,
                    "content": content,
                    "chunk_index": index,
                    "token_estimate": _estimate_tokens(content),
                    "metadata": {"document_version": version, "extraction": meta.get("source")},
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "embedding": [],
                    "embedding_model": model_name,
                    "embedding_dim": int(dim or 0),
                    "embedding_provider": embedding_config.get("provider"),
                    "embedding_version": embedding_config.get("version"),
                    "provenance": {"project_id": str(project_id), "collection_id": collection_id, "document_id": document_id, "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()},
                    "embedding_fingerprint": embedding_config.get("fingerprint"),
                    "active": False,
                    "index_status": "staged",
                    "document_status": "processing",
                    "index_generation": generation,
                    "created_at": timestamp,
                }
                inserted = await repo.knowledge_chunks.insert(chunk_doc)
                await upsert_chunk_embedding(
                    owner_id, inserted.get("id"), list(vector or []), model_name, int(dim or 0)
                )
                staged_chunk_ids.append(inserted.get("id"))
            for staged_id in staged_chunk_ids:
                await repo.knowledge_chunks.update(
                    staged_id, owner_id,
                    {"active": True, "index_status": "ready", "document_status": "ready", "indexed_at": now_iso()},
                )
            await repo.knowledge_chunks.delete_many(
                {"owner_id": owner_id, "document_id": document_id, "id": {"$nin": staged_chunk_ids}}
            )
            await _mark_document(
                owner_id,
                document_id,
                {
                    "status": "ready",
                    "error": None,
                    "error_code": None,
                    "error_message": None,
                    "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "chunk_count": len(chunks),
                    "token_estimate": _estimate_tokens(text),
                    "embedding_model": model_name,
                    "version": version,
                    "metadata": meta,
                    "parser": {"name": meta.get("source") or "attachment", "version": str(getattr(config, "KNOWLEDGE_PARSER_VERSION", "attachment-parser-v1"))},
                    "chunker": {**chunking, "version": str(getattr(config, "KNOWLEDGE_CHUNKER_VERSION", "fixed-paragraph-v1"))},
                    "index": {"status": "ready", "generation": generation, "embedding": dict(embedding_config)},
                    "provenance": {"project_id": str(project_id), "collection_id": collection_id, "project_file_id": document.get("project_file_id"), "attachment_id": document.get("attachment_id"), "source": meta.get("source")},
                    "last_indexed_at": now_iso(),
                    "index_generation": generation,
                },
            )
            ready += 1
        except EmbeddingError as exc:
            await repo.knowledge_chunks.delete_many({"owner_id": owner_id, "document_id": document_id, "index_generation": generation, "active": False})
            await _mark_document(owner_id, document_id, {"status": "unavailable", "error": exc.message, "error_code": "EMBEDDING_RESPONSE_ERROR"})
            failed += 1
            errors.append({"document_id": document_id, "error": exc.message, "error_code": "EMBEDDING_RESPONSE_ERROR"})
        except KnowledgeError as exc:
            await repo.knowledge_chunks.delete_many({"owner_id": owner_id, "document_id": document_id, "index_generation": generation, "active": False})
            await _mark_document(owner_id, document_id, {"status": "failed", "error": exc.message})
            failed += 1
            errors.append({"document_id": document_id, "error": exc.message})
        except Exception as exc:
            await repo.knowledge_chunks.delete_many({"owner_id": owner_id, "document_id": document_id, "index_generation": generation, "active": False})
            message = "Document indexing failed"
            await _mark_document(owner_id, document_id, {"status": "failed", "error": message})
            failed += 1
            errors.append({"document_id": document_id, "error": message})

    await _refresh_collection_counts(owner_id, collection_id)
    return {"processed": processed, "ready": ready, "failed": failed, "errors": errors}


async def reindex_document(
    owner_id: str, project_id: str, collection_id: str, document_id: str
) -> Dict[str, Any]:
    """Explicitly schedule one document for a synchronous replacement index."""
    collection = _require_active(await _require_collection(owner_id, collection_id))
    if collection.get("project_id") != str(project_id):
        raise KnowledgeError("Knowledge collection does not belong to this project")
    document = await get_document(owner_id, document_id)
    if document.get("collection_id") != str(collection_id) or document.get("project_id") != str(project_id):
        raise KnowledgeError("Knowledge document does not belong to this collection", status_code=404)
    if document.get("status") == "processing":
        raise KnowledgeError("Knowledge document is already being indexed", status_code=409)
    if document.get("status") == "detached":
        raise KnowledgeError("Knowledge document is detached", status_code=409)
    updated = await repo.knowledge_documents.update_if(
        document_id, owner_id, {"status": document.get("status")},
        {"status": "pending", "error": None, "error_code": None, "error_message": None},
    )
    if not updated:
        raise KnowledgeError("Knowledge document changed while reindexing", status_code=409)
    return await index_collection(owner_id, project_id, collection_id, [document_id])


async def detach_document(
    owner_id: str, project_id: str, collection_id: str, document_id: str
) -> Dict[str, Any]:
    """Detach a document and remove its searchable chunks."""
    collection = _require_active(await _require_collection(owner_id, collection_id))
    if collection.get("project_id") != str(project_id):
        raise KnowledgeError("Knowledge collection does not belong to this project")
    document = await get_document(owner_id, document_id)
    if document.get("collection_id") != str(collection_id) or document.get("project_id") != str(project_id):
        raise KnowledgeError("Knowledge document does not belong to this collection", status_code=404)
    timestamp = now_iso()
    await repo.knowledge_chunks.delete_many({"owner_id": owner_id, "document_id": document_id})
    detached = await repo.knowledge_documents.update(
        document_id, owner_id,
        {
            "status": "detached",
            "processing_status": "detached",
            "ingestion_status": "detached",
            "chunk_count": 0,
            "index": {"status": "detached"},
            "error": None,
            "error_code": None,
            "error_message": None,
            "updated_at": timestamp,
        },
    ) or document
    await _refresh_collection_counts(owner_id, collection_id)
    return _public_document(detached)
