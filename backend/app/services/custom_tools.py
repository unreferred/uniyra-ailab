"""Custom tool registry with immutable versioning (Custom Tool Platform).

Tools are metadata docs; each behavior change creates a new immutable
``tool_versions`` snapshot (never mutated in place). Execution mirrors the
``tools.execute_tool`` four-stage funnel (validate -> authorize -> resolve ->
run) and always persists a ``ToolCall`` record via
``tools.record_tool_call`` with extra ``tool_version_id``/``tool_type``
fields.

Explicit-enable authorization semantics: the act of selecting a tool for a
run grants its declared permissions. Callers pass
``granted_permissions=set(version.permissions)`` when the tool was selected
(e.g. enabled for the run / standalone test); the default ``None`` grants
exactly the declared permissions for the same reason. A caller that wants
stricter scoping passes a smaller set, and any ungranted declared
permission denies with TOOL_PERMISSION_DENIED.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .. import repository as repo
from ..models import CUSTOM_TOOL_TYPES, TOOL_VERSION_SOURCES, new_id, now_iso
from . import tools as tool_service
from .http_tool import HttpToolError, execute_http, validate_implementation_config

_MAX_ERROR_CHARS = 300
_MAX_OUTPUT_CHARS = 200_000


class CustomToolError(ValueError):
    """Raised for custom-tool contract violations. Carries an HTTP code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def validate_tool_type(tool_type: str) -> str:
    if tool_type not in CUSTOM_TOOL_TYPES:
        raise CustomToolError(f"Unsupported tool type '{tool_type}'", 400)
    return tool_type


def _validate_code_interpreter_config(cfg: Any) -> Dict[str, Any]:
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise CustomToolError("code_interpreter config must be an object", 400)
    allowed = {"limits"}
    for key in cfg:
        if key not in allowed:
            raise CustomToolError(
                f"code_interpreter config key '{key}' is not allowed", 400
            )
    if "limits" in cfg and not isinstance(cfg["limits"], dict):
        raise CustomToolError("code_interpreter limits must be an object", 400)
    return dict(cfg)


def _validate_version_payload(version: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(version, dict):
        raise CustomToolError("version must be an object", 400)
    tool_type = version.get("tool_type")
    validate_tool_type(tool_type)
    input_schema = version.get("input_schema") or {}
    if not isinstance(input_schema, dict):
        raise CustomToolError("version.input_schema must be an object", 400)
    output_schema = version.get("output_schema") or {}
    if not isinstance(output_schema, dict):
        raise CustomToolError("version.output_schema must be an object", 400)
    permissions = version.get("permissions") or []
    if not isinstance(permissions, list):
        raise CustomToolError("version.permissions must be a list", 400)
    risk_level = version.get("risk_level") or "low"
    if risk_level not in ("low", "medium", "high", "critical"):
        raise CustomToolError(f"Unsupported risk level '{risk_level}'", 400)
    raw_config = version.get("implementation_config") or {}
    if tool_type in ("http", "openapi"):
        try:
            cleaned_config = validate_implementation_config(raw_config)
        except HttpToolError as exc:
            raise CustomToolError(
                exc.message, exc.status_code if isinstance(exc.status_code, int) else 400
            ) from exc
    else:
        cleaned_config = _validate_code_interpreter_config(raw_config)
    source = version.get("source") or "manual"
    if source not in TOOL_VERSION_SOURCES:
        raise CustomToolError(f"Unsupported version source '{source}'", 400)
    return {
        "tool_type": tool_type,
        "version_label": version.get("version_label"),
        "input_schema": input_schema,
        "output_schema": output_schema,
        "permissions": list(permissions),
        "risk_level": risk_level,
        "implementation_config": cleaned_config,
        "source": source,
    }


async def _require_project(owner_id: str, project_id: Optional[str]) -> None:
    if project_id is None:
        return
    project = await repo.projects.get(project_id, owner_id)
    if project is None:
        raise CustomToolError("Project not found", 404)


async def create_custom_tool(owner_id: str, payload: Dict[str, Any]) -> Tuple[dict, dict]:
    """Create a tool doc plus its immutable version 1 snapshot."""
    if not isinstance(payload, dict):
        raise CustomToolError("Tool payload must be an object", 400)
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CustomToolError("Tool name is required", 400)
    project_id = payload.get("project_id")
    await _require_project(owner_id, project_id)
    query: Dict[str, Any] = {"owner_id": owner_id, "name": name}
    query["project_id"] = project_id
    existing = await repo.tools.find_one(query)
    if existing is not None:
        raise CustomToolError(f"Tool '{name}' already exists", 409)
    cleaned = _validate_version_payload(payload.get("version") or {})
    now = now_iso()
    tool_id = new_id()
    version_id = new_id()
    tool = {
        "id": tool_id,
        "owner_id": owner_id,
        "project_id": project_id,
        "name": name,
        "display_name": payload.get("display_name") or name,
        "description": payload.get("description") or "",
        "enabled": bool(payload.get("enabled", True)),
        "builtin": False,
        "tool_type": cleaned["tool_type"],
        "scope": "project" if project_id else "owner",
        "version": 1,
        "current_version_id": version_id,
        "input_schema": cleaned["input_schema"],
        "output_schema": cleaned["output_schema"],
        "permissions": list(cleaned["permissions"]),
        "risk_level": cleaned["risk_level"],
        "tags": list(payload.get("tags") or []),
        "created_at": now,
        "updated_at": now,
    }
    version = {
        "id": version_id,
        "owner_id": owner_id,
        "tool_id": tool_id,
        "version": 1,
        "version_label": cleaned.get("version_label"),
        "tool_type": cleaned["tool_type"],
        "input_schema": cleaned["input_schema"],
        "output_schema": cleaned["output_schema"],
        "permissions": list(cleaned["permissions"]),
        "risk_level": cleaned["risk_level"],
        "implementation_config": cleaned["implementation_config"],
        "source": cleaned["source"],
        "created_at": now,
        "immutable": True,
    }
    await repo.tools.insert(tool)
    await repo.tool_versions.insert(version)
    return tool, version


async def _load_tool(owner_id: str, tool_id: str) -> dict:
    tool = await repo.tools.get(tool_id, owner_id)
    if tool is None:
        # Global builtins live under a null owner; surface them so callers
        # can report the builtin-immutable path instead of a bare 404 for
        # known ids, unless the tool truly does not exist.
        tool = await repo.tools.find_one({"id": tool_id, "scope": "global"})
    if tool is None:
        raise CustomToolError("Tool not found", 404)
    return tool


async def update_custom_tool(
    owner_id: str, tool_id: str, patch: Dict[str, Any]
) -> Tuple[dict, Optional[dict]]:
    """Update metadata in place; a ``version`` payload appends version max+1."""
    tool = await _load_tool(owner_id, tool_id)
    if tool.get("builtin"):
        raise CustomToolError("Builtin tools are immutable; disable instead", 400)
    if tool.get("owner_id") != owner_id:
        raise CustomToolError("Tool not found", 404)
    update: Dict[str, Any] = {}
    for key in ("display_name", "description", "enabled", "tags"):
        if key in patch and patch[key] is not None:
            update[key] = patch[key]
    new_version: Optional[dict] = None
    if patch.get("version") is not None:
        cleaned = _validate_version_payload(patch["version"])
        existing_versions = await repo.tool_versions.list(
            {"owner_id": owner_id, "tool_id": tool_id}, limit=5000
        )
        max_version = 0
        for item in existing_versions:
            try:
                max_version = max(max_version, int(item.get("version") or 0))
            except (TypeError, ValueError):
                continue
        next_number = max(max_version, int(tool.get("version") or 0)) + 1
        now = now_iso()
        new_version = {
            "id": new_id(),
            "owner_id": owner_id,
            "tool_id": tool_id,
            "version": next_number,
            "version_label": cleaned.get("version_label"),
            "tool_type": cleaned["tool_type"],
            "input_schema": cleaned["input_schema"],
            "output_schema": cleaned["output_schema"],
            "permissions": list(cleaned["permissions"]),
            "risk_level": cleaned["risk_level"],
            "implementation_config": cleaned["implementation_config"],
            "source": cleaned["source"],
            "created_at": now,
            "immutable": True,
        }
        await repo.tool_versions.insert(new_version)
        update["version"] = next_number
        update["current_version_id"] = new_version["id"]
        update["tool_type"] = cleaned["tool_type"]
        update["input_schema"] = cleaned["input_schema"]
        update["output_schema"] = cleaned["output_schema"]
        update["permissions"] = list(cleaned["permissions"])
        update["risk_level"] = cleaned["risk_level"]
    if update:
        update["updated_at"] = now_iso()
        updated = await repo.tools.update(tool_id, owner_id, update)
        tool = updated or {**tool, **update}
    return tool, new_version


async def get_tool_with_version(owner_id: str, tool_id: str) -> Tuple[dict, dict]:
    """Return (tool, current version); 500 when the snapshot is missing."""
    tool = await _load_tool(owner_id, tool_id)
    if tool.get("builtin"):
        raise CustomToolError("Builtin tools have no versions", 404)
    if tool.get("scope") != "global" and tool.get("owner_id") != owner_id:
        raise CustomToolError("Tool not found", 404)
    version_id = tool.get("current_version_id")
    version = None
    if version_id:
        version = await repo.tool_versions.get(version_id, owner_id)
        if version is None and tool.get("scope") == "global":
            version = await repo.tool_versions.find_one({"id": version_id})
    if version is None and tool.get("id") == "tool-code-interpreter-0001":
        version = {
            "id": f"tool-code-interpreter-0001-v1",
            "tool_type": "code_interpreter",
            "permissions": ["code.execute"],
        }
    if version is None:
        raise CustomToolError("Tool version snapshot is missing", 500)
    return tool, version


async def list_versions(owner_id: str, tool_id: str) -> List[dict]:
    tool = await _load_tool(owner_id, tool_id)
    if tool.get("owner_id") != owner_id:
        raise CustomToolError("Tool not found", 404)
    if tool.get("builtin"):
        return []
    items = await repo.tool_versions.list(
        {"owner_id": owner_id, "tool_id": tool_id},
        sort_field="version",
        descending=False,
        limit=5000,
    )
    return sorted(items, key=lambda v: int(v.get("version") or 0))


async def get_version(owner_id: str, version_id: str) -> dict:
    version = await repo.tool_versions.get(version_id, owner_id)
    if version is None:
        raise CustomToolError("Tool version not found", 404)
    tool = await repo.tools.get(version.get("tool_id"), owner_id)
    if tool is None:
        raise CustomToolError("Tool not found", 404)
    return version


async def delete_custom_tool(owner_id: str, tool_id: str) -> dict:
    """Delete a custom tool after reference checks."""
    tool = await repo.tools.get(tool_id, owner_id)
    if tool is None:
        maybe_global = await repo.tools.find_one({"id": tool_id, "scope": "global"})
        if maybe_global is not None:
            raise CustomToolError("Builtin tools cannot be deleted", 400)
        raise CustomToolError("Tool not found", 404)
    if tool.get("builtin"):
        raise CustomToolError("Builtin tools cannot be deleted", 400)
    calls = await repo.tool_calls.list(
        {"owner_id": owner_id, "tool_id": tool_id}, limit=1
    )
    if calls:
        raise CustomToolError("Tool has recorded calls and cannot be deleted", 409)
    runs = await repo.runs.list(
        {"owner_id": owner_id, "enabled_tool_ids": {"$in": [tool_id]}}, limit=1
    )
    if runs:
        raise CustomToolError("Tool is enabled on runs and cannot be deleted", 409)
    await repo.tool_versions.delete_many({"owner_id": owner_id, "tool_id": tool_id})
    await repo.tools.delete(tool_id, owner_id)
    return {"ok": True}


def _cap_output(value: Any) -> Tuple[Any, bool]:
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"_unserializable": True}, True
    if len(serialized) > _MAX_OUTPUT_CHARS:
        return {
            "_truncated": True,
            "size_chars": len(serialized),
            "preview": serialized[:_MAX_OUTPUT_CHARS],
        }, True
    return value, False


async def execute_custom_tool(
    tool: Dict[str, Any],
    version: Dict[str, Any],
    arguments: Any,
    *,
    owner_id: str,
    project_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
    span_parent: Optional[str] = None,
    granted_permissions: Optional[Set[str]] = None,
    provider_tool_call_id: Optional[str] = None,
) -> dict:
    """Run the validate -> authorize -> resolve -> execute funnel.

    Always returns (and persists, best-effort) a ToolCall document mirroring
    ``tools.execute_tool``, plus ``tool_version_id``/``tool_type``.
    ``provider_tool_call_id`` is the LLM's ``call.id`` (None for standalone
    tests); it is stored distinctly from the internal ToolCall ``id`` and,
    for code_interpreter tools, forwarded as ``CodeExecution.tool_call_id``.
    """
    effective_project = project_id or tool.get("project_id") or version.get("project_id")
    doc: Dict[str, Any] = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": effective_project,
        "trace_id": trace_id,
        "run_id": run_id,
        "provider_tool_call_id": provider_tool_call_id,
        "tool_id": tool.get("id"),
        "tool_name": tool.get("name"),
        "tool_version": version.get("version"),
        "tool_version_id": version.get("id"),
        "tool_type": version.get("tool_type") or tool.get("tool_type"),
        "input": tool_service._safe_input(arguments),
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

    # 1. validate against the version snapshot schema.
    errors = tool_service.validate_tool_input(version, arguments)
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
        return await tool_service.record_tool_call(doc)
    doc["validation_status"] = "passed"

    # 2. authorize (explicit-enable semantics).
    declared = set(version.get("permissions") or [])
    granted = set(granted_permissions) if granted_permissions is not None else set(declared)
    if any(p not in granted for p in declared):
        doc["permission_status"] = "denied"
        doc["execution_status"] = "skipped"
        doc["error"] = {
            "code": "TOOL_PERMISSION_DENIED",
            "message": "Tool permissions are not granted",
        }
        doc["started_at"] = now_iso()
        doc["completed_at"] = doc["started_at"]
        doc["duration_ms"] = 0
        return await tool_service.record_tool_call(doc)
    doc["permission_status"] = "granted"

    # 3. resolve executor.
    tool_type = version.get("tool_type") or tool.get("tool_type")
    if tool_type == "code_interpreter":
        try:
            from . import code_interpreter as ci  # lazy: Wave 2 owns this service
            executor_kind = ("code", ci)
        except Exception as exc:
            doc["execution_status"] = "failed"
            doc["error"] = {
                "code": "TOOL_EXECUTION_FAILED",
                "message": "Code interpreter is unavailable",
            }
            doc["started_at"] = now_iso()
            doc["completed_at"] = doc["started_at"]
            doc["duration_ms"] = 0
            return await tool_service.record_tool_call(doc)
    elif tool_type in ("http", "openapi"):
        executor_kind = ("http", None)
    else:
        doc["execution_status"] = "failed"
        doc["error"] = {
            "code": "TOOL_NOT_IMPLEMENTED",
            "message": "Tool implementation is not available",
        }
        doc["started_at"] = now_iso()
        doc["completed_at"] = doc["started_at"]
        doc["duration_ms"] = 0
        return await tool_service.record_tool_call(doc)

    # 4. execute with timing.
    span = None
    span_start: Optional[float] = None
    if trace_id and span_parent is not None:
        try:
            from . import tracing as tracing_svc
            span_start = time.perf_counter()
            span = await tracing_svc.start_span(
                owner_id, trace_id, "TOOL_CALL",
                str(tool.get("name") or tool.get("id")),
                parent_span_id=span_parent, run_id=run_id,
            )
        except Exception:
            span = None
    doc["started_at"] = now_iso()
    start = time.perf_counter()
    try:
        if executor_kind[0] == "http":
            raw_output = await execute_http(
                owner_id, effective_project,
                version.get("implementation_config") or {}, arguments or {},
            )
        else:
            ci = executor_kind[1]
            args = arguments if isinstance(arguments, dict) else {}
            # ``tool_call_id`` on the CodeExecution rows references the
            # internal ToolCall ``doc["id"]`` (same id space as
            # ``code_interpreter.execute_code(tool_call_id=...)``); the
            # provider call id lives separately on
            # ``doc["provider_tool_call_id"]`` and is never conflated.
            raw_output = await ci.execute_code(
                owner_id=owner_id,
                project_id=effective_project,
                code=args.get("code") or "",
                input_file_ids=list(args.get("input_file_ids") or []),
                trace_id=trace_id,
                run_id=run_id,
                tool_call_id=doc.get("id"),
            )
        if not isinstance(raw_output, dict):
            raw_output = {"result": raw_output}
        capped, _ = _cap_output(raw_output)
        doc["output"] = capped
        doc["execution_status"] = "success"
        doc["error"] = None
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        message = getattr(exc, "message", None) or str(exc)
        message = (message or "Tool execution failed")[:_MAX_ERROR_CHARS]
        doc["execution_status"] = "failed"
        doc["output"] = None
        doc["error"] = {"code": "TOOL_EXECUTION_FAILED", "message": message}
        if isinstance(status_code, int):
            doc["error"]["status_code"] = status_code
    finally:
        doc["completed_at"] = now_iso()
        doc["duration_ms"] = int((time.perf_counter() - start) * 1000)
    if span is not None:
        try:
            from . import tracing as tracing_svc
            await tracing_svc.finish_span(
                span,
                status="completed" if doc["execution_status"] == "success" else "failed",
                started_perf=span_start,
            )
        except Exception:
            pass
    return await tool_service.record_tool_call(doc)


async def test_tool(
    owner_id: str,
    tool_id: str,
    arguments: Dict[str, Any],
    project_id: Optional[str] = None,
    code: Optional[str] = None,
) -> dict:
    """Standalone tool test: fresh ``tool_test`` trace + execution. No secrets."""
    tool, version = await get_tool_with_version(owner_id, tool_id)
    effective_project = project_id or tool.get("project_id")
    args = dict(arguments or {})
    if code is not None and (version.get("tool_type") == "code_interpreter"):
        args["code"] = code
    trace = None
    trace_start: Optional[float] = None
    try:
        from . import tracing as tracing_svc
        trace_start = time.perf_counter()
        trace = await tracing_svc.start_trace(
            owner_id, "tool_test", project_id=effective_project,
            metadata={"tool_id": tool_id},
        )
    except Exception:
        trace = None
    tool_call = await execute_custom_tool(
        tool, version, args,
        owner_id=owner_id, project_id=effective_project,
        trace_id=(trace.get("id") if isinstance(trace, dict) else None),
        granted_permissions=set(version.get("permissions") or []),
    )
    if isinstance(trace, dict):
        try:
            from . import tracing as tracing_svc
            await tracing_svc.finish_trace(trace, started_perf=trace_start)
        except Exception:
            pass
    return {
        "tool_call": tool_call,
        "output": tool_call.get("output"),
        "trace_id": trace.get("id") if isinstance(trace, dict) else None,
    }
