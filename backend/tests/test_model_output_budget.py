"""Focused unit tests for model-aware output budget (Section M).

Coverage:
1. known model output capability (e.g. 4096, 8192, 16384)
2. unknown model capability (represented as UNKNOWN / None, uses conservative fallback)
3. requested limit below model maximum (e.g. requested 1000 with model max 8192 -> 1000)
4. requested limit above known maximum (e.g. requested 16384 with model max 8192 -> clamped to 8192)
5. context window is not mistaken for max output (e.g. context_window 128k, max_output_tokens 4k -> effective max output 4k, not 128k)
6. provider-specific effective limit where supported (e.g. provider ceiling clamps model max)
7. OpenRouter discovery normalization extracts top_provider.max_completion_tokens
"""

import pytest
from app.models import (
    ModelCreate,
    ModelRecord,
    ModelUpdate,
    ProviderModelCatalogItem,
)
from app.providers import (
    model_output_capability,
    normalize_openai_compatible_model,
    normalize_openrouter_model,
    resolve_effective_output_budget,
)


def test_01_known_model_output_capability():
    """1. Known model output capability (e.g. 4096, 8192, 16384)."""
    for cap in (4096, 8192, 16384):
        budget = resolve_effective_output_budget(
            known_model_output_capability=cap,
        )
        assert budget["effective_output_limit"] == cap
        assert budget["is_model_capability_known"] is True
        assert budget["known_model_output_capability"] == cap
        assert budget["clamped_by"] == "model"


def test_02_unknown_model_capability():
    """2. Unknown model capability (represented as UNKNOWN / None, uses conservative fallback)."""
    # None capability
    budget_none = resolve_effective_output_budget(
        known_model_output_capability=None,
        default_fallback=8192,
    )
    assert budget_none["effective_output_limit"] == 8192
    assert budget_none["is_model_capability_known"] is False
    assert budget_none["known_model_output_capability"] == "UNKNOWN"
    assert budget_none["clamped_by"] == "default"

    # Non-positive capability treated as unknown
    budget_zero = resolve_effective_output_budget(
        known_model_output_capability=0,
        default_fallback=8192,
    )
    assert budget_zero["effective_output_limit"] == 8192
    assert budget_zero["is_model_capability_known"] is False
    assert budget_zero["known_model_output_capability"] == "UNKNOWN"
    assert budget_zero["clamped_by"] == "default"


def test_03_requested_limit_below_model_maximum():
    """3. Requested limit below model maximum (e.g. requested 1000 with model max 8192 -> 1000)."""
    budget = resolve_effective_output_budget(
        requested_output_limit=1000,
        known_model_output_capability=8192,
    )
    assert budget["effective_output_limit"] == 1000
    assert budget["requested_output_limit"] == 1000
    assert budget["is_model_capability_known"] is True
    assert budget["known_model_output_capability"] == 8192
    assert budget["clamped_by"] == "requested"


def test_04_requested_limit_above_known_maximum():
    """4. Requested limit above known maximum (e.g. requested 16384 with model max 8192 -> clamped to 8192)."""
    budget = resolve_effective_output_budget(
        requested_output_limit=16384,
        known_model_output_capability=8192,
    )
    assert budget["effective_output_limit"] == 8192
    assert budget["requested_output_limit"] == 16384
    assert budget["is_model_capability_known"] is True
    assert budget["known_model_output_capability"] == 8192
    assert budget["clamped_by"] == "model"


def test_05_context_window_not_mistaken_for_max_output():
    """5. Context window is not mistaken for max output (e.g. context_window 128k, max_output_tokens 4k -> effective 4k, not 128k)."""
    model_record = {
        "context_window": 131072,  # 128k
        "max_output_tokens": 4096,  # 4k
    }
    extracted_cap = model_output_capability(model_record)
    assert extracted_cap == 4096
    assert extracted_cap != model_record["context_window"]

    budget = resolve_effective_output_budget(
        requested_output_limit=64000,
        known_model_output_capability=extracted_cap,
    )
    assert budget["effective_output_limit"] == 4096
    assert budget["effective_output_limit"] != 131072
    assert budget["clamped_by"] == "model"


def test_06_provider_specific_effective_limit():
    """6. Provider-specific effective limit where supported (e.g. provider ceiling clamps model max)."""
    # Provider output limit (4096) lower than model capability (16384)
    budget = resolve_effective_output_budget(
        known_model_output_capability=16384,
        known_provider_output_capability=4096,
    )
    assert budget["effective_output_limit"] == 4096
    assert budget["clamped_by"] == "provider"

    # Execution ceiling (2048) lower than requested (4000) and model (8192)
    budget_ceiling = resolve_effective_output_budget(
        requested_output_limit=4000,
        known_model_output_capability=8192,
        configured_execution_ceiling=2048,
    )
    assert budget_ceiling["effective_output_limit"] == 2048
    assert budget_ceiling["clamped_by"] == "ceiling"


def test_07_openrouter_discovery_normalization_extracts_max_completion_tokens():
    """7. OpenRouter discovery normalization extracts top_provider.max_completion_tokens."""
    # From top_provider.max_completion_tokens
    raw_item_top = {
        "id": "openai/gpt-4o-mini",
        "name": "GPT-4o Mini",
        "context_length": 128000,
        "top_provider": {
            "max_completion_tokens": 16384,
        },
    }
    normalized_top = normalize_openrouter_model(raw_item_top)
    assert normalized_top is not None
    assert normalized_top.max_output_tokens == 16384
    assert normalized_top.capabilities.get("max_output_tokens") == 16384

    # From per_request_limits.max_tokens fallback
    raw_item_per_req = {
        "id": "anthropic/claude-3.5-haiku",
        "name": "Claude 3.5 Haiku",
        "context_length": 200000,
        "per_request_limits": {
            "max_tokens": 8192,
        },
    }
    normalized_per_req = normalize_openrouter_model(raw_item_per_req)
    assert normalized_per_req is not None
    assert normalized_per_req.max_output_tokens == 8192
    assert normalized_per_req.capabilities.get("max_output_tokens") == 8192

    # From architecture.max_output_tokens fallback
    raw_item_arch = {
        "id": "meta-llama/llama-3.1-70b-instruct",
        "name": "Llama 3.1 70B",
        "context_length": 131072,
        "architecture": {
            "max_output_tokens": 4096,
        },
    }
    normalized_arch = normalize_openrouter_model(raw_item_arch)
    assert normalized_arch is not None
    assert normalized_arch.max_output_tokens == 4096
    assert normalized_arch.capabilities.get("max_output_tokens") == 4096


def test_model_schemas_max_output_tokens():
    """Verify max_output_tokens on ProviderModelCatalogItem, ModelCreate, ModelUpdate, ModelRecord."""
    # ProviderModelCatalogItem
    item = ProviderModelCatalogItem(
        model_identifier="m1",
        display_name="Model 1",
        max_output_tokens=4096,
    )
    assert item.max_output_tokens == 4096
    assert item.capabilities.get("max_output_tokens") == 4096

    # ModelCreate
    create = ModelCreate(
        provider_id="p1",
        model_identifier="m1",
        display_name="Model 1",
        max_output_tokens=8192,
    )
    assert create.max_output_tokens == 8192
    assert create.capabilities.get("max_output_tokens") == 8192

    # ModelUpdate
    update = ModelUpdate(max_output_tokens=2048)
    assert update.max_output_tokens == 2048
    assert update.capabilities.get("max_output_tokens") == 2048

    # ModelRecord
    record = ModelRecord(
        owner_id="u1",
        provider_id="p1",
        model_identifier="m1",
        display_name="Model 1",
        max_output_tokens=16384,
    )
    assert record.max_output_tokens == 16384
    assert record.capabilities.get("max_output_tokens") == 16384


def test_openai_compatible_normalization_extracts_max_output():
    """Verify normalize_openai_compatible_model extracts max completion/output tokens."""
    raw = {
        "id": "local-model",
        "name": "Local Model",
        "max_completion_tokens": 4096,
    }
    normalized = normalize_openai_compatible_model(raw)
    assert normalized is not None
    assert normalized.max_output_tokens == 4096
    assert normalized.capabilities.get("max_output_tokens") == 4096
