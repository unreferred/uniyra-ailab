"""Tool/provider valve resolution, persistence and status (V4.2.3.2).

Bridges the pure Valve schema layer (``app.services.valves``) to storage:

* Non-secret valve values live in the ``valve_values`` collection, keyed by
  ``(owner_id, target_type, target_id, project_id)`` so a project override is
  just a second row (``project_id`` None = global default). Resolution order is
  global row -> project row (project wins).
* Secret valve values are routed to the existing encrypted credential vault
  (``tool_credentials``) by reference. Plaintext is never stored in
  ``valve_values`` and never returned to a client.
* Provider-backed builtin tools (``web_search`` / ``web_fetch``) expose their
  provider's valves; selecting a provider dynamically selects the schema.
* Status is four-dimensional (enabled / configured / available / healthy) with
  an explicit human-readable reason - never a single collapsed boolean.

Backward compatibility: existing provider docs that still carry ``api_key_enc``,
``credential_id`` or a ``configuration`` dict are projected through these valves
without any user re-entry.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .. import repository as repo
from ..models import new_id, now_iso
from ..security import encrypt_secret
from . import valves as valve_lib
from .fetch_providers import FetchProviderRegistry
from .search_providers import SearchProviderRegistry


class ValveError(ValueError):
    """Raised for valve contract violations. Carries an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


_HEALTHY = {"CONNECTED", "CONFIGURED", "OK", "HEALTHY", "AVAILABLE", "NOT_REQUIRED"}
_UNHEALTHY = {"UNAVAILABLE", "ERROR", "FAILED", "INVALID", "INVALID_CREDENTIAL", "AUTH_FAILED", "DISCONNECTED"}
_SECRET_CONFIG_KEYS = ("api_key", "secret", "token", "password")

#: Synthetic tool id under which the web_research breadth policy is stored.
RESEARCH_TOOL_ID = "tool-research-0001"
RESEARCH_CONFIG_SOURCE_GLOBAL = "GLOBAL"
RESEARCH_CONFIG_SOURCE_PROJECT = "PROJECT"
RESEARCH_CONFIG_SOURCE_REQUEST_DEFAULT = "REQUEST_DEFAULT"


# ------------------------------------------------------------------- vault write
def _secret_name(target_type: str, target_id: str, key: str) -> str:
    return f"valve:{target_type}:{target_id}:{key}"


async def _store_secret(
    owner_id: str,
    project_id: Optional[str],
    target_type: str,
    target_id: str,
    key: str,
    plaintext: str,
) -> str:
    """Encrypt and persist a valve secret, returning its vault credential id.

    Reuses an existing vault row for the same (owner, project, valve) so
    replacing a secret never leaves orphaned ciphertext.
    """
    name = _secret_name(target_type, target_id, key)
    now = now_iso()
    encrypted = encrypt_secret(plaintext)
    existing = await repo.tool_credentials.find_one(
        {"owner_id": owner_id, "project_id": project_id, "name": name}
    )
    if existing is not None:
        await repo.tool_credentials.update(
            existing["id"], owner_id, {"encrypted_value": encrypted, "updated_at": now}
        )
        return existing["id"]
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "name": name,
        "credential_type": "API_KEY",
        "encrypted_value": encrypted,
        "metadata": {
            "origin": "valve",
            "target_type": target_type,
            "target_id": target_id,
            "valve_key": key,
        },
        "created_at": now,
        "updated_at": now,
    }
    await repo.tool_credentials.insert(doc)
    return doc["id"]


# ------------------------------------------------------------------- row storage
async def _get_row(
    owner_id: str, target_type: str, target_id: str, project_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    return await repo.valve_values.find_one(
        {
            "owner_id": owner_id,
            "target_type": target_type,
            "target_id": target_id,
            "project_id": project_id,
        }
    )


async def _upsert_row(
    owner_id: str,
    target_type: str,
    target_id: str,
    project_id: Optional[str],
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    existing = await _get_row(owner_id, target_type, target_id, project_id)
    now = now_iso()
    if existing is not None:
        await repo.valve_values.update(existing["id"], owner_id, {**patch, "updated_at": now})
        return {**existing, **patch, "updated_at": now}
    doc: Dict[str, Any] = {
        "id": new_id(),
        "owner_id": owner_id,
        "target_type": target_type,
        "target_id": target_id,
        "project_id": project_id,
        "values": {},
        "secret_refs": {},
        "provider_id": None,
        "enabled": None,
        "created_at": now,
        "updated_at": now,
    }
    doc.update(patch)
    await repo.valve_values.insert(doc)
    return doc


def _merge_rows(rows: Iterable[Optional[Dict[str, Any]]]) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[str], Optional[bool]]:
    """Merge global-then-project rows (later wins). Returns values/refs/provider/enabled."""
    values: Dict[str, Any] = {}
    refs: Dict[str, Any] = {}
    provider_id: Optional[str] = None
    enabled: Optional[bool] = None
    for row in rows:
        if not row:
            continue
        values.update(row.get("values") or {})
        refs.update(row.get("secret_refs") or {})
        if row.get("provider_id"):
            provider_id = row["provider_id"]
        if row.get("enabled") is not None:
            enabled = bool(row["enabled"])
    return values, refs, provider_id, enabled


def _registry_for(kind: str):
    return SearchProviderRegistry if kind == "search" else FetchProviderRegistry


def _provider_schema(kind: Optional[str], provider_type: str) -> valve_lib.ValveSchema:
    if kind == "search":
        return valve_lib.search_provider_schema(provider_type)
    if kind == "fetch":
        return valve_lib.fetch_provider_schema(provider_type)
    return {}


def _clean_provider_config(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy provider ``configuration`` minus anything secret-shaped."""
    cfg = dict((doc or {}).get("configuration") or {})
    for key in _SECRET_CONFIG_KEYS:
        cfg.pop(key, None)
    return cfg


def _provider_has_legacy_secret(doc: Dict[str, Any]) -> bool:
    if not doc:
        return False
    if doc.get("credential_id") or doc.get("api_key_enc"):
        return True
    if doc.get("provider_type") == "tinyfish" and os.environ.get("TINYFISH_API_KEY"):
        return True
    return False


def valve_config_hash(
    values: Any, secret_keys: Iterable[str] = ()
) -> str:
    """Deterministic sha256 over the NON-SECRET effective valve values.

    Secret plaintext is never hashed: ``redact_for_hash`` drops any key listed
    in ``secret_keys`` before canonical JSON serialization.
    """
    redacted = valve_lib.redact_for_hash(
        values if isinstance(values, dict) else {}, secret_keys=set(secret_keys)
    )
    payload = json.dumps(redacted, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _credential_scope(
    project_row: Optional[Dict[str, Any]],
    global_row: Optional[Dict[str, Any]],
    effective: Dict[str, Any],
) -> Tuple[Optional[str], str]:
    """Return ``(credential_ref, scope)`` for an effective provider doc."""
    project_ref = ((project_row or {}).get("secret_refs") or {}).get("api_key")
    global_ref = ((global_row or {}).get("secret_refs") or {}).get("api_key")
    if project_ref:
        return str(project_ref), "PROJECT"
    if global_ref:
        return str(global_ref), "GLOBAL"
    legacy_ref = (effective or {}).get("credential_id")
    if legacy_ref:
        return str(legacy_ref), "GLOBAL"
    return None, "NONE"


async def resolve_provider_execution(
    owner_id: str,
    kind: str,
    provider_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return an effective provider doc (config + secret resolved) for execution.

    Precedence: provider base config/doc secret -> global valve row -> project
    valve row. Never mutates the shared provider doc. Never returns secret
    plaintext: the secret is always expressed as an opaque ``credential_id``
    vault reference. A project row that references a since-deleted credential
    fails closed (the adapter resolves no key) rather than falling back to the
    global secret.
    """
    registry = _registry_for(kind)
    if not provider_id:
        providers = await registry.list_providers(owner_id)
        default = next((p for p in providers if p.get("is_default")), None)
        provider_id = (default or {}).get("id") or (
            providers[0]["id"] if providers else None
        )
    if not provider_id:
        raise ValveError("Provider not found", 404)
    raw = await registry.get_raw_provider(provider_id)
    if raw is None:
        raise ValveError("Provider not found", 404)

    schema = _provider_schema(kind, raw.get("provider_type") or "")
    global_row = await _get_row(owner_id, "provider", provider_id, None)
    project_row = (
        await _get_row(owner_id, "provider", provider_id, project_id)
        if project_id
        else None
    )
    values, refs, _, _ = _merge_rows([global_row, project_row])
    effective_config = {**_clean_provider_config(raw), **values}

    effective = dict(raw)
    effective["configuration"] = effective_config
    effective["valve_schema"] = valve_lib.public_schema(schema)

    project_ref = ((project_row or {}).get("secret_refs") or {}).get("api_key")
    global_ref = ((global_row or {}).get("secret_refs") or {}).get("api_key")
    if project_ref:
        effective["credential_id"] = project_ref
        effective["credential_owner_id"] = owner_id
        effective["api_key_enc"] = None
    elif global_ref:
        effective["credential_id"] = global_ref
        effective["credential_owner_id"] = owner_id
        effective["api_key_enc"] = None
    # else: leave raw credential_id/credential_owner_id/api_key_enc as-is
    # (legacy/direct/env fallback).
    return effective


async def resolve_research_policy(
    owner_id: str, project_id: Optional[str] = None
) -> Dict[str, Any]:
    """Resolve the effective (global -> project) research breadth policy.

    Order: global tool-valve row -> project tool-valve row. Returns an
    immutable, snapshot-ready, non-secret dict. The hard server ceiling is
    10 for both breadth dimensions. ``source`` is PROJECT when a project row
    carries values, GLOBAL when a global row carries values, else
    REQUEST_DEFAULT (server defaults).
    """
    schema = valve_lib.research_policy_schema()
    global_row = await _get_row(owner_id, "tool", RESEARCH_TOOL_ID, None)
    project_row = (
        await _get_row(owner_id, "tool", RESEARCH_TOOL_ID, project_id)
        if project_id
        else None
    )
    values, _refs, _provider, _enabled = _merge_rows([global_row, project_row])
    projected = valve_lib.projection(schema, values)

    def _as_int(key: str, fallback: int) -> int:
        try:
            return int(projected.get(key))
        except (TypeError, ValueError):
            return fallback

    default_results = _as_int("research_default_max_results", 5)
    default_fetches = _as_int("research_default_max_fetches", 5)
    ceiling_results = _as_int("research_max_results_ceiling", 10)
    ceiling_fetches = _as_int("research_max_fetches_ceiling", 10)

    ceiling_results = max(1, min(ceiling_results, valve_lib.RESEARCH_HARD_MAX_RESULTS))
    ceiling_fetches = max(1, min(ceiling_fetches, valve_lib.RESEARCH_HARD_MAX_FETCHES))
    default_results = max(1, min(default_results, ceiling_results))
    default_fetches = max(1, min(default_fetches, ceiling_fetches))

    if (project_row or {}).get("values"):
        source = RESEARCH_CONFIG_SOURCE_PROJECT
    elif (global_row or {}).get("values"):
        source = RESEARCH_CONFIG_SOURCE_GLOBAL
    else:
        source = RESEARCH_CONFIG_SOURCE_REQUEST_DEFAULT

    return {
        "default_max_results": default_results,
        "default_max_fetches": default_fetches,
        "max_results_ceiling": ceiling_results,
        "max_fetches_ceiling": ceiling_fetches,
        "source": source,
        "config_hash": valve_config_hash(values),
    }


async def _provider_config_snapshot(
    owner_id: str, kind: str, provider_id: Optional[str], project_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Non-secret effective-config snapshot for one provider (or None)."""
    effective = await resolve_provider_execution(
        owner_id, kind, provider_id, project_id
    )
    effective_provider_id = effective.get("id")
    raw = await _registry_for(kind).get_raw_provider(effective_provider_id)
    schema = _provider_schema(
        kind, (raw or {}).get("provider_type") or effective.get("provider_type") or ""
    )
    global_row = await _get_row(owner_id, "provider", effective_provider_id, None)
    project_row = (
        await _get_row(owner_id, "provider", effective_provider_id, project_id)
        if project_id
        else None
    )
    values, refs, _, _ = _merge_rows([global_row, project_row])
    secret_keys = set(refs)
    if _provider_has_legacy_secret(raw or {}):
        secret_keys.add("api_key")
    merged = {**_clean_provider_config(raw or {}), **values}
    valves = valve_lib.projection(schema, merged, secret_keys=secret_keys)
    # Strip the {"stored": bool} secret markers: the snapshot is non-secret.
    valves = {
        key: value
        for key, value in valves.items()
        if not (isinstance(value, dict) and set(value.keys()) == {"stored"})
    }
    credential_ref, scope = _credential_scope(project_row, global_row, effective)
    return {
        "provider_id": effective_provider_id,
        "effective_valves": valves,
        "valve_config_hash": valve_config_hash(merged, secret_keys=secret_keys),
        "credential_ref": credential_ref,
        "credential_scope": scope,
    }


async def _code_interpreter_config_snapshot(
    owner_id: str, project_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Non-secret effective code-interpreter limits snapshot."""
    from . import code_interpreter as ci

    limits = await ci.resolve_execution_limits(owner_id, project_id)
    values = {
        "timeout_seconds": limits.get("timeout_s"),
        "memory_mb": limits.get("memory_mb"),
        "cpus": limits.get("cpus"),
        "max_output_bytes": limits.get("max_output_bytes"),
    }
    return {
        **values,
        "valve_config_hash": valve_config_hash(values),
    }


async def effective_tool_config_snapshot(
    owner_id: str,
    project_id: Optional[str],
    *,
    search_enabled: bool,
    search_provider_id: Optional[str],
    fetch_enabled: bool,
    fetch_provider_id: Optional[str],
) -> Dict[str, Any]:
    """Immutable, non-secret effective tool configuration for a run.

    Computed once and written onto the Run; consumers must never recompute it.
    Secret plaintext (or the secret marker) never appears in the result.
    """
    snapshot: Dict[str, Any] = {
        "search": None,
        "fetch": None,
        "code_interpreter": None,
        "research": None,
    }
    if search_enabled:
        try:
            snapshot["search"] = await _provider_config_snapshot(
                owner_id, "search", search_provider_id, project_id
            )
        except Exception:
            snapshot["search"] = None
    if fetch_enabled:
        try:
            snapshot["fetch"] = await _provider_config_snapshot(
                owner_id, "fetch", fetch_provider_id, project_id
            )
        except Exception:
            snapshot["fetch"] = None
    try:
        snapshot["code_interpreter"] = await _code_interpreter_config_snapshot(
            owner_id, project_id
        )
    except Exception:
        snapshot["code_interpreter"] = None
    try:
        snapshot["research"] = await resolve_research_policy(owner_id, project_id)
    except Exception:
        snapshot["research"] = None
    return snapshot


def _required_reason(schema: valve_lib.ValveSchema, secret_keys: Iterable[str]) -> Optional[str]:
    present = set(secret_keys)
    for key in valve_lib.required_secret_keys(schema):
        if key not in present:
            label = schema[key].label or key
            return f"{label} required"
    return None


# ------------------------------------------------------------------- resolution
async def resolve_tool(
    owner_id: str,
    tool_doc: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a tool's effective valve configuration and 4-state status.

    Never returns secret plaintext: SECRET valves project to ``{"stored": bool}``.
    """
    tool = tool_doc if isinstance(tool_doc, dict) else {}
    tool_id = str(tool.get("id") or "")
    kind = valve_lib.tool_provider_kind(tool)
    tool_schema = valve_lib.tool_valve_schema(tool)

    global_row = await _get_row(owner_id, "tool", tool_id, None)
    project_row = await _get_row(owner_id, "tool", tool_id, project_id) if project_id else None
    tool_values_raw, tool_refs, provider_id, enabled_override = _merge_rows([global_row, project_row])
    tool_secret_keys = set(tool_refs)

    doc_enabled = bool(tool.get("enabled", True))
    effective_enabled = enabled_override if enabled_override is not None else doc_enabled

    result: Dict[str, Any] = {
        "tool_id": tool_id,
        "name": tool.get("name"),
        "display_name": tool.get("display_name") or tool.get("name"),
        "tool_type": tool.get("tool_type") or "function",
        "schema": valve_lib.public_schema(tool_schema),
        "values": valve_lib.projection(tool_schema, tool_values_raw, secret_keys=tool_secret_keys),
        "provider_kind": kind,
        "provider_id": None,
        "providers": [],
        "provider_schema": {},
        "provider_values": {},
        "status": {
            "enabled": effective_enabled,
            "configured": True,
            "available": effective_enabled,
            "healthy": False,
            "health_status": None,
            "reason": None if effective_enabled else "Tool is disabled",
        },
    }

    if kind == "code_interpreter" or (tool.get("tool_type") == "code_interpreter"):
        from .tools import code_interpreter_availability

        available, reason = code_interpreter_availability()
        result["status"] = {
            "enabled": effective_enabled,
            "configured": True,
            "available": bool(effective_enabled and available),
            "healthy": bool(available),
            "health_status": "CONNECTED" if available else "UNAVAILABLE",
            "reason": None if (effective_enabled and available) else (reason or "Sandbox unavailable"),
        }
        return result

    if kind not in ("search", "fetch"):
        return result

    registry = _registry_for(kind)
    providers = await registry.list_providers(owner_id)
    if not provider_id:
        default = next((p for p in providers if p.get("is_default")), None)
        provider_id = (default or {}).get("id") or (providers[0]["id"] if providers else None)

    provider_doc = await registry.get_raw_provider(provider_id) if provider_id else None
    provider_type = (provider_doc or {}).get("provider_type") or ""
    provider_schema = _provider_schema(kind, provider_type)

    prov_global = await _get_row(owner_id, "provider", provider_id, None) if provider_id else None
    prov_project = (
        await _get_row(owner_id, "provider", provider_id, project_id)
        if (provider_id and project_id)
        else None
    )
    prov_values, prov_refs, _, _ = _merge_rows([prov_global, prov_project])
    merged_values = {**_clean_provider_config(provider_doc or {}), **prov_values}
    secret_keys = set(prov_refs)
    if _provider_has_legacy_secret(provider_doc or {}):
        secret_keys.add("api_key")

    configured = valve_lib.has_all_required(provider_schema, merged_values, secret_keys)
    health = str((provider_doc or {}).get("health_status") or "").upper()
    healthy = health in _HEALTHY
    provider_enabled = bool((provider_doc or {}).get("enabled", True))
    reason: Optional[str] = None
    if not effective_enabled:
        reason = "Tool is disabled"
    elif provider_doc is None:
        reason = "No provider selected"
    elif not provider_enabled:
        reason = "Provider is disabled"
    elif not configured:
        reason = _required_reason(provider_schema, secret_keys) or "Provider is not configured"
    elif health in _UNHEALTHY:
        reason = "Provider connection failed"

    # A provider-backed tool is only effectively enabled when its selected
    # provider is present and enabled; the four-state status must never report
    # ``enabled``/``available`` True for a tool whose provider is disabled.
    effective_enabled = bool(
        effective_enabled and provider_doc is not None and provider_enabled
    )
    available = bool(effective_enabled and configured and provider_doc is not None)
    enriched_providers = []
    for p in providers:
        pid = p.get("id")
        p_schema = _provider_schema(kind, p.get("provider_type") or "")
        p_global = await _get_row(owner_id, "provider", pid, None) if pid else None
        p_project = (
            await _get_row(owner_id, "provider", pid, project_id)
            if (pid and project_id)
            else None
        )
        p_override, p_override_refs, _, _ = _merge_rows([p_global, p_project])
        p_merged = {**_clean_provider_config(p), **p_override}
        p_refs = set(p_override_refs)
        if p.get("has_vault_credential") or p.get("key_source") in ("vault", "direct", "env"):
            p_refs.add("api_key")
        enriched_providers.append({
            "id": pid,
            "name": p.get("name"),
            "provider_type": p.get("provider_type"),
            "enabled": bool(p.get("enabled", True)),
            "health_status": p.get("health_status"),
            "valve_schema": valve_lib.public_schema(p_schema),
            "valve_values": valve_lib.projection(p_schema, p_merged, secret_keys=p_refs),
            "status": p.get("status") or {},
        })
    result["provider_id"] = provider_id
    result["providers"] = enriched_providers
    result["provider_schema"] = valve_lib.public_schema(provider_schema)
    result["provider_values"] = valve_lib.projection(
        provider_schema, merged_values, secret_keys=secret_keys
    )
    result["status"] = {
        "enabled": effective_enabled,
        "configured": configured,
        "available": available,
        "healthy": healthy,
        "health_status": health or None,
        "reason": reason,
    }
    return result


# ------------------------------------------------------------------- persistence
async def save_tool(
    owner_id: str,
    tool_doc: Dict[str, Any],
    body: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist valve values for a tool and (optionally) its selected provider."""
    tool = tool_doc if isinstance(tool_doc, dict) else {}
    tool_id = str(tool.get("id") or "")
    if not tool_id:
        raise ValveError("Tool id is required", 400)
    tool_schema = valve_lib.tool_valve_schema(tool)
    kind = valve_lib.tool_provider_kind(tool)

    row_patch: Dict[str, Any] = {}
    if "enabled" in body and body["enabled"] is not None:
        row_patch["enabled"] = bool(body["enabled"])

    if body.get("values") is not None:
        existing_refs = await _existing_secret_refs(owner_id, "tool", tool_id, project_id)
        clean, errors = valve_lib.validate_values(
            tool_schema, body["values"], existing_secret_keys=existing_refs
        )
        if errors:
            raise ValveError("; ".join(errors), 400)
        values_patch, ref_patch = await _split_secret_values(
            owner_id, project_id, "tool", tool_id, tool_schema, clean
        )
        row_patch["values"] = values_patch
        row_patch["secret_refs"] = ref_patch

    if "provider_id" in body and body["provider_id"]:
        provider_id = str(body["provider_id"])
        registry = _registry_for(kind) if kind else None
        if registry is None:
            raise ValveError("This tool has no provider registry", 400)
        raw = await registry.get_raw_provider(provider_id)
        if raw is None:
            raise ValveError("Provider not found", 404)
        row_patch["provider_id"] = provider_id

    if row_patch:
        await _upsert_row(owner_id, "tool", tool_id, project_id, row_patch)

    if kind in ("search", "fetch") and body.get("provider_values") is not None:
        await _save_provider_values(owner_id, kind, tool_id, body, project_id)

    return await resolve_tool(owner_id, tool, project_id)


async def _existing_secret_refs(
    owner_id: str, target_type: str, target_id: str, project_id: Optional[str]
) -> set:
    row = await _get_row(owner_id, target_type, target_id, project_id)
    return set((row or {}).get("secret_refs") or {})


async def _split_secret_values(
    owner_id: str,
    project_id: Optional[str],
    target_type: str,
    target_id: str,
    schema: valve_lib.ValveSchema,
    clean: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Route SECRET values to the vault; return (non_secret, secret_refs)."""
    values: Dict[str, Any] = {}
    refs: Dict[str, str] = {}
    for key, value in clean.items():
        definition = schema.get(key)
        if definition is not None and definition.type == "SECRET":
            refs[key] = await _store_secret(
                owner_id, project_id, target_type, target_id, key, str(value)
            )
        else:
            values[key] = value
    return values, refs


async def apply_provider_values(
    owner_id: str,
    kind: str,
    provider_id: str,
    values: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Validate + persist provider valves. Global writes also update the doc.

    Secrets are routed to the encrypted vault; the provider doc receives only a
    credential reference. Project-scoped provider overrides are stored in
    ``valve_values`` and never mutate the shared provider doc.
    """
    registry = _registry_for(kind)
    raw = await registry.get_raw_provider(provider_id)
    if raw is None:
        raise ValveError("Provider not found", 404)
    schema = _provider_schema(kind, raw.get("provider_type") or "")
    existing = await _get_row(owner_id, "provider", provider_id, project_id)
    existing_refs = set((existing or {}).get("secret_refs") or {})
    if _provider_has_legacy_secret(raw):
        existing_refs.add("api_key")
    clean, errors = valve_lib.validate_values(
        schema, values or {}, existing_secret_keys=existing_refs
    )
    if errors:
        raise ValveError("; ".join(errors), 400)
    values_patch, ref_patch = await _split_secret_values(
        owner_id, project_id, "provider", provider_id, schema, clean
    )
    await _upsert_row(
        owner_id, "provider", provider_id, project_id,
        {"values": values_patch, "secret_refs": ref_patch},
    )
    if project_id is None:
        doc_patch: Dict[str, Any] = {
            "updated_at": now_iso(),
            "configuration": {**_clean_provider_config(raw), **values_patch},
        }
        secret_id = ref_patch.get("api_key")
        if secret_id:
            doc_patch["credential_id"] = secret_id
            doc_patch["credential_owner_id"] = owner_id
            doc_patch["api_key_enc"] = None
        if kind == "search":
            await repo.search_providers.update_global(provider_id, doc_patch)
        else:
            await repo.fetch_providers.update_global(provider_id, doc_patch)
    return await registry.get_provider(provider_id)


async def _save_provider_values(
    owner_id: str, kind: str, tool_id: str, body: Dict[str, Any], project_id: Optional[str]
) -> None:
    provider_id = str(body.get("provider_id") or "").strip()
    if not provider_id and tool_id:
        row = await _get_row(owner_id, "tool", tool_id, project_id)
        provider_id = str((row or {}).get("provider_id") or "")
    if not provider_id:
        registry = _registry_for(kind)
        providers = await registry.list_providers(owner_id)
        default = next((p for p in providers if p.get("is_default")), None)
        provider_id = (default or {}).get("id") or (providers[0]["id"] if providers else "")
    if not provider_id:
        raise ValveError("No provider selected", 400)
    await apply_provider_values(
        owner_id, kind, provider_id, body.get("provider_values") or {}, project_id
    )


async def test_connection(
    owner_id: str, tool_doc: Dict[str, Any], project_id: Optional[str] = None
) -> Dict[str, Any]:
    """Validate the REAL configured endpoint for a tool, never a fabricated one."""
    resolved = await resolve_tool(owner_id, tool_doc, project_id)
    kind = resolved.get("provider_kind")
    tool_type = resolved.get("tool_type")
    if tool_type == "code_interpreter" or kind == "code_interpreter":
        status = resolved.get("status") or {}
        return {
            "ok": bool(status.get("available")),
            "status": status.get("health_status") or ("CONNECTED" if status.get("available") else "UNAVAILABLE"),
            "message": status.get("reason") or "Sandbox available",
        }
    if kind in ("search", "fetch"):
        provider_id = resolved.get("provider_id")
        registry = _registry_for(kind)
        adapter = await registry.get_adapter(provider_id)
        result = await adapter.test_connection()
        status = str(result.get("status") or "UNKNOWN")
        try:
            await registry.record_test_result(provider_id, status)
        except Exception:
            pass
        return result
    return {
        "ok": True,
        "status": "NOT_APPLICABLE",
        "message": "This tool has no external connection to test",
    }


async def list_tool_valves(owner_id: str, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Resolve every visible tool's valves. Provider list is cached per kind."""
    from .tools import list_tools

    tools = await list_tools(owner_id)
    items: List[Dict[str, Any]] = []
    for tool in tools:
        try:
            items.append(await resolve_tool(owner_id, tool, project_id))
        except Exception:
            # A single malformed tool must never break the whole listing.
            continue
    return items
