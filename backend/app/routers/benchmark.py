"""First-class benchmark endpoints: plan, start, progress, cancel, resume,
case-level evaluation, aggregation, regression comparison and findings.

Every path is owner-scoped and requires the experiment to belong to the caller.
Model execution is always launched through ``services.benchmark.run_benchmark``,
which is the only writer of benchmark Runs.
"""
import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import (BenchmarkStartRequest, EvaluationRequest,
                      FindingFromResultsRequest, RegressionRequest, now_iso)
from ..services import benchmark as benchmark_service
from ..services import evaluation as evaluation_service
from ..services import experiments as experiment_service
from ..services import regression as regression_service

router = APIRouter(prefix="/experiments", tags=["benchmark"])


async def _load_experiment(owner_id: str, experiment_id: str) -> Dict[str, Any]:
    exp = await repo.experiments.get(experiment_id, owner_id)
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return exp


def _require_status(exp: Dict[str, Any], allowed: set, message: str) -> str:
    status = experiment_service.normalize_status(exp.get("status"))
    if status not in allowed:
        raise HTTPException(status_code=400, detail=message)
    return status


def _plan_payload(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **plan,
        "requires_confirmation": benchmark_service.plan_requires_confirmation(plan),
        "total_planned_calls": plan.get("total_planned_calls"),
        "estimated_cost": plan.get("estimated_cost"),
    }


async def _latest_plan_sorted(owner_id: str, experiment_id: str) -> Optional[Dict[str, Any]]:
    plans = await repo.benchmark_plans.list(
        {"owner_id": owner_id, "experiment_id": experiment_id},
        sort_field="created_at",
        descending=True,
        limit=1,
    )
    return plans[0] if plans else None


async def _force_experiment_status(owner_id: str, exp: Dict[str, Any], target: str) -> None:
    try:
        status = experiment_service.transition_status(exp, target)
    except Exception:
        status = target
    await repo.experiments.update(exp["id"], owner_id, {
        "status": status, "updated_at": now_iso(),
    })


async def _config_progress(
    owner_id: str, plan: Dict[str, Any], configs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    runs = await repo.runs.list(
        {
            "owner_id": owner_id,
            "experiment_id": plan["experiment_id"],
            "execution_kind": "benchmark",
            "benchmark_plan_id": plan["id"],
        },
        sort_field="created_at",
        descending=False,
        limit=10000,
    )
    per_config = []
    total_per_config = len(plan.get("case_ids") or []) * int(plan.get("repetitions") or 1)
    for config in configs:
        config_runs = [r for r in runs if r.get("configuration_id") == config["id"]]
        identities = {
            (r.get("dataset_case_id"), r.get("repetition_index")) for r in config_runs
        }
        per_config.append({
            "configuration_id": config["id"],
            "label": config.get("label"),
            "total": total_per_config,
            "completed": sum(1 for r in config_runs if r.get("status") == "success"),
            "failed": sum(1 for r in config_runs if r.get("status") == "error"),
            "attempted_cases": len({
                r.get("dataset_case_id") for r in config_runs if r.get("dataset_case_id")
            }),
            "pending": max(0, total_per_config - len(identities)),
        })
    return per_config


# ------------------------------------------------------------------ plan / run
@router.post("/{experiment_id}/benchmark/plan")
async def create_benchmark_plan(
    experiment_id: str, user: dict = Depends(get_current_user)
):
    exp = await _load_experiment(user["id"], experiment_id)
    _require_status(exp, {"READY"}, "Experiment must be READY to build a benchmark plan")
    try:
        plan = await benchmark_service.build_plan(user["id"], exp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _plan_payload(plan)


@router.post("/{experiment_id}/benchmark/start")
async def start_benchmark(
    experiment_id: str,
    body: BenchmarkStartRequest,
    user: dict = Depends(get_current_user),
):
    exp = await _load_experiment(user["id"], experiment_id)
    plan = await repo.benchmark_plans.get(body.plan_id, user["id"])
    if not plan or plan.get("experiment_id") != experiment_id:
        raise HTTPException(status_code=404, detail="Benchmark plan not found")
    if (plan.get("status") or "").lower() == "running":
        raise HTTPException(status_code=409, detail="Benchmark plan is already running")
    if (plan.get("status") or "").lower() in ("completed",):
        raise HTTPException(status_code=409, detail="Benchmark plan has already completed")
    _require_status(exp, {"READY"}, "Experiment must be READY to start a benchmark")

    try:
        benchmark_service.validate_start(plan, body.confirm, body.max_calls_override)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    now = now_iso()
    updated = await repo.benchmark_plans.update(plan["id"], user["id"], {
        "status": "running",
        "cancel_requested": False,
        "error_message": None,
        "started_at": plan.get("started_at") or now,
        "completed_at": None,
        "updated_at": now,
    })
    await _force_experiment_status(user["id"], exp, "RUNNING")
    asyncio.create_task(benchmark_service.run_benchmark(plan["id"], user["id"]))
    return _plan_payload(updated or plan)


@router.get("/{experiment_id}/benchmark")
async def get_benchmark(
    experiment_id: str, user: dict = Depends(get_current_user)
):
    exp = await _load_experiment(user["id"], experiment_id)
    plan = await _latest_plan_sorted(user["id"], experiment_id)
    if not plan:
        return {"plan": None, "progress": None, "configurations": []}
    configs = await experiment_service.get_configurations(user["id"], experiment_id)
    return {
        "plan": _plan_payload(plan),
        "progress": plan.get("progress"),
        "configurations": await _config_progress(user["id"], plan, configs),
    }


@router.post("/{experiment_id}/benchmark/cancel")
async def cancel_benchmark(
    experiment_id: str, user: dict = Depends(get_current_user)
):
    await _load_experiment(user["id"], experiment_id)
    plan = await _latest_plan_sorted(user["id"], experiment_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Benchmark plan not found")
    patch: Dict[str, Any] = {"cancel_requested": True, "updated_at": now_iso()}
    if (plan.get("status") or "").lower() != "running":
        patch["status"] = "cancelled"
        patch["completed_at"] = now_iso()
    updated = await repo.benchmark_plans.update(plan["id"], user["id"], patch)
    return _plan_payload(updated or plan)


@router.post("/{experiment_id}/benchmark/resume")
async def resume_benchmark(
    experiment_id: str, user: dict = Depends(get_current_user)
):
    exp = await _load_experiment(user["id"], experiment_id)
    plan = await _latest_plan_sorted(user["id"], experiment_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Benchmark plan not found")
    status = (plan.get("status") or "").lower()
    if status == "running":
        return _plan_payload(plan)
    if status == "completed":
        raise HTTPException(status_code=400, detail="Benchmark plan is already complete")

    now = now_iso()
    updated = await repo.benchmark_plans.update(plan["id"], user["id"], {
        "status": "running",
        "cancel_requested": False,
        "error_message": None,
        "completed_at": None,
        "updated_at": now,
    })
    await _force_experiment_status(user["id"], exp, "RUNNING")
    asyncio.create_task(benchmark_service.run_benchmark(plan["id"], user["id"]))
    return _plan_payload(updated or plan)


# ------------------------------------------------------------------ evaluation
@router.post("/{experiment_id}/evaluate")
async def evaluate(
    experiment_id: str,
    body: EvaluationRequest,
    user: dict = Depends(get_current_user),
):
    exp = await _load_experiment(user["id"], experiment_id)
    try:
        return await evaluation_service.evaluate_run(
            user["id"], exp, body.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{experiment_id}/evaluations")
async def list_evaluations(
    experiment_id: str,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    await _load_experiment(user["id"], experiment_id)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    return await evaluation_service.list_evaluations(
        user["id"], experiment_id, limit=limit, offset=offset
    )


# ------------------------------------------------------------------ aggregation
@router.get("/{experiment_id}/results")
async def results(
    experiment_id: str,
    include_cases: bool = False,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    exp = await _load_experiment(user["id"], experiment_id)
    summary = await evaluation_service.aggregate_results(user["id"], exp)
    if include_cases:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        configs = await experiment_service.get_configurations(user["id"], experiment_id)
        summary["cases"] = await evaluation_service.case_drilldown(
            user["id"], exp, configs, limit=limit, offset=offset
        )
    return summary


@router.get("/{experiment_id}/compare")
async def compare(
    experiment_id: str,
    baseline: str,
    candidate: str,
    user: dict = Depends(get_current_user),
):
    exp = await _load_experiment(user["id"], experiment_id)
    try:
        return await evaluation_service.compare_configurations(
            user["id"], exp, baseline, candidate
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ------------------------------------------------------------------ findings
@router.post("/{experiment_id}/findings")
async def create_finding_from_results(
    experiment_id: str,
    body: FindingFromResultsRequest,
    user: dict = Depends(get_current_user),
):
    exp = await _load_experiment(user["id"], experiment_id)
    try:
        return await evaluation_service.finding_from_results(
            user["id"], exp, body.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ------------------------------------------------------------------ regression
@router.post("/{experiment_id}/regression")
async def run_experiment_regression(
    experiment_id: str,
    body: RegressionRequest,
    user: dict = Depends(get_current_user),
):
    exp = await _load_experiment(user["id"], experiment_id)
    try:
        return await regression_service.run_regression(
            user["id"], exp, body.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{experiment_id}/regression")
async def list_experiment_regressions(
    experiment_id: str,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    await _load_experiment(user["id"], experiment_id)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    return await regression_service.list_regressions(
        user["id"], experiment_id, limit=limit, offset=offset
    )


@router.get("/{experiment_id}/regression/{regression_id}")
async def get_experiment_regression(
    experiment_id: str,
    regression_id: str,
    user: dict = Depends(get_current_user),
):
    await _load_experiment(user["id"], experiment_id)
    try:
        regression = await regression_service.get_regression(
            user["id"], regression_id
        )
    except regression_service.RegressionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if regression.get("experiment_id") != experiment_id:
        raise HTTPException(status_code=404, detail="Regression result not found")
    return regression
