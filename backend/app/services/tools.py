"""Tool registry and execution contract (Phase 3).

Tools are persisted documents scoped to a global catalog, an owner, or a
project. Execution is a four-stage funnel -- validate, authorize, resolve,
run -- that always produces a persisted ``ToolCall`` record, including for
validation, permission and "not implemented" outcomes. Provider-facing schemas
are derived from the same registry so the model and the executor never diverge.
"""
import inspect
import json
import time
from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..models import new_id, now_iso
from . import builtin_tools
from .builtin_tools import BUILTIN_TOOL_DEFINITIONS

#: Canonical builtin fields owned by the server. Migration updates exactly
#: these fields and nothing else, so user/project metadata is preserved.
BUILTIN_CANONICAL_FIELDS = (
    "name",
    "display_name",
    "tool_type",
    "implementation",
    "permissions",
    "risk_level",
    "tags",
    "input_schema",
    "output_schema",
    "version",
)
#: Bumped whenever the canonical builtin schema shape changes. Used for
#: deterministic, idempotent migration of stale persisted builtin docs.
BUILTIN_SCHEMA_REVISION = 2

try:  # jsonschema is a declared dependency; guarded for import resilience.
    from jsonschema import Draft7Validator
    from jsonschema import validate as _jsonschema_validate  # noqa: F401
except Exception:  # pragma: no cover - fallback path
    Draft7Validator = None
    _jsonschema_validate = None


TOOL_TYPES = ("function",)
RISK_LEVELS = ("low", "medium", "high", "critical")
SAFE_BUILTIN_PERMISSIONS = {
    "calculator.execute",
    "datetime.read",
    "json.transform",
}

_MAX_STORED_INPUT_CHARS = 20_000
_MAX_ERROR_CHARS = 300


class ToolError(ValueError):
    """Raised for registry/execution contract violations. Carries an HTTP code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _collection(name: str):
    """Resolve a repository collection defensively (Phase 3 attributes)."""
    collection = getattr(repo, name, None)
    if collection is not None:
        return collection
    try:  # pragma: no cover - only hit before repository lands the attribute
        return repo.Repository(name)  # type: ignore[attr-defined]
    except Exception:
        return None


CUSTOM_TOOL_TYPES = ("http", "openapi", "code_interpreter")


def is_custom_tool(tool: dict) -> bool:
    """True when the tool doc is a versioned custom tool (not a builtin)."""
    if not isinstance(tool, dict):
        return False
    return tool.get("tool_type") in CUSTOM_TOOL_TYPES


def _resolve_executor(tool: dict) -> Optional[Any]:
    executor = builtin_tools.builtin_implementation(tool.get("implementation"))
    if executor is not None:
        return executor
    # Custom tools: executor is resolved per version at call time (see
    # ``execute_tool``); returning a marker here keeps resolution green.
    if is_custom_tool(tool):
        return _custom_tool_marker
    return None


def _custom_tool_marker(arguments: Any) -> Any:  # pragma: no cover - never called directly
    raise RuntimeError("Custom tool executors resolve per version at call time")


def _safe_input(arguments: Any) -> Any:
    """JSON-safe copy of tool arguments, truncated when large."""
    if arguments is None:
        return {}
    try:
        serialized = json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"_unserializable": True}
    if len(serialized) > _MAX_STORED_INPUT_CHARS:
        return {
            "_truncated": True,
            "size_chars": len(serialized),
            "preview": serialized[:_MAX_STORED_INPUT_CHARS],
        }
    return arguments


def _json_safe(value: Any) -> Optional[Any]:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except (TypeError, ValueError):
        return None


_SENSITIVE_DETAIL_PARTS = (
    "key", "secret", "token", "password", "passwd", "credential",
    "authorization", "cookie", "session",
)


def _safe_error_details(details: Any) -> Optional[Dict[str, Any]]:
    """Sanitize a provider error ``details`` mapping for the ToolResult.

    Plain-dict only, bounded in size, and drops credential-like keys. Never
    surfaces upstream headers or the raw body; values are truncated strings.
    """
    if not isinstance(details, dict):
        return None
    cleaned: Dict[str, Any] = {}
    for raw_key, value in list(details.items())[:20]:
        key = str(raw_key)
        if any(part in key.lower() for part in _SENSITIVE_DETAIL_PARTS):
            continue
        if isinstance(value, str):
            cleaned[key] = value[:500]
        elif value is None or isinstance(value, (int, float, bool)):
            cleaned[key] = value
        elif isinstance(value, (list, tuple)):
            cleaned[key] = [str(item)[:200] for item in list(value)[:20]]
        elif isinstance(value, dict):
            cleaned[key] = {
                str(k): str(v)[:200] for k, v in list(value.items())[:20]
            }
        else:
            cleaned[key] = str(value)[:200]
    return cleaned or None



CODE_INTERPRETER_TOOL_ID = "tool-code-interpreter-0001"


def _code_interpreter_seed_doc() -> dict:
    """Global code_interpreter registry entry (execution gated at call time)."""
    try:
        from . import code_interpreter as _ci
        input_schema = dict(getattr(_ci, "INPUT_SCHEMA", {}) or {})
    except Exception:
        input_schema = {"type": "object"}
    return {
        "id": CODE_INTERPRETER_TOOL_ID,
        "name": "code_interpreter",
        "display_name": "Code Interpreter",
        "description": "Execute Python code in a sandboxed Docker container.",
        "version": 1,
        "tool_type": "code_interpreter",
        "current_version_id": f"{CODE_INTERPRETER_TOOL_ID}-v1",
        "input_schema": input_schema,
        "output_schema": {},
        "enabled": True,
        "risk_level": "high",
        "permissions": ["code.execute"],
        "implementation": None,
        "scope": "global",
        "project_id": None,
        "owner_id": None,
        "builtin": False,
        "tags": ["code"],
    }


async def ensure_system_tools() -> None:
    """Seed the global code_interpreter tool doc + immutable v1 version snapshot."""
    tools_collection = _collection("tools")
    if tools_collection is None:
        return
    try:
        existing = await tools_collection.find_one({"id": CODE_INTERPRETER_TOOL_ID})
    except Exception:
        return
    if existing is None:
        try:
            now = now_iso()
            doc = _code_interpreter_seed_doc()
            doc["created_at"] = now
            doc["updated_at"] = now
            await tools_collection.insert(doc)
        except Exception:  # pragma: no cover - seeding is best-effort
            pass
    versions_collection = _collection("tool_versions")
    if versions_collection is None:
        return
    try:
        existing_version = await versions_collection.find_one(
            {"id": f"{CODE_INTERPRETER_TOOL_ID}-v1"}
        )
    except Exception:
        return
    if existing_version is None:
        try:
            from . import code_interpreter as _ci
            input_schema = dict(getattr(_ci, "INPUT_SCHEMA", {}) or {})
        except Exception:
            input_schema = {"type": "object"}
        try:
            now = now_iso()
            await versions_collection.insert({
                "id": f"{CODE_INTERPRETER_TOOL_ID}-v1",
                "owner_id": None,
                "tool_id": CODE_INTERPRETER_TOOL_ID,
                "version": 1,
                "version_label": "v1",
                "tool_type": "code_interpreter",
                "input_schema": input_schema,
                "output_schema": {},
                "permissions": ["code.execute"],
                "risk_level": "high",
                "implementation_config": {},
                "source": "system",
                "created_at": now,
                "immutable": True,
            })
        except Exception:  # pragma: no cover - seeding is best-effort
            pass


async def _migrate_builtin_tool(collection, existing: dict, definition: dict) -> bool:
    """Update ONLY canonical fields on a stale builtin doc.

    Preserves unrelated user/project metadata (description overrides,
    enabled, scope, project_id, owner_id, created_at, custom keys). Returns
    True when a write occurred. Deterministic and idempotent: a doc already
    matching the canonical definition plus current revision is left alone.
    """
    updates: Dict[str, Any] = {}
    for field in BUILTIN_CANONICAL_FIELDS:
        if field in definition and existing.get(field) != definition.get(field):
            updates[field] = definition[field]
    if existing.get("builtin_schema_revision") != BUILTIN_SCHEMA_REVISION:
        updates["builtin_schema_revision"] = BUILTIN_SCHEMA_REVISION
    if not updates:
        return False
    updates["updated_at"] = now_iso()
    try:
        await collection.update(existing.get("id"), existing.get("owner_id"), updates)
        return True
    except Exception:
        return False


async def ensure_builtin_tools() -> None:
    """Seed builtin tool definitions and migrate stale canonical schemas. Never overwrites non-canonical user state."""
    collection = _collection("tools")
    if collection is None:
        return
    try:
        await ensure_system_tools()
    except Exception:  # pragma: no cover - seeding is best-effort
        pass
    for definition in BUILTIN_TOOL_DEFINITIONS:
        try:
            existing = await collection.find_one({"id": definition["id"]})
            if existing is not None:
                await _migrate_builtin_tool(collection, existing, definition)
                continue
            now = now_iso()
            doc = dict(definition)
            doc["builtin_schema_revision"] = BUILTIN_SCHEMA_REVISION
            doc["created_at"] = now
            doc["updated_at"] = now
            await collection.insert(doc)
        except Exception:
            continue


def code_interpreter_availability() -> tuple:
    """Shared cached-only sandbox availability for registry views.

    Single source of truth for the ``code_interpreter`` tool's ``available``
    / ``availability_reason`` pair so ``services/tools.py`` and
    ``routers/tools.py`` can never diverge. Non-blocking: reads the cached
    snapshot only, never probes Docker. Cold cache (never probed) reports
    ``(False, "Code interpreter status unknown")`` rather than a bare
    disabled flag with an empty reason; probe failures keep the sandbox's
    safe reason string; unexpected errors map to
    ``"Code interpreter status unavailable"``. Never fakes an online status.
    """
    try:
        from . import code_interpreter as _ci_warm
        _warm = getattr(_ci_warm, "ensure_background_refresh", None)
        if callable(_warm):
            _warm()
    except Exception:
        pass
    try:
        from . import code_interpreter as _ci
        payload = _ci.check_availability_sync() if hasattr(_ci, "check_availability_sync") else None
        if payload is None:
            return False, "Code interpreter status unavailable"
        available = bool(payload.get("available"))
        reason = payload.get("reason") or None
        if not available and not reason:
            # Defensive: a falsy snapshot must always carry a reason string
            # so the frontend never renders "disabled" with no explanation.
            reason = "Code interpreter status unavailable"
        return available, reason
    except Exception:
        return False, "Code interpreter status unavailable"


def _public_tool(doc: dict) -> dict:
    """Build the client-safe tool view with availability metadata.

    ``available``/``availability_reason`` contract: disabled tools report
    ``(False, "Tool is disabled")``; the ``code_interpreter`` tool reports
    the shared cached sandbox snapshot via :func:`code_interpreter_availability`
    (cold cache -> ``"Code interpreter status unknown"``, never a faked
    online status); every other tool reports executor resolvability.
    Model-gated states (``MODEL_UNSUPPORTED`` / ``MODEL_CAPABILITY_UNKNOWN``)
    and project scoping (``DISABLED_BY_PROJECT``) are left to the frontend,
    which combines this registry availability with the model's capability
    status from ``providers.model_capability_status``.
    """
    enabled = bool(doc.get("enabled", True))
    if not enabled:
        available, reason = False, "Tool is disabled"
    elif is_custom_tool(doc) and (doc.get("tool_type") == "code_interpreter"
                                          or doc.get("id") == CODE_INTERPRETER_TOOL_ID
                                          or doc.get("name") == "code_interpreter"):
        available, reason = code_interpreter_availability()
    else:
        executor = _resolve_executor(doc)
        if executor is None:
            available, reason = False, "No executor available for implementation"
        else:
            available, reason = True, None
    decision = resolve_effective_permission(doc, for_selection=True)
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "display_name": doc.get("display_name") or doc.get("name"),
        "description": doc.get("description") or "",
        "version": doc.get("version"),
        "tool_type": doc.get("tool_type") or "function",
        "current_version_id": doc.get("current_version_id"),
        "input_schema": doc.get("input_schema") or {},
        "output_schema": doc.get("output_schema") or {},
        "enabled": enabled,
        "risk_level": doc.get("risk_level") or "low",
        "permissions": list(doc.get("permissions") or []),
        "required_permissions": list(decision.get("required_permission") or []),
        "permission_effective": decision.get("status"),
        "permission_source": decision.get("source"),
        "permission_reason": decision.get("reason"),
        "implementation": doc.get("implementation"),
        "scope": doc.get("scope") or "owner",
        "project_id": doc.get("project_id"),
        "builtin": bool(doc.get("builtin", False)),
        "owner_id": doc.get("owner_id"),
        "tags": list(doc.get("tags") or []),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "available": available,
        "availability_reason": reason,
    }


async def list_tools(
    owner_id: str,
    *,
    project_id: Optional[str] = None,
    include_disabled: bool = True,
) -> List[dict]:
    """List global builtins + owner tools + optional project tools."""
    try:
        from . import code_interpreter as _ci_warm
        _warm = getattr(_ci_warm, "ensure_background_refresh", None)
        if callable(_warm):
            _warm()
    except Exception:
        pass
    await ensure_builtin_tools()
    collection = _collection("tools")
    if collection is None:
        return []

    documents: List[dict] = []
    try:
        documents.extend(await collection.list({"scope": "global"}, limit=1000))
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        documents.extend(await collection.list({"owner_id": owner_id}, limit=1000))
    except Exception:  # pragma: no cover - defensive
        pass
    if project_id:
        try:
            documents.extend(await collection.list({"project_id": project_id}, limit=1000))
        except Exception:  # pragma: no cover - defensive
            pass

    deduped: Dict[str, dict] = {}
    for doc in documents:
        doc_id = doc.get("id")
        if not doc_id or doc_id in deduped:
            continue
        deduped[doc_id] = doc

    public = [_public_tool(doc) for doc in deduped.values()]
    if not include_disabled:
        public = [tool for tool in public if tool.get("enabled", True)]
    public.sort(key=lambda tool: (0 if tool.get("builtin") else 1, tool.get("name") or ""))
    return public


async def get_tool(owner_id: str, tool_id: str) -> dict:
    """Return the raw tool document (internal) or raise ToolError 404."""
    await ensure_builtin_tools()
    collection = _collection("tools")
    if collection is None:
        raise ToolError(f"Tool '{tool_id}' not found", 404)
    doc = None
    try:
        doc = await collection.get(tool_id, owner_id)
    except Exception:  # pragma: no cover - defensive
        doc = None
    if doc is None:
        try:
            doc = await collection.find_one({"id": tool_id, "scope": "global"})
        except Exception:  # pragma: no cover - defensive
            doc = None
    if doc is None:
        raise ToolError(f"Tool '{tool_id}' not found", 404)
    return doc


async def resolve_enabled_tools(
    owner_id: str,
    tool_ids: List[str],
    *,
    project_id: Optional[str] = None,
) -> List[dict]:
    """Resolve raw, enabled, implemented tool docs preserving input order."""
    resolved: List[dict] = []
    seen = set()
    for tool_id in tool_ids or []:
        if tool_id in seen:
            continue
        seen.add(tool_id)
        try:
            tool = await get_tool(owner_id, tool_id)
        except ToolError:
            raise ToolError(f"Tool '{tool_id}' not found", 400)
        if not tool.get("enabled", True):
            name = tool.get("name") or tool_id
            raise ToolError(f"Tool '{name}' is disabled", 400)
        if is_custom_tool(tool):
            # Custom tools: enforce a version snapshot, then attach it so
            # execution and provider schemas never need a second lookup.
            version = tool.get("_version")
            if version is None:
                try:
                    from . import custom_tools as _custom
                    _resolved_tool, version = await _custom.get_tool_with_version(
                        owner_id, tool.get("id")
                    )
                except Exception as exc:
                    status_code = getattr(exc, "status_code", None) or 500
                    if status_code == 404:
                        # Global system tools (e.g. code_interpreter) have no
                        # owner-scoped version row; the seeded v1 snapshot is
                        # the implementation.
                        version = None
                    else:
                        raise ToolError("tool version snapshot missing", 400)
            if version is None and tool.get("id") == CODE_INTERPRETER_TOOL_ID:
                version = {
                    "id": f"{CODE_INTERPRETER_TOOL_ID}-v1",
                    "tool_type": "code_interpreter",
                    "permissions": ["code.execute"],
                }
            if version is None:
                raise ToolError("tool version snapshot missing", 400)
            if isinstance(version, dict) and not version.get("permissions"):
                _declared = list(tool.get("permissions") or [])
                if _declared:
                    version = {**version, "permissions": _declared}
            tool = dict(tool)
            tool["_version"] = dict(version) if isinstance(version, dict) else version
            if not tool.get("input_schema") and isinstance(version, dict) and version.get("input_schema"):
                tool["input_schema"] = version.get("input_schema")
            resolved.append(tool)
            continue
        if _resolve_executor(tool) is None:
            name = tool.get("name") or tool_id
            raise ToolError(f"Tool '{name}' has no available implementation", 400)
        resolved.append(tool)
    return resolved


def provider_tool_schemas(tools: List[dict]) -> List[dict]:
    """Normalize tool docs into provider-agnostic function definitions."""
    schemas: List[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        schemas.append({
            "name": tool.get("name"),
            "description": tool.get("description") or "",
            "parameters": tool.get("input_schema") or {},
        })
    schemas.sort(key=lambda item: item.get("name") or "")
    return schemas


def _minimal_validation_errors(schema: dict, arguments: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(schema, dict) or not isinstance(arguments, dict):
        return errors
    for required in schema.get("required") or []:
        if required not in arguments:
            errors.append(f"{required}: required property is missing")
    properties = schema.get("properties") or {}
    for key, spec in properties.items():
        if key not in arguments or not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if not expected or isinstance(expected, list):
            continue
        value = arguments[key]
        checks = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "object": dict,
            "array": list,
            "boolean": bool,
        }
        if expected in checks and not isinstance(value, checks[expected]):
            errors.append(f"{key}: expected {expected}")
    return errors


def validate_tool_input(tool: dict, arguments: Any) -> List[str]:
    """Validate arguments against ``tool['input_schema']``; safe error strings."""
    schema = tool.get("input_schema") or {}
    if not isinstance(schema, dict):
        return []
    instance = arguments if arguments is not None else {}
    if Draft7Validator is None:  # pragma: no cover - fallback path
        return _minimal_validation_errors(schema, instance)
    errors: List[str] = []
    try:
        validator = Draft7Validator(schema)
        for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
            path = "/".join(str(part) for part in error.absolute_path) or "(root)"
            message = str(error.message)
            if len(message) > 200:
                message = message[:200]
            errors.append(f"{path}: {message}")
    except Exception:  # pragma: no cover - malformed schema must not crash
        return _minimal_validation_errors(schema, instance)
    return errors


def check_permission(tool: dict, granted: Optional[set] = None) -> bool:
    """Enforce default-deny: only safe builtin permissions are implicit."""
    permissions = tool.get("permissions") or []
    if not permissions:
        return True
    allowed = set(granted or set()) | SAFE_BUILTIN_PERMISSIONS
    return all(str(permission) in allowed for permission in permissions)


def declared_permissions(tool: dict) -> List[str]:
    """Union of tool-doc and attached ``_version`` declared permissions."""
    declared: List[str] = []
    for source in (tool or {}, (tool or {}).get("_version") or {}):
        if not isinstance(source, dict):
            continue
        for permission in source.get("permissions") or []:
            text = str(permission or "")
            if text and text not in declared:
                declared.append(text)
    return declared


def resolve_effective_permission(
    tool: dict,
    *,
    enabled: Optional[bool] = None,
    enabled_tool_ids: Optional[List[str]] = None,
    granted_permissions: Optional[set] = None,
    for_selection: bool = False,
) -> dict:
    """Canonical effective-permission decision (selection == execution).

    Required permission: the tool's declared ``permissions`` list
    (``code.execute`` for Code Interpreter). Grants:
    - GLOBAL: safe builtin permissions (``SAFE_BUILTIN_PERMISSIONS``) are
      implicit for every authenticated owner.
    - REQUEST: a tool explicitly present in ``enabled_tool_ids`` (the
      request/conversation/project effective selection) grants exactly its
      own declared permissions — selection-as-intent, resolved here as
      authorization. No wildcard: only the selected tool's own permissions.
    - SELECTION: when evaluating tool availability for selection (for_selection=True),
      enabled tools with grantable declared permissions report AVAILABLE_TO_SELECT
      so they are selectable in the UI before a request has been made.
    - ROLE/USER + PROJECT: the tool must be enabled (registry flag) and, for
      project-scoped tools, usable within the requesting project. Those are
      enforced here as denial sources; there is no per-user permission table
      beyond owner scoping, so the authenticated owner is the grant subject.
    Default-deny: anything else is DENIED with a human-readable reason.
    """
    tool = tool if isinstance(tool, dict) else {}
    required = [str(p) for p in (tool.get("permissions") or [])]
    version = tool.get("_version") if isinstance(tool.get("_version"), dict) else {}
    for permission in (version.get("permissions") or []) if isinstance(version, dict) else []:
        text = str(permission or "")
        if text and text not in required:
            required.append(text)
    if enabled is None:
        enabled = bool(tool.get("enabled", True))
    if not enabled:
        return {
            "status": "DENIED",
            "required_permission": required,
            "source": "PROJECT",
            "reason": "Tool is disabled",
            "missing": list(required),
        }
    if not required:
        status = "AVAILABLE_TO_SELECT" if for_selection else "GRANTED"
        return {
            "status": status,
            "required_permission": required,
            "source": "GLOBAL",
            "reason": "Tool declares no permissions",
            "missing": [],
        }
    if all(str(p) in SAFE_BUILTIN_PERMISSIONS for p in required):
        extra = [p for p in required if p not in SAFE_BUILTIN_PERMISSIONS]
        if not extra:
            status = "AVAILABLE_TO_SELECT" if for_selection else "GRANTED"
            return {
                "status": status,
                "required_permission": required,
                "source": "GLOBAL",
                "reason": "Safe builtin permission",
                "missing": [],
            }
    if for_selection:
        return {
            "status": "AVAILABLE_TO_SELECT",
            "required_permission": required,
            "source": "REQUEST",
            "reason": "Selectable: permissions granted upon selection",
            "missing": [],
        }
    granted = set(granted_permissions) if granted_permissions is not None else set()
    selected = (
        bool(enabled_tool_ids)
        and str(tool.get("id") or "") in set(map(str, enabled_tool_ids or []))
    )
    if selected:
        # Explicit selection grants the selected tool's own declared
        # permissions — and nothing else. Callers computing ``granted``
        # from the resolved tool set get the same decision via ``granted``.
        granted = granted | set(required)
    allowed = granted | SAFE_BUILTIN_PERMISSIONS
    missing = [p for p in required if p not in allowed]
    if not missing:
        source = "REQUEST" if selected else ("ROLE/USER" if granted else "GLOBAL")
        return {
            "status": "GRANTED",
            "required_permission": required,
            "source": source,
            "reason": (
                "Tool explicitly enabled for this run"
                if selected
                else "Permission granted"
            ),
            "missing": [],
        }
    return {
        "status": "DENIED",
        "required_permission": required,
        "source": "REQUEST" if enabled_tool_ids is not None else "GLOBAL",
        "reason": "Tool permissions are not granted",
        "missing": missing,
    }


def is_effectively_granted(
    tool: dict,
    *,
    enabled: Optional[bool] = None,
    enabled_tool_ids: Optional[List[str]] = None,
    granted_permissions: Optional[set] = None,
) -> bool:
    """Boolean view of :func:`resolve_effective_permission`."""
    return (
        resolve_effective_permission(
            tool,
            enabled=enabled,
            enabled_tool_ids=enabled_tool_ids,
            granted_permissions=granted_permissions,
        ).get("status") == "GRANTED"
    )


def _tool_call_doc(
    tool: dict,
    arguments: Any,
    owner_id: str,
    trace_id: Optional[str],
    run_id: Optional[str],
    provider_tool_call_id: Optional[str] = None,
) -> dict:
    """Build the ToolCall skeleton.

    ``id`` is the internal ToolCall record id; ``provider_tool_call_id``
    is the LLM's ``call.id`` threaded through from the agentic loop (None
    for standalone tests). The two id spaces are stored distinctly and
    never conflated.
    """
    return {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": tool.get("project_id"),
        "trace_id": trace_id,
        "run_id": run_id,
        "provider_tool_call_id": provider_tool_call_id,
        "tool_id": tool.get("id"),
        "tool_name": tool.get("name"),
        "tool_version": tool.get("version"),
        "tool_version_id": (
            (tool.get("_version") or {}).get("id")
            if isinstance(tool.get("_version"), dict)
            else tool.get("current_version_id")
        ),
        "tool_type": tool.get("tool_type") or "function",
        "input": _safe_input(arguments),
        "validation_status": None,
        "permission_status": None,
        "execution_status": None,
        "output": None,
        "error": None,
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
        "created_at": now_iso(),
    }


async def record_tool_call(doc: dict) -> dict:
    """Persist a ToolCall document best-effort. Never raises."""
    collection = _collection("tool_calls")
    if collection is not None:
        try:
            await collection.insert(doc)
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass
    return doc


async def execute_tool(
    tool: dict,
    arguments: Any,
    *,
    owner_id: str,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    provider_tool_call_id: Optional[str] = None,
    granted_permissions: Optional[set] = None,
    enabled_tool_ids: Optional[List[str]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> dict:
    """Run the validate -> authorize -> resolve -> execute funnel.

    Always returns (and persists, best-effort) a ToolCall document. Never leaks
    tracebacks or unvalidated exception text beyond a truncated message.
    ``provider_tool_call_id`` carries the LLM's ``call.id`` (None for
    standalone tests) through to the persisted ToolCall and, for
    code_interpreter tools, to ``CodeExecution.tool_call_id``.
    Authorization uses the canonical :func:`resolve_effective_permission`
    resolver (same semantics as selection gating): ``granted_permissions``
    carries the run's explicit-enable grant set, or ``enabled_tool_ids``
    carries the run's selection so the selected tool's own declared
    permissions grant exactly itself. Default-deny otherwise.
    """
    doc = _tool_call_doc(tool, arguments, owner_id, trace_id, run_id, provider_tool_call_id)

    # 1. validate
    errors = validate_tool_input(tool, arguments)
    if errors:
        doc["validation_status"] = "failed"
        doc["execution_status"] = "skipped"
        doc["error"] = {
            "code": "TOOL_VALIDATION_FAILED",
            "message": "Tool input failed validation",
            "errors": errors,
        }
        doc["started_at"] = now_iso()
        doc["completed_at"] = doc["started_at"]
        doc["duration_ms"] = 0
        return await record_tool_call(doc)

    doc["validation_status"] = "passed"

    # 2. authorize — canonical resolver (selection == execution).
    decision = resolve_effective_permission(
        tool,
        enabled_tool_ids=enabled_tool_ids,
        granted_permissions=granted_permissions,
    )
    if decision.get("status") != "GRANTED":
        doc["permission_status"] = "denied"
        doc["execution_status"] = "skipped"
        missing = decision.get("missing") or declared_permissions(tool)
        doc["error"] = {
            "code": "TOOL_PERMISSION_DENIED",
            "message": "Tool permissions are not granted",
            "missing_permissions": missing,
            "required_permissions": decision.get("required_permission") or [],
            "permission_source": decision.get("source"),
        }
        doc["started_at"] = now_iso()
        doc["completed_at"] = doc["started_at"]
        doc["duration_ms"] = 0
        return await record_tool_call(doc)

    doc["permission_status"] = "granted"

    # 3b. custom tools dispatch through the versioned executor (same funnel,
    # but the versioned path persists its own ToolCall doc — return it directly
    # to avoid double-persistence).
    if is_custom_tool(tool):
        try:
            from . import custom_tools as _custom
            version = tool.get("_version")
            if not isinstance(version, dict):
                _resolved_tool, version = await _custom.get_tool_with_version(
                    owner_id, tool.get("id")
                )
            if version is None and tool.get("id") == CODE_INTERPRETER_TOOL_ID:
                version = {
                    "id": f"{CODE_INTERPRETER_TOOL_ID}-v1",
                    "tool_type": "code_interpreter",
                    "permissions": ["code.execute"],
                }
            if version is None:
                raise ToolError("tool version snapshot missing", 400)
            if isinstance(version, dict) and not version.get("permissions"):
                _declared2 = declared_permissions(tool)
                if _declared2:
                    version = {**version, "permissions": _declared2}
        except Exception as exc:
            # Version lookup failures (CustomToolError included) become a
            # structured ToolCall, never a raised exception.
            message = (
                getattr(exc, "message", None) or str(exc) or "tool version snapshot missing"
            )
            doc["execution_status"] = "failed"
            doc["output"] = None
            doc["error"] = {"code": "TOOL_EXECUTION_FAILED", "message": message[:_MAX_ERROR_CHARS]}
            doc["started_at"] = doc.get("started_at") or now_iso()
            doc["completed_at"] = now_iso()
            doc["duration_ms"] = doc.get("duration_ms") or 0
            return await record_tool_call(doc)
        # Forward the same effective grant so Gate B (version-level) agrees
        # with Gate A (tool-level): the selected tool's own declared
        # permissions. Explicit ``granted_permissions`` wins when provided.
        _forward = (
            set(granted_permissions)
            if granted_permissions is not None
            else set(declared_permissions(tool))
        )
        if enabled_tool_ids and str(tool.get("id") or "") in set(map(str, enabled_tool_ids)):
            _forward = _forward | set(declared_permissions(tool))
        return await _custom.execute_custom_tool(
            tool,
            version,
            arguments,
            owner_id=owner_id,
            project_id=tool.get("project_id"),
            trace_id=trace_id,
            run_id=run_id,
            granted_permissions=_forward,
            provider_tool_call_id=provider_tool_call_id,
        )

    # 3. resolve
    executor = _resolve_executor(tool)
    if executor is None:
        doc["execution_status"] = "failed"
        doc["error"] = {
            "code": "TOOL_NOT_IMPLEMENTED",
            "message": "Tool implementation is not available",
        }
        doc["started_at"] = now_iso()
        doc["completed_at"] = doc["started_at"]
        doc["duration_ms"] = 0
        return await record_tool_call(doc)

    # 4. execute
    doc["started_at"] = now_iso()
    start = time.perf_counter()
    try:
        exec_context = {
            **(context or {}),
            "owner_id": owner_id,
            "project_id": (context or {}).get("project_id") or tool.get("project_id"),
        }
        if inspect.iscoroutinefunction(executor):
            sig = inspect.signature(executor)
            if "context" in sig.parameters:
                raw_output = await executor(arguments or {}, context=exec_context)
            else:
                raw_output = await executor(arguments or {})
        else:
            sig = inspect.signature(executor)
            if "context" in sig.parameters:
                raw_output = executor(arguments or {}, context=exec_context)
            else:
                raw_output = executor(arguments or {})
        if not isinstance(raw_output, dict):
            raw_output = {"result": raw_output}
        safe_output = _json_safe(raw_output)
        if safe_output is None:
            raise ValueError("Tool output was not JSON-serializable")
        doc["output"] = safe_output
        doc["execution_status"] = "success"
        doc["error"] = None
    except Exception as exc:
        doc["execution_status"] = "failed"
        doc["output"] = None
        message = str(exc)[:_MAX_ERROR_CHARS] or "Tool execution failed"
        error: Dict[str, Any] = {
            "code": getattr(exc, "code", None) or "TOOL_EXECUTION_FAILED",
            "message": message,
        }
        provider = getattr(exc, "provider", None)
        if provider:
            error["provider"] = provider
        upstream_status = getattr(exc, "upstream_status", None)
        if upstream_status is not None:
            error["upstream_status"] = upstream_status
        safe_details = _safe_error_details(getattr(exc, "details", None))
        if safe_details:
            error["details"] = safe_details
        doc["error"] = error
    finally:
        doc["completed_at"] = now_iso()
        doc["duration_ms"] = int((time.perf_counter() - start) * 1000)
    return await record_tool_call(doc)


async def list_tool_calls(
    owner_id: str,
    *,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[dict]:
    """List tool calls for an owner, optionally filtered by trace/run."""
    collection = _collection("tool_calls")
    if collection is None:
        return []
    query: Dict[str, Any] = {"owner_id": owner_id}
    if trace_id:
        query["trace_id"] = trace_id
    if run_id:
        query["run_id"] = run_id
    try:
        return await collection.list(query, limit=limit, offset=offset)
    except Exception:  # pragma: no cover - defensive
        return []


def tool_doc_to_context_definitions(tools: List[dict]) -> List[dict]:
    """Shape tool docs for the ContextSnapshot TOOL_DEFINITIONS component."""
    definitions: List[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name") or ""
        definitions.append({
            "name": name,
            "display_name": tool.get("display_name") or name,
            "description": tool.get("description") or "",
            "input_schema": tool.get("input_schema") or {},
        })
    return definitions
