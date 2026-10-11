"""Immutable pricing snapshots and cost estimation.

Pricing is captured *before* execution so a later edit to a model record can
never reprice an already-recorded Run. Cost is only ever derived from the
snapshot plus token counts that the provider actually reported; unknown usage
and unknown rates stay ``None`` and are never coerced to zero.

Explicit billing semantics prevent double-counting of reasoning tokens or
cached input tokens.
"""
from typing import Any, Dict, Optional, Union

from ..models import CostBreakdown, now_iso


def _pricing_rate(model: Dict[str, Any], *keys: str) -> Optional[float]:
    """Read the explicit rate first, while accepting existing model records."""
    if not isinstance(model, dict):
        return None
    for key in keys:
        value = model.get(key)
        if value is not None:
            try:
                num = float(value)
                if num >= 0:
                    return num
            except (TypeError, ValueError):
                pass
    return None


def pricing_snapshot(model: Dict[str, Any], captured_at: Optional[str] = None) -> Dict[str, Any]:
    """Copy pricing metadata before execution so later model edits cannot reprice it."""
    input_price = _pricing_rate(
        model, "input_price_per_1m", "input_price_per_1m_tokens", "input_cost"
    )
    output_price = _pricing_rate(
        model, "output_price_per_1m", "output_price_per_1m_tokens", "output_cost"
    )
    snap: Dict[str, Any] = {
        "input_price_per_1m": input_price,
        "output_price_per_1m": output_price,
        "pricing_source": model.get("pricing_source"),
        "pricing_updated_at": model.get("pricing_updated_at"),
        "captured_at": captured_at or now_iso(),
        "currency": model.get("currency") or model.get("pricing_currency") or "USD",
        "unit": model.get("unit") or model.get("pricing_unit") or "per_1m_tokens",
    }

    # Extended optional cache & reasoning fields
    cache_read = _pricing_rate(model, "cache_read_price_per_1m", "cache_read_per_1m", "cache_read_cost")
    if cache_read is not None or "cache_read_price_per_1m" in model or "cache_read_per_1m" in model:
        snap["cache_read_price_per_1m"] = cache_read
        snap["cache_read_per_1m"] = cache_read

    cache_write = _pricing_rate(model, "cache_write_price_per_1m", "cache_write_per_1m", "cache_write_cost")
    if cache_write is not None or "cache_write_price_per_1m" in model or "cache_write_per_1m" in model:
        snap["cache_write_price_per_1m"] = cache_write
        snap["cache_write_per_1m"] = cache_write

    cache_creation = _pricing_rate(model, "cache_creation_price_per_1m", "cache_creation_per_1m")
    if cache_creation is not None or "cache_creation_price_per_1m" in model:
        snap["cache_creation_price_per_1m"] = cache_creation

    reasoning_price = _pricing_rate(model, "reasoning_price_per_1m", "reasoning_per_1m", "reasoning_cost")
    if reasoning_price is not None or "reasoning_price_per_1m" in model or "reasoning_per_1m" in model:
        snap["reasoning_price_per_1m"] = reasoning_price
        snap["reasoning_per_1m"] = reasoning_price

    if "provider" in model or "provider_name" in model:
        snap["provider"] = model.get("provider") or model.get("provider_name") or model.get("provider_id")
    if "source" in model:
        snap["source"] = model["source"]
    if "fetched_at" in model:
        snap["fetched_at"] = model["fetched_at"]
    if "raw_pricing" in model and isinstance(model["raw_pricing"], dict):
        snap["raw_pricing"] = model["raw_pricing"]

    return snap



# Internal alias
_pricing_snapshot = pricing_snapshot


def calculate_cost_breakdown(
    pricing: Optional[Dict[str, Any]],
    usage: Optional[Dict[str, Any]],
    reasoning_included_in_output: bool = True,
) -> CostBreakdown:
    """Calculate explicit cost components without double billing.

    Rules:
    - If a component has > 0 tokens but missing rate, that component cost is None.
    - If reasoning is included in output billing and there's no separate reasoning rate,
      output is billed once and reasoning cost is 0.0 (or included).
    - If cached tokens exist and have a distinct cache_read rate, bill cache read tokens
      at cache_read_price_per_1m and uncached input at input_price_per_1m.
    - If all required rates are present, total_cost is the sum of known components.
    """
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()

    if not isinstance(pricing, dict) or not isinstance(usage, dict):
        return CostBreakdown(cost_available=False)

    input_rate = _pricing_rate(pricing, "input_price_per_1m", "input_price_per_1m_tokens", "input_cost")
    output_rate = _pricing_rate(pricing, "output_price_per_1m", "output_price_per_1m_tokens", "output_cost")
    cache_read_rate = _pricing_rate(pricing, "cache_read_price_per_1m", "cache_read_per_1m", "cache_read_cost")
    cache_write_rate = _pricing_rate(
        pricing, "cache_write_price_per_1m", "cache_write_per_1m", "cache_write_cost",
        "cache_creation_price_per_1m", "cache_creation_per_1m"
    )
    reasoning_rate = _pricing_rate(pricing, "reasoning_price_per_1m", "reasoning_per_1m", "reasoning_cost")

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    reasoning_tokens = usage.get("reasoning_tokens")
    cache_read_tokens = usage.get("cache_read_tokens")
    if cache_read_tokens is None:
        cache_read_tokens = usage.get("cached_tokens")
    cache_write_tokens = usage.get("cache_write_tokens")
    if cache_write_tokens is None:
        cache_write_tokens = usage.get("cache_creation_tokens")

    uncached_input_tokens = usage.get("uncached_input_tokens")
    if uncached_input_tokens is None and input_tokens is not None:
        if cache_read_tokens is not None and cache_read_rate is not None:
            uncached_input_tokens = max(0, input_tokens - cache_read_tokens)
        else:
            uncached_input_tokens = input_tokens

    # 1. Uncached input cost
    uncached_input_cost: Optional[float] = None
    if uncached_input_tokens is not None:
        if uncached_input_tokens == 0:
            uncached_input_cost = 0.0
        elif input_rate is not None:
            uncached_input_cost = round((uncached_input_tokens / 1_000_000) * input_rate, 6)
        else:
            uncached_input_cost = None

    # 2. Cache read cost
    cache_read_cost: Optional[float] = None
    if cache_read_tokens is not None and cache_read_rate is not None:
        if cache_read_tokens == 0:
            cache_read_cost = 0.0
        else:
            cache_read_cost = round((cache_read_tokens / 1_000_000) * cache_read_rate, 6)
    else:
        cache_read_cost = None

    # 3. Cache write cost
    cache_write_cost: Optional[float] = None
    if cache_write_tokens is not None and cache_write_rate is not None:
        if cache_write_tokens == 0:
            cache_write_cost = 0.0
        else:
            cache_write_cost = round((cache_write_tokens / 1_000_000) * cache_write_rate, 6)
    else:
        cache_write_cost = None

    # 4. Output and Reasoning costs
    output_cost: Optional[float] = None
    reasoning_cost: Optional[float] = None

    if output_tokens is not None:
        if output_tokens == 0:
            output_cost = 0.0
            if reasoning_tokens is not None and reasoning_tokens == 0:
                reasoning_cost = 0.0
        else:
            if reasoning_tokens is not None and reasoning_tokens > 0:
                if reasoning_rate is not None:
                    # Explicit separate reasoning price
                    reasoning_cost = round((reasoning_tokens / 1_000_000) * reasoning_rate, 6)
                    regular_output = max(0, output_tokens - reasoning_tokens) if reasoning_included_in_output else output_tokens
                    if regular_output == 0:
                        output_cost = 0.0
                    elif output_rate is not None:
                        output_cost = round((regular_output / 1_000_000) * output_rate, 6)
                    else:
                        output_cost = None
                else:
                    # Reasoning billed at regular output rate (or included in output)
                    if output_rate is not None:
                        output_cost = round((output_tokens / 1_000_000) * output_rate, 6)
                        reasoning_cost = 0.0  # Included in output billing
                    else:
                        output_cost = None
                        reasoning_cost = None
            else:
                if reasoning_tokens == 0:
                    reasoning_cost = 0.0
                if output_rate is not None:
                    output_cost = round((output_tokens / 1_000_000) * output_rate, 6)
                else:
                    output_cost = None

    # 5. Total cost evaluation
    # Total cost is valid ONLY if every non-zero token component has a known cost
    # and at least one pricing rate is defined.
    total_cost: Optional[float] = None
    if input_tokens is None or output_tokens is None:
        total_cost = None
    elif input_rate is None and output_rate is None and cache_read_rate is None:
        total_cost = None
    else:
        # Check all applicable components
        components = []
        is_complete = True

        # Input component check
        if uncached_input_tokens and uncached_input_tokens > 0 and uncached_input_cost is None:
            is_complete = False
        elif uncached_input_cost is not None:
            components.append(uncached_input_cost)

        # Cache read check
        if cache_read_rate is not None and cache_read_tokens and cache_read_tokens > 0 and cache_read_cost is None:
            is_complete = False
        elif cache_read_cost is not None:
            components.append(cache_read_cost)

        # Cache write check
        if cache_write_rate is not None and cache_write_tokens and cache_write_tokens > 0 and cache_write_cost is None:
            is_complete = False
        elif cache_write_cost is not None:
            components.append(cache_write_cost)

        # Output check
        if output_tokens and output_tokens > 0 and output_cost is None:
            is_complete = False
        elif output_cost is not None:
            components.append(output_cost)

        # Reasoning check
        if reasoning_tokens and reasoning_tokens > 0 and reasoning_cost is None:
            is_complete = False
        elif reasoning_cost is not None:
            components.append(reasoning_cost)

        if is_complete and (components or (input_tokens == 0 and output_tokens == 0)):
            total_cost = round(sum(components), 6)
        else:
            total_cost = None

    input_cost: Optional[float] = None
    if uncached_input_cost is not None or cache_read_cost is not None:
        parts = [c for c in (uncached_input_cost, cache_read_cost) if c is not None]
        input_cost = round(sum(parts), 6)

    return CostBreakdown(
        cost_available=bool(total_cost is not None),
        input_cost=input_cost,
        uncached_input_cost=uncached_input_cost,
        cache_read_cost=cache_read_cost,
        cache_write_cost=cache_write_cost,
        cache_creation_cost=cache_write_cost,
        output_cost=output_cost,
        reasoning_cost=reasoning_cost,
        total_cost=total_cost,
    )


def estimate_cost(
    model_or_pricing: Dict[str, Any],
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    cached_tokens: Optional[int] = None,
    reasoning_tokens: Optional[int] = None,
    cache_read_tokens: Optional[int] = None,
    cache_write_tokens: Optional[int] = None,
    uncached_input_tokens: Optional[int] = None,
) -> Optional[float]:
    """Estimate cost adhering to the requirement of knowing every non-zero component.

    Supports legacy (input_tokens, output_tokens) calls while factoring in
    cache and reasoning when supplied.
    """
    c_read = cache_read_tokens if cache_read_tokens is not None else cached_tokens
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": c_read,
        "cached_tokens": c_read,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
        "uncached_input_tokens": uncached_input_tokens,
    }
    breakdown = calculate_cost_breakdown(model_or_pricing, usage)
    return breakdown.total_cost
