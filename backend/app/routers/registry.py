from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import (MANUAL_CAPABILITY_KEYS, ModelCreate, ModelRecord, ModelUpdate, ProviderCreate,
                      ProviderModelCatalogItem, ProviderUpdate, now_iso)
from ..providers import ExecutionRequest, ProviderDiscoveryError, get_adapter
from ..ratelimit import enforce_rate_limit
from ..security import encrypt_secret
from ..urlguard import UrlValidationError, validate_provider_url

router = APIRouter(tags=["registry"])

SUPPORTED_TYPES = {"openai_compatible"}


def _sync_model_pricing_aliases(data: dict) -> dict:
    """Persist the canonical rate and both historical aliases together."""
    for keys in (
        ("input_price_per_1m", "input_price_per_1m_tokens", "input_cost"),
        ("output_price_per_1m", "output_price_per_1m_tokens", "output_cost"),
    ):
        supplied = [key for key in keys if key in data]
        if not supplied:
            continue
        value = next((data[key] for key in keys if key in data and data[key] is not None), None)
        for key in keys:
            data[key] = value
    return data


def _normalize_capability_source(value) -> "str | None":
    """Validate a capability_source token; None when absent/invalid."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text if text in ("PROVIDER", "VERIFIED", "MANUAL", "UNKNOWN") else None


def _sanitize_manual_capabilities(value) -> dict:
    """Keep only known manual capability keys with boolean coercion.

    Unknown keys are dropped (never stored) so a typo cannot fabricate a
    new capability dimension. ``None`` values are dropped; everything else
    is coerced with ``bool()``.
    """
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for key in MANUAL_CAPABILITY_KEYS:
        if key in value and value[key] is not None:
            cleaned[key] = bool(value[key])
    return cleaned


def _adapter_supports_model_discovery(adapter) -> bool:
    capability = getattr(adapter, "supports_model_discovery", False)
    return bool(capability() if callable(capability) else capability)


def _catalog_item_dict(item) -> dict:
    if isinstance(item, ProviderModelCatalogItem):
        return item.model_dump()
    return ProviderModelCatalogItem.model_validate(item).model_dump()


def _public_base_url(value):
    if not value:
        return value
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        # Provider URLs are configuration, not a place to echo credentials or
        # query secrets back to the browser.
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
    return value


def _provider_public(p: dict) -> dict:
    try:
        adapter = get_adapter(p)
        capabilities = dict(getattr(adapter, "capabilities", {}) or {})
    except (ValueError, TypeError):
        capabilities = {"model_discovery": False, "streaming": False}
    return {
        "id": p["id"], "owner_id": p["owner_id"], "name": p["name"],
        "provider_type": p["provider_type"], "base_url": _public_base_url(p.get("base_url")),
        "enabled": p.get("enabled", True), "has_api_key": bool(p.get("api_key_enc")),
        "seeded": p.get("seeded", False), "supports_model_discovery": bool(capabilities.get("model_discovery")),
        "capabilities": capabilities, "metadata": p.get("metadata", {}),
        "created_at": p["created_at"], "updated_at": p["updated_at"],
    }


# ------------------------------------------------------------- providers
# Safe provider lifecycle: providers are soft-deleted (tombstoned with
# ``deleted_at`` + ``enabled=False`` + revoked key material) so historical
# runs/messages/telemetry that reference them stay intact and readable, while
# tombstoned providers can never execute again (no key, excluded from active
# lists and rejected during resolution). Deletion is idempotent and always
# owner-scoped by the repository calls below.
def _provider_deleted(p: dict) -> bool:
    return bool(p and p.get("deleted_at"))


def _tombstone_patch() -> dict:
    ts = now_iso()
    return {"deleted_at": ts, "enabled": False, "api_key_enc": None, "updated_at": ts}


async def _live_providers(owner_id: str):
    """Providers not soft-deleted (active list / selector source of truth)."""
    items = await repo.providers.list({"owner_id": owner_id}, sort_field="created_at", descending=False)
    return [p for p in items if not _provider_deleted(p)]


@router.get("/providers")
async def list_providers(user: dict = Depends(get_current_user)):
    return [_provider_public(p) for p in await _live_providers(user["id"])]


@router.post("/providers")
async def create_provider(body: ProviderCreate, user: dict = Depends(get_current_user)):
    if body.provider_type not in SUPPORTED_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported provider type. Supported: {sorted(SUPPORTED_TYPES)}")
    base_url = body.base_url
    if body.provider_type == "openai_compatible":
        if not body.base_url:
            raise HTTPException(status_code=400, detail="base_url is required for OpenAI-compatible providers")
        try:
            base_url = validate_provider_url(body.base_url)
        except UrlValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message)
    doc = {
        "id": __import__("uuid").uuid4().hex, "owner_id": user["id"], "name": body.name,
        "provider_type": body.provider_type, "base_url": base_url,
        "api_key_enc": encrypt_secret(body.api_key) if body.api_key else None,
        "enabled": body.enabled, "seeded": False, "metadata": body.metadata,
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await repo.providers.insert(doc)
    return _provider_public(doc)


@router.patch("/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdate, user: dict = Depends(get_current_user)):
    provider = await repo.providers.get(provider_id, user["id"])
    if not provider or _provider_deleted(provider):
        raise HTTPException(status_code=404, detail="Provider not found")
    patch = {}
    data = body.model_dump(exclude_unset=True)
    for k in ("name", "base_url", "enabled", "metadata"):
        if k in data and data[k] is not None:
            patch[k] = data[k]
    if "base_url" in patch and provider.get("provider_type") == "openai_compatible":
        try:
            patch["base_url"] = validate_provider_url(patch["base_url"])
        except UrlValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message)
    if "api_key" in data:
        patch["api_key_enc"] = encrypt_secret(data["api_key"]) if data["api_key"] else None
    patch["updated_at"] = now_iso()
    updated = await repo.providers.update(provider_id, user["id"], patch)
    return _provider_public(updated)


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str, user: dict = Depends(get_current_user)):
    """Soft-delete a provider, idempotently and scoped to the owner.

    The provider is tombstoned (never hard-deleted): its encrypted API key is
    revoked, it is disabled for execution and excluded from active lists, and
    every linked model registration is deactivated for new executions while
    its historical identity is preserved. Historical conversations, runs,
    traces, usage and evaluation records are never touched.
    """
    provider = await repo.providers.get(provider_id, user["id"])
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    if _provider_deleted(provider):
        # Idempotent repeat delete: already tombstoned, nothing left to do.
        return {"ok": True, "already_deleted": True}
    patch = _tombstone_patch()
    await repo.providers.update(provider_id, user["id"], patch)
    linked = await repo.models.list(
        {"owner_id": user["id"], "provider_id": provider_id}, limit=1000
    )
    deactivated = 0
    for model in linked:
        if not model.get("deleted_at"):
            await repo.models.update(model["id"], user["id"], {
                "enabled": False, "deleted_at": patch["deleted_at"],
                "updated_at": patch["updated_at"],
            })
            deactivated += 1
    return {"ok": True, "linked_models_deactivated": deactivated}


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, user: dict = Depends(get_current_user)):
    provider = await repo.providers.get(provider_id, user["id"])
    if not provider or _provider_deleted(provider):
        raise HTTPException(status_code=404, detail="Provider not found")
    if not provider.get("enabled", True):
        raise HTTPException(status_code=400, detail="Provider is disabled")
    await enforce_rate_limit("provider_test", user["id"], 30, 3600)
    try:
        adapter = get_adapter(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await adapter.test_connection()


# ------------------------------------------------------------- models
@router.get("/providers/{provider_id}/models")
async def discover_provider_models(provider_id: str, user: dict = Depends(get_current_user)):
    provider = await repo.providers.get(provider_id, user["id"])
    if not provider or _provider_deleted(provider):
        raise HTTPException(status_code=404, detail="Provider not found")
    if not provider.get("enabled", True):
        raise HTTPException(status_code=400, detail="Provider is disabled")
    try:
        adapter = get_adapter(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported provider")
    supports = _adapter_supports_model_discovery(adapter)
    if not supports:
        raise HTTPException(status_code=400, detail="This provider does not support model discovery")
    try:
        catalog = await adapter.discover_models()
        models = [_catalog_item_dict(item) for item in catalog]
    except ProviderDiscoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail={
            "code": exc.code,
            "category": "PROVIDER",
            "message": exc.message,
            "retryable": exc.status_code in (502, 504),
            "source": "model_discovery",
            "upstream_status": None,
            "details_safe": None,
        })
    except Exception:
        # Do not surface provider response bodies, credentials, or adapter internals.
        raise HTTPException(status_code=502, detail="Provider model discovery failed")
    return {
        "provider_id": provider_id,
        "provider_type": provider.get("provider_type"),
        "supports_model_discovery": supports,
        "capabilities": dict(getattr(adapter, "capabilities", {}) or {}),
        "models": models,
    }


@router.get("/models")
async def list_models(user: dict = Depends(get_current_user)):
    items = await repo.models.list({"owner_id": user["id"]}, sort_field="created_at", descending=False)
    # Active selector list: exclude model registrations whose provider was
    # deleted. Historical records keep referencing the preserved model ids.
    deleted_provider_ids = {
        p["id"] for p in await repo.providers.list({"owner_id": user["id"]})
        if _provider_deleted(p)
    }
    return [
        _sync_model_pricing_aliases(dict(item))
        for item in items
        if not item.get("deleted_at") and item.get("provider_id") not in deleted_provider_ids
    ]


@router.post("/models")
async def create_model(body: ModelCreate, user: dict = Depends(get_current_user)):
    provider = await repo.providers.get(body.provider_id, user["id"])
    if not provider or _provider_deleted(provider):
        raise HTTPException(status_code=400, detail="Provider not found")
    data = _sync_model_pricing_aliases(body.model_dump())
    model = ModelRecord(owner_id=user["id"], **data).model_dump()
    await repo.models.insert(model)
    return model


@router.patch("/models/{model_id}")
async def update_model(model_id: str, body: ModelUpdate, user: dict = Depends(get_current_user)):
    """Patch a model. Requires auth (``get_current_user``); owner-scoped.

    Manual capability override: when ``capabilities`` is supplied, only the
    known keys (tools/vision/streaming/text/structured_output) are merged
    over the stored map (other stored keys are preserved, never wiped) and
    ``capability_source`` becomes ``"MANUAL"`` unless the caller explicitly
    sets a valid ``capability_source``. Omitting ``capabilities`` leaves both
    fields untouched.
    """
    existing = await repo.models.get(model_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Model not found")
    data = body.model_dump(exclude_unset=True)
    capabilities_override = data.pop("capabilities", None)
    source_override = _normalize_capability_source(data.pop("capability_source", None))
    patch = _sync_model_pricing_aliases(data)
    if capabilities_override is not None:
        merged = dict(existing.get("capabilities") or {})
        merged.update(_sanitize_manual_capabilities(capabilities_override))
        patch["capabilities"] = merged
        patch["capability_source"] = source_override or "MANUAL"
    elif source_override is not None:
        patch["capability_source"] = source_override
    if "provider_id" in patch:
        target = await repo.providers.get(patch["provider_id"], user["id"])
        if not target or _provider_deleted(target):
            raise HTTPException(status_code=400, detail="Provider not found")
    patch["updated_at"] = now_iso()
    return await repo.models.update(model_id, user["id"], patch)


@router.delete("/models/{model_id}")
async def delete_model(model_id: str, user: dict = Depends(get_current_user)):
    """Soft-delete a model: deactivated for new executions, identity preserved."""
    model = await repo.models.get(model_id, user["id"])
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    if not model.get("deleted_at"):
        await repo.models.update(model_id, user["id"], {
            "enabled": False, "deleted_at": now_iso(), "updated_at": now_iso(),
        })
    return {"ok": True}


@router.post("/models/{model_id}/refresh-metadata")
async def refresh_model_metadata(model_id: str, user: dict = Depends(get_current_user)):
    model = await repo.models.get(model_id, user["id"])
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    provider = await repo.providers.get(model.get("provider_id"), user["id"])
    if not provider or _provider_deleted(provider):
        raise HTTPException(status_code=400, detail="Provider not found")
    if not provider.get("enabled", True):
        raise HTTPException(status_code=400, detail="Provider is disabled")
    try:
        adapter = get_adapter(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported provider")
    if not _adapter_supports_model_discovery(adapter):
        raise HTTPException(status_code=400, detail="This provider does not support model discovery")
    try:
        catalog = [_catalog_item_dict(item) for item in await adapter.discover_models()]
    except ProviderDiscoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except Exception:
        raise HTTPException(status_code=502, detail="Provider model discovery failed")

    match = next((item for item in catalog if item["model_identifier"] == model.get("model_identifier")), None)
    if match is None:
        return {
            "ok": False,
            "refreshed": False,
            "model_id": model_id,
            "message": "Registered model was not found in the provider catalog; metadata was unchanged",
            "model": model,
        }

    # Manual capability overrides are never overwritten by discovery: a
    # MANUAL record keeps its capabilities AND its source; every other
    # record adopts the catalog capabilities with source PROVIDER. Other
    # metadata (pricing/modalities/provider_metadata) always refreshes.
    manual_hold = (model.get("capability_source") or "UNKNOWN") == "MANUAL"
    patch = {
        "display_name": match["display_name"],
        "context_window": match["context_window"],
        "input_cost": match["input_cost"],
        "output_cost": match["output_cost"],
        "input_price_per_1m": match["input_price_per_1m"],
        "output_price_per_1m": match["output_price_per_1m"],
        "input_price_per_1m_tokens": match["input_price_per_1m"],
        "output_price_per_1m_tokens": match["output_price_per_1m"],
        "cache_read_price_per_1m": match.get("cache_read_price_per_1m"),
        "cache_write_price_per_1m": match.get("cache_write_price_per_1m"),
        "cache_creation_price_per_1m": match.get("cache_creation_price_per_1m"),
        "reasoning_price_per_1m": match.get("reasoning_price_per_1m"),
        "raw_pricing": match.get("raw_pricing"),
        "modalities": match["modalities"],
        "provider_metadata": match["provider_metadata"],
        "pricing_source": match["pricing_source"],
        "pricing_updated_at": match["pricing_updated_at"],
        "updated_at": now_iso(),
    }
    if manual_hold:
        patch["capabilities"] = dict(model.get("capabilities") or {})
        patch["capability_source"] = "MANUAL"
    else:
        patch["capabilities"] = match["capabilities"]
        patch["capability_source"] = "PROVIDER"
    updated = await repo.models.update(model_id, user["id"], patch)
    return {
        "ok": True,
        "refreshed": True,
        "model_id": model_id,
        "message": "Model metadata and pricing refreshed",
        "model": updated,
    }


@router.post("/models/{model_id}/test")
async def test_model(model_id: str, user: dict = Depends(get_current_user)):
    model = await repo.models.get(model_id, user["id"])
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    provider = await repo.providers.get(model["provider_id"], user["id"])
    if not provider or _provider_deleted(provider):
        raise HTTPException(status_code=400, detail="Provider not found")
    if not model.get("enabled", True):
        raise HTTPException(status_code=400, detail="Model is disabled")
    if not provider.get("enabled", True):
        raise HTTPException(status_code=400, detail="Provider is disabled")
    await enforce_rate_limit("model_test", user["id"], 60, 3600)
    adapter = get_adapter(provider)
    req = ExecutionRequest(system_prompt="You are a connectivity probe.",
                           messages=[{"role": "user", "content": "Reply with the single word: OK"}],
                           max_tokens=5)
    result = await adapter.execute(model, req)
    return {"ok": result.status == "success", "latency_ms": result.latency_ms,
            "sample": (result.text or "")[:60], "error": result.error}
