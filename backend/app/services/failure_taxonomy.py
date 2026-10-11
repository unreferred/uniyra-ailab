"""Canonical quality / runtime failure taxonomy for OmniLab V5.

This module is deliberately self-contained: it imports nothing from
``app.models`` (so it can never participate in an import cycle) and keeps its own
copy of the execution ``ERROR_CATEGORIES`` vocabulary that it classifies. It is
the single source of truth for turning execution errors, evaluation findings and
security signals into stable, dot-namespaced taxonomy codes.

Runtime vs. quality
-------------------
A *runtime* failure means the system could not carry out the request: the
provider errored, the request timed out, the provider returned malformed output,
a tool crashed while running, or the run could not be finalized. Runtime failures
say nothing about the quality of an answer and use the ``operational`` severity
scale (``low`` -> ``critical``).

A *quality* failure means the system ran fine and produced an answer that is
wrong, incomplete, ungrounded, uncited, or that ignored instructions. Quality
failures use the ``quality`` severity scale (``info`` -> ``high``). The two must
never be conflated: a "knowledge" problem is a quality problem, not a runtime or
security problem.

Research findings (source quality, synthesis, evidence freshness) and security
signals are separate domains with their own vocabularies. Security findings use
the ``security`` scale (``security_note`` -> ``security_critical``) so an
answer-quality ``high`` can never be compared with, or promoted to, a security
``high``.

Real example
------------
Prompt: "Without using web search or external tools, ...". The runtime is healthy,
but the model called ``code_interpreter`` four times. That is a
``QUALITY.INSTRUCTION_FOLLOWING`` failure with related ``QUALITY.TOOL_SELECTION``
-- NOT a runtime failure and NOT a knowledge/correctness failure.
"""
from typing import Any, Dict, List, Optional, Tuple

DOMAINS: Tuple[str, ...] = ("RUNTIME", "QUALITY", "RESEARCH", "SECURITY")

# Domain -> the severity vocabulary its findings are scored on. Keeping these
# separate is what stops answer-quality severity being read as security
# semantics (or vice versa).
SEVERITY_SCALES: Dict[str, str] = {
    "RUNTIME": "operational",
    "QUALITY": "quality",
    "RESEARCH": "research",
    "SECURITY": "security",
}

OPERATIONAL_SEVERITIES = ("low", "medium", "high", "critical")
QUALITY_SEVERITIES = ("info", "low", "medium", "high")
RESEARCH_SEVERITIES = ("info", "low", "medium", "high")
SECURITY_SEVERITIES = (
    "security_note",
    "security_low",
    "security_medium",
    "security_high",
    "security_critical",
)

SEVERITIES_BY_SCALE: Dict[str, Tuple[str, ...]] = {
    "operational": OPERATIONAL_SEVERITIES,
    "quality": QUALITY_SEVERITIES,
    "research": RESEARCH_SEVERITIES,
    "security": SECURITY_SEVERITIES,
}

# (code, domain, category, label, description, default_severity)
_ENTRIES: Tuple[Tuple[str, str, str, str, str, str], ...] = (
    # ------------------------------------------------------------------ RUNTIME
    (
        "RUNTIME.PROVIDER_ERROR", "RUNTIME", "provider_error",
        "Provider error",
        "The upstream provider failed to complete the request.",
        "high",
    ),
    (
        "RUNTIME.TIMEOUT", "RUNTIME", "timeout",
        "Execution timeout",
        "The request or upstream call did not finish within its deadline.",
        "medium",
    ),
    (
        "RUNTIME.TOOL_RUNTIME_ERROR", "RUNTIME", "tool_runtime_error",
        "Tool runtime error",
        "A tool failed while executing (not a tool-selection mistake).",
        "high",
    ),
    (
        "RUNTIME.MALFORMED_PROVIDER_RESPONSE", "RUNTIME", "malformed_provider_response",
        "Malformed provider response",
        "The provider returned output that could not be parsed.",
        "medium",
    ),
    (
        "RUNTIME.FINALIZATION_FAILURE", "RUNTIME", "finalization_failure",
        "Finalization failure",
        "The run could not be finalized into a complete answer.",
        "high",
    ),
    (
        "RUNTIME.RUNTIME_FAILURE", "RUNTIME", "runtime_failure",
        "Runtime failure",
        "A generic runtime failure (e.g. context assembly) prevented completion.",
        "high",
    ),
    # ------------------------------------------------------------------ QUALITY
    (
        "QUALITY.CORRECTNESS", "QUALITY", "correctness",
        "Incorrect answer",
        "The answer is factually or logically wrong.",
        "high",
    ),
    (
        "QUALITY.INCOMPLETENESS", "QUALITY", "incompleteness",
        "Incomplete answer",
        "The answer omits required content or leaves the task unfinished.",
        "medium",
    ),
    (
        "QUALITY.INSTRUCTION_FOLLOWING", "QUALITY", "instruction_following",
        "Instruction not followed",
        "The answer violates an explicit instruction in the prompt.",
        "high",
    ),
    (
        "QUALITY.GROUNDING", "QUALITY", "grounding",
        "Ungrounded answer",
        "The answer is not grounded in the provided or retrieved evidence.",
        "medium",
    ),
    (
        "QUALITY.CITATION", "QUALITY", "citation",
        "Citation problem",
        "Citations are missing, malformed or do not support the claim.",
        "medium",
    ),
    (
        "QUALITY.TOOL_SELECTION", "QUALITY", "tool_selection",
        "Wrong tool selected",
        "The model used the wrong tool, or none, for the task.",
        "medium",
    ),
    (
        "QUALITY.UNNECESSARY_TOOL_USE", "QUALITY", "unnecessary_tool_use",
        "Unnecessary tool use",
        "The model called a tool that was not needed for the task.",
        "low",
    ),
    (
        "QUALITY.MISSING_REQUIRED_TOOL", "QUALITY", "missing_required_tool",
        "Required tool not used",
        "The model failed to call a tool the task required.",
        "medium",
    ),
    (
        "QUALITY.HALLUCINATION", "QUALITY", "hallucination",
        "Hallucinated content",
        "The answer invents facts, sources or claims that are unsupported.",
        "high",
    ),
    (
        "QUALITY.EFFICIENCY", "QUALITY", "efficiency",
        "Inefficient answer",
        "The answer was produced with avoidable cost, latency or repetition.",
        "low",
    ),
    # Additive (V6.7 Agent & Prompt Evaluation): agent/prompt quality failures.
    # They are QUALITY failures -- never RUNTIME and never SECURITY; security
    # signals stay in their own domain below.
    (
        "QUALITY.POLICY_COMPLIANCE", "QUALITY", "policy_compliance",
        "Policy not complied with",
        "The run violated a resolved agent permission or runtime policy.",
        "high",
    ),
    (
        "QUALITY.INVARIANT_COMPLIANCE", "QUALITY", "invariant_compliance",
        "Invariant not complied with",
        "A required invariant was missing from or violated by the run.",
        "high",
    ),
    (
        "QUALITY.CONTEXT_USAGE", "QUALITY", "context_usage",
        "Context misuse",
        "The run exceeded its context budget or excluded mandatory context.",
        "medium",
    ),
    (
        "QUALITY.TASK_COMPLETION", "QUALITY", "task_completion",
        "Task not completed",
        "The run did not complete the requested task.",
        "medium",
    ),
    (
        "QUALITY.SCOPE_ADHERENCE", "QUALITY", "scope_adherence",
        "Scope not adhered to",
        "The run went outside the task's declared scope.",
        "medium",
    ),
    (
        "QUALITY.ROLE_ADHERENCE", "QUALITY", "role_adherence",
        "Role not adhered to",
        "The run departed from the agent's declared role or persona.",
        "medium",
    ),
    (
        "QUALITY.DRIFT", "QUALITY", "drift",
        "Behavioural drift",
        "The run drifted from the intended behaviour over the task.",
        "medium",
    ),
    # Additive (V6.9 Drift & Failure Analysis): structural / prompt drift is a
    # quality failure -- never RUNTIME, never SECURITY. Existing entries above
    # (including QUALITY.DRIFT) are unchanged.
    (
        "QUALITY.PROMPT_DRIFT", "QUALITY", "prompt_drift",
        "Prompt drift",
        "The prompt/output drifted from the declared invariants, phases or scope.",
        "medium",
    ),
    (
        "QUALITY.DESIGN_DRIFT", "QUALITY", "design_drift",
        "Design drift",
        "The change set drifted from declared design tokens or UI patterns.",
        "medium",
    ),
    (
        "QUALITY.ARCHITECTURE_DRIFT", "QUALITY", "architecture_drift",
        "Architecture drift",
        "The change set duplicated or diverged from declared service boundaries.",
        "high",
    ),
    (
        "QUALITY.LOGIC_DRIFT", "QUALITY", "logic_drift",
        "Logic drift",
        "Business logic or enum semantics diverged between declared layers.",
        "high",
    ),
    # ----------------------------------------------------------------- RESEARCH
    (
        "RESEARCH.INSUFFICIENT_SOURCES", "RESEARCH", "insufficient_sources",
        "Insufficient sources",
        "Too few sources were gathered to support the research task.",
        "medium",
    ),
    (
        "RESEARCH.POOR_SOURCE_QUALITY", "RESEARCH", "poor_source_quality",
        "Poor source quality",
        "The sources used are low-quality, unreliable or not authoritative.",
        "medium",
    ),
    (
        "RESEARCH.SOURCE_DISAGREEMENT", "RESEARCH", "source_disagreement",
        "Sources disagree",
        "Credible sources contradict each other and the conflict is unresolved.",
        "low",
    ),
    (
        "RESEARCH.UNSUPPORTED_SYNTHESIS", "RESEARCH", "unsupported_synthesis",
        "Unsupported synthesis",
        "The synthesis or conclusion overgeneralizes beyond the evidence.",
        "high",
    ),
    (
        "RESEARCH.STALE_EVIDENCE", "RESEARCH", "stale_evidence",
        "Stale evidence",
        "The evidence relied on is outdated or expired.",
        "low",
    ),
    # ----------------------------------------------------------------- SECURITY
    (
        "SECURITY.CROSS_PROJECT_ACCESS", "SECURITY", "cross_project_access",
        "Cross-project access",
        "An attempt to read or write data belonging to another project or tenant.",
        "security_high",
    ),
    (
        "SECURITY.CREDENTIAL_EXPOSURE", "SECURITY", "credential_exposure",
        "Credential exposure",
        "A credential, secret or token was exposed or extracted.",
        "security_critical",
    ),
    (
        "SECURITY.UNSAFE_TOOL_ACCESS", "SECURITY", "unsafe_tool_access",
        "Unsafe tool access",
        "A forbidden, dangerous or insufficiently permitted tool was accessed.",
        "security_high",
    ),
    (
        "SECURITY.POLICY_BOUNDARY_VIOLATION", "SECURITY", "policy_boundary_violation",
        "Policy boundary violation",
        "A security policy boundary was crossed without a more specific signal.",
        "security_medium",
    ),
)

TAXONOMY: Dict[str, Dict[str, Any]] = {
    code: {
        "code": code,
        "domain": domain,
        "category": category,
        "label": label,
        "description": description,
        "severity_scale": SEVERITY_SCALES[domain],
        "default_severity": default_severity,
    }
    for code, domain, category, label, description, default_severity in _ENTRIES
}

TAXONOMY_CODES: Tuple[str, ...] = tuple(TAXONOMY.keys())

_CODE_BY_LOWER: Dict[str, str] = {code.lower(): code for code in TAXONOMY}
_CATEGORY_BY_LOWER: Dict[str, str] = {}
for _code, _entry in TAXONOMY.items():
    _CATEGORY_BY_LOWER.setdefault(_entry["category"].lower(), _code)


def _validate() -> None:
    """Fail fast if the taxonomy definition violates its own invariants."""
    for code, entry in TAXONOMY.items():
        scale = entry["severity_scale"]
        if entry["default_severity"] not in SEVERITIES_BY_SCALE[scale]:
            raise ValueError(f"{code}: invalid default severity {entry['default_severity']!r}")
        if entry["domain"] not in DOMAINS:
            raise ValueError(f"{code}: unknown domain {entry['domain']!r}")


_validate()

# Execution error categories mirroring app.errors.ERROR_CATEGORIES. Kept local
# so this module remains importable without app.errors / app.models.
EXECUTION_ERROR_CATEGORIES: Tuple[str, ...] = (
    "AUTH",
    "RATE_LIMIT",
    "TIMEOUT",
    "PROVIDER",
    "MODEL_UNAVAILABLE",
    "VALIDATION",
    "NETWORK",
    "MALFORMED_RESPONSE",
    "INTERNAL",
    "CANCELLED",
)

# Evaluation finding categories (app.models.EVALUATION_FINDING_CATEGORIES plus
# the judge-only dimensions "completeness" and "source_quality").
_BASE_FINDING_CODES: Dict[str, str] = {
    "correctness": "QUALITY.CORRECTNESS",
    "completeness": "QUALITY.INCOMPLETENESS",
    "instruction_following": "QUALITY.INSTRUCTION_FOLLOWING",
    "grounding": "QUALITY.GROUNDING",
    "citation": "QUALITY.CITATION",
    "tool_selection": "QUALITY.TOOL_SELECTION",
    "tool_failure": "RUNTIME.TOOL_RUNTIME_ERROR",
    "efficiency": "QUALITY.EFFICIENCY",
    "runtime": "RUNTIME.RUNTIME_FAILURE",
    "data": "QUALITY.CORRECTNESS",
    "security": "SECURITY.POLICY_BOUNDARY_VIOLATION",
    "source_quality": "RESEARCH.POOR_SOURCE_QUALITY",
    # Additive (V6.7): agent/prompt finding categories.
    "policy_compliance": "QUALITY.POLICY_COMPLIANCE",
    "invariant_compliance": "QUALITY.INVARIANT_COMPLIANCE",
    "context_usage": "QUALITY.CONTEXT_USAGE",
    "task_completion": "QUALITY.TASK_COMPLETION",
    "scope_adherence": "QUALITY.SCOPE_ADHERENCE",
    "role_adherence": "QUALITY.ROLE_ADHERENCE",
    "drift": "QUALITY.DRIFT",
    # Additive (V6.9): drift finding categories.
    "prompt_drift": "QUALITY.PROMPT_DRIFT",
    "design_drift": "QUALITY.DESIGN_DRIFT",
    "architecture_drift": "QUALITY.ARCHITECTURE_DRIFT",
    "logic_drift": "QUALITY.LOGIC_DRIFT",
}

_DOMAIN_FALLBACK: Dict[str, str] = {
    "RUNTIME": "RUNTIME.RUNTIME_FAILURE",
    "QUALITY": "QUALITY.CORRECTNESS",
    "RESEARCH": "RESEARCH.UNSUPPORTED_SYNTHESIS",
    "SECURITY": "SECURITY.POLICY_BOUNDARY_VIOLATION",
}

# Message keyword refinements, applied in order (first match wins).
_MESSAGE_REFINEMENTS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("insufficient source", "not enough source", "too few source"), "RESEARCH.INSUFFICIENT_SOURCES"),
    (("poor source", "low quality source", "unreliable source"), "RESEARCH.POOR_SOURCE_QUALITY"),
    (("source disagree", "sources disagree", "conflicting source", "sources conflict", "conflicting sources", "contradict"), "RESEARCH.SOURCE_DISAGREEMENT"),
    (("unsupported synthesis", "unsupported conclusion", "overgeneral"), "RESEARCH.UNSUPPORTED_SYNTHESIS"),
    (("stale", "outdated", "expired"), "RESEARCH.STALE_EVIDENCE"),
    (("hallucinat", "fabricat", "unsupported claim", "not supported by"), "QUALITY.HALLUCINATION"),
    (("unnecessary tool", "redundant tool", "excessive tool"), "QUALITY.UNNECESSARY_TOOL_USE"),
    (("missing required tool", "required tool"), "QUALITY.MISSING_REQUIRED_TOOL"),
)

# Security refinements, applied in order (first match wins). Credential checks
# come first so a message mentioning both credentials and projects is scored as
# credential exposure.
_SECURITY_REFINEMENTS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("credential", "secret", "token", "api key", "password"), "SECURITY.CREDENTIAL_EXPOSURE"),
    (("cross-project", "cross project", "foreign project", "tenant", "other project"), "SECURITY.CROSS_PROJECT_ACCESS"),
    (("unsafe tool", "forbidden tool", "tool access"), "SECURITY.UNSAFE_TOOL_ACCESS"),
)

# Terms that indicate the answer was produced with (or should have avoided) a
# tool. Used only to attach the instruction-following <-> tool-selection link.
_TOOL_MENTION_TERMS: Tuple[str, ...] = (
    "tool", "code_interpreter", "code interpreter", "web_search", "web search",
    "fetch", "browser", "sandbox", "function call",
)

_TOOL_QUALITY_CODES = (
    "QUALITY.TOOL_SELECTION",
    "QUALITY.UNNECESSARY_TOOL_USE",
    "QUALITY.MISSING_REQUIRED_TOOL",
)


def normalize_code(code: Optional[str]) -> Optional[str]:
    """Return the canonical taxonomy code for a code or category name.

    Accepts either a dotted code (``"QUALITY.CITATION"``) or a bare category
    name (``"citation"``), case-insensitively and ignoring surrounding
    whitespace. Returns ``None`` for anything unknown.
    """
    if not code or not isinstance(code, str):
        return None
    key = code.strip().lower()
    if not key:
        return None
    if key in _CODE_BY_LOWER:
        return _CODE_BY_LOWER[key]
    return _CATEGORY_BY_LOWER.get(key)


def describe(code: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return a copy of the taxonomy entry for ``code``, or ``None``."""
    canonical = normalize_code(code)
    if canonical is None:
        return None
    return dict(TAXONOMY[canonical])


def domain_of(code_or_category: Optional[str]) -> Optional[str]:
    """Return the domain owning a code or category name, or ``None``."""
    if not code_or_category or not isinstance(code_or_category, str):
        return None
    key = code_or_category.strip().upper()
    if key in DOMAINS:
        return key
    canonical = normalize_code(code_or_category)
    if canonical is None:
        return None
    return TAXONOMY[canonical]["domain"]


def severity_scale_for(domain: Optional[str]) -> Optional[str]:
    """Return the severity scale name for a domain (case-insensitive)."""
    if not domain or not isinstance(domain, str):
        return None
    return SEVERITY_SCALES.get(domain.strip().upper())


def _mentions_tools(message: str) -> bool:
    text = (message or "").lower()
    return any(term in text for term in _TOOL_MENTION_TERMS)


def _refine_from_message(message: str) -> Optional[str]:
    text = (message or "").lower()
    for needles, code in _MESSAGE_REFINEMENTS:
        if any(needle in text for needle in needles):
            return code
    return None


def _refine_security(message: str) -> str:
    text = (message or "").lower()
    for needles, code in _SECURITY_REFINEMENTS:
        if any(needle in text for needle in needles):
            return code
    return "SECURITY.POLICY_BOUNDARY_VIOLATION"


def _resolve_finding_code(category: str, message: str) -> str:
    normalized = (category or "").strip().lower()
    base = _BASE_FINDING_CODES.get(normalized, "QUALITY.CORRECTNESS")
    if normalized == "security":
        return _refine_security(message)
    # Documented instruction_following / tool_selection quality failure: an
    # instruction violation that involved tools stays an instruction violation
    # (never runtime, never a knowledge failure).
    if normalized == "instruction_following" and _mentions_tools(message):
        return "QUALITY.INSTRUCTION_FOLLOWING"
    refined = _refine_from_message(message)
    if refined:
        return refined
    return base


def _related_codes(code: str, category: str, message: str) -> List[str]:
    normalized = (category or "").strip().lower()
    text = (message or "").lower()
    related: List[str] = []

    if (
        normalized == "instruction_following"
        and _mentions_tools(message)
        and code == "QUALITY.INSTRUCTION_FOLLOWING"
    ):
        related.append("QUALITY.TOOL_SELECTION")

    if code in _TOOL_QUALITY_CODES and "instruction" in text:
        related.append("QUALITY.INSTRUCTION_FOLLOWING")

    if code in _TOOL_QUALITY_CODES and code != "QUALITY.TOOL_SELECTION":
        related.append("QUALITY.TOOL_SELECTION")

    deduped: List[str] = []
    for candidate in related:
        if candidate != code and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def classify_execution(
    error_category: str,
    error_code: str = "",
    message: str = "",
) -> Dict[str, Any]:
    """Map an execution error to the canonical RUNTIME taxonomy entry.

    ``error_category`` is one of :data:`EXECUTION_ERROR_CATEGORIES`; ``error_code``
    and ``message`` provide free-text hints. Rules are applied in priority order:
    tool -> finalization -> context -> timeout/network -> malformed -> provider.
    """
    haystack = f"{error_code} {message}".lower()
    category = (error_category or "").strip().upper()

    if "tool" in haystack:
        code = "RUNTIME.TOOL_RUNTIME_ERROR"
    elif "finaliz" in haystack:
        code = "RUNTIME.FINALIZATION_FAILURE"
    elif "context" in haystack:
        code = "RUNTIME.RUNTIME_FAILURE"
    elif (
        category in ("TIMEOUT", "NETWORK")
        or "timeout" in haystack
        or "timed out" in haystack
        or "network" in haystack
    ):
        code = "RUNTIME.TIMEOUT"
    elif category == "MALFORMED_RESPONSE":
        code = "RUNTIME.MALFORMED_PROVIDER_RESPONSE"
    else:
        code = "RUNTIME.PROVIDER_ERROR"

    return dict(TAXONOMY[code])


def classify_finding(
    category: str,
    message: str = "",
    signals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Map an evaluation finding to a canonical taxonomy entry.

    The returned dict is the taxonomy entry plus a ``related_codes`` list. An
    explicit ``signals["taxonomy_code"]`` (validated) wins over every heuristic;
    an explicit ``signals["domain"]`` constrains the result to that domain.
    """
    signals = signals or {}
    override = normalize_code(signals.get("taxonomy_code")) if signals.get("taxonomy_code") else None

    requested_domain: Optional[str] = None
    raw_domain = signals.get("domain")
    if raw_domain:
        candidate = str(raw_domain).strip().upper()
        if candidate in DOMAINS:
            requested_domain = candidate

    if override is not None:
        code = override
    else:
        code = _resolve_finding_code(category, message)
        if requested_domain and TAXONOMY[code]["domain"] != requested_domain:
            refined = _refine_from_message(message)
            if refined and TAXONOMY[refined]["domain"] == requested_domain:
                code = refined
            else:
                code = _DOMAIN_FALLBACK[requested_domain]

    entry = dict(TAXONOMY[code])
    entry["related_codes"] = _related_codes(code, category, message)
    return entry


def classify_security(signal: str) -> Dict[str, Any]:
    """Classify a security signal (thin wrapper over :func:`classify_finding`)."""
    return classify_finding("security", message=signal)


__all__ = [
    "DOMAINS",
    "SEVERITY_SCALES",
    "OPERATIONAL_SEVERITIES",
    "QUALITY_SEVERITIES",
    "RESEARCH_SEVERITIES",
    "SECURITY_SEVERITIES",
    "SEVERITIES_BY_SCALE",
    "EXECUTION_ERROR_CATEGORIES",
    "TAXONOMY",
    "TAXONOMY_CODES",
    "normalize_code",
    "describe",
    "domain_of",
    "severity_scale_for",
    "classify_execution",
    "classify_finding",
    "classify_security",
]
