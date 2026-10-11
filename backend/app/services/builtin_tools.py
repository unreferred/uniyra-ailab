"""Built-in, dependency-free tool definitions and pure executors.

This module owns the canonical Phase 3 builtin tool catalog and the safe,
side-effect-free implementations that back them. Executors are intentionally
pure functions over plain ``dict`` arguments: no network, no filesystem, no
imports of untrusted code. ``run_calculator`` in particular never calls
``eval``/``exec`` -- it walks a whitelist of Python ``ast`` nodes so that only
literal arithmetic can execute.

The tool *definitions* are separate from the *registry* (``tools.py``): this
module is importable without a database, which keeps unit testing and startup
seeding simple.
"""
import ast
import json
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit


# --------------------------------------------------------------------- catalog
# Stable tool ids are hardcoded so that enable/disable state and references
# survive restarts and re-seeding. Never regenerate these.
BUILTIN_TOOL_DEFINITIONS: List[dict] = [
    {
        "id": "tool-calculator-0001",
        "name": "calculator",
        "display_name": "Calculator",
        "description": "Evaluate a safe arithmetic expression.",
        "version": "1.0.0",
        "tool_type": "function",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Arithmetic expression",
                }
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string"},
                "result": {"type": "number"},
            },
        },
        "enabled": True,
        "risk_level": "low",
        "permissions": ["calculator.execute"],
        "implementation": "builtin:calculator",
        "scope": "global",
        "project_id": None,
        "owner_id": None,
        "builtin": True,
        "tags": ["math"],
    },
    {
        "id": "tool-datetime-0001",
        "name": "datetime",
        "display_name": "Date & Time",
        "description": "Report, parse and diff ISO-8601 timestamps.",
        "version": "1.0.0",
        "tool_type": "function",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["now", "parse", "diff"],
                    "default": "now",
                },
                "value": {"type": "string"},
                "value2": {"type": "string"},
                "format": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result": {"type": ["string", "object"]},
            },
        },
        "enabled": True,
        "risk_level": "low",
        "permissions": ["datetime.read"],
        "implementation": "builtin:datetime",
        "scope": "global",
        "project_id": None,
        "owner_id": None,
        "builtin": True,
        "tags": ["time"],
    },
    {
        "id": "tool-json-transform-0001",
        "name": "json_transform",
        "display_name": "JSON Transform",
        "description": "Inspect and reshape a JSON document.",
        "version": "1.0.0",
        "tool_type": "function",
        "input_schema": {
            "type": "object",
            "properties": {
                "json": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": ["pretty", "keys", "values", "length", "get", "flatten"],
                },
                "path": {
                    "type": "string",
                    "description": "Dot path for get",
                },
            },
            "required": ["json", "operation"],
            "additionalProperties": False,
        },
        "output_schema": {"type": "object"},
        "enabled": True,
        "risk_level": "low",
        "permissions": ["json.transform"],
        "implementation": "builtin:json_transform",
        "scope": "global",
        "project_id": None,
        "owner_id": None,
        "builtin": True,
        "tags": ["json"],
    },
    {
        "id": "tool-search-0001",
        "name": "web_search",
        "display_name": "Web Search",
        "description": "Search the web for real-time information via registered search providers.",
        "version": "1.0.0",
        "tool_type": "function",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5, max 20)",
                    "default": 5,
                },
                "location": {
                    "type": "string",
                    "description": "Optional regional search hint (e.g. ID); overrides the provider default",
                },
                "language": {
                    "type": "string",
                    "description": "Optional result language hint (e.g. id); overrides the provider default",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "provider": {"type": "string"},
                "location": {"type": "string"},
                "language": {"type": "string"},
                "results": {"type": "array"},
                "count": {"type": "integer"},
            },
        },
        "enabled": True,
        "risk_level": "medium",
        "permissions": ["search.execute"],
        "implementation": "builtin:web_search",
        "scope": "global",
        "project_id": None,
        "owner_id": None,
        "builtin": True,
        "tags": ["web", "search"],
    },
    {
        "id": "tool-fetch-0001",
        "name": "web_fetch",
        "display_name": "Web Fetch",
        "description": "Fetch and extract readable text content from a public web URL with SSRF protection.",
        "version": "1.0.0",
        "tool_type": "function",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Target HTTP/HTTPS URL",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "requested_url": {"type": "string"},
                "final_url": {"type": "string"},
                "status": {"type": "integer"},
                "content_type": {"type": "string"},
                "title": {"type": ["string", "null"]},
                "text": {"type": "string"},
                "provider": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
        "enabled": True,
        "risk_level": "medium",
        "permissions": ["fetch.execute"],
        "implementation": "builtin:web_fetch",
        "scope": "global",
        "project_id": None,
        "owner_id": None,
        "builtin": True,
        "tags": ["web", "fetch"],
    },
    {
        "id": "tool-research-0001",
        "name": "web_research",
        "display_name": "Web Research",
        "description": (
            "Research a question on the web in one call: search, "
            "deterministically choose the best candidate sources, then fetch "
            "their content and return an evidence bundle. Use this when you "
            "need evidence from multiple pages. Use web_search to only "
            "discover URLs, and web_fetch to read one known URL."
        ),
        "version": "1.0.0",
        "tool_type": "function",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Research question or search query string",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of search candidates to return",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
                "max_fetches": {
                    "type": "integer",
                    "description": "Maximum number of candidate URLs to fetch for deeper evidence",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
                "location": {
                    "type": "string",
                    "description": "Optional regional search hint (e.g. ID); overrides the provider default",
                },
                "language": {
                    "type": "string",
                    "description": "Optional result language hint (e.g. id); overrides the provider default",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "outcome": {
                    "type": "string",
                    "enum": ["SUCCESS", "PARTIAL_SUCCESS"],
                    "description": "SUCCESS when every selected source fetched; PARTIAL_SUCCESS when any failed",
                },
                "search": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string"},
                        "results": {"type": "array"},
                    },
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "title": {"type": ["string", "null"]},
                            "status": {
                                "type": "string",
                                "enum": ["success", "timeout", "failed"],
                            },
                            "text": {"type": "string"},
                            "fetch_provider": {"type": "string"},
                            "duration_ms": {"type": "integer"},
                            "error": {"type": "object"},
                        },
                    },
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "searched": {"type": "integer"},
                        "selected": {"type": "integer"},
                        "fetched": {"type": "integer"},
                        "failed": {"type": "integer"},
                    },
                },
                "internal_operations": {"type": "array"},
                "internal_budget": {
                    "type": "object",
                    "properties": {
                        "search_calls": {"type": "integer"},
                        "fetch_calls": {"type": "integer"},
                        "external_requests": {"type": "integer"},
                    },
                },
                "provider": {"type": "string"},
                "search_provider": {"type": "string"},
                "fetch_provider": {"type": "string"},
                "duration_ms": {"type": "integer"},
                "research": {"type": "object"},
            },
        },
        "enabled": True,
        "risk_level": "medium",
        "permissions": ["search.execute", "fetch.execute"],
        "implementation": "builtin:web_research",
        "scope": "global",
        "project_id": None,
        "owner_id": None,
        "builtin": True,
        "tags": ["web", "search", "fetch", "research"],
    },
]


# ------------------------------------------------------------------- calculator
_MAX_EXPRESSION_LEN = 500
_MAX_EXPONENT = 1000
_MAX_BASE = 1e12
_MAX_RESULT = 1e308


def _normalize_number(value: Any) -> Any:
    """Return an int for integral results, else float; reject non-reals."""
    if isinstance(value, bool):
        raise ValueError("Unsupported expression")
    if isinstance(value, complex):
        raise ValueError("Unsupported expression")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("Result too large")
        return value if not value.is_integer() else int(value)
    raise ValueError("Unsupported expression")


def _guard_result(value: Any) -> Any:
    if isinstance(value, int):
        if abs(value) >= 10 ** 308:
            raise ValueError("Result too large")
        return value
    value = float(value)
    if not math.isfinite(value) or abs(value) >= _MAX_RESULT:
        raise ValueError("Result too large")
    return value


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("Unsupported expression")
        return node.value

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        raise ValueError("Unsupported expression")

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            if right == 0:
                raise ValueError("Division by zero")
            return left / right
        if isinstance(op, ast.FloorDiv):
            if right == 0:
                raise ValueError("Division by zero")
            return left // right
        if isinstance(op, ast.Mod):
            if right == 0:
                raise ValueError("Division by zero")
            return left % right
        if isinstance(op, ast.Pow):
            if abs(right) > _MAX_EXPONENT or abs(left) > _MAX_BASE:
                raise ValueError("Exponent too large")
            return left ** right
        raise ValueError("Unsupported expression")

    raise ValueError("Unsupported expression")


def run_calculator(arguments: dict) -> dict:
    """Evaluate a whitelisted arithmetic expression. Never uses eval/exec."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    expression = arguments.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression is required")
    if len(expression) > _MAX_EXPRESSION_LEN:
        raise ValueError("expression is too long")

    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ValueError("Invalid expression") from exc
    except RecursionError as exc:
        raise ValueError("Expression is too complex") from exc

    try:
        result = _eval_node(tree)
        result = _guard_result(_normalize_number(result))
    except RecursionError as exc:
        raise ValueError("Expression is too complex") from exc

    return {"expression": expression, "result": result}


# --------------------------------------------------------------------- datetime
def _parse_iso(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Invalid datetime value")
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Invalid ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _human_duration(seconds: float) -> str:
    negative = seconds < 0
    remaining = abs(seconds)
    parts: List[str] = []
    units = (
        ("day", 86400),
        ("hour", 3600),
        ("minute", 60),
        ("second", 1),
    )
    for label, size in units:
        if remaining >= size or (label == "second" and not parts):
            count = int(remaining // size)
            remaining -= count * size
            if count or label == "second":
                suffix = "" if count == 1 else "s"
                parts.append(f"{count} {label}{suffix}")
    text = ", ".join(parts) if parts else "0 seconds"
    return f"-{text}" if negative else text


def run_datetime(arguments: dict) -> dict:
    """Report/parse/diff ISO-8601 timestamps using stdlib datetime (UTC)."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    operation = arguments.get("operation") or "now"
    if operation not in ("now", "parse", "diff"):
        raise ValueError("Unsupported operation")

    if operation == "now":
        now = datetime.now(timezone.utc)
        result: Any = _iso_utc(now)
        fmt = arguments.get("format")
        if isinstance(fmt, str) and fmt:
            try:
                result = now.strftime(fmt)
            except (ValueError, TypeError) as exc:
                raise ValueError("Invalid format string") from exc
        return {"result": result, "epoch_ms": int(now.timestamp() * 1000)}

    if operation == "parse":
        parsed = _parse_iso(arguments.get("value"))
        return {"result": _iso_utc(parsed), "epoch_ms": int(parsed.timestamp() * 1000)}

    start = _parse_iso(arguments.get("value"))
    end = _parse_iso(arguments.get("value2"))
    seconds = (end - start).total_seconds()
    return {"result": {"seconds": float(seconds), "human": _human_duration(seconds)}}


# --------------------------------------------------------------- json_transform
_MAX_JSON_OUTPUT = 100_000


def _lookup_path(obj: Any, path: str) -> Any:
    current = obj
    for part in (path or "").split("."):
        if part == "":
            continue
        if isinstance(current, dict):
            if part not in current:
                raise ValueError(f"Path not found: {path}")
            current = current[part]
        elif isinstance(current, list):
            try:
                index = int(part)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Path not found: {path}") from exc
            try:
                current = current[index]
            except IndexError as exc:
                raise ValueError(f"Path not found: {path}") from exc
        else:
            raise ValueError(f"Path not found: {path}")
    return current


def _flatten_into(value: Any, out: List[Any]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _flatten_into(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _flatten_into(item, out)
    else:
        out.append(value)


def _bounded(result: Any) -> Any:
    try:
        serialized = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise ValueError("Result is not JSON-serializable") from exc
    if len(serialized) > _MAX_JSON_OUTPUT:
        raise ValueError("Result too large")
    return result


def run_json_transform(arguments: dict) -> dict:
    """Inspect/reshape a JSON document. Pure and output-bounded."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    raw = arguments.get("json")
    if not isinstance(raw, str):
        raise ValueError("json is required")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("Invalid JSON") from exc

    operation = arguments.get("operation")
    if operation == "pretty":
        result = json.dumps(data, indent=2, sort_keys=True)
    elif operation == "keys":
        if not isinstance(data, dict):
            raise ValueError("keys requires a JSON object")
        result = sorted(data.keys())
    elif operation == "values":
        if isinstance(data, dict):
            result = list(data.values())
        elif isinstance(data, list):
            result = list(data)
        else:
            raise ValueError("values requires a JSON object or array")
    elif operation == "length":
        if not isinstance(data, (list, dict, str)):
            raise ValueError("length requires a JSON object, array or string")
        result = len(data)
    elif operation == "get":
        result = _lookup_path(data, arguments.get("path") or "")
    elif operation == "flatten":
        flat: List[Any] = []
        _flatten_into(data, flat)
        result = flat
    else:
        raise ValueError("Unsupported operation")

    return {"result": _bounded(result)}


# --------------------------------------------------------- provider resolution
_UNSET = object()


async def _resolve_search_adapter(context: Optional[dict], provider_id: Any = _UNSET):
    """Resolve the effective search adapter for execution.

    With an ``owner_id`` in the context the provider doc is resolved through
    the valve layer (global -> project overrides, vault secret precedence) and
    the adapter is built from that effective doc. Otherwise the legacy registry
    path is used. ``provider_id`` defaults to the context's
    ``search_provider_id`` (falling back to ``provider_id``); callers may pass
    an explicit value (including ``None``) to preserve their own precedence.
    """
    from .search_providers import SearchProviderRegistry

    ctx = context or {}
    if provider_id is _UNSET:
        provider_id = ctx.get("search_provider_id") or ctx.get("provider_id")
    owner_id = ctx.get("owner_id")
    project_id = ctx.get("project_id")
    if owner_id:
        from . import tool_valves

        doc = await tool_valves.resolve_provider_execution(
            owner_id, "search", provider_id, project_id
        )
        return SearchProviderRegistry.adapter_from_doc(doc)
    return await SearchProviderRegistry.get_adapter(provider_id)


async def _resolve_fetch_adapter(context: Optional[dict], provider_id: Any = _UNSET):
    """Resolve the effective fetch adapter (see :func:`_resolve_search_adapter`)."""
    from .fetch_providers import FetchProviderRegistry

    ctx = context or {}
    if provider_id is _UNSET:
        provider_id = ctx.get("fetch_provider_id") or ctx.get("provider_id")
    owner_id = ctx.get("owner_id")
    project_id = ctx.get("project_id")
    if owner_id:
        from . import tool_valves

        doc = await tool_valves.resolve_provider_execution(
            owner_id, "fetch", provider_id, project_id
        )
        return FetchProviderRegistry.adapter_from_doc(doc)
    return await FetchProviderRegistry.get_adapter(provider_id)


def _search_option_params(
    adapter: Any, location: Any, language: Any
) -> Dict[str, Any]:
    """Pass location/language only when the adapter's signature accepts them.

    Custom providers with the minimal ``search(query, *, max_results)`` contract
    keep working; the guard mirrors the original ``run_search`` behaviour.
    """
    import inspect

    try:
        search_params = inspect.signature(adapter.search).parameters
    except (TypeError, ValueError):  # pragma: no cover - exotic adapters
        return {}
    extra: Dict[str, Any] = {}
    if "location" in search_params and isinstance(location, str) and location.strip():
        extra["location"] = location
    if "language" in search_params and isinstance(language, str) and language.strip():
        extra["language"] = language
    return extra


# ------------------------------------------------------------------- web_search
async def run_search(arguments: dict, context: Optional[dict] = None) -> dict:
    """Execute canonical search via the effective (project-aware) provider.

    When an ``owner_id`` is present in the execution context the provider doc
    is resolved through the valve layer (global -> project overrides, vault
    secret precedence) and the adapter is built from that effective doc. The
    provider's ``SearchError`` is never swallowed or replaced with another
    provider's results: no silent fallback.
    """
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")
    ctx = context or {}
    provider_id = ctx.get("search_provider_id") or arguments.get("provider_id")
    adapter = await _resolve_search_adapter(ctx, provider_id)
    # Optional arguments may override valve defaults where the adapter supports
    # them; pass them only when the adapter's signature accepts them so custom
    # providers with the minimal contract keep working.
    extra = _search_option_params(
        adapter, arguments.get("location"), arguments.get("language")
    )
    return await adapter.search(
        query, max_results=arguments.get("max_results"), **extra
    )


# ------------------------------------------------------------------- web_fetch
async def run_fetch(arguments: dict, context: Optional[dict] = None) -> dict:
    """Execute canonical URL fetch via the effective (project-aware) provider.

    Same effective-provider resolution as :func:`run_search`: with an
    ``owner_id`` the project-scoped valves/secret are honored, otherwise the
    legacy registry path is used. Failures are raised, never faked.
    """
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    url = arguments.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url is required")
    ctx = context or {}
    provider_id = ctx.get("fetch_provider_id") or arguments.get("provider_id")
    adapter = await _resolve_fetch_adapter(ctx, provider_id)
    return await adapter.fetch(url)


# ----------------------------------------------------------------- web_research
_RESEARCH_HARD_MAX_RESULTS = 10
_RESEARCH_HARD_MAX_FETCHES = 10
# Backwards-compatible module aliases; live defaults come from app.config
# and the resolved research policy at execution time.
_RESEARCH_DEFAULT_RESULTS = 5
_RESEARCH_MAX_RESULTS = _RESEARCH_HARD_MAX_RESULTS
_RESEARCH_DEFAULT_FETCHES = 5
_RESEARCH_MAX_FETCHES = _RESEARCH_HARD_MAX_FETCHES


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    """Coerce ``value`` to an int and clamp it to ``[low, high]``."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _int_or(value, default):
    """Best-effort int coercion; returns default for None/bool/invalid."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _policy_int(policy, key, fallback):
    if isinstance(policy, dict):
        return _int_or(policy.get(key), fallback)
    return fallback


def _provider_batch_cap(adapter, config_module) -> int:
    """Provider execution batch size (NOT research logical breadth)."""
    cap = getattr(adapter, "batch_cap", None)
    if cap is None:
        cap = getattr(config_module, "MAX_BATCH_FETCH_URLS", 5)
    cap = _int_or(cap, 0)
    try:
        ceiling = int(getattr(config_module, "MAX_BATCH_FETCH_URLS", 5) or 5)
    except (TypeError, ValueError):
        ceiling = 5
    if cap <= 0:
        cap = ceiling
    return max(1, min(cap, max(1, ceiling)))


def _normalize_research_url(raw: Any) -> str:
    """Normalize a candidate URL: strip fragment, lowercase scheme + host.

    Returns ``""`` for empty/non-string URLs so they are dropped. Path, query
    and any userinfo are preserved verbatim.
    """
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if not parts.scheme and not parts.netloc:
        return text
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            parts.query,
            "",
        )
    )


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _select_research_sources(results: Any, max_fetches: Any) -> List[str]:
    """Deterministically choose which result URLs to fetch (no model).

    Algorithm (fully deterministic):

    1. Normalize every URL (``_normalize_research_url``) and drop empties.
    2. De-duplicate, preserving the first occurrence.
    3. Sort by the provider's ``rank`` ascending (ties/absent ranks fall back to
       original order).
    4. Domain diversity: walk the sorted list and take the first URL of each
       distinct host (one per host, in rank order) until ``max_fetches`` is
       reached.
    5. Fill any remaining slots with the next unselected URLs, still in rank
       order.

    The returned list preserves selection order and never exceeds
    ``max_fetches``.
    """
    try:
        limit = int(max_fetches)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return []

    seen: set = set()
    unique: List[tuple] = []
    for index, item in enumerate(results or []):
        if not isinstance(item, dict):
            continue
        url = _normalize_research_url(item.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        rank = item.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
            rank = index + 1
        unique.append((rank, index, url))
    unique.sort(key=lambda entry: (entry[0], entry[1]))

    selected: List[str] = []
    selected_set: set = set()
    hosts: set = set()
    for _rank, _index, url in unique:
        if len(selected) >= limit:
            break
        host = _host_of(url)
        if host in hosts:
            continue
        hosts.add(host)
        selected.append(url)
        selected_set.add(url)
    for _rank, _index, url in unique:
        if len(selected) >= limit:
            break
        if url in selected_set:
            continue
        selected.append(url)
        selected_set.add(url)
    return selected


def _normalize_search_results(raw: Any) -> List[dict]:
    """Guarantee the ``{title,url,snippet,rank}`` shape (extra keys preserved)."""
    normalized: List[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        title = entry.get("title")
        entry["title"] = title if isinstance(title, str) else str(title or "")
        url = entry.get("url")
        entry["url"] = url if isinstance(url, str) else str(url or "")
        snippet = entry.get("snippet")
        entry["snippet"] = (
            snippet if isinstance(snippet, str) else str(snippet or "")
        )
        rank = entry.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int):
            entry["rank"] = len(normalized) + 1
        normalized.append(entry)
    return normalized


async def _emit_operation(sink: Any, payload: Dict[str, Any]) -> None:
    """Best-effort, exception-swallowing emit of one internal operation event."""
    if not callable(sink):
        return
    import inspect

    try:
        result = sink(payload)
        if inspect.isawaitable(result):
            await result
    except Exception:
        return


async def run_research(arguments: dict, context: Optional[dict] = None) -> dict:
    """Compose Search -> deterministic URL selection -> batched Fetch.

    Fully deterministic: no LLM/model call ever happens here. The single
    returned evidence bundle is the only provider data the model sees. The
    atomic ``web_search`` / ``web_fetch`` executors are untouched.

    Logical research breadth (``max_results`` / ``max_fetches``) is resolved
    from the model's request, the server research policy, and the run budget;
    provider execution batching (``MAX_BATCH_FETCH_URLS`` / provider
    ``batch_cap``) is a separate concern and never caps logical breadth.
    """
    from .. import config
    from .fetch_providers import FetchError, _per_url_failure

    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")
    query = query.strip()

    ctx = context or {}
    sink = ctx.get("operation_sink")
    policy = ctx.get("research_policy")
    if not isinstance(policy, dict):
        policy = {}

    cfg_default_results = _int_or(
        getattr(config, "RESEARCH_DEFAULT_MAX_RESULTS", _RESEARCH_DEFAULT_RESULTS),
        _RESEARCH_DEFAULT_RESULTS,
    )
    cfg_default_fetches = _int_or(
        getattr(config, "RESEARCH_DEFAULT_MAX_FETCHES", _RESEARCH_DEFAULT_FETCHES),
        _RESEARCH_DEFAULT_FETCHES,
    )
    cfg_ceiling_results = _int_or(
        getattr(config, "RESEARCH_MAX_RESULTS_CEILING", _RESEARCH_HARD_MAX_RESULTS),
        _RESEARCH_HARD_MAX_RESULTS,
    )
    cfg_ceiling_fetches = _int_or(
        getattr(config, "RESEARCH_MAX_FETCHES_CEILING", _RESEARCH_HARD_MAX_FETCHES),
        _RESEARCH_HARD_MAX_FETCHES,
    )

    ceiling_results = _clamp_int(
        _policy_int(policy, "max_results_ceiling", cfg_ceiling_results),
        cfg_ceiling_results,
        1,
        _RESEARCH_HARD_MAX_RESULTS,
    )
    ceiling_fetches = _clamp_int(
        _policy_int(policy, "max_fetches_ceiling", cfg_ceiling_fetches),
        cfg_ceiling_fetches,
        1,
        _RESEARCH_HARD_MAX_FETCHES,
    )
    default_results = _clamp_int(
        _policy_int(policy, "default_max_results", cfg_default_results),
        cfg_default_results,
        1,
        ceiling_results,
    )
    default_fetches = _clamp_int(
        _policy_int(policy, "default_max_fetches", cfg_default_fetches),
        cfg_default_fetches,
        1,
        ceiling_fetches,
    )

    config_source = policy.get("source")
    if config_source not in ("GLOBAL", "PROJECT", "REQUEST_DEFAULT"):
        config_source = "REQUEST_DEFAULT"

    requested_results = _int_or(arguments.get("max_results"), None)
    requested_fetches = _int_or(arguments.get("max_fetches"), None)

    requested_or_default_results = _clamp_int(
        requested_results if requested_results is not None else default_results,
        default_results,
        1,
        ceiling_results,
    )
    requested_or_default_fetches = _clamp_int(
        requested_fetches if requested_fetches is not None else default_fetches,
        default_fetches,
        1,
        ceiling_fetches,
    )

    budget = ctx.get("research_budget")
    search_remaining: Any = None
    fetch_remaining: Any = None
    if isinstance(budget, dict):
        search_remaining = budget.get("search_remaining")
        fetch_remaining = budget.get("fetch_remaining")

    search_allowed = True
    if search_remaining is not None:
        try:
            if int(search_remaining) <= 0:
                search_allowed = False
        except (TypeError, ValueError):
            pass

    effective_max_results = requested_or_default_results
    if search_allowed and search_remaining is not None:
        try:
            effective_max_results = max(
                1, min(effective_max_results, max(0, int(search_remaining)))
            )
        except (TypeError, ValueError):
            pass

    effective_fetches_pre = requested_or_default_fetches
    if fetch_remaining is not None:
        try:
            effective_fetches_pre = min(
                effective_fetches_pre, max(0, int(fetch_remaining))
            )
        except (TypeError, ValueError):
            pass

    started = time.perf_counter()
    internal_operations: List[dict] = []
    results: List[dict] = []
    search_calls = 0
    search_provider_id = (
        ctx.get("search_provider_id") or ctx.get("provider_id") or ""
    )

    if search_allowed:
        search_adapter = await _resolve_search_adapter(ctx)
        search_provider_id = getattr(search_adapter, "id", "") or search_provider_id
        extra = _search_option_params(
            search_adapter, arguments.get("location"), arguments.get("language")
        )
        operation_id = str(uuid.uuid4())
        await _emit_operation(
            sink,
            {
                "op": "start",
                "kind": "search",
                "operation_id": operation_id,
                "query": query,
                "provider": search_provider_id,
            },
        )
        search_started = time.perf_counter()
        search_output = await search_adapter.search(
            query, max_results=effective_max_results, **extra
        )
        if not isinstance(search_output, dict):
            search_output = {}
        search_duration_ms = search_output.get("duration_ms")
        if isinstance(search_duration_ms, bool) or not isinstance(
            search_duration_ms, int
        ):
            search_duration_ms = int((time.perf_counter() - search_started) * 1000)
        results = _normalize_search_results(search_output.get("results"))
        search_calls = 1
        await _emit_operation(
            sink,
            {
                "op": "complete",
                "kind": "search",
                "operation_id": operation_id,
                "query": query,
                "provider": search_provider_id,
                "count": len(results),
                "duration_ms": search_duration_ms,
                "status": "success",
            },
        )
        search_meta = search_output.get("metadata")
        if not isinstance(search_meta, dict):
            search_meta = {}
        search_op = {
            "type": "SEARCH_OPERATION",
            "name": f"TinyFish search: {query}",
            "provider": search_provider_id,
            "status": "success",
            "duration_ms": search_duration_ms,
            "metadata": {
                "query": query,
                "count": len(results),
                "egress_mode": search_output.get("egress_mode")
                or search_meta.get("egress_mode")
                or "REMOTE",
            },
        }
        endpoint_host = search_meta.get("provider_endpoint_host")
        if endpoint_host:
            search_op["metadata"]["provider_endpoint_host"] = endpoint_host
        internal_operations.append(search_op)

    # Available logical candidates: normalized, de-duplicated, non-empty URLs.
    available = 0
    seen_candidates: set = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        candidate = _normalize_research_url(item.get("url"))
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        available += 1
    effective_max_fetches = max(0, min(effective_fetches_pre, available))
    urls = _select_research_sources(results, effective_max_fetches)

    fetch_provider_id = (
        ctx.get("fetch_provider_id") or ctx.get("provider_id") or ""
    )
    fetch_adapter = None
    provider_batch_cap: Optional[int] = None
    if urls:
        fetch_adapter = await _resolve_fetch_adapter(ctx)
        fetch_provider_id = getattr(fetch_adapter, "id", "") or fetch_provider_id
        provider_batch_cap = _provider_batch_cap(fetch_adapter, config)

    sources: List[dict] = []
    operation_ids = {url: str(uuid.uuid4()) for url in urls}

    def _fetch_outcome(url: str, record: dict, duration_ms: int):
        raw_meta = record.get("metadata") if isinstance(record, dict) else None
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        if record.get("status") == "success":
            source = {
                "url": url,
                "title": record.get("title"),
                "status": "success",
                "text": record.get("text") or "",
                "fetch_provider": record.get("fetch_provider")
                or record.get("provider")
                or fetch_provider_id,
                "duration_ms": duration_ms,
            }
            final_url = record.get("final_url") or url
            sink_payload = {
                "op": "complete",
                "kind": "fetch",
                "operation_id": operation_ids[url],
                "url": url,
                "provider": fetch_provider_id,
                "final_url": final_url,
                "duration_ms": duration_ms,
                "status": "success",
            }
            op_status = "success"
        else:
            err = record.get("error") if isinstance(record.get("error"), dict) else {}
            code = str(err.get("code") or "FETCH_ERROR")
            message = str(err.get("message") or "Fetch failed")[:300]
            error: Dict[str, Any] = {"code": code, "message": message}
            if err.get("timeout_seconds") is not None:
                error["timeout_seconds"] = err["timeout_seconds"]
            source = {
                "url": url,
                "status": "timeout" if code == "FETCH_TIMEOUT" else "failed",
                "error": error,
            }
            final_url = ""
            sink_payload = {
                "op": "failed",
                "kind": "fetch",
                "operation_id": operation_ids[url],
                "url": url,
                "provider": fetch_provider_id,
                "duration_ms": duration_ms,
                "status": "failed",
                "error": error,
            }
            op_status = "failed"
        egress = (
            record.get("egress_mode")
            or meta.get("egress_mode")
            or getattr(fetch_adapter, "egress_mode", None)
            or "LOCAL"
        )
        internal_op = {
            "type": "FETCH_OPERATION",
            "name": f"Fetch: {_host_of(url)}",
            "provider": fetch_provider_id,
            "status": op_status,
            "duration_ms": duration_ms,
            "metadata": {
                "url": url,
                "final_url": final_url,
                "egress_mode": egress,
            },
        }
        if meta.get("provider_endpoint_host"):
            internal_op["metadata"]["provider_endpoint_host"] = meta[
                "provider_endpoint_host"
            ]
        provider_status = record.get("provider_status") or meta.get(
            "provider_status"
        )
        if provider_status is not None:
            internal_op["metadata"]["provider_status"] = provider_status
        return source, sink_payload, internal_op

    provider_batch_count = 0
    if urls and fetch_adapter is not None:
        supports_batch = bool(
            getattr(fetch_adapter, "supports_batch_fetch", False)
        )
        if supports_batch:
            batch_cap = provider_batch_cap or _provider_batch_cap(
                fetch_adapter, config
            )
            for chunk_start in range(0, len(urls), batch_cap):
                chunk = urls[chunk_start : chunk_start + batch_cap]
                for url in chunk:
                    await _emit_operation(
                        sink,
                        {
                            "op": "start",
                            "kind": "fetch",
                            "operation_id": operation_ids[url],
                            "url": url,
                            "provider": fetch_provider_id,
                        },
                    )
                chunk_started = time.perf_counter()
                records: List[dict] = []
                chunk_exc: Optional[Exception] = None
                try:
                    raw_records = await fetch_adapter.fetch_many(list(chunk))
                    records = [r for r in (raw_records or []) if isinstance(r, dict)]
                except FetchError as exc:
                    chunk_exc = exc
                except Exception as exc:  # pragma: no cover - defensive
                    chunk_exc = exc
                chunk_ms = int((time.perf_counter() - chunk_started) * 1000)
                provider_batch_count += 1
                by_url: Dict[str, dict] = {}
                for record in records:
                    key = record.get("url")
                    if isinstance(key, str) and key not in by_url:
                        by_url[key] = record
                for index, url in enumerate(chunk):
                    record = by_url.get(url)
                    if record is None and chunk_exc is not None:
                        record = _per_url_failure(url, chunk_exc)
                    elif record is None and index < len(records):
                        record = records[index]
                    if record is None:
                        record = _per_url_failure(
                            url,
                            FetchError(
                                "No fetch result returned",
                                502,
                                "FETCH_ERROR",
                                code="FETCH_ERROR",
                            ),
                        )
                    record_duration = record.get("duration_ms")
                    if isinstance(record_duration, bool) or not isinstance(
                        record_duration, int
                    ):
                        record_duration = chunk_ms
                    source, sink_payload, internal_op = _fetch_outcome(
                        url, record, record_duration
                    )
                    sources.append(source)
                    internal_operations.append(internal_op)
                    await _emit_operation(sink, sink_payload)
        else:
            for url in urls:
                await _emit_operation(
                    sink,
                    {
                        "op": "start",
                        "kind": "fetch",
                        "operation_id": operation_ids[url],
                        "url": url,
                        "provider": fetch_provider_id,
                    },
                )
                url_started = time.perf_counter()
                try:
                    fetched = await fetch_adapter.fetch(url)
                    if not isinstance(fetched, dict):
                        fetched = {}
                    record = dict(fetched)
                    record["status"] = "success"
                    record["url"] = url
                except FetchError as exc:
                    record = _per_url_failure(url, exc)
                except Exception as exc:  # pragma: no cover - defensive
                    record = _per_url_failure(url, exc)
                duration_ms = int((time.perf_counter() - url_started) * 1000)
                source, sink_payload, internal_op = _fetch_outcome(
                    url, record, duration_ms
                )
                sources.append(source)
                internal_operations.append(internal_op)
                await _emit_operation(sink, sink_payload)

    fetched = sum(1 for source in sources if source.get("status") == "success")
    failed = len(sources) - fetched
    outcome = "SUCCESS" if failed == 0 else "PARTIAL_SUCCESS"
    total_ms = int((time.perf_counter() - started) * 1000)
    fetch_calls = len(urls)

    provider_type = None
    if fetch_adapter is not None:
        provider_type = getattr(fetch_adapter, "provider_type", None)
        if provider_type is None:
            doc = getattr(fetch_adapter, "doc", None)
            if isinstance(doc, dict):
                provider_type = doc.get("provider_type")
    provider_quota_units_consumed = (
        len(urls) if provider_type == "tinyfish" else None
    )

    research = {
        "requested_max_results": requested_results,
        "requested_max_fetches": requested_fetches,
        "default_max_results": default_results,
        "default_max_fetches": default_fetches,
        "configured_max_results": ceiling_results,
        "configured_max_fetches": ceiling_fetches,
        "effective_max_results": effective_max_results,
        "effective_max_fetches": effective_max_fetches,
        "candidate_results_returned": len(results),
        "fetch_urls_selected": len(urls),
        "fetch_urls_attempted": len(urls),
        "fetch_urls_succeeded": fetched,
        "fetch_urls_failed": failed,
        "fetch_provider": fetch_provider_id,
        "provider_batch_cap": provider_batch_cap,
        "provider_batch_count": provider_batch_count,
        "provider_quota_units_consumed": provider_quota_units_consumed,
        "remaining_search_budget": search_remaining,
        "remaining_fetch_budget": fetch_remaining,
        "research_config_source": config_source,
    }

    return {
        "query": query,
        "outcome": outcome,
        "search": {"provider": search_provider_id, "results": results},
        "sources": sources,
        "summary": {
            "searched": len(results),
            "selected": len(urls),
            "fetched": fetched,
            "failed": failed,
        },
        "internal_operations": internal_operations,
        "internal_budget": {
            "search_calls": search_calls,
            "fetch_calls": fetch_calls,
            "external_requests": search_calls + fetch_calls,
        },
        "provider": search_provider_id,
        "search_provider": search_provider_id,
        "fetch_provider": fetch_provider_id,
        "duration_ms": total_ms,
        "research": research,
    }


# ------------------------------------------------------------------- executors
EXECUTORS: Dict[str, Any] = {
    "builtin:calculator": run_calculator,
    "builtin:datetime": run_datetime,
    "builtin:json_transform": run_json_transform,
    "builtin:web_search": run_search,
    "builtin:web_fetch": run_fetch,
    "builtin:web_research": run_research,
}


def builtin_implementation(name: Optional[str]) -> Optional[Any]:
    """Resolve an implementation key (``builtin:calculator``) to its executor."""
    if not name:
        return None
    return EXECUTORS.get(str(name))
