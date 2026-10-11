"""Tests for V4.2.2 - Reasoning, Token, Cache, and Pricing Telemetry."""
import pytest

from app.models import ModelRecord, ProviderModelCatalogItem
from app.providers import ExecutionRequest, ExecutionResult, normalize_openrouter_model
from app.services.pricing import (
    calculate_cost_breakdown,
    estimate_cost,
    pricing_snapshot,
)
from app.services.telemetry_normalizer import (
    extract_reasoning_from_choice,
    normalize_usage,
    parse_non_negative_int,
)


# =====================================================================
# 1. Telemetry Normalizer Tests
# =====================================================================

def test_extract_reasoning_variants():
    # 1. reasoning_content in delta or message
    res1 = extract_reasoning_from_choice({"reasoning_content": "step 1", "content": "hello"})
    assert res1.content == "step 1"
    assert res1.available is True

    res2 = extract_reasoning_from_choice({}, raw_delta={"reasoning": "step 2", "content": "world"})
    assert res2.content == "step 2"
    assert res2.available is True

    res3 = extract_reasoning_from_choice({"thought": "deep thought"})
    assert res3.content == "deep thought"
    assert res3.available is True

    # 2. No reasoning field returns available=False, content=None, never synthetic text
    res4 = extract_reasoning_from_choice({"content": "direct answer"})
    assert res4.content is None
    assert res4.available is False

    res5 = extract_reasoning_from_choice({})
    assert res5.content is None
    assert res5.available is False

    res6 = extract_reasoning_from_choice({"reasoning_content": ""})
    assert res6.content is None
    assert res6.available is False

    res7 = extract_reasoning_from_choice({"reasoning_content": "   "})
    assert res7.content is None
    assert res7.available is False


def test_normalize_usage_openai_format():
    raw = {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "total_tokens": 1500,
        "prompt_tokens_details": {
            "cached_tokens": 800,
        },
        "completion_tokens_details": {
            "reasoning_tokens": 200,
        },
    }
    usage, cache = normalize_usage(raw)

    assert usage.input_tokens == 1000
    assert usage.output_tokens == 500
    assert usage.total_tokens == 1500
    assert usage.reasoning_tokens == 200
    assert usage.cache_read_tokens == 800
    assert usage.uncached_input_tokens == 200

    assert cache.cache_read_tokens == 800
    assert cache.uncached_input_tokens == 200
    assert cache.cache_hit_rate == 0.8  # 800 / 1000


def test_normalize_usage_anthropic_openrouter_format():
    raw = {
        "prompt_tokens": 1200,
        "completion_tokens": 400,
        "total_tokens": 1600,
        "prompt_tokens_details": {
            "cached_tokens": 600,
            "cache_creation_input_tokens": 300,
        },
    }
    usage, cache = normalize_usage(raw)

    assert usage.cache_read_tokens == 600
    assert usage.cache_creation_tokens == 300
    assert usage.uncached_input_tokens == 600  # 1200 - 600
    assert cache.cache_hit_rate == 0.5  # 600 / 1200
    assert usage.reasoning_tokens is None  # Not reported


def test_normalize_usage_missing_or_zero():
    # Never fabricate: when raw is empty/None
    usage, cache = normalize_usage(None)
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.cache_read_tokens is None
    assert cache.cache_hit_rate is None
    assert cache.cache_read_tokens is None

    # Distinct 0 from None
    usage_zero, cache_zero = normalize_usage({"prompt_tokens": 0, "completion_tokens": 0})
    assert usage_zero.input_tokens == 0
    assert usage_zero.output_tokens == 0
    assert usage_zero.cache_read_tokens is None  # not reported
    assert cache_zero.cache_hit_rate is None  # cannot divide 0/0


# =====================================================================
# 2. Cost Engine & Pricing Tests
# =====================================================================

def test_cost_breakdown_with_cache_and_reasoning_included():
    # Reasoning tokens included in output rate, cached input discounted
    snapshot = {
        "input_price_per_1m": 2.0,
        "output_price_per_1m": 10.0,
        "cache_read_price_per_1m": 0.5,
    }
    # 1000 input total, 800 cached, 200 uncached. 500 output total (200 reasoning).
    usage, _ = normalize_usage({
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "prompt_tokens_details": {"cached_tokens": 800},
        "completion_tokens_details": {"reasoning_tokens": 200},
    })
    cb = calculate_cost_breakdown(snapshot, usage)

    assert cb.cost_available is True
    # Uncached input: 200 * 2.0 / 1M = 0.0004
    assert cb.uncached_input_cost == pytest.approx(0.0004)
    # Cache read: 800 * 0.5 / 1M = 0.0004
    assert cb.cache_read_cost == pytest.approx(0.0004)
    # Total input: 0.0004 + 0.0004 = 0.0008
    assert cb.input_cost == pytest.approx(0.0008)
    # Output cost: 500 * 10.0 / 1M = 0.0050
    assert cb.output_cost == pytest.approx(0.0050)
    # Reasoning cost is 0.0 because it's billed at standard output rate (no double billing)
    assert cb.reasoning_cost == 0.0
    # Total cost = 0.0008 + 0.0050 = 0.0058
    assert cb.total_cost == pytest.approx(0.0058)


def test_cost_breakdown_with_separate_reasoning_rate():
    # If a model specifies a distinct reasoning_price_per_1m
    snapshot = {
        "input_price_per_1m": 2.0,
        "output_price_per_1m": 8.0,
        "reasoning_price_per_1m": 12.0,
    }
    usage, _ = normalize_usage({
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "completion_tokens_details": {"reasoning_tokens": 200},
    })
    cb = calculate_cost_breakdown(snapshot, usage)

    assert cb.cost_available is True
    # Standard output: (500 - 200) = 300 * 8.0 / 1M = 0.0024
    # Reasoning: 200 * 12.0 / 1M = 0.0024
    # Total output + reasoning = 0.0048
    assert cb.reasoning_cost == pytest.approx(0.0024)
    assert cb.output_cost == pytest.approx(0.0024)
    assert cb.input_cost == pytest.approx(0.0020)
    assert cb.total_cost == pytest.approx(0.0068)


def test_cost_breakdown_missing_pricing_rates():
    snapshot = {
        "input_price_per_1m": 2.0,
        "output_price_per_1m": None,
    }
    usage, _ = normalize_usage({
        "prompt_tokens": 1000,
        "completion_tokens": 500,
    })
    cb = calculate_cost_breakdown(snapshot, usage)
    assert cb.cost_available is False
    assert cb.total_cost is None


# =====================================================================
# 3. OpenRouter Model Normalization Tests
# =====================================================================

def test_normalize_openrouter_pricing():
    raw_item = {
        "id": "anthropic/claude-3.5-sonnet",
        "name": "Anthropic: Claude 3.5 Sonnet",
        "context_length": 200000,
        "pricing": {
            "prompt": "0.000003",
            "completion": "0.000015",
            "input_cache_read": "0.0000003",
            "input_cache_write": "0.00000375",
        },
        "architecture": {
            "modality": "text+image->text",
        },
        "description": "Claude 3.5 Sonnet",
    }
    item = normalize_openrouter_model(raw_item)

    assert item.input_price_per_1m == pytest.approx(3.0)
    assert item.output_price_per_1m == pytest.approx(15.0)
    assert item.cache_read_price_per_1m == pytest.approx(0.3)
    assert item.cache_write_price_per_1m == pytest.approx(3.75)
    assert item.raw_pricing == raw_item["pricing"]
    assert item.pricing_source == "openrouter"


def test_normalize_openrouter_missing_pricing():
    raw_item = {
        "id": "custom/mystery-model",
        "name": "Mystery Model",
    }
    item = normalize_openrouter_model(raw_item)
    # Never invent 0.0 or guessed rates
    assert item.input_price_per_1m is None
    assert item.output_price_per_1m is None
    assert item.cache_read_price_per_1m is None
    assert item.cache_write_price_per_1m is None


# =====================================================================
# 4. Pricing Snapshot Immutability
# =====================================================================

def test_pricing_snapshot_capture_extended_fields():
    model = {
        "input_price_per_1m": 1.5,
        "output_price_per_1m": 4.5,
        "cache_read_price_per_1m": 0.35,
        "cache_write_price_per_1m": 1.75,
        "reasoning_price_per_1m": 4.5,
        "pricing_source": "MANUAL",
        "pricing_updated_at": "2026-09-13T00:00:00Z",
    }
    snap = pricing_snapshot(model)
    assert snap["input_price_per_1m"] == 1.5
    assert snap["output_price_per_1m"] == 4.5
    assert snap["cache_read_price_per_1m"] == 0.35
    assert snap["cache_write_price_per_1m"] == 1.75
    assert snap["reasoning_price_per_1m"] == 4.5
    assert snap["pricing_source"] == "MANUAL"

    # Mutating the original dict does not affect snapshot
    model["input_price_per_1m"] = 999.0
    assert snap["input_price_per_1m"] == 1.5


# =====================================================================
# 5. Blind Run Projection Safe Telemetry
# =====================================================================

def test_blind_run_view_preserves_tokens_and_strips_identity():
    from app.services.trace_views import _blind_run_view

    run = {
        "id": "run-123",
        "model_id": "secret-model-id",
        "model_name": "Claude 3.5 Sonnet",
        "provider_name": "Anthropic",
        "status": "completed",
        "latency_ms": 1200,
        "ttft_ms": 300,
        "input_tokens": 1000,
        "output_tokens": 500,
        "reasoning_tokens": 200,
        "cache_read_tokens": 800,
        "cache_write_tokens": 100,
        "uncached_input_tokens": 200,
        "total_tokens": 1500,
        "finish_reason": "stop",
    }
    blind = _blind_run_view(run)

    # Identity must be stripped
    assert "model_id" not in blind
    assert "model_name" not in blind
    assert "provider_name" not in blind

    # Safe numeric metrics must be preserved
    assert blind["id"] == "run-123"
    assert blind["input_tokens"] == 1000
    assert blind["output_tokens"] == 500
    assert blind["reasoning_tokens"] == 200
    assert blind["cache_read_tokens"] == 800
    assert blind["cache_write_tokens"] == 100
    assert blind["uncached_input_tokens"] == 200
    assert blind["total_tokens"] == 1500


# =====================================================================
# 6. Observability Run Detail Serialization
# =====================================================================

def test_observability_run_detail_telemetry():
    from app.routers.observability import _run_detail

    run = {
        "id": "run-obs",
        "owner_id": "user-1",
        "model_id": "model-1",
        "model_name": "DeepSeek R1",
        "provider_name": "OpenRouter",
        "status": "completed",
        "latency_ms": 1500,
        "ttft_ms": 400,
        "input_tokens": 1200,
        "output_tokens": 600,
        "reasoning_tokens": 300,
        "cache_read_tokens": 400,
        "cache_write_tokens": 200,
        "cache_creation_tokens": 200,
        "uncached_input_tokens": 800,
        "total_tokens": 1800,
        "reasoning_content": "Chain of thought step 1\nStep 2",
        "reasoning_summary": "Thinking finished",
        "token_usage": {
            "input_tokens": 1200,
            "output_tokens": 600,
            "reasoning_tokens": 300,
            "cache_read_tokens": 400,
            "uncached_input_tokens": 800,
            "total_tokens": 1800,
        },
        "cache_telemetry": {
            "cache_supported": True,
            "cache_read_tokens": 400,
            "uncached_input_tokens": 800,
            "cache_hit_rate": 0.333333,
        },
        "reasoning_telemetry": {
            "available": True,
            "content": "Chain of thought step 1\nStep 2",
            "summary": "Thinking finished",
            "tokens": 300,
            "streamed": True,
        },
        "cost_breakdown": {
            "cost_available": True,
            "input_cost": 0.0016,
            "uncached_input_cost": 0.0016,
            "cache_read_cost": 0.0,
            "output_cost": 0.0048,
            "reasoning_cost": 0.0,
            "total_cost": 0.0064,
        },
    }
    detail = _run_detail(run)

    assert detail["reasoning_content"] == "Chain of thought step 1\nStep 2"
    assert detail["reasoning_summary"] == "Thinking finished"
    assert detail["cache_read_tokens"] == 400
    assert detail["uncached_input_tokens"] == 800
    assert detail["token_usage"]["reasoning_tokens"] == 300
    assert detail["cache_telemetry"]["cache_hit_rate"] == pytest.approx(0.333333)
    assert detail["cost_breakdown"]["total_cost"] == 0.0064
