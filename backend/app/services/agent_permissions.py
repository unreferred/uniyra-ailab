"""V6.6 Tool, Knowledge & Permission Boundaries.

This module is the single authority that turns a FROZEN agent version's
declared configuration into the permissions a run may actually exercise.

Two independent concepts are deliberately kept apart:

* **Model capability** -- what the underlying model/adapter *can* call (tool
  calling). Reported as a tri-state (``True`` / ``False`` / ``None`` unknown)
  and never conflated with permission.
* **Agent permission** -- what the agent *version* allows, derived exclusively
  from ``version["tool_policy"]`` / ``version["knowledge_policy"]``.

Permissions are resolved from the version configuration and NOTHING else. No
prompt text, compiled prompt content, task text, context pack, tool output or
knowledge content is ever consulted here, so prompt injection cannot escalate an
agent's authority (see :func:`assert_no_prompt_permission_escalation`).

Instead of raising on an invalid reference, resolution records an explicit,
structured *denial* so the mismatch is observable on the run and in traces. The
canonical tool permission engine
(:func:`app.services.tools.resolve_effective_permission`) is reused for the
authorization decision; this module only classifies *why* a requested resource
was not admitted.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import providers
from .. import repository as repo
from ..models import PERMISSION_DENIAL_KINDS
from . import tools as tools_service

__all__ = [
    "PERMISSION_DENIAL_KINDS",
    "resolve_agent_permissions",
    "assert_no_prompt_permission_escalation",
]

_REASON_DENIED = "Tool is explicitly denied by the agent version"
_REASON_NOT_FOUND = "Tool was not found"
_REASON_FOREIGN = "Tool belongs to a different project"
_REASON_DISABLED = "Tool is disabled"
_REASON_NOT_ALLOWED = "Tool permissions are not granted"
_REASON_CAPABILITY = "The bound model cannot call tools"

_REASON_COLLECTION_NOT_FOUND = "Knowledge collection was not found"
_REASON_COLLECTION_FOREIGN = "Knowledge collection belongs to a different project"
_REASON_COLLECTION_DISABLED = "Knowledge collection has been deleted"

_REASON_KNOWLEDGE_ONLY = "not_supported_by_runtime"
_REASON_CITATION = "evaluation_only_not_runtime_enforced"


def _denial(
    kind: str,
    resource_type: str,
    resource_id: Any,
    name: Any,
    reason: str,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "name": name,
        "reason": reason,
    }


async def _lookup_tool(owner_id: str, tool_id: str) -> Optional[Dict[str, Any]]:
    """Owner-scoped tool lookup with an owner-id-less global builtin fallback."""
    try:
        doc = await repo.tools.get(tool_id, owner_id)
    except Exception:
        doc = None
    if doc is None:
        try:
            doc = await repo.tools.find_one({"id": tool_id, "scope": "global"})
        except Exception:
            doc = None
    return doc


async def _lookup_collection(
    owner_id: str, collection_id: str
) -> Optional[Dict[str, Any]]:
    try:
        return await repo.knowledge_collections.get(str(collection_id), owner_id)
    except Exception:
        return None


def _model_supports_tools(model: Any, adapter: Any) -> Optional[bool]:
    """Tri-state model tool capability: ``True``/``False``/``None`` (unknown).

    ``False`` is only asserted when the adapter explicitly cannot call tools or
    the model explicitly declares no tool support -- otherwise unknown metadata
    stays ``None`` (the tool is kept) rather than being guessed either way.
    """
    adapter_flag = getattr(adapter, "supports_tools", None) if adapter is not None else None
    if adapter_flag is False:
        return False
    status: Optional[str] = None
    if isinstance(model, dict) and model:
        try:
            status = providers.model_capability_status(model, "tools")
        except Exception:
            status = "UNKNOWN"
    if status == "UNSUPPORTED":
        return False
    if adapter_flag is True and status == "SUPPORTED":
        return True
    return None


async def _resolve_tools(
    owner_id: str,
    project_id: str,
    tool_policy: Dict[str, Any],
    *,
    model: Any,
    adapter: Any,
) -> Dict[str, Any]:
    default_deny = bool(tool_policy.get("default_deny", True))
    allowed_tool_ids = [
        str(item) for item in (tool_policy.get("allowed_tool_ids") or []) if str(item)
    ]
    denied_tool_ids = [
        str(item) for item in (tool_policy.get("denied_tool_ids") or []) if str(item)
    ]
    denied_set = set(denied_tool_ids)
    supports_tools = _model_supports_tools(model, adapter)

    denials: List[Dict[str, Any]] = []
    enabled_tool_ids: List[str] = []
    seen: set = set()
    for tool_id in allowed_tool_ids:
        if tool_id in seen:
            continue
        seen.add(tool_id)

        if tool_id in denied_set:
            denials.append(
                _denial("tool_denied", "tool", tool_id, None, _REASON_DENIED)
            )
            continue

        tool = await _lookup_tool(owner_id, tool_id)
        if tool is None:
            denials.append(
                _denial("tool_not_found", "tool", tool_id, None, _REASON_NOT_FOUND)
            )
            continue

        name = tool.get("name") or tool_id
        if tool.get("project_id") not in (None, project_id):
            denials.append(
                _denial(
                    "tool_foreign_project", "tool", tool_id, name, _REASON_FOREIGN
                )
            )
            continue
        if not bool(tool.get("enabled", True)):
            denials.append(
                _denial("tool_disabled", "tool", tool_id, name, _REASON_DISABLED)
            )
            continue

        # Reuse the canonical effective-permission engine: selection of an
        # allow-listed tool grants exactly that tool's declared permissions.
        try:
            decision = tools_service.resolve_effective_permission(
                tool, enabled=True, enabled_tool_ids=allowed_tool_ids
            )
        except Exception:  # pragma: no cover - permission engine is defensive
            decision = {"status": "DENIED", "reason": _REASON_NOT_ALLOWED}
        if decision.get("status") != "GRANTED":
            denials.append(
                _denial(
                    "tool_not_allowed",
                    "tool",
                    tool_id,
                    name,
                    decision.get("reason") or _REASON_NOT_ALLOWED,
                )
            )
            continue

        enabled_tool_ids.append(tool_id)

    # Capability gate: a permission the model physically cannot exercise must
    # not be reported as usable. Remove it and record the mismatch explicitly.
    if enabled_tool_ids and supports_tools is False:
        for tool_id in list(enabled_tool_ids):
            tool = await _lookup_tool(owner_id, tool_id)
            name = (tool or {}).get("name") or tool_id
            denials.append(
                _denial(
                    "tool_model_capability_missing",
                    "tool",
                    tool_id,
                    name,
                    _REASON_CAPABILITY,
                )
            )
            enabled_tool_ids.remove(tool_id)

    return {
        "default_deny": default_deny,
        "allowed_tool_ids": allowed_tool_ids,
        "denied_tool_ids": denied_tool_ids,
        "enabled_tool_ids": enabled_tool_ids,
        "model_capability": {"supports_tools": supports_tools},
        "denials": denials,
    }


async def _resolve_knowledge(
    owner_id: str,
    project_id: str,
    knowledge_policy: Dict[str, Any],
) -> Dict[str, Any]:
    knowledge_enabled = bool(knowledge_policy.get("knowledge_enabled", False))
    requested_collection_ids = [
        str(item)
        for item in (knowledge_policy.get("allowed_collection_ids") or [])
        if str(item)
    ]
    denials: List[Dict[str, Any]] = []
    resolved_collection_ids: List[str] = []

    if knowledge_enabled:
        seen: set = set()
        for collection_id in requested_collection_ids:
            if collection_id in seen:
                continue
            seen.add(collection_id)

            collection = await _lookup_collection(owner_id, collection_id)
            if collection is None:
                denials.append(
                    _denial(
                        "knowledge_collection_not_found",
                        "knowledge_collection",
                        collection_id,
                        None,
                        _REASON_COLLECTION_NOT_FOUND,
                    )
                )
                continue
            name = collection.get("name") or collection_id
            if collection.get("project_id") not in (None, project_id):
                denials.append(
                    _denial(
                        "knowledge_collection_foreign_project",
                        "knowledge_collection",
                        collection_id,
                        name,
                        _REASON_COLLECTION_FOREIGN,
                    )
                )
                continue
            if collection.get("status") == "deleted":
                denials.append(
                    _denial(
                        "knowledge_collection_disabled",
                        "knowledge_collection",
                        collection_id,
                        name,
                        _REASON_COLLECTION_DISABLED,
                    )
                )
                continue
            resolved_collection_ids.append(collection_id)

    return {
        "knowledge_enabled": knowledge_enabled,
        "resolved_collection_ids": resolved_collection_ids,
        "requested_collection_ids": requested_collection_ids,
        "denials": denials,
    }


async def resolve_agent_permissions(
    owner_id: str,
    project_id: str,
    version: Dict[str, Any],
    *,
    model: Optional[Dict[str, Any]] = None,
    adapter: Any = None,
) -> Dict[str, Any]:
    """Resolve an agent version's effective tool + knowledge permissions.

    Pure resolution: owner-and-project scoped, never raising for a missing,
    foreign, disabled or capability-incompatible resource. Returns the applied
    policy snapshots, a flat ``permission_denials`` list, and the policy fields
    the runtime intentionally cannot enforce (``policy_unsupported``).
    """
    version = version or {}
    tool_policy = version.get("tool_policy") or {}
    knowledge_policy = version.get("knowledge_policy") or {}

    tool_result = await _resolve_tools(
        owner_id, project_id, tool_policy, model=model, adapter=adapter
    )
    knowledge_result = await _resolve_knowledge(
        owner_id, project_id, knowledge_policy
    )

    unsupported: List[Dict[str, str]] = []
    if knowledge_policy.get("knowledge_only_mode"):
        unsupported.append(
            {"field": "knowledge_only_mode", "reason": _REASON_KNOWLEDGE_ONLY}
        )
    if knowledge_policy.get("citation_required"):
        unsupported.append(
            {"field": "citation_required", "reason": _REASON_CITATION}
        )

    permission_denials = list(tool_result["denials"]) + list(
        knowledge_result["denials"]
    )

    return {
        "tool_policy_applied": {
            "default_deny": tool_result["default_deny"],
            "allowed_tool_ids": tool_result["allowed_tool_ids"],
            "denied_tool_ids": tool_result["denied_tool_ids"],
            "enabled_tool_ids": tool_result["enabled_tool_ids"],
            "model_capability": tool_result["model_capability"],
        },
        "knowledge_policy_applied": {
            "knowledge_enabled": knowledge_result["knowledge_enabled"],
            "allowed_collection_ids": knowledge_result["resolved_collection_ids"],
            "requested_collection_ids": knowledge_result["requested_collection_ids"],
            "retrieval_top_k": knowledge_policy.get("retrieval_top_k"),
            "citation_required": bool(
                knowledge_policy.get("citation_required", False)
            ),
            "knowledge_only_mode": bool(
                knowledge_policy.get("knowledge_only_mode", False)
            ),
        },
        "permission_denials": permission_denials,
        "policy_unsupported": unsupported,
    }


def assert_no_prompt_permission_escalation(
    version: Dict[str, Any], resolved: Dict[str, Any]
) -> None:
    """Defensive sanity check: resolved ids never escape the version allowlist.

    Raises :class:`AssertionError` when a resolved tool or knowledge collection
    is not present in the frozen version's configured allowlist. Because
    :func:`resolve_agent_permissions` reads only ``version`` configuration, this
    can only fire on a programming error -- it can never be triggered by prompt
    content.
    """
    version = version or {}
    resolved = resolved or {}

    allowed_tools = {
        str(item)
        for item in ((version.get("tool_policy") or {}).get("allowed_tool_ids") or [])
    }
    enabled_tools = {
        str(item)
        for item in (
            (resolved.get("tool_policy_applied") or {}).get("enabled_tool_ids") or []
        )
    }
    if not enabled_tools <= allowed_tools:
        raise AssertionError(
            "Resolved tool ids escaped the agent version allowlist"
        )

    allowed_collections = {
        str(item)
        for item in (
            (version.get("knowledge_policy") or {}).get("allowed_collection_ids") or []
        )
    }
    resolved_collections = {
        str(item)
        for item in (
            (resolved.get("knowledge_policy_applied") or {}).get(
                "allowed_collection_ids"
            )
            or []
        )
    }
    if not resolved_collections <= allowed_collections:
        raise AssertionError(
            "Resolved collection ids escaped the agent version allowlist"
        )
