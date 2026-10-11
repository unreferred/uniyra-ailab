"""Tool valve endpoints (V4.2.3.2).

Tool configuration belongs with the tool: one Configure surface driven by a
Valve schema. SECRET valves are written to the encrypted vault on save and are
never returned as plaintext (they project to ``{"stored": bool}``).
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_current_user
from ..services import tools as tool_service
from ..services.tool_valves import (
    ValveError,
    list_tool_valves,
    resolve_tool,
    save_tool,
    test_connection,
)

router = APIRouter(prefix="/tool-valves", tags=["tool-valves"])


class ToolValvePatch(BaseModel):
    enabled: Optional[bool] = None
    project_id: Optional[str] = None
    provider_id: Optional[str] = None
    values: Optional[Dict[str, Any]] = None
    provider_values: Optional[Dict[str, Any]] = None


@router.get("")
async def list_all(project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    items = await list_tool_valves(user["id"], project_id)
    return {"items": items, "total": len(items)}


@router.get("/{tool_id}")
async def get_one(tool_id: str, project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    try:
        tool = await tool_service.get_tool(user["id"], tool_id)
    except tool_service.ToolError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return await resolve_tool(user["id"], tool, project_id)


@router.put("/{tool_id}")
async def put_one(tool_id: str, body: ToolValvePatch, user: dict = Depends(get_current_user)):
    try:
        tool = await tool_service.get_tool(user["id"], tool_id)
        payload = body.model_dump(exclude_unset=True)
        project_id = payload.pop("project_id", None)
        return await save_tool(user["id"], tool, payload, project_id)
    except tool_service.ToolError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except ValveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/{tool_id}/test")
async def test_one(tool_id: str, project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    try:
        tool = await tool_service.get_tool(user["id"], tool_id)
    except tool_service.ToolError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return await test_connection(user["id"], tool, project_id)
