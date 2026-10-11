"""Provider telemetry normalizer for reasoning, tokens, and prompt caching.

Strict invariants:
1. NEVER FABRICATE TELEMETRY:
   OmniLab represents three distinct states correctly:
   - VALUE = 0
   - UNKNOWN
   - NOT_REPORTED (represented as None)
   If a provider does not report cache tokens or reasoning tokens, they remain None.
2. NEVER FABRICATE REASONING:
   Never reconstruct hidden chain-of-thought or infer private reasoning.
   Only capture reasoning explicitly exposed by the provider API.
3. DOUBLE-COUNTING PREVENTION:
   Understand provider semantics (e.g. whether reasoning is included inside
   output tokens or separate) so token totals and billing do not double-count.
"""
from typing import Any, Dict, Optional, Tuple

from ..models import CacheTelemetry, ReasoningTelemetry, TokenUsage


def parse_non_negative_int(value: Any) -> Optional[int]:
    """Parse an integer value >= 0.

    Distinguishes explicit 0 from None/invalid. Returns None if value is None,
    a boolean, or cannot be parsed as a non-negative integer.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def extract_reasoning_from_choice(
    choice_message: Optional[Dict[str, Any]],
    raw_delta: Optional[Dict[str, Any]] = None,
    reasoning_tokens: Optional[int] = None,
    streamed: bool = False,
    source: Optional[str] = None,
) -> ReasoningTelemetry:
    """Normalize provider reasoning metadata without fabricating text."""
    content: Optional[str] = None
    summary: Optional[str] = None
    raw_reasoning: Dict[str, Any] = {}

    msg = choice_message or {}
    delta = raw_delta or {}

    # Check for direct reasoning fields in message or delta
    for container in (msg, delta):
        if not isinstance(container, dict):
            continue
        for key in (
            "reasoning_content",
            "reasoning",
            "reasoning_text",
            "thought",
            "thinking",
        ):
            val = container.get(key)
            if isinstance(val, str) and val.strip():
                content = val
                raw_reasoning[key] = val
                break
        for key in ("reasoning_summary", "thought_summary"):
            val = container.get(key)
            if isinstance(val, str) and val.strip():
                summary = val
                raw_reasoning[key] = val
                break
        for key in ("reasoning_details", "reasoning_metadata"):
            val = container.get(key)
            if isinstance(val, dict) and val:
                raw_reasoning[key] = val

    tokens = parse_non_negative_int(reasoning_tokens)
    if tokens is None and isinstance(msg.get("reasoning_tokens"), (int, float, str)):
        tokens = parse_non_negative_int(msg.get("reasoning_tokens"))

    available = bool((content is not None and content != "") or (summary is not None) or (tokens is not None))

    return ReasoningTelemetry(
        available=available,
        content=content,
        summary=summary,
        tokens=tokens,
        streamed=streamed,
        source=source,
        raw=raw_reasoning,
    )


def normalize_usage(
    raw_usage: Any,
    provider_type: str = "openai_compatible",
    provider_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[TokenUsage, CacheTelemetry]:
    """Normalize raw provider usage payload into canonical TokenUsage and CacheTelemetry."""
    if not isinstance(raw_usage, dict):
        # When provider did not report usage, all metrics stay None (NOT_REPORTED)
        empty_usage = TokenUsage(raw_usage=raw_usage if isinstance(raw_usage, dict) else None)
        empty_cache = CacheTelemetry(cache_supported=None)
        return empty_usage, empty_cache

    prompt_details = raw_usage.get("prompt_tokens_details") or {}
    completion_details = raw_usage.get("completion_tokens_details") or {}

    # Core token counts
    input_tokens = parse_non_negative_int(
        raw_usage.get("prompt_tokens")
        if "prompt_tokens" in raw_usage
        else raw_usage.get("input_tokens")
    )
    output_tokens = parse_non_negative_int(
        raw_usage.get("completion_tokens")
        if "completion_tokens" in raw_usage
        else raw_usage.get("output_tokens")
    )
    total_tokens = parse_non_negative_int(raw_usage.get("total_tokens"))

    # Reasoning tokens
    reasoning_tokens = parse_non_negative_int(
        completion_details.get("reasoning_tokens")
        if isinstance(completion_details, dict) and "reasoning_tokens" in completion_details
        else raw_usage.get("reasoning_tokens")
    )

    # Cache read tokens:
    # Look for OpenAI cached_tokens, OpenRouter cache_read_input_tokens, or direct cache_read_tokens
    cache_read_tokens: Optional[int] = None
    if isinstance(prompt_details, dict) and "cached_tokens" in prompt_details:
        cache_read_tokens = parse_non_negative_int(prompt_details.get("cached_tokens"))
    elif "cached_tokens" in raw_usage:
        cache_read_tokens = parse_non_negative_int(raw_usage.get("cached_tokens"))
    elif "cache_read_input_tokens" in raw_usage:
        cache_read_tokens = parse_non_negative_int(raw_usage.get("cache_read_input_tokens"))
    elif "cache_read_tokens" in raw_usage:
        cache_read_tokens = parse_non_negative_int(raw_usage.get("cache_read_tokens"))

    # Cache write / creation tokens:
    cache_write_tokens: Optional[int] = None
    if isinstance(prompt_details, dict) and "cache_creation_tokens" in prompt_details:
        cache_write_tokens = parse_non_negative_int(prompt_details.get("cache_creation_tokens"))
    elif isinstance(prompt_details, dict) and "cache_creation_input_tokens" in prompt_details:
        cache_write_tokens = parse_non_negative_int(prompt_details.get("cache_creation_input_tokens"))
    elif isinstance(prompt_details, dict) and "cache_write_tokens" in prompt_details:
        cache_write_tokens = parse_non_negative_int(prompt_details.get("cache_write_tokens"))
    elif "cache_creation_input_tokens" in raw_usage:
        cache_write_tokens = parse_non_negative_int(raw_usage.get("cache_creation_input_tokens"))
    elif "cache_write_tokens" in raw_usage:
        cache_write_tokens = parse_non_negative_int(raw_usage.get("cache_write_tokens"))
    elif "cache_creation_tokens" in raw_usage:
        cache_write_tokens = parse_non_negative_int(raw_usage.get("cache_creation_tokens"))

    cache_creation_tokens = cache_write_tokens

    # Audio & image tokens if reported
    audio_tokens = parse_non_negative_int(
        (prompt_details.get("audio_tokens") if isinstance(prompt_details, dict) else None)
        or raw_usage.get("audio_tokens")
    )
    image_tokens = parse_non_negative_int(
        (prompt_details.get("image_tokens") if isinstance(prompt_details, dict) else None)
        or raw_usage.get("image_tokens")
    )

    # Uncached input tokens:
    # If input_tokens is total prompt tokens and cache_read_tokens is known:
    uncached_input_tokens: Optional[int] = None
    if "uncached_input_tokens" in raw_usage:
        uncached_input_tokens = parse_non_negative_int(raw_usage.get("uncached_input_tokens"))
    elif input_tokens is not None and cache_read_tokens is not None:
        uncached_input_tokens = max(0, input_tokens - cache_read_tokens)
    elif input_tokens is not None and cache_read_tokens is None:
        # If cache was never reported, uncached_input_tokens is not reported (None),
        # preserving the rule not to assume cache = 0.
        uncached_input_tokens = None

    # Total tokens calculation if provider did not report total_tokens:
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        # Standard OpenAI/OpenRouter semantics: completion_tokens already includes reasoning_tokens
        total_tokens = input_tokens + output_tokens

    # Cache hit rate:
    # ONLY when mathematically supported by reported values.
    cache_hit_rate: Optional[float] = None
    cache_supported: Optional[bool] = None
    if cache_read_tokens is not None or cache_write_tokens is not None:
        cache_supported = True
        total_prompt = input_tokens
        if total_prompt is not None and total_prompt > 0 and cache_read_tokens is not None:
            cache_hit_rate = round(float(cache_read_tokens) / float(total_prompt), 4)
        elif total_prompt == 0 and cache_read_tokens == 0:
            cache_hit_rate = 0.0

    token_usage = TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_creation_tokens=cache_creation_tokens,
        uncached_input_tokens=uncached_input_tokens,
        total_tokens=total_tokens,
        audio_tokens=audio_tokens,
        image_tokens=image_tokens,
        raw_usage=raw_usage,
    )

    cache_telemetry = CacheTelemetry(
        cache_supported=cache_supported,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_creation_tokens=cache_creation_tokens,
        uncached_input_tokens=uncached_input_tokens,
        cache_hit_rate=cache_hit_rate,
    )

    return token_usage, cache_telemetry
