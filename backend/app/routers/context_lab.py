"""V6.2 Context Lab HTTP surface: packs, budget policies, previews, inspection.

Routes live under ``/context-lab``. All work is delegated to
:mod:`app.services.context_lab`; this router only translates domain errors
(:class:`ContextLabNotFound` -> 404, :class:`ContextLabError` -> 400) and maps
bodies onto the service calls. Static routes are declared before dynamic ones so
``/packs/starters`` and ``.../versions/compare`` are never shadowed.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import (
    ContextBudgetPolicyCreate,
    ContextBudgetPolicyUpdate,
    ContextPackCreate,
    ContextPackUpdate,
    ContextPackVersionCreate,
    ContextPackVersionUpdate,
    ContextPreviewRequest,
)
from ..services import context_lab
from ..services.context_lab import ContextLabError, ContextLabNotFound

router = APIRouter(prefix="/context-lab", tags=["context-lab"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: ContextLabError) -> HTTPException:
    if isinstance(exc, ContextLabNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# ------------------------------------------------------------------ catalog
@router.get("/catalog")
async def context_lab_catalog(user: dict = Depends(get_current_user)):
    return context_lab.catalog()


# ------------------------------------------------------------------ packs
@router.get("/packs")
async def list_context_packs(
    project_id: str,
    include_archived: bool = False,
    user: dict = Depends(get_current_user),
):
    try:
        return await context_lab.list_packs(
            _owner(user), project_id, include_archived=include_archived
        )
    except ContextLabError as exc:
        raise _translate(exc)


@router.post("/packs")
async def create_context_pack(
    body: ContextPackCreate, user: dict = Depends(get_current_user)
):
    try:
        return await context_lab.create_pack(_owner(user), body.model_dump())
    except ContextLabError as exc:
        raise _translate(exc)


# NOTE: declared before the dynamic ``/packs/{pack_id}`` routes.
@router.post("/packs/starters")
async def materialize_context_starters(
    body: Dict[str, Any], user: dict = Depends(get_current_user)
):
    try:
        project_id = (body or {}).get("project_id")
        return await context_lab.materialize_starter_packs(_owner(user), project_id)
    except ContextLabError as exc:
        raise _translate(exc)


@router.get("/packs/{pack_id}")
async def get_context_pack(pack_id: str, user: dict = Depends(get_current_user)):
    try:
        return await context_lab.get_pack(_owner(user), pack_id)
    except ContextLabError as exc:
        raise _translate(exc)


@router.patch("/packs/{pack_id}")
async def update_context_pack(
    pack_id: str, body: ContextPackUpdate, user: dict = Depends(get_current_user)
):
    try:
        return await context_lab.update_pack(
            _owner(user), pack_id, body.model_dump(exclude_unset=True)
        )
    except ContextLabError as exc:
        raise _translate(exc)


@router.delete("/packs/{pack_id}")
async def delete_context_pack(pack_id: str, user: dict = Depends(get_current_user)):
    try:
        await context_lab.delete_pack(_owner(user), pack_id)
        return {"ok": True}
    except ContextLabError as exc:
        raise _translate(exc)


# ------------------------------------------------------------------ pack versions
@router.get("/packs/{pack_id}/versions")
async def list_context_pack_versions(
    pack_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await context_lab.list_pack_versions(_owner(user), pack_id)
    except ContextLabError as exc:
        raise _translate(exc)


@router.post("/packs/{pack_id}/versions")
async def create_context_pack_version(
    pack_id: str,
    body: ContextPackVersionCreate,
    user: dict = Depends(get_current_user),
):
    try:
        pack = await context_lab.get_pack(_owner(user), pack_id)
        return await context_lab.create_pack_version(
            _owner(user), pack, body.model_dump()
        )
    except ContextLabError as exc:
        raise _translate(exc)


# NOTE: ``/versions/compare`` MUST be declared before ``/versions/{version_id}``.
@router.get("/packs/{pack_id}/versions/compare")
async def compare_context_pack_versions(
    pack_id: str,
    from_version_id: str,
    to_version_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return await context_lab.compare_pack_versions(
            _owner(user), pack_id, from_version_id, to_version_id
        )
    except ContextLabError as exc:
        raise _translate(exc)


@router.get("/packs/{pack_id}/versions/{version_id}")
async def get_context_pack_version(
    pack_id: str, version_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await context_lab.get_pack_version(_owner(user), pack_id, version_id)
    except ContextLabError as exc:
        raise _translate(exc)


@router.patch("/packs/{pack_id}/versions/{version_id}")
async def update_context_pack_version(
    pack_id: str,
    version_id: str,
    body: ContextPackVersionUpdate,
    user: dict = Depends(get_current_user),
):
    try:
        return await context_lab.update_pack_version(
            _owner(user), pack_id, version_id, body.model_dump(exclude_unset=True)
        )
    except ContextLabError as exc:
        raise _translate(exc)


@router.post("/packs/{pack_id}/versions/{version_id}/freeze")
async def freeze_context_pack_version(
    pack_id: str, version_id: str, user: dict = Depends(get_current_user)
):
    try:
        pack = await context_lab.get_pack(_owner(user), pack_id)
        return await context_lab.freeze_pack_version(_owner(user), pack, version_id)
    except ContextLabError as exc:
        raise _translate(exc)


@router.post("/packs/{pack_id}/versions/{version_id}/deprecate")
async def deprecate_context_pack_version(
    pack_id: str, version_id: str, user: dict = Depends(get_current_user)
):
    try:
        pack = await context_lab.get_pack(_owner(user), pack_id)
        return await context_lab.deprecate_pack_version(_owner(user), pack, version_id)
    except ContextLabError as exc:
        raise _translate(exc)


# ------------------------------------------------------------------ runs
# NOTE: ``/runs/diff`` is declared before the dynamic ``/runs/{run_id}/assembly``.
@router.get("/runs/diff")
async def diff_run_contexts(
    run_a: str, run_b: str, user: dict = Depends(get_current_user)
):
    try:
        return await context_lab.diff_run_contexts(_owner(user), run_a, run_b)
    except ContextLabError as exc:
        raise _translate(exc)


@router.get("/runs/{run_id}/assembly")
async def run_assembly_view(run_id: str, user: dict = Depends(get_current_user)):
    try:
        return await context_lab.run_assembly_view(_owner(user), run_id)
    except ContextLabError as exc:
        raise _translate(exc)


# ------------------------------------------------------------------ preview
@router.post("/preview")
async def preview_context_assembly(
    body: ContextPreviewRequest, user: dict = Depends(get_current_user)
):
    try:
        return await context_lab.preview_assembly(_owner(user), body.model_dump())
    except ContextLabError as exc:
        raise _translate(exc)


# ------------------------------------------------------------------ budget policies
@router.get("/budget-policies")
async def list_context_budget_policies(
    project_id: str,
    include_archived: bool = False,
    user: dict = Depends(get_current_user),
):
    try:
        return await context_lab.list_budget_policies(
            _owner(user), project_id, include_archived=include_archived
        )
    except ContextLabError as exc:
        raise _translate(exc)


@router.post("/budget-policies")
async def create_context_budget_policy(
    body: ContextBudgetPolicyCreate, user: dict = Depends(get_current_user)
):
    try:
        return await context_lab.create_budget_policy(_owner(user), body.model_dump())
    except ContextLabError as exc:
        raise _translate(exc)


@router.get("/budget-policies/{policy_id}")
async def get_context_budget_policy(
    policy_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await context_lab.get_budget_policy(_owner(user), policy_id)
    except ContextLabError as exc:
        raise _translate(exc)


@router.patch("/budget-policies/{policy_id}")
async def update_context_budget_policy(
    policy_id: str,
    body: ContextBudgetPolicyUpdate,
    user: dict = Depends(get_current_user),
):
    try:
        return await context_lab.update_budget_policy(
            _owner(user), policy_id, body.model_dump(exclude_unset=True)
        )
    except ContextLabError as exc:
        raise _translate(exc)


@router.delete("/budget-policies/{policy_id}")
async def delete_context_budget_policy(
    policy_id: str, user: dict = Depends(get_current_user)
):
    try:
        await context_lab.delete_budget_policy(_owner(user), policy_id)
        return {"ok": True}
    except ContextLabError as exc:
        raise _translate(exc)
