"""Credential and secret redaction for OmniLab V5 Marathon B.

Security invariant
------------------
Model output, Knowledge content, web content, tool results, and dataset cases
are all untrusted. Secret-shaped values must never enter judge inputs,
findings, scorecards, certification evidence, or trace metadata. This module
provides a small, dependency-free redaction layer that scrubs secret material
from arbitrary nested payloads before it is persisted, scored, or surfaced.

The module is intentionally self-contained and imports only ``re`` and
``typing`` so it can be used from any layer without creating import cycles.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEY_NAMES: tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "pwd",
    "authorization",
    "credential",
    "private_key",
    "client_secret",
    "access_key",
)

_OPENAI = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}")
_GITHUB = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")
_AWS_ACCESS_KEY_ID = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_AWS_SECRET_ACCESS_KEY = re.compile(
    r"""(?ix)
    aws_secret_access_key \s* [:=] \s*
    (?P<secret> ["']? [A-Za-z0-9/+=]{20,} ["']? )
    """
)
_SLACK = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN[^-]*?PRIVATE KEY-----[\s\S]*?-----END[^-]*?PRIVATE KEY-----"
)
_BEARER = re.compile(
    r"""(?ix)
    (?: authorization \s* [:=] \s* )? ["']? bearer \s+
    (?P<secret> [A-Za-z0-9\-._~+/]{8,}=* )
    """
)
_GENERIC_CREDENTIAL = re.compile(
    r"""(?ix)
    (?P<key>
        client[_-]?secret | api[_-]?key | apikey |
        access[_-]?token | refresh[_-]?token |
        secret | password | passwd | pwd |
        authorization | credential | bearer
    )
    \s* (?P<sep> ["']? \s* [:=] \s* )
    (?P<quote> ["']? )
    (?P<secret> [^\s"',;{}]+ )
    (?P=quote)
    """
)

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    _PRIVATE_KEY,
    _BEARER,
    _OPENAI,
    _GITHUB,
    _AWS_ACCESS_KEY_ID,
    _AWS_SECRET_ACCESS_KEY,
    _SLACK,
    _JWT,
    _GENERIC_CREDENTIAL,
)

_MAX_DEPTH = 12
_MAX_ITEMS = 200


def _replacement(match: re.Match[str]) -> str:
    """Replace a match, preserving any non-secret prefix/suffix context."""
    if "secret" in match.re.groupindex:
        val = match.group("secret")
        if val == REDACTED or val.lower() == "bearer":
            return match.group(0)
        prefix = match.group(0)[: match.start("secret") - match.start(0)]
        suffix = match.group(0)[match.end("secret") - match.start(0):]
        return prefix + REDACTED + suffix
    return REDACTED


def redact_text(value: str) -> str:
    """Redact every secret-shaped pattern occurrence in ``value``.

    Non-string input is returned unchanged. This function never raises and is
    idempotent: ``redact_text(redact_text(x)) == redact_text(x)``.
    """
    if not isinstance(value, str):
        return value
    try:
        text = value
        for pattern in SECRET_PATTERNS:
            text = pattern.sub(_replacement, text)
        return text
    except Exception:
        return value


def _is_marker_only_match(match: re.Match[str]) -> bool:
    """Return True when a match captured the redaction marker, not a credential.

    An already-redacted assignment such as ``password=[REDACTED]`` or
    ``{"api_key": "[REDACTED]"}`` is credential-shaped text: the generic
    credential pattern happily captures the marker as its ``secret`` group.
    Treating that as a real secret would make ``contains_secret`` permanently
    true for scrubbed payloads, so marker-only captures are ignored. Any real
    secret elsewhere in the same text is still detected, because each match is
    evaluated independently.
    """
    if "secret" in match.re.groupindex:
        val = match.group("secret")
        # ``_replacement`` already treats a bare scheme marker ("Bearer") as
        # context, not a credential; mirror that here so an already-scrubbed
        # ``Authorization: Bearer [REDACTED]`` is stable under redaction.
        if val == REDACTED or val.lower() == "bearer":
            return True
    return match.group(0) == REDACTED


def contains_secret(value: Any) -> bool:
    """Return True if any secret pattern matches the string form of ``value``.

    The :data:`REDACTED` marker itself is never considered a secret, so the
    result is stable under repeated redaction: a fully scrubbed payload returns
    ``False`` while any surviving real credential still returns ``True``.
    """
    try:
        text = value if isinstance(value, str) else str(value)
        for pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                if _is_marker_only_match(match):
                    continue
                return True
        return False
    except Exception:
        return False


def redact(value: Any) -> Any:
    """Deep-walk ``value`` and redact secret-shaped data.

    Dict keys whose lowercased name contains an entry from
    :data:`SENSITIVE_KEY_NAMES` keep their key but have their value replaced by
    :data:`REDACTED`, even when the value does not match a pattern. Strings are
    passed through :func:`redact_text`. Ints, floats, bools, and ``None`` are
    preserved. Depth and collection length are bounded. Never raises.
    """
    try:
        return _redact(value, 0)
    except Exception:
        return value


def _redact(value: Any, depth: int) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        if depth >= _MAX_DEPTH:
            return REDACTED
        out: dict[Any, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                break
            key_name = key.lower() if isinstance(key, str) else str(key).lower()
            if any(fragment in key_name for fragment in SENSITIVE_KEY_NAMES):
                out[key] = REDACTED
            else:
                out[key] = _redact(item, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        if depth >= _MAX_DEPTH:
            return REDACTED
        if isinstance(value, set):
            return {_redact(item, depth + 1) for item in list(value)[:_MAX_ITEMS]}
        items = [_redact(item, depth + 1) for item in list(value)[:_MAX_ITEMS]]
        return tuple(items) if isinstance(value, tuple) else items
    return value


def safe_metadata(value: Any) -> dict:
    """Return a redacted copy of ``value`` when it is a dict, else ``{}``."""
    if isinstance(value, dict):
        result = redact(value)
        return result if isinstance(result, dict) else {}
    return {}


def scrub_error(value: Any) -> Any:
    """Deep-redact an error payload (alias of :func:`redact`)."""
    return redact(value)
