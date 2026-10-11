"""Search and Fetch provider endpoints (Phase 4.2.3.1).

Exposes public metadata, safe configuration updating (with secret encryption),
and connection testing for search and fetch providers.

PATCH bodies accept (all optional): name, enabled, configuration, api_key,
credential_id (string to link a vault credential, null/"" to clear),
project_id (vault scope hint, validated but never stored), is_default (fetch
registry switching), reuse_search_credential / reuse_fetch_credential
(ciphertext copy, no plaintext). Public projections never contain secrets.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_current_user
from ..services.search_providers import (
    SearchProviderRegistry,
    SearchError,
    public_search_provider,
)
from ..services.fetch_providers import (
    FetchProviderRegistry,
    FetchError,
    public_fetch_provider,
)
from ..services.tool_valves import apply_provider_values, ValveError

router = APIRouter(tags=["search-fetch"])


class ProviderPatch(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    configuration: Optional[Dict[str, Any]] = None
    api_key: Optional[str] = None
    credential_id: Optional[str] = None
    project_id: Optional[str] = None
    is_default: Optional[bool] = None
    reuse_search_credential: Optional[bool] = None
    reuse_fetch_credential: Optional[bool] = None
    valves: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------- Search Providers
@router.get("/search-providers")
async def list_search_providers(user: dict = Depends(get_current_user)):
    items = await SearchProviderRegistry.list_providers(user["id"])
    return {"items": items, "total": len(items)}


@router.get("/search-providers/{provider_id}")
async def get_search_provider(provider_id: str, user: dict = Depends(get_current_user)):
    prov = await SearchProviderRegistry.get_provider(provider_id)
    if prov is None:
        raise HTTPException(status_code=404, detail=f"Search provider '{provider_id}' not found")
    return prov


@router.patch("/search-providers/{provider_id}")
async def update_search_provider(
    provider_id: str, body: ProviderPatch, user: dict = Depends(get_current_user)
):
    try:
        patch = body.model_dump(exclude_unset=True)
        vals = patch.get("valves")
        if vals is not None:
            patch.pop("valves", None)
            await apply_provider_values(
                user["id"], "search", provider_id, vals, patch.get("project_id")
            )
        updated = await SearchProviderRegistry.update_provider(provider_id, patch, user["id"])
        return updated
    except SearchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except ValveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/search-providers/{provider_id}/test")
async def test_search_provider(provider_id: str, user: dict = Depends(get_current_user)):
    try:
        adapter = await SearchProviderRegistry.get_adapter(provider_id)
        result = await adapter.test_connection()
        status = str(result.get("status") or "UNKNOWN")
        await SearchProviderRegistry.record_test_result(provider_id, status)
        return result
    except SearchError as exc:
        return {
            "ok": False,
            "status": exc.error_category,
            "message": exc.message,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "UNAVAILABLE",
            "message": f"Connection check failed: {str(exc)[:150]}",
        }


# ---------------------------------------------------------------- Fetch Providers
@router.get("/fetch-providers")
async def list_fetch_providers(user: dict = Depends(get_current_user)):
    items = await FetchProviderRegistry.list_providers(user["id"])
    return {"items": items, "total": len(items)}


@router.get("/fetch-providers/{provider_id}")
async def get_fetch_provider(provider_id: str, user: dict = Depends(get_current_user)):
    prov = await FetchProviderRegistry.get_provider(provider_id)
    if prov is None:
        raise HTTPException(status_code=404, detail=f"Fetch provider '{provider_id}' not found")
    return prov


@router.patch("/fetch-providers/{provider_id}")
async def update_fetch_provider(
    provider_id: str, body: ProviderPatch, user: dict = Depends(get_current_user)
):
    try:
        patch = body.model_dump(exclude_unset=True)
        vals = patch.get("valves")
        if vals is not None:
            patch.pop("valves", None)
            await apply_provider_values(
                user["id"], "fetch", provider_id, vals, patch.get("project_id")
            )
        updated = await FetchProviderRegistry.update_provider(provider_id, patch, user["id"])
        return updated
    except FetchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except ValveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/fetch-providers/{provider_id}/test")
async def test_fetch_provider(provider_id: str, user: dict = Depends(get_current_user)):
    try:
        adapter = await FetchProviderRegistry.get_adapter(provider_id)
        result = await adapter.test_connection()
        status = str(result.get("status") or "UNKNOWN")
        await FetchProviderRegistry.record_test_result(provider_id, status)
        return result
    except FetchError as exc:
        return {
            "ok": False,
            "status": exc.error_category,
            "message": exc.message,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "UNAVAILABLE",
            "message": f"Connection check failed: {str(exc)[:150]}",
        }
