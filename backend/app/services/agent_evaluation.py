"""V6.7 Agent & Prompt Evaluation.

This module is a thin orchestration layer over the canonical V5 evaluation
framework. It does NOT introduce a second evaluator database, a second evaluator
implementation, or a parallel judge: every evaluation is produced by
:func:`app.services.evaluation.evaluate_run`, which persists the one canonical
evaluation document, uses :mod:`app.services.evaluators` for rule keys and
:mod:`app.services.judge_system` (tools disabled) for judge scoring.

Separation is preserved end to end:

* deterministic / rule results are stored exactly as V5 stores them;
* judge-derived results are stored as a distinct ``judge_model`` evaluation;
* manual human scores are never synthesised here.

An evaluator that cannot observe its inputs stays ``NOT_EVALUATED`` -- it is
never turned into a PASS.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..models import RULE_EVALUATOR_KEYS
from . import evaluation as evaluation_service
from . import redaction
from . import tracing

__all__ = [
    "AgentEvaluationError",
    "AgentEvaluationNotFound",
    "DEFAULT_AGENT_RULE_KEYS",
    "DEFAULT_PROMPT_RULE_KEYS",
    "AGENT_DIMENSIONS",
    "PROMPT_DIMENSIONS",
    "evaluate_agent_run",
    "list_run_agent_evaluations",
    "catalog",
]


class AgentEvaluationError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class AgentEvaluationNotFound(AgentEvaluationError):
    """Owner-scoped resource missing (HTTP 404)."""


DEFAULT_AGENT_RULE_KEYS = (
    "assertions",
    "tool_policy",
    "knowledge_grounding",
    "agent_policy_compliance",
    "agent_invariant_compliance",
    "agent_tool_discipline",
    "agent_context_usage",
)

DEFAULT_PROMPT_RULE_KEYS = (
    "assertions",
    "knowledge_grounding",
    "tool_policy",
)

AGENT_DIMENSIONS = (
    "role_adherence",
    "scope_adherence",
    "policy_compliance",
    "invariant_compliance",
    "task_completion",
    "tool_discipline",
    "context_usage",
)

PROMPT_DIMENSIONS = (
    "instruction_clarity",
    "constraint_adherence",
    "output_consistency",
    "task_completion",
    "drift_rate",
    "token_efficiency",
    "model_portability",
)

_SUPPORTED_RULE_KEYS = frozenset(RULE_EVALUATOR_KEYS)


def _defaults_for(profile_type: Any) -> tuple:
    return (
        DEFAULT_PROMPT_RULE_KEYS
        if str(profile_type or "agent").strip().lower() == "prompt"
        else DEFAULT_AGENT_RULE_KEYS
    )


def _resolve_rule_keys(
    version: Dict[str, Any], body: Dict[str, Any]
) -> List[str]:
    """Resolve the rule keys to run, filtered to supported keys only."""
    requested = [
        key for key in (body.get("evaluator_keys") or []) if isinstance(key, str)
    ]
    profile = version.get("evaluation_profile")
    profile = profile if isinstance(profile, dict) else {}

    if not requested and body.get("use_agent_profile", True):
        requested = [
            key for key in (profile.get("evaluator_keys") or [])
            if isinstance(key, str)
        ]
    if not requested:
        requested = list(_defaults_for(body.get("profile_type")))

    selected: List[str] = []
    for key in requested:
        if key in _SUPPORTED_RULE_KEYS and key not in selected:
            selected.append(key)
    return selected


async def _load_context(
    owner_id: str, run_id: str
) -> tuple:
    run = await repo.runs.get(run_id, owner_id)
    if not run:
        raise AgentEvaluationNotFound("Agent run not found")
    if not run.get("agent_version_id"):
        raise AgentEvaluationError("Run has no agent provenance")
    version = await repo.agent_versions.get(run.get("agent_version_id"), owner_id)
    if not version:
        raise AgentEvaluationNotFound("Agent version not found")
    agent_id = run.get("agent_id") or version.get("agent_id")
    agent = await repo.agents.get(agent_id, owner_id) if agent_id else None
    if not agent or agent.get("id") != version.get("agent_id"):
        raise AgentEvaluationNotFound("Agent not found")
    experiment = None
    if run.get("experiment_id"):
        experiment = await repo.experiments.get(run.get("experiment_id"), owner_id)
    return run, agent, version, experiment


async def _record_agent_evaluation_span(
    owner_id: str,
    run: Dict[str, Any],
    *,
    rule_evaluation: Optional[Dict[str, Any]],
    judge_evaluation: Optional[Dict[str, Any]],
    rule_keys: List[str],
) -> None:
    """Best-effort AGENT_EVALUATION span on the evaluated run's trace."""
    trace_id = run.get("trace_id")
    if not trace_id:
        return
    evaluation_ids = [
        evaluation.get("id")
        for evaluation in (rule_evaluation, judge_evaluation)
        if isinstance(evaluation, dict) and evaluation.get("id")
    ]
    try:
        started = time.perf_counter()
        span = await tracing.start_span(
            owner_id,
            trace_id,
            "AGENT_EVALUATION",
            "agent_evaluation",
            run_id=run.get("id"),
            metadata=redaction.redact({
                "evaluation_ids": evaluation_ids,
                "rule_keys": list(rule_keys),
                "judge_used": judge_evaluation is not None,
                "rule_outcome": (
                    (rule_evaluation or {}).get("result", {}).get("outcome")
                ),
                "judge_outcome": (
                    (judge_evaluation or {}).get("result", {}).get("outcome")
                ),
            }),
        )
        await tracing.finish_span(span, status="completed", started_perf=started)
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass


async def evaluate_agent_run(
    owner_id: str, run_id: str, body: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Evaluate one agent run via the canonical V5 evaluation framework."""
    body = body if isinstance(body, dict) else {}
    run, agent, version, experiment = await _load_context(owner_id, run_id)

    profile = version.get("evaluation_profile")
    profile = profile if isinstance(profile, dict) else {}
    profile_type = body.get("profile_type") or "agent"

    rule_keys = _resolve_rule_keys(version, body)
    record_failures = bool(body.get("record_failures"))
    threshold = body.get("threshold")
    notes = body.get("notes") or ""

    rule_evaluation = await evaluation_service.evaluate_run(
        owner_id,
        experiment,
        {
            "run_id": run_id,
            "evaluator_type": "rule",
            "evaluator_keys": rule_keys,
            "threshold": threshold,
            "record_failures": record_failures,
            "notes": notes,
        },
    )

    judge_model_id = body.get("judge_model_id") or profile.get("judge_model_id")
    rubric_id = body.get("rubric_id") or profile.get("rubric_id")
    judge_evaluation: Optional[Dict[str, Any]] = None
    if judge_model_id:
        # Always the canonical judge path (judge_system.evaluate_with_judge),
        # which disables tools. No separate judge implementation exists.
        judge_evaluation = await evaluation_service.evaluate_run(
            owner_id,
            experiment,
            {
                "run_id": run_id,
                "evaluator_type": "judge_model",
                "rubric_id": rubric_id,
                "judge_model_id": judge_model_id,
                "threshold": threshold,
                "record_failures": record_failures,
                "notes": notes,
            },
        )

    await _record_agent_evaluation_span(
        owner_id,
        run,
        rule_evaluation=rule_evaluation,
        judge_evaluation=judge_evaluation,
        rule_keys=rule_keys,
    )

    evaluations = [
        evaluation
        for evaluation in (rule_evaluation, judge_evaluation)
        if isinstance(evaluation, dict)
    ]
    return {
        "run_id": run_id,
        "evaluations": evaluations,
        "rule_evaluation": rule_evaluation,
        "judge_evaluation": judge_evaluation,
        "profile_type": profile_type,
    }


async def list_run_agent_evaluations(
    owner_id: str, run_id: str, limit: int = 50, offset: int = 0
) -> Dict[str, Any]:
    """Owner + run scoped evaluations, newest first."""
    run = await repo.runs.get(run_id, owner_id)
    if not run:
        raise AgentEvaluationNotFound("Agent run not found")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    query = {"owner_id": owner_id, "run_id": run_id}
    total = await repo.evaluations.count(query)
    items = await repo.evaluations.list(
        query, sort_field="created_at", descending=True, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def catalog() -> Dict[str, Any]:
    """Static vocabulary for the agent/prompt evaluation surface."""
    return {
        "agent_rule_keys": list(DEFAULT_AGENT_RULE_KEYS),
        "prompt_rule_keys": list(DEFAULT_PROMPT_RULE_KEYS),
        "agent_dimensions": list(AGENT_DIMENSIONS),
        "prompt_dimensions": list(PROMPT_DIMENSIONS),
    }
