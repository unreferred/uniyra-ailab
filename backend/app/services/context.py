"""Context assembly, token estimation, hashing, persistence and diffing.

This module turns a request (system prompt, message history, attachments, tool
definitions, project/dataset context) into a deterministic, auditable context
assembly. Two properties matter most:

* Token counts are never fabricated. When ``tiktoken`` is unavailable the
  estimator reports its heuristic fallback by name; when a provider reports a
  real token count it is recorded separately from the estimate.
* The assembly is stable: identical inputs produce an identical
  ``context_hash``, so snapshots and runs can be compared across time.
"""
import difflib
import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import (
    CONTEXT_COMPONENT_TYPES,
    CONTEXT_STRATEGIES,
    new_id,
    now_iso,
)


# Canonical component ordering. Lower sorts first; ties preserve input order.
# Additive (V6.2): PROMPT/POLICY/INVARIANT sit just after SYSTEM and TOOL_RESULT
# sits just before the generic OTHER fallback; every pre-existing value is
# unchanged so existing snapshots keep their exact ordering.
_ORDERING: Dict[str, int] = {
    "SYSTEM": 0,
    "PROMPT": 5,
    "POLICY": 6,
    "INVARIANT": 7,
    "USER_TASK": 10,
    "CONVERSATION_HISTORY": 20,
    "ATTACHMENTS": 30,
    "PROJECT_CONTEXT": 40,
    "DATASET_CASE": 50,
    "RETRIEVED_CONTEXT": 60,
    "TOOL_RESULT": 65,
    "TOOL_DEFINITIONS": 70,
    "OTHER": 100,
}

# Additive (V6.2): coarse source-class taxonomy used by the Context Lab
# inspector and budget enforcement. Unknown component types fall back to OTHER.
SOURCE_CLASS_BY_COMPONENT_TYPE: Dict[str, str] = {
    "SYSTEM": "SYSTEM",
    "PROMPT": "PROMPT",
    "POLICY": "POLICY",
    "INVARIANT": "INVARIANT",
    "USER_TASK": "MANUAL",
    "CONVERSATION_HISTORY": "CONVERSATION",
    "ATTACHMENTS": "FILES",
    "PROJECT_CONTEXT": "PROJECT",
    "DATASET_CASE": "MANUAL",
    "RETRIEVED_CONTEXT": "RETRIEVAL",
    "TOOL_DEFINITIONS": "TOOL_RESULT",
    "TOOL_RESULT": "TOOL_RESULT",
    "OTHER": "OTHER",
}

# Component types that a budget policy may never drop, plus the deterministic
# global drop order for everything else. DATASET_CASE is intentionally absent
# from the drop order, so a budget policy never discards the case under test.
_BUDGET_MANDATORY_TYPES = ("SYSTEM", "PROMPT", "POLICY", "INVARIANT", "USER_TASK")
_BUDGET_DROP_ORDER = (
    "CONVERSATION_HISTORY",
    "RETRIEVED_CONTEXT",
    "ATTACHMENTS",
    "PROJECT_CONTEXT",
    "TOOL_DEFINITIONS",
    "TOOL_RESULT",
    "OTHER",
)
# (budget field name, component types the per-source budget applies to).
_BUDGET_PER_SOURCE = (
    ("history_budget", ("CONVERSATION_HISTORY",)),
    ("knowledge_budget", ("RETRIEVED_CONTEXT",)),
    ("file_budget", ("ATTACHMENTS",)),
    ("policy_budget", ("PROMPT", "POLICY", "INVARIANT")),
)


def source_class_for(component_type: Any) -> str:
    """Map a component type to its coarse source class (OTHER when unknown)."""
    return SOURCE_CLASS_BY_COMPONENT_TYPE.get(str(component_type), "OTHER")

_TIKTOKEN_ENCODING = None
_TIKTOKEN_UNAVAILABLE = False

# Per-component storage cap. Snapshots must stay within document size limits,
# so very large extracted text is truncated for storage only; the original
# ``content_hash`` is preserved so identity remains intact.
STORAGE_CONTENT_CAP = 200_000


def _get_encoding():
    """Lazily resolve the shared tiktoken encoding, caching the result."""
    global _TIKTOKEN_ENCODING, _TIKTOKEN_UNAVAILABLE
    if _TIKTOKEN_ENCODING is not None or _TIKTOKEN_UNAVAILABLE:
        return _TIKTOKEN_ENCODING
    try:
        import tiktoken

        _TIKTOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _TIKTOKEN_ENCODING = None
        _TIKTOKEN_UNAVAILABLE = True
    return _TIKTOKEN_ENCODING


def _normalize_text(value: Any) -> str:
    """Coerce an arbitrary value to a stable string (JSON for structures)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def estimate_tokens(text: str) -> Tuple[int, str]:
    """Estimate tokens for ``text`` and name the method used. Never raises."""
    normalized = _normalize_text(text)
    encoding = _get_encoding()
    if encoding is not None:
        try:
            return len(encoding.encode(normalized)), "tiktoken:cl100k_base"
        except Exception:
            pass
    return math.ceil(len(normalized) / 4), "heuristic:chars_div_4"


def hash_content(text: str) -> str:
    """Stable sha256 (hex) of the UTF-8 text."""
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()


def make_component(
    component_type: str,
    content: Any,
    *,
    ordering: int,
    source_ref: Optional[str] = None,
    token_count: Optional[int] = None,
    included: bool = True,
    excluded_reason: Optional[str] = None,
    truncated: bool = False,
    truncation_reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> dict:
    """Build one normalized context component."""
    if component_type not in CONTEXT_COMPONENT_TYPES:
        component_type = "OTHER"
    text = _normalize_text(content)
    estimated, method = estimate_tokens(text)
    return {
        "component_type": component_type,
        "ordering": ordering,
        "source_ref": source_ref,
        "content": text,
        "content_hash": hash_content(text),
        "token_count": token_count,
        "estimated_tokens": estimated,
        "estimated_method": method,
        "included": bool(included),
        "excluded_reason": excluded_reason,
        "truncated": bool(truncated),
        "truncation_reason": truncation_reason,
        "metadata": dict(metadata or {}),
    }


def build_tool_definitions(tools: List[dict]) -> List[dict]:
    """Normalize tool documents into a stable, sorted list of definitions."""
    normalized: List[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name") or tool.get("tool_name") or ""
        normalized.append({
            "name": name,
            "display_name": tool.get("display_name") or name,
            "description": tool.get("description") or "",
            "input_schema": tool.get("input_schema") or tool.get("parameters") or {},
        })
    normalized.sort(key=lambda item: item.get("name") or "")
    return normalized


def _message_role(message: Dict[str, Any]) -> str:
    return str((message or {}).get("role") or "user")


def _message_text(message: Dict[str, Any]) -> str:
    """Flatten a message into readable text, noting any content-block types."""
    if not isinstance(message, dict):
        return _normalize_text(message)

    content = message.get("content")
    if isinstance(content, str) and content:
        return content

    blocks = message.get("content_blocks")
    if blocks is None and isinstance(content, list):
        blocks = content
    if isinstance(blocks, list):
        parts: List[str] = []
        block_types: List[str] = []
        for block in blocks:
            if isinstance(block, dict):
                block_type = str(block.get("type") or "text")
                block_types.append(block_type)
                if block.get("text"):
                    parts.append(str(block.get("text")))
            elif isinstance(block, str):
                parts.append(block)
        text = "\n".join(parts)
        if block_types:
            note = "[content_blocks: " + ", ".join(dict.fromkeys(block_types)) + "]"
            text = f"{text}\n{note}" if text else note
        if text:
            return text

    return _normalize_text(content)


def _attachment_content(attachment: Dict[str, Any]) -> str:
    """Human-readable stand-in when extracted text is unavailable."""
    filename = (
        attachment.get("filename")
        or attachment.get("name")
        or attachment.get("id")
        or "attachment"
    )
    mime_type = attachment.get("mime_type") or "application/octet-stream"
    size = attachment.get("size_bytes")
    size_text = f"{size} bytes" if size is not None else "size unknown"
    if str(mime_type).startswith("image/"):
        return f"[image attachment: {filename} ({mime_type}), {size_text}]"
    return f"[attachment: {filename} \u2014 text extraction unavailable]"


def _attachment_component(attachment: Dict[str, Any], *, enabled: bool) -> dict:
    extraction_status = attachment.get("extraction_status")
    extracted = attachment.get("extracted_text")
    if extraction_status == "extracted" and extracted:
        content = extracted
    else:
        content = _attachment_content(attachment)

    metadata: Dict[str, Any] = {
        "attachment_id": attachment.get("id"),
        "filename": (
            attachment.get("filename")
            or attachment.get("name")
            or attachment.get("id")
        ),
        "mime_type": attachment.get("mime_type"),
        "size_bytes": attachment.get("size_bytes"),
        "sha256": attachment.get("sha256"),
        "extraction_status": extraction_status,
    }
    if attachment.get("image_metadata") is not None:
        metadata["image_metadata"] = attachment.get("image_metadata")

    return make_component(
        "ATTACHMENTS",
        content,
        ordering=_ORDERING["ATTACHMENTS"],
        source_ref=attachment.get("id"),
        included=enabled,
        excluded_reason=None if enabled else "deselected",
        metadata=metadata,
    )


def _normalize_component_doc(component: Dict[str, Any]) -> dict:
    """Fill structural defaults on a caller-supplied component document.

    Retrieval builds full components via :func:`make_component`; previews and
    Context Lab callers may pass partial dicts (for example ``{"component_type":
    "POLICY", "content": "..."}``). Known fields are preserved exactly; missing
    structural keys are filled from the canonical ordering and estimator so
    budget enforcement and inspection never fail on a partial component.
    """
    out = dict(component)
    component_type = out.get("component_type")
    if component_type not in CONTEXT_COMPONENT_TYPES:
        component_type = "RETRIEVED_CONTEXT"
    out["component_type"] = component_type

    text = _normalize_text(out.get("content"))
    out["content"] = text
    if out.get("ordering") is None:
        out["ordering"] = _ORDERING.get(component_type, _ORDERING["OTHER"])
    if not out.get("content_hash"):
        out["content_hash"] = hash_content(text)
    if out.get("estimated_tokens") is None:
        estimated, method = estimate_tokens(text)
        out["estimated_tokens"] = estimated
        out["estimated_method"] = method
    elif not out.get("estimated_method"):
        out["estimated_method"] = estimate_tokens(text)[1]
    out.setdefault("token_count", None)
    out.setdefault("included", True)
    out.setdefault("excluded_reason", None)
    out.setdefault("truncated", False)
    out.setdefault("truncation_reason", None)
    out.setdefault("source_ref", None)
    out.setdefault("metadata", {})
    return out


def _enforce_budget_policy(
    components: List[dict],
    excluded: List[dict],
    budget_policy: dict,
    budget: Optional[int],
) -> int:
    """Drop non-mandatory components so a budget policy is satisfied.

    Per-source budgets are enforced first by dropping the lowest-priority
    members of each source class until it is within budget; the total budget is
    then enforced with the deterministic global drop order. Mandatory component
    types are never dropped. Returns the number of components moved to
    ``excluded`` (all tagged ``excluded_reason="budget"``).
    """
    dropped: List[dict] = []
    dropped_ids: set = set()

    def _mark(component: dict) -> None:
        if id(component) in dropped_ids:
            return
        dropped_ids.add(id(component))
        dropped.append(component)

    # 1. Per-source budgets (drop lowest priority members first).
    for field, types in _BUDGET_PER_SOURCE:
        limit = budget_policy.get(field)
        if limit is None:
            continue
        limit = max(int(limit), 0)
        members = [
            component for component in components
            if component.get("component_type") in types
            and id(component) not in dropped_ids
        ]
        total = sum(int(component.get("estimated_tokens") or 0) for component in members)
        for component in reversed(members):
            if total <= limit:
                break
            if component.get("component_type") in _BUDGET_MANDATORY_TYPES:
                continue
            total -= int(component.get("estimated_tokens") or 0)
            _mark(component)

    # 2. Total budget with the global drop order.
    if budget is not None:
        total = sum(
            int(component.get("estimated_tokens") or 0)
            for component in components
            if id(component) not in dropped_ids
        )
        for component_type in _BUDGET_DROP_ORDER:
            if total <= budget:
                break
            members = [
                component for component in components
                if component.get("component_type") == component_type
                and id(component) not in dropped_ids
            ]
            for component in reversed(members):
                if total <= budget:
                    break
                total -= int(component.get("estimated_tokens") or 0)
                _mark(component)

    if dropped:
        drop_ids = {id(component) for component in dropped}
        components[:] = [
            component for component in components if id(component) not in drop_ids
        ]
        for component in dropped:
            dropped_doc = dict(component)
            dropped_doc["included"] = False
            dropped_doc["excluded_reason"] = "budget"
            dropped_doc["budget_excluded"] = True
            excluded.append(dropped_doc)

    for component in components:
        component["budget_excluded"] = False
    for component in excluded:
        component.setdefault("budget_excluded", False)
    return len(dropped)


def _canonical_component_hash(components: List[dict]) -> str:
    canonical = [
        {
            "component_type": component.get("component_type"),
            "source_ref": component.get("source_ref"),
            "content_hash": component.get("content_hash"),
            "included": bool(component.get("included", True)),
        }
        for component in components
    ]
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assemble_context(
    owner_id: str,
    *,
    system_prompt: str,
    messages: List[dict],
    attachments: Optional[List[dict]] = None,
    tool_definitions: Optional[List[dict]] = None,
    dataset_case: Optional[dict] = None,
    project_context: Optional[dict] = None,
    retrieved_context: Optional[Any] = None,
    strategy: str = "FULL",
    recent_messages_limit: int = 20,
    manual_component_types: Optional[List[str]] = None,
    context_options: Optional[Dict[str, Any]] = None,
    max_context_tokens: Optional[int] = None,
    reserved_output_tokens: int = 0,
    budget_policy: Optional[dict] = None,
) -> dict:
    """Assemble a deterministic, auditable context payload.

    Returns included/excluded components plus estimate metadata. Never raises
    for missing optional inputs; absent context is simply omitted.

    ``budget_policy`` is additive (V6.2): when omitted the assembly is exactly
    the pre-existing one. When supplied, its ``max_context_tokens`` /
    ``reserve_output_tokens`` override the inline arguments and its per-source
    budgets plus the deterministic global drop order are enforced. Mandatory
    components (SYSTEM, PROMPT, POLICY, INVARIANT, USER_TASK) are never dropped;
    if they alone exceed the budget the assembly is flagged ``over_budget`` with
    a ``mandatory_components_exceed_budget`` warning.
    """
    options = dict(context_options or {})
    normalized_strategy = strategy if strategy in CONTEXT_STRATEGIES else "FULL"
    messages = list(messages or [])
    attachments = list(attachments or [])
    tool_definitions = list(tool_definitions or [])
    warnings: List[str] = []

    components: List[dict] = []
    excluded: List[dict] = []

    def _toggle(key: str) -> bool:
        return bool(options.get(key, True))

    # --- SYSTEM ----------------------------------------------------------
    if system_prompt:
        if _toggle("system_prompt"):
            components.append(
                make_component("SYSTEM", system_prompt, ordering=_ORDERING["SYSTEM"])
            )
        else:
            excluded.append(
                make_component(
                    "SYSTEM", system_prompt, ordering=_ORDERING["SYSTEM"],
                    included=False, excluded_reason="deselected",
                )
            )

    # --- USER_TASK (the final user message) ------------------------------
    user_indices = [
        index for index, message in enumerate(messages)
        if _message_role(message) == "user"
    ]
    last_user_index = user_indices[-1] if user_indices else None
    task_text = (
        _message_text(messages[last_user_index]) if last_user_index is not None else ""
    )
    if task_text:
        components.append(
            make_component("USER_TASK", task_text, ordering=_ORDERING["USER_TASK"])
        )

    # --- CONVERSATION_HISTORY (everything before the final user task) -----
    history_messages = [
        message for index, message in enumerate(messages)
        if index != last_user_index
    ]
    history_meta: Dict[str, Any] = {}
    history_enabled = _toggle("conversation_history")
    selected_history = history_messages
    history_truncated = False
    history_truncation_reason: Optional[str] = None

    if normalized_strategy == "RECENT_MESSAGES" and recent_messages_limit is not None:
        limit = max(int(recent_messages_limit), 0)
        if len(history_messages) > limit:
            selected_history = history_messages[-limit:] if limit else []
            history_truncated = True
            history_truncation_reason = "recent_messages_limit"
            history_meta["excluded_message_count"] = len(history_messages) - len(selected_history)
            history_meta["recent_messages_limit"] = limit

    history_lines = [
        f"{_message_role(message)}: {_message_text(message)}"
        for message in selected_history
    ]
    history_content = "\n".join(history_lines)
    if history_content:
        if history_enabled:
            components.append(
                make_component(
                    "CONVERSATION_HISTORY",
                    history_content,
                    ordering=_ORDERING["CONVERSATION_HISTORY"],
                    truncated=history_truncated,
                    truncation_reason=history_truncation_reason,
                    metadata=history_meta,
                )
            )
        else:
            excluded.append(
                make_component(
                    "CONVERSATION_HISTORY",
                    history_content,
                    ordering=_ORDERING["CONVERSATION_HISTORY"],
                    included=False,
                    excluded_reason="deselected",
                    metadata=history_meta,
                )
            )

    # --- ATTACHMENTS ------------------------------------------------------
    attachments_enabled = _toggle("attachments")
    for attachment in attachments:
        component = _attachment_component(attachment, enabled=attachments_enabled)
        (components if attachments_enabled else excluded).append(component)

    # --- PROJECT_CONTEXT --------------------------------------------------
    if project_context:
        content = json.dumps(project_context, sort_keys=True, ensure_ascii=False, default=str)
        if _toggle("project_context"):
            components.append(
                make_component(
                    "PROJECT_CONTEXT", content, ordering=_ORDERING["PROJECT_CONTEXT"]
                )
            )
        else:
            excluded.append(
                make_component(
                    "PROJECT_CONTEXT", content, ordering=_ORDERING["PROJECT_CONTEXT"],
                    included=False, excluded_reason="deselected",
                )
            )

    # --- RETRIEVED_CONTEXT ------------------------------------------------
    # Knowledge retrieval components (pre-built via make_component, or raw
    # text/dicts coerced here). Empty content is skipped so hash stability
    # for callers without retrieval is preserved exactly.
    retrieved_items = retrieved_context if isinstance(retrieved_context, list) else (
        [retrieved_context] if retrieved_context else []
    )
    for item in retrieved_items:
        if item is None:
            continue
        if isinstance(item, dict) and item.get("component_type"):
            if not item.get("content"):
                continue
            components.append(_normalize_component_doc(item))
            continue
        content = item if isinstance(item, str) else _normalize_text(item)
        if not content:
            continue
        components.append(
            make_component(
                "RETRIEVED_CONTEXT", content, ordering=_ORDERING["RETRIEVED_CONTEXT"]
            )
        )

    # --- DATASET_CASE -----------------------------------------------------
    if dataset_case:
        input_text = dataset_case.get("input_text") or ""
        expected = dataset_case.get("expected_output") or ""
        content = input_text
        if expected:
            content = f"{content}\n\nExpected:\n{expected}" if content else f"Expected:\n{expected}"
        if _toggle("dataset_case"):
            components.append(
                make_component(
                    "DATASET_CASE", content, ordering=_ORDERING["DATASET_CASE"],
                    source_ref=dataset_case.get("id"),
                )
            )
        else:
            excluded.append(
                make_component(
                    "DATASET_CASE", content, ordering=_ORDERING["DATASET_CASE"],
                    source_ref=dataset_case.get("id"),
                    included=False, excluded_reason="deselected",
                )
            )

    # --- TOOL_DEFINITIONS -------------------------------------------------
    if tool_definitions:
        normalized_tools = build_tool_definitions(tool_definitions)
        content = json.dumps(normalized_tools, sort_keys=True, ensure_ascii=False, default=str)
        tool_meta = {
            "tool_count": len(normalized_tools),
            "tool_names": [item["name"] for item in normalized_tools],
        }
        if _toggle("tool_definitions"):
            components.append(
                make_component(
                    "TOOL_DEFINITIONS", content, ordering=_ORDERING["TOOL_DEFINITIONS"],
                    metadata=tool_meta,
                )
            )
        else:
            excluded.append(
                make_component(
                    "TOOL_DEFINITIONS", content, ordering=_ORDERING["TOOL_DEFINITIONS"],
                    included=False, excluded_reason="deselected", metadata=tool_meta,
                )
            )

    # --- MANUAL_SELECTION: keep only the requested component types ---------
    if normalized_strategy == "MANUAL_SELECTION" and manual_component_types is not None:
        allowed = set(manual_component_types)
        kept: List[dict] = []
        for component in components:
            if component["component_type"] in allowed:
                kept.append(component)
            else:
                demoted = dict(component)
                demoted["included"] = False
                demoted["excluded_reason"] = "not_selected"
                excluded.append(demoted)
        components = kept

    # --- Budget / AUTO_TRUNCATE ------------------------------------------
    # A budget policy may override the inline window/reserve. When no policy is
    # supplied the effective values are exactly the inline arguments, so the
    # pre-existing AUTO_TRUNCATE path is unchanged.
    effective_max = max_context_tokens
    effective_reserved = reserved_output_tokens
    if budget_policy:
        if budget_policy.get("max_context_tokens") is not None:
            effective_max = budget_policy.get("max_context_tokens")
        if budget_policy.get("reserve_output_tokens") is not None:
            effective_reserved = budget_policy.get("reserve_output_tokens")

    budget: Optional[int] = None
    if effective_max is not None:
        budget = int(effective_max) - max(int(effective_reserved or 0), 0)

    total_estimated = sum(component["estimated_tokens"] for component in components)

    if normalized_strategy == "AUTO_TRUNCATE" and budget is not None:
        for component in components:
            if component["component_type"] != "CONVERSATION_HISTORY":
                continue
            if not component.get("content"):
                continue
            lines = component["content"].split("\n")
            dropped_chars = 0
            dropped_lines = 0
            while lines and total_estimated > budget:
                removed = lines.pop(0)
                dropped_chars += len(removed) + 1
                dropped_lines += 1
                new_content = "\n".join(lines)
                new_estimate, new_method = estimate_tokens(new_content)
                total_estimated += new_estimate - component["estimated_tokens"]
                component["content"] = new_content
                component["content_hash"] = hash_content(new_content)
                component["estimated_tokens"] = new_estimate
                component["estimated_method"] = new_method
            if dropped_lines:
                component["truncated"] = True
                component["truncation_reason"] = "auto_truncate"
                metadata = dict(component.get("metadata") or {})
                metadata["excluded_content_chars"] = dropped_chars
                metadata["dropped_history_lines"] = dropped_lines
                component["metadata"] = metadata

    budget_excluded_count = 0
    if budget_policy is not None:
        budget_excluded_count = _enforce_budget_policy(
            components, excluded, budget_policy, budget
        )

    total_estimated = sum(component["estimated_tokens"] for component in components)
    over_budget = budget is not None and total_estimated > budget
    if over_budget:
        warnings.append("estimated context exceeds available input budget")
    if budget_policy is not None and over_budget:
        warnings.append("mandatory_components_exceed_budget")

    # Additive (V6.2): annotate every component with its coarse source class.
    for component in components:
        component["source_class"] = source_class_for(component.get("component_type"))
    for component in excluded:
        component["source_class"] = source_class_for(component.get("component_type"))

    known_token_count = sum(
        component["token_count"]
        for component in components
        if component["token_count"] is not None
    )
    unknown_token_component_count = sum(
        1 for component in components if component["token_count"] is None
    )

    _, fallback_method = estimate_tokens("")
    estimated_method = (
        components[0]["estimated_method"] if components else fallback_method
    )

    ordered = sorted(components, key=lambda item: item["ordering"])
    context_hash = _canonical_component_hash(ordered)

    return {
        "components": components,
        "excluded_components": excluded,
        "estimated_total_tokens": total_estimated,
        "estimated_method": estimated_method,
        "known_token_count": known_token_count,
        "unknown_token_component_count": unknown_token_component_count,
        "context_hash": context_hash,
        "strategy": normalized_strategy,
        "over_budget": over_budget,
        "warnings": warnings,
        "reserved_output_tokens": effective_reserved,
        "estimated_plus_reserved_tokens": total_estimated + int(effective_reserved or 0),
        # Additive (V6.2): budget policy telemetry. ``None`` when no policy was
        # supplied, so default callers see their pre-existing shape.
        "budget_policy_applied": budget_policy,
        "budget_excluded_count": budget_excluded_count,
    }


def _cap_component_for_storage(component: dict) -> dict:
    capped = dict(component)
    content = capped.get("content")
    if isinstance(content, str) and len(content) > STORAGE_CONTENT_CAP:
        # Keep the original content_hash: identity must reflect the full
        # source content even though only a prefix is stored.
        capped["content"] = content[:STORAGE_CONTENT_CAP]
        metadata = dict(capped.get("metadata") or {})
        metadata["content_truncated_for_storage"] = True
        capped["metadata"] = metadata
        capped["truncated"] = True
        if not capped.get("truncation_reason"):
            capped["truncation_reason"] = "storage_cap"
    return capped


async def persist_context_snapshot(
    owner_id: str,
    run_id: str,
    trace_id: Optional[str],
    project_id: Optional[str],
    assembly: dict,
    *,
    provider_input_tokens: Optional[int] = None,
) -> dict:
    """Persist a context assembly snapshot best-effort. Never raises."""
    components = [
        _cap_component_for_storage(component)
        for component in (assembly.get("components") or [])
    ]
    excluded = [
        _cap_component_for_storage(component)
        for component in (assembly.get("excluded_components") or [])
    ]

    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "project_id": project_id,
        "components": components,
        "excluded_components": excluded,
        "total_context_tokens": provider_input_tokens if provider_input_tokens is not None else None,
        "known_token_count": assembly.get("known_token_count"),
        "estimated_total_tokens": assembly.get("estimated_total_tokens"),
        "estimated_method": assembly.get("estimated_method"),
        "unknown_token_component_count": assembly.get("unknown_token_component_count"),
        "context_hash": assembly.get("context_hash"),
        "strategy": assembly.get("strategy"),
        "over_budget": assembly.get("over_budget"),
        "warnings": list(assembly.get("warnings") or []),
        "schema_version": 1,
        "created_at": now_iso(),
    }
    try:
        await repo.context_snapshots.insert(doc)
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass
    return doc


async def get_context_snapshot(owner_id: str, run_id: str) -> Optional[dict]:
    """Fetch the persisted snapshot for a run, or None."""
    try:
        return await repo.context_snapshots.find_one(
            {"owner_id": owner_id, "run_id": run_id}
        )
    except Exception:  # pragma: no cover - telemetry is best-effort
        return None


def check_context_window(
    estimated_tokens: int,
    model_context_window: Optional[int],
    reserved_output_tokens: int,
    strategy: str,
) -> dict:
    """Compare an estimate against a model's context window."""
    reserved = int(reserved_output_tokens or 0)
    if model_context_window is None:
        return {
            "context_window": None,
            "estimated_usage": estimated_tokens,
            "reserved_output_tokens": reserved,
            "available_input_tokens": None,
            "exceeds": False,
            "required_reduction": None,
            "can_auto_truncate": strategy == "AUTO_TRUNCATE",
        }
    available = int(model_context_window) - reserved
    exceeds = estimated_tokens > available
    return {
        "context_window": model_context_window,
        "estimated_usage": estimated_tokens,
        "reserved_output_tokens": reserved,
        "available_input_tokens": available,
        "exceeds": exceeds,
        "required_reduction": (estimated_tokens - available) if exceeds else 0,
        "can_auto_truncate": strategy == "AUTO_TRUNCATE",
    }


def _components_by_type(snapshot: dict) -> Dict[str, List[dict]]:
    groups: Dict[str, List[dict]] = {}
    for component in snapshot.get("components") or []:
        groups.setdefault(component.get("component_type") or "OTHER", []).append(component)
    return groups


def _type_text(components: List[dict]) -> str:
    return "\n".join(component.get("content") or "" for component in components)


def _type_hash(components: List[dict]) -> Optional[str]:
    if not components:
        return None
    canonical = [component.get("content_hash") for component in components]
    payload = json.dumps(canonical, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _line_diff(a_text: str, b_text: str) -> List[dict]:
    entries: List[dict] = []
    diff = difflib.unified_diff(
        a_text.splitlines(), b_text.splitlines(), lineterm=""
    )
    for line in diff:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            entries.append({"op": "+", "line": line[1:]})
        elif line.startswith("-"):
            entries.append({"op": "-", "line": line[1:]})
        else:
            entries.append({"op": " ", "line": line[1:] if line.startswith(" ") else line})
    return entries


def _attachment_names(snapshot: dict) -> List[str]:
    names: List[str] = []
    for component in snapshot.get("components") or []:
        if component.get("component_type") != "ATTACHMENTS":
            continue
        metadata = component.get("metadata") or {}
        name = metadata.get("filename") or component.get("source_ref")
        if name:
            names.append(name)
    return names


def _tool_names(snapshot: dict) -> List[str]:
    for component in snapshot.get("components") or []:
        if component.get("component_type") == "TOOL_DEFINITIONS":
            return list((component.get("metadata") or {}).get("tool_names") or [])
    return []


def _model_params(snapshot: dict) -> Optional[Any]:
    if "model_params" in snapshot:
        return snapshot.get("model_params")
    metadata = snapshot.get("metadata") or {}
    if "model_params" in metadata:
        return metadata.get("model_params")
    for component in snapshot.get("components") or []:
        component_metadata = component.get("metadata") or {}
        if "model_params" in component_metadata:
            return component_metadata.get("model_params")
    return None


def diff_contexts(snapshot_a: dict, snapshot_b: dict) -> dict:
    """Structured diff between two context snapshots."""
    snapshot_a = snapshot_a or {}
    snapshot_b = snapshot_b or {}

    groups_a = _components_by_type(snapshot_a)
    groups_b = _components_by_type(snapshot_b)

    component_diffs: Dict[str, dict] = {}
    for component_type in sorted(set(groups_a) | set(groups_b)):
        comps_a = groups_a.get(component_type, [])
        comps_b = groups_b.get(component_type, [])
        hash_a = _type_hash(comps_a)
        hash_b = _type_hash(comps_b)
        if not comps_a:
            status = "added"
        elif not comps_b:
            status = "removed"
        elif hash_a == hash_b:
            status = "unchanged"
        else:
            status = "changed"
        component_diffs[component_type] = {
            "status": status,
            "text_diff": _line_diff(_type_text(comps_a), _type_text(comps_b)),
            "a_hash": hash_a,
            "b_hash": hash_b,
        }

    attachments_a = _attachment_names(snapshot_a)
    attachments_b = _attachment_names(snapshot_b)
    attachments_set_a = set(attachments_a)
    attachments_set_b = set(attachments_b)
    attachments_diff = {
        "added": [name for name in attachments_b if name not in attachments_set_a],
        "removed": [name for name in attachments_a if name not in attachments_set_b],
        "common": [name for name in attachments_a if name in attachments_set_b],
    }

    tools_a = _tool_names(snapshot_a)
    tools_b = _tool_names(snapshot_b)
    tools_set_a = set(tools_a)
    tools_set_b = set(tools_b)
    tools_diff = {
        "added": [name for name in tools_b if name not in tools_set_a],
        "removed": [name for name in tools_a if name not in tools_set_b],
    }

    params_a = _model_params(snapshot_a)
    params_b = _model_params(snapshot_b)
    parameters = {"a": params_a, "b": params_b} if params_a is not None and params_b is not None else {}

    tokens_a = snapshot_a.get("estimated_total_tokens")
    tokens_b = snapshot_b.get("estimated_total_tokens")
    token_delta = (
        tokens_b - tokens_a
        if tokens_a is not None and tokens_b is not None
        else None
    )

    return {
        "context_hash_a": snapshot_a.get("context_hash"),
        "context_hash_b": snapshot_b.get("context_hash"),
        "estimated_tokens_a": tokens_a,
        "estimated_tokens_b": tokens_b,
        "token_delta": token_delta,
        "component_diffs": component_diffs,
        "attachments": attachments_diff,
        "tools": tools_diff,
        "parameters": parameters,
    }
