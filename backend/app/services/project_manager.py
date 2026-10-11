"""Project workspace orchestration: defaults, overview, search and activity.

Composition-only service over the Repository layer. Import-cycle rule: this
module never imports the knowledge, custom-tool or code-interpreter services;
counts are computed with direct repository queries. Routers translate
:exc:`ProjectError` into HTTP responses; messages are client-safe.
"""
import re
from typing import Any, Dict, List, Optional

from .. import config
from .. import repository as repo
from ..models import new_id, now_iso


class ProjectError(ValueError):
    """Client-safe project failure carrying an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def require_project(owner_id: str, project_id: str) -> dict:
    """Return the project document or raise ProjectError 404."""
    project = await repo.projects.get(project_id, owner_id)
    if not project:
        raise ProjectError("Project not found", 404)
    return project


def resolve_project_defaults(project: dict) -> dict:
    """Return every project default key with sensible fallbacks.

    Missing values normalise to ``None`` (scalars), ``""`` (system
    instruction), ``"FULL"`` (context strategy), ``[]`` (id lists) or ``{}``
    (option dicts) so callers can rely on a complete shape.
    """
    project = project or {}
    return {
        "default_model_id": project.get("default_model_id"),
        "default_system_instruction": project.get("default_system_instruction") or "",
        "default_temperature": project.get("default_temperature"),
        "default_max_tokens": project.get("default_max_tokens"),
        "default_top_p": project.get("default_top_p"),
        "default_streaming": project.get("default_streaming"),
        "default_context_strategy": project.get("default_context_strategy") or "FULL",
        "default_context_options": dict(project.get("default_context_options") or {}),
        "default_enabled_tool_ids": list(project.get("default_enabled_tool_ids") or []),
        "default_knowledge_enabled": (
            project.get("default_knowledge_enabled")
            if project.get("default_knowledge_enabled") is not None
            else bool(getattr(config, "KNOWLEDGE_ENABLED", True))
        ),
        "default_knowledge_collection_ids": list(
            project.get("default_knowledge_collection_ids") or []
        ),
        "default_retrieval_options": dict(project.get("default_retrieval_options") or {}),
        "default_search_enabled": bool(project.get("default_search_enabled", False)),
        "default_search_provider_id": project.get("default_search_provider_id"),
        "default_fetch_enabled": bool(project.get("default_fetch_enabled", False)),
        "default_fetch_provider_id": project.get("default_fetch_provider_id"),
    }


# Effective config keys exposed to the execution pipeline.
_EFFECTIVE_KEYS = (
    "model_id",
    "system_prompt",
    "temperature",
    "max_tokens",
    "top_p",
    "streaming",
    "context_strategy",
    "context_options",
    "enabled_tool_ids",
    "knowledge_enabled",
    "knowledge_collection_ids",
    "retrieval_options",
    "search_enabled",
    "search_provider_id",
    "fetch_enabled",
    "fetch_provider_id",
)

# Effective key -> project default field.
_PROJECT_FIELD_FOR_KEY = {
    "model_id": "default_model_id",
    "system_prompt": "default_system_instruction",
    "temperature": "default_temperature",
    "max_tokens": "default_max_tokens",
    "top_p": "default_top_p",
    "streaming": "default_streaming",
    "context_strategy": "default_context_strategy",
    "context_options": "default_context_options",
    "enabled_tool_ids": "default_enabled_tool_ids",
    "knowledge_enabled": "default_knowledge_enabled",
    "knowledge_collection_ids": "default_knowledge_collection_ids",
    "retrieval_options": "default_retrieval_options",
    "search_enabled": "default_search_enabled",
    "search_provider_id": "default_search_provider_id",
    "fetch_enabled": "default_fetch_enabled",
    "fetch_provider_id": "default_fetch_provider_id",
}

# Effective key -> conversation document field.
_CONVERSATION_FIELD_FOR_KEY = {
    "model_id": "default_model_id",
    "system_prompt": "system_prompt",
    "temperature": "temperature",
    "max_tokens": "max_tokens",
    "top_p": "top_p",
    "streaming": "streaming",
    "context_strategy": "context_strategy",
    "context_options": "context_options",
    "enabled_tool_ids": "enabled_tool_ids",
    "knowledge_enabled": "knowledge_enabled",
    "knowledge_collection_ids": "knowledge_collection_ids",
    "retrieval_options": "retrieval_options",
    "search_enabled": "search_enabled",
    "search_provider_id": "search_provider_id",
    "fetch_enabled": "fetch_enabled",
    "fetch_provider_id": "fetch_provider_id",
}

# Last-resort defaults when no layer provides a value.
_EMPTY_DEFAULTS = {
    "model_id": None,
    "system_prompt": "",
    "temperature": None,
    "max_tokens": None,
    "top_p": None,
    "streaming": None,
    "context_strategy": "FULL",
    "context_options": {},
    "enabled_tool_ids": [],
    "knowledge_enabled": True,
    "knowledge_collection_ids": [],
    "retrieval_options": {},
    "search_enabled": False,
    "search_provider_id": None,
    "fetch_enabled": False,
    "fetch_provider_id": None,
}


def _is_unset(value: Any) -> bool:
    """True when a value should fall through to the next inheritance layer.

    ``None`` and empties (``""``, ``[]``, ``{}``) are unset; ``False`` and
    ``0`` are explicit values and therefore set.
    """
    if value is None:
        return True
    if isinstance(value, (str, list, tuple, dict)) and len(value) == 0:
        return True
    return False


def _copy_value(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        return list(value)
    return value


def resolve_effective_config(
    project: dict,
    conversation: Optional[dict],
    request_overrides: Optional[dict],
) -> Dict[str, Dict[str, Any]]:
    """Resolve effective generation config with per-key source attribution.

    Inheritance order is project defaults -> conversation overrides -> request
    overrides. ``None``/empty values are treated as unset and fall through, so
    an empty list on the conversation inherits the project default instead of
    clearing it. Request-level ``enabled_tool_ids`` /
    ``knowledge_collection_ids`` are explicit per-request selections: when the
    key is present with a non-``None`` value (even an empty list, which
    clears) they replace the inherited value instead of merging with it.
    """
    project = project or {}
    conversation = conversation or {}
    overrides = request_overrides or {}
    effective: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    for key in _EFFECTIVE_KEYS:
        request_present = key in overrides and overrides.get(key) is not None
        if request_present and (
            key in ("enabled_tool_ids", "knowledge_collection_ids")
            or not _is_unset(overrides.get(key))
        ):
            effective[key] = _copy_value(overrides.get(key))
            sources[key] = "request"
            continue
        conv_value = conversation.get(_CONVERSATION_FIELD_FOR_KEY[key])
        if not _is_unset(conv_value):
            effective[key] = _copy_value(conv_value)
            sources[key] = "conversation"
            continue
        proj_value = project.get(_PROJECT_FIELD_FOR_KEY[key])
        if not _is_unset(proj_value):
            effective[key] = _copy_value(proj_value)
            sources[key] = "project"
            continue
        effective[key] = _copy_value(_EMPTY_DEFAULTS[key])
        sources[key] = "default"
    return {"effective": effective, "sources": sources}


def conversation_fields_from_project(
    project: dict, overrides: Optional[dict]
) -> Dict[str, Any]:
    """Build conversation-document defaults from project defaults.

    ``overrides`` uses conversation field names; a ``None`` value leaves the
    inherited project default in place.
    """
    defaults = resolve_project_defaults(project or {})
    fields: Dict[str, Any] = {
        "system_prompt": defaults["default_system_instruction"],
        "default_model_id": defaults["default_model_id"],
        "temperature": defaults["default_temperature"],
        "max_tokens": defaults["default_max_tokens"],
        "top_p": defaults["default_top_p"],
        "streaming": defaults["default_streaming"],
        "context_strategy": defaults["default_context_strategy"],
        "context_options": dict(defaults["default_context_options"]),
        "enabled_tool_ids": list(defaults["default_enabled_tool_ids"]),
        "knowledge_enabled": defaults["default_knowledge_enabled"],
        "knowledge_collection_ids": list(defaults["default_knowledge_collection_ids"]),
        "retrieval_options": dict(defaults["default_retrieval_options"]),
    }
    for key, value in (overrides or {}).items():
        if key in fields and value is not None:
            fields[key] = value
    return fields


async def project_overview(owner_id: str, project_id: str) -> dict:
    """Assemble the project overview from real stored data only.

    Counts are direct repository queries. ``known_cost`` is the best-effort
    sum of ``usage_events.estimated_cost`` where ``cost_available`` is true,
    else ``None`` (any failure degrades to ``None``). No metrics are
    fabricated.
    """
    project = await require_project(owner_id, project_id)
    scope: Dict[str, Any] = {"owner_id": owner_id, "project_id": project_id}
    counts = {
        "conversations": await repo.conversations.count(dict(scope)),
        "experiments": await repo.experiments.count(dict(scope)),
        "runs": await repo.runs.count(dict(scope)),
        "findings": await repo.findings.count(dict(scope)),
        "files": await repo.project_files.count({**scope, "status": "active"}),
        "knowledge_collections": await repo.knowledge_collections.count(
            {**scope, "status": "active"}
        ),
        "prompts": await repo.prompts.count(dict(scope)),
        "datasets": await repo.datasets.count(dict(scope)),
        "artifacts": await repo.artifacts.count(dict(scope)),
        "tool_calls": await repo.tool_calls.count(dict(scope)),
        "failures": await repo.failures.count(dict(scope)),
        "code_executions": await repo.code_executions.count(dict(scope)),
    }
    known_cost: Optional[float] = None
    try:
        events = await repo.usage_events.list(dict(scope), limit=5000)
        available = [
            event
            for event in events
            if event.get("cost_available") is True
            and event.get("estimated_cost") is not None
        ]
        if available:
            known_cost = sum(float(event.get("estimated_cost") or 0.0) for event in available)
    except Exception:
        known_cost = None
    recent_activity = await repo.project_activity.list(dict(scope), limit=20)
    recent_conversations = await repo.conversations.list(dict(scope), limit=5)
    recent_experiments = await repo.experiments.list(dict(scope), limit=5)
    return {
        "project": project,
        "counts": counts,
        "known_cost": known_cost,
        "recent_activity": recent_activity,
        "recent_conversations": recent_conversations,
        "recent_experiments": recent_experiments,
    }


def _snippet(value: Any, limit: int = 160) -> str:
    """Collapse whitespace and truncate a value into a search snippet."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


async def project_search(
    owner_id: str, project_id: str, query: str, limit: int = 25
) -> Dict[str, List[Dict[str, Any]]]:
    """Case-insensitive grouped search across project-scoped collections.

    Regex metacharacters in ``query`` are escaped. Each group is capped at
    ``limit`` items shaped as ``{id, title, snippet}``; a blank query returns
    empty groups without scanning. Search spans all statuses.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {
        key: [] for key in (
            "conversations", "messages", "files", "knowledge",
            "experiments", "findings", "artifacts",
        )
    }
    text = (query or "").strip()
    if not text:
        return groups
    try:
        per_group = max(1, min(int(limit or 25), 50))
    except (TypeError, ValueError):
        per_group = 25
    rx = {"$regex": re.escape(text), "$options": "i"}
    scope: Dict[str, Any] = {"owner_id": owner_id, "project_id": project_id}

    conversations = await repo.conversations.list(
        {**scope, "$or": [{"title": rx}, {"system_prompt": rx}]}, limit=per_group
    )
    groups["conversations"] = [
        {
            "id": conv.get("id"),
            "title": conv.get("title") or "Untitled conversation",
            "snippet": _snippet(conv.get("system_prompt") or conv.get("title")),
        }
        for conv in conversations
    ]

    conv_docs = await repo.conversations.list(dict(scope), limit=1000)
    conv_titles = {
        doc.get("id"): (doc.get("title") or "Untitled conversation") for doc in conv_docs
    }
    if conv_titles:
        messages = await repo.messages.list(
            {
                "owner_id": owner_id,
                "conversation_id": {"$in": list(conv_titles)},
                "content": rx,
            },
            limit=per_group,
        )
        groups["messages"] = [
            {
                "id": msg.get("id"),
                "title": "Message in '%s'" % conv_titles.get(msg.get("conversation_id"), "conversation"),
                "snippet": _snippet(msg.get("content")),
            }
            for msg in messages
        ]

    files = await repo.project_files.list(
        {**scope, "$or": [{"display_name": rx}, {"description": rx}]},
        limit=per_group,
    )
    groups["files"] = [
        {
            "id": doc.get("id"),
            "title": doc.get("display_name") or "Untitled file",
            "snippet": _snippet(doc.get("description")),
        }
        for doc in files
    ]

    collections = await repo.knowledge_collections.list(
        {**scope, "$or": [{"name": rx}, {"description": rx}]}, limit=per_group
    )
    groups["knowledge"] = [
        {
            "id": doc.get("id"),
            "title": doc.get("name") or "Untitled collection",
            "snippet": _snippet(doc.get("description")),
        }
        for doc in collections
    ]

    experiments = await repo.experiments.list(
        {**scope, "$or": [{"title": rx}, {"objective": rx}, {"hypothesis": rx}]},
        limit=per_group,
    )
    groups["experiments"] = [
        {
            "id": doc.get("id"),
            "title": doc.get("title") or "Untitled experiment",
            "snippet": _snippet(doc.get("objective") or doc.get("hypothesis")),
        }
        for doc in experiments
    ]

    findings = await repo.findings.list(
        {**scope, "$or": [{"title": rx}, {"statement": rx}, {"description": rx}]},
        limit=per_group,
    )
    groups["findings"] = [
        {
            "id": doc.get("id"),
            "title": doc.get("title") or "Untitled finding",
            "snippet": _snippet(doc.get("statement") or doc.get("description")),
        }
        for doc in findings
    ]

    artifacts = await repo.artifacts.list(
        {**scope, "$or": [{"title": rx}, {"content": rx}]}, limit=per_group
    )
    groups["artifacts"] = [
        {
            "id": doc.get("id"),
            "title": doc.get("title") or "Untitled artifact",
            "snippet": _snippet(doc.get("content")),
        }
        for doc in artifacts
    ]
    return groups


async def record_activity(
    owner_id: str,
    project_id: str,
    activity_type: str,
    summary: str,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
    actor: str = "user",
) -> None:
    """Best-effort append to project_activity; never raises."""
    try:
        await repo.project_activity.insert(
            {
                "id": new_id(),
                "owner_id": owner_id,
                "project_id": project_id,
                "activity_type": activity_type,
                "summary": summary or "",
                "ref_type": ref_type,
                "ref_id": ref_id,
                "actor": actor or "user",
                "created_at": now_iso(),
            }
        )
    except Exception:
        pass


async def list_activity(
    owner_id: str, project_id: str, limit: int = 50, offset: int = 0
) -> List[Dict[str, Any]]:
    """Return the project's activity feed, newest first."""
    try:
        safe_limit = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        safe_limit = 50
    try:
        safe_offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        safe_offset = 0
    return await repo.project_activity.list(
        {"owner_id": owner_id, "project_id": project_id},
        limit=safe_limit,
        offset=safe_offset,
    )
