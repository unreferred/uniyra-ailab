"""Structured, client-safe error values for provider execution.

Every provider failure is represented by a plain ``dict`` with a stable shape so
that routers, services and telemetry can record structured detail without ever
persisting raw upstream bodies, credentials or exception messages.

The canonical shape is produced by :func:`make_error`::

    {
        "code": str,
        "category": str,            # one of ERROR_CATEGORIES
        "message": str,            # short, safe, human-readable
        "retryable": bool,
        "source": str | None,
        "upstream_status": int | None,
        "details_safe": dict | None,  # JSON-serializable, secret-free
    }
"""
import asyncio
import json
from typing import Any, Dict, Optional

import httpx

ERROR_CATEGORIES = (
    "AUTH",
    "RATE_LIMIT",
    "TIMEOUT",
    "PROVIDER",
    "MODEL_UNAVAILABLE",
    "VALIDATION",
    "NETWORK",
    "MALFORMED_RESPONSE",
    "INTERNAL",
    "CANCELLED",
)

# Substrings that mark a detail key as credential-like. Values under such keys
# are dropped before the details ever leave the process.
_SENSITIVE_KEY_PARTS = (
    "key",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "authorization",
    "auth",
    "cookie",
    "session",
)

_GENERIC_MESSAGES = {
    "AUTH": "Provider authentication failed",
    "RATE_LIMIT": "Provider rate limit exceeded",
    "TIMEOUT": "Provider request timed out",
    "PROVIDER": "Provider request failed",
    "MODEL_UNAVAILABLE": "Requested model is unavailable",
    "VALIDATION": "Request validation failed",
    "NETWORK": "Could not reach the provider",
    "MALFORMED_RESPONSE": "Provider returned an invalid response",
    "INTERNAL": "Internal execution error",
    "CANCELLED": "Request was cancelled",
}


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively drop credential-like keys and non-primitive values."""
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
                continue
            cleaned[key] = _scrub(raw_value, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_scrub(item, depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # Anything else (exceptions, bytes, objects) is reduced to a safe type name.
    return type(value).__name__


def _sanitize_details(details_safe: Optional[dict]) -> Optional[dict]:
    if details_safe is None:
        return None
    if not isinstance(details_safe, dict):
        raise ValueError("details_safe must be a dict or None")
    cleaned = _scrub(details_safe)
    try:
        json.dumps(cleaned)
    except (TypeError, ValueError) as exc:
        raise ValueError("details_safe must be JSON-serializable") from exc
    return cleaned


def make_error(
    code: str,
    category: str,
    message: str,
    *,
    retryable: bool = False,
    source: Optional[str] = None,
    upstream_status: Optional[int] = None,
    details_safe: Optional[dict] = None,
) -> dict:
    """Build a structured error dict with a stable, secret-free shape."""
    if category not in ERROR_CATEGORIES:
        raise ValueError(f"Unsupported error category: {category!r}")
    return {
        "code": str(code),
        "category": category,
        "message": str(message),
        "retryable": bool(retryable),
        "source": source,
        "upstream_status": upstream_status,
        "details_safe": _sanitize_details(details_safe),
    }


def error_message(err: Any) -> str:
    """Human-readable message for a structured dict, plain string, or None."""
    if err is None:
        return ""
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        message = err.get("message")
        if isinstance(message, str) and message:
            return message
        code = err.get("code")
        return str(code) if code else "Execution error"
    return str(err)


def safe_error_message(err: Any) -> Optional[str]:
    """Category-level, identity-free message for a structured error.

    Used where a raw message must never be surfaced, e.g. blind Arena views:
    resolution errors embed the model/provider name in their message
    ("Model 'X' is disabled"), which would defeat blindness. Only the generic
    category text is returned; non-structured errors yield ``None`` so no
    upstream text can leak.
    """
    if not isinstance(err, dict):
        return None
    category = err.get("category")
    generic = _GENERIC_MESSAGES.get(category)
    if generic:
        return f"{generic} ({category})"
    return "Execution error"


def is_retryable(err: Any) -> bool:
    if isinstance(err, dict):
        return bool(err.get("retryable"))
    return False


def _structured(
    code: str,
    category: str,
    *,
    retryable: bool = False,
    source: Optional[str],
    upstream_status: Optional[int] = None,
    details_safe: Optional[dict] = None,
) -> dict:
    message = f"{_GENERIC_MESSAGES[category]} ({category})"
    return make_error(
        code,
        category,
        message,
        retryable=retryable,
        source=source,
        upstream_status=upstream_status,
        details_safe=details_safe,
    )


def classify_exception(exc: BaseException, *, source: Optional[str] = None) -> dict:
    """Map a common exception type to a safe structured error.

    Raw exception text is never used as the message; only the exception type
    name is retained (in ``details_safe``), which cannot leak secrets.
    """
    type_name = type(exc).__name__
    details = {"type": type_name}

    if isinstance(exc, asyncio.CancelledError):
        return _structured("CANCELLED", "CANCELLED", source=source, details_safe=details)
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        return _structured("TIMEOUT", "TIMEOUT", retryable=True, source=source, details_safe=details)
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return _structured("NETWORK_ERROR", "NETWORK", retryable=True, source=source, details_safe=details)
    if isinstance(exc, httpx.RequestError):
        return _structured("NETWORK_ERROR", "NETWORK", retryable=True, source=source, details_safe=details)
    if isinstance(exc, PermissionError):
        return _structured("AUTH_ERROR", "AUTH", source=source, details_safe=details)
    # JSONDecodeError and UnicodeDecodeError subclass ValueError, so they must
    # be matched first.
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        return _structured("MALFORMED_RESPONSE", "MALFORMED_RESPONSE", source=source, details_safe=details)
    if isinstance(exc, ValueError):
        return _structured("VALIDATION_ERROR", "VALIDATION", source=source, details_safe=details)
    return _structured("INTERNAL_ERROR", "INTERNAL", source=source, details_safe=details)


def classify_http_status(status: int, *, source: Optional[str] = None) -> dict:
    """Map an upstream HTTP status to a safe structured error."""
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0

    message = f"Provider request failed (HTTP {status})"
    if status in (401, 403):
        code, category, retryable = "AUTH_ERROR", "AUTH", False
    elif status == 429:
        code, category, retryable = "RATE_LIMITED", "RATE_LIMIT", True
    elif status in (408, 504):
        code, category, retryable = "TIMEOUT", "TIMEOUT", True
    elif status == 404:
        code, category, retryable = "MODEL_UNAVAILABLE", "MODEL_UNAVAILABLE", False
    elif status in (400, 422):
        code, category, retryable = "VALIDATION_ERROR", "VALIDATION", False
    elif 500 <= status < 600:
        code, category, retryable = "PROVIDER_ERROR", "PROVIDER", True
    else:
        code, category, retryable = "PROVIDER_ERROR", "PROVIDER", False
    return make_error(
        code,
        category,
        message,
        retryable=retryable,
        source=source,
        upstream_status=status,
    )
