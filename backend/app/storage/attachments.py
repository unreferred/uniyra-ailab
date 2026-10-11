"""Filesystem-backed attachment content storage.

Attachment bytes never live in Mongo: this module is the only place that knows
where a blob physically lives. Callers persist an opaque, *relative*
``storage_ref`` and resolve it back through :class:`AttachmentStorage`, so no
physical server path is ever surfaced to clients or stored alongside metadata.

Security posture
----------------
* ``owner_id`` / ``attachment_id`` / every ``storage_ref`` component is strictly
  validated: ``/``, ``\\``, ``..`` and NUL are rejected outright.
* The final path is resolved with ``os.path.realpath`` and must stay inside the
  configured root (defeats both ``..`` traversal and symlink escape).
* Stored file names are derived from a content hash plus a sanitized extension,
  never from the client-supplied file name.

The implementation is deliberately dependency-free (stdlib only) and keeps the
root directory read lazy inside each method so tests can monkeypatch
``app.config.ATTACHMENT_STORAGE_DIR``.
"""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Protocol

# Extension must be short and strictly alphanumeric to be usable as a suffix.
_MAX_EXTENSION_LENGTH = 10

# Characters that may never appear inside a single path component.
_FORBIDDEN_COMPONENT_CHARS = ("/", "\\", "\x00")


def _sanitize_component(component: str, field: str = "component") -> str:
    """Validate one path component, rejecting traversal and separators."""
    if not isinstance(component, str) or not component:
        raise ValueError(f"Invalid {field}")
    if component == "." or component == "..":
        raise ValueError(f"Invalid {field}")
    if ".." in component:
        raise ValueError(f"Invalid {field}")
    for char in _FORBIDDEN_COMPONENT_CHARS:
        if char in component:
            raise ValueError(f"Invalid {field}")
    return component


def _safe_extension(filename: str) -> str:
    """Return a whitelisted ``.ext`` suffix or ``""``.

    The extension is lowercased, must be alphanumeric and at most
    ``_MAX_EXTENSION_LENGTH`` characters long. Anything else is dropped rather
    than repaired so a hostile name can never influence the stored path.
    """
    base = os.path.basename(str(filename or "").replace("\\", "/"))
    _, ext = os.path.splitext(base)
    ext = ext.lstrip(".").lower()
    if ext and len(ext) <= _MAX_EXTENSION_LENGTH and ext.isalnum():
        return "." + ext
    return ""


class AttachmentStorage(Protocol):
    """Async contract for attachment blob persistence."""

    async def save(
        self, owner_id: str, attachment_id: str, filename: str, data: bytes
    ) -> str: ...

    async def read(self, storage_ref: str) -> bytes: ...

    async def delete(self, storage_ref: str) -> None: ...

    def absolute_path(self, storage_ref: str) -> str: ...


class FilesystemAttachmentStorage:
    """Store attachment bytes under ``<root>/<owner_id>/<attachment_id>/``."""

    def __init__(self, root: Optional[str] = None):
        self._root_override = str(root) if root else None

    # ------------------------------------------------------------- internals
    def _root(self) -> Path:
        if self._root_override:
            raw = self._root_override
        else:
            from .. import config

            raw = getattr(config, "ATTACHMENT_STORAGE_DIR", None) or os.path.join(
                tempfile.gettempdir(), "omnilab-attachments"
            )
        return Path(os.path.realpath(str(raw)))

    def _components(self, storage_ref: str) -> list:
        if not isinstance(storage_ref, str) or not storage_ref:
            raise ValueError("Invalid storage reference")
        if storage_ref.startswith(("/", "\\")):
            raise ValueError("Invalid storage reference")
        parts = storage_ref.split("/")
        return [_sanitize_component(part, "storage reference") for part in parts]

    def _resolve(self, *components: str) -> Path:
        root = self._root()
        safe = [_sanitize_component(component) for component in components]
        candidate = root.joinpath(*safe)
        resolved = Path(os.path.realpath(str(candidate)))
        if resolved != root and root not in resolved.parents:
            raise ValueError("Attachment path escapes storage root")
        return resolved

    # ---------------------------------------------------------------- public
    async def save(
        self, owner_id: str, attachment_id: str, filename: str, data: bytes
    ) -> str:
        owner_id = _sanitize_component(owner_id, "owner_id")
        attachment_id = _sanitize_component(attachment_id, "attachment_id")
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("Attachment data must be bytes")
        data = bytes(data)

        stored_name = hashlib.sha256(data).hexdigest()[:16] + _safe_extension(filename)
        directory = self._resolve(owner_id, attachment_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / stored_name

        # Write to a sibling temp file and atomically rename so a crashed write
        # can never leave a truncated blob at the canonical path.
        tmp_target = directory / (stored_name + ".part")
        with open(tmp_target, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_target, target)

        return f"{owner_id}/{attachment_id}/{stored_name}"

    async def read(self, storage_ref: str) -> bytes:
        path = self._resolve(*self._components(storage_ref))
        if not path.is_file():
            raise FileNotFoundError(storage_ref)
        return path.read_bytes()

    async def delete(self, storage_ref: str) -> None:
        path = self._resolve(*self._components(storage_ref))
        attachment_dir = path.parent
        root = self._root()
        # Only ever remove the per-attachment directory, never the root itself.
        if attachment_dir == root:
            return
        if attachment_dir.exists():
            shutil.rmtree(attachment_dir, ignore_errors=True)

    def absolute_path(self, storage_ref: str) -> str:
        return str(self._resolve(*self._components(storage_ref)))
