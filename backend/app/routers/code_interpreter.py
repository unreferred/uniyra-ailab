"""Code-interpreter HTTP endpoints (Docker-sandboxed Python execution)."""
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .. import repository as repo
from ..deps import get_current_user
from ..services import code_interpreter as ci

router = APIRouter(prefix="/code-interpreter", tags=["code-interpreter"])


class ExecuteRequest(BaseModel):
    project_id: str
    code: str = Field(min_length=1)
    input_file_ids: List[str] = Field(default_factory=list)


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(exc, "status_code", None) or 400,
        detail=getattr(exc, "message", None) or str(exc),
    )


def _safe_header_filename(name: str) -> str:
    text = str(name or "output")
    return text.replace('"', "").replace("\r", "").replace("\n", "").replace("\\", "")


@router.get("/status")
async def interpreter_status(user: dict = Depends(get_current_user)):
    """Fresh-probe sandbox status (allowlisted payload, no daemon details)."""
    availability = await ci.check_availability()
    return ci.status_payload(availability)


@router.post("/execute")
async def execute(body: ExecuteRequest, user: dict = Depends(get_current_user)):
    from ..services import tracing as tracing_service

    owner_id = user["id"]
    project = await repo.projects.get(body.project_id, owner_id)
    if not project:
        raise HTTPException(status_code=400, detail="Project not found")

    trace = await tracing_service.start_trace(
        owner_id,
        "system",
        project_id=body.project_id,
        metadata={"kind": "code_interpreter"},
    )
    trace_start = time.perf_counter()
    try:
        result = await ci.execute_code(
            owner_id=owner_id,
            project_id=body.project_id,
            code=body.code,
            input_file_ids=list(body.input_file_ids or []),
            trace_id=trace.get("id"),
            timeout_override=None,
        )
    except ci.CodeInterpreterError as exc:
        await tracing_service.finish_trace(
            trace, status="failed", started_perf=trace_start,
            metadata={"kind": "code_interpreter",
                      "error": getattr(exc, "message", None) or str(exc)},
        )
        raise _http_error(exc)
    await tracing_service.finish_trace(
        trace, status="completed", started_perf=trace_start,
        metadata={"kind": "code_interpreter", "execution_id": result.get("execution_id"),
                  "execution_status": result.get("status")},
    )
    return result


@router.get("/executions")
async def list_executions(
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
):
    items = await ci.list_executions(
        user["id"], project_id=project_id, run_id=run_id, limit=limit, offset=offset
    )
    return {"items": items, "total": len(items), "limit": limit, "offset": offset}


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str, user: dict = Depends(get_current_user)):
    try:
        return await ci.get_execution(user["id"], execution_id)
    except ci.CodeInterpreterError as exc:
        raise _http_error(exc)


@router.get("/executions/{execution_id}/files/{filename:path}")
async def get_execution_file(
    execution_id: str, filename: str, user: dict = Depends(get_current_user)
):
    try:
        data, mime, warning = await ci.get_execution_file(user["id"], execution_id, filename)
    except ci.CodeInterpreterError as exc:
        raise _http_error(exc)
    headers = {
        "Content-Disposition": f'attachment; filename="{_safe_header_filename(filename.rsplit("/", 1)[-1])}"',
        "X-Content-Type-Options": "nosniff",
    }
    if warning == "active-content":
        headers["Content-Security-Policy"] = "sandbox"
    return Response(content=data, media_type=mime or "application/octet-stream", headers=headers)


@router.delete("/executions/{execution_id}")
async def delete_execution(execution_id: str, user: dict = Depends(get_current_user)):
    try:
        return await ci.delete_execution(user["id"], execution_id)
    except ci.CodeInterpreterError as exc:
        raise _http_error(exc)
