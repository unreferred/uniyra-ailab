"""Workspace attachment ingestion, processing and context integration.

The service owns the full attachment lifecycle: sanitize and validate an
upload, persist the raw bytes through :class:`AttachmentStorage`, extract a
bounded, client-safe text representation, and hand context components to the
execution pipeline. Everything returns plain ``dict`` values so routers,
context assembly and telemetry stay decoupled from persistence details.

Security invariants enforced here:
* client file names are sanitized and never used as a storage path,
* per-file / per-message / total byte limits are enforced before persistence,
* MIME is sniffed from magic bytes; a declared MIME is never trusted alone,
* SVG and script-bearing content is refused; HTML is decoded as plain text,
* ``storage_ref`` and physical paths are stripped from every public projection,
* owner/project scoping is re-checked on every read, mutation and deletion.

Extraction is intentionally best-effort: a document that cannot be parsed is
still stored (the source is never destroyed) with a ``failed`` status.
"""
import base64
import csv
import hashlib
import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .. import config
from .. import repository as repo
from ..models import new_id, now_iso
from ..storage import get_attachment_storage

# ---------------------------------------------------------------- supported sets
SUPPORTED_TEXT_EXTENSIONS = {
    "txt", "md", "markdown", "json", "yaml", "yml", "csv", "tsv", "log", "xml",
    "html", "htm", "ini", "toml", "cfg", "conf", "env", "py", "js", "jsx", "ts",
    "tsx", "java", "go", "rs", "rb", "php", "c", "h", "cpp", "hpp", "cs", "kt",
    "swift", "sh", "bash", "zsh", "sql", "css", "scss", "vue", "svelte",
}
SUPPORTED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
SUPPORTED_PDF_EXTENSIONS = {"pdf"}

# Explicitly refused regardless of MIME declaration: active content that must
# never be rendered or executed by a viewer.
_UNSAFE_EXTENSIONS = {"svg"}

_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
_PDF_MIMES = {"application/pdf"}

_EXTENSION_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "json": "application/json",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "yaml": "text/yaml",
    "yml": "text/yaml",
    "xml": "text/xml",
    "html": "text/html",
    "htm": "text/html",
    "md": "text/markdown",
    "markdown": "text/markdown",
}

_TEXT_PREVIEW_CHARS = 2000

# Fallback limits if the config module has not yet been extended; the config
# values always win when present so behaviour is centrally tunable.
_DEFAULT_MAX_ATTACHMENT_SIZE_MB = 10
_DEFAULT_MAX_ATTACHMENTS_PER_MESSAGE = 10
_DEFAULT_MAX_TOTAL_ATTACHMENT_SIZE_MB = 50


# ---------------------------------------------------------------- errors
class AttachmentError(ValueError):
    """Base class for client-safe attachment failures."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UnsupportedAttachmentError(AttachmentError):
    """Raised when an attachment type is not permitted."""


class AttachmentLimitError(AttachmentError):
    """Raised when an attachment exceeds a configured limit."""


# ---------------------------------------------------------------- repository
_fallback_attachments = None


def _attachments():
    """Return the attachments repository.

    ``repository.attachments`` is the canonical registration. A lazily created
    equivalent is used as a fallback so this module remains usable during
    rollout without editing the shared repository registry.
    """
    global _fallback_attachments
    existing = getattr(repo, "attachments", None)
    if existing is not None:
        return existing
    if _fallback_attachments is None:
        _fallback_attachments = repo.Repository("attachments")
    return _fallback_attachments


def _config_mb(name: str, default: float) -> float:
    value = getattr(config, name, None)
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# ---------------------------------------------------------------- filename / MIME
def sanitize_filename(name: str) -> str:
    """Return a safe display name with no directory components or dot-dot."""
    raw = str(name or "").replace("\x00", "")
    # Normalise both separators, then keep only the final component.
    raw = raw.replace("\\", "/").split("/")[-1]
    # Drop control characters (including newlines and tabs) but keep spaces.
    raw = "".join(ch for ch in raw if ord(ch) >= 32 and ord(ch) != 127)
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = raw.replace("..", "_").strip(". ")
    if len(raw) > 200:
        root, ext = os.path.splitext(raw)
        ext = ext[:20]
        raw = root[: max(0, 200 - len(ext))] + ext
    return raw or "attachment"


def detect_extension(filename: str) -> str:
    """Return the lowercase extension of a sanitized filename without a dot."""
    _, ext = os.path.splitext(sanitize_filename(filename))
    return ext[1:].lower()


def _magic_mime(data: bytes) -> Optional[str]:
    if not data:
        return None
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    return None


def _extension_mime(extension: str) -> Optional[str]:
    if not extension:
        return None
    if extension in _EXTENSION_MIME:
        return _EXTENSION_MIME[extension]
    if extension in SUPPORTED_TEXT_EXTENSIONS:
        return "text/plain"
    return None


def _looks_like_text(data: bytes) -> bool:
    if not data:
        return False
    return b"\x00" not in data[:8192]


def detect_mime(
    filename: str, data: bytes, declared_mime: Optional[str]
) -> str:
    """Resolve a MIME type, trusting magic bytes over extension over declared.

    An image extension (png/jpg/jpeg/webp) or an ``image/*`` declaration is
    only honoured when the payload carries a matching image magic-byte
    signature. Content wins over the extension: ``foo.png`` holding non-PNG
    bytes is rejected instead of being misclassified as an image. A declared
    PDF must carry a ``%PDF`` header within its first 1024 bytes.
    """
    declared = (declared_mime or "").strip().lower().split(";")[0].strip() or None
    extension = detect_extension(filename)

    magic = _magic_mime(data)
    if magic:
        if declared and declared.startswith("image/") and magic not in _IMAGE_MIMES:
            raise UnsupportedAttachmentError(
                "Declared image content does not match the uploaded data"
            )
        return magic

    # No recognised magic bytes. The extension and declared MIME are not
    # trusted for binary formats: a matching magic signature is required.
    wants_image = extension in SUPPORTED_IMAGE_EXTENSIONS or bool(
        declared and declared.startswith("image/")
    )
    if wants_image:
        raise UnsupportedAttachmentError(
            "The uploaded file content is not a valid image or its MIME type "
            "does not match (missing or unrecognised image magic bytes)"
        )

    wants_pdf = extension in SUPPORTED_PDF_EXTENSIONS or declared in _PDF_MIMES
    if wants_pdf:
        if b"%PDF" in data[:1024]:
            # PDFs may carry a small amount of leading junk before the header.
            return "application/pdf"
        raise UnsupportedAttachmentError(
            "The uploaded file content is not a valid PDF or its MIME type "
            "does not match (missing %PDF header)"
        )

    extension_mime = _extension_mime(extension)
    if extension_mime:
        return extension_mime

    if declared and (declared.startswith("text/") or declared == "application/json"):
        if _looks_like_text(data):
            return declared
    return "application/octet-stream"


def classify_kind(extension: str, mime: str) -> str:
    """Classify an attachment as text, image, pdf or unsupported."""
    mime = mime or ""
    if extension in SUPPORTED_IMAGE_EXTENSIONS or mime in _IMAGE_MIMES:
        return "image"
    if extension in SUPPORTED_PDF_EXTENSIONS or mime in _PDF_MIMES:
        return "pdf"
    if (
        extension in SUPPORTED_TEXT_EXTENSIONS
        or mime.startswith("text/")
        or mime == "application/json"
    ):
        return "text"
    return "unsupported"


def _unsupported_message(extension: str) -> str:
    label = f".{extension}" if extension else "this file type"
    return (
        f"Unsupported attachment type ({label}). Supported types are plain "
        "text and code files, PDF documents, and images (png, jpg, jpeg, webp)."
    )


# ---------------------------------------------------------------- validation
def validate_upload(
    owner_id: str,
    project_id: str,
    *,
    filename: str,
    data: bytes,
    declared_mime: Optional[str],
    existing_count: int,
    existing_total_bytes: int,
) -> Dict[str, Any]:
    """Validate an upload against type and size policy, returning its shape."""
    if not isinstance(data, (bytes, bytearray)):
        raise AttachmentError("Attachment data must be bytes")
    data = bytes(data)
    if not data:
        raise AttachmentError("Attachment is empty")

    max_file_bytes = int(_config_mb("MAX_ATTACHMENT_SIZE_MB", _DEFAULT_MAX_ATTACHMENT_SIZE_MB) * 1024 * 1024)
    if len(data) > max_file_bytes:
        limit_mb = max_file_bytes / (1024 * 1024)
        limit_mb_text = f"{limit_mb:.2f}" if limit_mb >= 1 else f"{limit_mb:.4f}"
        raise AttachmentLimitError(
            f"File exceeds the {max_file_bytes}-byte ({limit_mb_text} MB) per-file limit"
        )

    max_count = int(getattr(config, "MAX_ATTACHMENTS_PER_MESSAGE", _DEFAULT_MAX_ATTACHMENTS_PER_MESSAGE))
    if int(existing_count) + 1 > max_count:
        raise AttachmentLimitError(
            f"A message may contain at most {max_count} attachments"
        )

    max_total_mb = _config_mb(
        "MAX_TOTAL_ATTACHMENT_SIZE_MB", _DEFAULT_MAX_TOTAL_ATTACHMENT_SIZE_MB
    )
    max_total_bytes = int(max_total_mb * 1024 * 1024)
    if int(existing_total_bytes) + len(data) > max_total_bytes:
        raise AttachmentLimitError(
            f"Attachments exceed the {max_total_bytes // (1024 * 1024)} MB total limit"
        )

    sanitized = sanitize_filename(filename)
    extension = detect_extension(sanitized)

    if extension in _UNSAFE_EXTENSIONS:
        raise UnsupportedAttachmentError(
            "SVG attachments are not supported because they can carry active content"
        )

    mime_type = detect_mime(sanitized, data, declared_mime)  # may raise
    kind = classify_kind(extension, mime_type)

    if kind == "unsupported":
        raise UnsupportedAttachmentError(_unsupported_message(extension))
    if kind == "image" and extension and extension not in SUPPORTED_IMAGE_EXTENSIONS:
        raise UnsupportedAttachmentError(
            "Unsupported image format (png, jpg, jpeg, webp only)"
        )

    return {
        "filename": sanitized,
        "mime_type": mime_type,
        "kind": kind,
        "extension": extension,
        "size_bytes": len(data),
    }


# ---------------------------------------------------------------- extraction
def process_text(data: bytes) -> Tuple[str, str]:
    """Decode text bytes faithfully (invalid sequences replaced, never fatal)."""
    return data.decode("utf-8", errors="replace"), "extracted"


def process_pdf(data: bytes) -> Tuple[str, str, Optional[int]]:
    """Extract PDF text with page markers, degrading gracefully.

    ``pypdf`` is preferred with ``PyPDF2`` as a fallback. A missing library
    yields ``("", "unavailable", None)`` instead of raising, and a parse error
    yields ``("", "failed", None)``.
    """
    reader_cls = None
    try:
        from pypdf import PdfReader as reader_cls  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader as reader_cls  # type: ignore
        except Exception:
            return "", "unavailable", None

    try:
        reader = reader_cls(io.BytesIO(data))
        chunks: List[str] = []
        count = 0
        for index, page in enumerate(reader.pages, start=1):
            count = index
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            chunks.append(f"\n\n--- page {index} ---\n\n{page_text}")
        return "".join(chunks), "extracted", count
    except Exception:
        return "", "failed", None


def process_image(data: bytes) -> Tuple[Dict[str, Any], str]:
    """Read image dimensions/format via PIL; never OCR, never raise."""
    try:
        from PIL import Image
    except Exception:
        return {"error": "unreadable_image"}, "failed"
    try:
        with Image.open(io.BytesIO(data)) as image:
            return (
                {
                    "width": image.width,
                    "height": image.height,
                    "format": image.format,
                    "mode": image.mode,
                },
                "extracted",
            )
    except Exception:
        return {"error": "unreadable_image"}, "failed"


def _csv_metadata(text: str) -> Dict[str, Any]:
    """Cheap CSV schema/preview that never raises on malformed input."""
    try:
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return {"headers": [], "row_count": 0}
        return {
            "headers": [str(cell) for cell in rows[0]],
            "row_count": max(0, len(rows) - 1),
        }
    except Exception:
        return {"headers": [], "row_count": 0}


# ---------------------------------------------------------------- processing
def _process_payload(
    kind: str, extension: str, data: bytes
) -> Dict[str, Any]:
    """Run the extractor for a kind and return normalized extraction fields."""
    result: Dict[str, Any] = {
        "extraction_status": "failed",
        "processing_status": "failed",
        "extracted_text": None,
        "image_metadata": None,
        "metadata": {},
    }

    if kind == "text":
        text, status = process_text(data)
        result["extracted_text"] = text
        result["extraction_status"] = status
        result["processing_status"] = "processed"
        metadata: Dict[str, Any] = {"render_as": "text"}
        if extension in ("html", "htm"):
            # Stored as text, but a client must never render it as markup.
            metadata["render_as"] = "text"
            metadata["safe_render"] = False
        if extension in ("csv", "tsv"):
            metadata["csv"] = _csv_metadata(text)
        result["metadata"] = metadata

    elif kind == "pdf":
        text, status, pages = process_pdf(data)
        result["extracted_text"] = text or None
        result["extraction_status"] = status
        result["processing_status"] = "processed" if status != "failed" else "failed"
        result["metadata"] = {"pages": pages, "render_as": "text"}

    elif kind == "image":
        image_metadata, status = process_image(data)
        result["image_metadata"] = image_metadata
        result["extraction_status"] = status
        result["processing_status"] = "processed" if status == "extracted" else "failed"
        result["metadata"] = {"render_as": "none"}

    return result


# ---------------------------------------------------------------- create
async def create_attachment(
    owner_id: str,
    project_id: str,
    *,
    filename: str,
    data: bytes,
    declared_mime: Optional[str] = None,
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate, persist and process one attachment; returns a public dict."""
    raw = bytes(data) if isinstance(data, (bytes, bytearray)) else None
    if raw is None:
        raise AttachmentError("Attachment data must be bytes")

    sanitized = sanitize_filename(filename)

    # Limits are scoped to the owning message when known, else conversation.
    scope: Dict[str, Any] = {"owner_id": owner_id}
    if message_id:
        scope["message_id"] = message_id
    elif conversation_id:
        scope["conversation_id"] = conversation_id
    existing = await _attachments().list(scope, limit=1000)
    existing_count = len(existing)
    existing_total = sum(int(doc.get("size_bytes") or 0) for doc in existing)

    validated = validate_upload(
        owner_id,
        project_id,
        filename=sanitized,
        data=raw,
        declared_mime=declared_mime,
        existing_count=existing_count,
        existing_total_bytes=existing_total,
    )

    sha256 = hashlib.sha256(raw).hexdigest()
    attachment_id = new_id()
    storage = get_attachment_storage()

    try:
        storage_ref = await storage.save(owner_id, attachment_id, sanitized, raw)
    except Exception as exc:
        raise AttachmentError("Attachment could not be stored") from exc

    processed = _process_payload(validated["kind"], validated["extension"], raw)
    extracted_text = processed.get("extracted_text")
    timestamp = now_iso()

    document = {
        "id": attachment_id,
        "owner_id": owner_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "filename": sanitized,
        "original_filename": sanitize_filename(filename),
        "mime_type": validated["mime_type"],
        "kind": validated["kind"],
        "extension": validated["extension"],
        "size_bytes": validated["size_bytes"],
        "storage_type": "filesystem",
        "storage_ref": storage_ref,
        "sha256": sha256,
        "processing_status": processed.get("processing_status", "failed"),
        "extraction_status": processed.get("extraction_status", "failed"),
        "char_count": len(extracted_text) if extracted_text else 0,
        "extracted_text": extracted_text,
        "text_preview": (extracted_text or "")[:_TEXT_PREVIEW_CHARS],
        "image_metadata": processed.get("image_metadata"),
        "metadata": processed.get("metadata") or {},
        "smart_explanation": None,
        "detached": False,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    try:
        inserted = await _attachments().insert(document)
    except Exception as exc:
        # Best-effort cleanup: never leave a blob with no metadata record.
        try:
            await storage.delete(storage_ref)
        except Exception:
            pass
        raise AttachmentError("Attachment could not be stored") from exc

    return _public_attachment(inserted or document, include_text=False)


# ---------------------------------------------------------------- read
def _public_attachment(doc: Dict[str, Any], include_text: bool = False) -> Dict[str, Any]:
    """Strip internal storage details from an attachment record."""
    if not doc:
        return {}
    public = {key: value for key, value in doc.items() if key not in ("storage_ref", "_id")}
    if not include_text:
        public.pop("extracted_text", None)
    public["has_extracted_text"] = bool(doc.get("extracted_text"))
    if "text_preview" not in public:
        public["text_preview"] = (doc.get("extracted_text") or "")[:_TEXT_PREVIEW_CHARS]
    return public


async def list_attachments(
    owner_id: str,
    *,
    project_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"owner_id": owner_id}
    if project_id:
        query["project_id"] = project_id
    if conversation_id:
        query["conversation_id"] = conversation_id
    try:
        safe_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        safe_limit = 100
    try:
        safe_offset = max(0, int(offset))
    except (TypeError, ValueError):
        safe_offset = 0
    docs = await _attachments().list(query, limit=safe_limit, offset=safe_offset)
    return [_public_attachment(doc) for doc in docs]


async def get_attachment_doc(owner_id: str, attachment_id: str) -> Dict[str, Any]:
    """Internal: return the raw attachment document or raise a 404 error."""
    doc = await _attachments().get(attachment_id, owner_id)
    if not doc:
        raise AttachmentError("Attachment not found", status_code=404)
    return doc


async def get_attachment(
    owner_id: str,
    attachment_id: str,
    project_id: Optional[str] = None,
    *,
    include_text: bool = False,
) -> Dict[str, Any]:
    doc = await get_attachment_doc(owner_id, attachment_id)
    if project_id is not None and doc.get("project_id") != project_id:
        raise AttachmentError("Attachment not found", status_code=404)
    return _public_attachment(doc, include_text=include_text)


async def read_attachment_bytes(
    owner_id: str, attachment_id: str
) -> Tuple[bytes, Dict[str, Any]]:
    doc = await get_attachment_doc(owner_id, attachment_id)
    storage_ref = doc.get("storage_ref")
    if not storage_ref:
        raise AttachmentError("Attachment not found", status_code=404)
    try:
        data = await get_attachment_storage().read(storage_ref)
    except (FileNotFoundError, ValueError):
        raise AttachmentError("Attachment content not found", status_code=404)
    return data, doc


# ---------------------------------------------------------------- mutate
async def delete_attachment(
    owner_id: str, attachment_id: str, *, project_id: Optional[str] = None
) -> Dict[str, Any]:
    """Delete an attachment, or detach it when evidence preservation applies."""
    doc = await get_attachment_doc(owner_id, attachment_id)
    if project_id is not None and doc.get("project_id") != project_id:
        raise AttachmentError("Attachment not found", status_code=404)

    project_file_refs = await repo.project_files.count({"owner_id": owner_id, "attachment_id": attachment_id, "status": {"$ne": "deleted"}})
    knowledge_refs = await repo.knowledge_documents.count({"owner_id": owner_id, "attachment_id": attachment_id, "status": {"$ne": "detached"}})
    if project_file_refs or knowledge_refs:
        raise AttachmentError(
            "Attachment is still referenced by a project file or knowledge document",
            status_code=409,
        )
    referenced = bool(doc.get("referenced_by_run")) or bool(doc.get("run_ids"))
    if referenced:
        timestamp = now_iso()
        updated = await _attachments().update(
            attachment_id,
            owner_id,
            {"detached": True, "detached_at": timestamp, "updated_at": timestamp},
        )
        return {
            "detached": True,
            "attachment": _public_attachment(updated or doc),
        }

    storage_ref = doc.get("storage_ref")
    if storage_ref:
        try:
            await get_attachment_storage().delete(storage_ref)
        except Exception:
            pass
    await _attachments().delete(attachment_id, owner_id)
    return {"deleted": True}


async def mark_attachment_used(owner_id: str, attachment_id: str, run_id: str) -> None:
    """Record that a run consumed an attachment; best-effort, never raises."""
    if not run_id:
        return
    try:
        doc = await _attachments().get(attachment_id, owner_id)
        if not doc:
            return
        run_ids = list(doc.get("run_ids") or [])
        if run_id not in run_ids:
            run_ids.append(run_id)
        await _attachments().update(
            attachment_id,
            owner_id,
            {
                "referenced_by_run": True,
                "run_ids": run_ids,
                "updated_at": now_iso(),
            },
        )
    except Exception:
        return


# ---------------------------------------------------------------- context
async def validate_attachment_ids(
    owner_id: str, project_id: Optional[str], attachment_ids: List[str]
) -> List[Dict[str, Any]]:
    """Fetch each attachment id, rejecting missing or cross-project records."""
    docs: List[Dict[str, Any]] = []
    for raw_id in attachment_ids or []:
        attachment_id = str(raw_id)
        doc = await _attachments().get(attachment_id, owner_id)
        if not doc:
            raise AttachmentError("Attachment not found", status_code=404)
        if project_id is not None and doc.get("project_id") != project_id:
            raise AttachmentError("Attachment not found", status_code=404)
        docs.append(doc)
    return docs


async def attachment_context_components(
    owner_id: str, project_id: Optional[str], attachment_ids: List[str]
) -> List[Dict[str, Any]]:
    """Build context components (shape-compatible with context.make_component)."""
    docs = await validate_attachment_ids(owner_id, project_id, attachment_ids)
    components: List[Dict[str, Any]] = []
    for doc in docs:
        kind = doc.get("kind")
        document_metadata = doc.get("metadata") or {}
        metadata: Dict[str, Any] = {
            "attachment_id": doc.get("id"),
            "filename": doc.get("filename"),
            "mime_type": doc.get("mime_type"),
            "kind": kind,
            "size_bytes": doc.get("size_bytes"),
            "sha256": doc.get("sha256"),
            "processing_status": doc.get("processing_status"),
            "extraction_status": doc.get("extraction_status"),
        }

        if kind == "image":
            content = (
                f"[image attachment: {doc.get('filename')} "
                f"({doc.get('mime_type')}), {doc.get('size_bytes')} bytes]"
            )
            metadata["image_metadata"] = doc.get("image_metadata")
            metadata["render_as"] = "none"
        else:
            content = doc.get("extracted_text") or ""
            if not content:
                content = (
                    f"[attachment: {doc.get('filename')} "
                    f"({doc.get('mime_type')}), {doc.get('size_bytes')} bytes]"
                )
            metadata["render_as"] = document_metadata.get("render_as", "text")
            if kind == "pdf":
                metadata["pages"] = document_metadata.get("pages")

        components.append(
            {
                "component_type": "ATTACHMENTS",
                "source_ref": doc.get("id"),
                "content": content,
                "metadata": metadata,
                "token_count": None,
                "included": True,
                "truncated": False,
                "truncation_reason": None,
            }
        )
    return components


async def build_image_content_block(owner_id: str, attachment_id: str) -> Dict[str, Any]:
    """Return a normalized multimodal image content block for execution."""
    data, doc = await read_attachment_bytes(owner_id, attachment_id)
    if doc.get("kind") != "image":
        raise AttachmentError("Attachment is not an image")
    return {
        "type": "image",
        "mime_type": doc.get("mime_type"),
        "filename": doc.get("filename"),
        "attachment_id": doc.get("id"),
        "data_base64": base64.b64encode(data).decode("ascii"),
        "sha256": doc.get("sha256"),
    }


# ---------------------------------------------------------------- vision gating
def attachment_requires_vision(attachment_docs: List[Dict[str, Any]]) -> bool:
    return any(
        isinstance(doc, dict) and doc.get("kind") == "image"
        for doc in (attachment_docs or [])
    )


def unsupported_for_model(
    attachment_docs: List[Dict[str, Any]], model_supports_vision: bool
) -> Optional[str]:
    """Return a clear message when image inputs cannot reach the model."""
    if not model_supports_vision and attachment_requires_vision(attachment_docs):
        return (
            "The selected model does not support image attachments. Remove the "
            "image attachment(s) or choose a vision-capable model."
        )
    return None
