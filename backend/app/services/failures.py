"""Failure Lab: execution + quality failure taxonomy and audit-safe records.

Failures are append-only in spirit: every mutation through
``reclassify_failure`` appends a ``history`` event rather than rewriting prior
state, so the audit trail is always reconstructable. Execution failures are
captured automatically from Run documents (idempotently), while quality
failures are raised by humans, judges or rules.
"""
from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..models import new_id, now_iso
from . import failure_taxonomy as taxonomy
from . import redaction

try:
    from ..models import (
        FAILURE_SOURCES as _FAILURE_SOURCES,
        FAILURE_STATUSES as _FAILURE_STATUSES,
        QUALITY_FAILURE_CATEGORIES as _QUALITY_CATEGORIES,
    )
except Exception:  # pragma: no cover - models.py may land after this module
    _QUALITY_CATEGORIES = (
        "WRONG_ANSWER",
        "HALLUCINATION",
        "INSTRUCTION_VIOLATION",
        "INCOMPLETE",
        "CONTEXT_MISINTERPRETATION",
        "FORMAT_VIOLATION",
        "SECURITY_FAILURE",
        "TOOL_SELECTION_ERROR",
        "TOOL_EXECUTION_ERROR",
        "EXCESSIVE_COST",
        "EXCESSIVE_LATENCY",
        "OTHER",
    )
    _FAILURE_SOURCES = ("SYSTEM", "HUMAN", "JUDGE", "RULE")
    _FAILURE_STATUSES = ("OPEN", "CONFIRMED", "DISMISSED", "RESOLVED")

try:
    from ..errors import ERROR_CATEGORIES as _ERROR_CATEGORIES
except Exception:  # pragma: no cover - defensive
    _ERROR_CATEGORIES = (
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


QUALITY_CATEGORIES = tuple(_QUALITY_CATEGORIES)
FAILURE_SOURCES = tuple(_FAILURE_SOURCES)
FAILURE_STATUSES = tuple(_FAILURE_STATUSES)
EXECUTION_CATEGORIES = tuple(_ERROR_CATEGORIES)
SEVERITIES = ("low", "medium", "high", "critical")

_SEVERITY_BY_CATEGORY = {
    "RATE_LIMIT": "medium",
    "TIMEOUT": "medium",
    "NETWORK": "medium",
    "PROVIDER": "medium",
    "AUTH": "high",
    "VALIDATION": "high",
    "MODEL_UNAVAILABLE": "high",
    "INTERNAL": "medium",
    "MALFORMED_RESPONSE": "medium",
    "CANCELLED": "medium",
}

_MAX_DESCRIPTION_CHARS = 1000
_AGGREGATE_SCAN_LIMIT = 5000


class FailureError(ValueError):
    """Raised for failure-record contract violations. Carries an HTTP code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _collection(name: str):
    collection = getattr(repo, name, None)
    if collection is not None:
        return collection
    try:  # pragma: no cover - only hit before repository lands the attribute
        return repo.Repository(name)  # type: ignore[attr-defined]
    except Exception:
        return None


def _truncate(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def classify_failure_class(category: str, source: str) -> str:
    """Execution failures are SYSTEM-sourced provider errors; else quality."""
    if source == "SYSTEM" and category in EXECUTION_CATEGORIES:
        return "execution"
    return "quality"


def _severity_for(category: str) -> str:
    return _SEVERITY_BY_CATEGORY.get(category, "medium")


_QUALITY_CATEGORY_TO_FINDING = {
    "WRONG_ANSWER": "correctness",
    "HALLUCINATION": "correctness",
    "INSTRUCTION_VIOLATION": "instruction_following",
    "INCOMPLETE": "completeness",
    "CONTEXT_MISINTERPRETATION": "correctness",
    "FORMAT_VIOLATION": "correctness",
    "SECURITY_FAILURE": "security",
    "TOOL_SELECTION_ERROR": "tool_selection",
    "TOOL_EXECUTION_ERROR": "tool_failure",
    "EXCESSIVE_COST": "efficiency",
    "EXCESSIVE_LATENCY": "runtime",
    "OTHER": "correctness",
}


def _classify(
    *,
    category: str,
    source: str,
    subcategory: str = "",
    message: str = "",
    signals: Optional[dict] = None,
    taxonomy_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve the canonical taxonomy entry for a failure record."""
    if taxonomy_code and taxonomy.describe(taxonomy_code):
        return taxonomy.describe(taxonomy_code)
    if source == "SYSTEM" and category in EXECUTION_CATEGORIES:
        return taxonomy.classify_execution(
            category, error_code=subcategory, message=message
        )
    finding_category = _QUALITY_CATEGORY_TO_FINDING.get(category, "correctness")
    return taxonomy.classify_finding(finding_category, message=message, signals=signals)


def _enrichment_from_run(run: Optional[dict]) -> Dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    return {
        "run_model_id": run.get("model_id"),
        "run_model_name": run.get("model_name"),
        "model_id": run.get("model_id"),
        "experiment_id": run.get("experiment_id"),
    }


async def _lookup_run(owner_id: str, run_id: Optional[str]) -> Optional[dict]:
    if not run_id:
        return None
    collection = _collection("runs")
    if collection is None:
        return None
    try:
        return await collection.get(run_id, owner_id)
    except Exception:  # pragma: no cover - defensive
        return None


async def record_execution_failure(run: dict) -> Optional[dict]:
    """Capture a structured execution failure from an error Run, idempotently.

    Failure semantics (Tooling UX closure): the canonical failure funnel is
    Run -> FailureRecord. Tool-level outcomes inside an agentic run
    (``ToolCall.error``, including code-interpreter ``timeout`` /
    ``CODE_RUNTIME_ERROR`` persisted canonically on ``CodeExecution.error``)
    do NOT emit their own FailureRecords; they correlate via
    ``Run -> ToolCall -> CodeExecution -> FailureRecord -> Trace`` through
    the shared ``run_id`` / ``tool_call_id`` / ``provider_tool_call_id`` /
    ``trace_id`` keys. Only the terminal error Run produces one SYSTEM
    FailureRecord here, keyed idempotently on
    ``(owner_id, run_id, source=SYSTEM)``, so retries never duplicate it.
    Standalone tool tests (``POST /tools/{id}/test``) have no Run and
    therefore emit no FailureRecord — their ``ToolCall.error`` is canonical.
    """
    if not isinstance(run, dict):
        return None
    if run.get("status") == "success":
        return None
    error = run.get("error")
    if not isinstance(error, dict) or not error:
        return None

    owner_id = run.get("owner_id")
    run_id = run.get("id")
    if not owner_id or not run_id:
        return None

    collection = _collection("failures")
    if collection is None:
        return None

    try:
        existing = await collection.find_one(
            {"owner_id": owner_id, "run_id": run_id, "source": "SYSTEM"}
        )
    except Exception:  # pragma: no cover - defensive
        existing = None
    if existing is not None:
        return existing

    category = error.get("category")
    if category not in EXECUTION_CATEGORIES:
        category = "INTERNAL"
    subcategory = error.get("code") or ""
    severity = _severity_for(category)
    error_message = error.get("message") or f"Execution failed with category {category}"
    description = _truncate(
        redaction.redact_text(str(error_message)),
        _MAX_DESCRIPTION_CHARS,
    )
    classification = _classify(
        category=category,
        source="SYSTEM",
        subcategory=subcategory,
        message=str(error_message),
    )
    now = now_iso()
    enrichment = _enrichment_from_run(run)
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": run.get("project_id"),
        "run_id": run_id,
        "trace_id": run.get("trace_id"),
        "evaluation_id": None,
        "dataset_case_id": run.get("dataset_case_id"),
        "span_id": None,
        "finding_id": None,
        "category": category,
        "subcategory": subcategory,
        "source": "SYSTEM",
        "severity": severity,
        "title": f"Execution failed: {category}",
        "description": description,
        "evidence": redaction.redact({
            "error": error,
            "finish_reason": run.get("finish_reason"),
            "latency_ms": run.get("latency_ms"),
        }),
        "confidence": None,
        "tags": [],
        "failure_class": "execution",
        "failure_domain": classification.get("domain"),
        "taxonomy_code": classification.get("code"),
        "severity_scale": classification.get("severity_scale"),
        "taxonomy": classification,
        "status": "OPEN",
        "resolved_at": None,
        "tool_id": run.get("tool_id"),
        "run_model_id": enrichment.get("run_model_id"),
        "run_model_name": enrichment.get("run_model_name"),
        "model_id": enrichment.get("model_id"),
        "experiment_id": enrichment.get("experiment_id"),
        "history": [{
            "at": now,
            "actor": "system",
            "action": "created",
            "category": category,
            "subcategory": subcategory,
            "source": "SYSTEM",
            "status": "OPEN",
            "note": "",
            "failure_domain": classification.get("domain"),
            "taxonomy_code": classification.get("code"),
            "severity_scale": classification.get("severity_scale"),
            "taxonomy": classification,
        }],
        "created_at": now,
        "updated_at": now,
    }
    try:
        await collection.insert(doc)
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass
    return doc


async def create_failure(
    owner_id: str,
    project_id: Optional[str],
    *,
    category: str,
    source: str = "HUMAN",
    subcategory: str = "",
    severity: str = "medium",
    title: str,
    description: str = "",
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    evaluation_id: Optional[str] = None,
    evidence: Optional[dict] = None,
    confidence: Optional[float] = None,
    tags: Optional[List[str]] = None,
    dataset_case_id: Optional[str] = None,
    span_id: Optional[str] = None,
    finding_id: Optional[str] = None,
    taxonomy_code: Optional[str] = None,
    signals: Optional[dict] = None,
) -> dict:
    """Create a failure record (quality by default) with an audit history."""
    if not isinstance(category, str) or not category.strip():
        raise FailureError("category is required", 400)
    if source not in FAILURE_SOURCES:
        raise FailureError(f"Unsupported failure source: {source}", 400)
    if severity not in SEVERITIES:
        severity = "medium"

    run = await _lookup_run(owner_id, run_id)
    enrichment = _enrichment_from_run(run)
    resolved_project_id = project_id
    resolved_trace_id = trace_id
    if run is not None:
        if resolved_project_id is None:
            resolved_project_id = run.get("project_id")
        if resolved_trace_id is None:
            resolved_trace_id = run.get("trace_id")
    if dataset_case_id is None:
        dataset_case_id = (run or {}).get("dataset_case_id")

    classification = _classify(
        category=category,
        source=source,
        subcategory=subcategory or "",
        message=f"{title or ''}\n{description or ''}".strip(),
        signals=signals,
        taxonomy_code=taxonomy_code,
    )
    redacted_title = redaction.redact_text(str(title))
    redacted_description = redaction.redact_text(str(description))
    redacted_evidence = redaction.redact(dict(evidence or {}))

    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": resolved_project_id,
        "run_id": run_id,
        "trace_id": resolved_trace_id,
        "evaluation_id": evaluation_id,
        "dataset_case_id": dataset_case_id,
        "span_id": span_id,
        "finding_id": finding_id,
        "category": category,
        "subcategory": subcategory or "",
        "source": source,
        "severity": severity,
        "title": _truncate(redacted_title, 300),
        "description": _truncate(redacted_description, _MAX_DESCRIPTION_CHARS),
        "evidence": redacted_evidence,
        "confidence": confidence,
        "tags": list(tags or []),
        "failure_class": classify_failure_class(category, source),
        "failure_domain": classification.get("domain"),
        "taxonomy_code": classification.get("code"),
        "severity_scale": classification.get("severity_scale"),
        "taxonomy": classification,
        "status": "OPEN",
        "resolved_at": None,
        "tool_id": (run or {}).get("tool_id"),
        "run_model_id": enrichment.get("run_model_id"),
        "run_model_name": enrichment.get("run_model_name"),
        "model_id": enrichment.get("model_id"),
        "experiment_id": enrichment.get("experiment_id"),
        "history": [{
            "at": now,
            "actor": "human",
            "action": "created",
            "category": category,
            "subcategory": subcategory or "",
            "source": source,
            "status": "OPEN",
            "note": "",
            "failure_domain": classification.get("domain"),
            "taxonomy_code": classification.get("code"),
            "severity_scale": classification.get("severity_scale"),
            "taxonomy": classification,
        }],
        "created_at": now,
        "updated_at": now,
    }
    collection = _collection("failures")
    if collection is None:
        raise FailureError("Failure store unavailable", 503)
    await collection.insert(doc)
    return doc


async def get_failure(owner_id: str, failure_id: str) -> dict:
    """Fetch a failure by id or raise FailureError 404."""
    collection = _collection("failures")
    if collection is None:
        raise FailureError("Failure store unavailable", 503)
    doc = await collection.get(failure_id, owner_id)
    if doc is None:
        raise FailureError(f"Failure '{failure_id}' not found", 404)
    return doc


async def reclassify_failure(
    owner_id: str,
    failure_id: str,
    *,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    source: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    note: str = "",
    actor: str = "human",
) -> dict:
    """Apply audited updates; history is appended, never rewritten."""
    if source is not None and source not in FAILURE_SOURCES:
        raise FailureError(f"Unsupported failure source: {source}", 400)
    if status is not None and status not in FAILURE_STATUSES:
        raise FailureError(f"Unsupported failure status: {status}", 400)
    if severity is not None and severity not in SEVERITIES:
        raise FailureError(f"Unsupported severity: {severity}", 400)

    doc = await get_failure(owner_id, failure_id)

    updates: Dict[str, Any] = {}
    previous: Dict[str, Any] = {}
    content_changed = False

    def _apply(field: str, value: Any, counts_as_content: bool = True) -> None:
        nonlocal content_changed
        if value is None:
            return
        previous[field] = doc.get(field)
        updates[field] = value
        if counts_as_content:
            content_changed = True

    _apply("category", category)
    _apply("subcategory", subcategory)
    _apply("source", source)
    _apply("severity", severity)
    _apply("title", title)
    _apply("description", description)
    # status participates but is tracked separately for the action label.
    if status is not None:
        previous["status"] = doc.get("status")
        updates["status"] = status
    if category is not None or source is not None:
        updated_category = updates.get("category", doc.get("category"))
        updated_source = updates.get("source", doc.get("source"))
        updates["failure_class"] = classify_failure_class(updated_category, updated_source)

    classification = None
    if any(
        field in updates
        for field in ("category", "subcategory", "source", "title", "description")
    ):
        classification = _classify(
            category=updates.get("category", doc.get("category")) or "",
            source=updates.get("source", doc.get("source")) or "",
            subcategory=updates.get("subcategory", doc.get("subcategory")) or "",
            message=(
                f"{updates.get('title', doc.get('title')) or ''}\n"
                f"{updates.get('description', doc.get('description')) or ''}"
            ).strip(),
        )
        updates["failure_domain"] = classification.get("domain")
        updates["taxonomy_code"] = classification.get("code")
        updates["severity_scale"] = classification.get("severity_scale")
        updates["taxonomy"] = classification

    action = "status_changed" if (status is not None and not content_changed) else "reclassified"
    now = now_iso()
    if status == "RESOLVED":
        updates["resolved_at"] = now

    history = list(doc.get("history") or [])
    history_entry = {
        "at": now,
        "actor": actor,
        "action": action,
        "previous": previous,
        "category": updates.get("category", doc.get("category")),
        "subcategory": updates.get("subcategory", doc.get("subcategory")),
        "source": updates.get("source", doc.get("source")),
        "severity": updates.get("severity", doc.get("severity")),
        "status": updates.get("status", doc.get("status")),
        "note": note or "",
    }
    if classification is not None:
        history_entry["failure_domain"] = classification.get("domain")
        history_entry["taxonomy_code"] = classification.get("code")
        history_entry["severity_scale"] = classification.get("severity_scale")
        history_entry["taxonomy"] = classification
    history.append(history_entry)
    updates["history"] = history
    updates["updated_at"] = now

    collection = _collection("failures")
    if collection is None:
        raise FailureError("Failure store unavailable", 503)
    updated = await collection.update(failure_id, owner_id, updates)
    return updated if updated is not None else {**doc, **updates}


def _build_query(
    owner_id: str,
    *,
    project_id: Optional[str],
    category: Optional[str],
    source: Optional[str],
    status: Optional[str],
    failure_class: Optional[str],
    failure_domain: Optional[str],
    model_id: Optional[str],
    experiment_id: Optional[str],
    run_id: Optional[str],
    trace_id: Optional[str],
    tool_id: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> Dict[str, Any]:
    query: Dict[str, Any] = {"owner_id": owner_id}
    if project_id:
        query["project_id"] = project_id
    if category:
        query["category"] = category
    if source:
        query["source"] = source
    if status:
        query["status"] = status
    if failure_class:
        query["failure_class"] = failure_class
    if failure_domain:
        query["failure_domain"] = failure_domain
    if model_id:
        query["$or"] = [{"model_id": model_id}, {"run_model_id": model_id}]
    if experiment_id:
        query["experiment_id"] = experiment_id
    if run_id:
        query["run_id"] = run_id
    if trace_id:
        query["trace_id"] = trace_id
    if tool_id:
        query["tool_id"] = tool_id
    if date_from or date_to:
        created = {}
        if date_from:
            created["$gte"] = date_from
        if date_to:
            created["$lte"] = date_to
        query["created_at"] = created
    return query


async def list_failures(
    owner_id: str,
    *,
    project_id: Optional[str] = None,
    category: Optional[str] = None,
    source: Optional[str] = None,
    status: Optional[str] = None,
    failure_class: Optional[str] = None,
    failure_domain: Optional[str] = None,
    model_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    tool_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List failures with filters plus a total count."""
    collection = _collection("failures")
    if collection is None:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}
    query = _build_query(
        owner_id,
        project_id=project_id,
        category=category,
        source=source,
        status=status,
        failure_class=failure_class,
        failure_domain=failure_domain,
        model_id=model_id,
        experiment_id=experiment_id,
        run_id=run_id,
        trace_id=trace_id,
        tool_id=tool_id,
        date_from=date_from,
        date_to=date_to,
    )
    try:
        items = await collection.list(query, limit=limit, offset=offset)
    except Exception:  # pragma: no cover - defensive
        items = []
    try:
        total = await collection.count(query)
    except Exception:  # pragma: no cover - defensive
        total = len(items)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _sorted_counts(counter: Dict[Any, int], key_name: str) -> List[dict]:
    return [
        {key_name: key, "count": count}
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    ]


async def aggregate_failures(
    owner_id: str,
    *,
    project_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """Return simple aggregate counts over a bounded scan of failures."""
    collection = _collection("failures")
    if collection is None:
        return {
            "total": 0,
            "execution_count": 0,
            "quality_count": 0,
            "by_category": [],
            "by_source": [],
            "by_status": [],
            "by_severity": [],
            "runtime_count": 0,
            "research_count": 0,
            "security_count": 0,
            "by_domain": [],
            "by_model": [],
            "by_experiment": [],
            "by_tool": [],
            "recent": [],
        }
    query = _build_query(
        owner_id,
        project_id=project_id,
        category=None,
        source=None,
        status=None,
        failure_class=None,
        failure_domain=None,
        model_id=None,
        experiment_id=None,
        run_id=None,
        trace_id=None,
        tool_id=None,
        date_from=date_from,
        date_to=date_to,
    )
    try:
        items = await collection.list(query, limit=_AGGREGATE_SCAN_LIMIT)
    except Exception:  # pragma: no cover - defensive
        items = []

    by_category: Dict[Any, int] = {}
    by_source: Dict[Any, int] = {}
    by_status: Dict[Any, int] = {}
    by_severity: Dict[Any, int] = {}
    by_model: Dict[Any, int] = {}
    by_experiment: Dict[Any, int] = {}
    by_tool: Dict[Any, int] = {}
    by_domain: Dict[Any, int] = {}
    execution_count = 0
    quality_count = 0
    runtime_count = 0
    research_count = 0
    security_count = 0

    for item in items:
        failure_class = item.get("failure_class")
        if failure_class == "execution":
            execution_count += 1
        elif failure_class == "quality":
            quality_count += 1
        domain = item.get("failure_domain")
        if domain:
            by_domain[domain] = by_domain.get(domain, 0) + 1
            if domain == "RUNTIME":
                runtime_count += 1
            elif domain == "RESEARCH":
                research_count += 1
            elif domain == "SECURITY":
                security_count += 1
        for counter, key in (
            (by_category, item.get("category") or "OTHER"),
            (by_source, item.get("source") or "HUMAN"),
            (by_status, item.get("status") or "OPEN"),
            (by_severity, item.get("severity") or "medium"),
            (by_model, item.get("run_model_name") or "unknown"),
            (by_experiment, item.get("experiment_id") or "none"),
            (by_tool, item.get("tool_id") or "none"),
        ):
            counter[key] = counter.get(key, 0) + 1

    return {
        "total": len(items),
        "execution_count": execution_count,
        "quality_count": quality_count,
        "runtime_count": runtime_count,
        "research_count": research_count,
        "security_count": security_count,
        "by_domain": _sorted_counts(by_domain, "domain"),
        "by_category": _sorted_counts(by_category, "category"),
        "by_source": _sorted_counts(by_source, "source"),
        "by_status": _sorted_counts(by_status, "status"),
        "by_severity": _sorted_counts(by_severity, "severity"),
        "by_model": _sorted_counts(by_model, "model"),
        "by_experiment": _sorted_counts(by_experiment, "experiment_id"),
        "by_tool": _sorted_counts(by_tool, "tool_id"),
        "recent": items[:10],
    }
