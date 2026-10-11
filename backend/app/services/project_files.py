"""Project file registry: stable metadata pointers over attachment blobs.

A project file is a project-scoped record referencing one attachment with its
own display name, description, tags and lifecycle status. All mutations here
are soft: bytes are never physically deleted, so evidence referenced by runs
or knowledge documents is preserved. Only ``services.attachments`` is imported
(import-cycle rule); :exc:`ProjectError` is shared with project_manager.
"""
import re
from typing import Any, Dict, List, Optional

from .. import config
from .. import repository as repo
from ..models import PROJECT_FILE_STATUSES, new_id, now_iso
from .attachments import AttachmentError, create_attachment


class ProjectError(ValueError):
    """Client-safe project-file failure carrying an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code



_ALLOWED_PATCH_FIELDS = ("display_name", "description", "tags", "status")


def _file_limit_bytes() -> int:
    try:
        return int(getattr(config, "PROJECT_FILE_MAX_BYTES"))
    except (TypeError, ValueError):
        return 20 * 1024 * 1024


async def create_from_attachment(
    owner_id: str,
    project_id: str,
    attachment_id: str,
    display_name: Optional[str] = None,
    description: str = "",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Register an existing attachment as a project file.

    The attachment must exist under the same owner and project: missing is a
    404, cross-project is a 403. Idempotent per attachment: when an active
    project file already references ``attachment_id`` it is returned as-is.
    """
    attachment = await repo.attachments.get(attachment_id, owner_id)
    if not attachment:
        raise ProjectError("Attachment not found", 404)
    if attachment.get("project_id") != project_id:
        raise ProjectError("Attachment does not belong to this project", 403)
    existing = await repo.project_files.find_one(
        {
            "owner_id": owner_id,
            "project_id": project_id,
            "attachment_id": attachment_id,
            "status": "active",
        }
    )
    if existing:
        return existing
    timestamp = now_iso()
    document = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "attachment_id": attachment_id,
        "display_name": (display_name or "").strip()
        or attachment.get("filename")
        or "attachment",
        "description": description or "",
        "tags": list(tags or []),
        "mime_type": attachment.get("mime_type"),
        "kind": attachment.get("kind"),
        "size_bytes": attachment.get("size_bytes"),
        "sha256": attachment.get("sha256"),
        "processing_status": "ready",
        "status": "active",
        "metadata": {},
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    return await repo.project_files.insert(document)


async def upload_project_file(
    owner_id: str,
    project_id: str,
    filename: str,
    data: bytes,
    declared_mime: Optional[str] = None,
    display_name: Optional[str] = None,
    description: str = "",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Validate size, persist bytes via attachments, then register the file.

    Attachment validation failures are re-raised as ProjectError with the same
    status code so routers have a single error type to translate.
    """
    raw = bytes(data) if isinstance(data, (bytes, bytearray)) else b""
    limit = _file_limit_bytes()
    if len(raw) > limit:
        raise ProjectError(
            "File exceeds the %d-byte project file limit" % limit, 413
        )
    try:
        attachment = await create_attachment(
            owner_id,
            project_id,
            filename=filename,
            data=raw,
            declared_mime=declared_mime,
        )
    except AttachmentError as exc:
        raise ProjectError(exc.message, exc.status_code) from exc
    return await create_from_attachment(
        owner_id,
        project_id,
        attachment.get("id"),
        display_name=display_name,
        description=description or "",
        tags=tags,
    )


async def list_project_files(
    owner_id: str,
    project_id: str,
    *,
    search: Optional[str] = None,
    kind: Optional[str] = None,
    tag: Optional[str] = None,
    status: str = "active",
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List project files; ``status="all"`` includes detached and deleted."""
    query: Dict[str, Any] = {"owner_id": owner_id, "project_id": project_id}
    if status != "all":
        if status not in PROJECT_FILE_STATUSES:
            raise ProjectError("Invalid file status: %s" % status, 400)
        query["status"] = status
    if kind:
        query["kind"] = kind
    if tag:
        query["tags"] = tag
    if search:
        rx = {"$regex": re.escape(str(search)), "$options": "i"}
        query["$or"] = [{"display_name": rx}, {"description": rx}]
    try:
        safe_limit = max(1, min(int(limit or 100), 500))
    except (TypeError, ValueError):
        safe_limit = 100
    try:
        safe_offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        safe_offset = 0
    return await repo.project_files.list(
        query, limit=safe_limit, offset=safe_offset
    )


async def get_project_file(owner_id: str, file_id: str) -> Optional[Dict[str, Any]]:
    """Return one project file document or None. Routers enforce project match."""
    return await repo.project_files.get(file_id, owner_id)


async def update_project_file(
    owner_id: str, file_id: str, patch: dict
) -> Optional[Dict[str, Any]]:
    """Apply a soft metadata patch (display_name/description/tags/status).

    Unknown fields are ignored; an invalid status raises ProjectError 400.
    Returns the updated document, or None when the file does not exist.
    """
    updates: Dict[str, Any] = {}
    for key in _ALLOWED_PATCH_FIELDS:
        value = (patch or {}).get(key)
        if value is not None:
            updates[key] = value
    if "status" in updates and updates["status"] not in PROJECT_FILE_STATUSES:
        raise ProjectError(
            "Invalid file status: %s" % updates["status"], 400
        )
    if "tags" in updates:
        updates["tags"] = list(updates["tags"] or [])
    if not updates:
        return await repo.project_files.get(file_id, owner_id)
    updates["updated_at"] = now_iso()
    return await repo.project_files.update(file_id, owner_id, updates)


async def soft_delete_project_file(
    owner_id: str, file_id: str
) -> Optional[Dict[str, Any]]:
    """Mark a project file deleted. Raw attachment bytes stay; evidence preserved."""
    updated = await repo.project_files.update(
        file_id, owner_id, {"status": "deleted", "updated_at": now_iso()}
    )
    if updated:
        timestamp = now_iso()
        await repo.knowledge_chunks.delete_many({"owner_id": owner_id, "project_file_id": file_id})
        documents = await repo.knowledge_documents.list(
            {"owner_id": owner_id, "project_file_id": file_id}, limit=5000
        )
        document_ids = [document.get("id") for document in documents if document.get("id")]
        if document_ids:
            await repo.knowledge_chunks.delete_many({"owner_id": owner_id, "document_id": {"$in": document_ids}})
        for document in documents:
            await repo.knowledge_documents.update(
                document.get("id"), owner_id,
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
            )
        # Persisted collection counters must reflect the detach immediately so
        # list and detail endpoints agree (no import cycle: lazy import).
        collection_ids = {
            str(document.get("collection_id"))
            for document in documents
            if document.get("collection_id")
        }
        if collection_ids:
            from . import knowledge as knowledge_service

            for collection_id in collection_ids:
                await knowledge_service._refresh_collection_counts(owner_id, collection_id)
    return updated


async def file_references(
    owner_id: str, file_doc: Optional[dict]
) -> Dict[str, Any]:
    """Count knowledge documents and runs referencing this file."""
    file_doc = file_doc or {}
    attachment_id = file_doc.get("attachment_id")
    file_id = file_doc.get("id")
    references: Dict[str, Any] = {
        "knowledge_documents": 0,
        "runs": 0,
        "attachment_id": attachment_id,
    }
    if file_id:
        references["knowledge_documents"] = await repo.knowledge_documents.count(
            {"owner_id": owner_id, "project_file_id": file_id}
        )
    if attachment_id:
        references["runs"] = await repo.runs.count(
            {"owner_id": owner_id, "attachment_ids": attachment_id}
        )
    return references
