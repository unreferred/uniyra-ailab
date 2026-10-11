"""Provider execution abstraction.

A single, generic execution surface used by Workspace, Arena, Judge (and later
Council). Feature code never talks to a specific vendor SDK — it builds an
`ExecutionRequest`, resolves the model's provider, and calls `execute()`.

One adapter is implemented in V1:
  * openai_compatible   — any OpenAI-compatible /chat/completions endpoint (BYOK)

Adding OpenRouter / Anthropic-native / Ollama later means adding an adapter
here; no feature code changes.
"""
import json
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from . import config
from .errors import classify_exception, classify_http_status, make_error
from .models import ProviderModelCatalogItem
from .security import decrypt_secret
from .services.telemetry_normalizer import extract_reasoning_from_choice, normalize_usage
from .urlguard import UrlValidationError, async_validate_provider_url


@dataclass
class ExecutionRequest:
    system_prompt: str
    messages: List[Dict[str, Any]]  # [{role, content}] excluding system
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    seed: Optional[int] = None
    reasoning_effort: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[str] = None


@dataclass
class ExecutionResult:
    status: str                      # success | error
    text: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: int = 0
    ttft_ms: Optional[int] = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    error_detail: Optional[dict] = None
    cached_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    cache_creation_tokens: Optional[int] = None
    uncached_input_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    reasoning_content: Optional[str] = None
    reasoning_summary: Optional[str] = None
    reasoning_details: Optional[dict] = None
    token_usage: Optional[Dict[str, Any]] = None
    cache_telemetry: Optional[Dict[str, Any]] = None
    reasoning_telemetry: Optional[Dict[str, Any]] = None
    streaming: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)


def _params_dict(req: ExecutionRequest) -> Dict[str, Any]:
    p: Dict[str, Any] = {}
    if req.temperature is not None:
        p["temperature"] = req.temperature
    if req.max_tokens is not None:
        p["max_tokens"] = req.max_tokens
    if req.top_p is not None:
        p["top_p"] = req.top_p
    if req.seed is not None:
        p["seed"] = req.seed
    if req.reasoning_effort is not None:
        p["reasoning_effort"] = req.reasoning_effort
    return p


class ProviderDiscoveryError(Exception):
    """Safe error raised when a provider catalog cannot be retrieved."""

    def __init__(self, message: str, status_code: int = 502, code: str = "PROVIDER_DISCOVERY_FAILED"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


# Compatibility name for callers that use the more explicit terminology.
ProviderModelDiscoveryError = ProviderDiscoveryError


class _StreamOptionsRejected(Exception):
    """Internal signal: provider rejected ``stream_options`` before any content."""


def _is_openrouter_base(base_url: str) -> bool:
    hostname = (urlparse(base_url).hostname or "").lower().rstrip(".")
    return hostname == "openrouter.ai" or hostname.endswith(".openrouter.ai")


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _numeric_price(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number * 1_000_000


def _usage_count(usage: Any, key: str) -> Optional[int]:
    if not isinstance(usage, dict) or key not in usage or usage[key] is None:
        return None
    try:
        value = int(usage[key])
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _usage_detail_count(usage: Any, container_key: str, detail_key: str) -> Optional[int]:
    if not isinstance(usage, dict):
        return None
    container = usage.get(container_key)
    if not isinstance(container, dict):
        return None
    return _usage_count(container, detail_key)


def _attr_int(obj: Any, name: str) -> Optional[int]:
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _new_async_client(timeout: float) -> httpx.AsyncClient:
    """Create an httpx client that never follows redirects.

    ``follow_redirects`` is assigned after construction so lightweight test
    doubles that only accept ``timeout`` keep working while real clients still
    refuse redirects (matching httpx's own secure default explicitly).
    """
    client = httpx.AsyncClient(timeout=timeout)
    client.follow_redirects = False
    return client


def _connection_timeout(default: float) -> float:
    """Provider connection timeout, overridable via config when present."""
    try:
        return float(getattr(config, "PROVIDER_CONNECTION_TIMEOUT_SECONDS", default))
    except (TypeError, ValueError):
        return default


def _delta_text(content: Any) -> str:
    """Concatenate text from a streaming delta content value.

    Handles a plain string and a list of content parts where each part may be
    ``{"type": "text", "text": ...}``. Non-text parts (e.g. images) are ignored
    rather than stringified so they never leak into the visible output.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: List[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type not in (None, "text"):
                continue
            text = part.get("text")
            if isinstance(text, str):
                pieces.append(text)
        return "".join(pieces)
    return ""


def _parse_stream_chunk(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse one decoded SSE chunk into ordered streaming delta events.

    Pure and side-effect free so it can be unit-tested without a network or a
    database. Tool-call arguments are deliberately left unparsed here; the
    canonical execution loop owns accumulation across chunks.
    """
    events: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return events
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return events
    choice = choices[0]
    if not isinstance(choice, dict):
        return events
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        delta = {}

    reasoning_text = _delta_text(
        delta.get("reasoning_content")
        or delta.get("reasoning")
        or delta.get("reasoning_text")
        or delta.get("thought")
        or delta.get("thinking")
    )
    if reasoning_text:
        events.append({
            "type": "MODEL_REASONING_DELTA",
            "delta": "",
            "reasoning_delta": reasoning_text,
            "done": False,
        })

    content_text = _delta_text(delta.get("content"))
    if content_text:
        events.append({
            "type": "MODEL_OUTPUT_DELTA",
            "delta": content_text,
            "done": False,
            "ttft_ms": None,
        })

    legacy_function_call = delta.get("function_call")
    if isinstance(legacy_function_call, dict):
        legacy_name = legacy_function_call.get("name")
        legacy_arguments = legacy_function_call.get("arguments")
        if legacy_name or legacy_arguments:
            events.append({
                "type": "MODEL_TOOL_CALL_DELTA",
                "index": 0,
                "id": None,
                "name": legacy_name or None,
                "arguments_delta": _normalize_tool_arguments(legacy_arguments),
                "done": False,
            })

    tool_call_fragments = delta.get("tool_calls")
    if isinstance(tool_call_fragments, list):
        for position, fragment in enumerate(tool_call_fragments):
            if not isinstance(fragment, dict):
                continue
            function = fragment.get("function") if isinstance(fragment.get("function"), dict) else {}
            fragment_id = fragment.get("id")
            name = fragment.get("name") or function.get("name")
            arguments = fragment.get("arguments")
            if arguments is None:
                arguments = function.get("arguments")
            if not (fragment_id or name or arguments):
                continue
            index = fragment.get("index")
            if index is None:
                index = position
            events.append({
                "type": "MODEL_TOOL_CALL_DELTA",
                "index": index,
                "id": fragment_id,
                "name": name or None,
                "arguments_delta": _normalize_tool_arguments(arguments),
                "done": False,
            })
    return events


def _accumulate_stream_tool_calls(
    accumulator: Dict[Any, Dict[str, Any]],
    events: List[Dict[str, Any]],
) -> Dict[Any, Dict[str, Any]]:
    """Accumulate fragmented tool-call deltas keyed by fragment index (pure)."""
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "MODEL_TOOL_CALL_DELTA":
            continue
        index = event.get("index")
        if index is None:
            index = 0
        slot = accumulator.get(index)
        if slot is None:
            slot = {"id": None, "name": None, "arguments": ""}
            accumulator[index] = slot
        call_id = event.get("id")
        if call_id and not slot["id"]:
            slot["id"] = call_id
        name = event.get("name")
        if name:
            slot["name"] = name if slot["name"] is None else slot["name"] + name
        arguments_delta = event.get("arguments_delta")
        if isinstance(arguments_delta, str) and arguments_delta:
            slot["arguments"] += arguments_delta
    return accumulator


def _tool_call_sort_key(index: Any):
    if isinstance(index, int) and not isinstance(index, bool):
        return (0, index, "")
    return (1, 0, str(index))


def _finalized_stream_tool_calls(
    accumulator: Dict[Any, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Assemble accumulated fragments into ordered OpenAI-shaped tool calls."""
    calls: List[Dict[str, Any]] = []
    for index in sorted(accumulator, key=_tool_call_sort_key):
        slot = accumulator[index]
        arguments = slot.get("arguments") or ""
        calls.append({
            "id": slot.get("id"),
            "type": "function",
            "name": slot.get("name"),
            "arguments": arguments,
            "arguments_raw": arguments,
        })
    return calls


def _openai_tools(req: ExecutionRequest) -> List[Dict[str, Any]]:
    """Build the OpenAI ``tools`` payload shared by execute() and stream()."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters")
                or tool.get("input_schema")
                or {"type": "object", "properties": {}},
            },
        }
        for tool in req.tools or []
        if isinstance(tool, dict)
    ]


def _stream_terminal_event(
    usage: Any,
    finish_reason: Optional[str],
    accumulated_reasoning: Optional[str] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    output_text: Optional[str] = None,
) -> dict:
    token_usage, cache_telemetry = normalize_usage(usage, provider_type="openai_compatible")
    reasoning_telemetry = extract_reasoning_from_choice(
        None,
        reasoning_tokens=token_usage.reasoning_tokens,
        streamed=True,
        source="openai_compatible",
    )
    if accumulated_reasoning:
        reasoning_telemetry.content = accumulated_reasoning
        reasoning_telemetry.available = True

    return {
        "type": "RUN_COMPLETE",
        "delta": "",
        "content": output_text or "",
        "done": True,
        "finish_reason": finish_reason,
        "usage": usage if isinstance(usage, dict) else None,
        "input_tokens": token_usage.input_tokens,
        "output_tokens": token_usage.output_tokens,
        "cached_tokens": token_usage.cache_read_tokens,
        "cache_read_tokens": token_usage.cache_read_tokens,
        "cache_write_tokens": token_usage.cache_write_tokens,
        "cache_creation_tokens": token_usage.cache_creation_tokens,
        "uncached_input_tokens": token_usage.uncached_input_tokens,
        "reasoning_tokens": token_usage.reasoning_tokens,
        "total_tokens": token_usage.total_tokens,
        "token_usage": token_usage.model_dump(),
        "cache_telemetry": cache_telemetry.model_dump(),
        "reasoning_telemetry": reasoning_telemetry.model_dump(),
        "reasoning_content": reasoning_telemetry.content,
        "tool_calls": tool_calls or [],
    }


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _context_window(value: Any) -> Optional[int]:
    return _positive_int(value)


def normalize_openrouter_model(item: Dict[str, Any]) -> Optional[ProviderModelCatalogItem]:
    """Convert one OpenRouter model object into a normalized catalog item.

    Tool-calling support is inferred ONLY from an explicit
    ``"tools"`` entry in the provider's ``supported_parameters`` list
    (surfaced to callers via ``model_capability_status(..., "tools")``);
    never from the model name, slug, or description. Raw provider signals
    (``supported_parameters``/``modality``/``modalities`` plus the
    allowlisted ``provider_metadata`` below) are preserved alongside the
    normalized view so provenance stays auditable; unknown/secret keys are
    dropped and never echoed.
    """
    identifier = item.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    modalities = {
        "input": _string_list(architecture.get("input_modalities")),
        "output": _string_list(architecture.get("output_modalities")),
    }
    top_level_modalities = item.get("modalities")
    if isinstance(top_level_modalities, dict):
        modalities["input"] = _string_list(top_level_modalities.get("input")) or modalities["input"]
        modalities["output"] = _string_list(top_level_modalities.get("output")) or modalities["output"]
    modalities = {key: value for key, value in modalities.items() if value}

    supported_parameters = _string_list(item.get("supported_parameters"))
    capabilities: Dict[str, Any] = {}
    if supported_parameters:
        capabilities["supported_parameters"] = supported_parameters
    architecture_modality = architecture.get("modality")
    if isinstance(architecture_modality, str) and architecture_modality:
        capabilities["modality"] = architecture_modality
    if modalities:
        capabilities["modalities"] = modalities

    # Raw provider signals preserved verbatim (separately from the
    # normalized ``capabilities`` above): the full supported_parameters
    # list, the raw architecture modality/modalities, and the allowlisted
    # identity keys. Nothing is inferred from names; secrets/unknown keys
    # are never retained.
    provider_metadata: Dict[str, Any] = {
        "source": "openrouter",
        "supported_parameters": list(supported_parameters),
    }
    if isinstance(architecture_modality, str) and architecture_modality:
        provider_metadata["architecture_modality"] = architecture_modality
    if modalities:
        provider_metadata["architecture_modalities"] = {k: list(v) for k, v in modalities.items()}
    for key in ("canonical_slug", "hugging_face_id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            provider_metadata[key] = value
    created = item.get("created")
    if isinstance(created, (int, float)) and not isinstance(created, bool):
        provider_metadata["created"] = created

    top_provider = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
    per_request_limits = item.get("per_request_limits") if isinstance(item.get("per_request_limits"), dict) else {}
    max_output_tokens = (
        _positive_int(top_provider.get("max_completion_tokens"))
        or _positive_int(per_request_limits.get("max_tokens"))
        or _positive_int(architecture.get("max_output_tokens"))
    )

    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    timestamp = datetime.now(timezone.utc).isoformat()
    return ProviderModelCatalogItem(
        model_identifier=identifier,
        display_name=item.get("name") if isinstance(item.get("name"), str) and item["name"] else identifier,
        context_window=_context_window(item.get("context_length")),
        max_output_tokens=max_output_tokens,
        input_price_per_1m=_numeric_price(pricing.get("prompt")),
        output_price_per_1m=_numeric_price(pricing.get("completion")),
        cache_read_price_per_1m=_numeric_price(
            pricing.get("input_cache_read") if "input_cache_read" in pricing else pricing.get("cache_read")
        ),
        cache_write_price_per_1m=_numeric_price(
            pricing.get("input_cache_write") if "input_cache_write" in pricing else pricing.get("cache_write")
        ),
        reasoning_price_per_1m=_numeric_price(
            pricing.get("reasoning") if "reasoning" in pricing else pricing.get("internal_reasoning")
        ),
        raw_pricing=dict(pricing) if pricing else None,
        capabilities=capabilities,
        modalities=modalities,
        provider_metadata=provider_metadata,
        pricing_source="openrouter",
        pricing_updated_at=timestamp,
    )


def normalize_openai_compatible_model(item: Dict[str, Any]) -> Optional[ProviderModelCatalogItem]:
    """Normalize a common OpenAI ``/models`` item without inventing metadata."""
    identifier = item.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    display_name = item.get("name")
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = identifier
    capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
    modalities = item.get("modalities") if isinstance(item.get("modalities"), dict) else {}
    clean_modalities = {
        key: _string_list(value)
        for key, value in modalities.items()
        if key in ("input", "output") and _string_list(value)
    }
    context = item.get("context_length")
    if context is None:
        context = item.get("context_window")
    max_output = (
        _positive_int(item.get("max_completion_tokens"))
        or _positive_int(item.get("max_output_tokens"))
        or _positive_int(item.get("max_tokens"))
        or _positive_int(capabilities.get("max_output_tokens"))
    )
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    return ProviderModelCatalogItem(
        model_identifier=identifier.strip(),
        display_name=display_name.strip(),
        context_window=_context_window(context),
        max_output_tokens=max_output,
        input_price_per_1m=_numeric_price(pricing.get("prompt")) if pricing else None,
        output_price_per_1m=_numeric_price(pricing.get("completion")) if pricing else None,
        capabilities=dict(capabilities),
        modalities=clean_modalities,
        provider_metadata={
            "source": "openai_compatible",
            **({"owned_by": item["owned_by"]} if isinstance(item.get("owned_by"), str) else {}),
        },
    )



def _capabilities(model: Any) -> Dict[str, Any]:
    return (model or {}).get("capabilities") or {}


def _modalities(model: Any) -> Dict[str, Any]:
    return (model or {}).get("modalities") or {}


def model_capability_status(model: Any, capability: str) -> str:
    """Return SUPPORTED / UNSUPPORTED / UNKNOWN for a model capability.

    Missing metadata is reported as UNKNOWN rather than guessed, so callers can
    decide whether to require an explicit capability declaration.
    """
    caps = _capabilities(model)
    modalities = _modalities(model)
    if capability == "vision":
        if caps.get("vision") is True:
            return "SUPPORTED"
        caps_modalities = caps.get("modalities") or {}
        if "image" in (caps_modalities.get("input") or []):
            return "SUPPORTED"
        if "image" in (modalities.get("input") or []):
            return "SUPPORTED"
        if caps.get("vision") is False:
            return "UNSUPPORTED"
        return "UNKNOWN"
    if capability == "tools":
        if caps.get("tools") is True:
            return "SUPPORTED"
        if "tools" in (caps.get("supported_parameters") or []):
            return "SUPPORTED"
        if caps.get("tools") is False:
            return "UNSUPPORTED"
        return "UNKNOWN"
    if capability == "streaming":
        if caps.get("streaming") is True or caps.get("stream") is True:
            return "SUPPORTED"
        if caps.get("streaming") is False or caps.get("stream") is False:
            return "UNSUPPORTED"
        return "UNKNOWN"
    return "UNKNOWN"


def supports_vision(provider: Dict[str, Any], model: Any) -> bool:
    """True only when the adapter and the model both declare image support."""
    try:
        adapter = get_adapter(provider)
    except ValueError:
        return False
    return bool(getattr(adapter, "supports_vision", False)) and model_capability_status(model, "vision") == "SUPPORTED"


def supports_tools(provider: Dict[str, Any], model: Any) -> bool:
    """True only when the adapter and the model both declare tool support."""
    try:
        adapter = get_adapter(provider)
    except ValueError:
        return False
    return bool(getattr(adapter, "supports_tools", False)) and model_capability_status(model, "tools") == "SUPPORTED"


def _stringify_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def _normalize_content_blocks(content: Any) -> Any:
    """Translate normalized content blocks into OpenAI chat content parts."""
    if not isinstance(content, list):
        return content
    parts: List[Dict[str, Any]] = []
    for block in content:
        if isinstance(block, str):
            parts.append({"type": "text", "text": block})
            continue
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            parts.append({"type": "text", "text": str(block.get("text") or "")})
        elif block_type == "image":
            data_url = block.get("data_url")
            if not data_url:
                data_base64 = block.get("data_base64")
                if data_base64:
                    mime = block.get("mime_type") or "image/png"
                    data_url = f"data:{mime};base64,{data_base64}"
            if data_url:
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
        elif block_type == "attachment_ref":
            descriptor = block.get("text") or block.get("filename") or "[attachment]"
            parts.append({"type": "text", "text": str(descriptor)})
        # Anything else is skipped safely rather than forwarded blindly.
    return parts


def _normalize_tool_arguments(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _openai_messages(system_prompt: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build OpenAI-shaped messages, keeping legacy string messages working."""
    built: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt or ""}]
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "user"
        content = message.get("content")
        tool_calls = message.get("tool_calls")

        if role == "tool":
            tool_message: Dict[str, Any] = {
                "role": "tool",
                "tool_call_id": message.get("tool_call_id"),
                "content": _stringify_content(content),
            }
            if message.get("name"):
                tool_message["name"] = message.get("name")
            built.append(tool_message)
            continue

        if tool_calls:
            normalized_calls: List[Dict[str, Any]] = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = call.get("name") or function.get("name")
                arguments = call.get("arguments")
                if arguments is None:
                    arguments = function.get("arguments")
                normalized_calls.append({
                    "id": call.get("id"),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": _normalize_tool_arguments(arguments),
                    },
                })
            built.append({
                "role": role,
                "content": content if content not in (None, "") else None,
                "tool_calls": normalized_calls,
            })
            continue

        if isinstance(content, list):
            built.append({"role": role, "content": _normalize_content_blocks(content)})
        else:
            built.append({"role": role, "content": content or ""})
    return built


def _parse_tool_calls(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize an OpenAI response message's tool calls, keeping the raw args."""
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    parsed_calls: List[Dict[str, Any]] = []
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        raw_arguments = function.get("arguments")
        arguments: Any = raw_arguments
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, ValueError):
                arguments = raw_arguments
        parsed_calls.append({
            "id": call.get("id"),
            "type": "function",
            "name": function.get("name"),
            "arguments": arguments,
            "arguments_raw": raw_arguments,
        })
    return parsed_calls


class ProviderAdapter:
    supports_model_discovery = False
    supports_tools = False
    supports_vision = False
    supports_reasoning_effort = False

    @property
    def capabilities(self) -> Dict[str, bool]:
        return {
            "model_discovery": bool(self.supports_model_discovery),
            "streaming": False,
            "output_stream": False,
            "reasoning_stream": False,
            "tool_streaming": False,
        }

    def __init__(self, provider: Dict[str, Any]):
        self.provider = provider

    async def discover_models(self) -> List[ProviderModelCatalogItem]:
        raise ProviderDiscoveryError("Model discovery is not supported for this provider", status_code=400)

    async def execute(self, model: Dict[str, Any], req: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError

    async def stream(self, model: Dict[str, Any], req: ExecutionRequest) -> AsyncIterator[dict]:
        raise NotImplementedError

    async def test_connection(self) -> Dict[str, Any]:
        raise NotImplementedError


class OpenAICompatibleAdapter(ProviderAdapter):
    """Any OpenAI-compatible endpoint. base_url should include the version path
    (e.g. https://api.openai.com/v1). Secret decrypted only at request time."""

    supports_tools = True
    supports_vision = True
    supports_reasoning_effort = True

    def _headers(self) -> Dict[str, str]:
        key = self.provider.get("api_key_enc")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {decrypt_secret(key)}"
        return headers

    def _base(self) -> str:
        return (self.provider.get("base_url") or "").rstrip("/")

    @property
    def supports_model_discovery(self) -> bool:
        # OpenRouter keeps its richer builtin catalog normalization, while all
        # other OpenAI-compatible gateways may use the standard /models route.
        return bool(self._base())

    @property
    def capabilities(self) -> Dict[str, bool]:
        return {
            "model_discovery": self.supports_model_discovery,
            "streaming": True,
            "output_stream": True,
            "reasoning_stream": True,
            "tool_streaming": True,
        }

    async def discover_models(self) -> List[ProviderModelCatalogItem]:
        if not self.supports_model_discovery:
            raise ProviderDiscoveryError("Model discovery is not supported for this provider", status_code=400)
        if _is_openrouter_base(self._base()) and not self.provider.get("api_key_enc"):
            raise ProviderDiscoveryError("Provider API key is required for model discovery", status_code=400, code="CREDENTIAL_REQUIRED")
        try:
            base_url = await async_validate_provider_url(self._base())
        except UrlValidationError as exc:
            raise ProviderDiscoveryError(exc.message, status_code=400, code=exc.code or "INVALID_PROVIDER_URL")
        try:
            headers = self._headers()
        except Exception:
            raise ProviderDiscoveryError("Provider credentials could not be used", status_code=502, code="CREDENTIAL_ERROR")
        try:
            async with _new_async_client(_connection_timeout(30.0)) as cli:
                if _is_openrouter_base(base_url):
                    auth_response = await cli.get(f"{base_url}/auth/key", headers=headers)
                    if auth_response.status_code >= 300:
                        raise ProviderDiscoveryError(
                            "Provider authentication failed during model discovery",
                            status_code=502,
                            code="DISCOVERY_AUTH_FAILED",
                        )
                response = await cli.get(f"{base_url}/models", headers=headers)
        except ProviderDiscoveryError:
            raise
        except httpx.TimeoutException:
            raise ProviderDiscoveryError("Provider model discovery timed out", status_code=504, code="DISCOVERY_TIMEOUT")
        except httpx.RequestError:
            raise ProviderDiscoveryError("Provider model discovery failed", status_code=502, code="DISCOVERY_NETWORK_ERROR")
        except Exception:
            raise ProviderDiscoveryError("Provider model discovery failed", status_code=502, code="DISCOVERY_FAILED")

        if response.status_code in (401, 403):
            raise ProviderDiscoveryError("Provider authentication failed during model discovery", status_code=502, code="DISCOVERY_AUTH_FAILED")
        if response.status_code == 404:
            raise ProviderDiscoveryError("Provider does not expose a /models endpoint", status_code=502, code="DISCOVERY_UNSUPPORTED")
        if response.status_code >= 300:
            raise ProviderDiscoveryError("Provider model discovery returned an upstream error", status_code=502, code="DISCOVERY_UPSTREAM_ERROR")
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise ProviderDiscoveryError("Provider returned invalid model discovery JSON", status_code=502, code="DISCOVERY_MALFORMED_RESPONSE")
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderDiscoveryError("Provider returned an invalid model catalog", status_code=502, code="DISCOVERY_MALFORMED_RESPONSE")
        normalizer = normalize_openrouter_model if _is_openrouter_base(base_url) else normalize_openai_compatible_model
        return [
            normalized
            for raw_item in payload["data"]
            if isinstance(raw_item, dict)
            for normalized in [normalizer(raw_item)]
            if normalized is not None
        ]

    async def execute(self, model: Dict[str, Any], req: ExecutionRequest) -> ExecutionResult:
        base = self._base()
        start = time.perf_counter()
        if not base:
            err = make_error("INVALID_PROVIDER_URL", "VALIDATION", "Provider base_url is not set")
            return ExecutionResult(status="error", error=err["message"], error_detail=err)
        try:
            base = await async_validate_provider_url(base)
        except UrlValidationError as exc:
            err = make_error("INVALID_PROVIDER_URL", "VALIDATION", exc.message)
            return ExecutionResult(
                status="error",
                error=err["message"],
                error_detail=err,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        messages = _openai_messages(req.system_prompt, req.messages)
        payload: Dict[str, Any] = {
            "model": model["model_identifier"],
            "messages": messages,
        }
        payload.update(_params_dict(req))
        tools_payload = _openai_tools(req)
        if tools_payload:
            payload["tools"] = tools_payload
            payload["tool_choice"] = req.tool_choice or "auto"
        try:
            async with _new_async_client(_connection_timeout(120.0)) as cli:
                r = await cli.post(f"{base}/chat/completions", headers=self._headers(), json=payload)
            latency = int((time.perf_counter() - start) * 1000)
            if r.status_code >= 300:
                # Redirects are disabled; a 3xx is treated as an upstream error
                # rather than silently re-sent with credentials to another host.
                err = classify_http_status(r.status_code, source="openai_compatible")
                return ExecutionResult(status="error", error=err["message"], error_detail=err, latency_ms=latency)
            try:
                data = r.json()
            except Exception as exc:
                err = classify_exception(exc, source="openai_compatible")
                return ExecutionResult(status="error", error=err["message"], error_detail=err, latency_ms=latency)
            if not isinstance(data, dict):
                err = make_error(
                    "MALFORMED_RESPONSE", "MALFORMED_RESPONSE", "Provider returned an invalid response"
                )
                return ExecutionResult(status="error", error=err["message"], error_detail=err, latency_ms=latency)
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            tool_calls = _parse_tool_calls(message)
            usage = data.get("usage")

            token_usage, cache_telemetry = normalize_usage(usage, provider_type="openai_compatible")
            reasoning_telemetry = extract_reasoning_from_choice(
                message,
                reasoning_tokens=token_usage.reasoning_tokens,
                streamed=False,
                source="openai_compatible",
            )

            raw_record = {"usage": usage, "choice": choice}

            return ExecutionResult(
                status="success",
                text=text,
                input_tokens=token_usage.input_tokens,
                output_tokens=token_usage.output_tokens,
                cached_tokens=token_usage.cache_read_tokens,
                reasoning_tokens=token_usage.reasoning_tokens,
                cache_read_tokens=token_usage.cache_read_tokens,
                cache_write_tokens=token_usage.cache_write_tokens,
                cache_creation_tokens=token_usage.cache_creation_tokens,
                uncached_input_tokens=token_usage.uncached_input_tokens,
                total_tokens=token_usage.total_tokens,
                reasoning_content=reasoning_telemetry.content,
                reasoning_summary=reasoning_telemetry.summary,
                reasoning_details=reasoning_telemetry.raw,
                token_usage=token_usage.model_dump(),
                cache_telemetry=cache_telemetry.model_dump(),
                reasoning_telemetry=reasoning_telemetry.model_dump(),
                latency_ms=latency,
                finish_reason=choice.get("finish_reason"),
                tool_calls=tool_calls,
                streaming=False,
                raw=raw_record,
            )
        except Exception as exc:
            err = classify_exception(exc, source="openai_compatible")
            return ExecutionResult(
                status="error",
                error=err["message"],
                error_detail=err,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )

    async def stream(self, model: Dict[str, Any], req: ExecutionRequest) -> AsyncIterator[dict]:
        base = self._base()
        if not base:
            raise ValueError("Provider base_url is not set")
        base = await async_validate_provider_url(base)
        payload: Dict[str, Any] = {
            "model": model["model_identifier"],
            "messages": _openai_messages(req.system_prompt, req.messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        payload.update(_params_dict(req))
        tools_payload = _openai_tools(req)
        if tools_payload:
            payload["tools"] = tools_payload
            payload["tool_choice"] = req.tool_choice or "auto"
        start = time.perf_counter()
        include_usage = True
        while True:
            try:
                async for event in self._stream_once(base, payload, start, include_usage):
                    yield event
                return
            except _StreamOptionsRejected:
                if not include_usage:
                    raise
                # The provider rejected stream_options before emitting content;
                # retry once without it rather than fabricating usage.
                include_usage = False
                payload.pop("stream_options", None)
                start = time.perf_counter()

    async def _stream_once(
        self,
        base: str,
        payload: Dict[str, Any],
        start: float,
        allow_usage_retry: bool,
    ) -> AsyncIterator[dict]:
        first_token_at: Optional[float] = None
        usage: Any = None
        finish_reason: Optional[str] = None
        accumulated_reasoning: List[str] = []
        output_chunks: List[str] = []
        tool_call_state: Dict[Any, Dict[str, Any]] = {}
        async with _new_async_client(_connection_timeout(120.0)) as cli:
            async with cli.stream("POST", f"{base}/chat/completions", headers=self._headers(), json=payload) as r:
                if r.status_code in (400, 422) and allow_usage_retry:
                    await r.aread()
                    raise _StreamOptionsRejected()
                if r.status_code >= 300:
                    await r.aread()
                    err = classify_http_status(r.status_code, source="openai_compatible")
                    raise RuntimeError(err["message"])
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except Exception:
                        continue
                    if not isinstance(data, dict):
                        continue
                    chunk_usage = data.get("usage")
                    if isinstance(chunk_usage, dict):
                        # Retain the final reported usage; providers may only send
                        # it in a trailing chunk (include_usage) or omit it.
                        usage = chunk_usage
                    choices = data.get("choices") or []
                    if choices and isinstance(choices[0], dict):
                        finish = choices[0].get("finish_reason")
                        if finish is not None:
                            finish_reason = finish
                    events = _parse_stream_chunk(data)
                    _accumulate_stream_tool_calls(tool_call_state, events)
                    for event in events:
                        event_type = event.get("type")
                        if event_type == "MODEL_REASONING_DELTA":
                            accumulated_reasoning.append(event["reasoning_delta"])
                            yield event
                        elif event_type == "MODEL_OUTPUT_DELTA":
                            output_chunks.append(event["delta"])
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            event = dict(event)
                            event["ttft_ms"] = int((first_token_at - start) * 1000)
                            yield event
                        else:
                            yield event
        reasoning_text = "".join(accumulated_reasoning) if accumulated_reasoning else None
        output_text = "".join(output_chunks) if output_chunks else None
        yield _stream_terminal_event(
            usage,
            finish_reason,
            accumulated_reasoning=reasoning_text,
            tool_calls=_finalized_stream_tool_calls(tool_call_state),
            output_text=output_text,
        )


    async def test_connection(self) -> Dict[str, Any]:
        base = self._base()
        if not base:
            return {"ok": False, "message": "base_url is not set"}
        try:
            base = await async_validate_provider_url(base)
        except UrlValidationError as exc:
            return {"ok": False, "message": exc.message}
        try:
            async with _new_async_client(_connection_timeout(30.0)) as cli:
                r = await cli.get(f"{base}/models", headers=self._headers())
                if r.status_code >= 300:
                    return {"ok": False, "message": f"Provider request failed (HTTP {r.status_code})"}
                # GET /models can be public (e.g. OpenRouter), so it alone
                # cannot prove the key is valid. Perform an authenticated probe.
                headers = self._headers()
                if _is_openrouter_base(base):
                    ar = await cli.get(f"{base}/auth/key", headers=headers)
                    if ar.status_code < 300:
                        return {"ok": True, "message": f"Endpoint reachable, key valid (HTTP {r.status_code})"}
                    return {"ok": False, "message": f"Authentication failed (HTTP {ar.status_code})"}
                # Generic OpenAI-compatible probe: minimal chat completion.
                # Reuse first model id from /models when available so a valid
                # key is not misreported due to an unknown probe model name.
                probe_model = "test-connection-probe"
                try:
                    data = r.json()
                    items = data.get("data") if isinstance(data, dict) else None
                    if isinstance(items, list) and items and isinstance(items[0], dict):
                        first_id = items[0].get("id")
                        if first_id:
                            probe_model = first_id
                except Exception:
                    pass
                cr = await cli.post(
                    f"{base}/chat/completions",
                    headers=headers,
                    json={"model": probe_model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                )
                if cr.status_code in (401, 403):
                    return {"ok": False, "message": f"Authentication failed (HTTP {cr.status_code})"}
                if cr.status_code < 300:
                    return {"ok": True, "message": f"Endpoint reachable, key valid (HTTP {cr.status_code})"}
                # 400/404 naming an unknown model means auth passed but the
                # probe model name is invalid — not an auth failure.
                if cr.status_code in (400, 404):
                    return {"ok": True, "message": f"Endpoint reachable, key accepted (HTTP {cr.status_code})"}
                return {"ok": False, "message": f"Provider request failed (HTTP {cr.status_code})"}
        except httpx.TimeoutException:
            return {"ok": False, "message": "Provider connection timed out"}
        except httpx.RequestError:
            return {"ok": False, "message": "Provider connection failed"}
        except Exception:
            return {"ok": False, "message": "Provider connection failed"}


def get_adapter(provider: Dict[str, Any]) -> ProviderAdapter:
    ptype = provider.get("provider_type")
    if ptype == "openai_compatible":
        return OpenAICompatibleAdapter(provider)
    raise ValueError(f"Unsupported provider_type: {ptype}")


def supports_stream(provider: Dict[str, Any], model: Optional[Dict[str, Any]] = None) -> bool:
    """True only for openai_compatible providers."""
    if (provider or {}).get("provider_type") != "openai_compatible":
        return False
    if model is not None:
        caps = model.get("capabilities") or {}
        if "stream" in caps:
            return bool(caps.get("stream"))
    return True


def _model_stream_capability(model: Any, key: str) -> Optional[bool]:
    caps = (model or {}).get("capabilities") if isinstance(model, dict) else None
    if isinstance(caps, dict) and isinstance(caps.get(key), bool):
        return caps[key]
    return None


def _supports_stream_capability(provider: Dict[str, Any], model: Any, key: str) -> bool:
    """Conservative streaming-capability probe.

    Only ``openai_compatible`` providers are considered; a model may narrow the
    provider default with an explicit boolean under its ``capabilities[key]``.
    """
    if (provider or {}).get("provider_type") != "openai_compatible":
        return False
    explicit = _model_stream_capability(model, key)
    if explicit is not None:
        return explicit
    return True


def supports_output_stream(provider: Dict[str, Any], model: Optional[Dict[str, Any]] = None) -> bool:
    return _supports_stream_capability(provider, model, "output_stream")


def supports_reasoning_stream(provider: Dict[str, Any], model: Optional[Dict[str, Any]] = None) -> bool:
    return _supports_stream_capability(provider, model, "reasoning_stream")


def adapter_supports_reasoning_effort(adapter: Any) -> bool:
    """True only when the resolved adapter can forward a reasoning-effort param."""
    return bool(getattr(adapter, "supports_reasoning_effort", False))


def model_reasoning_effort_capability(model: Optional[Dict[str, Any]]) -> Any:
    """Declared reasoning-effort capability for a model, or None.

    Accepts a truthy bool (provider uses the server default effort), a list
    of allowed effort strings, or a concrete string. Free-form model
    ``capabilities`` dict — no fabrication when absent.
    """
    if not isinstance(model, dict):
        return None
    capabilities = model.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    return capabilities.get("reasoning_effort")


def supports_tool_streaming(provider: Dict[str, Any], model: Optional[Dict[str, Any]] = None) -> bool:
    return _supports_stream_capability(provider, model, "tool_streaming")



def model_output_capability(model: Any) -> Optional[int]:
    """Extract known max output tokens capability from model record/dict if present and > 0."""
    if not isinstance(model, dict):
        if hasattr(model, "max_output_tokens"):
            return _positive_int(getattr(model, "max_output_tokens"))
        return None
    val = _positive_int(model.get("max_output_tokens"))
    if val is not None:
        return val
    caps = model.get("capabilities")
    if isinstance(caps, dict):
        return _positive_int(caps.get("max_output_tokens"))
    return None


def resolve_effective_output_budget(
    *,
    requested_output_limit: Optional[int] = None,
    known_model_output_capability: Optional[int] = None,
    known_provider_output_capability: Optional[int] = None,
    configured_execution_ceiling: Optional[int] = None,
    default_fallback: int = 8192,
) -> Dict[str, Any]:
    """Resolve effective output token limit respecting model and provider capabilities.

    Algorithm:
        effective_output_limit = min(
            requested_output_limit,
            known_model_output_capability,
            known_provider_output_capability,
            configured_execution_ceiling
        )
    - Only include constraints that actually exist (are not None and > 0).
    - If all are None, use default_fallback (e.g., 8192 or configured_execution_ceiling if provided).
    """
    req = _positive_int(requested_output_limit)
    mod = _positive_int(known_model_output_capability)
    prov = _positive_int(known_provider_output_capability)
    ceil = _positive_int(configured_execution_ceiling)

    candidates = [
        ("requested", req),
        ("model", mod),
        ("provider", prov),
        ("ceiling", ceil),
    ]
    valid_candidates = [(source, val) for source, val in candidates if val is not None]

    is_model_known = mod is not None
    model_val: Any = mod if is_model_known else "UNKNOWN"

    if not valid_candidates:
        fallback = ceil if ceil is not None else default_fallback
        return {
            "effective_output_limit": fallback,
            "requested_output_limit": requested_output_limit,
            "known_model_output_capability": model_val,
            "is_model_capability_known": is_model_known,
            "clamped_by": "default",
        }

    min_source, min_val = min(valid_candidates, key=lambda x: x[1])
    return {
        "effective_output_limit": min_val,
        "requested_output_limit": requested_output_limit,
        "known_model_output_capability": model_val,
        "is_model_capability_known": is_model_known,
        "clamped_by": min_source,
    }
