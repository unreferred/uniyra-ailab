"""V5.7 red team service.

Red-team cases are ordinary dataset version cases carrying a ``red_team`` spec
(:class:`app.models.RedTeamExpectation`). This module reuses the existing
Dataset -> Experiment -> Run -> Evaluation -> Finding pipeline: an experiment is
created with ``experiment_type="red_team"`` and the shared rule evaluator key
``red_team``; execution is delegated to the benchmark runner. No parallel run
system is introduced.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .. import repository as repo
from . import benchmark as benchmark_service
from . import experiments as experiment_service
from . import redaction
from . import tracing

RED_TEAM_EXPERIMENT_TYPE = "red_team"
RED_TEAM_VERSION = "v5.7.0"


class RedTeamError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class RedTeamNotFound(RedTeamError):
    """Missing owner-scoped red-team resource; routers translate to HTTP 404."""


def _input_snippet(text: Any, limit: int = 240) -> str:
    value = "" if text is None else str(text)
    return value[:limit]


def _is_red_team_evaluation(evaluation: Dict[str, Any]) -> bool:
    """True when a persisted evaluation ran the shared ``red_team`` evaluator."""
    if not isinstance(evaluation, dict):
        return False
    raw = evaluation.get("raw_result")
    if not isinstance(raw, dict):
        return False
    if raw.get("evaluator_key") == "red_team":
        return True
    for sub in raw.get("evaluators") or []:
        if isinstance(sub, dict) and sub.get("evaluator_key") == "red_team":
            return True
    return False


def _boundary_held(outcomes: List[str]) -> Optional[bool]:
    """Any FAIL -> False; all PASS -> True; anything else -> None."""
    decided = [outcome for outcome in outcomes if outcome in ("PASS", "FAIL")]
    if not decided:
        return None
    if any(outcome == "FAIL" for outcome in decided):
        return False
    return True


async def create_red_team_experiment(
    owner_id: str,
    project_id: str,
    dataset_id: str,
    dataset_version_id: str,
    model_ids: Optional[List[str]] = None,
    title: Optional[str] = None,
    objective: str = "",
    repetitions: int = 1,
) -> Dict[str, Any]:
    """Create (and, when configured, ready) a red_team experiment.

    The dataset must belong to ``project_id`` and the version must belong to the
    dataset, belong to the project and be frozen. Cross-project datasets are
    rejected here deterministically.
    """
    project = await repo.projects.get(project_id, owner_id)
    if not project:
        raise RedTeamError("Project not found")

    dataset = await repo.datasets.get(dataset_id, owner_id)
    if not dataset:
        raise RedTeamError("Dataset not found")
    if dataset.get("project_id") != project_id:
        raise RedTeamError("Dataset belongs to a different project")

    version = await repo.dataset_versions.get(dataset_version_id, owner_id)
    if not version or version.get("dataset_id") != dataset_id:
        raise RedTeamError("Dataset version was not found for this dataset")
    if version.get("project_id") not in (None, project_id):
        raise RedTeamError("Dataset version belongs to a different project")
    if version.get("status") != "frozen":
        raise RedTeamError("Dataset version must be frozen")

    model_ids = list(model_ids or [])
    configurations = [
        {"label": f"cfg-{index}", "model_id": model_id, "order": index}
        for index, model_id in enumerate(model_ids)
    ]
    try:
        repeat_count = max(1, int(repetitions or 1))
    except (TypeError, ValueError):
        repeat_count = 1

    protocol = {
        "dataset_id": dataset_id,
        "dataset_version_id": dataset_version_id,
        "repetitions": repeat_count,
        "evaluation": {
            "enabled": True,
            "evaluator_type": "rule",
            "evaluator_keys": ["red_team"],
        },
    }
    resolved_title = title or f"Red team: {dataset.get('name') or dataset_id}"

    experiment = await experiment_service.create_experiment(
        owner_id,
        project_id,
        resolved_title,
        objective=objective,
        experiment_type=RED_TEAM_EXPERIMENT_TYPE,
        protocol=protocol,
        configurations=configurations or None,
    )
    if configurations:
        experiment = await experiment_service.ready_experiment(owner_id, experiment)
    return experiment


async def execute_red_team(owner_id: str, experiment_id: str) -> Dict[str, Any]:
    """Run a READY red_team experiment through the shared benchmark runner."""
    experiment = await repo.experiments.get(experiment_id, owner_id)
    if not experiment:
        raise RedTeamNotFound("Red team experiment not found")
    if experiment.get("experiment_type") != RED_TEAM_EXPERIMENT_TYPE:
        raise RedTeamError("Experiment is not a red team experiment")
    if experiment_service.normalize_status(experiment.get("status")) != "READY":
        raise RedTeamError("Red team experiment must be READY to execute")

    trace_metadata = redaction.redact({
        "experiment_id": experiment_id,
        "experiment_type": RED_TEAM_EXPERIMENT_TYPE,
        "dataset_version_id": experiment.get("dataset_version_id"),
        "red_team_version": RED_TEAM_VERSION,
    })
    trace = await tracing.start_trace(
        owner_id,
        "red_team",
        project_id=experiment.get("project_id"),
        experiment_id=experiment_id,
        metadata=trace_metadata,
    )
    started = time.perf_counter()
    span = await tracing.start_span(
        owner_id,
        trace["id"],
        "RED_TEAM_EXECUTION",
        "red_team.execute",
        metadata=trace_metadata,
    )

    plan: Optional[Dict[str, Any]] = None
    try:
        plan = await benchmark_service.build_plan(owner_id, experiment)
        await benchmark_service.run_benchmark(plan["id"], owner_id)
    except ValueError as exc:
        summary = {"runs_total": 0, "runs_error": 0, "evaluations": 0,
                   "boundaries": {"held": 0, "violated": 0, "not_evaluated": 0}}
        await tracing.finish_span(
            span, status="failed", metadata={**trace_metadata, "summary": summary},
            structured_error={"error": str(exc)}, started_perf=started,
        )
        await tracing.finish_trace(
            trace, status="failed", metadata={**trace_metadata, "summary": summary},
            started_perf=started,
        )
        raise RedTeamError(str(exc)) from exc

    runs = await repo.runs.list(
        {"owner_id": owner_id, "experiment_id": experiment_id},
        sort_field="created_at", descending=False, limit=10000,
    )
    evaluations = await repo.evaluations.list(
        {"owner_id": owner_id, "experiment_id": experiment_id},
        sort_field="created_at", descending=False, limit=10000,
    )
    case_summary = (await red_team_summary(owner_id, experiment_id))["summary"]
    summary = {
        "runs_total": len(runs),
        "runs_error": sum(1 for run in runs if run.get("status") == "error"),
        "evaluations": len(evaluations),
        "boundaries": {
            "held": case_summary.get("boundaries_held", 0),
            "violated": case_summary.get("boundaries_violated", 0),
            "not_evaluated": case_summary.get("not_evaluated", 0),
        },
    }
    await tracing.finish_span(
        span, status="completed", metadata={**trace_metadata, "summary": summary},
        started_perf=started,
    )
    await tracing.finish_trace(
        trace, status="completed", metadata={**trace_metadata, "summary": summary},
        started_perf=started,
    )
    return {
        "experiment_id": experiment_id,
        "plan_id": plan["id"] if plan else None,
        "trace_id": trace["id"],
        "trace": trace,
        "span_id": span["id"],
        "summary": summary,
    }


async def red_team_summary(owner_id: str, experiment_id: str) -> Dict[str, Any]:
    """Per-case boundary outcomes for a red_team experiment."""
    experiment = await repo.experiments.get(experiment_id, owner_id)
    if not experiment:
        raise RedTeamNotFound("Red team experiment not found")
    if experiment.get("experiment_type") != RED_TEAM_EXPERIMENT_TYPE:
        raise RedTeamError("Experiment is not a red team experiment")

    protocol = experiment.get("protocol_snapshot") or experiment.get("protocol") or {}
    dataset_version_id = (
        protocol.get("dataset_version_id") or experiment.get("dataset_version_id")
    )
    cases: List[Dict[str, Any]] = []
    if dataset_version_id:
        cases = await repo.dataset_version_cases.list(
            {"owner_id": owner_id, "dataset_version_id": dataset_version_id},
            sort_field="ordering", descending=False, limit=10000,
        )
    cases = sorted(
        cases,
        key=lambda case: (
            case.get("ordering", 0),
            case.get("created_at") or "",
            case.get("id") or "",
        ),
    )

    runs = await repo.runs.list(
        {"owner_id": owner_id, "experiment_id": experiment_id},
        sort_field="created_at", descending=False, limit=10000,
    )
    evaluations = await repo.evaluations.list(
        {"owner_id": owner_id, "experiment_id": experiment_id},
        sort_field="created_at", descending=False, limit=10000,
    )
    failures = await repo.failures.list(
        {"owner_id": owner_id, "experiment_id": experiment_id},
        sort_field="created_at", descending=False, limit=10000,
    )

    runs_by_case: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        case_id = run.get("dataset_case_id")
        if case_id:
            runs_by_case.setdefault(case_id, []).append(run)

    red_team_evaluations = [e for e in evaluations if _is_red_team_evaluation(e)]

    case_rows: List[Dict[str, Any]] = []
    held_count = 0
    violated_count = 0
    not_evaluated_count = 0
    case_evaluations = 0

    for case in cases:
        spec = case.get("red_team") if isinstance(case.get("red_team"), dict) else {}
        case_runs = runs_by_case.get(case.get("id"), [])
        case_run_ids = {run.get("id") for run in case_runs}
        case_evals = [
            evaluation for evaluation in red_team_evaluations
            if evaluation.get("run_id") in case_run_ids
        ]
        case_failures = [
            failure for failure in failures
            if failure.get("run_id") in case_run_ids
        ]
        outcomes = [
            (evaluation.get("result") or {}).get("outcome")
            for evaluation in case_evals
        ]
        boundary = _boundary_held(outcomes)
        if boundary is True:
            held_count += 1
        elif boundary is False:
            violated_count += 1
        else:
            not_evaluated_count += 1
        case_evaluations += len(case_evals)

        case_rows.append({
            "case_id": case.get("id"),
            "input_snippet": _input_snippet(case.get("input_text")),
            "category": spec.get("category"),
            "expected_behavior": spec.get("expected_behavior"),
            "expected_boundary": spec.get("expected_boundary"),
            "severity": spec.get("severity"),
            "tags": list(spec.get("tags") or case.get("tags") or []),
            "runs": [
                {
                    "run_id": run.get("id"),
                    "configuration_id": run.get("configuration_id"),
                    "status": run.get("status"),
                }
                for run in case_runs
            ],
            "evaluations": [
                {
                    "evaluation_id": evaluation.get("id"),
                    "outcome": (evaluation.get("result") or {}).get("outcome"),
                    "result": evaluation.get("result"),
                }
                for evaluation in case_evals
            ],
            "failures": [
                redaction.redact({
                    "id": failure.get("id"),
                    "failure_domain": failure.get("failure_domain"),
                    "taxonomy_code": failure.get("taxonomy_code"),
                    "severity": failure.get("severity"),
                    "status": failure.get("status"),
                })
                for failure in case_failures
            ],
            "boundary_held": boundary,
        })

    return {
        "experiment": {
            "id": experiment.get("id"),
            "title": experiment.get("title"),
            "status": experiment.get("status"),
            "project_id": experiment.get("project_id"),
            "dataset_version_id": dataset_version_id,
        },
        "cases": case_rows,
        "summary": {
            "cases_total": len(case_rows),
            "boundaries_held": held_count,
            "boundaries_violated": violated_count,
            "not_evaluated": not_evaluated_count,
            "case_evaluations": case_evaluations,
        },
        "red_team_version": RED_TEAM_VERSION,
    }


async def list_red_team_experiments(
    owner_id: str,
    project_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Owner-scoped listing of red_team experiments, optionally by project."""
    query: Dict[str, Any] = {
        "owner_id": owner_id,
        "experiment_type": RED_TEAM_EXPERIMENT_TYPE,
    }
    if project_id:
        query["project_id"] = project_id
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    total = await repo.experiments.count(query)
    items = await repo.experiments.list(
        query, sort_field="created_at", descending=True, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def red_team_fixture_catalog() -> Dict[str, Any]:
    """Static, JSON-safe description of the five fixture categories."""
    from . import red_team_fixtures as fixtures

    return fixtures.fixture_catalog()
