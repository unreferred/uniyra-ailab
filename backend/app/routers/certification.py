"""V5.10 certification readiness endpoints.

Readiness-for-certification only: no endpoint or state can ever return a
"certified" result. Owner isolation is enforced for every route.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import CertificationCandidateCreate, CertificationCandidateUpdate
from ..services import certification as certification_service
from ..services.certification import (
    CertificationError,
    CertificationNotFound,
    READINESS_POLICY,
)

router = APIRouter(prefix="/certification", tags=["certification"])


def _http_error(exc: CertificationError) -> HTTPException:
    if isinstance(exc, CertificationNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/candidates")
async def create_candidate(
    body: CertificationCandidateCreate,
    user: dict = Depends(get_current_user),
):
    try:
        return await certification_service.create_candidate(user["id"], body.model_dump())
    except CertificationError as exc:
        raise _http_error(exc)


@router.get("/candidates")
async def list_candidates(
    project_id: Optional[str] = None,
    state: Optional[str] = None,
    subject_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    return await certification_service.list_candidates(
        user["id"],
        project_id=project_id,
        state=state,
        subject_type=subject_type,
        limit=limit,
        offset=offset,
    )


@router.get("/policy")
async def get_policy(user: dict = Depends(get_current_user)):
    return READINESS_POLICY


@router.get("/candidates/{candidate_id}")
async def get_candidate(candidate_id: str, user: dict = Depends(get_current_user)):
    try:
        candidate = await certification_service.get_candidate(user["id"], candidate_id)
        readiness = await certification_service.evaluate_readiness(
            user["id"], candidate_id, persist=False
        )
    except CertificationError as exc:
        raise _http_error(exc)
    return {**candidate, "readiness": readiness}


@router.post("/candidates/{candidate_id}/readiness")
async def evaluate_readiness(candidate_id: str, user: dict = Depends(get_current_user)):
    try:
        return await certification_service.evaluate_readiness(
            user["id"], candidate_id, persist=True
        )
    except CertificationError as exc:
        raise _http_error(exc)


@router.patch("/candidates/{candidate_id}")
async def update_candidate(
    candidate_id: str,
    body: CertificationCandidateUpdate,
    user: dict = Depends(get_current_user),
):
    try:
        return await certification_service.update_candidate(
            user["id"], candidate_id, body.model_dump(exclude_unset=True)
        )
    except CertificationError as exc:
        raise _http_error(exc)
