"""Case-level evaluation, truthful aggregation, regression comparison and
findings derived from benchmark results.

Every value that cannot be known stays ``None``: unknown token counts, unknown
costs and absent scores are never coerced to zero. Aggregation reads the
collections directly (read-only, owner + experiment scoped) so the sums are
exact; writes still go through the repository abstraction.
"""
import json
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..database import db
from ..errors import error_message
from ..models import (
    DETERMINISTIC_EVALUATOR_KEYS,
    EVALUATION_TYPES,
    EVALUATOR_VERSION,
    LEGACY_EVALUATOR_ALIASES,
    V5_SCHEMA_VERSION,
    new_id,
    now_iso,
)
from . import experiments as experiment_service
from . import redaction
from . import tracing
from .execution import execute_model


class EvaluationError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


# Legacy tuple retained for callers that still reference it.
EVALUATOR_TYPES = ("deterministic", "human", "llm_judge")
DETERMINISTIC_METHODS = DETERMINISTIC_EVALUATOR_KEYS
_LEGACY_EVAL_TYPE = {
    "deterministic": "deterministic",
    "human": "human",
    "rule": "rule",
    "judge_model": "ai_judge",
}

# ------------------------------------------------------------------ primitives


def _strip_json(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        value = value.split("```", 2)[1] if value.count("```") >= 2 else value.strip("`")
        if value.startswith("json"):
            value = value[4:]
    return value.strip()


def _normalize_text(value: Optional[str]) -> str:
    return " ".join((value or "").split()).strip().casefold()


def _tokenize(value: Optional[str]) -> List[str]:
    return re.findall(r"[a-z0-9]+", (value or "").casefold())


def _token_overlap(expected: str, candidate: str) -> float:
    exp = set(_tokenize(expected))
    cand = set(_tokenize(candidate))
    if not exp and not cand:
        return 1.0
    union = exp | cand
    if not union:
        return 1.0
    return round(len(exp & cand) / len(union), 6)


def _normalize_score(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    if 0.0 <= number <= 1.0:
        return number
    if 1.0 < number <= 100.0:
        return number / 100.0
    return None


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(percentile * len(ordered))
    rank = max(1, min(rank, len(ordered)))
    return ordered[rank - 1]


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _delta(baseline: Optional[float], candidate: Optional[float]) -> Optional[float]:
    if baseline is None or candidate is None:
        return None
    return round(candidate - baseline, 6)


# ------------------------------------------------------------------ deterministic evaluation
def _deterministic_score(
    case: Dict[str, Any], candidate: str, method: str
) -> Tuple[Optional[float], Optional[bool], Dict[str, Any]]:
    expected = case.get("expected_output") or ""
    metadata = case.get("metadata") or {}

    if method == "exact_match":
        if not expected:
            return None, None, {"reason": "missing_expected_output"}
        matched = _normalize_text(candidate) == _normalize_text(expected)
        return (1.0 if matched else 0.0), matched, {"expected_normalized": _normalize_text(expected)}

    if method == "contains":
        if not expected:
            return None, None, {"reason": "missing_expected_output"}
        matched = _normalize_text(expected) in _normalize_text(candidate)
        return (1.0 if matched else 0.0), matched, {"expected_normalized": _normalize_text(expected)}

    if method == "regex":
        pattern = metadata.get("pattern") or expected
        if not pattern:
            return None, None, {"reason": "missing_regex_pattern"}
        try:
            matched = re.search(pattern, candidate) is not None
        except re.error as exc:
            raise EvaluationError(f"Invalid regex pattern: {exc}")
        return (1.0 if matched else 0.0), matched, {"pattern": pattern}

    if method == "token_overlap":
        if not expected:
            return None, None, {"reason": "missing_expected_output"}
        score = _token_overlap(expected, candidate)
        return score, score >= 0.5, {}

    raise EvaluationError(f"Unsupported deterministic method: {method}")


def _judge_prompt(
    case: Optional[Dict[str, Any]],
    expected: Optional[str],
    candidate: str,
    rubric: Optional[Dict[str, Any]],
    run: Dict[str, Any],
) -> str:
    sections = [
        "You are scoring a single model response against a dataset case.",
        "Respond ONLY with valid JSON of the shape "
        '{"score": <0..1 or 0..100>, "rationale": "<short>"}.',
    ]
    if case and case.get("input_text"):
        sections.append(f"CASE INPUT:\n{case['input_text']}")
    if expected:
        sections.append(f"EXPECTED OUTPUT:\n{expected}")
    sections.append(f"CANDIDATE OUTPUT:\n{candidate}")
    if rubric and rubric.get("dimensions"):
        lines = [
            f"- {d.get('key')} ({d.get('label')}, 0-{d.get('max_score', 10)}): "
            f"{d.get('description', '')}"
            for d in rubric["dimensions"]
        ]
        sections.append("RUBRIC DIMENSIONS:\n" + "\n".join(lines))
    if run.get("model_name"):
        sections.append(f"CANDIDATE MODEL: {run['model_name']}")
    return redaction.redact_text("\n\n".join(sections))


# ------------------------------------------------------------------ evaluate
def _resolve_evaluator_type(body: Dict[str, Any]) -> str:
    raw = (
        body.get("evaluation_type")
        or body.get("evaluator_type")
        or body.get("eval_type")
        or "deterministic"
    )
    if not isinstance(raw, str) or not raw.strip():
        raw = "deterministic"
    raw = raw.strip()
    if raw in EVALUATION_TYPES:
        return raw
    canonical = LEGACY_EVALUATOR_ALIASES.get(raw)
    if canonical is None:
        raise EvaluationError(f"Unsupported evaluator_type: {raw}")
    return canonical


async def _resolve_case(owner_id: str, run: Dict[str, Any]) -> Dict[str, Any]:
    case_id = run.get("dataset_case_id")
    if not case_id:
        return {}
    case = await repo.dataset_version_cases.get(case_id, owner_id)
    case = case if isinstance(case, dict) else {}
    case_project = case.get("project_id")
    run_project = run.get("project_id")
    if case_project and run_project and case_project != run_project:
        raise EvaluationError("Dataset case belongs to a different project")
    return case


def _blank_evaluator_result() -> Dict[str, Any]:
    return {
        "status": "not_evaluated",
        "passed": None,
        "score": None,
        "threshold": None,
        "confidence": None,
        "metrics": {},
        "findings": [],
        "scores": {},
        "details": {},
        "error": None,
        "reason": None,
    }


def _normalize_evaluator_result(raw: Any) -> Dict[str, Any]:
    result = _blank_evaluator_result()
    if isinstance(raw, dict):
        result.update({
            "status": raw.get("status") or "not_evaluated",
            "passed": raw.get("passed"),
            "score": raw.get("score"),
            "threshold": raw.get("threshold"),
            "confidence": raw.get("confidence"),
            "metrics": raw.get("metrics") or {},
            "findings": list(raw.get("findings") or []),
            "scores": raw.get("scores") or {},
            "details": raw.get("details") or {},
            "error": raw.get("error"),
            "reason": raw.get("reason"),
        })
    return result


def _evidence_subset(evidence: Dict[str, Any]) -> Dict[str, Any]:
    from .evidence import marker_validity

    tool_calls = evidence.get("tool_calls") or []
    tool_call_ids = [
        call.get("id")
        for call in tool_calls
        if isinstance(call, dict) and call.get("id")
    ]
    validity = marker_validity(
        list(evidence.get("knowledge_markers") or []),
        evidence.get("knowledge_provenance") or {},
    )
    return redaction.redact({
        "run_id": evidence.get("run_id"),
        "experiment_id": evidence.get("experiment_id"),
        "project_id": evidence.get("project_id"),
        "dataset_case_id": evidence.get("dataset_case_id"),
        "dataset_version_id": evidence.get("dataset_version_id"),
        "trace_id": evidence.get("trace_id"),
        "context_snapshot_id": evidence.get("context_snapshot_id"),
        # Additive (V6.7): agent provenance summary for agent-run evaluations.
        "agent_id": evidence.get("agent_id"),
        "agent_version_id": evidence.get("agent_version_id"),
        "compiled_prompt_id": evidence.get("compiled_prompt_id"),
        "permission_denial_count": len(evidence.get("permission_denials") or []),
        "tool_call_ids": tool_call_ids,
        "tool_call_count": evidence.get("tool_call_count"),
        "tool_failure_count": evidence.get("tool_failure_count"),
        "tool_names": list(evidence.get("tool_names") or []),
        "knowledge_chunk_ids": list(evidence.get("knowledge_chunk_ids") or []),
        "knowledge_marker_labels": list(evidence.get("knowledge_marker_labels") or []),
        "marker_validity": validity,
        "candidate_available": evidence.get("candidate_available"),
        "knowledge_retrieval_count": evidence.get("knowledge_retrieval_count"),
        "knowledge_injection": evidence.get("knowledge_injection"),
        "latency_ms": evidence.get("latency_ms"),
        "ttft_ms": evidence.get("ttft_ms"),
        "input_tokens": evidence.get("input_tokens"),
        "output_tokens": evidence.get("output_tokens"),
        "estimated_cost": evidence.get("estimated_cost"),
        "cost_available": evidence.get("cost_available"),
    })


async def evaluate_run(
    owner_id: str,
    experiment: Optional[Dict[str, Any]],
    body: Dict[str, Any],
) -> Dict[str, Any]:
    from . import evaluators as evaluator_engine
    from . import evidence as evidence_service

    run = await repo.runs.get(body.get("run_id"), owner_id)
    if not run:
        raise EvaluationError("Run not found")

    if experiment is not None:
        if run.get("experiment_id") != experiment.get("id"):
            raise EvaluationError("Run does not belong to this experiment")
        experiment_project = experiment.get("project_id")
        if run.get("project_id") not in (None, experiment_project):
            raise EvaluationError("Run does not belong to this experiment's project")
        project_id = (
            experiment_project if experiment_project is not None else run.get("project_id")
        )
        experiment_id = experiment.get("id")
    else:
        project_id = run.get("project_id")
        experiment_id = run.get("experiment_id")

    canonical = _resolve_evaluator_type(body)
    case = await _resolve_case(owner_id, run)
    evidence = await evidence_service.collect_run_evidence(owner_id, run)

    now = now_iso()
    evaluation: Dict[str, Any] = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "experiment_id": experiment_id,
        "run_id": run["id"],
        "dataset_case_id": run.get("dataset_case_id"),
        "configuration_id": run.get("configuration_id"),
        "repetition_index": run.get("repetition_index"),
        "evaluator_type": canonical,
        "eval_type": _LEGACY_EVAL_TYPE.get(canonical, canonical),
        "method": body.get("method"),
        "rubric_id": body.get("rubric_id"),
        "rubric_snapshot": None,
        "judge_model_id": body.get("judge_model_id"),
        "judge_model_snapshot": None,
        "judge_run_id": None,
        "raw_result": None,
        "parsed_score": None,
        "status": "completed",
        "error": None,
        "error_message": None,
        "notes": body.get("notes") or "",
        "usage": {
            "input_tokens": None,
            "output_tokens": None,
            "cost": None,
            "cost_available": False,
        },
        "created_at": now,
        "schema_version": V5_SCHEMA_VERSION,
        "evaluation_type": canonical,
        "evaluator": {
            "type": canonical,
            "key": canonical,
            "implementation": "app.services.evaluation",
            "version": EVALUATOR_VERSION,
        },
        "rubric": None,
        "judge": None,
        "evidence": _evidence_subset(evidence),
        "scores": {},
        "findings": [],
        "metrics": {},
        "result": {
            "outcome": "NOT_EVALUATED",
            "status": "not_evaluated",
            "passed": None,
            "score": None,
            "threshold": None,
            "confidence": None,
            "reason": None,
        },
        "provenance": {},
    }

    evaluator_key = canonical
    implementation = "app.services.evaluation"
    judge_info: Optional[Dict[str, Any]] = None
    rubric_info: Optional[Dict[str, Any]] = None
    evaluator_result = _blank_evaluator_result()

    try:
        if canonical == "deterministic":
            method = body.get("method") or body.get("evaluator_key") or "exact_match"
            if isinstance(method, str):
                method = method.strip()
            if method not in DETERMINISTIC_EVALUATOR_KEYS:
                raise EvaluationError(f"Unsupported deterministic method: {method}")
            evaluator_key = method
            implementation = "app.services.evaluators"
            evaluation["method"] = method
            run_ok = run.get("status") == "success"
            candidate_available = bool(evidence.get("candidate_available"))
            if not run_ok or not candidate_available:
                reason = "run_failed" if not run_ok else "no_output"
                evaluator_result = _blank_evaluator_result()
                evaluator_result["reason"] = reason
                evaluation["raw_result"] = {
                    "method": method,
                    "computable": False,
                    "reason": reason,
                    "run_status": run.get("status"),
                }
            else:
                raw = evaluator_engine.run_deterministic(case, evidence, method)
                evaluator_result = _normalize_evaluator_result(raw)
                evaluation["raw_result"] = {
                    "method": method,
                    "computable": raw.get("score") is not None,
                    "passed": raw.get("passed"),
                    "expected_output": case.get("expected_output") or "",
                    "candidate_output": evidence.get("candidate"),
                    **(raw.get("details") or {}),
                }
        elif canonical == "rule":
            keys = (
                body.get("evaluator_keys")
                or ([body.get("evaluator_key")] if body.get("evaluator_key") else [])
                or None
            )
            evaluator_key = body.get("evaluator_key") or "rule"
            implementation = "app.services.evaluators"
            evaluation["method"] = "rule"
            raw = evaluator_engine.run_rule_evaluators(case, evidence, keys)
            evaluator_result = _normalize_evaluator_result(raw)
            evaluation["raw_result"] = raw if isinstance(raw, dict) else None
            if evaluator_result["status"] == "error":
                evaluation["error"] = evaluator_result.get("error")
                evaluation["error_message"] = evaluator_result.get("error")
        elif canonical == "judge_model":
            evaluator_key = "judge_model"
            implementation = "app.services.judge_system"
            evaluation["method"] = "judge_model"
            (
                evaluator_result,
                judge_info,
                rubric_info,
            ) = await _run_judge_model(
                owner_id, experiment, run, case, evidence, body, evaluation
            )
        else:
            evaluator_key = "human"
            implementation = "app.services.evaluation"
            evaluator_result, raw_result = _human_evaluation(body)
            evaluation["method"] = "human"
            evaluation["raw_result"] = raw_result
    except EvaluationError:
        raise
    except Exception as exc:  # defensive: an evaluator must never 500
        evaluator_result = _blank_evaluator_result()
        evaluator_result.update({
            "status": "error",
            "error": f"evaluator_exception:{exc}",
            "reason": f"evaluator_exception:{exc}",
        })
        evaluation["error"] = evaluator_result["error"]
        evaluation["error_message"] = evaluator_result["error"]

    evaluation["evaluator"]["key"] = evaluator_key
    evaluation["evaluator"]["implementation"] = implementation

    error_state = (
        evaluator_result.get("status") == "error"
        or evaluation.get("status") == "error"
    )
    run_ok = run.get("status") == "success"
    candidate_available = bool(evidence.get("candidate_available"))

    if error_state:
        outcome = "ERROR"
        status = "error"
        passed: Optional[bool] = None
        score: Optional[float] = None
        reason = (
            evaluation.get("error")
            or evaluator_result.get("error")
            or evaluator_result.get("reason")
        )
    elif canonical != "human" and (not run_ok or not candidate_available):
        outcome = "NOT_EVALUATED"
        status = "not_evaluated"
        passed = None
        score = None
        reason = "run_failed" if not run_ok else "no_candidate"
    elif evaluator_result.get("status") == "not_evaluated":
        outcome = "NOT_EVALUATED"
        status = "not_evaluated"
        passed = None
        score = evaluator_result.get("score")
        reason = evaluator_result.get("reason")
    elif evaluator_result.get("passed") is True:
        outcome = "PASS"
        status = "completed"
        passed = True
        score = evaluator_result.get("score")
        reason = None
    elif evaluator_result.get("passed") is False:
        outcome = "FAIL"
        status = "completed"
        passed = False
        score = evaluator_result.get("score")
        reason = evaluator_result.get("reason")
    else:
        outcome = "NOT_EVALUATED"
        status = "not_evaluated"
        passed = None
        score = evaluator_result.get("score")
        reason = evaluator_result.get("reason")

    findings = list(evaluator_result.get("findings") or [])
    if outcome == "FAIL" and not findings and canonical in (
        "deterministic",
        "rule",
        "judge_model",
    ):
        findings = [{
            "category": "correctness",
            "severity": "medium",
            "message": str(reason or f"{evaluator_key}_evaluation_failed"),
            "evidence": {},
        }]

    evaluation["status"] = status
    evaluation["parsed_score"] = None if error_state else score
    evaluation["scores"] = evaluator_result.get("scores") or {}
    evaluation["metrics"] = evaluator_result.get("metrics") or {}
    evaluation["findings"] = findings
    evaluation["result"] = {
        "outcome": outcome,
        "status": status,
        "passed": passed,
        "score": score,
        "threshold": evaluator_result.get("threshold"),
        "confidence": evaluator_result.get("confidence"),
        "reason": reason,
    }

    if canonical == "judge_model" and judge_info is not None:
        evaluation["judge"] = {
            "provider_id": judge_info.get("provider_id"),
            "provider_name": judge_info.get("provider_name"),
            "model_id": judge_info.get("model_id"),
            "model_name": judge_info.get("model_name"),
            "model_identifier": judge_info.get("model_identifier"),
            "family": judge_info.get("family"),
            "run_id": judge_info.get("run_id") or evaluation.get("judge_run_id"),
            "tools_enabled": False,
            "rubric_id": (rubric_info or {}).get("id"),
            "rubric_version": (rubric_info or {}).get("version"),
            "input_hash": judge_info.get("input_hash"),
        }
    evaluation["rubric"] = rubric_info
    # Additive (V6.7): snapshot the agent provenance actually used so an
    # agent-run evaluation is reproducible after the version changes. Only set
    # when the source run carries agent fields; existing evaluations are
    # byte-for-byte unaffected.
    if (
        run.get("agent_id")
        or run.get("agent_version_id")
        or run.get("compiled_prompt_id")
    ):
        evaluation["agent"] = redaction.redact({
            "agent_id": run.get("agent_id"),
            "agent_version_id": run.get("agent_version_id"),
            "compiled_prompt_id": run.get("compiled_prompt_id"),
            "output_contract": run.get("output_contract"),
            "runtime_policy_applied": run.get("runtime_policy_applied"),
        })
    evaluation["provenance"] = {
        "schema_version": V5_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_implementation": implementation,
        "rubric_id": (rubric_info or {}).get("id"),
        "rubric_version": (rubric_info or {}).get("version"),
        "source_run_id": run.get("id"),
        "evaluator_type": canonical,
        "created_at": now,
        # Additive (V6.7): agent/context provenance links (None for non-agent).
        "agent_id": run.get("agent_id"),
        "agent_version_id": run.get("agent_version_id"),
        "compiled_prompt_id": run.get("compiled_prompt_id"),
        "context_snapshot_id": run.get("context_snapshot_id"),
    }

    await repo.evaluations.insert(evaluation)
    await record_evaluation_span(
        owner_id,
        run,
        evaluation,
        span_type="JUDGE_EVALUATION" if canonical == "judge_model" else "EVALUATION",
    )
    if body.get("record_failures"):
        await _record_findings_as_failures(owner_id, evaluation)
    return evaluation


async def _run_judge_model(
    owner_id: str,
    experiment: Optional[Dict[str, Any]],
    run: Dict[str, Any],
    case: Dict[str, Any],
    evidence: Dict[str, Any],
    body: Dict[str, Any],
    evaluation: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    from . import judge_system as judge_engine

    protocol = (experiment or {}).get("protocol")
    protocol = protocol if isinstance(protocol, dict) else {}
    eval_spec = protocol.get("evaluation")
    eval_spec = eval_spec if isinstance(eval_spec, dict) else {}

    rubric_id = (
        body.get("rubric_id")
        or protocol.get("judge_rubric_id")
        or protocol.get("rubric_id")
        or eval_spec.get("rubric_id")
    )
    judge_model_id = (
        body.get("judge_model_id")
        or protocol.get("judge_model_id")
        or eval_spec.get("judge_model_id")
    )
    if not judge_model_id:
        raise EvaluationError("judge_model_id is required for judge_model")

    threshold = body.get("threshold")
    if threshold is None:
        threshold = eval_spec.get("threshold")
    if threshold is None:
        threshold = protocol.get("threshold")

    evaluation["judge_model_id"] = judge_model_id

    rubric = None
    rubric_info: Optional[Dict[str, Any]] = None
    if rubric_id:
        rubric = await repo.rubrics.get(rubric_id, owner_id)
        if not rubric:
            raise EvaluationError("Rubric not found")
        if threshold is None:
            threshold = rubric.get("threshold")
        evaluation["rubric_id"] = rubric.get("id")
        evaluation["rubric_snapshot"] = rubric
        rubric_info = {
            "id": rubric.get("id"),
            "version": rubric.get("version") or 1,
            "threshold": threshold,
            "snapshot": rubric,
        }

    if rubric is not None:
        judge_experiment = experiment if experiment is not None else {
            "id": run.get("experiment_id"),
            "project_id": run.get("project_id"),
        }
        options = {"case_input": case.get("input_text")} if case.get("input_text") else None
        judged = await judge_engine.evaluate_with_judge(
            owner_id,
            judge_experiment,
            run,
            judge_model_id=judge_model_id,
            rubric=rubric,
            threshold=threshold,
            options=options,
        )
        judge_info = dict(judged.get("judge") or {})
        judge_info["tools_enabled"] = False
        judge_info["run_id"] = judge_info.get("run_id") or judged.get("judge_run_id")
        evaluation["judge_run_id"] = judged.get("judge_run_id")
        evaluation["judge_model_snapshot"] = {
            "model_id": judge_info.get("model_id"),
            "model_name": judge_info.get("model_name"),
            "model_identifier": judge_info.get("model_identifier"),
            "provider_id": judge_info.get("provider_id"),
            "provider_name": judge_info.get("provider_name"),
            "family": judge_info.get("family"),
        }
        evaluation["usage"] = judged.get("usage") or evaluation["usage"]
        evaluation["raw_result"] = {
            "method": "judge_model",
            "judge_response": judged.get("raw_result"),
            "judge_input": judged.get("judge_input"),
        }
        if judged.get("status") == "error":
            evaluation["error"] = judged.get("error")
            evaluation["error_message"] = judged.get("error")
        return _normalize_evaluator_result(judged), judge_info, rubric_info

    evaluator_result, judge_info = await _run_legacy_judge(
        owner_id,
        experiment,
        run,
        case,
        evidence,
        body,
        evaluation,
        judge_model_id=judge_model_id,
        threshold=threshold,
    )
    return evaluator_result, judge_info, None


async def record_evaluation_span(
    owner_id: str,
    run: Dict[str, Any],
    evaluation: Dict[str, Any],
    *,
    span_name: str = "Evaluation",
    span_type: str = "EVALUATION",
) -> None:
    """Best-effort EVALUATION span on the evaluated run's trace.

    Telemetry only: a missing trace or persistence failure is a no-op and never
    affects the evaluation that was just persisted. No model/provider identity is
    recorded here.
    """
    trace_id = run.get("trace_id")
    if not trace_id:
        return
    try:
        started = time.perf_counter()
        span = await tracing.start_span(
            owner_id,
            trace_id,
            span_type,
            span_name,
            run_id=run.get("id"),
            metadata={
                "evaluation_id": evaluation.get("id"),
                "evaluator_type": (
                    evaluation.get("evaluator_type") or evaluation.get("eval_type")
                ),
            },
        )
        await tracing.finish_span(
            span,
            status="completed" if evaluation.get("status") != "error" else "error",
            started_perf=started,
        )
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass


def _human_evaluation(body: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    score = body.get("score")
    passed = body.get("passed")
    if score is None and passed is None:
        raise EvaluationError("Human evaluation requires score or passed")
    raw_scale: Optional[str] = None
    normalized: Optional[float] = None
    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError):
            raise EvaluationError("score must be numeric")
        if score < 0 or score > 100:
            raise EvaluationError("score must be between 0 and 100")
        if score <= 1:
            raw_scale = "0-1"
            normalized = score
        else:
            raw_scale = "0-100"
            normalized = score / 100.0
    else:
        normalized = 1.0 if passed else 0.0

    passed_value = bool(passed) if passed is not None else None
    result = _blank_evaluator_result()
    result.update({
        "status": "completed" if passed_value is not None else "not_evaluated",
        "passed": passed_value,
        "score": normalized,
        "scores": {"human": normalized} if normalized is not None else {},
    })
    raw_result = {
        "score": score,
        "passed": passed,
        "raw_score_scale": raw_scale,
        "notes": body.get("notes") or "",
    }
    return result, raw_result


async def _run_legacy_judge(
    owner_id: str,
    experiment: Optional[Dict[str, Any]],
    run: Dict[str, Any],
    case: Dict[str, Any],
    evidence: Dict[str, Any],
    body: Dict[str, Any],
    evaluation: Dict[str, Any],
    *,
    judge_model_id: str,
    threshold: Optional[float],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    judge_model = await repo.models.get(judge_model_id, owner_id)
    if not judge_model:
        raise EvaluationError("Judge model not found")

    expected = case.get("expected_output")
    candidate = evidence.get("candidate")
    candidate = candidate if candidate is not None else ""

    system = (
        "You are a rigorous, impartial AI evaluation judge. Respond ONLY with "
        "valid JSON."
    )
    prompt = _judge_prompt(case or None, expected, candidate, None, run)
    judge_experiment_id = (experiment or {}).get("id") or run.get("experiment_id")
    judge_project_id = (experiment or {}).get("project_id") or run.get("project_id")
    judge_run = await execute_model(
        owner_id,
        judge_model_id,
        system,
        [{"role": "user", "content": prompt}],
        {"temperature": 0},
        experiment_id=judge_experiment_id,
        context="judge",
        execution_kind="judge",
        parent_run_id=run["id"],
        project_id=judge_project_id,
        enabled_tool_ids=[],
        max_tool_rounds=0,
    )
    evaluation["judge_run_id"] = judge_run["id"]
    judge_info = {
        "model_id": judge_run.get("model_id") or judge_model_id,
        "model_name": judge_run.get("model_name"),
        "model_identifier": judge_run.get("model_identifier"),
        "provider_id": judge_run.get("provider_id"),
        "provider_name": judge_run.get("provider_name"),
        "family": judge_run.get("family"),
        "run_id": judge_run.get("id"),
        "tools_enabled": False,
        "rubric_id": None,
        "rubric_version": None,
        "input_hash": None,
    }
    evaluation["judge_model_snapshot"] = {
        "model_id": judge_info["model_id"],
        "model_name": judge_info["model_name"],
        "model_identifier": judge_info["model_identifier"],
        "provider_id": judge_info["provider_id"],
        "provider_name": judge_info["provider_name"],
        "family": judge_info["family"],
        "pricing_snapshot": judge_run.get("pricing_snapshot"),
    }
    evaluation["usage"] = {
        "input_tokens": judge_run.get("input_tokens"),
        "output_tokens": judge_run.get("output_tokens"),
        "cost": judge_run.get("estimated_cost"),
        "cost_available": bool(judge_run.get("cost_available")),
    }

    parsed: Dict[str, Any] = {}
    parse_error: Optional[str] = None
    if judge_run.get("status") != "success":
        parse_error = (
            judge_run.get("error_message")
            or error_message(judge_run.get("error"))
            or "Judge execution failed"
        )
    elif judge_run.get("normalized_response"):
        try:
            parsed = json.loads(_strip_json(judge_run["normalized_response"]))
            score = parsed.get("score", parsed.get("parsed_score"))
            normalized = _normalize_score(score)
            if normalized is None:
                parse_error = "Judge response did not include a numeric score"
            else:
                evaluation["parsed_score"] = normalized
        except Exception as exc:
            parse_error = f"Judge returned unparseable JSON: {exc}"
    else:
        parse_error = "Judge returned no output"

    evaluation["raw_result"] = {
        "method": "llm_judge",
        "judge_response": parsed or judge_run.get("normalized_response"),
    }

    result = _blank_evaluator_result()
    result["threshold"] = threshold
    if parse_error:
        evaluation["status"] = "error"
        evaluation["error"] = parse_error
        evaluation["error_message"] = parse_error
        evaluation["parsed_score"] = None
        result["status"] = "error"
        result["error"] = parse_error
        result["reason"] = parse_error
    elif evaluation.get("parsed_score") is None:
        result["status"] = "not_evaluated"
        result["reason"] = "judge_score_unavailable"
    else:
        result["status"] = "completed"
        result["score"] = evaluation["parsed_score"]
        if threshold is not None:
            try:
                result["passed"] = float(evaluation["parsed_score"]) >= float(threshold)
            except (TypeError, ValueError):
                result["passed"] = None
        else:
            result["passed"] = None
    return result, judge_info


_FINDING_TO_FAILURE_CATEGORY = {
    "correctness": "WRONG_ANSWER",
    "grounding": "HALLUCINATION",
    "citation": "HALLUCINATION",
    "instruction_following": "INSTRUCTION_VIOLATION",
    "tool_selection": "TOOL_SELECTION_ERROR",
    "tool_failure": "TOOL_EXECUTION_ERROR",
    "efficiency": "EXCESSIVE_COST",
    "runtime": "EXCESSIVE_LATENCY",
    "security": "SECURITY_FAILURE",
    "data": "OTHER",
    # Additive (V6.7 Agent & Prompt Evaluation): agent/prompt finding
    # categories map onto existing quality failure categories. Security stays
    # distinct and is never inferred from these.
    "policy_compliance": "INSTRUCTION_VIOLATION",
    "invariant_compliance": "INSTRUCTION_VIOLATION",
    "context_usage": "CONTEXT_MISINTERPRETATION",
    "task_completion": "INCOMPLETE",
    "scope_adherence": "INSTRUCTION_VIOLATION",
    "role_adherence": "INSTRUCTION_VIOLATION",
    "drift": "INSTRUCTION_VIOLATION",
}


def _failure_category_for(category: Any) -> str:
    return _FINDING_TO_FAILURE_CATEGORY.get(category, "OTHER")


def _failure_severity_for(severity: Any) -> str:
    if severity in ("low", "medium", "high", "critical"):
        return severity
    if severity == "info":
        return "low"
    return "medium"


async def _record_findings_as_failures(
    owner_id: str, evaluation: Dict[str, Any]
) -> None:
    from . import failures as failure_service

    canonical = evaluation.get("evaluator_type")
    if canonical == "judge_model":
        source = "JUDGE"
    elif canonical in ("deterministic", "rule"):
        source = "RULE"
    else:
        source = "HUMAN"
    for finding in evaluation.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        try:
            message = str(finding.get("message") or "")
            await failure_service.create_failure(
                owner_id,
                evaluation.get("project_id"),
                category=_failure_category_for(finding.get("category")),
                source=source,
                severity=_failure_severity_for(finding.get("severity")),
                title=redaction.redact_text(message or "Evaluation finding"),
                description=redaction.redact_text(message),
                run_id=evaluation.get("run_id"),
                evaluation_id=evaluation.get("id"),
                dataset_case_id=evaluation.get("dataset_case_id"),
                finding_id=finding.get("id"),
                signals={"finding": finding},
                evidence=redaction.redact({
                    "finding": finding,
                    "evaluation_id": evaluation.get("id"),
                }),
            )
        except Exception:
            continue


async def list_evaluations(
    owner_id: str, experiment_id: str, limit: int = 50, offset: int = 0
) -> Dict[str, Any]:
    query = {"owner_id": owner_id, "experiment_id": experiment_id}
    total = await repo.evaluations.count(query)
    items = await repo.evaluations.list(
        query, sort_field="created_at", descending=True, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ------------------------------------------------------------------ aggregation
def _config_base(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "configuration_id": config.get("id"),
        "label": config.get("label"),
        "model_id": config.get("model_id"),
        "model_snapshot": config.get("model_snapshot"),
    }


async def _run_metrics(
    owner_id: str, experiment_id: str, configuration_id: str
) -> Dict[str, Any]:
    pipeline = [
        {"$match": {
            "owner_id": owner_id,
            "experiment_id": experiment_id,
            "execution_kind": "benchmark",
            "configuration_id": configuration_id,
        }},
        {"$group": {
            "_id": None,
            "run_count": {"$sum": 1},
            "case_ids": {"$addToSet": "$dataset_case_id"},
            "completed": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
            "failed": {"$sum": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}},
            "latencies": {"$push": "$latency_ms"},
            "input_sum": {"$sum": "$input_tokens"},
            "input_known": {"$sum": {"$cond": [{"$ne": ["$input_tokens", None]}, 1, 0]}},
            "output_sum": {"$sum": "$output_tokens"},
            "output_known": {"$sum": {"$cond": [{"$ne": ["$output_tokens", None]}, 1, 0]}},
            "cached_sum": {"$sum": "$cached_tokens"},
            "cached_known": {"$sum": {"$cond": [{"$ne": ["$cached_tokens", None]}, 1, 0]}},
            "reasoning_sum": {"$sum": "$reasoning_tokens"},
            "reasoning_known": {"$sum": {"$cond": [{"$ne": ["$reasoning_tokens", None]}, 1, 0]}},
            "known_usage_run_count": {"$sum": {"$cond": [{"$and": [
                {"$ne": ["$input_tokens", None]},
                {"$ne": ["$output_tokens", None]},
            ]}, 1, 0]}},
            "unknown_usage_run_count": {"$sum": {"$cond": [{"$and": [
                {"$ne": ["$input_tokens", None]},
                {"$ne": ["$output_tokens", None]},
            ]}, 0, 1]}},
            "known_cost_total": {"$sum": {"$cond": [
                {"$eq": ["$cost_available", True]},
                {"$ifNull": ["$estimated_cost", 0]},
                0,
            ]}},
            "known_cost_run_count": {"$sum": {"$cond": [
                {"$eq": ["$cost_available", True]}, 1, 0]}},
            "unknown_cost_run_count": {"$sum": {"$cond": [
                {"$eq": ["$cost_available", True]}, 0, 1]}},
        }},
    ]
    rows = await db.runs.aggregate(pipeline).to_list(length=1)
    doc = rows[0] if rows else {}

    latencies = sorted(
        value for value in doc.get("latencies", []) if value is not None
    )
    run_count = int(doc.get("run_count", 0) or 0)
    known_cost_run_count = int(doc.get("known_cost_run_count", 0) or 0)
    known_cost_total = doc.get("known_cost_total")
    if known_cost_run_count <= 0:
        known_cost_total = None
    case_ids = [c for c in doc.get("case_ids", []) if c is not None]

    def summed(total: Any, known: Any) -> Optional[float]:
        return total if int(known or 0) > 0 else None

    return {
        "total_cases": len(case_ids),
        "attempted_runs": run_count,
        "completed": int(doc.get("completed", 0) or 0),
        "failed": int(doc.get("failed", 0) or 0),
        "success_rate": (
            round(int(doc.get("completed", 0) or 0) / run_count, 6)
            if run_count > 0 else None
        ),
        "avg_latency_ms": round(_mean(latencies), 3) if latencies else None,
        "median_latency_ms": _median(latencies),
        "p95_latency_ms": _percentile(latencies, 0.95) if len(latencies) >= 5 else None,
        "latency_sample_count": len(latencies),
        "total_input_tokens": summed(doc.get("input_sum"), doc.get("input_known")),
        "total_output_tokens": summed(doc.get("output_sum"), doc.get("output_known")),
        "cached_tokens": summed(doc.get("cached_sum"), doc.get("cached_known")),
        "reasoning_tokens": summed(doc.get("reasoning_sum"), doc.get("reasoning_known")),
        "known_usage_run_count": int(doc.get("known_usage_run_count", 0) or 0),
        "unknown_usage_run_count": int(doc.get("unknown_usage_run_count", 0) or 0),
        "known_cost_total": known_cost_total,
        "unknown_cost_run_count": int(doc.get("unknown_cost_run_count", 0) or 0),
        "avg_known_cost_per_task": (
            round(known_cost_total / known_cost_run_count, 6)
            if known_cost_run_count > 0 and known_cost_total is not None else None
        ),
    }


async def _eval_metrics(
    owner_id: str, experiment_id: str, configuration_id: str
) -> Dict[str, Any]:
    score_pipeline = [
        {"$match": {
            "owner_id": owner_id,
            "experiment_id": experiment_id,
            "configuration_id": configuration_id,
        }},
        {"$group": {
            "_id": None,
            "run_ids": {"$addToSet": "$run_id"},
            "scores": {"$push": "$parsed_score"},
        }},
    ]
    rows = await db.evaluations.aggregate(score_pipeline).to_list(length=1)
    doc = rows[0] if rows else {}
    scores = [
        float(value) for value in doc.get("scores", [])
        if value is not None and not isinstance(value, bool)
    ]
    run_ids = [value for value in doc.get("run_ids", []) if value is not None]

    cost_pipeline = [
        {"$match": {
            "owner_id": owner_id,
            "experiment_id": experiment_id,
            "configuration_id": configuration_id,
            "evaluator_type": {"$in": ["llm_judge", "judge_model"]},
        }},
        {"$group": {
            "_id": None,
            "eval_cost_total": {"$sum": {"$cond": [
                {"$eq": [{"$ifNull": ["$usage.cost_available", False]}, True]},
                {"$ifNull": ["$usage.cost", 0]},
                0,
            ]}},
            "eval_cost_known_count": {"$sum": {"$cond": [
                {"$eq": [{"$ifNull": ["$usage.cost_available", False]}, True]}, 1, 0]}},
            "eval_cost_unknown_count": {"$sum": {"$cond": [
                {"$eq": [{"$ifNull": ["$usage.cost_available", False]}, True]}, 0, 1]}},
        }},
    ]
    cost_rows = await db.evaluations.aggregate(cost_pipeline).to_list(length=1)
    cost_doc = cost_rows[0] if cost_rows else {}
    eval_cost_known_count = int(cost_doc.get("eval_cost_known_count", 0) or 0)
    eval_cost_total = (
        cost_doc.get("eval_cost_total") if eval_cost_known_count > 0 else None
    )

    return {
        "evaluated_count": len(run_ids),
        "evaluated_run_ids": set(run_ids),
        "avg_score": round(_mean(scores), 6) if scores else None,
        "median_score": _median(scores),
        "evaluation_cost_total": eval_cost_total,
        "evaluation_cost_unknown_count": int(cost_doc.get("eval_cost_unknown_count", 0) or 0),
    }


async def aggregate_configuration(
    owner_id: str, experiment_id: str, config: Dict[str, Any]
) -> Dict[str, Any]:
    metrics = await _run_metrics(owner_id, experiment_id, config["id"])
    eval_metrics = await _eval_metrics(owner_id, experiment_id, config["id"])
    execution_cost_total = metrics["known_cost_total"]
    evaluation_cost_total = eval_metrics["evaluation_cost_total"]
    exec_unknown = metrics["unknown_cost_run_count"] > 0
    eval_unknown = eval_metrics["evaluation_cost_unknown_count"] > 0
    if exec_unknown or eval_unknown:
        total_experiment_cost = None
    elif execution_cost_total is None and evaluation_cost_total is None:
        total_experiment_cost = None
    else:
        total_experiment_cost = round(
            (execution_cost_total or 0) + (evaluation_cost_total or 0), 6
        )
    return {
        **_config_base(config),
        **metrics,
        "evaluated_count": eval_metrics["evaluated_count"],
        "avg_score": eval_metrics["avg_score"],
        "median_score": eval_metrics["median_score"],
        "evaluation_cost_total": evaluation_cost_total,
        "execution_cost_total": execution_cost_total,
        "total_experiment_cost": total_experiment_cost,
    }


async def aggregate_results(owner_id: str, experiment: Dict[str, Any]) -> Dict[str, Any]:
    configs = await experiment_service.get_configurations(owner_id, experiment["id"])
    if not configs:
        run_count = await repo.runs.count({
            "owner_id": owner_id, "experiment_id": experiment["id"]})
        return {
            "experiment_id": experiment["id"],
            "legacy": True,
            "configurations": [],
            "run_count": run_count,
            "note": "No benchmark configurations for this experiment",
        }
    results = [
        await aggregate_configuration(owner_id, experiment["id"], config)
        for config in configs
    ]
    plan = await repo.benchmark_plans.find_one(
        {"owner_id": owner_id, "experiment_id": experiment["id"]}
    ) or {}
    return {
        "experiment_id": experiment["id"],
        "legacy": False,
        "configurations": results,
        "total_planned_calls": plan.get("total_planned_calls"),
        "plan_status": plan.get("status"),
    }


# ------------------------------------------------------------------ case drill-down
async def _case_index(owner_id: str, experiment: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    plan = await repo.benchmark_plans.find_one(
        {"owner_id": owner_id, "experiment_id": experiment["id"]}
    ) or {}
    dataset_version_id = plan.get("dataset_version_id") or experiment.get("dataset_version_id")
    index: Dict[str, Dict[str, Any]] = {}
    if not dataset_version_id:
        return index
    cases = await repo.dataset_version_cases.list(
        {"owner_id": owner_id, "dataset_version_id": dataset_version_id},
        sort_field="ordering",
        descending=False,
        limit=10000,
    )
    for case in cases:
        index[case["id"]] = case
    return index


def _pick_run(runs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not runs:
        return None
    successes = [r for r in runs if r.get("status") == "success"]
    pool = successes or runs
    return sorted(
        pool,
        key=lambda r: (
            r.get("repetition_index") or 0,
            r.get("attempt_index") or 0,
            r.get("created_at") or "",
        ),
    )[-1]


async def case_drilldown(
    owner_id: str,
    experiment: Dict[str, Any],
    configs: List[Dict[str, Any]],
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    runs = await repo.runs.list(
        {"owner_id": owner_id, "experiment_id": experiment["id"], "execution_kind": "benchmark"},
        sort_field="created_at",
        descending=False,
        limit=5000,
    )
    evaluations = await repo.evaluations.list(
        {"owner_id": owner_id, "experiment_id": experiment["id"]},
        sort_field="created_at",
        descending=False,
        limit=5000,
    )
    score_by_run: Dict[str, Dict[str, Any]] = {}
    for evaluation in evaluations:
        run_id = evaluation.get("run_id")
        if run_id and evaluation.get("parsed_score") is not None:
            score_by_run[run_id] = evaluation

    cases = await _case_index(owner_id, experiment)
    grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    attempted_cases = set()
    for run in runs:
        case_id = run.get("dataset_case_id")
        config_id = run.get("configuration_id")
        if not case_id or not config_id:
            continue
        attempted_cases.add(case_id)
        grouped.setdefault(case_id, {}).setdefault(config_id, []).append(run)

    ordered_case_ids = sorted(
        attempted_cases,
        key=lambda cid: (
            (cases.get(cid) or {}).get("ordering", 0),
            cid,
        ),
    )

    rows: List[Dict[str, Any]] = []
    for case_id in ordered_case_ids:
        case = cases.get(case_id) or {}
        per_config: Dict[str, Any] = {}
        for config in configs:
            run = _pick_run(grouped.get(case_id, {}).get(config["id"], []))
            if run is None:
                per_config[config["id"]] = None
                continue
            evaluation = score_by_run.get(run["id"])
            per_config[config["id"]] = {
                "run_id": run["id"],
                "configuration_id": config["id"],
                "label": config.get("label"),
                "status": run.get("status"),
                "latency_ms": run.get("latency_ms"),
                "input_tokens": run.get("input_tokens"),
                "output_tokens": run.get("output_tokens"),
                "cost_known": bool(run.get("cost_available")),
                "cost": run.get("estimated_cost") if run.get("cost_available") else None,
                "score": evaluation.get("parsed_score") if evaluation else None,
                "evaluation_id": evaluation.get("id") if evaluation else None,
            }
        rows.append({
            "dataset_case_id": case_id,
            "input_text": case.get("input_text"),
            "expected_output": case.get("expected_output"),
            "configurations": per_config,
        })

    total = len(rows)
    paginated = rows[offset:offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": paginated}


# ------------------------------------------------------------------ comparison
async def compare_configurations(
    owner_id: str, experiment: Dict[str, Any], baseline_id: str, candidate_id: str
) -> Dict[str, Any]:
    configs = {
        c["id"]: c
        for c in await experiment_service.get_configurations(owner_id, experiment["id"])
    }
    if baseline_id not in configs:
        raise EvaluationError("Baseline configuration not found")
    if candidate_id not in configs:
        raise EvaluationError("Candidate configuration not found")

    baseline_metrics = await aggregate_configuration(owner_id, experiment["id"], configs[baseline_id])
    candidate_metrics = await aggregate_configuration(owner_id, experiment["id"], configs[candidate_id])

    runs = await repo.runs.list(
        {"owner_id": owner_id, "experiment_id": experiment["id"], "execution_kind": "benchmark"},
        sort_field="created_at",
        descending=False,
        limit=5000,
    )
    evaluations = await repo.evaluations.list(
        {"owner_id": owner_id, "experiment_id": experiment["id"]},
        sort_field="created_at",
        descending=False,
        limit=5000,
    )
    score_by_run = {
        e["run_id"]: e["parsed_score"]
        for e in evaluations
        if e.get("run_id") and e.get("parsed_score") is not None
    }

    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    attempted_cases = set()
    for run in runs:
        case_id = run.get("dataset_case_id")
        config_id = run.get("configuration_id")
        if not case_id or not config_id:
            continue
        attempted_cases.add(case_id)
        grouped.setdefault((case_id, config_id), []).append(run)

    cases = await _case_index(owner_id, experiment)
    counts = {"improved": 0, "regressed": 0, "unchanged": 0, "incomparable_error": 0}
    case_rows: List[Dict[str, Any]] = []
    for case_id in sorted(attempted_cases, key=lambda cid: ((cases.get(cid) or {}).get("ordering", 0), cid)):
        base_run = _pick_run(grouped.get((case_id, baseline_id), []))
        cand_run = _pick_run(grouped.get((case_id, candidate_id), []))
        base_score = score_by_run.get(base_run["id"]) if base_run else None
        cand_score = score_by_run.get(cand_run["id"]) if cand_run else None

        if (
            base_run is None or cand_run is None
            or base_run.get("status") != "success" or cand_run.get("status") != "success"
            or base_score is None or cand_score is None
        ):
            classification = "incomparable_error"
            delta = None
        elif cand_score > base_score:
            classification = "improved"
            delta = round(cand_score - base_score, 6)
        elif cand_score < base_score:
            classification = "regressed"
            delta = round(cand_score - base_score, 6)
        else:
            classification = "unchanged"
            delta = 0.0
        counts[classification] += 1
        case_rows.append({
            "case_id": case_id,
            "input_snippet": ((cases.get(case_id) or {}).get("input_text") or "")[:120],
            "classification": classification,
            "baseline_score": base_score,
            "candidate_score": cand_score,
            "delta": delta,
        })

    changed_cases = [
        row for row in case_rows if row["classification"] in ("improved", "regressed")
    ]
    return {
        "experiment_id": experiment["id"],
        "baseline": {**_config_base(configs[baseline_id]), **baseline_metrics},
        "candidate": {**_config_base(configs[candidate_id]), **candidate_metrics},
        "deltas": {
            "avg_score": _delta(baseline_metrics["avg_score"], candidate_metrics["avg_score"]),
            "success_rate": _delta(baseline_metrics["success_rate"], candidate_metrics["success_rate"]),
            "avg_latency_ms": _delta(baseline_metrics["avg_latency_ms"], candidate_metrics["avg_latency_ms"]),
            "known_cost_total": _delta(baseline_metrics["known_cost_total"], candidate_metrics["known_cost_total"]),
        },
        "comparable": bool(
            baseline_metrics["attempted_runs"] and candidate_metrics["attempted_runs"]
        ),
        "case_counts": counts,
        "changed_cases": changed_cases,
        "cases": case_rows,
    }


# ------------------------------------------------------------------ findings
async def finding_from_results(
    owner_id: str, experiment: Dict[str, Any], payload: Dict[str, Any]
) -> Dict[str, Any]:
    exp_id = experiment["id"]
    project_id = experiment.get("project_id")
    title = (payload.get("title") or "").strip()
    if not title:
        raise EvaluationError("title is required")
    evidence_input = payload.get("evidence") or {}
    run_ids = evidence_input.get("run_ids") or []
    evaluation_ids = evidence_input.get("evaluation_ids") or []
    configuration_ids = evidence_input.get("configuration_ids") or []
    dataset_version_id = evidence_input.get("dataset_version_id")

    evidence: List[Dict[str, Any]] = [
        {"type": "experiment", "ref_id": exp_id, "label": experiment.get("title", "")}
    ]

    for run_id in run_ids:
        run = await repo.runs.get(run_id, owner_id)
        if not run or run.get("experiment_id") != exp_id:
            raise EvaluationError(f"Run {run_id} does not belong to this experiment")
        if run.get("project_id") not in (None, project_id):
            raise EvaluationError(f"Run {run_id} belongs to a different project")
        evidence.append({"type": "run", "ref_id": run_id, "label": run.get("model_name") or ""})

    for evaluation_id in evaluation_ids:
        evaluation = await repo.evaluations.get(evaluation_id, owner_id)
        if not evaluation or evaluation.get("experiment_id") != exp_id:
            raise EvaluationError(
                f"Evaluation {evaluation_id} does not belong to this experiment"
            )
        if evaluation.get("project_id") not in (None, project_id):
            raise EvaluationError(f"Evaluation {evaluation_id} belongs to a different project")
        evidence.append({
            "type": "evaluation",
            "ref_id": evaluation_id,
            "label": evaluation.get("evaluator_type") or evaluation.get("eval_type") or "",
        })

    if dataset_version_id:
        version = await repo.dataset_versions.get(dataset_version_id, owner_id)
        if not version:
            raise EvaluationError("Dataset version not found")
        if version.get("project_id") not in (None, project_id):
            raise EvaluationError("Dataset version belongs to a different project")
        if experiment.get("dataset_id") and version.get("dataset_id") != experiment.get("dataset_id"):
            raise EvaluationError("Dataset version does not belong to this experiment's dataset")
        evidence.append({
            "type": "dataset_version",
            "ref_id": dataset_version_id,
            "label": f"v{version.get('version')}",
        })

    for configuration_id in configuration_ids:
        config = await repo.experiment_configs.get(configuration_id, owner_id)
        if not config or config.get("experiment_id") != exp_id:
            raise EvaluationError(
                f"Configuration {configuration_id} does not belong to this experiment"
            )
        evidence.append({
            "type": "benchmark_result",
            "ref_id": configuration_id,
            "label": f"Configuration {config.get('label', configuration_id)}",
        })

    now = now_iso()
    finding = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "title": title,
        "statement": payload.get("statement", ""),
        "description": payload.get("description", ""),
        "confidence": payload.get("confidence") or "medium",
        "status": "provisional",
        "notes": payload.get("notes", ""),
        "tags": [],
        "evidence": evidence,
        "created_at": now,
        "updated_at": now,
    }
    await repo.findings.insert(finding)
    return finding


# ------------------------------------------------------------------ V5 read APIs
class EvaluationNotFound(EvaluationError):
    """Raised when an owner-scoped evaluation/run cannot be found (HTTP 404)."""


async def _resolve_evaluation_provenance(
    owner_id: str, evaluation: Dict[str, Any]
) -> Dict[str, Any]:
    run_id = evaluation.get("run_id")
    run = await repo.runs.get(run_id, owner_id) if run_id else None
    resolved_run = {
        "id": run_id,
        "exists": run is not None,
        "experiment_id": (run or {}).get("experiment_id"),
        "project_id": (run or {}).get("project_id"),
        "status": (run or {}).get("status"),
        "model_name": (run or {}).get("model_name"),
    }

    experiment_id = evaluation.get("experiment_id") or (run or {}).get("experiment_id")
    experiment = (
        await repo.experiments.get(experiment_id, owner_id) if experiment_id else None
    )
    resolved_experiment = {
        "id": experiment_id,
        "exists": experiment is not None,
        "title": (experiment or {}).get("title"),
    }

    case_id = evaluation.get("dataset_case_id")
    dataset_case = (
        await repo.dataset_version_cases.get(case_id, owner_id) if case_id else None
    )
    input_text = (dataset_case or {}).get("input_text")
    resolved_case = {
        "id": case_id,
        "exists": dataset_case is not None,
        "input_snippet": (input_text or "")[:200] or None,
    }

    judge_run_id = evaluation.get("judge_run_id")
    judge_run = await repo.runs.get(judge_run_id, owner_id) if judge_run_id else None
    resolved_judge_run = {
        "id": judge_run_id,
        "exists": judge_run is not None,
        "status": (judge_run or {}).get("status"),
    }

    rubric = evaluation.get("rubric")
    if not isinstance(rubric, dict):
        rubric_id = evaluation.get("rubric_id")
        rubric = {"id": rubric_id} if rubric_id else None

    return {
        "run": resolved_run,
        "experiment": resolved_experiment,
        "dataset_case": resolved_case,
        "rubric": rubric,
        "judge_run": resolved_judge_run,
    }


async def get_evaluation(owner_id: str, evaluation_id: str) -> Dict[str, Any]:
    evaluation = await repo.evaluations.get(evaluation_id, owner_id)
    if not evaluation:
        raise EvaluationNotFound("Evaluation not found")
    result = dict(evaluation)
    result["resolved"] = await _resolve_evaluation_provenance(owner_id, evaluation)
    return result


async def list_run_evaluations(
    owner_id: str, run_id: str, limit: int = 50, offset: int = 0
) -> Dict[str, Any]:
    run = await repo.runs.get(run_id, owner_id)
    if not run:
        raise EvaluationNotFound("Run not found")
    query = {"owner_id": owner_id, "run_id": run_id}
    total = await repo.evaluations.count(query)
    items = await repo.evaluations.list(
        query, sort_field="created_at", descending=True, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def promote_finding(
    owner_id: str, evaluation_id: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    from . import failures as failure_service

    evaluation = await repo.evaluations.get(evaluation_id, owner_id)
    if not evaluation:
        raise EvaluationNotFound("Evaluation not found")

    findings = evaluation.get("findings") or []
    index = body.get("finding_index")
    if isinstance(index, bool) or not isinstance(index, int):
        raise EvaluationError("finding_index must be an integer")
    if index < 0 or index >= len(findings):
        raise EvaluationError("finding_index out of range")
    finding = findings[index] if isinstance(findings[index], dict) else {}

    canonical = evaluation.get("evaluator_type") or evaluation.get("evaluation_type")
    if canonical == "judge_model":
        source = "JUDGE"
    elif canonical == "human":
        source = "HUMAN"
    else:
        source = "RULE"

    category = body.get("category") or _failure_category_for(finding.get("category"))
    severity = body.get("severity") or _failure_severity_for(finding.get("severity"))
    title = body.get("title") or finding.get("message") or "Evaluation finding"
    description = body.get("description") or finding.get("message") or ""

    return await failure_service.create_failure(
        owner_id,
        evaluation.get("project_id"),
        category=category,
        source=source,
        severity=severity,
        title=redaction.redact_text(str(title)),
        description=redaction.redact_text(str(description)),
        run_id=evaluation.get("run_id"),
        evaluation_id=evaluation.get("id"),
        dataset_case_id=evaluation.get("dataset_case_id"),
        finding_id=finding.get("id"),
        signals={"finding": finding},
        evidence=redaction.redact({
            "finding": finding,
            "evaluation_id": evaluation.get("id"),
        }),
    )
