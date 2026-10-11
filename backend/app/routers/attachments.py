"""Attachment upload, retrieval, preview and deletion endpoints (Phase 3)."""
from typing import List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     Response, UploadFile)

from .. import config
from .. import repository as repo
from ..deps import get_current_user
from ..services import attachments as attachment_service
from ..services.attachments import AttachmentError

router = APIRouter(prefix="/attachments", tags=["attachments"])


def _http_error(err: AttachmentError) -> HTTPException:
    return HTTPException(
        status_code=getattr(err, "status_code", None) or 400,
        detail=getattr(err, "message", None) or str(err),
    )


def _safe_header_filename(doc: dict) -> str:
    name = str(doc.get("filename") or "attachment")
    return name.replace('"', "").replace("\r", "").replace("\n", "")


@router.post("")
async def upload_attachments(
    files: List[UploadFile] = File(...),
    project_id: str = Form(...),
    conversation_id: Optional[str] = Form(None),
    message_id: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
):
    owner_id = user["id"]
    if not await repo.projects.get(project_id, owner_id):
        raise HTTPException(status_code=400, detail="Project not found")

    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    max_bytes = int(float(config.MAX_ATTACHMENT_SIZE_MB) * 1024 * 1024)
    max_count = int(config.MAX_ATTACHMENTS_PER_MESSAGE)
    max_total_bytes = int(float(config.MAX_TOTAL_ATTACHMENT_SIZE_MB) * 1024 * 1024)

    existing: List[dict] = []
    if conversation_id:
        existing = await attachment_service.list_attachments(
            owner_id, conversation_id=conversation_id, limit=500
        )
    existing_count = len(existing)
    running_total = sum(int(doc.get("size_bytes") or 0) for doc in existing)

    created: List[dict] = []
    for index, upload in enumerate(files):
        if existing_count + index + 1 > max_count:
            raise HTTPException(
                status_code=400,
                detail=f"A message may contain at most {max_count} attachments",
            )
        data = await upload.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Attachment exceeds the {max_bytes}-byte ({max_bytes / 1048576:.2f} MB) per-file limit",
            )
        running_total += len(data)
        if running_total > max_total_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Attachments exceed the {max_total_bytes // (1024 * 1024)} MB total limit",
            )
        try:
            created.append(
                await attachment_service.create_attachment(
                    owner_id,
                    project_id,
                    filename=upload.filename or "attachment",
                    data=data,
                    declared_mime=upload.content_type,
                    conversation_id=conversation_id,
                    message_id=message_id,
                )
            )
        except AttachmentError as exc:
            raise _http_error(exc)
    return {"attachments": created}


@router.get("")
async def list_attachments(
    project_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    owner_id = user["id"]
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    items = await attachment_service.list_attachments(
        owner_id,
        project_id=project_id,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
    )
    query: dict = {"owner_id": owner_id}
    if project_id:
        query["project_id"] = project_id
    if conversation_id:
        query["conversation_id"] = conversation_id
    total = await repo.attachments.count(query)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/{attachment_id}")
async def get_attachment(
    attachment_id: str,
    project_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    try:
        return await attachment_service.get_attachment(
            user["id"], attachment_id, project_id
        )
    except AttachmentError as exc:
        raise _http_error(exc)


@router.get("/{attachment_id}/content")
async def get_attachment_content(
    attachment_id: str,
    project_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    owner_id = user["id"]
    try:
        data, doc = await attachment_service.read_attachment_bytes(owner_id, attachment_id)
    except AttachmentError as exc:
        raise _http_error(exc)
    if project_id is not None and doc.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="Attachment not found")

    kind = doc.get("kind")
    if kind == "image":
        return Response(
            content=data,
            media_type=doc.get("mime_type") or "application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{_safe_header_filename(doc)}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    text = doc.get("extracted_text")
    if not text and kind not in ("text", "pdf"):
        raise HTTPException(status_code=409, detail="No inline preview available")
    if text is None:
        text = data.decode("utf-8", errors="replace")
    return Response(content=text, media_type="text/plain; charset=utf-8")


@router.get("/{attachment_id}/text")
async def get_attachment_text(
    attachment_id: str,
    project_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    try:
        doc = await attachment_service.get_attachment(
            user["id"], attachment_id, project_id, include_text=True
        )
    except AttachmentError as exc:
        raise _http_error(exc)
    kind = doc.get("kind")
    text = doc.get("extracted_text")
    available = kind != "image" and bool(text)
    return {
        "text": text,
        "pages": (doc.get("metadata") or {}).get("pages"),
        "extraction_status": doc.get("extraction_status"),
        "char_count": doc.get("char_count") or 0,
        "available": available,
    }


@router.delete("/{attachment_id}")
async def delete_attachment(
    attachment_id: str,
    project_id: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    try:
        return await attachment_service.delete_attachment(
            user["id"], attachment_id, project_id=project_id
        )
    except AttachmentError as exc:
        raise _http_error(exc)
