"""Knowledge collection, document, chunk and retrieval endpoints."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import repository as repo
from ..deps import get_current_user
from ..models import KnowledgeCollectionCreate, KnowledgeCollectionUpdate, KnowledgeDocumentAttach, KnowledgeSearchRequest
from ..services import knowledge as knowledge_service
from ..services.knowledge import KnowledgeError
from ..services import retrieval as retrieval_service
from ..services.retrieval import RetrievalError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _http_error(err: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(err, "status_code", None) or 400,
        detail=getattr(err, "message", None) or str(err),
    )


def _record_activity(activity: Dict[str, Any]) -> None:
    """Best-effort project activity hook; never fails a request.

    ``project_manager`` does not exist yet (a later wave owns it) and must
    not be imported at module scope: the lazy import below keeps this router
    cycle-free while still recording activity once the service lands.
    """
    try:
        from ..services import project_manager  # type: ignore[attr-defined]
        recorder = getattr(project_manager, "record_activity", None)
        if callable(recorder):
            # Fire-and-forget for sync recorders; async recorders are left to
            # the later wave's contract (never awaited/never blocking here).
            recorder(**activity)
    except Exception:
        return


@router.get("")
async def list_knowledge_collections(
    project_id: Optional[str] = Query(default=None),
    include_deleted: bool = Query(default=False),
    user: dict = Depends(get_current_user),
):
    try:
        return await knowledge_service.list_collections(
            user["id"], project_id=project_id, include_deleted=include_deleted
        )
    except KnowledgeError as exc:
        raise _http_error(exc)


@router.post("")
async def create_knowledge_collection(
    body: KnowledgeCollectionCreate, user: dict = Depends(get_current_user)
):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    try:
        collection = await knowledge_service.create_collection(
            user["id"],
            {
                "project_id": body.project_id,
                "name": body.name,
                "description": body.description,
                "embedding_config": body.embedding_config,
                "chunking_config": body.chunking_config,
            },
        )
    except KnowledgeError as exc:
        raise _http_error(exc)
    _record_activity(
        {"owner_id": user["id"], "project_id": body.project_id,
         "activity_type": "knowledge_created", "ref_id": collection.get("id")}
    )
    return collection


@router.get("/{collection_id}")
async def get_knowledge_collection(collection_id: str, user: dict = Depends(get_current_user)):
    try:
        collection = await knowledge_service.get_collection(user["id"], collection_id)
        counts = await knowledge_service.collection_counts(user["id"], collection_id)
        return {**collection, **counts}
    except KnowledgeError as exc:
        raise _http_error(exc)


@router.patch("/{collection_id}")
async def update_knowledge_collection(
    collection_id: str, body: KnowledgeCollectionUpdate, user: dict = Depends(get_current_user)
):
    # NOTE: embedding/chunking config changes affect future indexing only;
    # already-indexed chunks keep the vectors they were built with until the
    # collection is reindexed.
    try:
        return await knowledge_service.update_collection(
            user["id"],
            collection_id,
            {
                "name": body.name,
                "description": body.description,
                "embedding_config": body.embedding_config,
                "chunking_config": body.chunking_config,
            },
        )
    except KnowledgeError as exc:
        raise _http_error(exc)


@router.delete("/{collection_id}")
async def delete_knowledge_collection(collection_id: str, user: dict = Depends(get_current_user)):
    try:
        collection = await knowledge_service.get_collection(user["id"], collection_id)
        deleted = await knowledge_service.delete_collection(user["id"], collection_id)
    except KnowledgeError as exc:
        raise _http_error(exc)
    _record_activity(
        {"owner_id": user["id"], "project_id": collection.get("project_id"),
         "activity_type": "knowledge_deleted", "ref_id": collection_id}
    )
    return deleted


@router.post("/{collection_id}/documents")
async def attach_knowledge_documents(
    collection_id: str, body: KnowledgeDocumentAttach, user: dict = Depends(get_current_user)
):
    try:
        collection = await knowledge_service.get_collection(user["id"], collection_id)
        documents = await knowledge_service.attach_files(
            user["id"], str(collection.get("project_id")), collection_id,
            list(body.project_file_ids or []),
        )
        return documents
    except KnowledgeError as exc:
        raise _http_error(exc)


@router.get("/{collection_id}/documents")
async def list_knowledge_documents(
    collection_id: str,
    limit: int = Query(default=100),
    offset: int = Query(default=0),
    user: dict = Depends(get_current_user),
):
    try:
        return await knowledge_service.list_documents(
            user["id"], collection_id, limit=limit, offset=offset
        )
    except KnowledgeError as exc:
        raise _http_error(exc)


@router.post("/{collection_id}/index")
async def index_knowledge_collection(collection_id: str, user: dict = Depends(get_current_user)):
    # Synchronous by design: fine for local scale; a later wave may queue it.
    try:
        collection = await knowledge_service.get_collection(user["id"], collection_id)
        summary = await knowledge_service.index_collection(
            user["id"], str(collection.get("project_id")), collection_id
        )
        _record_activity(
            {"owner_id": user["id"], "project_id": collection.get("project_id"),
             "activity_type": "knowledge_indexed", "ref_id": collection_id}
        )
        return summary
    except KnowledgeError as exc:
        raise _http_error(exc)


@router.get("/{collection_id}/chunks")
async def list_knowledge_chunks(
    collection_id: str,
    document_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100),
    offset: int = Query(default=0),
    user: dict = Depends(get_current_user),
):
    # Embeddings are excluded; content ships with a preview cap.
    try:
        return await knowledge_service.list_chunks(
            user["id"], collection_id, document_id=document_id, limit=limit, offset=offset
        )
    except KnowledgeError as exc:
        raise _http_error(exc)


@router.post("/search")
async def search_knowledge(body: KnowledgeSearchRequest, user: dict = Depends(get_current_user)):
    owner_id = user["id"]
    requested: List[str] = [str(cid) for cid in (body.collection_ids or []) if str(cid)]
    if requested:
        for collection_id in requested:
            collection = await repo.knowledge_collections.get(collection_id, owner_id)
            if not collection or collection.get("status") == "deleted":
                raise HTTPException(status_code=404, detail="Knowledge collection not found")
            if body.project_id and collection.get("project_id") != str(body.project_id):
                raise HTTPException(
                    status_code=400,
                    detail="Knowledge collection does not belong to this project",
                )
    try:
        return await retrieval_service.retrieve(
            owner_id,
            body.project_id,
            body.query,
            requested,
            top_k=body.top_k,
            score_threshold=body.score_threshold,
        )
    except RetrievalError as exc:
        raise _http_error(exc)


@router.post("/{collection_id}/documents/{document_id}/reindex")
async def reindex_knowledge_document(
    collection_id: str, document_id: str, user: dict = Depends(get_current_user)
):
    try:
        collection = await knowledge_service.get_collection(user["id"], collection_id)
        return await knowledge_service.reindex_document(
            user["id"], str(collection.get("project_id")), collection_id, document_id
        )
    except KnowledgeError as exc:
        raise _http_error(exc)


@router.delete("/{collection_id}/documents/{document_id}")
async def detach_knowledge_document(
    collection_id: str, document_id: str, user: dict = Depends(get_current_user)
):
    try:
        collection = await knowledge_service.get_collection(user["id"], collection_id)
        return await knowledge_service.detach_document(
            user["id"], str(collection.get("project_id")), collection_id, document_id
        )
    except KnowledgeError as exc:
        raise _http_error(exc)
