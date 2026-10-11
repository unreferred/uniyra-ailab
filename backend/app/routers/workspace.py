import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import repository as repo
from ..deps import get_current_user
from ..models import (
    ArtifactCreate,
    BulkDeleteConversationsRequest,
    CONTEXT_STRATEGIES,
    ContinueConversationRequest,
    ConversationCreate,
    ConversationUpdate,
    MessageRequest,
    RegenerateRequest,
    new_id,
    now_iso,
)
from ..providers import supports_stream, supports_vision
from ..services import (
    execute_model,
    execute_resumable_model,
    stream_execution,
    stream_resumable_execution,
)
from ..services.continuation import build_continuation_context, merge_continuation_segment
from ..services import attachments as attachments_service
from ..services import run_cancellation
from ..services import stream_events as se
from ..services import workspace_modes
from ..services import planning_state

router = APIRouter(tags=["workspace"])

#: Headers that keep SSE un-buffered across proxies (V4.2.3.7 streaming).
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

#: Sentinel pushed onto the drain queue once the upstream generator finishes.
_STREAM_SENTINEL = object()

#: How long the route keeps draining after a client disconnect so the upstream
#: execute_model task can persist the cancelled Run and emit RUN_CANCELLED.
_DISCONNECT_DRAIN_SECONDS = 5.0

# Fields a PATCH /conversations/{id} is allowed to change.
_CONVERSATION_PATCH_FIELDS = (
    "title",
    "system_prompt",
    "default_model_id",
    "temperature",
    "max_tokens",
    "top_p",
    "streaming",
    "enabled_tool_ids",
    "knowledge_enabled",
    "context_strategy",
    "context_options",
    "knowledge_collection_ids",
    "retrieval_options",
    "search_enabled",
    "search_provider_id",
    "fetch_enabled",
    "fetch_provider_id",
    "workspace_mode",
)

_TEXT_PER_FILE_CAP = 200_000
_TEXT_TOTAL_CAP = 400_000


@router.get("/conversations")
async def list_conversations(project_id: str, user: dict = Depends(get_current_user)):
    return await repo.conversations.list(
        {"owner_id": user["id"], "project_id": project_id}, sort_field="updated_at")


def _conversation_defaults(conv: dict) -> dict:
    """Additive conversation generation defaults (never raises on missing keys)."""
    conv = conv or {}
    return {
        "default_model_id": conv.get("default_model_id"),
        "temperature": conv.get("temperature"),
        "max_tokens": conv.get("max_tokens"),
        "top_p": conv.get("top_p"),
        "streaming": conv.get("streaming"),
        "enabled_tool_ids": list(conv.get("enabled_tool_ids") or []),
        "knowledge_enabled": conv.get("knowledge_enabled"),
        "context_strategy": conv.get("context_strategy") or "FULL",
        "context_options": dict(conv.get("context_options") or {}),
        "knowledge_collection_ids": list(conv.get("knowledge_collection_ids") or []),
        "retrieval_options": dict(conv.get("retrieval_options") or {}),
        "workspace_mode": conv.get("workspace_mode") or "chat",
    }


def _resolve_workspace_mode(conv: dict, body: Any) -> str:
    """Resolve and validate the effective workspace mode for this turn.

    Raises HTTP 400 for an invalid mode so callers validate before side effects.
    """
    mode = getattr(body, "workspace_mode", None) or (conv or {}).get("workspace_mode") or "chat"
    try:
        return workspace_modes.validate_mode(mode)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workspace mode")


async def _effective_system_prompt(owner_id, conv, body, system_prompt, conversation_id):
    """Compose the mode-scaffolded system prompt for the existing execution funnel.

    Modes never create a separate runtime; they only shape the system prompt
    before the canonical execute_model / stream_execution call. In PLAN mode the
    authoritative decision ledger is injected as a bounded decision-state block.
    """
    mode = _resolve_workspace_mode(conv, body)
    decision_block = ""
    readiness_block = ""
    if mode == "plan":
        try:
            state = await planning_state.get_state(
                owner_id, conv["project_id"], conversation_id)
            decision_block = workspace_modes.render_decision_state_block(state)
        except Exception:
            decision_block = ""
        # Bounded, additive readiness hint. Wrapped so it can NEVER break the
        # existing plan-mode behavior; CHAT mode stays byte-for-byte unchanged.
        try:
            from ..services import planning_prd
            readiness = await planning_prd.get_readiness(
                owner_id, conv["project_id"], conversation_id=conversation_id)
            readiness_block = workspace_modes.render_readiness_block(readiness)
        except Exception:
            readiness_block = ""
    composed = workspace_modes.compose_system_prompt(mode, system_prompt, decision_block)
    if mode == "plan" and readiness_block:
        composed = f"{composed}\n\n{readiness_block}"
    return composed


@router.get("/workspace-modes")
async def workspace_modes_catalog(user: dict = Depends(get_current_user)):
    return workspace_modes.catalog()


@router.post("/conversations")
async def create_conversation(body: ConversationCreate, user: dict = Depends(get_current_user)):
    project = await repo.projects.get(body.project_id, user["id"])
    if not project:
        raise HTTPException(status_code=400, detail="Project not found")
    if body.context_strategy is not None:
        _resolve_context_strategy(body.context_strategy)
    try:
        from ..services import project_manager as _project_manager
        inherited = _project_manager.conversation_fields_from_project(project, {
            "system_prompt": body.system_prompt if body.system_prompt else None,
            "default_model_id": body.default_model_id,
            "temperature": body.temperature,
            "max_tokens": body.max_tokens,
            "top_p": body.top_p,
            "streaming": body.streaming,
            "context_strategy": body.context_strategy,
            "context_options": dict(body.context_options or {}) or None,
            "enabled_tool_ids": list(body.enabled_tool_ids or []) or None,
            "knowledge_enabled": body.knowledge_enabled,
            "knowledge_collection_ids": list(body.knowledge_collection_ids or []) or None,
            "retrieval_options": dict(body.retrieval_options or {}) or None,
        })
    except Exception:
        inherited = None
    await _validate_knowledge_ids(user["id"], body.project_id, body.knowledge_collection_ids)
    if inherited is None:
        inherited = {
            "system_prompt": body.system_prompt,
            "default_model_id": body.default_model_id,
            "temperature": body.temperature,
            "max_tokens": body.max_tokens,
            "top_p": body.top_p,
            "streaming": body.streaming,
            "context_strategy": body.context_strategy or "FULL",
            "context_options": dict(body.context_options or {}),
            "enabled_tool_ids": list(body.enabled_tool_ids or []),
            "knowledge_enabled": body.knowledge_enabled,
            "knowledge_collection_ids": list(body.knowledge_collection_ids or []),
            "retrieval_options": dict(body.retrieval_options or {}),
        }
    conv = {
        "id": new_id(), "owner_id": user["id"], "project_id": body.project_id,
        "title": body.title,
        "system_prompt": inherited.get("system_prompt", body.system_prompt),
        "default_model_id": inherited.get("default_model_id"),
        "temperature": inherited.get("temperature"),
        "max_tokens": inherited.get("max_tokens"),
        "top_p": inherited.get("top_p"),
        "streaming": inherited.get("streaming"),
        "enabled_tool_ids": list(inherited.get("enabled_tool_ids") or []),
        "context_strategy": inherited.get("context_strategy") or "FULL",
        "context_options": dict(inherited.get("context_options") or {}),
        "knowledge_enabled": inherited.get("knowledge_enabled"),
        "knowledge_collection_ids": list(inherited.get("knowledge_collection_ids") or []),
        "retrieval_options": dict(inherited.get("retrieval_options") or {}),
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    try:
        conv["workspace_mode"] = workspace_modes.validate_mode(body.workspace_mode)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workspace mode")
    await _validate_knowledge_ids(user["id"], body.project_id, conv["knowledge_collection_ids"])
    await repo.conversations.insert(conv)
    return conv


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user: dict = Depends(get_current_user)):
    conv = await repo.conversations.get(conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = await repo.messages.list({"owner_id": user["id"], "conversation_id": conversation_id},
                                    sort_field="created_at", descending=False, limit=1000)
    return {**conv, "messages": msgs, "defaults": _conversation_defaults(conv)}


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: ConversationUpdate,
                              user: dict = Depends(get_current_user)):
    conv = await repo.conversations.get(conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    provided = body.model_dump(exclude_none=True)
    await _validate_knowledge_ids(user["id"], conv["project_id"], provided.get("knowledge_collection_ids"))
    updates = {key: value for key, value in provided.items() if key in _CONVERSATION_PATCH_FIELDS}
    if "workspace_mode" in updates:
        try:
            updates["workspace_mode"] = workspace_modes.validate_mode(updates["workspace_mode"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workspace mode")
    if "context_strategy" in updates:
        updates["context_strategy"] = _resolve_context_strategy(updates["context_strategy"])
    updates["updated_at"] = now_iso()
    updated = await repo.conversations.update(conversation_id, user["id"], updates)
    return updated or {**conv, **updates}


@router.post("/conversations/bulk-delete")
async def bulk_delete_conversations(
    body: BulkDeleteConversationsRequest,
    user: dict = Depends(get_current_user),
):
    owner_id = user["id"]
    # Deduplicate requested IDs safely
    requested_ids = list(dict.fromkeys(cid for cid in body.conversation_ids if cid))
    if not requested_ids:
        return {"ok": True, "deleted_count": 0, "deleted_ids": [], "unauthorized_ids": [], "missing_ids": []}

    # Query matching conversations owned by this user
    query: dict[str, Any] = {"owner_id": owner_id, "id": {"$in": requested_ids}}
    if body.project_id:
        query["project_id"] = body.project_id

    existing_convs = await repo.conversations.list(query, limit=len(requested_ids) + 10)
    owned_ids = [c["id"] for c in existing_convs if c.get("id")]
    deleted_ids_set = set(owned_ids)

    # Check for missing vs unauthorized/cross-project IDs
    # To detect IDs that exist in db but belong to other users or other projects:
    other_convs = await repo.conversations.list({"id": {"$in": requested_ids}}, limit=len(requested_ids) + 10)
    other_by_id = {c["id"]: c for c in other_convs if c.get("id")}

    unauthorized_ids = []
    missing_ids = []
    for cid in requested_ids:
        if cid in deleted_ids_set:
            continue
        doc = other_by_id.get(cid)
        if doc and (doc.get("owner_id") != owner_id or (body.project_id and doc.get("project_id") != body.project_id)):
            unauthorized_ids.append(cid)
        else:
            missing_ids.append(cid)

    if owned_ids:
        await repo.messages.delete_many({"owner_id": owner_id, "conversation_id": {"$in": owned_ids}})
        await repo.conversations.delete_many({"owner_id": owner_id, "id": {"$in": owned_ids}})

    return {
        "ok": True,
        "deleted_count": len(owned_ids),
        "deleted_ids": owned_ids,
        "unauthorized_ids": unauthorized_ids,
        "missing_ids": missing_ids,
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user: dict = Depends(get_current_user)):
    if not await repo.conversations.get(conversation_id, user["id"]):
        raise HTTPException(status_code=404, detail="Conversation not found")
    await repo.messages.delete_many({"owner_id": user["id"], "conversation_id": conversation_id})
    await repo.conversations.delete(conversation_id, user["id"])
    return {"ok": True}


async def _history(conversation_id: str, owner_id: str):
    """Replay only text turns for provider history.

    V1 limitation: only ``content`` is replayed. Historical assistant messages
    with empty content (tool-call placeholders) and attachment-only user turns
    carry no text and are skipped. Attachments/content blocks apply to the
    current turn only; they are not reconstructed for history.
    """
    msgs = await repo.messages.list({"owner_id": owner_id, "conversation_id": conversation_id},
                                    sort_field="created_at", descending=False, limit=1000)
    return [{"role": m["role"], "content": m["content"]} for m in msgs if m.get("content")]


async def _resolve_enabled_model(model_id: str, owner_id: str) -> tuple:
    """Return ``(model, provider, reason)``; reason is a human error or None."""
    model = await repo.models.get(model_id, owner_id)
    if not model:
        return None, None, "Model not found"
    if not model.get("enabled", True):
        return model, None, f"Model '{model.get('display_name') or model_id}' is disabled"
    provider = await repo.providers.get(model["provider_id"], owner_id)
    if not provider:
        return model, None, "Provider not found"
    if not provider.get("enabled", True):
        return model, provider, f"Provider '{provider.get('name')}' is disabled"
    return model, provider, None


def _resolve_context_strategy(value) -> str:
    """Validate a context strategy against the canonical enum."""
    if not value:
        return "FULL"
    if value not in CONTEXT_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Invalid context strategy: {value}")
    return value


async def _build_attachments(owner_id: str, project_id: str, attachment_ids):
    """Fetch raw attachment docs, translating AttachmentError to HTTP 4xx."""
    if not attachment_ids:
        return []
    try:
        return await attachments_service.validate_attachment_ids(
            owner_id, project_id, list(attachment_ids))
    except attachments_service.AttachmentError as err:
        raise HTTPException(
            status_code=getattr(err, "status_code", 400) or 400,
            detail=getattr(err, "message", None) or str(err),
        )


def _attachment_text_augmentation(attachment_docs) -> str:
    """Append extracted text/pdf text to the current user turn.

    Images contribute nothing here: the execution funnel injects image content
    blocks into the last user message itself.
    """
    parts = []
    total = 0
    for doc in attachment_docs or []:
        if not isinstance(doc, dict):
            continue
        if doc.get("kind") not in ("text", "pdf"):
            continue
        if doc.get("extraction_status") != "extracted":
            continue
        text = doc.get("extracted_text") or ""
        if not text:
            continue

        header = f"\n\n--- File: {doc.get('filename')} ({doc.get('mime_type')}) ---\n"
        contribution = header + text
        if len(text) > _TEXT_PER_FILE_CAP:
            contribution = (
                header + text[:_TEXT_PER_FILE_CAP]
                + "\n\n[truncated: file exceeded per-file character limit]"
            )
        if total + len(contribution) > _TEXT_TOTAL_CAP:
            remaining = _TEXT_TOTAL_CAP - total
            if remaining <= len(header):
                parts.append("\n\n[truncated: total attachment text limit reached]")
                break
            contribution = (
                contribution[:remaining]
                + "\n\n[truncated: total attachment text limit reached]"
            )
            parts.append(contribution)
            total += len(contribution)
            break
        parts.append(contribution)
        total += len(contribution)
    return "".join(parts)


def _collect_attachment_ids(body) -> list:
    """Dedupe body.attachment_ids plus attachment refs in content_blocks."""
    ids = []
    seen = set()
    for raw_id in list(getattr(body, "attachment_ids", None) or []):
        attachment_id = str(raw_id)
        if attachment_id and attachment_id not in seen:
            seen.add(attachment_id)
            ids.append(attachment_id)
    for block in getattr(body, "content_blocks", None) or []:
        attachment_id = getattr(block, "attachment_id", None)
        if attachment_id:
            attachment_id = str(attachment_id)
            if attachment_id not in seen:
                seen.add(attachment_id)
                ids.append(attachment_id)
    return ids


def _serialized_content_blocks(body, attachment_docs) -> list:
    """Serialize client blocks and auto-add refs for new attachments."""
    blocks = [block.model_dump() for block in (getattr(body, "content_blocks", None) or [])]
    referenced = {block.get("attachment_id") for block in blocks if block.get("attachment_id")}
    for doc in attachment_docs or []:
        attachment_id = doc.get("id")
        if not attachment_id or attachment_id in referenced:
            continue
        blocks.append({
            "type": "attachment_ref",
            "attachment_id": attachment_id,
            "metadata": {
                "filename": doc.get("filename"),
                "mime_type": doc.get("mime_type"),
                "kind": doc.get("kind"),
            },
        })
    return blocks


def _tool_ids_for(body, conv: dict, effective: Optional[dict] = None) -> list:
    provided = list(getattr(body, "enabled_tool_ids", None) or [])
    if provided:
        tool_ids = list(provided)
    else:
        tool_ids = list((conv or {}).get("enabled_tool_ids") or [])

    # Search auto-wiring
    search_on = getattr(body, "search_enabled", None)
    if search_on is None and effective is not None:
        search_on = effective.get("search_enabled")
    if search_on is None:
        search_on = (conv or {}).get("search_enabled")
    if search_on and "tool-search-0001" not in tool_ids:
        tool_ids.append("tool-search-0001")

    # Fetch auto-wiring
    fetch_on = getattr(body, "fetch_enabled", None)
    if fetch_on is None and effective is not None:
        fetch_on = effective.get("fetch_enabled")
    if fetch_on is None:
        fetch_on = (conv or {}).get("fetch_enabled")
    if fetch_on and "tool-fetch-0001" not in tool_ids:
        tool_ids.append("tool-fetch-0001")

    # Composite research auto-wiring: only when BOTH capabilities are on.
    # Search/fetch stay available so the model chooses between the atomic
    # tools and ``web_research``; never force it when only one is enabled.
    if search_on and fetch_on and "tool-research-0001" not in tool_ids:
        tool_ids.append("tool-research-0001")

    return tool_ids

def _request_field(body, name: str):
    """Return a request override while preserving explicit empty values."""
    fields = getattr(body, "model_fields_set", None)
    if fields is not None and name not in fields:
        return None
    return getattr(body, name, None)

    return tool_ids


async def _resolve_effective(owner_id: str, project_id: str, conv: dict, body) -> dict:
    """Resolve project -> conversation -> request config. Never raises."""
    try:
        from ..services import project_manager as _project_manager
        project = await repo.projects.get(project_id, owner_id)
        overrides = {
            "model_id": getattr(body, "model_id", None),
            "system_prompt": getattr(body, "system_prompt", None),
            "temperature": getattr(body, "temperature", None),
            "max_tokens": getattr(body, "max_tokens", None),
            "top_p": getattr(body, "top_p", None),
            "streaming": getattr(body, "streaming", None),
            "context_strategy": getattr(body, "context_strategy", None),
            "context_options": getattr(body, "context_options", None),
            "enabled_tool_ids": list(getattr(body, "enabled_tool_ids", None) or []) or None,
            "knowledge_enabled": _request_field(body, "knowledge_enabled"),
            "knowledge_collection_ids": (
                list(_request_field(body, "knowledge_collection_ids") or [])
                if _request_field(body, "knowledge_collection_ids") is not None else None
            ),
            "retrieval_options": _request_field(body, "retrieval_options"),
            "search_enabled": getattr(body, "search_enabled", None),
            "search_provider_id": getattr(body, "search_provider_id", None),
            "fetch_enabled": getattr(body, "fetch_enabled", None),
            "fetch_provider_id": getattr(body, "fetch_provider_id", None),
        }
        resolved = _project_manager.resolve_effective_config(
            project or {}, conv or {}, {k: v for k, v in overrides.items() if v is not None}
        )
        return resolved
    except Exception:
        return {"effective": {}, "sources": {}}


async def _validate_knowledge_ids(owner_id: str, project_id: str, collection_ids) -> None:
    """Reject cross-project knowledge collections with a 400."""
    for collection_id in list(collection_ids or []):
        doc = await repo.knowledge_collections.get(collection_id, owner_id)
        if not doc or doc.get("project_id") != project_id:
            raise HTTPException(
                status_code=400,
                detail=f"Knowledge collection '{collection_id}' is not in this project",
            )


def _knowledge_for_run(effective: dict, conv: dict, body) -> tuple:
    """(collection_ids, retrieval_options) honoring request > conversation > project."""
    effective = effective or {}
    conv = conv or {}
    resolved_collections = effective.get("knowledge_collection_ids")
    if isinstance(resolved_collections, list):
        collections = list(resolved_collections)
    elif _request_field(body, "knowledge_collection_ids") is not None:
        collections = list(_request_field(body, "knowledge_collection_ids") or [])
    else:
        collections = list(conv.get("knowledge_collection_ids") or [])
    retrieval = effective.get("retrieval_options")
    if not isinstance(retrieval, dict):
        request_retrieval = _request_field(body, "retrieval_options")
        retrieval = dict(request_retrieval or conv.get("retrieval_options") or {})
    return collections, retrieval


def _params(body) -> dict:
    params = {}
    for key in ("temperature", "max_tokens", "top_p", "seed"):
        value = getattr(body, key, None)
        if value is not None:
            params[key] = value
    return params


async def _vision_precheck(owner_id: str, model: dict, attachment_docs) -> None:
    """Reject image attachments for non-vision models before creating a Run."""
    if not attachment_docs:
        return
    if not attachments_service.attachment_requires_vision(attachment_docs):
        return
    provider = await repo.providers.get(model.get("provider_id"), owner_id)
    if provider is None:
        raise HTTPException(status_code=400, detail="Provider not found")
    if not supports_vision(provider, model):
        raise HTTPException(
            status_code=400,
            detail=(
                "The selected model does not support image input. Choose a "
                "vision-capable model or remove the image attachment."
            ),
        )


def _assistant_message(conversation_id: str, owner_id: str, project_id: str, model_id: str,
                       run: dict, *, content=None, extra=None) -> dict:
    """Canonical assistant message shape shared by all send/regenerate paths."""
    finish_reason = run.get("finish_reason") or run.get("provider_finish_reason")
    output_limit_exhausted = bool(run.get("output_limit_exhausted"))
    continuation_count = int(run.get("continuation_count") or 0)
    run_chain = run.get("run_chain") or ([run.get("id")] if run.get("id") else [])

    # If the generation was auto-continued and completed naturally, it is NOT output limited.
    if output_limit_exhausted:
        output_limit = True
        output_limit_reason = "Output limit reached — automatic continuation budget exhausted."
    elif continuation_count > 0 and run.get("status") == "success":
        output_limit = False
        output_limit_reason = None
    else:
        output_limit = str(finish_reason or "").lower() in {
            "length", "max_tokens", "max_output_tokens", "token_limit", "output_limit",
        } or bool(run.get("finalization_truncated"))
        output_limit_reason = finish_reason if output_limit else None

    message = {
        "id": new_id(), "owner_id": owner_id, "conversation_id": conversation_id,
        "project_id": project_id, "role": "assistant",
        "content": content if content is not None else (run.get("normalized_response") or ""),
        "run_id": run.get("id"),
        "trace_id": run.get("trace_id"),
        "model_id": model_id,
        "model_name": run.get("model_name"),
        "status": run.get("status"),
        "finish_reason": finish_reason,
        "output_limit": output_limit,
        "output_limit_reason": output_limit_reason,
        "continuation_count": continuation_count,
        "run_chain": run_chain,
        "output_limit_exhausted": output_limit_exhausted,
        "error": run.get("error_message") or run.get("error"),
        "enabled_tool_ids": list(run.get("enabled_tool_ids") or []),
        "tool_call_count": run.get("tool_call_count") or 0,
        # V4.2.3.7 partial-success / tooling telemetry (mirrored in telemetry).
        "tool_outcome": run.get("tool_outcome"),
        "tool_success_count": run.get("tool_success_count") or 0,
        "tool_failure_count": run.get("tool_failure_count") or 0,
        "fetch_timeout_count": run.get("fetch_timeout_count") or 0,
        "search_timeout_count": run.get("search_timeout_count") or 0,
        "research_call_count": run.get("research_call_count") or 0,
        "external_request_count": run.get("external_request_count") or 0,
        "forced_finalization": run.get("forced_finalization"),
        "finalization_reason": run.get("finalization_reason"),
        "reasoning_content": run.get("reasoning_content"),
        "reasoning_segments": run.get("reasoning_segments") or [],
        "reasoning_telemetry": run.get("reasoning_telemetry"),
        "token_usage": run.get("token_usage"),
        "cache_telemetry": run.get("cache_telemetry"),
        "cost_breakdown": run.get("cost_breakdown"),
        "telemetry": {
            "latency_ms": run.get("latency_ms"),
            "ttft_ms": run.get("ttft_ms"),
            "input_tokens": run.get("input_tokens"),
            "output_tokens": run.get("output_tokens"),
            "cached_tokens": run.get("cached_tokens"),
            "cache_read_tokens": run.get("cache_read_tokens") or run.get("cached_tokens"),
            "cache_write_tokens": run.get("cache_write_tokens"),
            "cache_creation_tokens": run.get("cache_creation_tokens"),
            "uncached_input_tokens": run.get("uncached_input_tokens"),
            "reasoning_tokens": run.get("reasoning_tokens"),
            "total_tokens": run.get("total_tokens"),
            "estimated_cost": run.get("estimated_cost"),
            "cost_available": run.get("cost_available"),
            "cost_breakdown": run.get("cost_breakdown"),
            "cache_telemetry": run.get("cache_telemetry"),
            "reasoning_telemetry": run.get("reasoning_telemetry"),
            "pricing_snapshot": run.get("pricing_snapshot"),
            "trace_id": run.get("trace_id"),
            "finish_reason": run.get("finish_reason"),
            # V4.2.3.7 partial-success / tooling counters.
            "tool_outcome": run.get("tool_outcome"),
            "tool_success_count": run.get("tool_success_count") or 0,
            "tool_failure_count": run.get("tool_failure_count") or 0,
            "fetch_timeout_count": run.get("fetch_timeout_count") or 0,
            "search_timeout_count": run.get("search_timeout_count") or 0,
            "forced_finalization": run.get("forced_finalization"),
            "finalization_reason": run.get("finalization_reason"),
        },
        "created_at": now_iso(),
    }
    if extra:
        message.update(extra)
    return message


def _sse_single_shot(assistant_msg: dict, run: dict) -> StreamingResponse:
    """SSE single-shot for the explicit ``streaming: false`` path.

    Presents the canonical V4.2.3.7 lifecycle so the frontend consumes one
    event vocabulary on every path: ``MODEL_STARTED`` (``streaming: false``),
    optional reasoning, ``MODEL_OUTPUT_DELTA`` and a terminal
    ``RUN_COMPLETED`` carrying the assistant message + tool outcome.
    """
    full_text = assistant_msg.get("content") or run.get("normalized_response") or ""
    output_limit = str(run.get("finish_reason") or "").lower() in {
        "length", "max_tokens", "max_output_tokens", "token_limit", "output_limit",
    } or bool(run.get("finalization_truncated"))

    async def _gen():
        yield se.sse_frame(se.event(
            se.MODEL_STARTED,
            run_id=run.get("id"), trace_id=run.get("trace_id"),
            conversation_id=run.get("conversation_id"),
            model_id=run.get("model_id"), streaming=False,
        ))
        if run.get("reasoning_content"):
            yield se.sse_frame(se.event(
                se.MODEL_REASONING_DELTA,
                run_id=run.get("id"), trace_id=run.get("trace_id"),
                conversation_id=run.get("conversation_id"),
                delta=run["reasoning_content"],
            ))
        yield se.sse_frame(se.event(
            se.MODEL_OUTPUT_DELTA,
            run_id=run.get("id"), trace_id=run.get("trace_id"),
            conversation_id=run.get("conversation_id"),
            delta=full_text, ttft_ms=run.get("ttft_ms"),
        ))
        terminal = se.event(
            se.RUN_COMPLETED,
            run_id=run.get("id"), trace_id=run.get("trace_id"),
            conversation_id=run.get("conversation_id"),
            run=run,
            full_text=full_text,
            partial=bool(run.get("tool_outcome") == "PARTIAL_TOOL_FAILURE"),
            tool_outcome=run.get("tool_outcome"),
            output_limit=output_limit,
            output_limit_reason=run.get("finish_reason") if output_limit else None,
        )
        terminal.update({
            "assistant_message": assistant_msg,
            "done": True,
        })
        yield se.sse_frame(terminal)

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)



@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, body: MessageRequest, user: dict = Depends(get_current_user)):
    conv = await repo.conversations.get(conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    mode = _resolve_workspace_mode(conv, body)
    await _validate_knowledge_ids(user["id"], conv["project_id"], body.knowledge_collection_ids)

    model, _provider, reason = await _resolve_enabled_model(body.model_id, user["id"])
    if model is None:
        raise HTTPException(status_code=400, detail="Model not found")
    if reason:
        raise HTTPException(status_code=400, detail=reason)


    attachment_ids = _collect_attachment_ids(body)
    attachment_docs = await _build_attachments(user["id"], conv["project_id"], attachment_ids)
    await _vision_precheck(user["id"], model, attachment_docs)
    augment = _attachment_text_augmentation(attachment_docs)
    task_text = (body.content or "") + augment

    # History is read before inserting the current turn so it never duplicates it.
    history = await _history(conversation_id, user["id"])

    user_msg = {
        "id": new_id(), "owner_id": user["id"], "conversation_id": conversation_id,
        "project_id": conv["project_id"], "role": "user", "content": body.content,
        "content_blocks": _serialized_content_blocks(body, attachment_docs),
        "attachment_ids": attachment_ids,
        "created_at": now_iso(),
    }
    await repo.messages.insert(user_msg)
    if mode == "plan":
        try:
            await planning_state.ingest_user_message(
                user["id"], conv["project_id"], conversation_id, user_msg)
        except Exception:
            pass
    base_system_prompt = body.system_prompt if body.system_prompt is not None else conv.get("system_prompt", "")
    system_prompt = await _effective_system_prompt(
        user["id"], conv, body, base_system_prompt, conversation_id)

    messages = history + [{"role": "user", "content": task_text if task_text else ""}]
    params = _params(body)
    if body.context_strategy is not None:
        _resolve_context_strategy(body.context_strategy)
    resolved = await _resolve_effective(user["id"], conv["project_id"], conv, body)
    effective = resolved.get("effective") or {}
    tool_ids = _tool_ids_for(body, conv, effective)
    strategy = str((effective.get("context_strategy") or "FULL"))
    knowledge_ids, retrieval_options = _knowledge_for_run(effective, conv, body)
    await _validate_knowledge_ids(user["id"], conv["project_id"], knowledge_ids)

    run = await execute_resumable_model(
        user["id"], body.model_id, system_prompt, messages, params,
        context="workspace", project_id=conv["project_id"], conversation_id=conversation_id,
        attachments=attachment_docs, enabled_tool_ids=tool_ids,
        max_tool_rounds=body.max_tool_rounds, context_strategy=strategy,
        context_options=body.context_options or None,
        knowledge_enabled=effective.get("knowledge_enabled"),
        knowledge_collection_ids=knowledge_ids,
        retrieval_options=retrieval_options or None,
        project_defaults=resolved,
        search_enabled=effective.get("search_enabled"),
        search_provider_id=effective.get("search_provider_id"),
        fetch_enabled=effective.get("fetch_enabled"),
        fetch_provider_id=effective.get("fetch_provider_id"),
    )
    conv_updates = {"updated_at": now_iso()}
    if getattr(body, "knowledge_collection_ids", None):
        conv_updates["knowledge_collection_ids"] = list(knowledge_ids)
        conv_updates["retrieval_options"] = dict(retrieval_options or {})
    if getattr(body, "search_enabled", None) is not None:
        conv_updates["search_enabled"] = bool(body.search_enabled)
    if getattr(body, "search_provider_id", None) is not None:
        conv_updates["search_provider_id"] = body.search_provider_id
    if getattr(body, "fetch_enabled", None) is not None:
        conv_updates["fetch_enabled"] = bool(body.fetch_enabled)
    if getattr(body, "fetch_provider_id", None) is not None:
        conv_updates["fetch_provider_id"] = body.fetch_provider_id
    if getattr(body, "workspace_mode", None):
        try:
            conv_updates["workspace_mode"] = workspace_modes.validate_mode(body.workspace_mode)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workspace mode")
    try:
        await repo.conversations.update(conversation_id, user["id"], conv_updates)
    except Exception:
        pass

    assistant_msg = _assistant_message(
        conversation_id, user["id"], conv["project_id"], body.model_id, run)
    await repo.messages.insert(assistant_msg)
    if mode == "plan":
        try:
            await planning_state.ingest_assistant_message(
                user["id"], conv["project_id"], conversation_id, assistant_msg)
        except Exception:
            pass
    await repo.conversations.update(conversation_id, user["id"], {"updated_at": now_iso()})

    # The funnel already marks attachments used; repeating is harmless/idempotent.
    for attachment_id in attachment_ids:
        try:
            await attachments_service.mark_attachment_used(user["id"], attachment_id, run.get("id"))
        except Exception:
            pass

    return {"user_message": user_msg, "assistant_message": assistant_msg, "run": run}


@router.post("/conversations/{conversation_id}/regenerate")
async def regenerate(conversation_id: str, body: RegenerateRequest, user: dict = Depends(get_current_user)):
    conv = await repo.conversations.get(conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    mode = _resolve_workspace_mode(conv, body)
    await _validate_knowledge_ids(user["id"], conv["project_id"], body.knowledge_collection_ids)

    model, _provider, reason = await _resolve_enabled_model(body.model_id, user["id"])
    if model is None:
        raise HTTPException(status_code=400, detail="Model not found")
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    history = await _history(conversation_id, user["id"])
    # drop trailing assistant turns to re-answer the last user prompt
    while history and history[-1]["role"] == "assistant":
        history.pop()
    if not history:
        raise HTTPException(status_code=400, detail="Nothing to regenerate")

    msgs = await repo.messages.list({"owner_id": user["id"], "conversation_id": conversation_id},
                                    sort_field="created_at", descending=False, limit=1000)
    last_user = None
    for message in msgs:
        if message.get("role") == "user":
            last_user = message
    attachment_ids = list((last_user or {}).get("attachment_ids") or [])

    attachment_docs = await _build_attachments(user["id"], conv["project_id"], attachment_ids)
    await _vision_precheck(user["id"], model, attachment_docs)
    augment = _attachment_text_augmentation(attachment_docs)
    task_text = ((last_user or {}).get("content") or "") + augment

    if history:
        history[-1]["content"] = task_text
    else:
        history.append({"role": "user", "content": task_text})

    base_system_prompt = body.system_prompt if body.system_prompt is not None else conv.get("system_prompt", "")
    system_prompt = await _effective_system_prompt(
        user["id"], conv, body, base_system_prompt, conversation_id)
    params = _params(body)
    if body.context_strategy is not None:
        _resolve_context_strategy(body.context_strategy)
    resolved = await _resolve_effective(user["id"], conv["project_id"], conv, body)
    effective = resolved.get("effective") or {}
    tool_ids = _tool_ids_for(body, conv, effective)
    strategy = str((effective.get("context_strategy") or "FULL"))
    knowledge_ids, retrieval_options = _knowledge_for_run(effective, conv, body)
    await _validate_knowledge_ids(user["id"], conv["project_id"], knowledge_ids)

    run = await execute_model(
        user["id"], body.model_id, system_prompt, history, params,
        context="workspace", project_id=conv["project_id"], conversation_id=conversation_id,
        attachments=attachment_docs, enabled_tool_ids=tool_ids,
        max_tool_rounds=body.max_tool_rounds, context_strategy=strategy,
        context_options=body.context_options or None,
        knowledge_enabled=effective.get("knowledge_enabled"),
        knowledge_collection_ids=knowledge_ids,
        retrieval_options=retrieval_options or None,
        project_defaults=resolved,
        search_enabled=effective.get("search_enabled"),
        search_provider_id=effective.get("search_provider_id"),
        fetch_enabled=effective.get("fetch_enabled"),
        fetch_provider_id=effective.get("fetch_provider_id"),
    )

    assistant_msg = _assistant_message(
        conversation_id, user["id"], conv["project_id"], body.model_id, run,
        extra={"regenerated": True})
    await repo.messages.insert(assistant_msg)
    if mode == "plan":
        try:
            await planning_state.ingest_assistant_message(
                user["id"], conv["project_id"], conversation_id, assistant_msg)
        except Exception:
            pass
    conv_updates = {"updated_at": now_iso()}
    if getattr(body, "workspace_mode", None):
        try:
            conv_updates["workspace_mode"] = workspace_modes.validate_mode(body.workspace_mode)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workspace mode")
    try:
        await repo.conversations.update(conversation_id, user["id"], conv_updates)
    except Exception:
        pass
    return {"assistant_message": assistant_msg, "run": run}


@router.post("/conversations/{conversation_id}/continue")
async def continue_conversation(
    conversation_id: str,
    body: ContinueConversationRequest,
    user: dict = Depends(get_current_user),
):
    conv = await repo.conversations.get(conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    mode = _resolve_workspace_mode(conv, body)
    await _validate_knowledge_ids(user["id"], conv["project_id"], body.knowledge_collection_ids)

    model, _provider, reason = await _resolve_enabled_model(body.model_id, user["id"])
    if model is None:
        raise HTTPException(status_code=400, detail="Model not found")
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    msgs = await repo.messages.list(
        {"owner_id": user["id"], "conversation_id": conversation_id},
        sort_field="created_at", descending=False, limit=1000,
    )
    if not msgs:
        raise HTTPException(status_code=400, detail="Nothing to continue")

    last_assistant = None
    for m in reversed(msgs):
        if m.get("role") == "assistant":
            last_assistant = m
            break

    # Determine continuation prompt instruction
    instruction = (body.continuation_prompt or "").strip() or "Please continue from where you left off."

    # Build bounded continuation history refitted with prior assistant content
    # Guarantee: no gross restarts, no instruction leaking into user turns
    prior_assistant_content = (last_assistant or {}).get("content") or ""
    history = build_continuation_context(
        original_messages=msgs,
        assembled_response=prior_assistant_content,
        continuation_instruction=instruction,
    )
    if not history:
        raise HTTPException(status_code=400, detail="Nothing to continue")

    base_system_prompt = body.system_prompt if body.system_prompt is not None else conv.get("system_prompt", "")
    system_prompt = await _effective_system_prompt(
        user["id"], conv, body, base_system_prompt, conversation_id)
    params = _params(body)
    if body.context_strategy is not None:
        _resolve_context_strategy(body.context_strategy)
    resolved = await _resolve_effective(user["id"], conv["project_id"], conv, body)
    effective = resolved.get("effective") or {}
    tool_ids = _tool_ids_for(body, conv, effective)
    strategy = str((effective.get("context_strategy") or "FULL"))
    knowledge_ids, retrieval_options = _knowledge_for_run(effective, conv, body)
    await _validate_knowledge_ids(user["id"], conv["project_id"], knowledge_ids)

    parent_run_id = (last_assistant or {}).get("run_id")

    run = await execute_resumable_model(
        user["id"], body.model_id, system_prompt, history, params,
        continuation_policy=None,
        context="workspace", project_id=conv["project_id"], conversation_id=conversation_id,
        parent_run_id=parent_run_id,
        enabled_tool_ids=tool_ids,
        max_tool_rounds=body.max_tool_rounds, context_strategy=strategy,
        context_options=body.context_options or None,
        knowledge_enabled=effective.get("knowledge_enabled"),
        knowledge_collection_ids=knowledge_ids,
        retrieval_options=retrieval_options or None,
        project_defaults=resolved,
        search_enabled=effective.get("search_enabled"),
        search_provider_id=effective.get("search_provider_id"),
        fetch_enabled=effective.get("fetch_enabled"),
        fetch_provider_id=effective.get("fetch_provider_id"),
    )

    new_segment = run.get("normalized_response") or ""
    if last_assistant:
        # Single persistent assistant message: update the existing message in-place
        target_message_id = last_assistant["id"]
        prior_content = last_assistant.get("content") or ""
        assembled_content = merge_continuation_segment(prior_content, new_segment)

        existing_segments = list(last_assistant.get("segments") or ([prior_content] if prior_content else []))
        if new_segment:
            existing_segments.append(new_segment)

        existing_chain = list(last_assistant.get("run_chain") or ([last_assistant.get("run_id")] if last_assistant.get("run_id") else []))
        if run.get("id") and run.get("id") not in existing_chain:
            existing_chain.append(run.get("id"))

        curr_count = int(last_assistant.get("continuation_count") or 0) + 1

        finish_reason = run.get("finish_reason") or run.get("provider_finish_reason")
        output_limit_exhausted = bool(run.get("output_limit_exhausted"))
        if output_limit_exhausted:
            output_limit = True
            output_limit_reason = "Output limit reached — automatic continuation budget exhausted."
        elif run.get("status") == "success" and str(finish_reason or "").lower() not in {
            "length", "max_tokens", "max_output_tokens", "token_limit", "output_limit",
        }:
            output_limit = False
            output_limit_reason = None
        else:
            output_limit = str(finish_reason or "").lower() in {
                "length", "max_tokens", "max_output_tokens", "token_limit", "output_limit",
            } or bool(run.get("finalization_truncated"))
            output_limit_reason = finish_reason if output_limit else None

        # Aggregate telemetry & tokens
        prior_telemetry = last_assistant.get("telemetry") or {}
        run_telemetry = run.get("telemetry") or {}
        total_in = (prior_telemetry.get("input_tokens") or 0) + (run.get("input_tokens") or run_telemetry.get("input_tokens") or 0)
        total_out = (prior_telemetry.get("output_tokens") or 0) + (run.get("output_tokens") or run_telemetry.get("output_tokens") or 0)
        total_tok = (prior_telemetry.get("total_tokens") or 0) + (run.get("total_tokens") or run_telemetry.get("total_tokens") or 0)
        est_cost = (prior_telemetry.get("estimated_cost") or 0.0) + (run.get("estimated_cost") or run_telemetry.get("estimated_cost") or 0.0)

        updated_telemetry = dict(prior_telemetry)
        updated_telemetry.update({
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_tok,
            "estimated_cost": est_cost,
            "finish_reason": finish_reason,
        })

        message_updates = {
            "content": assembled_content,
            "segments": existing_segments,
            "run_id": run.get("id"),
            "run_chain": existing_chain,
            "continuation_count": curr_count,
            "continued": True,
            "continued_from_message_id": target_message_id,
            "finish_reason": finish_reason,
            "output_limit": output_limit,
            "output_limit_reason": output_limit_reason,
            "output_limit_exhausted": output_limit_exhausted,
            "status": run.get("status") or last_assistant.get("status"),
            "telemetry": updated_telemetry,
            "updated_at": now_iso(),
        }
        await repo.messages.update(target_message_id, user["id"], message_updates)
        assistant_msg = {**last_assistant, **message_updates}
    else:
        assistant_msg = _assistant_message(
            conversation_id, user["id"], conv["project_id"], body.model_id, run,
            extra={"continued": True, "continued_from_message_id": None, "segments": [new_segment] if new_segment else []},
        )
        await repo.messages.insert(assistant_msg)
    if mode == "plan":
        try:
            await planning_state.ingest_assistant_message(
                user["id"], conv["project_id"], conversation_id, assistant_msg)
        except Exception:
            pass
    conv_updates = {"updated_at": now_iso()}
    if getattr(body, "workspace_mode", None):
        try:
            conv_updates["workspace_mode"] = workspace_modes.validate_mode(body.workspace_mode)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workspace mode")
    try:
        await repo.conversations.update(conversation_id, user["id"], conv_updates)
    except Exception:
        pass
    return {"assistant_message": assistant_msg, "run": run}


@router.get("/conversations/{conversation_id}/stream-support")
async def stream_support(conversation_id: str, model_id: str, user: dict = Depends(get_current_user)):
    conv = await repo.conversations.get(conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    model = await repo.models.get(model_id, user["id"])
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    if not model.get("enabled", True):
        return {"supports_stream": False, "model_id": model_id, "reason": "Model is disabled"}
    provider = await repo.providers.get(model["provider_id"], user["id"])
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    if not provider.get("enabled", True):
        return {"supports_stream": False, "model_id": model_id, "reason": "Provider is disabled"}
    return {"supports_stream": supports_stream(provider, model), "model_id": model_id}


@router.post("/conversations/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: str,
    body: MessageRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Canonical agentic SSE route (V4.2.3.7).

    Every run — including tool/search/fetch-enabled runs and providers without
    native streaming — is routed through :func:`stream_execution`, the thin
    queue-pump over the single execution loop. ``stream_execution`` already
    emits ``MODEL_STARTED`` with ``streaming: false`` and the final output for
    non-streaming providers, so no separate fallback path is needed here.

    An explicit ``streaming: false`` request still runs the plain
    ``execute_model`` path and returns a single-shot SSE response.
    """
    conv = await repo.conversations.get(conversation_id, user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    mode = _resolve_workspace_mode(conv, body)
    await _validate_knowledge_ids(user["id"], conv["project_id"], body.knowledge_collection_ids)

    model, _provider, reason = await _resolve_enabled_model(body.model_id, user["id"])
    if model is None:
        raise HTTPException(status_code=400, detail="Model not found")
    if reason:
        raise HTTPException(status_code=400, detail=reason)


    attachment_ids = _collect_attachment_ids(body)
    attachment_docs = await _build_attachments(user["id"], conv["project_id"], attachment_ids)
    await _vision_precheck(user["id"], model, attachment_docs)
    augment = _attachment_text_augmentation(attachment_docs)
    task_text = (body.content or "") + augment

    history = await _history(conversation_id, user["id"])
    user_msg = {
        "id": new_id(), "owner_id": user["id"], "conversation_id": conversation_id,
        "project_id": conv["project_id"], "role": "user", "content": body.content,
        "content_blocks": _serialized_content_blocks(body, attachment_docs),
        "attachment_ids": attachment_ids,
        "created_at": now_iso(),
    }
    await repo.messages.insert(user_msg)
    if mode == "plan":
        try:
            await planning_state.ingest_user_message(
                user["id"], conv["project_id"], conversation_id, user_msg)
        except Exception:
            pass
    base_system_prompt = body.system_prompt if body.system_prompt is not None else conv.get("system_prompt", "")
    system_prompt = await _effective_system_prompt(
        user["id"], conv, body, base_system_prompt, conversation_id)

    messages = history + [{"role": "user", "content": task_text if task_text else ""}]
    params = _params(body)
    if body.context_strategy is not None:
        _resolve_context_strategy(body.context_strategy)

    resolved = await _resolve_effective(user["id"], conv["project_id"], conv, body)
    effective = resolved.get("effective") or {}
    tool_ids = _tool_ids_for(body, conv, effective)
    strategy = str((effective.get("context_strategy") or "FULL"))
    knowledge_ids, retrieval_options = _knowledge_for_run(effective, conv, body)
    await _validate_knowledge_ids(user["id"], conv["project_id"], knowledge_ids)

    conv_updates = {"updated_at": now_iso()}
    if getattr(body, "knowledge_collection_ids", None):
        conv_updates["knowledge_collection_ids"] = list(knowledge_ids)
        conv_updates["retrieval_options"] = dict(retrieval_options or {})
    if getattr(body, "search_enabled", None) is not None:
        conv_updates["search_enabled"] = bool(body.search_enabled)
    if getattr(body, "search_provider_id", None) is not None:
        conv_updates["search_provider_id"] = body.search_provider_id
    if getattr(body, "fetch_enabled", None) is not None:
        conv_updates["fetch_enabled"] = bool(body.fetch_enabled)
    if getattr(body, "fetch_provider_id", None) is not None:
        conv_updates["fetch_provider_id"] = body.fetch_provider_id
    if getattr(body, "workspace_mode", None):
        try:
            conv_updates["workspace_mode"] = workspace_modes.validate_mode(body.workspace_mode)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid workspace mode")
    try:
        await repo.conversations.update(conversation_id, user["id"], conv_updates)
    except Exception:
        pass

    run_kwargs = dict(
        context="workspace", project_id=conv["project_id"], conversation_id=conversation_id,
        attachments=attachment_docs, enabled_tool_ids=tool_ids,
        max_tool_rounds=body.max_tool_rounds, context_strategy=strategy,
        context_options=body.context_options or None,
        knowledge_enabled=effective.get("knowledge_enabled"),
        knowledge_collection_ids=knowledge_ids,
        retrieval_options=retrieval_options or None,
        project_defaults=resolved,
        search_enabled=effective.get("search_enabled"),
        search_provider_id=effective.get("search_provider_id"),
        fetch_enabled=effective.get("fetch_enabled"),
        fetch_provider_id=effective.get("fetch_provider_id"),
    )

    # Explicit non-streaming request: run once and project the result as SSE.
    if body.streaming is False:
        run = await execute_model(
            user["id"], body.model_id, system_prompt, messages, params, **run_kwargs,
        )
        assistant_msg = _assistant_message(
            conversation_id, user["id"], conv["project_id"], body.model_id, run)
        await repo.messages.insert(assistant_msg)
        if mode == "plan":
            try:
                await planning_state.ingest_assistant_message(
                    user["id"], conv["project_id"], conversation_id, assistant_msg)
            except Exception:
                pass
        await repo.conversations.update(conversation_id, user["id"], {"updated_at": now_iso()})
        return _sse_single_shot(assistant_msg, run)

    cancel_event = asyncio.Event()
    run_kwargs["cancel_event"] = cancel_event

    async def _event_stream():
        queue: "asyncio.Queue" = asyncio.Queue()
        loop = asyncio.get_running_loop()
        run_id: Optional[str] = None

        async def _pump() -> None:
            try:
                async for evt in stream_resumable_execution(
                    user["id"], body.model_id, system_prompt, messages, params,
                    **run_kwargs,
                ):
                    await queue.put(evt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - defensive projection
                await queue.put({"_stream_error": exc})
            finally:
                await queue.put(_STREAM_SENTINEL)

        pump_task = asyncio.ensure_future(_pump())
        disconnected = False
        drain_deadline: Optional[float] = None
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if not disconnected:
                        try:
                            if await request.is_disconnected():
                                # Client is gone: stop the upstream run and keep
                                # draining (bounded) so execute_model persists the
                                # cancelled Run and emits RUN_CANCELLED.
                                disconnected = True
                                cancel_event.set()
                                drain_deadline = loop.time() + _DISCONNECT_DRAIN_SECONDS
                        except Exception:
                            pass
                    elif drain_deadline is not None and loop.time() >= drain_deadline:
                        break
                    continue

                if evt is _STREAM_SENTINEL:
                    break

                if isinstance(evt, dict) and "_stream_error" in evt:
                    exc = evt["_stream_error"]
                    if not disconnected:
                        yield se.sse_frame(se.event(
                            se.RUN_FAILED, run_id=run_id,
                            error={"code": "STREAM_ERROR", "message": str(exc),
                                   "source": "workspace"},
                        ))
                    break

                if not isinstance(evt, dict):
                    continue

                if evt.get("type") == se.RUN_STARTED:
                    run_id = evt.get("run_id") or run_id
                    if run_id:
                        run_cancellation.register(run_id, cancel_event)

                if se.is_terminal(evt):
                    run = evt.get("run") if isinstance(evt.get("run"), dict) else {}
                    full_text = evt.get("full_text") or run.get("normalized_response") or ""
                    assistant_msg = _assistant_message(
                        conversation_id, user["id"], conv["project_id"], body.model_id,
                        run, content=full_text,
                    )
                    # Persist exactly one assistant message, terminal regardless
                    # of whether the client is still connected.
                    try:
                        await repo.messages.insert(assistant_msg)
                        await repo.conversations.update(
                            conversation_id, user["id"], {"updated_at": now_iso()})
                    except Exception:
                        pass
                    if mode == "plan":
                        try:
                            await planning_state.ingest_assistant_message(
                                user["id"], conv["project_id"], conversation_id, assistant_msg)
                        except Exception:
                            pass
                    if not disconnected:
                        terminal = dict(evt)
                        terminal.update({
                            "assistant_message": assistant_msg,
                            "full_text": full_text,
                            "tool_outcome": run.get("tool_outcome"),
                            "done": True,
                        })
                        yield se.sse_frame(terminal)
                    break

                if not disconnected:
                    yield se.sse_frame(evt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak a bare error frame
            if not disconnected:
                yield se.sse_frame(se.event(
                    se.RUN_FAILED, run_id=run_id,
                    error={"code": "STREAM_ERROR", "message": str(exc),
                           "source": "workspace"},
                ))
        finally:
            if run_id:
                run_cancellation.unregister(run_id)
            if not pump_task.done():
                pump_task.cancel()
            try:
                await pump_task
            except BaseException:
                pass

    return StreamingResponse(
        _event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )
