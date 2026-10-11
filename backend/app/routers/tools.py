"""Tool registry endpoints: list, inspect, update and schema lookup (Phase 3).

Extended by the Custom Tool Platform: custom tools (versioned, project- or
owner-scoped) support create / version / test / delete plus OpenAPI import,
while ALL builtin endpoints and behavior are preserved unchanged.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import repository as repo
from ..deps import get_current_user
from ..models import (
    CustomToolCreate,
    OpenAPIGenerateRequest,
    OpenAPIImportRequest,
    ToolTestRequest,
    ToolUpdate,
    now_iso,
)
from ..services import builtin_tools
from ..services import custom_tools as custom_service
from ..services import openapi_tools as openapi_service
from ..services import tools as tool_service
from ..services.custom_tools import CustomToolError
from ..services.openapi_tools import OpenAPIError
from ..services.tools import ToolError

router = APIRouter(prefix="/tools", tags=["tools"])


def _http_error(err: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(err, "status_code", None) or 400,
        detail=getattr(err, "message", None) or str(err),
    )


def _public_tool(doc: dict) -> dict:
    """Client-safe view of a raw tool document with availability metadata.

    Delegates to ``services.tools._public_tool`` — the single source of
    truth for ``available``/``availability_reason`` — so the router and the
    service can never diverge on sandbox gating (notably the
    ``code_interpreter`` cached snapshot). Falls back to a minimal local
    projection only if the service helper is unavailable.
    """
    if not isinstance(doc, dict):
        return {}
    try:
        return tool_service._public_tool(doc)
    except Exception:
        pass
    enabled = bool(doc.get("enabled", True))
    implementation = doc.get("implementation")
    executor = builtin_tools.builtin_implementation(implementation)
    if doc.get("builtin"):
        available, reason = (False, "Tool is disabled") if not enabled else (
            (True, None) if executor is not None
            else (False, "No executor available for implementation")
        )
    else:
        # Custom tools resolve per version at call time; availability here
        # reflects only the enabled flag.
        available, reason = (True, None) if enabled else (False, "Tool is disabled")
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
        "implementation": implementation,
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


@router.get("")
async def list_tools(
    project_id: Optional[str] = None,
    include_disabled: bool = True,
    user: dict = Depends(get_current_user),
):
    items = await tool_service.list_tools(
        user["id"], project_id=project_id, include_disabled=include_disabled
    )
    # Additive: surface tool_type/current_version_id on every item.
    normalized = []
    for item in items:
        entry = dict(item)
        entry.setdefault("tool_type", "function")
        entry.setdefault("current_version_id", None)
        normalized.append(entry)
    return {"items": normalized, "total": len(normalized)}


@router.get("/code-interpreter/status")
async def code_interpreter_status_alias(user: dict = Depends(get_current_user)):
    """Alias of ``GET /code-interpreter/status`` under the tools namespace.

    Returns the identical allowlisted payload
    ``{available, reason, image, runtime, limits, policy}`` via the shared
    ``code_interpreter.status_payload`` helper: a fresh sandbox probe, with
    no docker daemon details, host paths, container inventory, or secrets.
    """
    from ..services import code_interpreter as _ci

    availability = await _ci.check_availability()
    return _ci.status_payload(availability)


@router.post("")
async def create_custom_tool(
    body: CustomToolCreate, user: dict = Depends(get_current_user)
):
    try:
        tool, version = await custom_service.create_custom_tool(
            user["id"], body.model_dump()
        )
    except CustomToolError as exc:
        raise _http_error(exc)
    view = _public_tool(tool)
    view["current_version_id"] = version.get("id")
    return view


@router.post("/import-openapi")
async def import_openapi_preview(
    body: OpenAPIImportRequest, user: dict = Depends(get_current_user)
):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=404, detail="Project not found")
    spec_text = body.spec
    if body.spec_url:
        try:
            spec_text = await openapi_service.fetch_spec_from_url(body.spec_url)
        except OpenAPIError as exc:
            raise _http_error(exc)
    if not spec_text:
        raise HTTPException(status_code=422, detail="spec or spec_url is required")
    try:
        return await openapi_service.preview_import(spec_text, body.base_url_override)
    except OpenAPIError as exc:
        raise _http_error(exc)


@router.post("/generate-from-openapi")
async def generate_from_openapi(
    body: OpenAPIGenerateRequest, user: dict = Depends(get_current_user)
):
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        payloads = await openapi_service.generate_tool_payloads(
            body.spec,
            body.base_url_override,
            [selection.model_dump() for selection in body.operations],
        )
    except OpenAPIError as exc:
        raise _http_error(exc)
    created = []
    errors = []
    for payload in payloads:
        payload = dict(payload)
        payload["project_id"] = body.project_id
        # Name collisions -> auto-suffix.
        base_name = str(payload.get("name") or "tool")
        candidate = base_name
        suffix = 2
        while await repo.tools.find_one(
            {"owner_id": user["id"], "project_id": body.project_id, "name": candidate}
        ):
            candidate = f"{base_name}_{suffix}"
            suffix += 1
        payload["name"] = candidate
        try:
            tool, version = await custom_service.create_custom_tool(user["id"], payload)
        except CustomToolError as exc:
            errors.append({
                "name": candidate,
                "message": getattr(exc, "message", None) or str(exc),
            })
            continue
        view = _public_tool(tool)
        view["current_version_id"] = version.get("id")
        created.append(view)
    return {"items": created, "errors": errors}


@router.get("/versions/{version_id}")
async def get_tool_version(version_id: str, user: dict = Depends(get_current_user)):
    try:
        return await custom_service.get_version(user["id"], version_id)
    except CustomToolError as exc:
        raise _http_error(exc)


@router.get("/{tool_id}/versions")
async def list_tool_versions(tool_id: str, user: dict = Depends(get_current_user)):
    try:
        doc = await tool_service.get_tool(user["id"], tool_id)
    except ToolError as exc:
        raise _http_error(exc)
    if doc.get("builtin") or doc.get("owner_id") is None:
        return {"items": [], "builtin": True}
    try:
        items = await custom_service.list_versions(user["id"], tool_id)
    except CustomToolError as exc:
        raise _http_error(exc)
    return {"items": items, "builtin": False}


@router.get("/{tool_id}/schema")
async def get_tool_schema(tool_id: str, user: dict = Depends(get_current_user)):
    try:
        doc = await tool_service.get_tool(user["id"], tool_id)
    except ToolError as exc:
        raise _http_error(exc)
    return {
        "input_schema": doc.get("input_schema") or {},
        "output_schema": doc.get("output_schema") or {},
        "version": doc.get("version"),
        "permissions": list(doc.get("permissions") or []),
    }


@router.get("/{tool_id}")
async def get_tool(tool_id: str, user: dict = Depends(get_current_user)):
    try:
        doc = await tool_service.get_tool(user["id"], tool_id)
    except ToolError as exc:
        raise _http_error(exc)
    return _public_tool(doc)


@router.post("/{tool_id}/test")
async def test_tool(
    tool_id: str, body: ToolTestRequest, user: dict = Depends(get_current_user)
):
    try:
        return await custom_service.test_tool(
            user["id"], tool_id, dict(body.arguments or {}),
            project_id=body.project_id, code=body.code,
        )
    except CustomToolError as exc:
        raise _http_error(exc)


@router.patch("/{tool_id}")
async def update_tool(
    tool_id: str, request: Request, user: dict = Depends(get_current_user)
):
    try:
        doc = await tool_service.get_tool(user["id"], tool_id)
    except ToolError as exc:
        raise _http_error(exc)

    try:
        raw_body = await request.json()
    except Exception:
        raw_body = {}
    if not isinstance(raw_body, dict):
        raw_body = {}

    # Custom (non-builtin) tools: metadata via ToolUpdate + optional version.
    if not doc.get("builtin") and (doc.get("owner_id") or None) is not None:
        try:
            parsed = ToolUpdate.model_validate(raw_body)
        except Exception:
            parsed = ToolUpdate()
        data = parsed.model_dump(exclude_unset=True)
        patch: dict = {}
        for key in ("enabled", "description", "permissions", "tags"):
            if key in data:
                patch[key] = data[key]
        # Detect a version payload via the raw dict (ToolUpdate ignores extras).
        version_payload = raw_body.get("version")
        if version_payload is not None:
            patch["version"] = version_payload
        # Also allow display_name for custom tools (ToolUpdate omits it).
        if raw_body.get("display_name") is not None:
            patch["display_name"] = raw_body["display_name"]
        try:
            updated, _ = await custom_service.update_custom_tool(
                user["id"], tool_id, patch
            )
        except CustomToolError as exc:
            raise _http_error(exc)
        return _public_tool(updated)

    # Builtin path: EXACTLY as before (enabled/description/permissions/tags).
    try:
        body = ToolUpdate.model_validate(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    data = body.model_dump(exclude_unset=True)
    patch = {}
    for key in ("enabled", "description", "permissions", "tags"):
        if key in data:
            patch[key] = data[key]
    patch["updated_at"] = now_iso()

    # Global builtins are stored with ``owner_id`` null; Repository.update with
    # a None owner scopes the write to that null owner, so they update in place.
    updated = await repo.tools.update(tool_id, doc.get("owner_id"), patch)
    return _public_tool(updated or {**doc, **patch})


@router.delete("/{tool_id}")
async def delete_tool(tool_id: str, user: dict = Depends(get_current_user)):
    try:
        doc = await tool_service.get_tool(user["id"], tool_id)
    except ToolError as exc:
        raise _http_error(exc)
    if doc.get("builtin") or (doc.get("owner_id") or None) is None:
        raise HTTPException(status_code=400, detail="Builtin tools cannot be deleted")
    try:
        return await custom_service.delete_custom_tool(user["id"], tool_id)
    except CustomToolError as exc:
        raise _http_error(exc)
