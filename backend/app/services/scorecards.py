"""V5.8 Research Quality Scorecards.

A scorecard is a *truthful* per-dimension view of a run, an experiment, or a
baseline/candidate comparison. Three invariants drive every decision in this
module:

* **Missing is not zero.** A dimension that cannot be observed is reported with
  ``value=None`` and a state of ``not_available`` or ``not_evaluated`` -- it is
  never coerced to ``0``.
* **Provenance is explicit.** Every dimension records *why* it has its value,
  which evaluator types produced it and links to the evaluation/run/failure
  evidence behind it. Subjective judge signals are labelled
  ``source="judge_derived"``, deterministic/rule signals ``source="deterministic"``
  and raw run telemetry ``source="telemetry"``.
* **No invented global score.** ``composite.available`` is ``False`` with reason
  ``no_scoring_policy`` unless the caller supplies an explicit, versioned scoring
  policy. When one is supplied the weighted mean is taken over *available*
  dimensions only and the policy id/version and missing dimensions are recorded.

All resources are owner-scoped; a cross-project reference is a 404/400, never a
silent read.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import new_id, now_iso
from . import experiments as experiment_service
from . import redaction
from . import regression
from . import tracing

SCORECARD_SCHEMA_VERSION = 1
SCORECARD_BUILDER_VERSION = "v5.8.0"


class ScorecardError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class ScorecardNotFound(ScorecardError):
    """Owner-scoped scorecard/run/experiment missing (HTTP 404)."""


# ------------------------------------------------------------------ dimensions
# ``source`` is the *default* provenance for a dimension. Evaluative dimensions
# may be downgraded from ``deterministic`` to ``judge_derived`` at build time.
DIMENSION_SPECS: Tuple[Dict[str, str], ...] = (
    {"key": "correctness", "label": "Correctness", "unit": "score", "source": "deterministic"},
    {"key": "grounding", "label": "Grounding", "unit": "score", "source": "deterministic"},
    {"key": "citation", "label": "Citation", "unit": "score", "source": "deterministic"},
    {"key": "instruction_following", "label": "Instruction following", "unit": "score", "source": "deterministic"},
    {"key": "tool_discipline", "label": "Tool discipline", "unit": "score", "source": "deterministic"},
    {"key": "completeness", "label": "Completeness", "unit": "score", "source": "judge_derived"},
    {"key": "source_quality", "label": "Source quality", "unit": "score", "source": "judge_derived"},
    {"key": "research_breadth", "label": "Research breadth", "unit": "count", "source": "telemetry"},
    {"key": "latency", "label": "Latency", "unit": "ms", "source": "telemetry"},
    {"key": "token_use", "label": "Token use", "unit": "tokens", "source": "telemetry"},
    {"key": "cost", "label": "Cost", "unit": "usd", "source": "telemetry"},
    {"key": "runtime_reliability", "label": "Runtime reliability", "unit": "ratio", "source": "telemetry"},
    # Additive (V6.9 Drift & Failure Analysis): observed drift failures.
    {"key": "drift", "label": "Drift", "unit": "count", "source": "deterministic"},
    # Additive (V6.10 Scorecards & Certification Handoff): agent/prompt
    # evaluation-derived dimensions. Each is emitted as ``not_available`` when
    # no matching evaluation was observed -- never coerced to zero.
    {"key": "policy_compliance", "label": "Policy compliance", "unit": "ratio", "source": "deterministic"},
    {"key": "invariant_compliance", "label": "Invariant compliance", "unit": "ratio", "source": "deterministic"},
    {"key": "task_completion", "label": "Task completion", "unit": "ratio", "source": "judge_derived"},
    {"key": "context_usage", "label": "Context usage", "unit": "ratio", "source": "deterministic"},
)
DIMENSIONS: Dict[str, Dict[str, str]] = {spec["key"]: dict(spec) for spec in DIMENSION_SPECS}
DIMENSION_ORDER: Tuple[str, ...] = tuple(spec["key"] for spec in DIMENSION_SPECS)

# Evaluative dimensions the judge may supply; subjective by nature.
_JUDGE_ONLY_DIMENSIONS = ("completeness", "source_quality")
_EVALUATIVE_DIMENSIONS = (
    "correctness",
    "grounding",
    "citation",
    "instruction_following",
)

# Dimensions where a smaller value is better. Everything else is higher-is-better.
_LOWER_IS_BETTER = ("latency", "token_use", "cost", "drift")
DIMENSION_DIRECTIONS: Dict[str, str] = {
    key: ("lower_is_better" if key in _LOWER_IS_BETTER else "higher_is_better")
    for key in DIMENSION_ORDER
}

# Reuse the regression engine's direction table where a scorecard dimension has
# an unambiguous metric analogue. ``tool_discipline`` is deliberately absent:
# the regression ``tool_error_total`` direction is inverted relative to a
# discipline ratio.
_DIMENSION_TO_REGRESSION_METRIC = {
    "latency": "avg_latency_ms",
    "token_use": "total_input_tokens",
    "cost": "known_cost_total",
}

# Evaluator/method keys that imply a given evaluative dimension.
_DIM_EVALUATOR_KEYS: Dict[str, set] = {
    "correctness": {
        "exact_match", "contains", "regex", "token_overlap", "assertions", "correctness",
    },
    "grounding": {"knowledge_grounding", "grounding"},
    "citation": {"knowledge_grounding", "grounded_citation", "citation"},
    "instruction_following": {
        "assertions", "tool_policy", "instruction_following", "red_team",
    },
    "tool_discipline": {"tool_policy", "tool_discipline"},
    "completeness": {"completeness"},
    "source_quality": {"source_quality"},
}

_LEGACY_EVAL_ALIASES = {"llm_judge": "judge_model", "ai_judge": "judge_model"}
_STATE_AVAILABLE = "available"
_STATE_NOT_EVALUATED = "not_evaluated"
_STATE_NOT_AVAILABLE = "not_available"


# ------------------------------------------------------------------ primitives
def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return float(value)


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def _eval_type(evaluation: Dict[str, Any]) -> str:
    raw = evaluation.get("evaluator_type") or evaluation.get("evaluation_type") or ""
    if not isinstance(raw, str):
        return ""
    raw = raw.strip()
    return _LEGACY_EVAL_ALIASES.get(raw, raw)


def _eval_result(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    result = evaluation.get("result")
    return result if isinstance(result, dict) else {}


def _eval_outcome(evaluation: Dict[str, Any]) -> Optional[str]:
    outcome = _eval_result(evaluation).get("outcome")
    return outcome if isinstance(outcome, str) else None


def _eval_score(evaluation: Dict[str, Any]) -> Optional[float]:
    score = _numeric(_eval_result(evaluation).get("score"))
    if score is None:
        score = _numeric(evaluation.get("parsed_score"))
    return score


def _eval_scores(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    scores = evaluation.get("scores")
    return scores if isinstance(scores, dict) else {}


def _eval_findings(evaluation: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = evaluation.get("findings")
    return [f for f in findings if isinstance(f, dict)] if isinstance(findings, list) else []


def _eval_evidence(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    evidence = evaluation.get("evidence")
    return evidence if isinstance(evidence, dict) else {}


def _evaluator_keys(evaluation: Dict[str, Any]) -> set:
    keys: set = set()
    declared = evaluation.get("evaluator_keys")
    if isinstance(declared, list):
        keys.update(k for k in declared if isinstance(k, str))
    evaluator = evaluation.get("evaluator")
    if isinstance(evaluator, dict) and isinstance(evaluator.get("key"), str):
        keys.add(evaluator["key"])
    for field in ("method", "eval_type", "evaluator_type", "evaluation_type"):
        value = evaluation.get(field)
        if isinstance(value, str):
            keys.add(value)
    return keys


def _dimension(
    key: str,
    *,
    state: str,
    source: str,
    explanation: str,
    value: Optional[float] = None,
    evidence: Optional[List[Dict[str, str]]] = None,
    evaluator_keys: Optional[List[str]] = None,
    sample_size: int = 0,
    judge_derived: bool = False,
    deterministic: bool = False,
    not_evaluated_count: int = 0,
) -> Dict[str, Any]:
    spec = DIMENSIONS[key]
    return {
        "key": key,
        "label": spec["label"],
        "value": value,
        "unit": spec["unit"],
        "state": state,
        "source": source,
        "explanation": explanation,
        "evidence": evidence or [],
        "evaluator_keys": evaluator_keys or [],
        "sample_size": sample_size,
        "judge_derived": judge_derived,
        "deterministic": deterministic,
        "not_evaluated_count": not_evaluated_count,
        "direction": DIMENSION_DIRECTIONS[key],
    }


def _evidence_refs(run_id: str, evaluation_ids: List[str], failure_ids: List[str]) -> List[Dict[str, str]]:
    refs: List[Dict[str, str]] = [{"type": "run", "ref_id": run_id}]
    for evaluation_id in evaluation_ids:
        refs.append({"type": "evaluation", "ref_id": evaluation_id})
    for failure_id in failure_ids:
        refs.append({"type": "failure", "ref_id": failure_id})
    return refs


def _failures_by_dimension(failures: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    for failure in failures:
        failure_id = failure.get("id")
        category = failure.get("category")
        if not failure_id or not isinstance(category, str):
            continue
        if category not in DIMENSIONS:
            continue
        mapping.setdefault(category, []).append(failure_id)
    return mapping


# ------------------------------------------------------------------ evaluative dims
def _marker_validity_citation(evaluations: List[Dict[str, Any]]) -> Optional[Tuple[float, str]]:
    """Return ``(valid_ratio, evaluation_id)`` from marker-validity coverage."""
    for evaluation in evaluations:
        if _eval_type(evaluation) not in ("deterministic", "rule"):
            continue
        coverage = _eval_evidence(evaluation).get("marker_validity")
        if not isinstance(coverage, dict):
            details = evaluation.get("details")
            coverage = details.get("marker_validity") if isinstance(details, dict) else None
        if not isinstance(coverage, dict):
            continue
        total = _numeric(coverage.get("total"))
        valid = _numeric(coverage.get("valid_count"))
        if total is not None and total > 0 and valid is not None:
            return round(valid / total, 6), evaluation.get("id")
    return None


def _resolve_evaluative_dimension(
    key: str,
    evaluations: List[Dict[str, Any]],
    run_id: str,
    failure_ids: List[str],
) -> Dict[str, Any]:
    if key == "citation":
        coverage = _marker_validity_citation(evaluations)
        if coverage is not None:
            ratio, evaluation_id = coverage
            return _dimension(
                key,
                state=_STATE_AVAILABLE,
                source="deterministic",
                value=ratio,
                explanation=(
                    "Citation marker validity: valid_count/total over "
                    "knowledge markers found in the evaluated run."
                ),
                evidence=_evidence_refs(run_id, [evaluation_id], failure_ids),
                evaluator_keys=["knowledge_grounding"],
                sample_size=1,
                deterministic=True,
            )

    det_values: List[float] = []
    judge_values: List[float] = []
    evidence_ids: List[str] = []
    evaluator_keys: set = set()
    not_evaluated = 0

    for evaluation in evaluations:
        etype = _eval_type(evaluation)
        if etype not in ("deterministic", "rule", "judge_model", "human"):
            continue
        evaluation_id = evaluation.get("id")
        scores = _eval_scores(evaluation)
        direct = _numeric(scores.get(key))
        if direct is None and key in _JUDGE_ONLY_DIMENSIONS:
            direct = _numeric(scores.get("dimension"))
        if direct is None and key not in _JUDGE_ONLY_DIMENSIONS:
            direct = _numeric(scores.get("dimension"))
        finding_match = any(f.get("category") == key for f in _eval_findings(evaluation))
        key_match = bool(_DIM_EVALUATOR_KEYS.get(key, set()) & _evaluator_keys(evaluation))
        overall = _eval_score(evaluation)
        outcome = _eval_outcome(evaluation)

        if etype in ("deterministic", "rule"):
            if direct is not None:
                det_values.append(direct)
                _track(evidence_ids, evaluator_keys, evaluation, key)
            elif finding_match:
                # A finding is an observed failure signal. Use the evaluation's
                # own score when it exists, else a real reported 0.0 -- never an
                # invented value for a missing observation.
                det_values.append(overall if overall is not None else 0.0)
                _track(evidence_ids, evaluator_keys, evaluation, key)
            elif key_match and overall is not None:
                det_values.append(overall)
                _track(evidence_ids, evaluator_keys, evaluation, key)
            elif (
                overall is not None
                and key == "correctness"
                and etype == "deterministic"
            ):
                det_values.append(overall)
                _track(evidence_ids, evaluator_keys, evaluation, key)
            elif outcome == "NOT_EVALUATED" and (key_match or finding_match):
                not_evaluated += 1
        elif etype in ("judge_model", "human"):
            if direct is not None:
                judge_values.append(direct)
                _track(evidence_ids, evaluator_keys, evaluation, key)
            elif finding_match and overall is not None:
                judge_values.append(overall)
                _track(evidence_ids, evaluator_keys, evaluation, key)
            elif outcome == "NOT_EVALUATED" and (
                key_match or finding_match or key in _JUDGE_ONLY_DIMENSIONS
            ):
                not_evaluated += 1

    if det_values:
        return _dimension(
            key,
            state=_STATE_AVAILABLE,
            source="deterministic",
            value=_mean(det_values),
            explanation=(
                f"Mean of {len(det_values)} deterministic/rule evaluation score(s) "
                f"for '{key}'. Deterministic, not a judge opinion."
            ),
            evidence=_evidence_refs(run_id, sorted(set(evidence_ids)), failure_ids),
            evaluator_keys=sorted(evaluator_keys),
            sample_size=len(det_values),
            deterministic=True,
        )
    if judge_values:
        return _dimension(
            key,
            state=_STATE_AVAILABLE,
            source="judge_derived",
            value=_mean(judge_values),
            explanation=(
                f"Mean of {len(judge_values)} judge evaluation dimension score(s) "
                f"for '{key}'. Subjective signal; treated as judge-derived."
            ),
            evidence=_evidence_refs(run_id, sorted(set(evidence_ids)), failure_ids),
            evaluator_keys=sorted(evaluator_keys),
            sample_size=len(judge_values),
            judge_derived=True,
        )
    if not_evaluated:
        return _dimension(
            key,
            state=_STATE_NOT_EVALUATED,
            source=DIMENSIONS[key]["source"],
            explanation=(
                f"{not_evaluated} evaluation(s) for this run returned NOT_EVALUATED "
                f"with no score for '{key}'. The value is unknown, not zero."
            ),
            evidence=_evidence_refs(run_id, [], failure_ids),
            not_evaluated_count=not_evaluated,
        )
    return _dimension(
        key,
        state=_STATE_NOT_AVAILABLE,
        source=DIMENSIONS[key]["source"],
        explanation=(
            f"No evaluation observed '{key}' for this run. The value is missing, "
            "not zero."
        ),
        evidence=_evidence_refs(run_id, [], failure_ids),
    )


def _track(
    evidence_ids: List[str],
    evaluator_keys: set,
    evaluation: Dict[str, Any],
    key: str,
) -> None:
    evaluation_id = evaluation.get("id")
    if evaluation_id:
        evidence_ids.append(evaluation_id)
    evaluator_keys.update(_evaluator_keys(evaluation) & _DIM_EVALUATOR_KEYS.get(key, set()))


# ------------------------------------------------------------------ telemetry dims
def _tool_discipline(run: Dict[str, Any], run_id: str, failure_ids: List[str]) -> Dict[str, Any]:
    count = _int_or_none(run.get("tool_call_count"))
    failures = _int_or_none(run.get("tool_failure_count"))
    if count is None:
        return _dimension(
            "tool_discipline",
            state=_STATE_NOT_AVAILABLE,
            source="deterministic",
            explanation="Run tool_call_count telemetry is unavailable; discipline is unknown.",
            evidence=_evidence_refs(run_id, [], failure_ids),
        )
    if count == 0:
        value = 1.0
        explanation = "No tool calls were made (tool_call_count = 0); nothing failed."
    elif failures is None:
        return _dimension(
            "tool_discipline",
            state=_STATE_NOT_AVAILABLE,
            source="deterministic",
            explanation=(
                "tool_call_count is known but tool_failure_count is unavailable; "
                "discipline cannot be computed."
            ),
            evidence=_evidence_refs(run_id, [], failure_ids),
        )
    else:
        value = round(1 - (failures / count), 6)
        explanation = (
            f"tool_failure_count/tool_call_count = {failures}/{count} inspected; "
            "discipline = 1 - failures/count."
        )
    return _dimension(
        "tool_discipline",
        state=_STATE_AVAILABLE,
        source="deterministic",
        value=value,
        explanation=explanation,
        evidence=_evidence_refs(run_id, [], failure_ids),
        sample_size=1,
        deterministic=True,
    )


def _research_breadth(run: Dict[str, Any], run_id: str, failure_ids: List[str]) -> Dict[str, Any]:
    source_count = _int_or_none(run.get("source_count"))
    evidence_count = _int_or_none(run.get("evidence_item_count"))
    if source_count is not None:
        value, field = float(source_count), "source_count"
    elif evidence_count is not None:
        value, field = float(evidence_count), "evidence_item_count"
    else:
        return _dimension(
            "research_breadth",
            state=_STATE_NOT_AVAILABLE,
            source="telemetry",
            explanation="No source_count or evidence_item_count telemetry; breadth is unknown.",
            evidence=_evidence_refs(run_id, [], failure_ids),
        )
    return _dimension(
        "research_breadth",
        state=_STATE_AVAILABLE,
        source="telemetry",
        value=value,
        explanation=f"Raw run telemetry field '{field}' (sources/evidence items observed).",
        evidence=_evidence_refs(run_id, [], failure_ids),
        sample_size=1,
    )


def _latency(run: Dict[str, Any], run_id: str, failure_ids: List[str]) -> Dict[str, Any]:
    latency = _numeric(run.get("latency_ms"))
    if latency is None:
        return _dimension(
            "latency", state=_STATE_NOT_AVAILABLE, source="telemetry",
            explanation="Run latency_ms telemetry is unavailable; latency is unknown.",
            evidence=_evidence_refs(run_id, [], failure_ids),
        )
    return _dimension(
        "latency", state=_STATE_AVAILABLE, source="telemetry", value=latency,
        explanation="Raw run telemetry field 'latency_ms'.",
        evidence=_evidence_refs(run_id, [], failure_ids), sample_size=1,
    )


def _token_use(run: Dict[str, Any], run_id: str, failure_ids: List[str]) -> Dict[str, Any]:
    input_tokens = _int_or_none(run.get("input_tokens"))
    output_tokens = _int_or_none(run.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return _dimension(
            "token_use", state=_STATE_NOT_AVAILABLE, source="telemetry",
            explanation=(
                "input_tokens and output_tokens are not both known; token use is "
                "unknown (partial telemetry is not treated as zero)."
            ),
            evidence=_evidence_refs(run_id, [], failure_ids),
        )
    return _dimension(
        "token_use", state=_STATE_AVAILABLE, source="telemetry",
        value=float(input_tokens + output_tokens),
        explanation="Raw run telemetry input_tokens + output_tokens.",
        evidence=_evidence_refs(run_id, [], failure_ids), sample_size=1,
    )


def _cost(run: Dict[str, Any], run_id: str, failure_ids: List[str]) -> Dict[str, Any]:
    if run.get("cost_available") is not True:
        return _dimension(
            "cost", state=_STATE_NOT_AVAILABLE, source="telemetry",
            explanation="cost_available is not truthy; cost is unknown, not zero.",
            evidence=_evidence_refs(run_id, [], failure_ids),
        )
    cost = _numeric(run.get("estimated_cost"))
    if cost is None:
        return _dimension(
            "cost", state=_STATE_NOT_AVAILABLE, source="telemetry",
            explanation="cost_available is set but estimated_cost is missing; cost is unknown.",
            evidence=_evidence_refs(run_id, [], failure_ids),
        )
    return _dimension(
        "cost", state=_STATE_AVAILABLE, source="telemetry", value=cost,
        explanation="Raw run telemetry estimated_cost (cost_available = true).",
        evidence=_evidence_refs(run_id, [], failure_ids), sample_size=1,
    )


def _runtime_reliability(run: Dict[str, Any], run_id: str, failure_ids: List[str]) -> Dict[str, Any]:
    status = run.get("status")
    if status == "success":
        value: Optional[float] = 1.0
    elif status == "error":
        value = 0.0
    else:
        value = None
    if value is None:
        return _dimension(
            "runtime_reliability", state=_STATE_NOT_AVAILABLE, source="telemetry",
            explanation=(
                f"Run status '{status}' is neither success nor error; runtime "
                "reliability is unknown."
            ),
            evidence=_evidence_refs(run_id, [], failure_ids),
        )
    return _dimension(
        "runtime_reliability", state=_STATE_AVAILABLE, source="telemetry", value=value,
        explanation=f"Run terminal status was '{status}' (success=1.0, error=0.0).",
        evidence=_evidence_refs(run_id, [], failure_ids), sample_size=1,
    )


def _drift_dimension(
    run_id: str,
    failures: List[Dict[str, Any]],
    evaluations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Observed drift failures for this run (additive V6.9 dimension).

    The value is the count of failures whose canonical taxonomy code is one of
    the four ``QUALITY.*_DRIFT`` codes. Missing is not zero: with no failures
    and no evaluations the dimension is ``not_available``. A measured 0 (at
    least one failure/evaluation observed, none of them drift) is a real 0.0.
    """
    from .drift import drift_failure_codes

    codes = set(drift_failure_codes())
    drift_failure_ids = [
        failure.get("id")
        for failure in failures
        if failure.get("taxonomy_code") in codes and failure.get("id")
    ]
    if not failures and not evaluations:
        return _dimension(
            "drift",
            state=_STATE_NOT_AVAILABLE,
            source="deterministic",
            explanation=(
                "No failures or evaluations were observed for this run; drift "
                "is unknown, not zero."
            ),
            evidence=_evidence_refs(run_id, [], []),
        )
    count = len(drift_failure_ids)
    return _dimension(
        "drift",
        state=_STATE_AVAILABLE,
        source="deterministic",
        value=float(count),
        explanation=(
            f"{count} failure(s) are classified as drift "
            "(QUALITY.PROMPT_DRIFT / DESIGN_DRIFT / ARCHITECTURE_DRIFT / "
            "LOGIC_DRIFT) for this run."
        ),
        evidence=_evidence_refs(run_id, [], drift_failure_ids),
        sample_size=len(failures),
        deterministic=True,
    )


# Additive (V6.10): agent/prompt rule-evaluator dimensions. Each key maps to
# the canonical rule evaluator key(s) that observe it.
_AGENT_DIMENSION_EVALUATOR_KEYS: Dict[str, Tuple[str, ...]] = {
    "policy_compliance": ("agent_policy_compliance",),
    "invariant_compliance": ("agent_invariant_compliance",),
    "context_usage": ("agent_context_usage",),
}
_TASK_COMPLETION_EVALUATOR_KEYS = ("assertions", "task_completion")


def _extended_evaluator_keys(evaluation: Dict[str, Any]) -> set:
    """Evaluator keys including per-evaluator keys inside ``raw_result``.

    A V6.7 rule evaluation stores the selected keys under
    ``raw_result.evaluators[].evaluator_key`` rather than a flat
    ``evaluator_keys`` list, so both shapes must be inspected.
    """
    keys = set(_evaluator_keys(evaluation))
    raw = evaluation.get("raw_result")
    if isinstance(raw, dict):
        for sub in raw.get("evaluators") or []:
            if isinstance(sub, dict) and isinstance(sub.get("evaluator_key"), str):
                keys.add(sub["evaluator_key"])
    return keys


def _matching_sub_result(
    evaluation: Dict[str, Any], target_keys: Tuple[str, ...]
) -> Optional[Dict[str, Any]]:
    raw = evaluation.get("raw_result")
    if isinstance(raw, dict):
        for sub in raw.get("evaluators") or []:
            if isinstance(sub, dict) and sub.get("evaluator_key") in target_keys:
                return sub
    return None


def _pass_fail_from_sub(sub: Dict[str, Any]) -> Tuple[Optional[float], bool]:
    """Map a rule sub-result to ``(value, is_not_evaluated)``.

    ``value`` is 1.0 for a pass, 0.0 for a fail, ``None`` when the sub-result
    cannot decide. Missing is never turned into 0.
    """
    status = sub.get("status")
    passed = sub.get("passed")
    if status == "error":
        return None, False
    if passed is True:
        return 1.0, False
    if passed is False:
        return 0.0, False
    if status == "not_evaluated":
        return None, True
    return None, False


def _agent_pass_rate_dimension(
    key: str,
    evaluations: List[Dict[str, Any]],
    run_id: str,
    failure_ids: List[str],
) -> Dict[str, Any]:
    """Observed pass rate for an agent rule evaluator (PASS=1, FAIL=0)."""
    target_keys = _AGENT_DIMENSION_EVALUATOR_KEYS[key]
    values: List[float] = []
    evidence_ids: List[str] = []
    matched_keys: set = set()
    not_evaluated = 0

    for evaluation in evaluations:
        keys = _extended_evaluator_keys(evaluation)
        match = [candidate for candidate in target_keys if candidate in keys]
        if not match:
            continue
        sub = _matching_sub_result(evaluation, target_keys)
        if sub is not None:
            value, is_not_evaluated = _pass_fail_from_sub(sub)
        else:
            outcome = _eval_outcome(evaluation)
            if outcome == "PASS":
                value, is_not_evaluated = 1.0, False
            elif outcome == "FAIL":
                value, is_not_evaluated = 0.0, False
            elif outcome == "NOT_EVALUATED":
                value, is_not_evaluated = None, True
            else:
                value, is_not_evaluated = None, False
        matched_keys.update(match)
        if evaluation.get("id"):
            evidence_ids.append(evaluation["id"])
        if value is not None:
            values.append(value)
        elif is_not_evaluated:
            not_evaluated += 1

    if values:
        return _dimension(
            key,
            state=_STATE_AVAILABLE,
            source="deterministic",
            value=_mean(values),
            explanation=(
                f"Pass rate over {len(values)} '{target_keys[0]}' evaluation(s) "
                "(PASS=1, FAIL=0). Deterministic rule signal."
            ),
            evidence=_evidence_refs(run_id, sorted(set(evidence_ids)), failure_ids),
            evaluator_keys=sorted(matched_keys),
            sample_size=len(values),
            deterministic=True,
        )
    if not_evaluated:
        return _dimension(
            key,
            state=_STATE_NOT_EVALUATED,
            source=DIMENSIONS[key]["source"],
            explanation=(
                f"{not_evaluated} '{target_keys[0]}' evaluation(s) returned "
                "NOT_EVALUATED. The value is unknown, not zero."
            ),
            evidence=_evidence_refs(run_id, sorted(set(evidence_ids)), failure_ids),
            evaluator_keys=sorted(matched_keys),
            not_evaluated_count=not_evaluated,
        )
    return _dimension(
        key,
        state=_STATE_NOT_AVAILABLE,
        source=DIMENSIONS[key]["source"],
        explanation=(
            f"No '{target_keys[0]}' evaluation observed for this run. The value "
            "is missing, not zero."
        ),
        evidence=_evidence_refs(run_id, [], failure_ids),
    )


def _task_completion_dimension(
    evaluations: List[Dict[str, Any]],
    run_id: str,
    failure_ids: List[str],
) -> Dict[str, Any]:
    """Task completion from assertions/task_completion rules or judge scores.

    Rule/assertion signals are labelled ``deterministic``; a judge rubric score
    named ``task_completion`` is labelled ``judge_derived``. With neither, the
    dimension is ``not_available`` (or ``not_evaluated``), never zero.
    """
    det_values: List[float] = []
    judge_values: List[float] = []
    evidence_ids: List[str] = []
    evaluator_keys: set = set()
    not_evaluated = 0

    for evaluation in evaluations:
        etype = _eval_type(evaluation)
        keys = _extended_evaluator_keys(evaluation)
        scores = _eval_scores(evaluation)
        direct = _numeric(scores.get("task_completion"))

        if etype in ("deterministic", "rule"):
            matched = keys & set(_TASK_COMPLETION_EVALUATOR_KEYS)
            if not matched:
                continue
            evaluator_keys.update(matched)
            if direct is not None:
                det_values.append(direct)
            else:
                sub = _matching_sub_result(evaluation, _TASK_COMPLETION_EVALUATOR_KEYS)
                if sub is not None:
                    value, is_not_evaluated = _pass_fail_from_sub(sub)
                else:
                    outcome = _eval_outcome(evaluation)
                    if outcome == "PASS":
                        value, is_not_evaluated = 1.0, False
                    elif outcome == "FAIL":
                        value, is_not_evaluated = 0.0, False
                    elif outcome == "NOT_EVALUATED":
                        value, is_not_evaluated = None, True
                    else:
                        value, is_not_evaluated = None, False
                if value is not None:
                    det_values.append(value)
                elif is_not_evaluated:
                    not_evaluated += 1
            if evaluation.get("id"):
                evidence_ids.append(evaluation["id"])
        elif etype in ("judge_model", "human"):
            if direct is None:
                continue
            judge_values.append(direct)
            evaluator_keys.add("judge_model")
            if evaluation.get("id"):
                evidence_ids.append(evaluation["id"])
        else:
            continue

    if det_values:
        return _dimension(
            "task_completion",
            state=_STATE_AVAILABLE,
            source="deterministic",
            value=_mean(det_values),
            explanation=(
                f"Task completion over {len(det_values)} assertions/task_completion "
                "rule evaluation(s). Deterministic signal."
            ),
            evidence=_evidence_refs(run_id, sorted(set(evidence_ids)), failure_ids),
            evaluator_keys=sorted(evaluator_keys),
            sample_size=len(det_values),
            deterministic=True,
        )
    if judge_values:
        return _dimension(
            "task_completion",
            state=_STATE_AVAILABLE,
            source="judge_derived",
            value=_mean(judge_values),
            explanation=(
                f"Task completion from {len(judge_values)} judge score(s) keyed "
                "'task_completion'. Subjective signal; treated as judge-derived."
            ),
            evidence=_evidence_refs(run_id, sorted(set(evidence_ids)), failure_ids),
            evaluator_keys=sorted(evaluator_keys),
            sample_size=len(judge_values),
            judge_derived=True,
        )
    if not_evaluated:
        return _dimension(
            "task_completion",
            state=_STATE_NOT_EVALUATED,
            source=DIMENSIONS["task_completion"]["source"],
            explanation=(
                f"{not_evaluated} task-completion evaluation(s) returned "
                "NOT_EVALUATED. The value is unknown, not zero."
            ),
            evidence=_evidence_refs(run_id, sorted(set(evidence_ids)), failure_ids),
            evaluator_keys=sorted(evaluator_keys),
            not_evaluated_count=not_evaluated,
        )
    return _dimension(
        "task_completion",
        state=_STATE_NOT_AVAILABLE,
        source=DIMENSIONS["task_completion"]["source"],
        explanation=(
            "No assertions/task_completion evaluation was observed for this run. "
            "The value is missing, not zero."
        ),
        evidence=_evidence_refs(run_id, [], failure_ids),
    )


# ------------------------------------------------------------------ composite
def _composite(dimensions: List[Dict[str, Any]], policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    dimension_map = {d["key"]: d for d in dimensions}
    all_missing = [
        d["key"]
        for d in dimensions
        if not (d.get("state") == _STATE_AVAILABLE and _numeric(d.get("value")) is not None)
    ]
    if not policy:
        return {
            "available": False,
            "reason": "no_scoring_policy",
            "value": None,
            "policy": None,
            "dimensions_used": [],
            "missing_dimensions": all_missing,
            "note": "No versioned scoring policy supplied; no composite score is computed.",
        }

    policy_id = policy.get("id")
    policy_version = policy.get("version")
    weights = policy.get("weights")
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise ScorecardError("scoring policy requires a non-empty id")
    if policy_version is None or isinstance(policy_version, bool):
        raise ScorecardError("scoring policy requires a version")
    if not isinstance(weights, dict) or not weights:
        raise ScorecardError("scoring policy requires a non-empty weights mapping")

    used: List[str] = []
    missing: List[str] = []
    weighted = 0.0
    total_weight = 0.0
    for key, weight in weights.items():
        numeric_weight = _numeric(weight)
        if numeric_weight is None:
            raise ScorecardError(f"scoring policy weight for '{key}' must be numeric")
        dimension = dimension_map.get(key)
        if dimension is None:
            raise ScorecardError(f"scoring policy references unknown dimension: {key}")
        value = _numeric(dimension.get("value")) if dimension.get("state") == _STATE_AVAILABLE else None
        if value is None:
            missing.append(key)
            continue
        used.append(key)
        weighted += value * numeric_weight
        total_weight += numeric_weight

    recorded_policy = {"id": policy_id, "version": policy_version, "weights": dict(weights)}
    if total_weight <= 0:
        return {
            "available": False,
            "reason": "no_available_dimensions",
            "value": None,
            "policy": recorded_policy,
            "dimensions_used": [],
            "missing_dimensions": missing,
            "note": "No weighted dimension was available; no composite score is computed.",
        }
    return {
        "available": True,
        "reason": None,
        "value": round(weighted / total_weight, 6),
        "policy": recorded_policy,
        "dimensions_used": used,
        "missing_dimensions": missing,
        "note": "Weighted mean over available dimensions only.",
    }


# ------------------------------------------------------------------ views
_RUN_VIEW_FIELDS = (
    "id", "status", "project_id", "experiment_id", "configuration_id", "model_id",
    "dataset_case_id", "dataset_version_id", "latency_ms", "ttft_ms", "input_tokens",
    "output_tokens", "estimated_cost", "cost_available", "tool_call_count",
    "tool_failure_count", "tool_success_count", "source_count", "evidence_item_count",
    "trace_id", "execution_kind", "repetition_index", "attempt_index", "created_at",
)


def _run_view(run: Dict[str, Any]) -> Dict[str, Any]:
    view = {field: run.get(field) for field in _RUN_VIEW_FIELDS}
    return redaction.redact(view)


def _experiment_view(experiment: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("id", "project_id", "title", "status", "experiment_type", "created_at")
    return redaction.redact({key: experiment.get(key) for key in keys})


def _evaluation_view(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": evaluation.get("id"),
        "evaluator_type": _eval_type(evaluation) or evaluation.get("evaluator_type"),
        "outcome": _eval_outcome(evaluation),
        "score": _eval_score(evaluation),
        "status": evaluation.get("status"),
    }


def _outcome_counts(evaluations: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "NOT_EVALUATED": 0}
    for evaluation in evaluations:
        outcome = _eval_outcome(evaluation)
        if outcome in counts:
            counts[outcome] += 1
    return counts


# ------------------------------------------------------------------ run scorecard
async def _load_run_evaluations(owner_id: str, run_id: str) -> List[Dict[str, Any]]:
    return await repo.evaluations.list(
        {"owner_id": owner_id, "run_id": run_id},
        sort_field="created_at",
        descending=False,
        limit=1000,
    )


async def _load_run_failures(owner_id: str, run_id: str) -> List[Dict[str, Any]]:
    try:
        return await repo.failures.list(
            {"owner_id": owner_id, "run_id": run_id},
            sort_field="created_at",
            descending=False,
            limit=1000,
        )
    except Exception:  # pragma: no cover - best-effort evidence read
        return []


async def build_run_scorecard(
    owner_id: str,
    run_id: str,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a live, per-dimension scorecard for a single owner-scoped run."""
    run = await repo.runs.get(run_id, owner_id)
    if not run:
        raise ScorecardNotFound("Run not found")

    evaluations = await _load_run_evaluations(owner_id, run_id)
    failures = await _load_run_failures(owner_id, run_id)
    failure_ids = [f.get("id") for f in failures if f.get("id")]
    failures_by_dim = _failures_by_dimension(failures)

    dimensions: List[Dict[str, Any]] = []
    for key in _EVALUATIVE_DIMENSIONS:
        dimension = _resolve_evaluative_dimension(
            key, evaluations, run_id, failures_by_dim.get(key, [])
        )
        dimensions.append(dimension)
    dimensions.append(_tool_discipline(run, run_id, failures_by_dim.get("tool_discipline", [])))
    for key in _JUDGE_ONLY_DIMENSIONS:
        dimensions.append(
            _resolve_evaluative_dimension(
                key, evaluations, run_id, failures_by_dim.get(key, [])
            )
        )
    dimensions.append(_research_breadth(run, run_id, failures_by_dim.get("research_breadth", [])))
    dimensions.append(_latency(run, run_id, failures_by_dim.get("latency", [])))
    dimensions.append(_token_use(run, run_id, failures_by_dim.get("token_use", [])))
    dimensions.append(_cost(run, run_id, failures_by_dim.get("cost", [])))
    dimensions.append(
        _runtime_reliability(run, run_id, failures_by_dim.get("runtime_reliability", []))
    )
    dimensions.append(_drift_dimension(run_id, failures, evaluations))
    for key in ("policy_compliance", "invariant_compliance", "context_usage"):
        dimensions.append(
            _agent_pass_rate_dimension(
                key, evaluations, run_id, failures_by_dim.get(key, [])
            )
        )
    dimensions.append(
        _task_completion_dimension(
            evaluations, run_id, failures_by_dim.get("task_completion", [])
        )
    )

    # Preserve the canonical dimension order.
    dimensions.sort(key=lambda d: DIMENSION_ORDER.index(d["key"]))
    dimension_map = {d["key"]: d for d in dimensions}

    not_evaluated_evidence = [
        {
            "evaluation_id": evaluation.get("id"),
            "run_id": run_id,
            "outcome": _eval_outcome(evaluation),
            "reason": _eval_result(evaluation).get("reason"),
        }
        for evaluation in evaluations
        if _eval_outcome(evaluation) == "NOT_EVALUATED"
    ]

    evaluation_ids = [e.get("id") for e in evaluations if e.get("id")]
    return {
        "scope": "run",
        "run": _run_view(run),
        "run_id": run_id,
        "dimensions": dimensions,
        "dimension_map": dimension_map,
        "evaluations": [_evaluation_view(e) for e in evaluations],
        "evaluation_outcomes": _outcome_counts(evaluations),
        "not_evaluated_evidence": not_evaluated_evidence,
        "composite": _composite(dimensions, policy),
        "evidence": {
            "run_id": run_id,
            "evaluation_ids": evaluation_ids,
            "failure_ids": failure_ids,
        },
        "builder_version": SCORECARD_BUILDER_VERSION,
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "created_at": now_iso(),
    }


# ------------------------------------------------------------------ experiment scorecard
def _aggregate_run_scorecards(scorecards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    aggregated: List[Dict[str, Any]] = []
    for key in DIMENSION_ORDER:
        values: List[float] = []
        runs: List[Dict[str, Any]] = []
        for scorecard in scorecards:
            dimension = (scorecard.get("dimension_map") or {}).get(key) or {}
            state = dimension.get("state") or _STATE_NOT_AVAILABLE
            value = _numeric(dimension.get("value"))
            if state == _STATE_AVAILABLE and value is not None:
                values.append(value)
            runs.append({
                "run_id": scorecard.get("run_id"),
                "value": value,
                "state": state,
            })
        missing_count = sum(
            1 for entry in runs if entry["state"] != _STATE_AVAILABLE or entry["value"] is None
        )
        aggregated.append({
            "key": key,
            "label": DIMENSIONS[key]["label"],
            "unit": DIMENSIONS[key]["unit"],
            "direction": DIMENSION_DIRECTIONS[key],
            "source": DIMENSIONS[key]["source"],
            "value": _mean(values),
            "state": _STATE_AVAILABLE if values else _STATE_NOT_AVAILABLE,
            "sample_size": len(values),
            "missing_count": missing_count,
            "runs": runs,
        })
    return aggregated


def _dimension_map_of(dimensions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {d["key"]: d for d in dimensions}


async def _scorecards_for_runs(owner_id: str, runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scorecards: List[Dict[str, Any]] = []
    for run in runs:
        run_id = run.get("id")
        if not run_id:
            continue
        scorecards.append(await build_run_scorecard(owner_id, run_id))
    return scorecards


async def _experiment_runs(
    owner_id: str, experiment_id: str, configuration_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    query = {
        "owner_id": owner_id,
        "experiment_id": experiment_id,
        "execution_kind": "benchmark",
    }
    if configuration_id is not None:
        query["configuration_id"] = configuration_id
    return await repo.runs.list(
        query, sort_field="created_at", descending=False, limit=10000
    )


def _no_policy_composite(dimensions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return _composite(dimensions, None)


async def build_experiment_scorecard(
    owner_id: str,
    experiment_id: str,
    configuration_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate per-configuration run scorecards for an owner-scoped experiment."""
    experiment = await repo.experiments.get(experiment_id, owner_id)
    if not experiment:
        raise ScorecardNotFound("Experiment not found")

    configs = await experiment_service.get_configurations(owner_id, experiment_id)
    config_by_id = {config["id"]: config for config in configs}
    if configuration_id is not None and configuration_id not in config_by_id:
        raise ScorecardNotFound("Configuration not found")

    runs = await _experiment_runs(owner_id, experiment_id, configuration_id)
    runs_by_config: Dict[Optional[str], List[Dict[str, Any]]] = {}
    for run in runs:
        runs_by_config.setdefault(run.get("configuration_id"), []).append(run)

    configuration_blocks: List[Dict[str, Any]] = []
    selected_configs = (
        [config_by_id[configuration_id]] if configuration_id is not None else configs
    )
    for config in selected_configs:
        config_runs = runs_by_config.get(config["id"], [])
        scorecards = await _scorecards_for_runs(owner_id, config_runs)
        dimensions = _aggregate_run_scorecards(scorecards)
        configuration_blocks.append({
            "configuration_id": config["id"],
            "label": config.get("label"),
            "model_id": config.get("model_id"),
            "run_count": len(config_runs),
            "dimensions": dimensions,
            "dimension_map": _dimension_map_of(dimensions),
        })

    overall_scorecards = await _scorecards_for_runs(owner_id, runs)
    overall_dimensions = _aggregate_run_scorecards(overall_scorecards)

    return {
        "scope": "experiment",
        "experiment": _experiment_view(experiment),
        "experiment_id": experiment_id,
        "configuration_id": configuration_id,
        "configurations": configuration_blocks,
        "overall": {
            "run_count": len(runs),
            "configuration_count": len(selected_configs),
            "dimensions": overall_dimensions,
            "dimension_map": _dimension_map_of(overall_dimensions),
        },
        "composite": _no_policy_composite(overall_dimensions),
        "builder_version": SCORECARD_BUILDER_VERSION,
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "created_at": now_iso(),
    }


# ------------------------------------------------------------------ comparison
def _direction_for(key: str) -> str:
    metric = _DIMENSION_TO_REGRESSION_METRIC.get(key)
    if metric and metric in regression.METRIC_DIRECTIONS:
        return regression.METRIC_DIRECTIONS[metric]
    return DIMENSION_DIRECTIONS[key]


def _classify(baseline: Optional[float], candidate: Optional[float], direction: str) -> Tuple[Optional[float], str]:
    if baseline is None or candidate is None:
        return None, "unknown"
    delta = round(candidate - baseline, 6)
    if delta == 0:
        return delta, "unchanged"
    improved = (delta > 0) == (direction == "higher_is_better")
    return delta, ("improved" if improved else "degraded")


async def build_comparison_scorecard(
    owner_id: str,
    experiment_id: str,
    baseline_configuration_id: str,
    candidate_configuration_id: str,
) -> Dict[str, Any]:
    """Per-dimension baseline/candidate comparison for two owned configurations."""
    experiment = await repo.experiments.get(experiment_id, owner_id)
    if not experiment:
        raise ScorecardNotFound("Experiment not found")

    configs = await experiment_service.get_configurations(owner_id, experiment_id)
    config_by_id = {config["id"]: config for config in configs}
    for configuration_id in (baseline_configuration_id, candidate_configuration_id):
        if configuration_id not in config_by_id:
            raise ScorecardNotFound("Configuration not found")
    if baseline_configuration_id == candidate_configuration_id:
        raise ScorecardError("Baseline and candidate configurations must differ")

    baseline_runs = await _experiment_runs(owner_id, experiment_id, baseline_configuration_id)
    candidate_runs = await _experiment_runs(owner_id, experiment_id, candidate_configuration_id)
    baseline_dims = _dimension_map_of(
        _aggregate_run_scorecards(await _scorecards_for_runs(owner_id, baseline_runs))
    )
    candidate_dims = _dimension_map_of(
        _aggregate_run_scorecards(await _scorecards_for_runs(owner_id, candidate_runs))
    )

    rows: List[Dict[str, Any]] = []
    counts = {"improved": 0, "degraded": 0, "unchanged": 0, "unknown": 0}
    for key in DIMENSION_ORDER:
        baseline_dim = baseline_dims.get(key) or {}
        candidate_dim = candidate_dims.get(key) or {}
        baseline_value = _numeric(baseline_dim.get("value"))
        candidate_value = _numeric(candidate_dim.get("value"))
        direction = _direction_for(key)
        delta, classification = _classify(baseline_value, candidate_value, direction)
        counts[classification] += 1
        rows.append({
            "key": key,
            "label": DIMENSIONS[key]["label"],
            "unit": DIMENSIONS[key]["unit"],
            "direction": direction,
            "baseline": baseline_value,
            "candidate": candidate_value,
            "delta": delta,
            "classification": classification,
            "baseline_state": baseline_dim.get("state"),
            "candidate_state": candidate_dim.get("state"),
            "significance": None,
            "significance_note": "no statistical significance claim",
        })

    return {
        "scope": "comparison",
        "experiment": _experiment_view(experiment),
        "experiment_id": experiment_id,
        "baseline_configuration_id": baseline_configuration_id,
        "candidate_configuration_id": candidate_configuration_id,
        "baseline": {
            "configuration_id": baseline_configuration_id,
            "label": (config_by_id.get(baseline_configuration_id) or {}).get("label"),
            "run_count": len(baseline_runs),
            "dimensions": list(baseline_dims.values()),
            "dimension_map": baseline_dims,
        },
        "candidate": {
            "configuration_id": candidate_configuration_id,
            "label": (config_by_id.get(candidate_configuration_id) or {}).get("label"),
            "run_count": len(candidate_runs),
            "dimensions": list(candidate_dims.values()),
            "dimension_map": candidate_dims,
        },
        "dimensions": rows,
        "dimension_map": {row["key"]: row for row in rows},
        "summary": counts,
        "significance": None,
        "significance_note": "no statistical significance claim",
        "composite": _no_policy_composite(list(baseline_dims.values())),
        "builder_version": SCORECARD_BUILDER_VERSION,
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "created_at": now_iso(),
    }


# ------------------------------------------------------------------ persistence
async def _record_scorecard_span(
    owner_id: str,
    scorecard: Dict[str, Any],
    run: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Best-effort SCORECARD_BUILD span. Never raises, never blocks a scorecard."""
    scope = scorecard.get("scope")
    project_id = scorecard.get("project_id")
    experiment_id = scorecard.get("experiment_id")
    metadata = redaction.safe_metadata({
        "kind": "scorecard",
        "scorecard_id": scorecard.get("id"),
        "scope": scope,
        "dimension_count": len(scorecard.get("dimensions") or []),
        "composite_available": (scorecard.get("composite") or {}).get("available"),
    })
    try:
        started = time.perf_counter()
        trace_id = None
        attached = False
        if scope == "run" and run is not None:
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
                project_id=project_id,
                experiment_id=experiment_id,
                metadata=metadata,
            )
            trace_id = trace["id"]
        span = await tracing.start_span(
            owner_id,
            trace_id,
            "SCORECARD_BUILD",
            "Scorecard build",
            run_id=run.get("id") if run else None,
            metadata=metadata,
        )
        await tracing.finish_span(span, status="completed", started_perf=started)
        if not attached:
            await tracing.finish_trace(trace, status="completed", started_perf=started)
        return trace_id
    except Exception:  # pragma: no cover - telemetry is best-effort
        return None


async def create_scorecard(owner_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build, persist and trace a scorecard for a body-like build request."""
    body = dict(body or {})
    scope = body.get("scope") or "run"
    project_id = body.get("project_id")
    if not project_id:
        raise ScorecardError("project_id is required")
    project = await repo.projects.get(project_id, owner_id)
    if not project:
        raise ScorecardNotFound("Project not found")

    policy = body.get("scoring_policy")
    run: Optional[Dict[str, Any]] = None
    experiment_id: Optional[str] = None

    if scope == "run":
        run_id = body.get("run_id")
        if not run_id:
            raise ScorecardError("run_id is required for a run scorecard")
        run = await repo.runs.get(run_id, owner_id)
        if not run:
            raise ScorecardNotFound("Run not found")
        if run.get("project_id") and run.get("project_id") != project_id:
            raise ScorecardError("Run belongs to a different project")
        experiment_id = run.get("experiment_id")
        scorecard = await build_run_scorecard(owner_id, run_id, policy=policy)
    elif scope == "experiment":
        experiment_id = body.get("experiment_id")
        if not experiment_id:
            raise ScorecardError("experiment_id is required for an experiment scorecard")
        experiment = await repo.experiments.get(experiment_id, owner_id)
        if not experiment:
            raise ScorecardNotFound("Experiment not found")
        if experiment.get("project_id") and experiment.get("project_id") != project_id:
            raise ScorecardError("Experiment belongs to a different project")
        scorecard = await build_experiment_scorecard(
            owner_id, experiment_id, body.get("configuration_id")
        )
    elif scope == "comparison":
        experiment_id = body.get("experiment_id")
        baseline = body.get("baseline_configuration_id")
        candidate = body.get("candidate_configuration_id")
        if not experiment_id or not baseline or not candidate:
            raise ScorecardError(
                "experiment_id, baseline_configuration_id and candidate_configuration_id "
                "are required for a comparison scorecard"
            )
        experiment = await repo.experiments.get(experiment_id, owner_id)
        if not experiment:
            raise ScorecardNotFound("Experiment not found")
        if experiment.get("project_id") and experiment.get("project_id") != project_id:
            raise ScorecardError("Experiment belongs to a different project")
        scorecard = await build_comparison_scorecard(
            owner_id, experiment_id, baseline, candidate
        )
    else:
        raise ScorecardError(f"Unsupported scorecard scope: {scope}")

    scorecard.update({
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "scope": scope,
        "notes": body.get("notes") or "",
        "metadata": redaction.safe_metadata(body.get("metadata")),
        "created_at": now_iso(),
    })
    if experiment_id:
        scorecard["experiment_id"] = experiment_id

    await repo.scorecards.insert(scorecard)

    trace_id = await _record_scorecard_span(owner_id, scorecard, run)
    if trace_id:
        scorecard["trace_id"] = trace_id
        try:
            await repo.scorecards.update(scorecard["id"], owner_id, {"trace_id": trace_id})
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass
    return scorecard


async def get_scorecard(owner_id: str, scorecard_id: str) -> Dict[str, Any]:
    scorecard = await repo.scorecards.get(scorecard_id, owner_id)
    if not scorecard:
        raise ScorecardNotFound("Scorecard not found")
    return scorecard


async def list_scorecards(
    owner_id: str,
    project_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    query: Dict[str, Any] = {"owner_id": owner_id}
    if project_id is not None:
        if not await repo.projects.get(project_id, owner_id):
            raise ScorecardNotFound("Project not found")
        query["project_id"] = project_id
    if experiment_id is not None:
        if not await repo.experiments.get(experiment_id, owner_id):
            raise ScorecardNotFound("Experiment not found")
        query["experiment_id"] = experiment_id
    if run_id is not None:
        if not await repo.runs.get(run_id, owner_id):
            raise ScorecardNotFound("Run not found")
        query["run_id"] = run_id

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    total = await repo.scorecards.count(query)
    items = await repo.scorecards.list(
        query, sort_field="created_at", descending=True, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}
