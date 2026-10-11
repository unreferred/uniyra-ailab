"""Valve schema/config abstraction for OmniLab V4.2.3.2.

A *Valve* is a typed, declarative configuration knob exposed by a provider,
tool, or subsystem (for example a search provider's ``max_results`` or the code
interpreter's ``memory_mb``). Valves describe **shape and policy** only: the
type, label, default, bounds and scope of a setting. Non-secret valve values are
ordinary project data and are stored unencrypted alongside the thing they
configure.

This module sits on the **Valve / Credential Vault boundary**:

* **Valves (this module)** are pure and permission-aware. They validate incoming
  values, clamp them against server-side *hard ceilings* (protected
  ``app.config`` attributes that a project may lower but never raise), and
  project a client-safe view. A valve may be declared ``SECRET`` (for example an
  API key), which is a routing signal, not storage: this module validates the
  plaintext and hands it back to the caller, who is responsible for writing it
  into the credential vault. This module never stores, encrypts, logs, hashes or
  returns secret material.
* **Credential Vault** (see ``tool_credentials``) owns encrypted secret storage.
  It never exposes plaintext, and secret values are referenced by id or
  ``{{secret:NAME}}`` template rather than by value.

Consequently the public projections here (``public_schema``, ``projection``) and
the hashing aid (``redact_for_hash``) are safe to hand to clients or feed into a
config hash: secret values are represented only as a boolean ``stored`` marker,
or dropped entirely.

The module is deliberately dependency-light and side-effect free: no database
access, no provider imports, and no import cycles. ``app.config`` is imported
lazily inside functions so ceilings can be monkeypatched in tests.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

__all__ = [
    "VALVE_TYPES",
    "VALVE_SCOPES",
    "TOOL_VALVE_NAMES",
    "ValveDefinition",
    "ValveSchema",
    "d",
    "public_schema",
    "ceiling_for",
    "effective_max",
    "validate_values",
    "projection",
    "RESEARCH_HARD_MAX_RESULTS",
    "RESEARCH_HARD_MAX_FETCHES",
    "search_provider_schema",
    "fetch_provider_schema",
    "code_interpreter_schema",
    "research_policy_schema",
    "tool_provider_kind",
    "tool_valve_schema",
    "required_secret_keys",
    "has_all_required",
    "redact_for_hash",
]

#: Supported valve value types.
VALVE_TYPES: Tuple[str, ...] = (
    "STRING",
    "SECRET",
    "INTEGER",
    "NUMBER",
    "BOOLEAN",
    "SELECT",
    "URL",
    "MULTISELECT",
    "JSON",
)

#: Where a valve's value is allowed to be set.
VALVE_SCOPES: Tuple[str, ...] = (
    "ADMIN_ONLY",
    "PROJECT",
    "CONVERSATION",
    "REQUEST",
)

#: Sentinel returned by internal normalizers when a value should be left as-is
#: (for example an empty secret meaning "keep the stored value").
_KEEP_EXISTING: Any = object()

#: Hard logical ceiling for research breadth. ``web_research`` cannot return
#: more than 10 search results, so neither search candidates nor fetch URLs may
#: be configured above this regardless of provider batch capacity.
RESEARCH_HARD_MAX_RESULTS = 10
RESEARCH_HARD_MAX_FETCHES = 10


@dataclass
class ValveDefinition:
    """Declarative description of a single configurable valve.

    A definition is metadata, not a value. ``SECRET`` valves additionally mark
    their value as belonging in the credential vault; ``public()`` never leaks
    such values (a secret's ``default`` is always masked to ``None``).

    Attributes:
        key: Stable machine name of the valve.
        type: One of :data:`VALVE_TYPES`.
        label: Human-readable label for UI rendering.
        description: Longer help text.
        required: Whether the valve must be satisfied before use.
        default: Default value applied when the valve is unset.
        options: For ``SELECT``/``MULTISELECT``, a list of
            ``{"value": ..., "label": ...}`` entries.
        secret: ``True`` when the value must be routed to the credential vault.
        min: Inclusive numeric lower bound for ``INTEGER``/``NUMBER``.
        max: Inclusive numeric upper bound for ``INTEGER``/``NUMBER``.
        min_length: Inclusive minimum length for ``STRING``/``URL``/``SECRET``.
        max_length: Inclusive maximum length for ``STRING``/``URL``/``SECRET``.
        pattern: Regex the whole value must match (``re.fullmatch``).
        scope: One of :data:`VALVE_SCOPES`.
        ceiling_attr: Name of an ``app.config`` attribute holding a protected
            server-side hard ceiling for this valve. Users may configure below
            it, never above it.
    """

    key: str
    type: str
    label: str
    description: str = ""
    required: bool = False
    default: Any = None
    options: Optional[List[Dict[str, Any]]] = None
    secret: bool = False
    min: Optional[float] = None
    max: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    scope: str = "PROJECT"
    ceiling_attr: Optional[str] = None

    def __post_init__(self) -> None:
        """Keep the ``secret`` flag consistent with the valve type."""
        if self.type == "SECRET":
            self.secret = True

    def public(self) -> Dict[str, Any]:
        """Return a JSON-safe, client-facing view of this definition.

        Includes every descriptive field. ``options`` is only emitted when
        present. Secret material is never included: when ``secret`` is set the
        ``default`` is masked to ``None`` regardless of what was configured.
        """
        payload: Dict[str, Any] = {
            "key": self.key,
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "required": bool(self.required),
            "default": None if self.secret else self.default,
            "secret": bool(self.secret),
            "scope": self.scope,
            "min": self.min,
            "max": self.max,
            "min_length": self.min_length,
            "max_length": self.max_length,
            "pattern": self.pattern,
            "ceiling_attr": self.ceiling_attr,
        }
        if self.options is not None:
            payload["options"] = [dict(option) for option in self.options]
        return payload


#: A schema maps valve keys to their definitions.
ValveSchema = Dict[str, ValveDefinition]


def d(key: str, type: str, label: str, **kwargs: Any) -> ValveDefinition:
    """Compact constructor for :class:`ValveDefinition`.

    Args:
        key: Stable machine name of the valve.
        type: One of :data:`VALVE_TYPES`.
        label: Human-readable label.
        **kwargs: Any remaining :class:`ValveDefinition` fields.

    Returns:
        The constructed definition.
    """
    return ValveDefinition(key=key, type=type, label=label, **kwargs)


def public_schema(schema: ValveSchema) -> Dict[str, Dict[str, Any]]:
    """Render a whole schema as ``{key: definition.public()}``."""
    return {key: definition.public() for key, definition in schema.items()}


# --------------------------------------------------------------------- ceilings


def _config_module() -> Any:
    """Import ``app.config`` lazily so ceilings stay monkeypatchable."""
    from app import config  # local import: avoids cycles and enables patching

    return config


def ceiling_for(definition: ValveDefinition) -> Optional[float]:
    """Return the protected server-side ceiling for a valve, if any.

    Reads the ``app.config`` attribute named by ``definition.ceiling_attr``.
    Importing config inside the function keeps the module importable without
    config side effects and lets tests monkeypatch the value.

    Args:
        definition: The valve definition to resolve a ceiling for.

    Returns:
        The ceiling as a float, or ``None`` when the valve has no ceiling or the
        configured attribute is missing/not coercible.
    """
    attr = definition.ceiling_attr
    if not attr:
        return None
    try:
        raw = getattr(_config_module(), attr, None)
    except Exception:  # pragma: no cover - config import should not fail
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def effective_max(definition: ValveDefinition) -> Optional[float]:
    """Return the tightest upper bound for a valve's numeric value.

    The effective maximum is the minimum of the valve's own ``max`` and any
    protected server-side ceiling. If only one is set, that one wins; if neither
    is set, ``None`` is returned and no upper bound applies.
    """
    candidates: List[float] = []
    if definition.max is not None:
        candidates.append(definition.max)
    ceiling = ceiling_for(definition)
    if ceiling is not None:
        candidates.append(ceiling)
    if not candidates:
        return None
    return min(candidates)


# -------------------------------------------------------------------- validation


def _option_values(definition: ValveDefinition) -> List[Any]:
    """Extract the allowed values from a definition's options."""
    return [option.get("value") for option in (definition.options or [])]


def _matches_an_option(value: Any, allowed: Sequence[Any]) -> bool:
    """Equality check tolerant of unhashable option values."""
    return any(value == candidate for candidate in allowed)


def _length_error(definition: ValveDefinition, key: str, value: str) -> Optional[str]:
    """Validate string length bounds, returning an error message or ``None``."""
    if definition.min_length is not None and len(value) < definition.min_length:
        return f"{key} must be at least {definition.min_length} characters"
    if definition.max_length is not None and len(value) > definition.max_length:
        return f"{key} must be at most {definition.max_length} characters"
    return None


def _pattern_error(definition: ValveDefinition, key: str, value: str) -> Optional[str]:
    """Validate a ``re.fullmatch`` pattern, returning an error or ``None``."""
    if not definition.pattern:
        return None
    try:
        if re.fullmatch(definition.pattern, value) is None:
            return f"{key} does not match the required format"
    except re.error:
        # A malformed server-side pattern must not blow up validation.
        return None
    return None


def _range_error(definition: ValveDefinition, key: str, number: float) -> Optional[str]:
    """Validate numeric bounds, mentioning the security ceiling when relevant."""
    if definition.min is not None and number < definition.min:
        return f"{key} must be at least {definition.min}"
    upper = effective_max(definition)
    if upper is not None and number > upper:
        if definition.ceiling_attr:
            return f"{key} cannot exceed the security ceiling of {upper}"
        return f"{key} must be at most {upper}"
    return None


def _normalize(
    definition: ValveDefinition, key: str, value: Any
) -> Tuple[bool, Any, Optional[str]]:
    """Normalize and validate a single non-absent value.

    Returns:
        ``(ok, normalized_value, error)``. When ``ok`` is ``False`` the error is
        populated and the value must be ignored. ``_KEEP_EXISTING`` is returned
        as the value when the input should leave any stored value untouched.
    """
    valve_type = definition.type

    if valve_type == "BOOLEAN":
        if isinstance(value, bool):
            return True, value, None
        if isinstance(value, int):
            if value in (0, 1):
                return True, bool(value), None
            return False, None, f"{key} must be a boolean"
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1"):
                return True, True, None
            if lowered in ("false", "0"):
                return True, False, None
        return False, None, f"{key} must be a boolean"

    if valve_type == "INTEGER":
        if isinstance(value, bool):
            return False, None, f"{key} must be an integer"
        try:
            number = int(value)
        except (TypeError, ValueError):
            return False, None, f"{key} must be an integer"
        error = _range_error(definition, key, number)
        if error:
            return False, None, error
        return True, number, None

    if valve_type == "NUMBER":
        if isinstance(value, bool):
            return False, None, f"{key} must be a number"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False, None, f"{key} must be a number"
        if not math.isfinite(number):
            return False, None, f"{key} must be a finite number"
        error = _range_error(definition, key, number)
        if error:
            return False, None, error
        return True, number, None

    if valve_type == "SELECT":
        allowed = _option_values(definition)
        if _matches_an_option(value, allowed):
            return True, value, None
        rendered = ", ".join(str(option) for option in allowed) or "(none)"
        return False, None, f"{key} must be one of: {rendered}"

    if valve_type == "MULTISELECT":
        if not isinstance(value, list):
            return False, None, f"{key} must be a list"
        allowed = _option_values(definition)
        for item in value:
            if not _matches_an_option(item, allowed):
                return False, None, f"{key} contains an unsupported option"
        return True, list(value), None

    if valve_type == "JSON":
        if isinstance(value, (dict, list)):
            return True, value, None
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return False, None, f"{key} must be a JSON object or array"
            if isinstance(parsed, (dict, list)):
                return True, parsed, None
        return False, None, f"{key} must be a JSON object or array"

    if valve_type in ("STRING", "URL"):
        if not isinstance(value, str):
            return False, None, f"{key} must be a string"
        error = _length_error(definition, key, value)
        if error:
            return False, None, error
        error = _pattern_error(definition, key, value)
        if error:
            return False, None, error
        if valve_type == "URL":
            if not (value.startswith("http://") or value.startswith("https://")):
                return False, None, f"{key} must start with http:// or https://"
            try:
                parts = urlsplit(value)
            except ValueError:
                return False, None, f"{key} must be a valid URL"
            if not parts.hostname:
                return False, None, f"{key} must be a valid URL"
        return True, value, None

    # Unknown types are a schema bug, but never a crash.
    return False, None, f"{key} has unsupported valve type '{valve_type}'"


def validate_values(
    schema: ValveSchema,
    raw_values: Any,
    *,
    existing_secret_keys: Iterable[str] = (),
) -> Tuple[Dict[str, Any], List[str]]:
    """Validate raw input against a schema.

    Unknown keys are ignored and never returned. Absent keys are left unset so
    the caller can preserve an existing value. ``SECRET`` values are validated as
    plaintext and returned **in plaintext** in the result, signalling to the
    caller that they belong in the credential vault; non-secret values are
    normalized (booleans, ints, floats, parsed JSON, ...).

    Args:
        schema: Valve definitions to validate against, in iteration order.
        raw_values: Caller-supplied mapping of key to raw value.
        existing_secret_keys: Secret keys already stored. Used so an absent,
            required secret is only an error when nothing is stored yet.

    Returns:
        ``(clean_values, errors)``. ``clean_values`` contains only schema keys
        that were provided and valid (plus validated secrets); ``errors`` is a
        list of human-readable messages. This function never raises on bad input.
    """
    if not isinstance(raw_values, dict):
        return {}, ["values must be an object"]

    existing = set(existing_secret_keys)
    clean: Dict[str, Any] = {}
    errors: List[str] = []

    for key, definition in schema.items():
        try:
            if key not in raw_values:
                if definition.type == "SECRET":
                    if definition.required and key not in existing:
                        errors.append(f"{key} is required")
                elif definition.required and definition.default is None:
                    errors.append(f"{key} is required")
                continue

            value = raw_values[key]

            if definition.type == "SECRET":
                if not isinstance(value, str):
                    errors.append(f"{key} must be a string")
                    continue
                if not value.strip():
                    # Blank secret means "keep the stored value".
                    continue
                length_error = _length_error(definition, key, value)
                if length_error:
                    errors.append(length_error)
                    continue
                clean[key] = value
                continue

            ok, normalized, error = _normalize(definition, key, value)
            if not ok:
                errors.append(error or f"{key} is invalid")
                continue
            if normalized is _KEEP_EXISTING:
                continue
            clean[key] = normalized
        except Exception:  # defensive: validation must never raise
            errors.append(f"{key} is invalid")

    return clean, errors


# -------------------------------------------------------------------- projection


def projection(
    schema: ValveSchema,
    stored_values: Any,
    *,
    secret_keys: Iterable[str] = (),
) -> Dict[str, Any]:
    """Build a client-safe view of stored values, defaults filled in.

    Non-secret valves resolve to their stored value or, when missing, their
    default. Every ``SECRET`` valve is replaced by ``{"stored": bool}`` so a
    client can tell whether a secret exists without ever receiving plaintext.
    Stored secret values are dropped even if present in ``stored_values``.
    """
    stored = stored_values if isinstance(stored_values, dict) else {}
    secrets = set(secret_keys)
    result: Dict[str, Any] = {}
    for key, definition in schema.items():
        if definition.type == "SECRET":
            result[key] = {"stored": key in secrets}
            continue
        if key in stored:
            result[key] = stored[key]
        else:
            result[key] = definition.default
    return result


# ----------------------------------------------------------- provider schemas


def search_provider_schema(provider_type: str) -> ValveSchema:
    """Return the valve schema for a search provider type ({} when unknown).

    TinyFish Search exposes provider/account quota valves (rolling per-minute and
    per-hour request limits). These are the primary throttle for the provider
    account and are distinct from per-run budgets: an operator may lower them to
    stay under the provider quota, but never raise them above the protected
    server ceilings (``ceiling_attr``).
    """
    normalized = (provider_type or "").strip().lower()
    if normalized == "tinyfish":
        return {
            "api_key": d(
                "api_key",
                "SECRET",
                "API Key",
                required=True,
                scope="PROJECT",
            ),
            "max_results": d(
                "max_results",
                "INTEGER",
                "Max Results",
                default=5,
                min=1,
                max=20,
            ),
            "timeout_seconds": d(
                "timeout_seconds",
                "INTEGER",
                "Timeout (seconds)",
                default=15,
                min=1,
                max=60,
                ceiling_attr="SEARCH_REQUEST_TIMEOUT_MAX_SECONDS",
            ),
            # Optional regional hints for TinyFish Search. Project-configurable
            # via valves; the provider falls back to the same defaults when a
            # run resolves no stored value. A tool-call argument may override.
            "location": d(
                "location",
                "STRING",
                "Location",
                description="Regional search hint (ISO 3166-1 alpha-2, e.g. ID)",
                default="ID",
                max_length=64,
            ),
            "language": d(
                "language",
                "STRING",
                "Language",
                description="Result language hint (ISO 639-1, e.g. id)",
                default="id",
                max_length=64,
            ),
            # Provider/account quota limits (primary throttle), distinct from
            # per-run budgets. Lower them to throttle; the protected server
            # ceilings prevent raising above the account/quota allowance.
            "search_rpm_limit": d(
                "search_rpm_limit",
                "INTEGER",
                "Search requests / minute",
                description="TinyFish Search rolling per-minute provider quota (lower to throttle)",
                default=30,
                min=1,
                max=120,
                ceiling_attr="TINYFISH_SEARCH_RPM_CEILING",
            ),
            "search_hourly_limit": d(
                "search_hourly_limit",
                "INTEGER",
                "Search requests / hour",
                description="TinyFish Search rolling per-hour provider quota (lower to throttle)",
                default=500,
                min=1,
                max=5000,
                ceiling_attr="TINYFISH_SEARCH_HOURLY_CEILING",
            ),
        }
    return {}


def fetch_provider_schema(provider_type: str) -> ValveSchema:
    """Return the valve schema for a fetch provider type ({} when unknown).

    TinyFish Fetch exposes provider/account quota valves (rolling per-minute and
    per-day URL limits). These are the primary throttle for the provider account
    and are distinct from per-run budgets: an operator may lower them to stay
    under the provider quota, but never raise them above the protected server
    ceilings (``ceiling_attr``). The ``standard`` fetch branch intentionally does
    not define quota valves.
    """
    normalized = (provider_type or "").strip().lower()
    if normalized == "tinyfish":
        return {
            "api_key": d(
                "api_key",
                "SECRET",
                "API Key",
                required=True,
                scope="PROJECT",
            ),
            "format": d(
                "format",
                "SELECT",
                "Format",
                default="markdown",
                options=[
                    {"value": "markdown", "label": "Markdown"},
                    {"value": "html", "label": "HTML"},
                    {"value": "json", "label": "JSON"},
                ],
            ),
            "timeout_seconds": d(
                "timeout_seconds",
                "INTEGER",
                "Timeout (seconds)",
                default=20,
                min=1,
                max=60,
                ceiling_attr="FETCH_REQUEST_TIMEOUT_MAX_SECONDS",
            ),
            "max_length": d(
                "max_length",
                "INTEGER",
                "Max Length",
                default=100000,
                min=1000,
                max=100000,
            ),
            "max_urls": d(
                "max_urls",
                "INTEGER",
                "Batch size (provider batch cap)",
                description=(
                    "Provider execution batch size: how many URLs one provider "
                    "request carries. This is provider batching, NOT research "
                    "breadth (see Web Research settings)."
                ),
                default=5,
                min=1,
                max=100,
                ceiling_attr="MAX_BATCH_FETCH_URLS",
            ),
            # Provider/account quota limits (primary throttle), distinct from
            # per-run budgets. URL-based rolling quotas for the TinyFish Fetch
            # account; lower to throttle, ceilings cap how high they may go.
            "fetch_urls_per_minute_limit": d(
                "fetch_urls_per_minute_limit",
                "INTEGER",
                "Fetch URLs / minute",
                description="TinyFish Fetch rolling per-minute URL quota (URL-based, lower to throttle)",
                default=150,
                min=1,
                max=500,
                ceiling_attr="TINYFISH_FETCH_URLS_PER_MINUTE_CEILING",
            ),
            "fetch_urls_per_day_limit": d(
                "fetch_urls_per_day_limit",
                "INTEGER",
                "Fetch URLs / day",
                description="TinyFish Fetch rolling 24h URL quota (URL-based, lower to throttle)",
                default=1000,
                min=1,
                max=10000,
                ceiling_attr="TINYFISH_FETCH_URLS_PER_DAY_CEILING",
            ),
        }
    if normalized == "standard":
        return {
            "timeout_seconds": d(
                "timeout_seconds",
                "INTEGER",
                "Timeout (seconds)",
                default=15,
                min=1,
                max=60,
                ceiling_attr="FETCH_REQUEST_TIMEOUT_MAX_SECONDS",
            ),
            "max_response_bytes": d(
                "max_response_bytes",
                "INTEGER",
                "Max Response Bytes",
                default=512000,
                min=1024,
                max=2048000,
            ),
            "max_urls": d(
                "max_urls",
                "INTEGER",
                "Batch size (provider batch cap)",
                description=(
                    "Provider execution batch size: how many URLs one provider "
                    "request carries. This is provider batching, NOT research "
                    "breadth (see Web Research settings)."
                ),
                default=5,
                min=1,
                max=100,
                ceiling_attr="MAX_BATCH_FETCH_URLS",
            ),
        }
    return {}


def code_interpreter_schema() -> ValveSchema:
    """Return the valve schema for the sandboxed code interpreter.

    Defaults and ceilings come from ``app.config`` and are read defensively so
    the module still imports (with sane fallbacks) if an attribute is missing.
    Each ``ceiling_attr`` names a protected server-side hard limit: a project may
    request less, never more.
    """
    config = _config_module()
    timeout_seconds = int(
        getattr(config, "CODE_INTERPRETER_TIMEOUT_SECONDS", 30) or 30
    )
    memory_mb = int(getattr(config, "CODE_INTERPRETER_MEMORY_MB", 512) or 512)
    try:
        cpus = float(getattr(config, "CODE_INTERPRETER_CPUS", 1.0) or 1.0)
    except (TypeError, ValueError):
        cpus = 1.0
    max_output_bytes = int(
        getattr(config, "CODE_INTERPRETER_MAX_OUTPUT_BYTES", 204800) or 204800
    )

    return {
        "timeout_seconds": d(
            "timeout_seconds",
            "INTEGER",
            "Timeout (seconds)",
            default=timeout_seconds,
            min=1,
            max=timeout_seconds,
            ceiling_attr="CODE_INTERPRETER_TIMEOUT_SECONDS",
        ),
        "memory_mb": d(
            "memory_mb",
            "INTEGER",
            "Memory (MB)",
            default=memory_mb,
            min=64,
            ceiling_attr="CODE_INTERPRETER_MEMORY_MB",
        ),
        "cpus": d(
            "cpus",
            "NUMBER",
            "CPUs",
            default=cpus,
            min=0.1,
            ceiling_attr="CODE_INTERPRETER_CPUS",
        ),
        "max_output_bytes": d(
            "max_output_bytes",
            "INTEGER",
            "Max Output Bytes",
            default=max_output_bytes,
            min=1024,
            ceiling_attr="CODE_INTERPRETER_MAX_OUTPUT_BYTES",
        ),
    }


def research_policy_schema() -> ValveSchema:
    """Return the valve schema for the ``web_research`` breadth policy.

    Research *breadth* is a logical concept: how many search candidates and
    fetch URLs a single ``web_research`` operation may inspect. It is distinct
    from provider batch capacity (the fetch provider ``max_urls`` valve) and
    from provider quota. Defaults come from ``app.config`` and are read
    defensively, then clamped into ``[1, hard max]`` so a misconfigured server
    value can never widen the hard logical ceiling.
    """
    config = _config_module()

    def _int_attr(attr: str, fallback: int) -> int:
        raw = getattr(config, attr, fallback)
        if raw is None:
            return fallback
        try:
            return int(raw)
        except (TypeError, ValueError):
            return fallback

    def _clamp(value: int, hard_max: int) -> int:
        return max(1, min(value, hard_max))

    default_results = _clamp(
        _int_attr("RESEARCH_DEFAULT_MAX_RESULTS", 5), RESEARCH_HARD_MAX_RESULTS
    )
    default_fetches = _clamp(
        _int_attr("RESEARCH_DEFAULT_MAX_FETCHES", 5), RESEARCH_HARD_MAX_FETCHES
    )
    ceiling_results = _clamp(
        _int_attr("RESEARCH_MAX_RESULTS_CEILING", 10), RESEARCH_HARD_MAX_RESULTS
    )
    ceiling_fetches = _clamp(
        _int_attr("RESEARCH_MAX_FETCHES_CEILING", 10), RESEARCH_HARD_MAX_FETCHES
    )

    return {
        "research_default_max_results": d(
            "research_default_max_results",
            "INTEGER",
            "Default search results",
            description="Used when the model does not request max_results.",
            default=default_results,
            min=1,
            max=RESEARCH_HARD_MAX_RESULTS,
        ),
        "research_default_max_fetches": d(
            "research_default_max_fetches",
            "INTEGER",
            "Default fetch URLs",
            description="Used when the model does not request max_fetches.",
            default=default_fetches,
            min=1,
            max=RESEARCH_HARD_MAX_FETCHES,
        ),
        "research_max_results_ceiling": d(
            "research_max_results_ceiling",
            "INTEGER",
            "Maximum search results",
            description=(
                "Maximum search breadth one research operation may request; "
                "cannot exceed the hard server ceiling of 10."
            ),
            default=ceiling_results,
            min=1,
            max=RESEARCH_HARD_MAX_RESULTS,
        ),
        "research_max_fetches_ceiling": d(
            "research_max_fetches_ceiling",
            "INTEGER",
            "Maximum fetch URLs",
            description=(
                "Maximum fetch breadth one research operation may request; "
                "cannot exceed the hard server ceiling of 10."
            ),
            default=ceiling_fetches,
            min=1,
            max=RESEARCH_HARD_MAX_FETCHES,
        ),
    }


#: Builtin tool name -> provider kind whose provider-level valves configure it.
TOOL_VALVE_NAMES: Dict[str, str] = {
    "web_search": "search",
    "web_fetch": "fetch",
    "web_research": "search",
}


def tool_provider_kind(tool_doc: Any) -> Optional[str]:
    """Return ``"search"``/``"fetch"`` for builtin web tools, else ``None``."""
    if not isinstance(tool_doc, dict):
        return None
    name = tool_doc.get("name")
    return TOOL_VALVE_NAMES.get(name)


def tool_valve_schema(tool_doc: Any) -> ValveSchema:
    """Return the valve schema owned directly by a tool.

    Provider-backed builtins (``web_search``/``web_fetch``) are configured via
    their provider's valves and therefore expose no tool-local valves here. The
    code interpreter exposes its sandbox limits. Everything else (for example
    the Calculator) has no configurable settings.
    """
    if not isinstance(tool_doc, dict):
        return {}
    name = tool_doc.get("name")
    tool_type = tool_doc.get("tool_type")
    implementation = tool_doc.get("implementation")

    if name == "web_research":
        return research_policy_schema()
    if name in TOOL_VALVE_NAMES:
        return {}
    if tool_type == "code_interpreter" or name == "code_interpreter":
        return code_interpreter_schema()
    if implementation == "builtin:code_interpreter":
        return code_interpreter_schema()
    return {}


# --------------------------------------------------------------------- helpers


def required_secret_keys(schema: ValveSchema) -> List[str]:
    """Return the keys of required ``SECRET`` valves, in schema order."""
    return [
        key
        for key, definition in schema.items()
        if definition.type == "SECRET" and definition.required
    ]


def has_all_required(
    schema: ValveSchema,
    stored_values: Any,
    secret_keys: Iterable[str] = (),
) -> bool:
    """Return whether every required valve is satisfied.

    Required secrets must appear in ``secret_keys``. Other required valves must
    be present in ``stored_values`` or carry a non-``None`` default.
    """
    stored = stored_values if isinstance(stored_values, dict) else {}
    secrets = set(secret_keys)
    for key, definition in schema.items():
        if not definition.required:
            continue
        if definition.type == "SECRET":
            if key not in secrets:
                return False
        elif key not in stored and definition.default is None:
            return False
    return True


def redact_for_hash(
    values: Any,
    *,
    secret_keys: Iterable[str] = (),
) -> Dict[str, Any]:
    """Return a stable shallow copy safe to feed into a config hash.

    Drops anything secret. When ``values`` is a :data:`ValveSchema` (values are
    :class:`ValveDefinition` objects), ``SECRET`` definitions are removed. When
    ``values`` is a plain mapping of valve key to value, keys listed in
    ``secret_keys`` are removed. Secret plaintext is therefore never hashed.

    Args:
        values: A schema or a mapping of valve key to value.
        secret_keys: Secret keys to drop when ``values`` is a plain mapping.

    Returns:
        A new dict with secret entries removed.
    """
    if not isinstance(values, dict):
        return {}
    secrets = set(secret_keys)
    result: Dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, ValveDefinition):
            if value.type == "SECRET":
                continue
            result[key] = value
            continue
        if key in secrets:
            continue
        result[key] = value
    return result
