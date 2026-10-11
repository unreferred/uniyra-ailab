"""Benchmark planning and the resumable, bounded benchmark runner.

Planning is deterministic: a plan pins the ordered configuration set, the frozen
dataset version's ordered cases and a repetition count, then hashes that into a
stable ``plan_hash``. Execution is idempotent per
``(dataset_case_id, configuration_id, repetition_index)``: every network
attempt is a separate Run tagged with ``attempt_index`` and an item is finished
once it succeeds or its retries are exhausted. Nothing here ever mutates legacy
Arena runs, and an unexpected error can never crash the server.
"""
import asyncio
import math
from typing import Any, Dict, List, Optional, Tuple

from .. import config
from .. import repository as repo
from ..database import db
from ..errors import is_retryable
from ..models import new_id, now_iso
from . import experiments as experiment_service
from .execution import execute_model

TERMINAL_PLAN_STATUSES = ("completed", "failed", "cancelled")


class BenchmarkError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


def _now() -> str:
    return now_iso()


# ------------------------------------------------------------------ estimate
def _estimate_cost(
    configs: List[Dict[str, Any]],
    cases: List[Dict[str, Any]],
    repetitions: int,
) -> Dict[str, Any]:
    """Heuristic cost range, computed only when every config is fully priced.

    Never fabricates a value for an unknown price: any unpriceable config turns
    the whole estimate unavailable and is counted instead.
    """
    unknown = 0
    per_call: List[Tuple[float, float]] = []
    for c in configs:
        pricing = c.get("pricing_snapshot") or {}
        in_price = pricing.get("input_price_per_1m")
        out_price = pricing.get("output_price_per_1m")
        max_tokens = c.get("max_tokens")
        if in_price is None or out_price is None or max_tokens is None:
            unknown += 1
            continue
        system_prompt = c.get("system_prompt") or ""
        for case in cases:
            input_tokens = math.ceil(
                len(system_prompt + (case.get("input_text") or "")) / 4
            )
            input_cost = (input_tokens / 1_000_000) * in_price
            output_cost = (max_tokens / 1_000_000) * out_price
            per_call.append((input_cost, input_cost + output_cost))

    if unknown:
        return {
            "available": False,
            "method": None,
            "currency": "USD",
            "min": None,
            "max": None,
            "unknown_pricing_config_count": unknown,
        }
    total_min = sum(pair[0] for pair in per_call) * max(0, repetitions)
    total_max = sum(pair[1] for pair in per_call) * max(0, repetitions)
    return {
        "available": True,
        "method": "heuristic_chars_div_4",
        "currency": "USD",
        "min": round(total_min, 6),
        "max": round(total_max, 6),
        "unknown_pricing_config_count": 0,
    }


def _initial_progress(total: int) -> Dict[str, int]:
    return {
        "total": int(total),
        "completed": 0,
        "failed": 0,
        "running": 0,
        "pending": int(total),
        "cancelled": 0,
        "skipped": 0,
    }


# ------------------------------------------------------------------ planning
async def build_plan(owner_id: str, experiment: Dict[str, Any]) -> Dict[str, Any]:
    """Build and persist a deterministic benchmark plan for an experiment."""
    experiment_id = experiment["id"]
    configs = await experiment_service.get_configurations(owner_id, experiment_id)
    if not configs:
        raise BenchmarkError("At least one configuration is required to build a benchmark plan")
    configs = sorted(configs, key=lambda c: (c.get("order", 0), c.get("id") or ""))

    protocol = experiment.get("protocol_snapshot") or experiment.get("protocol") or {}
    dataset_version_id = (
        protocol.get("dataset_version_id") or experiment.get("dataset_version_id")
    )
    if not dataset_version_id:
        raise BenchmarkError("A dataset version is required to build a benchmark plan")
    version = await repo.dataset_versions.get(dataset_version_id, owner_id)
    if not version:
        raise BenchmarkError("Dataset version was not found")
    if version.get("status") != "frozen":
        raise BenchmarkError("Dataset version must be frozen before building a plan")

    cases = await repo.dataset_version_cases.list(
        {"owner_id": owner_id, "dataset_version_id": dataset_version_id},
        sort_field="ordering",
        descending=False,
        limit=10000,
    )
    cases = sorted(cases, key=lambda c: (c.get("ordering", 0), c.get("id") or ""))

    repetitions = int(protocol.get("repetitions", 1) or 1)
    if repetitions < 1:
        repetitions = 1

    case_ids = [c["id"] for c in cases]
    configuration_ids = [c["id"] for c in configs]
    total = len(configs) * len(cases) * repetitions
    dataset_version_hash = version.get("content_hash") or experiment.get(
        "dataset_version_hash"
    )

    plan_hash = experiment_service.canonical_hash({
        "experiment_id": experiment_id,
        "dataset_version_id": dataset_version_id,
        "dataset_version_hash": dataset_version_hash,
        "configurations": [
            {"configuration_id": c["id"], "configuration_hash": c.get("configuration_hash")}
            for c in configs
        ],
        "repetitions": repetitions,
        "runner_version": config.RUNNER_VERSION,
        "schema_version": config.SCHEMA_VERSION,
    })

    # Asset-attachment vision gate (PLAN time): every config whose model must
    # read a case with image/file asset attachments has to be vision-capable.
    # Additive and best-effort: cases without asset ids pass through untouched.
    asset_cases = [
        case for case in cases
        if isinstance(case, dict) and list(case.get("asset_attachment_ids") or [])
    ]
    if asset_cases:
        try:
            from ..providers import model_capability_status
            from . import attachments as attachments_service
            incompatible: list = []
            for config_doc in configs:
                try:
                    model_doc = await repo.models.get(
                        config_doc.get("model_id"), owner_id
                    )
                except Exception:
                    model_doc = None
                try:
                    supported = (
                        model_capability_status(model_doc or {}, "vision") == "SUPPORTED"
                    )
                except Exception:
                    supported = False
                if not supported:
                    try:
                        needs_vision = False
                        for case in asset_cases:
                            docs = await attachments_service.validate_attachment_ids(
                                owner_id, experiment.get("project_id"),
                                list(case.get("asset_attachment_ids") or []),
                            )
                            if attachments_service.attachment_requires_vision(docs):
                                needs_vision = True
                                break
                    except Exception:
                        needs_vision = False
                    if needs_vision:
                        incompatible.append(
                            config_doc.get("label") or config_doc.get("id")
                        )
            if incompatible:
                raise BenchmarkError(
                    "Benchmark configs lack vision support for cases with image "
                    f"asset attachments: {', '.join(str(label) for label in incompatible)}"
                )
        except BenchmarkError:
            raise
        except Exception:
            pass

    estimated_cost = _estimate_cost(configs, cases, repetitions)
    now = _now()
    plan = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": experiment.get("project_id"),
        "experiment_id": experiment_id,
        "dataset_id": version.get("dataset_id") or experiment.get("dataset_id"),
        "dataset_version_id": dataset_version_id,
        "dataset_version_hash": dataset_version_hash,
        "configuration_ids": configuration_ids,
        "case_ids": case_ids,
        "repetitions": repetitions,
        "total_planned_calls": total,
        "plan_hash": plan_hash,
        "status": "planned",
        "cancel_requested": False,
        "runner_version": config.RUNNER_VERSION,
        "schema_version": config.SCHEMA_VERSION,
        "progress": _initial_progress(total),
        "estimated_cost": estimated_cost,
        "requires_confirmation": total > config.BENCHMARK_CONFIRM_THRESHOLD,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
    }
    await repo.benchmark_plans.insert(plan)
    return plan


def plan_requires_confirmation(plan: Dict[str, Any]) -> bool:
    """Evaluate against the *current* threshold so an operator can lower it live."""
    total = int(plan.get("total_planned_calls") or 0)
    return total > config.BENCHMARK_CONFIRM_THRESHOLD


def validate_start(
    plan: Dict[str, Any],
    confirm: bool = False,
    override_max: Optional[int] = None,
) -> None:
    """Raise BenchmarkError when a start request must be rejected."""
    total = int(plan.get("total_planned_calls") or 0)
    hard_cap = config.BENCHMARK_MAX_PLANNED_CALLS
    cap = hard_cap if override_max is None else min(int(override_max), hard_cap)
    if total > cap:
        raise BenchmarkError(
            f"This plan requires {total} calls, exceeding the maximum of {cap}"
        )
    if plan_requires_confirmation(plan) and not confirm:
        raise BenchmarkError(
            f"This plan will make {total} calls; set confirm=true to proceed"
        )


# ------------------------------------------------------------------ runner helpers
async def _inc_progress(plan_id: str, owner_id: str, deltas: Dict[str, int]) -> None:
    if not deltas:
        return
    inc = {f"progress.{key}": value for key, value in deltas.items()}
    await db.benchmark_plans.update_one(
        {"id": plan_id, "owner_id": owner_id},
        {"$inc": inc, "$set": {"updated_at": _now()}},
    )


async def _set_experiment_status(owner_id: str, exp: Dict[str, Any], target: str) -> None:
    """Transition when legal; fall back to a direct set so resume never stalls."""
    if not exp:
        return
    current = experiment_service.normalize_status(exp.get("status"))
    if current == target:
        return
    try:
        status = experiment_service.transition_status(exp, target)
    except Exception:
        status = target
    await repo.experiments.update(exp["id"], owner_id, {
        "status": status, "updated_at": _now(),
    })


async def _runs_for_item(
    owner_id: str, plan: Dict[str, Any], case_id: str, config_id: str, repetition: int
) -> List[Dict[str, Any]]:
    return await repo.runs.list(
        {
            "owner_id": owner_id,
            "experiment_id": plan["experiment_id"],
            "execution_kind": "benchmark",
            "dataset_case_id": case_id,
            "configuration_id": config_id,
            "repetition_index": repetition,
        },
        sort_field="created_at",
        descending=True,
        limit=100,
    )


def _terminal_run(runs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the run that proves an item is finished, if any.

    Finished means a success, a non-retryable failure, or a retryable failure
    whose attempt budget is exhausted.
    """
    if not runs:
        return None
    for run in runs:
        if run.get("status") == "success":
            return run
    latest = runs[0]
    if latest.get("status") == "error":
        if not is_retryable(latest.get("error")):
            return latest
        if int(latest.get("attempt_index") or 0) >= config.BENCHMARK_MAX_RETRIES:
            return latest
    return None


# ------------------------------------------------------------------ auto-eval
async def _auto_evaluate_runs(
    owner_id: str,
    plan: Dict[str, Any],
    experiment: Dict[str, Any],
    spec: Dict[str, Any],
) -> None:
    """Best-effort automatic evaluation of newly finished benchmark runs.

    Opt-in only: the caller checks ``protocol.evaluation.enabled``. Every run is
    wrapped defensively and the whole pass is wrapped again so a failing
    evaluator can never fail the benchmark. A run that already has any
    evaluation is left untouched.
    """
    try:
        from . import evaluation as evaluation_service
    except Exception:
        return
    try:
        runs = await repo.runs.list(
            {
                "owner_id": owner_id,
                "experiment_id": plan.get("experiment_id"),
                "execution_kind": "benchmark",
                "benchmark_plan_id": plan.get("id"),
            },
            sort_field="created_at",
            descending=False,
            limit=10000,
        )
    except Exception:
        return
    evaluator_type = spec.get("evaluator_type") or "rule"
    evaluator_keys = list(spec.get("evaluator_keys") or [])
    for run in runs:
        try:
            existing = await repo.evaluations.count(
                {"owner_id": owner_id, "run_id": run.get("id")}
            )
            if existing:
                continue
            await evaluation_service.evaluate_run(owner_id, experiment, {
                "run_id": run.get("id"),
                "evaluation_type": evaluator_type,
                "evaluator_keys": evaluator_keys,
                "rubric_id": spec.get("rubric_id"),
                "judge_model_id": spec.get("judge_model_id"),
                "threshold": spec.get("threshold"),
            })
        except Exception:
            continue


# ------------------------------------------------------------------ dispatch
async def _dispatch_benchmark_run(
    owner_id: str,
    plan: Dict[str, Any],
    experiment: Optional[Dict[str, Any]],
    config_doc: Dict[str, Any],
    case: Dict[str, Any],
    case_id: str,
    config_id: str,
    repetition: int,
    attempt_index: int,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute one benchmark item through the canonical runner path.

    Additive V6.8: a configuration that binds an agent version is dispatched to
    the existing agent runtime instead of a bare model call. Everything else
    keeps the original ``execute_model`` call byte-for-byte. Both branches
    return the same Run document shape the runner consumes (``id``/``status``).
    """
    agent_version_id = config_doc.get("agent_version_id")
    if agent_version_id:
        from . import agent_runtime

        dispatched = await agent_runtime.run_agent_version(
            owner_id,
            {
                "project_id": plan.get("project_id"),
                "agent_version_id": agent_version_id,
                # Prompt Arena: pin the configuration's prompt variant when the
                # config binds one; otherwise the agent version's prompt is used.
                "prompt_version_id": config_doc.get("prompt_version_id"),
                "task": case.get("input_text") or "",
            },
            experiment=experiment,
            experiment_id=plan["experiment_id"],
            configuration_id=config_id,
            dataset_case_id=case_id,
            dataset_version_id=plan.get("dataset_version_id"),
            repetition_index=repetition,
            benchmark_plan_id=plan.get("id"),
            attempt_index=attempt_index,
        )
        return dispatched.get("run") if isinstance(dispatched, dict) else dispatched

    return await execute_model(
        owner_id,
        config_doc.get("model_id"),
        config_doc.get("system_prompt") or "",
        [{"role": "user", "content": case.get("input_text") or ""}],
        params,
        experiment_id=plan["experiment_id"],
        context="dataset",
        project_id=plan.get("project_id"),
        execution_kind="benchmark",
        dataset_case_id=case_id,
        dataset_version_id=plan.get("dataset_version_id"),
        configuration_id=config_id,
        repetition_index=repetition,
        benchmark_plan_id=plan.get("id"),
        attempt_index=attempt_index,
        configuration_snapshot=config_doc,
        dataset_id=plan.get("dataset_id"),
        enabled_tool_ids=config_doc.get("enabled_tool_ids"),
        knowledge_enabled=config_doc.get("knowledge_enabled"),
        knowledge_collection_ids=config_doc.get("knowledge_collection_ids") or None,
        retrieval_options=config_doc.get("retrieval_options") or None,
        search_enabled=config_doc.get("search_enabled"),
        search_provider_id=config_doc.get("search_provider_id"),
        fetch_enabled=config_doc.get("fetch_enabled"),
        fetch_provider_id=config_doc.get("fetch_provider_id"),
    )


# ------------------------------------------------------------------ arena summary
async def _evaluation_outcomes(
    owner_id: str, experiment_id: str, configuration_id: str
) -> Dict[str, int]:
    """Per-configuration counts of evaluation result outcomes.

    Reuses the canonical evaluations collection; unknown outcomes fall back to
    ``NOT_EVALUATED`` and every outcome key is present (0 when unseen).
    """
    counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "NOT_EVALUATED": 0}
    try:
        pipeline = [
            {"$match": {
                "owner_id": owner_id,
                "experiment_id": experiment_id,
                "configuration_id": configuration_id,
            }},
            {"$group": {
                "_id": {"$ifNull": ["$result.outcome", "NOT_EVALUATED"]},
                "count": {"$sum": 1},
            }},
        ]
        rows = await db.evaluations.aggregate(pipeline).to_list(length=20)
    except Exception:  # pragma: no cover - read-only, defensive
        return counts
    for row in rows:
        key = row.get("_id")
        if key in counts:
            counts[key] = int(row.get("count") or 0)
    return counts


def _violation_counts(
    failures: List[Dict[str, Any]], config_id: str, run_config: Dict[str, str]
) -> Tuple[int, int]:
    """Count tool-selection and policy-compliance failures for one config."""
    tool_violations = 0
    policy_violations = 0
    for failure in failures:
        if not isinstance(failure, dict):
            continue
        if run_config.get(failure.get("run_id")) != config_id:
            continue
        code = failure.get("taxonomy_code") or ""
        category = failure.get("category") or ""
        if code == "QUALITY.TOOL_SELECTION" or category == "TOOL_SELECTION_ERROR":
            tool_violations += 1
        if code == "QUALITY.POLICY_COMPLIANCE" or (
            category == "INSTRUCTION_VIOLATION" and failure.get("source") == "RULE"
        ):
            policy_violations += 1
    return tool_violations, policy_violations


async def arena_summary(
    owner_id: str, experiment: Dict[str, Any]
) -> Dict[str, Any]:
    """Truthful per-configuration metrics for an agent/prompt arena.

    Builds on the canonical V5 aggregation (``aggregate_configuration``) and the
    append-only failures collection; it never re-derives scores and never coerces
    an unknown telemetry value to 0 (unknown stays ``None``).
    """
    from . import evaluation as evaluation_service

    experiment_id = experiment["id"]
    configs = await experiment_service.get_configurations(owner_id, experiment_id)
    runs = await repo.runs.list(
        {
            "owner_id": owner_id,
            "experiment_id": experiment_id,
            "execution_kind": "benchmark",
        },
        sort_field="created_at",
        descending=False,
        limit=10000,
    )
    run_config = {
        run.get("id"): run.get("configuration_id")
        for run in runs
        if run.get("id")
    }
    try:
        failures = await repo.failures.list(
            {"owner_id": owner_id, "experiment_id": experiment_id},
            sort_field="created_at",
            descending=False,
            limit=5000,
        )
    except Exception:  # pragma: no cover - defensive
        failures = []
    plan = await repo.benchmark_plans.find_one(
        {"owner_id": owner_id, "experiment_id": experiment_id}
    ) or {}

    summaries: List[Dict[str, Any]] = []
    for config in configs:
        agg = await evaluation_service.aggregate_configuration(
            owner_id, experiment_id, config
        )
        tool_violations, policy_violations = _violation_counts(
            failures, config["id"], run_config
        )
        input_tokens = agg.get("total_input_tokens")
        output_tokens = agg.get("total_output_tokens")
        total_tokens = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )
        run_count = int(agg.get("attempted_runs") or 0)
        failed = int(agg.get("failed") or 0)
        summaries.append({
            "configuration_id": config.get("id"),
            "label": config.get("label"),
            "model_id": config.get("model_id"),
            "agent_version_id": config.get("agent_version_id"),
            "prompt_version_id": config.get("prompt_version_id"),
            "run_count": run_count,
            "completed": int(agg.get("completed") or 0),
            "failed": failed,
            "completion_rate": agg.get("success_rate"),
            "failure_rate": (
                round(failed / run_count, 6) if run_count > 0 else None
            ),
            "avg_score": agg.get("avg_score"),
            "median_score": agg.get("median_score"),
            "evaluated_count": agg.get("evaluated_count"),
            "latency_ms": {
                "avg": agg.get("avg_latency_ms"),
                "median": agg.get("median_latency_ms"),
                "p95": agg.get("p95_latency_ms"),
                "sample_count": agg.get("latency_sample_count"),
            },
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost": agg.get("known_cost_total"),
            "tool_violations": tool_violations,
            "policy_violations": policy_violations,
            "evaluation_outcomes": await _evaluation_outcomes(
                owner_id, experiment_id, config["id"]
            ),
        })

    protocol = experiment.get("protocol_snapshot") or experiment.get("protocol") or {}
    return {
        "experiment_id": experiment_id,
        "experiment_type": experiment.get("experiment_type"),
        "title": experiment.get("title"),
        "status": experiment.get("status"),
        "protocol": protocol,
        "plan_status": plan.get("status"),
        "total_planned_calls": plan.get("total_planned_calls"),
        "configurations": summaries,
    }


# ------------------------------------------------------------------ runner
async def run_benchmark(plan_id: str, owner_id: str) -> None:
    """Execute a persisted plan with bounded concurrency, retries and cancellation."""
    plan: Optional[Dict[str, Any]] = None
    try:
        plan = await repo.benchmark_plans.get(plan_id, owner_id)
        if not plan:
            return
        if (plan.get("status") or "").lower() in TERMINAL_PLAN_STATUSES:
            return

        total_planned = int(plan.get("total_planned_calls") or 0)
        if total_planned > config.BENCHMARK_MAX_PLANNED_CALLS:
            await repo.benchmark_plans.update(plan_id, owner_id, {
                "status": "failed",
                "completed_at": _now(),
                "updated_at": _now(),
                "error_message": (
                    f"Plan requires {total_planned} calls, exceeding the maximum of "
                    f"{config.BENCHMARK_MAX_PLANNED_CALLS}"
                ),
            })
            return

        now = _now()
        await repo.benchmark_plans.update(plan_id, owner_id, {
            "status": "running",
            "started_at": plan.get("started_at") or now,
            "updated_at": now,
            "error_message": None,
        })
        plan = await repo.benchmark_plans.get(plan_id, owner_id) or plan

        experiment = await repo.experiments.get(plan["experiment_id"], owner_id)
        await _set_experiment_status(owner_id, experiment, "RUNNING")

        configs = await experiment_service.get_configurations(owner_id, plan["experiment_id"])
        config_by_id = {c["id"]: c for c in configs}
        cases = await repo.dataset_version_cases.list(
            {"owner_id": owner_id, "dataset_version_id": plan.get("dataset_version_id")},
            sort_field="ordering",
            descending=False,
            limit=10000,
        )
        case_by_id = {c["id"]: c for c in cases}

        matrix: List[Tuple[str, str, int]] = []
        for config_id in plan.get("configuration_ids") or []:
            for case_id in plan.get("case_ids") or []:
                for repetition in range(int(plan.get("repetitions") or 1)):
                    matrix.append((case_id, config_id, repetition))
        total = len(matrix)

        lock = asyncio.Lock()
        cancel_event = asyncio.Event()
        state: Dict[str, Any] = {"any_success": False, "cancelled": False}

        async def bump(deltas: Dict[str, int]) -> None:
            async with lock:
                await _inc_progress(plan_id, owner_id, deltas)

        async def process_item(case_id: str, config_id: str, repetition: int) -> None:
            config_doc = config_by_id.get(config_id)
            case = case_by_id.get(case_id)
            if config_doc is None or case is None:
                await bump({"failed": 1, "pending": -1})
                return

            prior_runs = await _runs_for_item(
                owner_id, plan, case_id, config_id, repetition
            )
            terminal = _terminal_run(prior_runs)
            if terminal is not None:
                if terminal.get("status") == "success":
                    state["any_success"] = True
                # Already reflected in the persisted progress when it was first
                # run; decrementing pending again would drive it negative on
                # resume. Only record the skip.
                await bump({"skipped": 1})
                return

            attempt_index = (
                int(prior_runs[0].get("attempt_index") or 0) + 1 if prior_runs else 0
            )
            params = {
                key: config_doc.get(key)
                for key in ("temperature", "max_tokens", "top_p")
                if config_doc.get(key) is not None
            }
            await bump({"running": 1, "pending": -1})
            try:
                while True:
                    run = await _dispatch_benchmark_run(
                        owner_id,
                        plan,
                        experiment,
                        config_doc,
                        case,
                        case_id,
                        config_id,
                        repetition,
                        attempt_index,
                        params,
                    )
                    if run.get("status") == "success":
                        state["any_success"] = True
                        await bump({"completed": 1, "running": -1})
                        return
                    if (
                        is_retryable(run.get("error"))
                        and attempt_index < config.BENCHMARK_MAX_RETRIES
                    ):
                        await asyncio.sleep(
                            config.BENCHMARK_RETRY_BACKOFF_SECONDS * (attempt_index + 1)
                        )
                        attempt_index += 1
                        continue
                    await bump({"failed": 1, "running": -1})
                    return
            except Exception:
                await bump({"failed": 1, "running": -1})

        queue: "asyncio.Queue[Tuple[str, str, int]]" = asyncio.Queue()
        for item in matrix:
            queue.put_nowait(item)
        concurrency = max(
            1, min(config.BENCHMARK_CONCURRENCY, config.BENCHMARK_MAX_CONCURRENCY)
        )

        async def worker() -> None:
            while True:
                if cancel_event.is_set():
                    return
                fresh = await repo.benchmark_plans.get(plan_id, owner_id)
                if fresh and fresh.get("cancel_requested"):
                    cancel_event.set()
                    return
                try:
                    case_id, config_id, repetition = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await process_item(case_id, config_id, repetition)

        await asyncio.gather(*[asyncio.create_task(worker()) for _ in range(concurrency)])

        fresh = await repo.benchmark_plans.get(plan_id, owner_id) or plan
        if cancel_event.is_set():
            state["cancelled"] = True
            pending = int((fresh.get("progress") or {}).get("pending", 0) or 0)
            await db.benchmark_plans.update_one(
                {"id": plan_id, "owner_id": owner_id},
                {"$inc": {"progress.cancelled": pending},
                 "$set": {"progress.pending": 0, "updated_at": _now()}},
            )
            final_status = "cancelled"
        else:
            final_status = "completed" if state["any_success"] else "failed"

        protocol = (experiment or {}).get("protocol_snapshot") or (
            experiment or {}
        ).get("protocol") or {}
        eval_spec = protocol.get("evaluation") or {}
        if eval_spec.get("enabled"):
            await _auto_evaluate_runs(owner_id, plan, experiment or {}, eval_spec)

        await repo.benchmark_plans.update(plan_id, owner_id, {
            "status": final_status,
            "completed_at": _now(),
            "updated_at": _now(),
        })
        await _set_experiment_status(
            owner_id,
            await repo.experiments.get(plan["experiment_id"], owner_id),
            "COMPLETED" if state["any_success"] else "FAILED",
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        try:
            await repo.benchmark_plans.update(plan_id, owner_id, {
                "status": "failed",
                "completed_at": _now(),
                "updated_at": _now(),
                "error_message": f"Benchmark runner error: {type(exc).__name__}",
            })
            if plan:
                await _set_experiment_status(
                    owner_id,
                    await repo.experiments.get(plan["experiment_id"], owner_id),
                    "FAILED",
                )
        except Exception:
            pass
