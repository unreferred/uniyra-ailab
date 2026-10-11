"""V6.14 Workspace Prompt Capture HTTP surface.

Routes live under ``/prompt-capture``. All work is delegated to
:mod:`app.services.prompt_capture`; this router only maps bodies onto service
calls and translates domain errors (:class:`PromptCaptureNotFound` -> 404,
:class:`PromptCaptureError` -> 400). Static routes are declared before the
dynamic ``/{capture_id}`` route so ``/catalog`` is never shadowed.
"""
from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import PromptCaptureRequest, PromptCaptureSaveRequest
from ..services import prompt_capture as service
from ..services.prompt_capture import PromptCaptureError, PromptCaptureNotFound

router = APIRouter(prefix="/prompt-capture", tags=["prompt-capture"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: PromptCaptureError) -> HTTPException:
    if isinstance(exc, PromptCaptureNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/catalog")
async def prompt_capture_catalog(user: dict = Depends(get_current_user)):
    return service.catalog()


@router.post("/analyze")
async def analyze_prompt_capture(
    body: PromptCaptureRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.analyze(_owner(user), body.model_dump())
    except PromptCaptureError as exc:
        raise _translate(exc)


@router.post("/save")
async def save_prompt_capture(
    body: PromptCaptureSaveRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.save(_owner(user), body.model_dump())
    except PromptCaptureError as exc:
        raise _translate(exc)


# NOTE: the static list route is declared before the dynamic ``/{capture_id}``.
@router.get("")
async def list_prompt_captures(
    project_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await service.list_captures(_owner(user), project_id)
    except PromptCaptureError as exc:
        raise _translate(exc)


@router.get("/{capture_id}")
async def get_prompt_capture(
    capture_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await service.get_capture(_owner(user), capture_id)
    except PromptCaptureError as exc:
        raise _translate(exc)
