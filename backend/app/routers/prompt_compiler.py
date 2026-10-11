"""V6.3 Prompt Compiler HTTP surface.

Routes live under ``/prompt-compiler``. All work is delegated to
:mod:`app.services.prompt_compiler`; this router only maps bodies onto service
calls and translates domain errors (:class:`PromptCompilerNotFound` -> 404,
:class:`PromptCompilerError` -> 400). Static routes are declared before dynamic
ones so ``/compiled`` is never shadowed by ``/compiled/{compiled_id}``.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import PromptCompileRequest
from ..services import prompt_compiler as service
from ..services.prompt_compiler import PromptCompilerError, PromptCompilerNotFound

router = APIRouter(prefix="/prompt-compiler", tags=["prompt-compiler"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


def _translate(exc: PromptCompilerError) -> HTTPException:
    if isinstance(exc, PromptCompilerNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/catalog")
async def prompt_compiler_catalog(user: dict = Depends(get_current_user)):
    return service.catalog()


@router.post("/preview")
async def preview_prompt_compile(
    body: PromptCompileRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.compile_prompt(_owner(user), body.model_dump(), persist=False)
    except PromptCompilerError as exc:
        raise _translate(exc)


@router.post("/compile")
async def compile_prompt(
    body: PromptCompileRequest, user: dict = Depends(get_current_user)
):
    try:
        return await service.compile_prompt(_owner(user), body.model_dump(), persist=True)
    except PromptCompilerError as exc:
        raise _translate(exc)


# NOTE: the list route is declared before the dynamic ``/compiled/{compiled_id}``.
@router.get("/compiled")
async def list_compiled_prompts(
    project_id: Optional[str] = None,
    prompt_version_id: Optional[str] = None,
    agent_version_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    try:
        return await service.list_compiled_prompts(
            _owner(user),
            project_id=project_id,
            prompt_version_id=prompt_version_id,
            agent_version_id=agent_version_id,
            limit=limit,
            offset=offset,
        )
    except PromptCompilerError as exc:
        raise _translate(exc)


@router.get("/compiled/{compiled_id}")
async def get_compiled_prompt(
    compiled_id: str, user: dict = Depends(get_current_user)
):
    try:
        return await service.get_compiled_prompt(_owner(user), compiled_id)
    except PromptCompilerError as exc:
        raise _translate(exc)
