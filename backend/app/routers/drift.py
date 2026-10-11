"""V6.9 drift & failure analysis endpoints.

POST /drift/analyze runs a deterministic (+ optional judged) drift analysis and
persists it; the read paths are owner-scoped. A missing/foreign resource maps to
404 and a domain validation failure maps to 400.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_current_user
from ..models import DriftAnalyzeRequest
from ..services import drift as drift_service
from ..services.drift import DriftError, DriftNotFound

router = APIRouter(prefix="/drift", tags=["drift"])


def _owner(user: dict) -> str:
    owner_id = user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return owner_id


@router.get("/catalog")
async def drift_catalog(user: dict = Depends(get_current_user)):
    return drift_service.drift_catalog()


@router.post("/analyze")
async def analyze_drift(
    body: DriftAnalyzeRequest,
    user: dict = Depends(get_current_user),
):
    try:
        return await drift_service.analyze_drift(_owner(user), body.model_dump())
    except DriftNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DriftError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/analyses")
async def list_analyses(
    project_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    try:
        return await drift_service.list_drift_analyses(
            _owner(user), project_id=project_id, limit=limit, offset=offset
        )
    except DriftNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DriftError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return await drift_service.get_drift_analysis(_owner(user), analysis_id)
    except DriftNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DriftError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/analyses/{analysis_id}/findings")
async def materialize_analysis_findings(
    analysis_id: str,
    user: dict = Depends(get_current_user),
):
    try:
        return await drift_service.materialize_findings(_owner(user), analysis_id)
    except DriftNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DriftError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
