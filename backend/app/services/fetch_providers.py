"""Fetch provider registry and canonical fetch executor (Phase 4.2.3.1).

Provides an extensible provider-based architecture for URL fetch / extraction tools.
Standard HTTP Fetch and TinyFish Fetch are supported.
Applies strict SSRF checks and redirect validation using urlguard.

V4.2.3.1 provider credential ownership model (see module docstring — normative):
  Provider docs are GLOBAL (owner_id None): they are shared configuration, not
  per-user records. A ``credential_id`` vault reference therefore MUST be
  owner-scoped to be secure: at PATCH time we verify the vault credential
  belongs to the requesting user (repo.tool_credentials.get(credential_id,
  user_id)) and persist BOTH ``credential_id`` AND ``credential_owner_id``
  (the requesting user's id). At resolve time the vault lookup uses
  repo.tool_credentials.get(credential_id, credential_owner_id) — never an
  unscoped cross-owner lookup. A missing vault doc (deleted externally)
  resolves to no key (NOT_CONFIGURED), never to another owner's secret.
"""
from __future__ import annotations

import asyncio
import html
import os
import re
import time
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import httpx

from .. import config
from .. import repository as repo
from ..models import now_iso
from ..security import decrypt_secret, encrypt_secret
from ..urlguard import (
    LOCAL_EGRESS,
    REMOTE_EGRESS,
    UrlValidationError,
    async_validate_provider_url,
    async_validate_remote_target_url,
)
from . import valves as valve_lib
from .provider_quota import (
    PROVIDER_TINYFISH,
    RESOURCE_FETCH,
    ProviderRateLimited,
    consume_fetch,
    default_limits,
)

STANDARD_FETCH_PROVIDER_ID = "prov-fetch-standard"
TINYFISH_FETCH_PROVIDER_ID = "prov-fetch-tinyfish"
#: Canonical TinyFish Fetch endpoint. The endpoint is adapter-owned and NOT
#: user-configurable: the provider doc's ``configuration.base_url`` (writable
#: through the update API) deliberately has no effect on it. A narrowly-scoped
#: env override (``TINYFISH_FETCH_BASE_URL`` / ``TINYFISH_BASE_URL``) exists for
#: tests/ops and is fully SSRF-validated (as a provider endpoint) before use.
TINYFISH_FETCH_ENDPOINT = "https://api.fetch.tinyfish.ai"
# Ciphertext-copy source for ``reuse_search_credential``. Hardcoded id (not an
# import) to avoid a fetch<->search service import cycle.
_TINYFISH_SEARCH_PROVIDER_ID = "prov-search-tinyfish"

_MAX_FETCH_BYTES = 500 * 1024  # 500 KB limit
_MAX_REDIRECTS = 3


def _request_timeout_ceiling(attr: str, fallback: float) -> float:
    """Resolve a protected per-request timeout ceiling from ``app.config``.

    The attribute is read lazily (and accessed live) so tests/ops may
    monkeypatch the ceiling. Falls back to ``fallback`` when the attribute is
    missing or not coercible, and is never below 1 second.
    """
    try:
        from .. import config

        raw = getattr(config, attr, None)
        value = float(raw) if raw is not None else float(fallback)
    except (TypeError, ValueError, ImportError):  # pragma: no cover
        value = float(fallback)
    return max(1.0, value)


def _fetch_quota_limits(doc: Dict[str, Any]) -> Dict[str, int]:
    """Effective TinyFish Fetch URL quota windows from valve config.

    Provider/account limits, not run budgets; missing/invalid values fall back
    to config defaults. Values <= 0 fall back to the default.
    """
    cfg = (doc or {}).get("configuration") or {}
    defaults = default_limits(PROVIDER_TINYFISH, RESOURCE_FETCH)
    def _int(key: str, fallback: int) -> int:
        try:
            value = int(cfg.get(key))
        except (TypeError, ValueError):
            return int(fallback)
        return value if value > 0 else int(fallback)
    return {
        "minute": _int("fetch_urls_per_minute_limit", defaults["minute"]),
        "day": _int("fetch_urls_per_day_limit", defaults["day"]),
    }


def _quota_exceeded_error(exc: ProviderRateLimited) -> "FetchError":
    return FetchError(
        exc.error.get("message") or "TinyFish fetch provider quota exceeded",
        status_code=429,
        error_category="RATE_LIMIT",
        code="PROVIDER_RATE_LIMITED",
        provider="tinyfish",
        upstream_status=429,
        details=dict(exc.error),
    )


class FetchError(ValueError):
    """Raised for fetch contract violations, SSRF violations, or network failures.

    Positional parameters keep the historical ``(message, status_code,
    error_category)`` signature; the keyword-only attributes carry structured,
    secret-free provider detail up to ``ToolResult``.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_category: str = "FETCH_ERROR",
        *,
        code: Optional[str] = None,
        provider: Optional[str] = None,
        upstream_status: Optional[int] = None,
        details: Optional[dict] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_category = error_category
        self.code = code or "FETCH_PROVIDER_ERROR"
        self.provider = provider
        self.upstream_status = upstream_status
        self.details = details or {}


class _HTMLTextExtractor(HTMLParser):
    """Simple, dependency-free HTML title and readable text extractor."""

    def __init__(self):
        super().__init__()
        self._title_tokens: List[str] = []
        self._in_title = False
        self._text_tokens: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        tag_lower = tag.lower()
        if tag_lower == "title":
            self._in_title = True
        elif tag_lower in ("script", "style", "noscript", "svg", "head"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "title":
            self._in_title = False
        elif tag_lower in ("script", "style", "noscript", "svg", "head"):
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_tokens.append(data)
        elif self._skip_depth == 0:
            cleaned = data.strip()
            if cleaned:
                self._text_tokens.append(cleaned)

    def get_title(self) -> str:
        return " ".join(self._title_tokens).strip()

    def get_text(self) -> str:
        raw = " ".join(self._text_tokens)
        return re.sub(r"\s+", " ", raw).strip()


def _url_validation_error_code(exc: Exception) -> str:
    """Return upper-cased urlguard ``code`` handling old exceptions without it."""
    try:
        code = getattr(exc, "code", None)
    except Exception:
        code = None
    if code is None:
        return ""
    try:
        return str(code).strip().upper()
    except Exception:
        return ""


def _url_validation_message(exc: Exception) -> str:
    try:
        msg = getattr(exc, "message", None)
    except Exception:
        msg = None
    if isinstance(msg, str) and msg:
        return msg
    try:
        return str(exc) or ""
    except Exception:
        return ""


_DNS_RESOLVE_PHRASES = ("could not be resolved", "failed to resolve")
_DNS_NETWORK_PHRASES = (
    "name or service not known",
    "getaddrinfo failed",
    "nodename nor servname",
)
_MALFORMED_URL_PHRASES = (
    "must use http",
    "must include a host",
    "invalid port",
    "url is required",
    "provider url is required",
    "embedded credentials",
    "invalid url",
    "malformed",
)
_BLOCKED_CONTENT_PREFIXES = (
    "image/",
    "audio/",
    "video/",
    "application/octet-stream",
    "font/",
)


def _map_url_validation_error(exc: Exception) -> tuple:
    """Map UrlValidationError (old without code / new with code) to fetch error.

    Returns ``(status_code, error_category, code)`` with ``code`` matching
    ``error_category`` per the fetch contract.
    """
    code_up = _url_validation_error_code(exc)
    msg = _url_validation_message(exc)
    low = msg.lower() if isinstance(msg, str) else ""
    if code_up == "INVALID_URL":
        return (400, "INVALID_URL", "INVALID_URL")
    if code_up in ("DNS_RESOLUTION_FAILED", "DNS_FAILED", "DNS_ERROR"):
        return (502, "DNS_RESOLUTION_FAILED", "DNS_RESOLUTION_FAILED")
    if code_up.startswith("REDIRECT"):
        return (403, "REDIRECT_BLOCKED", "REDIRECT_BLOCKED")
    if any(ph in low for ph in _DNS_RESOLVE_PHRASES):
        return (502, "DNS_RESOLUTION_FAILED", "DNS_RESOLUTION_FAILED")
    if any(ph in low for ph in _MALFORMED_URL_PHRASES):
        return (400, "INVALID_URL", "INVALID_URL")
    return (403, "SSRF_BLOCKED", "SSRF_BLOCKED")


def _is_dns_network_message(text: str) -> bool:
    low = (text or "").lower()
    return any(ph in low for ph in _DNS_NETWORK_PHRASES)


def _safe_upstream_error(response: Any) -> Dict[str, Any]:
    """Extract ``{"error": {"code", "message"}}`` from an upstream response.

    Best-effort and strictly bounded: never returns headers, the API key, or
    the raw body. At most the upstream ``code`` and a 200-character message.
    """
    details: Dict[str, Any] = {}
    try:
        body = response.json()
    except Exception:
        return details
    if not isinstance(body, dict):
        return details
    error = body.get("error")
    code: Any = None
    message: Any = None
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
    elif isinstance(error, str):
        message = error
    else:
        code = body.get("code")
        message = body.get("message")
    if isinstance(code, str) and code.strip():
        details["upstream_code"] = code.strip()[:200]
    if isinstance(message, str) and message.strip():
        details["upstream_message"] = message.strip()[:200]
    return details


def _host_only(url: str) -> Optional[str]:
    """Return a bare, credential-free hostname for safe trace metadata."""
    try:
        host = urlsplit(str(url or "")).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.lower().rstrip(".")


def _per_url_failure(url: str, exc: Exception) -> Dict[str, Any]:
    """Build a secret-free per-URL failure record from an exception.

    Never includes the provider API key or raw upstream body: only the error
    code/message plus (when available) url/timeout_seconds/provider detail.
    """
    code = getattr(exc, "code", None) or "FETCH_PROVIDER_ERROR"
    raw_message = getattr(exc, "message", None)
    message = str(raw_message) if raw_message else str(exc)
    error: Dict[str, Any] = {"code": str(code), "message": message[:300]}
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        if details.get("url") is not None:
            error["url"] = details["url"]
        if details.get("timeout_seconds") is not None:
            error["timeout_seconds"] = details["timeout_seconds"]
    provider = getattr(exc, "provider", None)
    if provider is None and isinstance(details, dict):
        provider = details.get("provider")
    if provider:
        error["provider"] = provider
    return {"url": url, "status": "failed", "error": error}


class BaseFetchProvider(ABC):
    """Abstract fetch provider interface."""

    SUPPORTS_BATCH_FETCH = False

    def __init__(self, doc: Dict[str, Any]):
        self.doc = doc

    @property
    def id(self) -> str:
        return self.doc.get("id") or ""

    @property
    def name(self) -> str:
        return self.doc.get("name") or ""

    @property
    def provider_type(self) -> str:
        return self.doc.get("provider_type") or "unknown"

    @property
    def enabled(self) -> bool:
        return bool(self.doc.get("enabled", True))

    @property
    def capabilities(self) -> Dict[str, str]:
        return self.doc.get("capabilities") or {"fetch": "SUPPORTED"}

    @property
    def egress_mode(self) -> str:
        """Egress trust boundary for this provider.

        Defaults to ``LOCAL`` (this process performs the outbound connection and
        therefore resolves + SSRF-checks the target). Remote providers override
        this to ``REMOTE``.
        """
        return LOCAL_EGRESS

    @property
    def supports_batch_fetch(self) -> bool:
        return bool(getattr(type(self), "SUPPORTS_BATCH_FETCH", False))

    @property
    def batch_cap(self) -> int:
        """Provider execution batch size (NOT research breadth).

        Resolved from the provider's effective configuration
        (``max_urls`` / ``fetch_provider_batch_size``), clamped to the
        protected server ceiling ``config.MAX_BATCH_FETCH_URLS``. A missing or
        invalid value falls back to the ceiling. Never returns < 1.
        """
        cfg = self.doc.get("configuration") or {}
        raw = cfg.get("max_urls", cfg.get("fetch_provider_batch_size"))
        try:
            value = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            value = 0
        try:
            ceiling = int(getattr(config, "MAX_BATCH_FETCH_URLS", 5) or 5)
        except (TypeError, ValueError):
            ceiling = 5
        ceiling = max(1, ceiling)
        if value <= 0:
            value = ceiling
        return max(1, min(value, ceiling))

    async def fetch_many(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Default batch fetch: sequential, order-preserving, never raises.

        Clamps ``urls`` to the resolved provider batch cap (batch_cap), itself
        bounded by ``config.MAX_BATCH_FETCH_URLS``, and returns exactly
        one record per (clamped) input URL. Each success record is the adapter's
        normalized single-URL dict plus ``status="success"`` and the requested
        ``url``; each failure is a secret-free ``_per_url_failure`` record.
        """
        limit = self.batch_cap
        clamped = list(urls or [])[:limit]
        records: List[Dict[str, Any]] = []
        for url in clamped:
            try:
                result = await self.fetch(url)
            except FetchError as exc:
                records.append(_per_url_failure(url, exc))
                continue
            except Exception as exc:  # pragma: no cover - defensive
                records.append(_per_url_failure(url, exc))
                continue
            record = dict(result) if isinstance(result, dict) else {}
            record["status"] = "success"
            record["url"] = url
            records.append(record)
        return records

    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """Test provider connectivity."""
        raise NotImplementedError

    @abstractmethod
    async def fetch(self, url: str) -> Dict[str, Any]:
        """Fetch URL content with SSRF and redirect validation."""
        raise NotImplementedError


async def _resolve_vault_key(doc: Dict[str, Any]) -> Optional[str]:
    """Decrypt the vault-referenced key server-side only (never logged/returned).

    Owner-scoped lookup via the stored ``credential_owner_id``. Returns None
    when no reference is set, the vault doc is gone, or decryption fails.
    """
    credential_id = doc.get("credential_id")
    credential_owner_id = doc.get("credential_owner_id")
    if not credential_id or not credential_owner_id:
        return None
    try:
        vault_doc = await repo.tool_credentials.get(
            str(credential_id), str(credential_owner_id)
        )
    except Exception:
        return None
    if not vault_doc or not vault_doc.get("encrypted_value"):
        return None
    try:
        return decrypt_secret(vault_doc["encrypted_value"])
    except Exception:
        return None


class StandardFetchProvider(BaseFetchProvider):
    """Standard HTTP Fetch Provider using strict SSRF and redirect validation.

    Egress is LOCAL: the backend performs the request, so every target host is
    resolved locally and every resolved address (and every redirect hop) is
    SSRF-checked. This behaviour is intentionally unchanged.
    """

    SUPPORTS_BATCH_FETCH = True

    @property
    def egress_mode(self) -> str:
        return LOCAL_EGRESS

    async def fetch_many(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Bounded-concurrency LOCAL batch fetch; order-preserving, never raises.

        Each URL is fetched independently through :meth:`fetch` so every target
        keeps full local SSRF/redirect enforcement. A semaphore caps in-flight
        requests at ``config.FETCH_BATCH_CONCURRENCY`` (never more than the
        requested batch size).
        """
        limit = self.batch_cap
        clamped = list(urls or [])[:limit]
        if not clamped:
            return []
        concurrency = max(1, int(getattr(config, "FETCH_BATCH_CONCURRENCY", 2)))
        sem = asyncio.Semaphore(min(concurrency, len(clamped)))

        async def _one(url: str) -> Dict[str, Any]:
            async with sem:
                try:
                    result = await self.fetch(url)
                except Exception as exc:
                    return _per_url_failure(url, exc)
                record = dict(result) if isinstance(result, dict) else {}
                record["status"] = "success"
                record["url"] = url
                return record

        gathered = await asyncio.gather(
            *(_one(u) for u in clamped), return_exceptions=True
        )
        records: List[Dict[str, Any]] = []
        for url, outcome in zip(clamped, gathered):
            if isinstance(outcome, BaseException):
                records.append(_per_url_failure(url, outcome))
            else:
                records.append(outcome)
        return records

    async def test_connection(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "status": "CONNECTED",
            "message": "Standard HTTP fetch engine ready",
        }

    def _timeout(self) -> float:
        """Per-request timeout: valve value (default 15) capped by the ceiling."""
        cfg = self.doc.get("configuration") or {}
        try:
            timeout = float(cfg.get("timeout_seconds") or 15)
        except (TypeError, ValueError):
            timeout = 15.0
        ceiling = _request_timeout_ceiling("FETCH_REQUEST_TIMEOUT_MAX_SECONDS", 60.0)
        return max(1.0, min(timeout, ceiling))

    async def fetch(self, url: str) -> Dict[str, Any]:
        if not url or not isinstance(url, str) or not url.strip():
            raise FetchError(
                "URL is required", 400, "INVALID_URL", code="INVALID_URL",
            )
        requested_url = url if isinstance(url, str) else str(url)
        current_url = url.strip()

        # Follow redirects manually with SSRF validation on every hop.
        # Query strings are preserved: the validated URL is used VERBATIM.
        visited: List[str] = []
        redirect_count = 0

        while redirect_count <= _MAX_REDIRECTS:
            try:
                validated_url = await async_validate_provider_url(current_url)
            except UrlValidationError as exc:
                status, category, code = _map_url_validation_error(exc)
                raise FetchError(
                    f"SSRF validation blocked request to URL: {_url_validation_message(exc)}",
                    status_code=status,
                    error_category=category,
                    code=code,
                )

            visited.append(validated_url)

            try:
                async with httpx.AsyncClient(
                    follow_redirects=False,
                    timeout=self._timeout(),
                    headers={"User-Agent": "OmniLab-Agent/1.0 (Web Fetch)"},
                ) as cli:
                    r = await cli.get(validated_url)
            except httpx.TimeoutException:
                raise FetchError(
                    "Request to URL timed out",
                    504,
                    "FETCH_TIMEOUT",
                    code="FETCH_TIMEOUT",
                    provider="standard",
                    details={
                        "url": current_url,
                        "timeout_seconds": self._timeout(),
                        "provider": "standard",
                    },
                )
            except Exception as exc:
                msg = str(exc)[:150]
                if _is_dns_network_message(str(exc)):
                    raise FetchError(
                        f"DNS resolution failed: {msg}",
                        502,
                        "DNS_RESOLUTION_FAILED",
                        code="DNS_RESOLUTION_FAILED",
                    )
                raise FetchError(
                    f"Network request failed: {msg}", 502, "NETWORK_ERROR",
                    code="NETWORK_ERROR",
                )

            # Check for redirect (3xx): urljoin preserves query strings.
            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get("Location")
                if not location:
                    break
                next_url = urljoin(validated_url, location)
                if next_url in visited:
                    raise FetchError(
                        "Redirect loop detected", 400, "REDIRECT_LOOP",
                        code="REDIRECT_LOOP",
                    )
                current_url = next_url
                redirect_count += 1
                if redirect_count > _MAX_REDIRECTS:
                    raise FetchError(
                        "Too many redirects (limit 3)", 400, "TOO_MANY_REDIRECTS",
                        code="TOO_MANY_REDIRECTS",
                    )
                continue

            # Terminal (non-redirect) response: classify upstream errors first.
            status = int(r.status_code)
            if status == 403:
                content_type_raw = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                snippet = ""
                try:
                    snippet = (r.content or b"")[:500].decode("utf-8", errors="replace")
                except Exception:
                    snippet = ""
                details: Dict[str, Any] = {
                    "content_type": content_type_raw,
                    "bytes": len(r.content or b""),
                }
                if snippet.strip():
                    details["body_snippet"] = snippet.strip()[:500]
                raise FetchError(
                    f"Upstream denied the request (HTTP 403, {content_type_raw or 'unknown content type'})",
                    status_code=502,
                    error_category="UPSTREAM_403",
                    code="UPSTREAM_403",
                    upstream_status=403,
                    details=details,
                )
            if status == 429:
                raise FetchError(
                    "Upstream rate limited the request (HTTP 429)",
                    status_code=429,
                    error_category="UPSTREAM_429",
                    code="UPSTREAM_429",
                    upstream_status=429,
                )
            if status >= 500:
                raise FetchError(
                    f"Upstream server error (HTTP {status})",
                    status_code=502,
                    error_category="UPSTREAM_5XX",
                    code="UPSTREAM_5XX",
                    upstream_status=status,
                )

            # Successful or other terminal response
            content_type = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type and (
                content_type in _BLOCKED_CONTENT_PREFIXES
                or content_type.startswith("image/")
                or content_type.startswith("audio/")
                or content_type.startswith("video/")
                or content_type.startswith("font/")
                or content_type.startswith("application/octet-stream")
            ):
                raise FetchError(
                    f"Unsupported content type: {content_type or 'unknown'}",
                    status_code=502,
                    error_category="UNSUPPORTED_CONTENT_TYPE",
                    code="UNSUPPORTED_CONTENT_TYPE",
                    upstream_status=status,
                    details={"content_type": content_type},
                )
            full_bytes = r.content or b""
            if len(full_bytes) > _MAX_FETCH_BYTES:
                raise FetchError(
                    f"Response too large ({len(full_bytes)} bytes, limit {_MAX_FETCH_BYTES})",
                    status_code=502,
                    error_category="CONTENT_TOO_LARGE",
                    code="CONTENT_TOO_LARGE",
                    upstream_status=status,
                    details={"bytes": len(full_bytes), "limit": _MAX_FETCH_BYTES},
                )
            raw_bytes = full_bytes[:_MAX_FETCH_BYTES]
            encoding = r.encoding or "utf-8"
            try:
                text_content = raw_bytes.decode(encoding, errors="replace")
            except Exception:
                text_content = raw_bytes.decode("utf-8", errors="replace")

            title = ""
            extracted_text = text_content
            if "html" in content_type:
                try:
                    parser = _HTMLTextExtractor()
                    parser.feed(text_content)
                    title = parser.get_title()
                    extracted_text = parser.get_text()
                except Exception:
                    extracted_text = text_content

            return {
                "requested_url": requested_url,
                "final_url": validated_url,
                "status": status,
                "content_type": content_type or "text/plain",
                "title": title or None,
                "text": extracted_text[:100_000],
                "provider": self.id,
                "provider_type": "standard",
                "egress_mode": LOCAL_EGRESS,
                "provider_status": status,
                "target_url": validated_url,
                "metadata": {
                    "bytes": len(raw_bytes),
                    "redirect_count": redirect_count,
                    "encoding": encoding,
                    "source": "standard",
                    "egress_mode": LOCAL_EGRESS,
                    "provider_endpoint_host": _host_only(validated_url),
                    "provider_status": status,
                    "target_url": validated_url,
                },
            }

        raise FetchError(
            "Exceeded max redirects", 400, "TOO_MANY_REDIRECTS",
            code="TOO_MANY_REDIRECTS",
        )


class TinyFishFetchProvider(BaseFetchProvider):
    """TinyFish Fetch / Web Scraping Provider (REMOTE egress).

    OmniLab POSTs the target URL to the TinyFish service, which performs the
    actual fetch. The trust boundary is therefore the provider's egress: the
    target is validated syntactically/ by policy only and is NOT resolved
    locally. The provider endpoint itself is fully SSRF-validated before use.
    """

    SUPPORTS_BATCH_FETCH = True

    @property
    def egress_mode(self) -> str:
        return REMOTE_EGRESS

    def _get_api_key(self) -> Optional[str]:
        """Sync fallback: direct ``api_key_enc`` then ``TINYFISH_API_KEY`` env.

        Vault resolution is async and lives in ``_resolve_api_key``; this sync
        path is kept for compatibility and as the non-vault fallback.
        """
        enc = self.doc.get("api_key_enc")
        if enc:
            try:
                key = decrypt_secret(enc)
            except Exception:
                return None
            stripped = (key or "").strip()
            return stripped or None
        env_key = os.environ.get("TINYFISH_API_KEY")
        if env_key:
            stripped = env_key.strip()
            return stripped or None
        return None

    async def _resolve_api_key(self) -> Optional[str]:
        """Async key resolution order: (1) vault, (2) direct, (3) env.

        Strips the resolved key and treats an empty/whitespace value as not
        configured; secrets are never logged.
        """
        vault_key = await _resolve_vault_key(self.doc)
        if vault_key:
            stripped = vault_key.strip()
            if stripped:
                return stripped
        return self._get_api_key()

    def _get_base_url(self) -> str:
        """Return the adapter-owned canonical TinyFish fetch endpoint.

        The provider doc's ``configuration.base_url`` is intentionally IGNORED:
        it is user-influenceable through the update API and must never redirect
        OmniLab's authenticated provider call. Only a narrowly-scoped env
        override (for tests/ops) may change it, and that value is still fully
        validated before use.
        """
        override = (
            os.environ.get("TINYFISH_FETCH_BASE_URL")
            or os.environ.get("TINYFISH_BASE_URL")
        )
        return (override or TINYFISH_FETCH_ENDPOINT).rstrip("/")

    def _timeout(self) -> float:
        cfg = self.doc.get("configuration") or {}
        try:
            timeout = float(cfg.get("timeout_seconds") or 20)
        except (TypeError, ValueError):
            timeout = 20.0
        ceiling = _request_timeout_ceiling("FETCH_REQUEST_TIMEOUT_MAX_SECONDS", 60.0)
        return max(1.0, min(timeout, ceiling))

    def _format(self) -> str:
        cfg = self.doc.get("configuration") or {}
        value = str(cfg.get("format") or cfg.get("fetch_format") or "markdown").strip().lower()
        return value if value in ("markdown", "html", "json") else "markdown"

    async def test_connection(self) -> Dict[str, Any]:
        key = await self._resolve_api_key()
        if not key:
            return {
                "ok": False,
                "status": "NOT_CONFIGURED",
                "message": "TinyFish API key is not configured",
            }
        base = self._get_base_url()
        try:
            validated = await async_validate_provider_url(base)
        except UrlValidationError as exc:
            return {
                "ok": False,
                "status": "INVALID_URL",
                "message": f"Base URL SSRF validation failed: {exc.message}",
            }
        try:
            consume_fetch(_fetch_quota_limits(self.doc), 1)
        except ProviderRateLimited as exc:
            return {
                "ok": False,
                "status": "PROVIDER_RATE_LIMITED",
                "message": "TinyFish fetch provider quota exhausted",
                "details": dict(exc.error),
            }
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as cli:
                r = await cli.post(
                    validated,
                    headers={
                        "X-API-Key": key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "urls": ["https://example.com"],
                        "format": self._format(),
                        "ttl": 0,
                    },
                )
                if r.status_code in (401, 403):
                    return {
                        "ok": False,
                        "status": "INVALID_CREDENTIAL",
                        "message": "Invalid TinyFish API key",
                    }
                if r.status_code >= 500:
                    return {
                        "ok": False,
                        "status": "UNAVAILABLE",
                        "message": f"TinyFish service unavailable (HTTP {r.status_code})",
                    }
                if r.status_code >= 400:
                    return {
                        "ok": False,
                        "status": "UNAVAILABLE",
                        "message": f"TinyFish fetch API returned HTTP {r.status_code}",
                    }
                return {
                    "ok": True,
                    "status": "CONNECTED",
                    "message": "TinyFish fetch API reachable",
                }
        except httpx.TimeoutException:
            return {
                "ok": False,
                "status": "UNAVAILABLE",
                "message": "Connection to TinyFish timed out",
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "UNAVAILABLE",
                "message": f"Connection check failed: {str(exc)[:150]}",
            }

    async def fetch(self, url: str) -> Dict[str, Any]:
        if not url or not isinstance(url, str) or not url.strip():
            raise FetchError(
                "URL is required", 400, "INVALID_URL", code="INVALID_URL",
            )
        requested_url = url if isinstance(url, str) else str(url)
        key = await self._resolve_api_key()
        if not key:
            raise FetchError(
                "TinyFish API key is not configured. Live execution pending credentials.",
                status_code=400,
                error_category="NOT_CONFIGURED",
                code="NOT_CONFIGURED",
            )
        # --- provider endpoint: adapter-owned, FULL local validation -------
        provider_endpoint = self._get_base_url()
        try:
            validated_endpoint = await async_validate_provider_url(provider_endpoint)
        except UrlValidationError as exc:
            status, category, code = _map_url_validation_error(exc)
            # Endpoint failures are PROVIDER failures, never target DNS errors.
            raise FetchError(
                f"TinyFish provider endpoint validation failed: {_url_validation_message(exc)}",
                status_code=status,
                error_category=category,
                code=code,
                provider="tinyfish",
            )
        # --- target URL: REMOTE egress, syntax/policy only, NO local DNS ----
        try:
            target_url = await async_validate_remote_target_url(url.strip())
        except UrlValidationError as exc:
            status, category, code = _map_url_validation_error(exc)
            raise FetchError(
                f"Target URL validation failed: {_url_validation_message(exc)}",
                status_code=status,
                error_category=category,
                code=code,
            )

        try:
            consume_fetch(_fetch_quota_limits(self.doc), 1)
        except ProviderRateLimited as exc:
            raise _quota_exceeded_error(exc) from None

        fetch_started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as cli:
                r = await cli.post(
                    validated_endpoint,
                    headers={
                        "X-API-Key": key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "urls": [target_url],
                        "format": self._format(),
                        "ttl": 0,
                    },
                )
                if r.status_code >= 400:
                    status = int(r.status_code)
                    upstream = _safe_upstream_error(r)
                    if status in (401, 403):
                        code, category = "FETCH_AUTH_ERROR", "INVALID_CREDENTIAL"
                    elif status == 429:
                        code, category = "FETCH_RATE_LIMITED", "RATE_LIMIT"
                    elif status >= 500:
                        code, category = "FETCH_PROVIDER_UNAVAILABLE", "PROVIDER_UNAVAILABLE"
                    else:
                        code, category = "FETCH_PROVIDER_ERROR", "PROVIDER_ERROR"
                    message = upstream.get("upstream_message") or (
                        f"TinyFish fetch failed with status {status}"
                    )
                    raise FetchError(
                        message,
                        status_code=status,
                        error_category=category,
                        code=code,
                        provider="tinyfish",
                        upstream_status=status,
                        details=upstream,
                    )
                data = r.json()
        except httpx.TimeoutException:
            raise FetchError(
                "TinyFish fetch request timed out", 504, "FETCH_TIMEOUT",
                code="FETCH_TIMEOUT", provider="tinyfish",
                details={
                    "url": requested_url,
                    "timeout_seconds": self._timeout(),
                    "provider": "tinyfish",
                },
            )
        except FetchError:
            raise
        except Exception as exc:
            if _is_dns_network_message(str(exc)):
                raise FetchError(
                    f"TinyFish DNS resolution failed: {str(exc)[:150]}",
                    502, "DNS_RESOLUTION_FAILED",
                    code="DNS_RESOLUTION_FAILED", provider="tinyfish",
                )
            raise FetchError(
                "TinyFish request error", 502, "PROVIDER_ERROR",
                code="FETCH_PROVIDER_ERROR", provider="tinyfish",
            )

        results = data.get("results") or []
        errors = data.get("errors") or []
        if not results:
            if errors:
                raise FetchError(
                    "TinyFish fetch failed for the requested URL",
                    502,
                    "PROVIDER_ERROR",
                    code="FETCH_PROVIDER_ERROR",
                )
            raise FetchError(
                "TinyFish returned no result", 502, "MALFORMED_RESPONSE",
                code="MALFORMED_RESPONSE",
            )
        first = results[0] if isinstance(results[0], dict) else {}
        duration_ms = int((time.perf_counter() - fetch_started) * 1000)
        return self._normalize_fetch_result(
            requested_url, target_url, first, validated_endpoint, duration_ms
        )

    def _normalize_fetch_result(
        self,
        requested_url: str,
        target_url: str,
        first: Dict[str, Any],
        validated_endpoint: str,
        duration_ms: int,
    ) -> Dict[str, Any]:
        """Normalize one TinyFish ``results[]`` entry.

        Shared by :meth:`fetch` (single-dict, unchanged public shape) and
        :meth:`fetch_many` (batch record, adapted by the caller).
        """
        text = first.get("text")
        if isinstance(text, (dict, list)):
            import json

            text = json.dumps(text)
        text = str(text or "")
        return {
            "requested_url": requested_url,
            "final_url": str(first.get("final_url") or target_url),
            "status": 200,
            "content_type": str(first.get("format") or "text/markdown"),
            "title": first.get("title"),
            "text": text[:100000],
            "provider": self.id,
            "fetch_provider": self.id,
            "provider_type": "tinyfish",
            "egress_mode": REMOTE_EGRESS,
            "provider_status": 200,
            "duration_ms": duration_ms,
            "target_url": target_url,
            "metadata": {
                "source": "tinyfish",
                "egress_mode": REMOTE_EGRESS,
                "provider_endpoint_host": _host_only(validated_endpoint),
                "provider_status": 200,
                "duration_ms": duration_ms,
                "target_url": target_url,
            },
        }

    async def fetch_many(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Batch TinyFish fetch: ONE provider request containing all URLs.

        Order is preserved and every (clamped) input URL yields exactly one
        record. This raises ``FetchError`` when the provider endpoint fails
        SSRF validation, the HTTP transport times out/fails, or the local
        provider quota is exhausted (``PROVIDER_RATE_LIMITED``); per-target
        validation failures and per-URL upstream ``errors[]`` become records.
        """
        limit = self.batch_cap
        clamped = list(urls or [])[:limit]
        if not clamped:
            return []
        records: List[Optional[Dict[str, Any]]] = [None] * len(clamped)

        key = await self._resolve_api_key()
        if not key:
            exc = FetchError(
                "TinyFish API key is not configured. Live execution pending credentials.",
                status_code=400,
                error_category="NOT_CONFIGURED",
                code="NOT_CONFIGURED",
            )
            return [_per_url_failure(u, exc) for u in clamped]

        # Provider endpoint: adapter-owned, FULL local validation (whole batch).
        provider_endpoint = self._get_base_url()
        try:
            validated_endpoint = await async_validate_provider_url(provider_endpoint)
        except UrlValidationError as exc:
            status, category, code = _map_url_validation_error(exc)
            raise FetchError(
                f"TinyFish provider endpoint validation failed: {_url_validation_message(exc)}",
                status_code=status,
                error_category=category,
                code=code,
                provider="tinyfish",
            )

        # Targets: REMOTE egress, syntax/policy only. Invalid targets are
        # reported per-URL and excluded from the request (never abort the batch).
        valid: List[tuple] = []
        for index, url in enumerate(clamped):
            try:
                target_url = await async_validate_remote_target_url(str(url).strip())
            except UrlValidationError as exc:
                status, category, code = _map_url_validation_error(exc)
                records[index] = {
                    "url": url,
                    "status": "failed",
                    "error": {
                        "code": code,
                        "message": _url_validation_message(exc),
                    },
                }
                continue
            valid.append((index, url, target_url))

        if not valid:
            return [r for r in records if r is not None]

        try:
            consume_fetch(_fetch_quota_limits(self.doc), len(valid))
        except ProviderRateLimited as exc:
            raise _quota_exceeded_error(exc) from None

        fetch_started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as cli:
                r = await cli.post(
                    validated_endpoint,
                    headers={
                        "X-API-Key": key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "urls": [entry[2] for entry in valid],
                        "format": self._format(),
                        "ttl": 0,
                    },
                )
        except httpx.TimeoutException:
            raise FetchError(
                "TinyFish fetch request timed out", 504, "FETCH_TIMEOUT",
                code="FETCH_TIMEOUT", provider="tinyfish",
                details={
                    "timeout_seconds": self._timeout(),
                    "provider": "tinyfish",
                },
            )
        except Exception as exc:
            if _is_dns_network_message(str(exc)):
                raise FetchError(
                    f"TinyFish DNS resolution failed: {str(exc)[:150]}",
                    502, "DNS_RESOLUTION_FAILED",
                    code="DNS_RESOLUTION_FAILED", provider="tinyfish",
                )
            raise FetchError(
                "TinyFish request error", 502, "PROVIDER_ERROR",
                code="FETCH_PROVIDER_ERROR", provider="tinyfish",
            )

        if int(r.status_code) >= 400:
            status = int(r.status_code)
            upstream = _safe_upstream_error(r)
            if status in (401, 403):
                code, category = "FETCH_AUTH_ERROR", "INVALID_CREDENTIAL"
            elif status == 429:
                code, category = "FETCH_RATE_LIMITED", "RATE_LIMIT"
            elif status >= 500:
                code, category = "FETCH_PROVIDER_UNAVAILABLE", "PROVIDER_UNAVAILABLE"
            else:
                code, category = "FETCH_PROVIDER_ERROR", "PROVIDER_ERROR"
            message = upstream.get("upstream_message") or (
                f"TinyFish fetch failed with status {status}"
            )
            batch_error = FetchError(
                message,
                status_code=status,
                error_category=category,
                code=code,
                provider="tinyfish",
                upstream_status=status,
                details=upstream,
            )
            for index, url, _target in valid:
                records[index] = _per_url_failure(url, batch_error)
            return [r for r in records if r is not None]

        try:
            data = r.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        results = data.get("results") or []
        errors = data.get("errors") or []
        duration_ms = int((time.perf_counter() - fetch_started) * 1000)

        used: set = set()

        def _claim(candidate: Any) -> Optional[tuple]:
            if candidate is not None:
                cand = str(candidate)
                for entry in valid:
                    if entry[0] in used:
                        continue
                    if cand == entry[1] or cand == entry[2]:
                        return entry
            for entry in valid:
                if entry[0] not in used:
                    return entry
            return None

        for result in results:
            if not isinstance(result, dict):
                continue
            candidate = (
                result.get("url")
                or result.get("requested_url")
                or result.get("target_url")
            )
            entry = _claim(candidate)
            if entry is None:
                continue
            index, url, target_url = entry
            used.add(index)
            record = self._normalize_fetch_result(
                url, target_url, result, validated_endpoint, duration_ms
            )
            record["url"] = url
            record["status"] = "success"
            records[index] = record

        for error in errors:
            if not isinstance(error, dict):
                continue
            candidate = (
                error.get("url")
                or error.get("requested_url")
                or error.get("target_url")
            )
            entry = _claim(candidate)
            if entry is None:
                continue
            index, url, _target = entry
            used.add(index)
            body = error.get("error") if isinstance(error.get("error"), dict) else error
            code = body.get("code") or "FETCH_PROVIDER_ERROR"
            message = body.get("message") or "TinyFish failed to fetch URL"
            records[index] = {
                "url": url,
                "status": "failed",
                "error": {"code": str(code), "message": str(message)[:300]},
            }

        for index, url, _target in valid:
            if records[index] is None:
                records[index] = {
                    "url": url,
                    "status": "failed",
                    "error": {
                        "code": "MALFORMED_RESPONSE",
                        "message": "TinyFish returned no result for URL",
                    },
                }
        return [r for r in records if r is not None]


# Adapter factory mapping
_FETCH_ADAPTERS = {
    "standard": StandardFetchProvider,
    "tinyfish": TinyFishFetchProvider,
}


def register_fetch_adapter(provider_type: str, adapter_cls: type[BaseFetchProvider]) -> None:
    _FETCH_ADAPTERS[provider_type.lower()] = adapter_cls


def _fetch_key_source(doc: Dict[str, Any]) -> str:
    """One of vault|direct|env|none. Standard provider reports direct (N/A)."""
    if doc.get("provider_type") == "standard":
        return "direct"
    if doc.get("credential_id"):
        return "vault"
    if doc.get("api_key_enc"):
        return "direct"
    if os.environ.get("TINYFISH_API_KEY"):
        return "env"
    return "none"


def _seed_fetch_provider_docs() -> List[Dict[str, Any]]:
    now = now_iso()
    return [
        {
            "id": STANDARD_FETCH_PROVIDER_ID,
            "name": "Standard HTTP Fetch",
            "provider_type": "standard",
            "enabled": True,
            "capabilities": {"fetch": "SUPPORTED"},
            "configuration": {},
            "api_key_enc": None,
            "credential_id": None,
            "credential_owner_id": None,
            "is_default": True,
            "health_status": "CONNECTED",
            "last_checked_at": None,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": TINYFISH_FETCH_PROVIDER_ID,
            "name": "TinyFish Fetch",
            "provider_type": "tinyfish",
            "enabled": True,
            "capabilities": {"fetch": "SUPPORTED"},
            # Endpoint is adapter-owned (TINYFISH_FETCH_ENDPOINT); the provider
            # doc deliberately carries no base_url.
            "configuration": {},
            "api_key_enc": None,
            "credential_id": None,
            "credential_owner_id": None,
            "is_default": False,
            "health_status": None,
            "last_checked_at": None,
            "created_at": now,
            "updated_at": now,
        },
    ]


def public_fetch_provider(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitized, client-safe fetch provider metadata."""
    ptype = doc.get("provider_type", "standard")
    has_key = bool(
        doc.get("credential_id")
        or doc.get("api_key_enc")
        or (ptype == "tinyfish" and os.environ.get("TINYFISH_API_KEY"))
    )
    if ptype == "standard":
        credential_status = "NOT_REQUIRED"
        health_status = "CONNECTED"
    else:
        credential_status = "CONFIGURED" if has_key else "NOT_CONFIGURED"
        health_status = "CONFIGURED" if has_key else "NOT_CONFIGURED"
    adapter_cls = _FETCH_ADAPTERS.get(str(ptype).lower())
    supports_batch_fetch = bool(getattr(adapter_cls, "SUPPORTS_BATCH_FETCH", False))
    cfg = dict(doc.get("configuration") or {})
    for secret_key in ("api_key", "secret", "token", "password"):
        cfg.pop(secret_key, None)
    schema = valve_lib.fetch_provider_schema(doc.get("provider_type") or "")
    secret_keys = {"api_key"} if has_key else set()
    valve_values = valve_lib.projection(schema, cfg, secret_keys=secret_keys)
    configured = valve_lib.has_all_required(schema, cfg, secret_keys)
    status = {
        "enabled": bool(doc.get("enabled", True)),
        "configured": bool(configured),
        "available": bool(doc.get("enabled", True) and configured),
        "healthy": bool((doc.get("health_status") or "").upper() in {"CONNECTED", "CONFIGURED", "OK", "HEALTHY", "AVAILABLE"}),
        "health_status": doc.get("health_status"),
        "reason": None,
    }
    if not status["enabled"]:
        status["reason"] = "Provider is disabled"
    elif not configured:
        missing = valve_lib.required_secret_keys(schema)
        label = schema[missing[0]].label if missing and missing[0] in schema else "Configuration"
        status["reason"] = f"{label} required"
    capabilities = dict(doc.get("capabilities") or {"fetch": "SUPPORTED"})
    capabilities["supports_batch_fetch"] = supports_batch_fetch
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "provider_type": doc.get("provider_type"),
        "enabled": bool(doc.get("enabled", True)),
        "supports_batch_fetch": supports_batch_fetch,
        "capabilities": capabilities,
        "credential_status": credential_status,
        "credential_id": doc.get("credential_id"),
        "has_vault_credential": bool(doc.get("credential_id")),
        "key_source": _fetch_key_source(doc),
        "configuration": cfg,
        "health_status": doc.get("health_status") or health_status,
        "last_checked_at": doc.get("last_checked_at"),
        "is_default": bool(doc.get("is_default", ptype == "standard")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "valve_schema": valve_lib.public_schema(schema),
        "valve_values": valve_values,
        "status": status,
    }


class FetchProviderRegistry:
    """Registry managing fetch provider definitions and live adapters."""

    @staticmethod
    async def ensure_defaults() -> None:
        try:
            for seed in _seed_fetch_provider_docs():
                existing = await repo.fetch_providers.find_one({"id": seed["id"]})
                if existing is None:
                    await repo.fetch_providers.insert(seed)
                    existing = await repo.fetch_providers.find_one({"id": seed["id"]})
                # The TinyFish fetch endpoint is adapter-owned and canonical
                # (TINYFISH_FETCH_ENDPOINT); any historical base_url in the
                # provider doc is intentionally ignored, so no migration of it
                # is required.
        except Exception:
            pass

    @classmethod
    async def list_providers(cls, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        await cls.ensure_defaults()
        docs = await repo.fetch_providers.list({}, limit=100)
        return [public_fetch_provider(d) for d in docs]

    @classmethod
    async def get_raw_provider(cls, provider_id: str) -> Optional[Dict[str, Any]]:
        await cls.ensure_defaults()
        return await repo.fetch_providers.find_one({"id": provider_id})

    @classmethod
    async def get_provider(cls, provider_id: str) -> Optional[Dict[str, Any]]:
        doc = await cls.get_raw_provider(provider_id)
        if doc is None:
            return None
        return public_fetch_provider(doc)

    @classmethod
    async def record_test_result(cls, provider_id: str, status: str) -> None:
        """Best-effort persistence of the REAL adapter-reported health status."""
        if provider_id == STANDARD_FETCH_PROVIDER_ID:
            status = "CONNECTED"
        try:
            await repo.fetch_providers.update_global(provider_id, {
                "health_status": str(status),
                "last_checked_at": now_iso(),
                "updated_at": now_iso(),
            })
        except Exception:
            pass

    @classmethod
    async def update_provider(
        cls, provider_id: str, patch: Dict[str, Any], owner_id: str
    ) -> Dict[str, Any]:
        await cls.ensure_defaults()
        doc = await repo.fetch_providers.find_one({"id": provider_id})
        if doc is None:
            raise FetchError(f"Fetch provider '{provider_id}' not found", 404)
        updates: Dict[str, Any] = {"updated_at": now_iso()}
        if "name" in patch and patch["name"]:
            updates["name"] = str(patch["name"]).strip()
        if "enabled" in patch and patch["enabled"] is not None:
            updates["enabled"] = bool(patch["enabled"])
        if "configuration" in patch and isinstance(patch["configuration"], dict):
            existing_cfg = dict(doc.get("configuration") or {})
            existing_cfg.update(patch["configuration"])
            updates["configuration"] = existing_cfg
        if "api_key" in patch and patch["api_key"] is not None:
            raw_key = str(patch["api_key"]).strip()
            updates["api_key_enc"] = encrypt_secret(raw_key) if raw_key else None
        # Vault reference: owner-scoped verify, store id + owner id. project_id
        # is a scope hint only (validated, never stored on the provider doc).
        if "credential_id" in patch:
            raw_cid = patch["credential_id"]
            if raw_cid is None or (isinstance(raw_cid, str) and not raw_cid.strip()):
                updates["credential_id"] = None
                updates["credential_owner_id"] = None
            else:
                cid = str(raw_cid).strip()
                vault_doc = await repo.tool_credentials.get(cid, owner_id)
                if vault_doc is None:
                    raise FetchError("Credential not found", 404)
                hint = patch.get("project_id")
                if hint is not None and vault_doc.get("project_id") != hint:
                    raise FetchError(
                        "Credential does not belong to this project", 403
                    )
                updates["credential_id"] = cid
                updates["credential_owner_id"] = owner_id
        # Ciphertext reuse from the TinyFish search provider (no plaintext).
        if patch.get("reuse_search_credential") is True:
            source = await repo.search_providers.find_one({"id": _TINYFISH_SEARCH_PROVIDER_ID})
            if not source or not source.get("api_key_enc"):
                raise FetchError("Source provider has no API key to reuse", 400)
            updates["api_key_enc"] = source["api_key_enc"]
        # Default switching within the fetch registry (single default).
        if patch.get("is_default") is True:
            peer_updates = {"is_default": False, "updated_at": now_iso()}
            try:
                peers = await repo.fetch_providers.list({}, limit=100)
                for peer in peers:
                    if peer.get("id") != provider_id and peer.get("is_default"):
                        await repo.fetch_providers.update_global(
                            str(peer.get("id")), dict(peer_updates)
                        )
            except FetchError:
                raise
            except Exception:
                pass
            updates["is_default"] = True
        try:
            updated = await repo.fetch_providers.update_global(provider_id, updates)
        except Exception:
            updated = None
        return public_fetch_provider(updated or {**doc, **updates})

    @classmethod
    def adapter_from_doc(cls, doc: Dict[str, Any]) -> BaseFetchProvider:
        """Construct an adapter from an already-resolved provider doc (no DB)."""
        if not isinstance(doc, dict) or not doc:
            raise FetchError("Fetch provider not found", 404)
        ptype = str(doc.get("provider_type") or "standard").lower()
        adapter_cls = _FETCH_ADAPTERS.get(ptype)
        if adapter_cls is None:
            raise FetchError(
                f"No adapter registered for fetch provider type '{ptype}'", 400
            )
        return adapter_cls(doc)

    @classmethod
    async def get_adapter(cls, provider_id: Optional[str] = None) -> BaseFetchProvider:
        pid = provider_id
        if not pid:
            try:
                default_doc = await repo.fetch_providers.find_one({"is_default": True})
            except Exception:
                default_doc = None
            pid = (default_doc or {}).get("id") or STANDARD_FETCH_PROVIDER_ID
        doc = await cls.get_raw_provider(pid)
        if doc is None:
            # Fallback to seed docs
            for s in _seed_fetch_provider_docs():
                if s["id"] == pid:
                    doc = s
                    break
            if doc is None:
                raise FetchError(f"Fetch provider '{pid}' not found", 404)
        ptype = doc.get("provider_type", "standard").lower()
        adapter_cls = _FETCH_ADAPTERS.get(ptype)
        if adapter_cls is None:
            raise FetchError(f"No adapter registered for fetch provider type '{ptype}'", 400)
        return adapter_cls(doc)
