"""V6.9 Drift & Failure Analysis.

Drift analysis is an *additive quality* layer on top of the V5 failure
taxonomy. It compares an observed change set (files/imports/routes/components)
and/or a prompt output against explicitly declared contracts, and surfaces
drift findings whose evidence is always an observable value (a path, pattern,
enum value, invariant code or output term).

Two invariants drive this module:

* **No fabricated violation.** A deterministic finding is emitted only from a
  recorded check against caller-supplied observable data. A judge-derived
  finding is emitted only when a judge actually ran and its provenance
  (judge model id, rubric id, judge run/evaluation id) is recorded on the
  finding itself. Without that provenance, no judge finding is ever created.
* **Additive only.** Nothing here mutates the V5 taxonomy, failure records or
  existing findings: drift findings are stored as a separate
  ``drift_analyses`` document and only materialize into ordinary findings when
  the caller explicitly asks for it.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import DRIFT_SOURCES, DRIFT_TYPES, new_id, now_iso
from . import judge_system
from . import redaction
from . import tracing

DRIFT_VERSION = "v6.9.0"

SCOPES: Tuple[str, ...] = ("all", "prompt", "design", "architecture", "logic")

_ALLOWED_SEVERITIES = ("info", "low", "medium", "high")

_CATEGORY_TO_DRIFT_TYPE: Dict[str, str] = {
    "prompt_drift": "PROMPT_DRIFT",
    "design_drift": "DESIGN_DRIFT",
    "architecture_drift": "ARCHITECTURE_DRIFT",
    "logic_drift": "LOGIC_DRIFT",
}
_DRIFT_TYPE_TO_CATEGORY: Dict[str, str] = {
    value: key for key, value in _CATEGORY_TO_DRIFT_TYPE.items()
}

_DRIFT_TYPE_TO_TAXONOMY: Dict[str, str] = {
    "PROMPT_DRIFT": "QUALITY.PROMPT_DRIFT",
    "DESIGN_DRIFT": "QUALITY.DESIGN_DRIFT",
    "ARCHITECTURE_DRIFT": "QUALITY.ARCHITECTURE_DRIFT",
    "LOGIC_DRIFT": "QUALITY.LOGIC_DRIFT",
}

_SCOPE_TO_DRIFT_TYPE: Dict[str, str] = {
    "prompt": "PROMPT_DRIFT",
    "design": "DESIGN_DRIFT",
    "architecture": "ARCHITECTURE_DRIFT",
    "logic": "LOGIC_DRIFT",
}

# Deterministic checks exposed by GET /drift/catalog.
DRIFT_CHECKS: Tuple[Dict[str, str], ...] = (
    {
        "check_id": "architecture.parallel_subsystem",
        "drift_type": "ARCHITECTURE_DRIFT",
        "scope": "architecture",
        "description": "A canonical service is reimplemented outside its declared paths.",
    },
    {
        "check_id": "architecture.duplicate_service_responsibility",
        "drift_type": "ARCHITECTURE_DRIFT",
        "scope": "architecture",
        "description": "A change adds a service name already declared canonical.",
    },
    {
        "check_id": "architecture.forbidden_pattern",
        "drift_type": "ARCHITECTURE_DRIFT",
        "scope": "architecture",
        "description": "An architecture-forbidden pattern appears in paths/imports.",
    },
    {
        "check_id": "design.hardcoded_design_token",
        "drift_type": "DESIGN_DRIFT",
        "scope": "design",
        "description": "A hardcoded color is not one of the declared design tokens.",
    },
    {
        "check_id": "design.duplicate_ui_pattern",
        "drift_type": "DESIGN_DRIFT",
        "scope": "design",
        "description": "The same UI component is added in more than one file.",
    },
    {
        "check_id": "design.forbidden_pattern",
        "drift_type": "DESIGN_DRIFT",
        "scope": "design",
        "description": "A design-forbidden pattern appears in paths/imports.",
    },
    {
        "check_id": "logic.duplicated_business_rule",
        "drift_type": "LOGIC_DRIFT",
        "scope": "logic",
        "description": "A business rule appears in both backend and frontend code.",
    },
    {
        "check_id": "logic.conflicting_enum_semantics",
        "drift_type": "LOGIC_DRIFT",
        "scope": "logic",
        "description": "Declared backend and frontend enum values differ.",
    },
    {
        "check_id": "logic.forbidden_pattern",
        "drift_type": "LOGIC_DRIFT",
        "scope": "logic",
        "description": "A logic-forbidden pattern appears in paths/imports.",
    },
    {
        "check_id": "prompt.missing_required_invariant",
        "drift_type": "PROMPT_DRIFT",
        "scope": "prompt",
        "description": "A required invariant was not used in the compiled prompt.",
    },
    {
        "check_id": "prompt.required_phase_omitted",
        "drift_type": "PROMPT_DRIFT",
        "scope": "prompt",
        "description": "A required output phase label is missing from the output.",
    },
    {
        "check_id": "prompt.prohibited_scope_changed",
        "drift_type": "PROMPT_DRIFT",
        "scope": "prompt",
        "description": "A prohibited scope term appears in the output.",
    },
)


class DriftError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class DriftNotFound(DriftError):
    """Owner-scoped drift analysis/project missing (HTTP 404)."""


# ------------------------------------------------------------------ helpers
def drift_failure_codes() -> Tuple[str, ...]:
    """The four canonical QUALITY.*_DRIFT taxonomy codes."""
    return tuple(_DRIFT_TYPE_TO_TAXONOMY.values())


def taxonomy_code_for_drift_type(drift_type: str) -> Optional[str]:
    """Return the canonical taxonomy code for a DRIFT_TYPES value."""
    return _DRIFT_TYPE_TO_TAXONOMY.get(drift_type)


def drift_catalog() -> Dict[str, Any]:
    """Static catalog of drift types, sources, scopes and deterministic checks."""
    return {
        "version": DRIFT_VERSION,
        "drift_types": list(DRIFT_TYPES),
        "sources": list(DRIFT_SOURCES),
        "scopes": list(SCOPES),
        "checks": [dict(check) for check in DRIFT_CHECKS],
    }


def _list(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item)
        if text:
            out.append(text)
    return out


def _changed_files(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = payload.get("changed_files")
    if not isinstance(raw, list):
        return []
    files: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            continue
        files.append({
            "path": path,
            "language": str(item.get("language") or ""),
            "added_lines": item.get("added_lines") or 0,
            "removed_lines": item.get("removed_lines") or 0,
            "imports_added": _list(item.get("imports_added")),
            "routes_added": _list(item.get("routes_added")),
            "components_added": _list(item.get("components_added")),
            "services_added": _list(item.get("services_added")),
            "enums_added": _list(item.get("enums_added")),
            "constants_added": _list(item.get("constants_added")),
            "hardcoded_colors": _list(item.get("hardcoded_colors")),
            "design_tokens_added": _list(item.get("design_tokens_added")),
        })
    return files


def _finding(
    drift_type: str,
    check_id: str,
    severity: str,
    message: str,
    evidence: Dict[str, Any],
    *,
    source: str = "deterministic",
    provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    category = _DRIFT_TYPE_TO_CATEGORY.get(drift_type, drift_type.lower())
    prov: Dict[str, Any] = {"check_id": check_id, "drift_version": DRIFT_VERSION}
    if provenance:
        prov.update(provenance)
    return {
        "id": new_id(),
        "drift_type": drift_type,
        "category": category,
        "severity": severity if severity in _ALLOWED_SEVERITIES else "medium",
        "message": message,
        "evidence": evidence or {},
        "source": source,
        "provenance": prov,
    }


def _canonical_services(contracts: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = contracts.get("canonical_services")
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get("name"):
            out.append(entry)
    return out


def _declared_paths(service: Dict[str, Any]) -> List[str]:
    raw = service.get("paths")
    if isinstance(raw, str):
        raw = [raw]
    return _list(raw)


def _path_is_declared(path: str, declared: List[str]) -> bool:
    for candidate in declared:
        if path == candidate:
            return True
        prefix = candidate.rstrip("/")
        if prefix and path.startswith(prefix + "/"):
            return True
    return False


def _forbidden(contracts: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    raw = contracts.get("forbidden_patterns")
    if not isinstance(raw, list):
        return []
    return [
        entry for entry in raw
        if isinstance(entry, dict) and str(entry.get("type") or "").lower() == kind
    ]


def _match_forbidden(
    files: List[Dict[str, Any]], entries: List[Dict[str, Any]]
) -> List[Tuple[Dict[str, Any], str, str]]:
    matches: List[Tuple[Dict[str, Any], str, str]] = []
    for entry in entries:
        pattern = str(entry.get("pattern") or "")
        if not pattern:
            continue
        matched_field = ""
        matched_value = ""
        for changed in files:
            if pattern in (changed.get("path") or ""):
                matched_field, matched_value = "path", changed.get("path") or ""
                break
            for imported in changed.get("imports_added") or []:
                if pattern in imported:
                    matched_field, matched_value = "import", imported
                    break
            if matched_field:
                break
        if matched_field:
            matches.append((entry, matched_field, matched_value))
    return matches


# ------------------------------------------------------------------ deterministic checks
def _architecture_findings(
    files: List[Dict[str, Any]], contracts: Dict[str, Any]
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    services = _canonical_services(contracts)
    canonical_names = {str(service["name"]) for service in services}

    for changed in files:
        path = changed["path"]
        if "/services/" not in path:
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        for service in services:
            name = str(service["name"])
            if stem != name:
                continue
            declared = _declared_paths(service)
            if _path_is_declared(path, declared):
                continue
            findings.append(_finding(
                "ARCHITECTURE_DRIFT",
                f"architecture.parallel_subsystem:{path}",
                str(service.get("severity") or "high"),
                f"Changed file '{path}' reimplements canonical service '{name}' "
                "outside its declared paths.",
                {
                    "check": "parallel_subsystem",
                    "path": path,
                    "service_name": name,
                    "declared_paths": declared,
                },
            ))
            break

    for changed in files:
        for added in changed["services_added"]:
            if added in canonical_names:
                findings.append(_finding(
                    "ARCHITECTURE_DRIFT",
                    f"architecture.duplicate_service_responsibility:{added}",
                    "medium",
                    f"Changed file '{changed['path']}' adds service '{added}', "
                    "which is already declared canonical.",
                    {
                        "check": "duplicate_service_responsibility",
                        "path": changed["path"],
                        "service_name": added,
                        "canonical": True,
                    },
                ))

    for entry, field, value in _match_forbidden(files, _forbidden(contracts, "architecture")):
        pattern = str(entry.get("pattern"))
        findings.append(_finding(
            "ARCHITECTURE_DRIFT",
            f"architecture.forbidden_pattern:{pattern}",
            str(entry.get("severity") or "medium"),
            f"Forbidden architecture pattern '{pattern}' matched {field} '{value}'.",
            {
                "check": "forbidden_pattern",
                "pattern": pattern,
                "matched_field": field,
                "matched_value": value,
                "forbidden_id": entry.get("id"),
            },
        ))
    return findings


def _design_findings(
    files: List[Dict[str, Any]], contracts: Dict[str, Any]
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    tokens = set(_list(contracts.get("design_tokens")))

    for changed in files:
        for color in changed["hardcoded_colors"]:
            if color in tokens:
                continue
            findings.append(_finding(
                "DESIGN_DRIFT",
                f"design.hardcoded_design_token:{changed['path']}:{color}",
                "medium",
                f"Hardcoded color '{color}' in '{changed['path']}' is not a "
                "declared design token.",
                {
                    "check": "hardcoded_design_token",
                    "path": changed["path"],
                    "value": color,
                    "expected_tokens": sorted(tokens),
                },
            ))

    component_paths: Dict[str, List[str]] = {}
    for changed in files:
        for component in changed["components_added"]:
            component_paths.setdefault(component, [])
            if changed["path"] not in component_paths[component]:
                component_paths[component].append(changed["path"])
    for component, paths in sorted(component_paths.items()):
        if len(paths) > 1:
            findings.append(_finding(
                "DESIGN_DRIFT",
                f"design.duplicate_ui_pattern:{component}",
                "low",
                f"UI component '{component}' is added in {len(paths)} files: "
                f"{', '.join(paths)}.",
                {
                    "check": "duplicate_ui_pattern",
                    "component": component,
                    "paths": paths,
                },
            ))

    for entry, field, value in _match_forbidden(files, _forbidden(contracts, "design")):
        pattern = str(entry.get("pattern"))
        findings.append(_finding(
            "DESIGN_DRIFT",
            f"design.forbidden_pattern:{pattern}",
            str(entry.get("severity") or "medium"),
            f"Forbidden design pattern '{pattern}' matched {field} '{value}'.",
            {
                "check": "forbidden_pattern",
                "pattern": pattern,
                "matched_field": field,
                "matched_value": value,
                "forbidden_id": entry.get("id"),
            },
        ))
    return findings


def _logic_findings(
    files: List[Dict[str, Any]], contracts: Dict[str, Any]
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    parts: List[str] = []
    for changed in files:
        parts.append(changed["path"])
        parts.extend(changed["imports_added"])
        parts.extend(changed["constants_added"])
    haystack = "\n".join(parts)

    duplicated_rules = contracts.get("duplicated_rules")
    if isinstance(duplicated_rules, list):
        for rule in duplicated_rules:
            if not isinstance(rule, dict):
                continue
            backend = str(rule.get("backend_pattern") or "")
            frontend = str(rule.get("frontend_pattern") or "")
            if not backend or not frontend:
                continue
            if backend in haystack and frontend in haystack:
                rule_id = str(rule.get("id") or rule.get("name") or f"{backend}|{frontend}")
                findings.append(_finding(
                    "LOGIC_DRIFT",
                    f"logic.duplicated_business_rule:{rule_id}",
                    "high",
                    f"Business rule '{rule_id}' appears in both backend "
                    f"('{backend}') and frontend ('{frontend}').",
                    {
                        "check": "duplicated_business_rule",
                        "rule_id": rule_id,
                        "backend_pattern": backend,
                        "frontend_pattern": frontend,
                    },
                ))

    enums = contracts.get("enums")
    if isinstance(enums, list):
        for enum in enums:
            if not isinstance(enum, dict):
                continue
            backend_values = _list(enum.get("backend_values"))
            frontend_values = _list(enum.get("frontend_values"))
            if not backend_values or not frontend_values:
                continue
            if set(backend_values) == set(frontend_values):
                continue
            name = str(enum.get("name") or enum.get("id") or "enum")
            findings.append(_finding(
                "LOGIC_DRIFT",
                f"logic.conflicting_enum_semantics:{name}",
                "high",
                f"Enum '{name}' backend values differ from its frontend values.",
                {
                    "check": "conflicting_enum_semantics",
                    "enum": name,
                    "backend_values": backend_values,
                    "frontend_values": frontend_values,
                },
            ))

    for entry, field, value in _match_forbidden(files, _forbidden(contracts, "logic")):
        pattern = str(entry.get("pattern"))
        findings.append(_finding(
            "LOGIC_DRIFT",
            f"logic.forbidden_pattern:{pattern}",
            str(entry.get("severity") or "medium"),
            f"Forbidden logic pattern '{pattern}' matched {field} '{value}'.",
            {
                "check": "forbidden_pattern",
                "pattern": pattern,
                "matched_field": field,
                "matched_value": value,
                "forbidden_id": entry.get("id"),
            },
        ))
    return findings


def _prompt_findings(prompt_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    required = _list(prompt_context.get("required_invariant_codes"))
    used = set(_list(prompt_context.get("compiled_invariants_used")))
    for code in required:
        if code in used:
            continue
        findings.append(_finding(
            "PROMPT_DRIFT",
            f"prompt.missing_required_invariant:{code}",
            "high",
            f"Required invariant '{code}' was not used in the compiled prompt.",
            {
                "check": "missing_required_invariant",
                "required_invariant_code": code,
                "compiled_invariants_used": sorted(used),
            },
        ))

    output_text = prompt_context.get("output_text")
    output_text = output_text if isinstance(output_text, str) else ""
    if output_text.strip():
        for phase in _list(prompt_context.get("required_phases")):
            if phase in output_text:
                continue
            findings.append(_finding(
                "PROMPT_DRIFT",
                f"prompt.required_phase_omitted:{phase}",
                "medium",
                f"Required phase '{phase}' was not observed in the output.",
                {
                    "check": "required_phase_omitted",
                    "required_phase": phase,
                    "output_length": len(output_text),
                },
            ))
    for term in _list(prompt_context.get("prohibited_scope")):
        if term and term in output_text:
            findings.append(_finding(
                "PROMPT_DRIFT",
                f"prompt.prohibited_scope_changed:{term}",
                "high",
                f"Prohibited scope term '{term}' appeared in the output.",
                {
                    "check": "prohibited_scope_changed",
                    "prohibited_term": term,
                    "matched": True,
                },
            ))
    return findings


def _deterministic_findings(
    files: List[Dict[str, Any]],
    contracts: Dict[str, Any],
    prompt_context: Dict[str, Any],
    scope: str,
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    def _enabled(drift_type: str) -> bool:
        return scope == "all" or _SCOPE_TO_DRIFT_TYPE.get(scope) == drift_type

    if _enabled("ARCHITECTURE_DRIFT"):
        findings.extend(_architecture_findings(files, contracts))
    if _enabled("DESIGN_DRIFT"):
        findings.extend(_design_findings(files, contracts))
    if _enabled("LOGIC_DRIFT"):
        findings.extend(_logic_findings(files, contracts))
    if _enabled("PROMPT_DRIFT"):
        findings.extend(_prompt_findings(prompt_context))
    return findings


# ------------------------------------------------------------------ judge path
async def _judge_findings(
    owner_id: str,
    payload: Dict[str, Any],
    scope: str,
    run: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Judged drift findings, labelled ``judge_derived`` with provenance.

    Returns ``(findings, note)``. When the run/rubric/experiment is missing the
    judge is never called and no finding is fabricated -- only a note is
    returned for the analysis summary.
    """
    judge_model_id = payload.get("judge_model_id")
    rubric_id = payload.get("rubric_id")
    run_id = payload.get("run_id")
    if not (judge_model_id and rubric_id and run_id):
        return [], None
    if run is None:
        return [], "judge_run_not_found"
    experiment_id = run.get("experiment_id")
    if experiment_id:
        experiment = await repo.experiments.get(experiment_id, owner_id)
        if not experiment:
            return [], "judge_experiment_not_found"
    else:
        experiment = {"id": None, "project_id": run.get("project_id")}
    rubric = await repo.rubrics.get(rubric_id, owner_id)
    if not rubric:
        return [], "judge_rubric_not_found"

    try:
        result = await judge_system.evaluate_with_judge(
            owner_id,
            experiment,
            run,
            judge_model_id=judge_model_id,
            rubric=rubric,
            options={"drift": True},
        )
    except Exception as exc:  # pragma: no cover - judge never raises by contract
        return [], f"judge_error:{exc}"
    result = result if isinstance(result, dict) else {}
    judge = result.get("judge") if isinstance(result.get("judge"), dict) else {}

    allowed = None if scope == "all" else {_SCOPE_TO_DRIFT_TYPE.get(scope)}
    findings: List[Dict[str, Any]] = []
    for index, finding in enumerate(result.get("findings") or []):
        if not isinstance(finding, dict):
            continue
        category = finding.get("category")
        drift_type = _CATEGORY_TO_DRIFT_TYPE.get(category)
        if drift_type is None:
            continue
        if allowed is not None and drift_type not in allowed:
            continue
        message = finding.get("message")
        if not isinstance(message, str) or not message.strip():
            continue
        severity = finding.get("severity")
        if severity not in _ALLOWED_SEVERITIES:
            severity = "medium"
        judge_run_id = result.get("judge_run_id")
        findings.append(_finding(
            drift_type,
            f"judge.{drift_type.lower()}:{index}",
            severity,
            message,
            {
                "category": category,
                "judge_message": message,
                "judge_status": result.get("status"),
                "judge_input_hash": judge.get("input_hash"),
            },
            source="judge_derived",
            provenance={
                "judge_model_id": judge.get("model_id") or judge_model_id,
                "rubric_id": rubric_id,
                "judge_evaluation_id": judge_run_id,
                "judge_run_id": judge_run_id,
                "provider": judge.get("provider_id") or judge.get("provider_name"),
            },
        ))

    note: Optional[str] = None
    if not findings and result.get("status") != "completed":
        note = (
            "judge_not_completed:"
            + str(result.get("error") or result.get("reason") or result.get("status"))
        )
    return findings, note


# ------------------------------------------------------------------ summary / span
def _summary(findings: List[Dict[str, Any]], judge_note: Optional[str]) -> Dict[str, Any]:
    by_type = {drift_type: 0 for drift_type in DRIFT_TYPES}
    deterministic_count = 0
    judge_derived_count = 0
    for finding in findings:
        drift_type = finding.get("drift_type")
        if drift_type in by_type:
            by_type[drift_type] += 1
        if finding.get("source") == "judge_derived":
            judge_derived_count += 1
        else:
            deterministic_count += 1
    summary: Dict[str, Any] = {
        "total": len(findings),
        "by_drift_type": by_type,
        "deterministic_count": deterministic_count,
        "judge_derived_count": judge_derived_count,
    }
    if judge_note:
        summary["judge_note"] = judge_note
    return summary


async def _record_span(
    owner_id: str, doc: Dict[str, Any], run: Optional[Dict[str, Any]]
) -> None:
    """Best-effort DRIFT_ANALYSIS span. Never raises, never blocks analysis."""
    try:
        started = time.perf_counter()
        trace_id = None
        attached = False
        if run is not None:
            candidate = run.get("trace_id")
            if candidate:
                trace = await tracing.get_trace(owner_id, candidate)
                if trace is not None:
                    trace_id = candidate
                    attached = True
        if trace_id is None:
            trace = await tracing.start_trace(
                owner_id,
                "system",
                project_id=doc.get("project_id"),
                experiment_id=doc.get("experiment_id"),
                metadata=redaction.safe_metadata({"kind": "drift_analysis"}),
            )
            trace_id = trace.get("id")
        if not trace_id:
            return
        span = await tracing.start_span(
            owner_id,
            trace_id,
            "DRIFT_ANALYSIS",
            "Drift analysis",
            run_id=run.get("id") if run else None,
            metadata=redaction.safe_metadata({
                "kind": "drift",
                "analysis_id": doc.get("id"),
                "scope": doc.get("scope"),
                "version": doc.get("version"),
                "total": (doc.get("summary") or {}).get("total"),
            }),
            status="completed",
        )
        await tracing.finish_span(span, status="completed", started_perf=started)
        if not attached:
            await tracing.finish_trace(trace, status="completed", started_perf=started)
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass


# ------------------------------------------------------------------ public API
async def analyze_drift(owner_id: str, payload: dict) -> dict:
    """Run a deterministic (+ optional judged) drift analysis and persist it."""
    payload = payload if isinstance(payload, dict) else {}
    project_id = payload.get("project_id")
    if not project_id:
        raise DriftError("project_id is required")
    if not await repo.projects.get(project_id, owner_id):
        raise DriftNotFound("Project not found")

    scope = str(payload.get("scope") or "all").strip().lower()
    if scope not in SCOPES:
        raise DriftError(f"Unsupported drift scope: {scope}")

    files = _changed_files(payload)
    contracts = payload.get("contracts")
    contracts = contracts if isinstance(contracts, dict) else {}
    prompt_context = payload.get("prompt_context")
    prompt_context = prompt_context if isinstance(prompt_context, dict) else {}

    findings = _deterministic_findings(files, contracts, prompt_context, scope)

    run_id = payload.get("run_id")
    run = await repo.runs.get(run_id, owner_id) if run_id else None
    judge_findings, judge_note = await _judge_findings(owner_id, payload, scope, run)
    findings.extend(judge_findings)

    resolved_experiment_id = payload.get("experiment_id")
    if resolved_experiment_id is None and run is not None:
        resolved_experiment_id = run.get("experiment_id")

    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "scope": scope,
        "version": DRIFT_VERSION,
        "findings": findings,
        "summary": _summary(findings, judge_note),
        "run_id": run_id,
        "evaluation_id": payload.get("evaluation_id"),
        "experiment_id": resolved_experiment_id,
        "notes": payload.get("notes") or "",
        "created_at": now_iso(),
        "schema_version": 1,
    }
    await repo.drift_analyses.insert(doc)
    await _record_span(owner_id, doc, run)
    return doc


async def list_drift_analyses(
    owner_id: str,
    *,
    project_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List owner-scoped drift analyses (optionally project-filtered)."""
    query: Dict[str, Any] = {"owner_id": owner_id}
    if project_id is not None:
        if not await repo.projects.get(project_id, owner_id):
            raise DriftNotFound("Project not found")
        query["project_id"] = project_id
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    total = await repo.drift_analyses.count(query)
    items = await repo.drift_analyses.list(
        query, sort_field="created_at", descending=True, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def get_drift_analysis(owner_id: str, analysis_id: str) -> dict:
    """Fetch one owner-scoped analysis or raise DriftNotFound (404)."""
    doc = await repo.drift_analyses.get(analysis_id, owner_id)
    if not doc:
        raise DriftNotFound(f"Drift analysis '{analysis_id}' not found")
    return doc


async def materialize_findings(owner_id: str, analysis_id: str) -> dict:
    """Turn an analysis' drift findings into ordinary repo findings.

    Idempotent: a finding is created only once per
    ``(drift_analysis_id, check_id)`` pair; prior materializations are returned
    under ``existing``.
    """
    analysis = await get_drift_analysis(owner_id, analysis_id)
    project_id = analysis.get("project_id")

    try:
        existing_docs = await repo.findings.list(
            {"owner_id": owner_id, "metadata.drift_analysis_id": analysis_id},
            limit=1000,
        )
    except Exception:  # pragma: no cover - best-effort read
        existing_docs = []

    seen = set()
    existing: List[Dict[str, Any]] = []
    for doc in existing_docs or []:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        check_id = metadata.get("check_id")
        if check_id:
            seen.add(check_id)
        existing.append(doc)

    created: List[Dict[str, Any]] = []
    now = now_iso()
    for finding in analysis.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        provenance = finding.get("provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        check_id = provenance.get("check_id")
        if not check_id or check_id in seen:
            continue
        seen.add(check_id)
        drift_type = str(finding.get("drift_type") or "")
        message = str(finding.get("message") or "")
        doc = {
            "id": new_id(),
            "owner_id": owner_id,
            "project_id": project_id,
            "title": message[:300],
            "statement": message,
            "description": message,
            "status": "provisional",
            "confidence": None,
            "tags": ["drift", drift_type.lower()],
            "evidence": [{
                "type": "drift_analysis",
                "ref_id": analysis_id,
                "label": message[:200],
                "exists": True,
            }],
            "metadata": {
                "drift_type": drift_type,
                "category": finding.get("category"),
                "source": finding.get("source"),
                "provenance": provenance,
                "check_id": check_id,
                "drift_analysis_id": analysis_id,
            },
            "created_at": now,
            "updated_at": now,
        }
        await repo.findings.insert(doc)
        created.append(doc)
    return {"created": created, "existing": existing}
