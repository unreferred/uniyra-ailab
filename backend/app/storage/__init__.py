"""Attachment content storage package.

Exposes the :class:`AttachmentStorage` contract and a process-wide singleton
filesystem implementation. Import of the concrete class is deferred to first
use so importing this package never creates directories or touches config.
"""
from .attachments import AttachmentStorage, FilesystemAttachmentStorage

_storage_singleton = None

__all__ = [
    "AttachmentStorage",
    "FilesystemAttachmentStorage",
    "get_attachment_storage",
]


def get_attachment_storage() -> FilesystemAttachmentStorage:
    """Return the module-level singleton filesystem storage backend."""
    global _storage_singleton
    if _storage_singleton is None:
        from .attachments import FilesystemAttachmentStorage as _FilesystemAttachmentStorage

        _storage_singleton = _FilesystemAttachmentStorage()
    return _storage_singleton
