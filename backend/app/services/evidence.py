"""Deterministic run-evidence collection for the V5 evaluator contract.

Read-only and best-effort: every repository access degrades to an empty list or
``None`` instead of raising. Nothing here mutates state or invents values -- a
metric that cannot be observed stays ``None`` and is never coerced to ``0``.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from .. import repository as repo

_MARKER_NUMBER_PATTERN = re.compile(r"\[K(\d+)\]")
_MARKER_LABEL_PATTERN = re.compile(r"\[K\d+\]")

KNOWLEDGE_SPAN_TYPES = (
    "KNOWLEDGE_QUERY",
    "KNOWLEDGE_RETRIEVAL",
    "KNOWLEDGE_CONTEXT_INJECTION",
)

_RETRIEVAL_SPAN = "KNOWLEDGE_RETRIEVAL"
_QUERY_SPAN = "KNOWLEDGE_QUERY"
_INJECTION_SPAN = "KNOWLEDGE_CONTEXT_INJECTION"


# ------------------------------------------------------------------ scalar coercion
def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _truncate(value: Any, limit: int = 300) -> Optional[str]:
    if value is None:
        return None
    return str(value)[:limit]


# ------------------------------------------------------------------ markers
def parse_knowledge_markers(text: Optional[str]) -> List[int]:
    """Return ``[K1], [K2]`` -> ``[1, 2]`` in order of appearance.

    Repeats are preserved (``[K1] [K1]`` -> ``[1, 1]``).
    """
    if not text:
        return []
    return [int(match) for match in _MARKER_NUMBER_PATTERN.findall(text)]


def extract_marker_labels(text: Optional[str]) -> List[str]:
    """Return the literal ``"[K1]"`` labels, deduplicated, order preserved."""
    if not text:
        return []
    labels: List[str] = []
    seen = set()
    for match in _MARKER_LABEL_PATTERN.findall(text):
        if match not in seen:
            seen.add(match)
            labels.append(match)
    return labels


def marker_validity(markers: List[int], provenance: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``[K#]`` markers against ``provenance.chunk_ids``.

    A marker ``n`` is valid iff ``1 <= n <= len(chunk_ids)``. Duplicates are
    valid but reported in ``duplicates``; ``valid_count`` counts valid
    occurrences, while ``valid``/``invalid`` list distinct values.
    """
    provenance = provenance if isinstance(provenance, dict) else {}
    chunk_ids = provenance.get("chunk_ids")
    provenance_available = isinstance(chunk_ids, list)
    available = len(chunk_ids) if provenance_available else 0

    markers = list(markers or [])
    counts: Dict[int, int] = {}
    for marker in markers:
        counts[marker] = counts.get(marker, 0) + 1

    valid_occurrences = [m for m in markers if 1 <= m <= available]
    valid = sorted({m for m in markers if 1 <= m <= available})
    invalid = sorted({m for m in markers if not (1 <= m <= available)})
    duplicates = sorted(m for m, count in counts.items() if count > 1)

    return {
        "total": len(markers),
        "unique": len(counts),
        "valid_count": len(valid_occurrences),
        "invalid": invalid,
        "valid": valid,
        "duplicates": duplicates,
        "has_invalid": bool(invalid),
        "has_valid": bool(valid),
        "provenance_available": provenance_available,
    }


# ------------------------------------------------------------------ evidence
def _tool_call_view(row: Dict[str, Any]) -> Dict[str, Any]:
    error = row.get("error") if isinstance(row.get("error"), dict) else {}
    return {
        "id": row.get("id"),
        "tool_name": row.get("tool_name"),
        "execution_status": row.get("execution_status"),
        "error_code": error.get("code"),
        "error_message": _truncate(error.get("message")),
        "duration_ms": _as_float(row.get("duration_ms")),
    }


async def _read_tool_calls(
    owner_id: str, run_id: Optional[str]
) -> "tuple[List[Dict[str, Any]], bool]":
    if not run_id:
        return [], False
    try:
        rows = await repo.tool_calls.list(
            {"owner_id": owner_id, "run_id": run_id}, limit=500
        )
        return list(rows or []), True
    except Exception:  # pragma: no cover - best-effort read
        return [], False


async def _read_spans(owner_id: str, trace_id: Optional[str]) -> List[Dict[str, Any]]:
    if not trace_id:
        return []
    try:
        rows = await repo.trace_spans.list(
            {"owner_id": owner_id, "trace_id": trace_id}, limit=500
        )
        return list(rows or [])
    except Exception:  # pragma: no cover - best-effort read
        return []


async def _snapshot_has_retrieved_context(
    owner_id: str, run: Dict[str, Any]
) -> bool:
    snapshot_id = run.get("context_snapshot_id")
    run_id = run.get("id")
    snapshot = None
    try:
        if snapshot_id:
            snapshot = await repo.context_snapshots.get(snapshot_id, owner_id)
        elif run_id:
            snapshot = await repo.context_snapshots.find_one(
                {"owner_id": owner_id, "run_id": run_id}
            )
    except Exception:  # pragma: no cover - best-effort read
        snapshot = None
    if not isinstance(snapshot, dict):
        return False
    for component in snapshot.get("components") or []:
        if not isinstance(component, dict):
            continue
        if (
            component.get("component_type") == "RETRIEVED_CONTEXT"
            and component.get("included", True)
        ):
            return True
    return False


def _span_retrieval_count(spans: List[Dict[str, Any]]) -> Optional[int]:
    values: List[int] = []
    for span in spans:
        if span.get("span_type") != _RETRIEVAL_SPAN:
            continue
        metadata = span.get("metadata") if isinstance(span.get("metadata"), dict) else {}
        for key in ("returned_count", "chunk_count"):
            value = _as_int(metadata.get(key))
            if value is not None:
                values.append(value)
                break
    if not values:
        return None
    return max(values)


async def _load_compiled_prompt(
    owner_id: str, run: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Best-effort projection of the run's compiled prompt (additive V6.7).

    Returns ``None`` when the run has no compiled prompt or the owner-scoped
    document is unavailable. All keys are read straight from the stored doc;
    unknown fields stay ``None`` and are never fabricated.
    """
    compiled_prompt_id = run.get("compiled_prompt_id")
    if not compiled_prompt_id:
        return None
    try:
        doc = await repo.compiled_prompts.get(compiled_prompt_id, owner_id)
    except Exception:  # pragma: no cover - best-effort read
        doc = None
    if not isinstance(doc, dict):
        return None
    return {
        "id": doc.get("id"),
        "target": doc.get("target"),
        "checksum": doc.get("checksum"),
        "invariants_used": list(doc.get("invariants_used") or []),
        "policies_used": list(doc.get("policies_used") or []),
        "blocks_used": list(doc.get("blocks_used") or []),
        "token_estimate": doc.get("token_estimate"),
    }


async def _load_context_snapshot(
    owner_id: str, run: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Best-effort projection of the run's context snapshot (additive V6.7)."""
    snapshot_id = run.get("context_snapshot_id")
    if not snapshot_id:
        return None
    try:
        snapshot = await repo.context_snapshots.get(snapshot_id, owner_id)
    except Exception:  # pragma: no cover - best-effort read
        snapshot = None
    if not isinstance(snapshot, dict):
        return None

    excluded_components: List[Dict[str, Any]] = []
    seen_excluded = set()
    for component in list(snapshot.get("excluded_components") or []):
        if not isinstance(component, dict):
            continue
        component_type = component.get("component_type")
        key = (component_type, component.get("excluded_reason"))
        if key in seen_excluded:
            continue
        seen_excluded.add(key)
        excluded_components.append({
            "component_type": component_type,
            "excluded_reason": component.get("excluded_reason"),
        })
    included_component_types: List[str] = []
    for component in list(snapshot.get("components") or []):
        if not isinstance(component, dict):
            continue
        component_type = component.get("component_type")
        if not component_type:
            continue
        if component.get("included", True):
            if component_type not in included_component_types:
                included_component_types.append(component_type)
        else:
            key = (component_type, component.get("excluded_reason"))
            if key not in seen_excluded:
                seen_excluded.add(key)
                excluded_components.append({
                    "component_type": component_type,
                    "excluded_reason": component.get("excluded_reason"),
                })

    return {
        "id": snapshot.get("id"),
        "over_budget": snapshot.get("over_budget"),
        "strategy": snapshot.get("strategy"),
        "excluded_components": excluded_components,
        "included_component_types": included_component_types,
        "budget_policy_applied": snapshot.get("budget_policy_applied"),
        "estimated_total_tokens": snapshot.get("estimated_total_tokens"),
    }


async def _load_enabled_tool_names(
    owner_id: str, run: Dict[str, Any]
) -> Optional[List[str]]:
    """Resolve allowed tool ids to names, best-effort (additive V6.7).

    Returns ``None`` when there is nothing to resolve (no agent tool policy),
    an empty list for an explicit empty allowlist, and only the names that were
    actually resolved for the supplied ids.
    """
    policy = run.get("tool_policy_applied")
    policy = policy if isinstance(policy, dict) else {}
    raw_ids = policy.get("enabled_tool_ids")
    if raw_ids is None:
        raw_ids = run.get("enabled_tool_ids")
    if raw_ids is None:
        return None
    names: List[str] = []
    for tool_id in list(raw_ids):
        if not tool_id:
            continue
        try:
            tool = await repo.tools.get(tool_id, owner_id)
        except Exception:  # pragma: no cover - best-effort read
            tool = None
        name = (tool or {}).get("name") if isinstance(tool, dict) else None
        if name and name not in names:
            names.append(name)
    return names


async def collect_run_evidence(owner_id: str, run: Dict[str, Any]) -> Dict[str, Any]:
    """Collect the deterministic evidence tuple for a single run.

    Returns exactly the documented keys. Unknown values stay ``None``; missing
    data is never coerced to ``0``.
    """
    run = run if isinstance(run, dict) else {}
    run_id = run.get("id")
    trace_id = run.get("trace_id")

    candidate = run.get("normalized_response")
    if candidate is None:
        candidate = run.get("raw_response")
    if candidate is None:
        candidate = None

    raw_tool_calls, tool_read_ok = await _read_tool_calls(owner_id, run_id)
    tool_calls = [_tool_call_view(row) for row in raw_tool_calls]
    tool_names = sorted(
        {row.get("tool_name") for row in raw_tool_calls if row.get("tool_name")}
    )

    run_call_count = _as_int(run.get("tool_call_count"))
    if run_call_count is not None:
        tool_call_count: Optional[int] = run_call_count
        tool_count_source: Optional[str] = "run"
    elif trace_id and tool_read_ok:
        tool_call_count = len(raw_tool_calls)
        tool_count_source = "tool_calls"
    else:
        tool_call_count = None
        tool_count_source = None

    derived_failed = sum(
        1 for row in raw_tool_calls if row.get("execution_status") == "failed"
    )
    derived_success = sum(
        1 for row in raw_tool_calls if row.get("execution_status") == "success"
    )

    tool_failure_count = _as_int(run.get("tool_failure_count"))
    if tool_failure_count is None and trace_id and tool_read_ok:
        tool_failure_count = derived_failed

    tool_success_count = _as_int(run.get("tool_success_count"))
    if tool_success_count is None and trace_id and tool_read_ok:
        tool_success_count = derived_success

    knowledge_enabled = run.get("knowledge_enabled")
    if not isinstance(knowledge_enabled, bool):
        knowledge_enabled = None

    provenance = run.get("knowledge_provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    knowledge_provenance = dict(provenance)

    chunk_ids = provenance.get("chunk_ids")
    chunk_ids_present = isinstance(chunk_ids, list)
    knowledge_chunk_ids = list(chunk_ids) if chunk_ids_present else []

    if provenance.get("chunk_count") is not None:
        knowledge_chunk_count: Optional[int] = _as_int(provenance.get("chunk_count"))
    elif chunk_ids_present:
        knowledge_chunk_count = len(knowledge_chunk_ids)
    else:
        knowledge_chunk_count = None

    if provenance.get("chunk_count") is not None:
        knowledge_retrieval_count: Optional[int] = _as_int(provenance.get("chunk_count"))
    elif chunk_ids_present:
        knowledge_retrieval_count = len(knowledge_chunk_ids)
    else:
        knowledge_retrieval_count = None

    spans = await _read_spans(owner_id, trace_id)
    span_counts = {"query": 0, "retrieval": 0, "injection": 0}
    for span in spans:
        span_type = span.get("span_type")
        if span_type == _QUERY_SPAN:
            span_counts["query"] += 1
        elif span_type == _RETRIEVAL_SPAN:
            span_counts["retrieval"] += 1
        elif span_type == _INJECTION_SPAN:
            span_counts["injection"] += 1

    if knowledge_retrieval_count is None:
        knowledge_retrieval_count = _span_retrieval_count(spans)

    snapshot_injected = await _snapshot_has_retrieved_context(owner_id, run)
    injection_present = snapshot_injected or span_counts["injection"] > 0
    knowledge_attempted = (
        knowledge_enabled is True
        or any(span_counts.values())
        or bool(provenance)
    )
    if injection_present:
        knowledge_injection: Optional[bool] = True
    elif knowledge_attempted:
        knowledge_injection = False
    else:
        knowledge_injection = None

    markers = parse_knowledge_markers(candidate)

    input_tokens = _as_int(run.get("input_tokens"))
    output_tokens = _as_int(run.get("output_tokens"))
    total_tokens = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )

    # --- additive V6.7 agent provenance / context projections ---------------
    compiled_prompt = await _load_compiled_prompt(owner_id, run)
    context_snapshot = await _load_context_snapshot(owner_id, run)
    enabled_tool_names = await _load_enabled_tool_names(owner_id, run)

    return {
        "run_id": run_id,
        "experiment_id": run.get("experiment_id"),
        "project_id": run.get("project_id"),
        "dataset_case_id": run.get("dataset_case_id"),
        "dataset_version_id": run.get("dataset_version_id"),
        "trace_id": trace_id,
        "context_snapshot_id": run.get("context_snapshot_id"),
        "status": run.get("status"),
        "candidate": candidate,
        "candidate_available": candidate is not None,
        "tool_calls": tool_calls,
        "tool_names": tool_names,
        "tool_call_count": tool_call_count,
        "tool_failure_count": tool_failure_count,
        "tool_success_count": tool_success_count,
        "tool_count_source": tool_count_source,
        "knowledge_enabled": knowledge_enabled,
        "knowledge_provenance": knowledge_provenance,
        "knowledge_chunk_ids": knowledge_chunk_ids,
        "knowledge_chunk_count": knowledge_chunk_count,
        "knowledge_marker_labels": extract_marker_labels(candidate),
        "knowledge_markers": markers,
        "knowledge_retrieval_count": knowledge_retrieval_count,
        "knowledge_injection": knowledge_injection,
        "knowledge_span_counts": span_counts,
        "evidence_item_count": _as_int(run.get("evidence_item_count")),
        "source_count": _as_int(run.get("source_count")),
        "latency_ms": _as_float(run.get("latency_ms")),
        "ttft_ms": _as_float(run.get("ttft_ms")),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": _as_float(run.get("estimated_cost")),
        "cost_available": bool(run.get("cost_available")),
        # Additive (V6.7 Agent & Prompt Evaluation): agent/runtime provenance.
        # Every value is read from the run; absent telemetry stays None and is
        # never coerced to a default. Existing keys above are unchanged.
        "agent_id": run.get("agent_id"),
        "agent_version_id": run.get("agent_version_id"),
        "agent_version_snapshot": run.get("agent_version_snapshot"),
        "compiled_prompt_id": run.get("compiled_prompt_id"),
        "runtime_policy_applied": run.get("runtime_policy_applied"),
        "tool_policy_applied": run.get("tool_policy_applied"),
        "knowledge_policy_applied": run.get("knowledge_policy_applied"),
        "permission_denials": list(run.get("permission_denials") or []),
        "output_contract": run.get("output_contract"),
        "enabled_tool_ids": run.get("enabled_tool_ids"),
        "enabled_tool_names": enabled_tool_names,
        "compiled_prompt": compiled_prompt,
        "context_snapshot": context_snapshot,
    }
