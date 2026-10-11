from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from .. import config
from .. import repository as repo
from ..database import db
from ..deps import get_current_user
from ..models import (
    CONTEXT_STRATEGIES,
    Project,
    ProjectCreate,
    ProjectFileUpdate,
    ProjectUpdate,
    now_iso,
)
from ..services import project_files as project_files_service
from ..services import project_manager as project_manager_service
from ..services import tools as tool_service
from ..services.project_files import ProjectError as ProjectFileError
from ..services.project_manager import ProjectError as ProjectManagerError
from ..services.tools import ToolError

router = APIRouter(prefix="/projects", tags=["projects"])

# Collections removed with their project. Some are future/planned and may not
# exist yet; deletion is guarded by db.list_collection_names() so startup and
# deletion never fail on an absent collection.
_PROJECT_SCOPED_COLLECTIONS = [
    "experiments", "conversations", "runs", "evaluations", "artifacts",
    "councils", "research_sessions", "findings", "datasets", "dataset_cases",
    "usage_events", "reveal_events",
    "dataset_versions", "dataset_version_cases",
    "prompts", "prompt_versions",
    "experiment_configs", "benchmark_plans",
    "benchmark_runs", "benchmark_results", "benchmark_cases",
    # Phase 4: workspace platform collections.
    "project_files", "knowledge_collections", "knowledge_documents",
    "knowledge_chunks", "tools", "tool_versions", "tool_credentials",
    "code_executions", "project_activity",
]

# Fields PATCH /projects/{id}/settings is allowed to change: project identity
# fields plus every project default consumed by conversation inheritance.
_SETTINGS_FIELDS = (
    "name",
    "description",
    "tags",
    "status",
    "default_model_id",
    "default_system_instruction",
    "default_temperature",
    "default_max_tokens",
    "default_top_p",
    "default_streaming",
    "default_context_strategy",
    "default_context_options",
    "default_enabled_tool_ids",
    "default_knowledge_enabled",
    "default_knowledge_collection_ids",
    "default_retrieval_options",
    "default_search_enabled",
    "default_search_provider_id",
    "default_fetch_enabled",
    "default_fetch_provider_id",
)


def _project_error(exc: ValueError) -> HTTPException:
    """Translate a service ProjectError into an HTTP error (safe message)."""
    return HTTPException(
        status_code=getattr(exc, "status_code", None) or 400,
        detail=getattr(exc, "message", None) or str(exc),
    )


async def _require_project(project_id: str, owner_id: str) -> dict:
    """Return the owned project or raise 404 (no cross-owner leakage)."""
    project = await repo.projects.get(project_id, owner_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _require_project_file(project_id: str, file_id: str, owner_id: str) -> dict:
    """Return the owned file bound to this project or raise 404."""
    file_doc = await project_files_service.get_project_file(owner_id, file_id)
    if not file_doc or file_doc.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="File not found")
    return file_doc


def _parse_tags_field(tags: Optional[str]) -> List[str]:
    """Split a comma-separated form tags field into a clean list."""
    if not tags:
        return []
    return [item.strip() for item in str(tags).split(",") if item.strip()]


@router.get("")
async def list_projects(user: dict = Depends(get_current_user)):
    return await repo.projects.list({"owner_id": user["id"]}, sort_field="updated_at")


@router.post("")
async def create_project(body: ProjectCreate, user: dict = Depends(get_current_user)):
    project = Project(owner_id=user["id"], **body.model_dump()).model_dump()
    await repo.projects.insert(project)
    return project


@router.get("/{project_id}")
async def get_project(project_id: str, user: dict = Depends(get_current_user)):
    project = await repo.projects.get(project_id, user["id"])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    stats = {
        "experiments": await repo.experiments.count({"owner_id": user["id"], "project_id": project_id}),
        "conversations": await repo.conversations.count({"owner_id": user["id"], "project_id": project_id}),
        "artifacts": await repo.artifacts.count({"owner_id": user["id"], "project_id": project_id}),
        "runs": await repo.runs.count({"owner_id": user["id"], "project_id": project_id}),
        "files": await repo.project_files.count(
            {"owner_id": user["id"], "project_id": project_id, "status": "active"}),
        "knowledge_collections": await repo.knowledge_collections.count(
            {"owner_id": user["id"], "project_id": project_id, "status": "active"}),
        "findings": await repo.findings.count({"owner_id": user["id"], "project_id": project_id}),
    }
    return {**project, "stats": stats}


@router.patch("/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, user: dict = Depends(get_current_user)):
    if not await repo.projects.get(project_id, user["id"]):
        raise HTTPException(status_code=404, detail="Project not found")
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    patch["updated_at"] = now_iso()
    return await repo.projects.update(project_id, user["id"], patch)


@router.delete("/{project_id}")
async def delete_project(project_id: str, user: dict = Depends(get_current_user)):
    if not await repo.projects.get(project_id, user["id"]):
        raise HTTPException(status_code=404, detail="Project not found")
    owner_id = user["id"]
    scope: dict[str, Any] = {"owner_id": owner_id, "project_id": project_id}

    # Messages are conversation-scoped, not project-scoped: resolve the
    # project's conversations first so no orphan messages are left behind.
    conversation_ids = await db.conversations.distinct("id", scope)
    if conversation_ids:
        await repo.messages.delete_many({"owner_id": owner_id, "conversation_id": {"$in": conversation_ids}})

    existing = set(await db.list_collection_names())
    doomed_executions: list = []
    if "code_executions" in existing:
        try:
            doomed_executions = await repo.code_executions.list(dict(scope), limit=10000)
        except Exception:
            doomed_executions = []
    for name in _PROJECT_SCOPED_COLLECTIONS:
        if name in existing:
            await db[name].delete_many(dict(scope))

    # Code-interpreter sandbox outputs: the collection rows are deleted above;
    # durable per-execution output dirs are removed best-effort so no orphan
    # bytes survive. Runs/traces keep their provenance snapshots; project_files
    # docs are deleted above while raw attachment bytes keep current behavior
    # (attachments collection is intentionally NOT in the cascade).
    if "code_executions" in existing:
        try:
            from ..services import code_interpreter as _ci
            outputs_root = _ci._outputs_root()
            for execution in doomed_executions:
                execution_id = (execution or {}).get("id")
                if not execution_id:
                    continue
                try:
                    target = _ci._ensure_within_root(
                        outputs_root / str(execution_id), outputs_root
                    )
                    import shutil as _shutil
                    _shutil.rmtree(str(target), ignore_errors=True)
                except Exception:
                    continue
        except Exception:
            pass

    await repo.projects.delete(project_id, owner_id)
    return {"ok": True}


# ---------------------------------------------------------------- overview / search / activity
@router.get("/{project_id}/overview")
async def get_project_overview(project_id: str, user: dict = Depends(get_current_user)):
    """Project overview assembled from real stored data only (no fabricated metrics)."""
    try:
        return await project_manager_service.project_overview(user["id"], project_id)
    except ProjectManagerError as exc:
        raise _project_error(exc)


@router.patch("/{project_id}/settings")
async def update_project_settings(
    project_id: str, body: Dict[str, Any], user: dict = Depends(get_current_user)
):
    """Update project identity fields and conversation defaults.

    Only affects future conversations created from these defaults; existing
    conversations and runs are never mutated here.
    """
    owner_id = user["id"]
    await _require_project(project_id, owner_id)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Settings body must be an object")
    unknown = [key for key in body if key not in _SETTINGS_FIELDS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown settings fields: {', '.join(unknown)}")
    patch = {key: body[key] for key in _SETTINGS_FIELDS if key in body}

    model_id = patch.get("default_model_id")
    if model_id is not None:
        if not await repo.models.get(model_id, owner_id):
            raise HTTPException(status_code=400, detail="Default model not found")

    tool_ids = patch.get("default_enabled_tool_ids")
    if tool_ids is not None:
        if not isinstance(tool_ids, list):
            raise HTTPException(status_code=400, detail="default_enabled_tool_ids must be a list")
        for tool_id in tool_ids:
            try:
                doc = await tool_service.get_tool(owner_id, tool_id)
            except ToolError:
                raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_id}")
            if doc.get("project_id") and doc.get("project_id") != project_id:
                raise HTTPException(
                    status_code=400, detail=f"Tool '{tool_id}' belongs to another project")

    collection_ids = patch.get("default_knowledge_collection_ids")
    if collection_ids is not None:
        if not isinstance(collection_ids, list):
            raise HTTPException(
                status_code=400, detail="default_knowledge_collection_ids must be a list")
        for collection_id in collection_ids:
            doc = await repo.knowledge_collections.get(collection_id, owner_id)
            if not doc or doc.get("project_id") != project_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Knowledge collection '{collection_id}' is not in this project")

    strategy = patch.get("default_context_strategy")
    if strategy is not None and strategy not in CONTEXT_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Invalid context strategy: {strategy}")

    for flag in ("default_search_enabled", "default_fetch_enabled"):
        if flag in patch and patch[flag] is not None and not isinstance(patch[flag], bool):
            raise HTTPException(status_code=400, detail=f"{flag} must be a boolean")

    search_pid = patch.get("default_search_provider_id")
    if search_pid is not None:
        from ..services.search_providers import SearchProviderRegistry
        if not await SearchProviderRegistry.get_raw_provider(str(search_pid)):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown search provider: {search_pid}")

    fetch_pid = patch.get("default_fetch_provider_id")
    if fetch_pid is not None:
        from ..services.fetch_providers import FetchProviderRegistry
        if not await FetchProviderRegistry.get_raw_provider(str(fetch_pid)):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown fetch provider: {fetch_pid}")

    patch["updated_at"] = now_iso()
    return await repo.projects.update(project_id, owner_id, patch)


@router.get("/{project_id}/search")
async def search_project(
    project_id: str,
    q: str = Query(default=""),
    limit: int = Query(default=25),
    user: dict = Depends(get_current_user),
):
    """Grouped case-insensitive search across the project's collections."""
    await _require_project(project_id, user["id"])
    try:
        return await project_manager_service.project_search(
            user["id"], project_id, q, limit=limit)
    except ProjectManagerError as exc:
        raise _project_error(exc)


@router.get("/{project_id}/activity")
async def get_project_activity(
    project_id: str,
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    user: dict = Depends(get_current_user),
):
    """Newest-first project activity feed."""
    await _require_project(project_id, user["id"])
    return await project_manager_service.list_activity(
        user["id"], project_id, limit=limit, offset=offset)


# ---------------------------------------------------------------- project files
@router.get("/{project_id}/files")
async def list_files(
    project_id: str,
    search: Optional[str] = Query(default=None),
    kind: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    status: str = Query(default="active"),
    limit: int = Query(default=100),
    offset: int = Query(default=0),
    user: dict = Depends(get_current_user),
):
    """List project files with optional search/kind/tag/status filters."""
    await _require_project(project_id, user["id"])
    try:
        return await project_files_service.list_project_files(
            user["id"], project_id, search=search, kind=kind, tag=tag,
            status=status, limit=limit, offset=offset)
    except ProjectFileError as exc:
        raise _project_error(exc)


@router.post("/{project_id}/files/upload")
async def upload_files(
    project_id: str,
    files: List[UploadFile] = File(...),
    display_name: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    tags: Optional[str] = Form(default=None),
    user: dict = Depends(get_current_user),
):
    """Upload one or more files as project files (tags are comma-separated)."""
    owner_id = user["id"]
    await _require_project(project_id, owner_id)
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    try:
        max_bytes = int(config.PROJECT_FILE_MAX_BYTES)
    except (TypeError, ValueError):
        max_bytes = 20 * 1024 * 1024
    tag_list = _parse_tags_field(tags)
    created: List[dict] = []
    for upload in files:
        data = await upload.read(max_bytes + 1)
        try:
            file_doc = await project_files_service.upload_project_file(
                owner_id, project_id,
                filename=upload.filename or "attachment",
                data=data,
                declared_mime=upload.content_type,
                display_name=display_name,
                description=description or "",
                tags=tag_list,
            )
        except ProjectFileError as exc:
            raise _project_error(exc)
        created.append(file_doc)
        await project_manager_service.record_activity(
            owner_id, project_id, "file_uploaded",
            f"Uploaded file '{file_doc.get('display_name')}'",
            ref_type="project_file", ref_id=file_doc.get("id"))
    return {"files": created}


@router.post("/{project_id}/files/from-attachment")
async def create_file_from_attachment(
    project_id: str, body: Dict[str, Any], user: dict = Depends(get_current_user)
):
    """Register an existing same-project attachment as a project file."""
    owner_id = user["id"]
    await _require_project(project_id, owner_id)
    attachment_id = (body or {}).get("attachment_id")
    if not attachment_id:
        raise HTTPException(status_code=400, detail="attachment_id is required")
    try:
        file_doc = await project_files_service.create_from_attachment(
            owner_id, project_id, attachment_id,
            display_name=(body or {}).get("display_name"),
            description=(body or {}).get("description") or "",
            tags=list((body or {}).get("tags") or []),
        )
    except ProjectFileError as exc:
        raise _project_error(exc)
    await project_manager_service.record_activity(
        owner_id, project_id, "file_uploaded",
        f"Registered file '{file_doc.get('display_name')}' from attachment",
        ref_type="project_file", ref_id=file_doc.get("id"))
    return file_doc


@router.get("/{project_id}/files/{file_id}")
async def get_file(
    project_id: str, file_id: str, user: dict = Depends(get_current_user)
):
    """Project file detail with knowledge/run reference counts."""
    file_doc = await _require_project_file(project_id, file_id, user["id"])
    references = await project_files_service.file_references(user["id"], file_doc)
    return {**file_doc, "references": references}


@router.patch("/{project_id}/files/{file_id}")
async def update_file(
    project_id: str, file_id: str, body: ProjectFileUpdate,
    user: dict = Depends(get_current_user),
):
    """Update file metadata (display_name/description/tags/status). Soft only."""
    await _require_project_file(project_id, file_id, user["id"])
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        updated = await project_files_service.update_project_file(
            user["id"], file_id, patch)
    except ProjectFileError as exc:
        raise _project_error(exc)
    if not updated:
        raise HTTPException(status_code=404, detail="File not found")
    return updated


@router.delete("/{project_id}/files/{file_id}")
async def delete_file(
    project_id: str, file_id: str, user: dict = Depends(get_current_user)
):
    """Soft-delete a project file. Raw attachment bytes stay; evidence preserved."""
    owner_id = user["id"]
    file_doc = await _require_project_file(project_id, file_id, owner_id)
    updated = await project_files_service.soft_delete_project_file(owner_id, file_id)
    if not updated:
        raise HTTPException(status_code=404, detail="File not found")
    await project_manager_service.record_activity(
        owner_id, project_id, "file_deleted",
        f"Deleted file '{file_doc.get('display_name')}'",
        ref_type="project_file", ref_id=file_id)
    return updated
