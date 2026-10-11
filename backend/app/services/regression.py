"""V5.3 Regression Engine: truthful, deterministic configuration comparison.

A regression pins a baseline and candidate configuration, ensures both sides
carry evaluations from an explicit evaluator spec and then reports per-metric
deltas plus case-level classifications. Every unknown telemetry value stays
``None`` and is classified ``unknown`` rather than silently coerced to zero;
there is never a single aggregate "score". The persisted ``regression_runs``
document is append-only and owner/project scoped.
"""
import math
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import (
    EVALUATION_TYPES,
    LEGACY_EVALUATOR_ALIASES,
    V5_SCHEMA_VERSION,
    new_id,
    now_iso,
)
from . import evaluation as evaluation_service
from . import experiments as experiment_service
from . import tracing


class RegressionError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class RegressionNotFound(RegressionError):
    """Owner-scoped regression result missing (HTTP 404)."""


METRIC_DIRECTIONS = {
    "pass_count": "higher_is_better",
    "fail_count": "lower_is_better",
    "error_count": "lower_is_better",
    "not_evaluated_count": "lower_is_better",
    "avg_score": "higher_is_better",
    "success_count": "higher_is_better",
    "success_rate": "higher_is_better",
    "failed_run_count": "lower_is_better",
    "avg_latency_ms": "lower_is_better",
    "avg_ttft_ms": "lower_is_better",
    "total_input_tokens": "lower_is_better",
    "total_output_tokens": "lower_is_better",
    "known_cost_total": "lower_is_better",
    "tool_call_total": "lower_is_better",
    "tool_error_total": "lower_is_better",
    "grounding_failure_count": "lower_is_better",
    "citation_failure_count": "lower_is_better",
    "instruction_violation_count": "lower_is_better",
}

_pick_run = getattr(evaluation_service, "_pick_run", None)


# ------------------------------------------------------------------ primitives
def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _mean(values: List[Any]) -> Optional[float]:
    known = [float(v) for v in values if v is not None]
    if not known:
        return None
    return round(sum(known) / len(known), 6)


def _median(values: List[Any]) -> Optional[float]:
    known = sorted(float(v) for v in values if v is not None)
    if not known:
        return None
    mid = len(known) // 2
    if len(known) % 2:
        return known[mid]
    return (known[mid - 1] + known[mid]) / 2


def _sum_or_none(runs: List[Dict[str, Any]], field: str) -> Tuple[Optional[float], int]:
    if not runs:
        return None, 0
    values = [_numeric(run.get(field)) for run in runs]
    missing = sum(1 for value in values if value is None)
    if missing:
        return None, missing
    return sum(values), 0


def _cost_total(runs: List[Dict[str, Any]]) -> Tuple[Optional[float], int]:
    if not runs:
        return None, 0
    total = 0.0
    unknown = 0
    for run in runs:
        if run.get("cost_available") is not True:
            unknown += 1
            continue
        total += float(_numeric(run.get("estimated_cost")) or 0.0)
    if unknown:
        return None, unknown
    return round(total, 6), 0


def _canonical_evaluator_type(raw: Any) -> str:
    value = raw.strip() if isinstance(raw, str) else ""
    if not value:
        value = "rule"
    if value in EVALUATION_TYPES:
        return value
    canonical = LEGACY_EVALUATOR_ALIASES.get(value)
    if canonical is None:
        raise RegressionError(f"Unsupported evaluator_type: {value}")
    return canonical


def _evaluation_type_of(evaluation: Dict[str, Any]) -> Optional[str]:
    return evaluation.get("evaluation_type") or evaluation.get("evaluator_type")


def _evaluation_matches(
    evaluation: Dict[str, Any], canonical: str, keys: List[str]
) -> bool:
    if _evaluation_type_of(evaluation) != canonical:
        return False
    if keys:
        key = (evaluation.get("evaluator") or {}).get("key")
        if key not in keys:
            return False
    return True


def _index_latest_evaluations(
    evaluations: List[Dict[str, Any]], canonical: str, keys: List[str]
) -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Tuple[Tuple[int, str], Dict[str, Any]]] = {}
    for evaluation in evaluations:
        run_id = evaluation.get("run_id")
        if not run_id:
            continue
        rank = (
            1 if _evaluation_matches(evaluation, canonical, keys) else 0,
            evaluation.get("created_at") or "",
        )
        current = best.get(run_id)
        if current is None or rank > current[0]:
            best[run_id] = (rank, evaluation)
    return {run_id: entry[1] for run_id, entry in best.items()}


def _pick_representative(runs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not runs:
        return None
    if _pick_run is not None:
        return _pick_run(runs)
    successes = [run for run in runs if run.get("status") == "success"]
    pool = successes or runs
    return sorted(
        pool,
        key=lambda run: (
            run.get("repetition_index") or 0,
            run.get("attempt_index") or 0,
            run.get("created_at") or "",
        ),
    )[-1]


# ------------------------------------------------------------------ aggregation
def _aggregate_configuration(
    runs: List[Dict[str, Any]], evaluation_by_run: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    attempted = len(runs)
    success = sum(1 for run in runs if run.get("status") == "success")
    failed = sum(1 for run in runs if run.get("status") == "error")
    success_rate = round(success / attempted, 6) if attempted > 0 else None

    outcomes = {"PASS": 0, "FAIL": 0, "ERROR": 0, "NOT_EVALUATED": 0}
    evaluations: List[Dict[str, Any]] = []
    for run in runs:
        evaluation = evaluation_by_run.get(run.get("id"))
        if evaluation is None:
            continue
        evaluations.append(evaluation)
        outcome = (evaluation.get("result") or {}).get("outcome")
        if outcome in outcomes:
            outcomes[outcome] += 1

    scores = [
        _numeric((evaluation.get("result") or {}).get("score"))
        for evaluation in evaluations
    ]
    scores = [score for score in scores if score is not None]

    latencies = [_numeric(run.get("latency_ms")) for run in runs]
    latencies = [value for value in latencies if value is not None]
    ttfts = [_numeric(run.get("ttft_ms")) for run in runs]
    ttfts = [value for value in ttfts if value is not None]

    total_input, input_missing = _sum_or_none(runs, "input_tokens")
    total_output, output_missing = _sum_or_none(runs, "output_tokens")
    cost_total, cost_unknown = _cost_total(runs)
    tool_calls, tool_missing = _sum_or_none(runs, "tool_call_count")
    tool_errors, tool_error_missing = _sum_or_none(runs, "tool_failure_count")

    grounding = 0
    citation = 0
    instruction = 0
    eval_cost_known: List[float] = []
    eval_cost_unknown = 0
    for evaluation in evaluations:
        categories = {
            finding.get("category")
            for finding in (evaluation.get("findings") or [])
            if isinstance(finding, dict)
        }
        if categories & {"grounding", "citation"}:
            grounding += 1
        if "citation" in categories:
            citation += 1
        if "instruction_following" in categories:
            instruction += 1
        usage = evaluation.get("usage") or {}
        if usage.get("cost_available") is True:
            cost = _numeric(usage.get("cost"))
            if cost is None:
                eval_cost_unknown += 1
            else:
                eval_cost_known.append(float(cost))
        else:
            eval_cost_unknown += 1

    return {
        "attempted_runs": attempted,
        "success_count": success,
        "failed_run_count": failed,
        "success_rate": success_rate,
        "evaluated_count": len(evaluations),
        "pass_count": outcomes["PASS"],
        "fail_count": outcomes["FAIL"],
        "error_count": outcomes["ERROR"],
        "not_evaluated_count": outcomes["NOT_EVALUATED"],
        "avg_score": _mean(scores),
        "median_score": _median(scores),
        "avg_latency_ms": _mean(latencies),
        "avg_ttft_ms": _mean(ttfts),
        "total_input_tokens": total_input,
        "input_tokens_missing_run_count": input_missing,
        "total_output_tokens": total_output,
        "output_tokens_missing_run_count": output_missing,
        "known_cost_total": cost_total,
        "cost_unknown_run_count": cost_unknown,
        "tool_call_total": tool_calls,
        "tool_call_missing_run_count": tool_missing,
        "tool_error_total": tool_errors,
        "tool_error_missing_run_count": tool_error_missing,
        "grounding_failure_count": grounding,
        "citation_failure_count": citation,
        "instruction_violation_count": instruction,
        "evaluation_cost_total": (
            round(sum(eval_cost_known), 6) if eval_cost_known else None
        ),
        "evaluation_cost_unknown_count": eval_cost_unknown,
    }


def _metric_rows(
    baseline_metrics: Dict[str, Any], candidate_metrics: Dict[str, Any]
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for metric, direction in METRIC_DIRECTIONS.items():
        baseline = baseline_metrics.get(metric)
        candidate = candidate_metrics.get(metric)
        baseline_known = baseline is not None
        candidate_known = candidate is not None
        delta: Optional[float] = None
        classification = "unknown"
        if baseline_known and candidate_known:
            try:
                delta = round(float(candidate) - float(baseline), 6)
            except (TypeError, ValueError):
                delta = None
            if delta is not None:
                if delta == 0:
                    classification = "unchanged"
                elif (delta > 0) == (direction == "higher_is_better"):
                    classification = "improved"
                else:
                    classification = "degraded"
        rows.append({
            "metric": metric,
            "baseline": baseline,
            "candidate": candidate,
            "delta": delta,
            "direction": direction,
            "classification": classification,
            "baseline_missing": not baseline_known,
            "candidate_missing": not candidate_known,
        })
    return rows


async def _load_case_order(
    owner_id: str, experiment: Dict[str, Any], plan: Dict[str, Any]
) -> Tuple[Dict[str, int], Optional[str]]:
    dataset_version_id = plan.get("dataset_version_id") or experiment.get(
        "dataset_version_id"
    )
    if not dataset_version_id:
        return {}, None
    cases = await repo.dataset_version_cases.list(
        {"owner_id": owner_id, "dataset_version_id": dataset_version_id},
        sort_field="ordering",
        descending=False,
        limit=10000,
    )
    order = {
        case["id"]: case.get("ordering")
        for case in cases
        if isinstance(case, dict) and case.get("id")
    }
    return order, dataset_version_id


def _case_rows(
    runs: List[Dict[str, Any]],
    evaluation_by_run: Dict[str, Dict[str, Any]],
    baseline_id: str,
    candidate_id: str,
    case_order: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    case_ids = set()
    for run in runs:
        configuration_id = run.get("configuration_id")
        case_id = run.get("dataset_case_id")
        if not case_id or configuration_id not in (baseline_id, candidate_id):
            continue
        case_ids.add(case_id)
        grouped.setdefault(case_id, {}).setdefault(configuration_id, []).append(run)

    ordered = sorted(
        case_ids,
        key=lambda case_id: (case_order.get(case_id) is None, case_order.get(case_id, 0), case_id),
    )

    counts = {
        "improved": 0,
        "regressed": 0,
        "unchanged": 0,
        "incomparable": 0,
        "unknown": 0,
    }
    rows: List[Dict[str, Any]] = []
    for case_id in ordered:
        baseline_run = _pick_representative(
            grouped.get(case_id, {}).get(baseline_id, [])
        )
        candidate_run = _pick_representative(
            grouped.get(case_id, {}).get(candidate_id, [])
        )
        baseline_eval = (
            evaluation_by_run.get(baseline_run["id"]) if baseline_run else None
        )
        candidate_eval = (
            evaluation_by_run.get(candidate_run["id"]) if candidate_run else None
        )
        baseline_score = (
            _numeric((baseline_eval.get("result") or {}).get("score"))
            if baseline_eval
            else None
        )
        candidate_score = (
            _numeric((candidate_eval.get("result") or {}).get("score"))
            if candidate_eval
            else None
        )
        delta: Optional[float] = None
        if (
            baseline_run is None
            or candidate_run is None
            or baseline_score is None
            or candidate_score is None
        ):
            classification = "unknown"
        elif (
            baseline_run.get("status") != "success"
            or candidate_run.get("status") != "success"
        ):
            classification = "incomparable"
        else:
            delta = round(candidate_score - baseline_score, 6)
            if delta > 0:
                classification = "improved"
            elif delta < 0:
                classification = "regressed"
            else:
                classification = "unchanged"
        counts[classification] += 1
        rows.append({
            "dataset_case_id": case_id,
            "baseline_run_id": baseline_run.get("id") if baseline_run else None,
            "candidate_run_id": candidate_run.get("id") if candidate_run else None,
            "baseline_status": baseline_run.get("status") if baseline_run else None,
            "candidate_status": candidate_run.get("status") if candidate_run else None,
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "baseline_outcome": (
                (baseline_eval.get("result") or {}).get("outcome")
                if baseline_eval
                else None
            ),
            "candidate_outcome": (
                (candidate_eval.get("result") or {}).get("outcome")
                if candidate_eval
                else None
            ),
            "delta": delta,
            "classification": classification,
        })
    return rows, counts


# ------------------------------------------------------------------ entry points
async def run_regression(
    owner_id: str, experiment: Dict[str, Any], body: Dict[str, Any]
) -> Dict[str, Any]:
    configs = await experiment_service.get_configurations(owner_id, experiment["id"])
    config_by_id = {config["id"]: config for config in configs}

    baseline_id = body.get("baseline_configuration_id")
    candidate_id = body.get("candidate_configuration_id")
    if baseline_id not in config_by_id:
        raise RegressionError("Baseline configuration not found")
    if candidate_id not in config_by_id:
        raise RegressionError("Candidate configuration not found")

    canonical = _canonical_evaluator_type(body.get("evaluator_type"))
    evaluator_keys = list(body.get("evaluator_keys") or [])
    reevaluate = bool(body.get("reevaluate"))

    runs = await repo.runs.list(
        {
            "owner_id": owner_id,
            "experiment_id": experiment["id"],
            "execution_kind": "benchmark",
        },
        sort_field="created_at",
        descending=False,
        limit=10000,
    )

    preliminary = await repo.evaluations.list(
        {"owner_id": owner_id, "experiment_id": experiment["id"]},
        sort_field="created_at",
        descending=False,
        limit=10000,
    )
    existing_by_run: Dict[str, List[Dict[str, Any]]] = {}
    for evaluation in preliminary:
        run_id = evaluation.get("run_id")
        if run_id:
            existing_by_run.setdefault(run_id, []).append(evaluation)

    evaluation_errors = 0
    for run in runs:
        if run.get("configuration_id") not in (baseline_id, candidate_id):
            continue
        run_id = run.get("id")
        if not reevaluate:
            already = any(
                _evaluation_matches(evaluation, canonical, evaluator_keys)
                for evaluation in existing_by_run.get(run_id, [])
            )
            if already:
                continue
        try:
            await evaluation_service.evaluate_run(owner_id, experiment, {
                "run_id": run_id,
                "evaluation_type": canonical,
                "evaluator_keys": evaluator_keys,
                "rubric_id": body.get("rubric_id"),
                "judge_model_id": body.get("judge_model_id"),
                "threshold": body.get("threshold"),
            })
        except Exception:
            evaluation_errors += 1

    evaluations = await repo.evaluations.list(
        {"owner_id": owner_id, "experiment_id": experiment["id"]},
        sort_field="created_at",
        descending=False,
        limit=10000,
    )
    evaluation_by_run = _index_latest_evaluations(
        evaluations, canonical, evaluator_keys
    )

    plans = await repo.benchmark_plans.list(
        {"owner_id": owner_id, "experiment_id": experiment["id"]},
        sort_field="created_at",
        descending=True,
        limit=1,
    )
    plan = plans[0] if plans else {}
    case_order, dataset_version_id = await _load_case_order(owner_id, experiment, plan)

    baseline_runs = [
        run for run in runs if run.get("configuration_id") == baseline_id
    ]
    candidate_runs = [
        run for run in runs if run.get("configuration_id") == candidate_id
    ]
    baseline_metrics = _aggregate_configuration(baseline_runs, evaluation_by_run)
    candidate_metrics = _aggregate_configuration(candidate_runs, evaluation_by_run)

    metric_rows = _metric_rows(baseline_metrics, candidate_metrics)
    summary = {"improved": 0, "unchanged": 0, "degraded": 0, "unknown": 0}
    for row in metric_rows:
        summary[row["classification"]] += 1

    case_rows, case_counts = _case_rows(
        runs,
        evaluation_by_run,
        baseline_id,
        candidate_id,
        case_order,
    )

    rubric_snapshot = None
    rubric_id = body.get("rubric_id")
    if rubric_id:
        rubric_snapshot = await repo.rubrics.get(rubric_id, owner_id)

    def _config_block(configuration_id: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
        config = config_by_id.get(configuration_id) or {}
        return {
            "configuration_id": configuration_id,
            "label": config.get("label"),
            "model_id": config.get("model_id"),
            "model_snapshot": config.get("model_snapshot"),
            "metrics": metrics,
        }

    now = now_iso()
    document = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": experiment.get("project_id"),
        "experiment_id": experiment["id"],
        "name": body.get("name") or "",
        "status": "completed",
        "schema_version": V5_SCHEMA_VERSION,
        "baseline": _config_block(baseline_id, baseline_metrics),
        "candidate": _config_block(candidate_id, candidate_metrics),
        "metrics": metric_rows,
        "summary": summary,
        "deltas": {row["metric"]: row["delta"] for row in metric_rows},
        "case_counts": case_counts,
        "cases": case_rows,
        "evaluator_spec": {
            "evaluation_type": canonical,
            "evaluator_keys": evaluator_keys,
            "rubric_id": rubric_id,
            "judge_model_id": body.get("judge_model_id"),
            "threshold": body.get("threshold"),
        },
        "rubric_snapshot": rubric_snapshot,
        "dataset_version_id": dataset_version_id,
        "plan_id": plan.get("id"),
        "evaluation_errors": evaluation_errors,
        "notes": body.get("notes") or "",
        "created_at": now,
    }
    await repo.regression_runs.insert(document)

    try:
        trace = await tracing.start_trace(
            owner_id,
            "system",
            project_id=experiment.get("project_id"),
            experiment_id=experiment["id"],
            metadata={"kind": "regression", "regression_id": document["id"]},
        )
        span = await tracing.start_span(
            owner_id,
            trace["id"],
            "REGRESSION_EXECUTION",
            "Regression comparison",
            metadata={
                "regression_id": document["id"],
                "baseline_configuration_id": baseline_id,
                "candidate_configuration_id": candidate_id,
                "evaluator_spec": document.get("evaluator_spec"),
                "metric_count": len(document.get("metrics") or []),
                "summary": document.get("summary"),
            },
        )
        await tracing.finish_span(span, status="completed")
        await tracing.finish_trace(trace, status="completed")
        document["trace_id"] = trace["id"]
        try:
            await repo.regression_runs.update(
                document["id"], owner_id, {"trace_id": trace["id"]}
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass
    return document


async def list_regressions(
    owner_id: str, experiment_id: str, limit: int = 50, offset: int = 0
) -> Dict[str, Any]:
    query = {"owner_id": owner_id, "experiment_id": experiment_id}
    total = await repo.regression_runs.count(query)
    items = await repo.regression_runs.list(
        query, sort_field="created_at", descending=True, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def get_regression(owner_id: str, regression_id: str) -> Dict[str, Any]:
    regression = await repo.regression_runs.get(regression_id, owner_id)
    if not regression:
        raise RegressionNotFound("Regression result not found")
    return regression
