"""Search provider registry and canonical search executor (Phase 4.2.3.1).

Provides an extensible provider-based architecture for search tools.
TinyFish is the first provider implementation.
Credentials are encrypted server-side and never returned to the browser or logged.

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

import os
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from .. import repository as repo
from ..models import now_iso
from ..security import decrypt_secret, encrypt_secret
from ..urlguard import (
    REMOTE_EGRESS,
    UrlValidationError,
    async_validate_provider_url,
)
from . import valves as valve_lib
from .provider_quota import (
    PROVIDER_TINYFISH,
    RESOURCE_SEARCH,
    ProviderRateLimited,
    consume_search,
    default_limits,
)

TINYFISH_SEARCH_PROVIDER_ID = "prov-search-tinyfish"
#: Canonical TinyFish Search endpoint (adapter-owned; the provider doc's
#: ``configuration.base_url`` is ignored). An env override
#: (``TINYFISH_SEARCH_BASE_URL`` / ``TINYFISH_BASE_URL``) exists for tests/ops
#: and is fully SSRF-validated before use.
TINYFISH_SEARCH_ENDPOINT = "https://api.search.tinyfish.ai"
#: Defaults for the optional location/language search valves. Project-
#: configurable; these are the fallbacks when no valve/argument is supplied.
DEFAULT_SEARCH_LOCATION = "ID"
DEFAULT_SEARCH_LANGUAGE = "id"
# Ciphertext-copy source for ``reuse_fetch_credential``. Hardcoded id (not an
# import) to avoid a search<->fetch service import cycle.
_TINYFISH_FETCH_PROVIDER_ID = "prov-fetch-tinyfish"



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


def _search_quota_limits(doc: Dict[str, Any]) -> Dict[str, int]:
    """Effective TinyFish Search quota windows from valve config.

    Provider/account limits, not run budgets. Missing/invalid values fall back
    to the config defaults. Values <= 0 are treated as the default (a valve of
    0 is not a valid "off switch" here).
    """
    cfg = (doc or {}).get("configuration") or {}
    defaults = default_limits(PROVIDER_TINYFISH, RESOURCE_SEARCH)
    def _int(key: str, fallback: int) -> int:
        try:
            value = int(cfg.get(key))
        except (TypeError, ValueError):
            return int(fallback)
        return value if value > 0 else int(fallback)
    return {
        "minute": _int("search_rpm_limit", defaults["minute"]),
        "hour": _int("search_hourly_limit", defaults["hour"]),
    }


class SearchError(ValueError):
    """Raised for search provider contract violations or failures.

    Positional parameters keep the historical ``(message, status_code,
    error_category)`` signature. The keyword-only attributes carry the
    structured, secret-free provider detail threaded up to ``ToolResult``:
    ``code`` (stable machine code), ``provider``, ``upstream_status`` and a
    sanitized ``details`` mapping. Secrets are never stored here.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_category: str = "PROVIDER_ERROR",
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
        self.code = code or "SEARCH_PROVIDER_ERROR"
        self.provider = provider
        self.upstream_status = upstream_status
        self.details = details or {}


def _host_only(url: str) -> Optional[str]:
    """Return a bare, credential-free hostname for safe trace metadata."""
    try:
        host = urlsplit(str(url or "")).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.lower().rstrip(".")


class BaseSearchProvider(ABC):
    """Abstract search provider interface."""

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
        return self.doc.get("capabilities") or {"search": "SUPPORTED"}

    @property
    def egress_mode(self) -> str:
        """Search providers are REMOTE: the provider performs the egress."""
        return REMOTE_EGRESS

    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """Test provider connectivity without leaking credentials."""
        raise NotImplementedError

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        max_results: Optional[int] = None,
        location: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute canonical search."""
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


def _safe_upstream_error(response: Any) -> Dict[str, Any]:
    """Extract ``{"error": {"code", "message"}}`` from an upstream response.

    Best-effort and strictly bounded: never returns headers, the API key, or
    the raw body. At most the upstream ``code`` and a 200-character
    ``message`` are captured, so the structured error can be surfaced safely.
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


class TinyFishSearchProvider(BaseSearchProvider):
    """TinyFish Search Provider.

    Adheres strictly to the invariant: NEVER fake search results.
    If credentials or remote endpoints are unconfigured, marks execution as
    unverified / not configured.
    """

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

        The resolved key is stripped and an empty/whitespace-only value is
        treated as "not configured" (``None``); secrets are never logged.
        """
        vault_key = await _resolve_vault_key(self.doc)
        if vault_key:
            stripped = vault_key.strip()
            if stripped:
                return stripped
        return self._get_api_key()

    def _get_base_url(self) -> str:
        """Return the adapter-owned canonical TinyFish search endpoint.

        The provider doc's ``configuration.base_url`` is intentionally IGNORED
        (it is user-influenceable through the update API). Only a narrow env
        override for tests/ops may change it, and that value is fully validated
        before use.
        """
        override = (
            os.environ.get("TINYFISH_SEARCH_BASE_URL")
            or os.environ.get("TINYFISH_BASE_URL")
        )
        return (override or TINYFISH_SEARCH_ENDPOINT).rstrip("/")

    def _config_str(self, key: str, default: str) -> str:
        cfg = self.doc.get("configuration") or {}
        value = cfg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    def _resolve_option(self, override: Optional[str], key: str, default: str) -> str:
        """Resolve an optional search option: arg override > valve config > default."""
        if isinstance(override, str) and override.strip():
            return override.strip()
        return self._config_str(key, default)

    def _timeout(self) -> float:
        cfg = self.doc.get("configuration") or {}
        try:
            timeout = float(cfg.get("timeout_seconds") or 15)
        except (TypeError, ValueError):
            timeout = 15.0
        ceiling = _request_timeout_ceiling("SEARCH_REQUEST_TIMEOUT_MAX_SECONDS", 60.0)
        return max(1.0, min(timeout, ceiling))

    def _max_results(self) -> int:
        cfg = self.doc.get("configuration") or {}
        try:
            value = int(cfg.get("max_results") or 5)
        except (TypeError, ValueError):
            value = 5
        return max(1, min(value, 20))

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
            consume_search(_search_quota_limits(self.doc), 1)
        except ProviderRateLimited as exc:
            return {
                "ok": False,
                "status": "PROVIDER_RATE_LIMITED",
                "message": "TinyFish search provider quota exhausted",
                "details": dict(exc.error),
            }
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as cli:
                r = await cli.get(
                    validated,
                    params={"query": "tinyfish"},
                    headers={"X-API-Key": key, "Accept": "application/json"},
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
                        "message": f"TinyFish search API returned HTTP {r.status_code}",
                    }
                return {
                    "ok": True,
                    "status": "CONNECTED",
                    "message": "TinyFish search API reachable",
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

    async def search(
        self,
        query: str,
        *,
        max_results: Optional[int] = None,
        location: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            return {
                "query": query,
                "provider": self.id,
                "provider_type": "tinyfish",
                "egress_mode": REMOTE_EGRESS,
                "location": self._resolve_option(
                    location, "location", DEFAULT_SEARCH_LOCATION
                ),
                "language": self._resolve_option(
                    language, "language", DEFAULT_SEARCH_LANGUAGE
                ),
                "results": [],
                "count": 0,
            }
        query = query.strip()
        # Resolve the optional location/language AFTER valve defaults +
        # tool-call overrides so the exact values are preserved in the result.
        resolved_location = self._resolve_option(
            location, "location", DEFAULT_SEARCH_LOCATION
        )
        resolved_language = self._resolve_option(
            language, "language", DEFAULT_SEARCH_LANGUAGE
        )
        key = await self._resolve_api_key()
        if not key:
            raise SearchError(
                "TinyFish API key is not configured. Live execution pending credentials.",
                status_code=400,
                error_category="NOT_CONFIGURED",
                code="SEARCH_NOT_CONFIGURED",
                provider="tinyfish",
            )
        base = self._get_base_url()
        try:
            validated = await async_validate_provider_url(base)
        except UrlValidationError as exc:
            raise SearchError(
                f"TinyFish URL SSRF validation failed: {exc.message}",
                status_code=400,
                error_category="SSRF_BLOCKED",
                code="SEARCH_SSRF_BLOCKED",
                provider="tinyfish",
            )
        try:
            consume_search(_search_quota_limits(self.doc), 1)
        except ProviderRateLimited as exc:
            raise SearchError(
                exc.error.get("message") or "TinyFish search provider quota exceeded",
                status_code=429,
                error_category="RATE_LIMIT",
                code="PROVIDER_RATE_LIMITED",
                provider="tinyfish",
                upstream_status=429,
                details=dict(exc.error),
            ) from None

        limit = max(1, min(int(max_results) if max_results else self._max_results(), 20))
        search_started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as cli:
                r = await cli.get(
                    validated,
                    params={
                        "query": query,
                        "location": resolved_location,
                        "language": resolved_language,
                    },
                    headers={"X-API-Key": key, "Accept": "application/json"},
                )
                if r.status_code >= 400:
                    status = int(r.status_code)
                    upstream = _safe_upstream_error(r)
                    if status in (401, 403):
                        code, category = "SEARCH_AUTH_ERROR", "INVALID_CREDENTIAL"
                    elif status == 402:
                        code, category = "SEARCH_ENTITLEMENT_REQUIRED", "PROVIDER_ERROR"
                    elif status == 429:
                        code, category = "SEARCH_RATE_LIMITED", "RATE_LIMIT"
                    elif status >= 500:
                        code, category = "SEARCH_PROVIDER_UNAVAILABLE", "PROVIDER_UNAVAILABLE"
                    else:
                        code, category = "SEARCH_PROVIDER_ERROR", "PROVIDER_ERROR"
                    message = upstream.get("upstream_message") or (
                        f"TinyFish search failed with status {status}"
                    )
                    raise SearchError(
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
            raise SearchError(
                "TinyFish search request timed out",
                status_code=504,
                error_category="TIMEOUT",
                code="SEARCH_TIMEOUT",
                provider="tinyfish",
                details={
                    "query": query,
                    "timeout_seconds": self._timeout(),
                    "provider": "tinyfish",
                },
            )
        except SearchError:
            raise
        except Exception:
            raise SearchError(
                "TinyFish request error",
                status_code=502,
                error_category="PROVIDER_ERROR",
                code="SEARCH_PROVIDER_ERROR",
                provider="tinyfish",
            )

        # Normalize results strictly preserving title, url, snippet/summary, rank
        raw_items = data.get("results") or data.get("items") or []
        normalized_results = []
        for idx, item in enumerate(raw_items[:limit]):
            if not isinstance(item, dict):
                continue
            normalized_results.append({
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or item.get("link") or ""),
                "snippet": str(item.get("snippet") or item.get("summary") or item.get("content") or ""),
                "rank": int(item.get("rank") or item.get("position") or (idx + 1)),
                "provider_metadata": {
                    "source": "tinyfish",
                    "site_name": item.get("site_name"),
                    "score": item.get("score"),
                },
            })

        duration_ms = int((time.perf_counter() - search_started) * 1000)
        return {
            "query": query,
            "provider": self.id,
            "search_provider": self.id,
            "provider_type": "tinyfish",
            "egress_mode": REMOTE_EGRESS,
            "location": resolved_location,
            "language": resolved_language,
            "provider_status": 200,
            "duration_ms": duration_ms,
            "results": normalized_results,
            "count": len(normalized_results),
            "metadata": {
                "source": "tinyfish",
                "egress_mode": REMOTE_EGRESS,
                "provider_endpoint_host": _host_only(validated),
                "provider_status": 200,
                "duration_ms": duration_ms,
                "location": resolved_location,
                "language": resolved_language,
            },
        }


# Adapter factory mapping
_SEARCH_ADAPTERS = {
    "tinyfish": TinyFishSearchProvider,
}


def register_search_adapter(provider_type: str, adapter_cls: type[BaseSearchProvider]) -> None:
    _SEARCH_ADAPTERS[provider_type.lower()] = adapter_cls


def _search_key_source(doc: Dict[str, Any]) -> str:
    """One of vault|direct|env|none. Vault wins over direct over env."""
    if doc.get("credential_id"):
        return "vault"
    if doc.get("api_key_enc"):
        return "direct"
    if os.environ.get("TINYFISH_API_KEY"):
        return "env"
    return "none"


def _seed_tinyfish_search_doc() -> Dict[str, Any]:
    now = now_iso()
    return {
        "id": TINYFISH_SEARCH_PROVIDER_ID,
        "name": "TinyFish Search",
        "provider_type": "tinyfish",
        "enabled": True,
        "capabilities": {"search": "SUPPORTED"},
        # Endpoint is adapter-owned (TINYFISH_SEARCH_ENDPOINT).
        "configuration": {},
        "api_key_enc": None,
        "credential_id": None,
        "credential_owner_id": None,
        "is_default": True,
        "health_status": None,
        "last_checked_at": None,
        "created_at": now,
        "updated_at": now,
    }


def public_search_provider(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitized, client-safe search provider metadata (secrets never exposed)."""
    has_key = bool(
        doc.get("credential_id")
        or doc.get("api_key_enc")
        or os.environ.get("TINYFISH_API_KEY")
    )
    credential_status = "CONFIGURED" if has_key else "NOT_CONFIGURED"
    cfg = dict(doc.get("configuration") or {})
    # Strip any potential secrets from configuration
    for secret_key in ("api_key", "secret", "token", "password"):
        cfg.pop(secret_key, None)
    schema = valve_lib.search_provider_schema(doc.get("provider_type") or "")
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
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "provider_type": doc.get("provider_type"),
        "enabled": bool(doc.get("enabled", True)),
        "capabilities": doc.get("capabilities") or {"search": "SUPPORTED"},
        "credential_status": credential_status,
        "credential_id": doc.get("credential_id"),
        "has_vault_credential": bool(doc.get("credential_id")),
        "key_source": _search_key_source(doc),
        "configuration": cfg,
        "health_status": doc.get("health_status") or (
            "CONFIGURED" if has_key else "NOT_CONFIGURED"
        ),
        "last_checked_at": doc.get("last_checked_at"),
        "is_default": bool(doc.get("is_default", True)),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "valve_schema": valve_lib.public_schema(schema),
        "valve_values": valve_values,
        "status": status,
    }


class SearchProviderRegistry:
    """Registry managing search provider definitions and live adapters."""

    @staticmethod
    async def ensure_defaults() -> None:
        try:
            existing = await repo.search_providers.find_one({"id": TINYFISH_SEARCH_PROVIDER_ID})
            if existing is None:
                await repo.search_providers.insert(_seed_tinyfish_search_doc())
                existing = await repo.search_providers.find_one({"id": TINYFISH_SEARCH_PROVIDER_ID})
            # The TinyFish search endpoint is adapter-owned and canonical
            # (TINYFISH_SEARCH_ENDPOINT); any historical base_url in the
            # provider doc is intentionally ignored.
        except Exception:
            pass

    @classmethod
    async def list_providers(cls, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        await cls.ensure_defaults()
        docs = await repo.search_providers.list({}, limit=100)
        return [public_search_provider(d) for d in docs]

    @classmethod
    async def get_raw_provider(cls, provider_id: str) -> Optional[Dict[str, Any]]:
        await cls.ensure_defaults()
        return await repo.search_providers.find_one({"id": provider_id})

    @classmethod
    async def get_provider(cls, provider_id: str) -> Optional[Dict[str, Any]]:
        doc = await cls.get_raw_provider(provider_id)
        if doc is None:
            return None
        return public_search_provider(doc)

    @classmethod
    async def record_test_result(cls, provider_id: str, status: str) -> None:
        """Best-effort persistence of the REAL adapter-reported health status."""
        try:
            await repo.search_providers.update_global(provider_id, {
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
        doc = await repo.search_providers.find_one({"id": provider_id})
        if doc is None:
            raise SearchError(f"Search provider '{provider_id}' not found", 404)
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
                    raise SearchError("Credential not found", 404)
                hint = patch.get("project_id")
                if hint is not None and vault_doc.get("project_id") != hint:
                    raise SearchError(
                        "Credential does not belong to this project", 403
                    )
                updates["credential_id"] = cid
                updates["credential_owner_id"] = owner_id
        # Ciphertext reuse from the TinyFish fetch provider (no plaintext).
        if patch.get("reuse_fetch_credential") is True:
            source = await repo.fetch_providers.find_one({"id": _TINYFISH_FETCH_PROVIDER_ID})
            if not source or not source.get("api_key_enc"):
                raise SearchError("Source provider has no API key to reuse", 400)
            updates["api_key_enc"] = source["api_key_enc"]
        # Single-provider registry: is_default=True is a no-op accept.
        if patch.get("is_default") is True:
            updates["is_default"] = True
        try:
            updated = await repo.search_providers.update_global(provider_id, updates)
        except Exception:
            updated = None
        return public_search_provider(updated or {**doc, **updates})

    @classmethod
    def adapter_from_doc(cls, doc: Dict[str, Any]) -> BaseSearchProvider:
        """Construct an adapter from an already-resolved provider doc (no DB).

        Used by execution-time provider resolution so project-scoped valve and
        vault overrides are honored without re-reading the global provider.
        """
        if not isinstance(doc, dict) or not doc:
            raise SearchError("Search provider not found", 404)
        ptype = str(doc.get("provider_type") or "tinyfish").lower()
        adapter_cls = _SEARCH_ADAPTERS.get(ptype)
        if adapter_cls is None:
            raise SearchError(
                f"No adapter registered for search provider type '{ptype}'", 400
            )
        return adapter_cls(doc)

    @classmethod
    async def get_adapter(cls, provider_id: Optional[str] = None) -> BaseSearchProvider:
        pid = provider_id
        if not pid:
            try:
                default_doc = await repo.search_providers.find_one({"is_default": True})
            except Exception:
                default_doc = None
            pid = (default_doc or {}).get("id") or TINYFISH_SEARCH_PROVIDER_ID
        doc = await cls.get_raw_provider(pid)
        if doc is None:
            # Fallback to seed doc in memory
            if pid == TINYFISH_SEARCH_PROVIDER_ID:
                doc = _seed_tinyfish_search_doc()
            else:
                raise SearchError(f"Search provider '{pid}' not found", 404)
        ptype = doc.get("provider_type", "tinyfish").lower()
        adapter_cls = _SEARCH_ADAPTERS.get(ptype)
        if adapter_cls is None:
            raise SearchError(f"No adapter registered for search provider type '{ptype}'", 400)
        return adapter_cls(doc)
