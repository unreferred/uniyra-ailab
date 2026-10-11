"""V6.8 Agent Arena & Prompt Arena.

Both arenas are thin builders over the EXISTING protocol pipeline: a Dataset
version plus an ordered configuration matrix compile down to an ordinary
experiment, which is then frozen with ``experiments.ready_experiment`` and run
by the one and only ``benchmark.run_benchmark`` runner. There is deliberately no
parallel benchmark engine, no parallel evaluation funnel and no adaptive
routing: an agent-bound configuration always uses its frozen agent version's
fixed model binding.

Agent Arena compares frozen agent versions (optionally crossed with a list of
models, all still fixed per configuration). Prompt Arena compares frozen prompt
versions under one fixed model. Errors are owner/project scoped:
:class:`ArenaV6NotFound` -> HTTP 404, :class:`ArenaV6Error` -> HTTP 400.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..models import (
    AGENT_ARENA_EXPERIMENT_TYPE,
    ARENA_EXPERIMENT_TYPES,
    PROMPT_ARENA_EXPERIMENT_TYPE,
    RULE_EVALUATOR_KEYS,
)
from . import benchmark as benchmark_service
from . import experiments as experiment_service


class ArenaV6Error(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class ArenaV6NotFound(ArenaV6Error):
    """Owner-scoped resource missing (HTTP 404)."""


def _as_dict(item: Any) -> Dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return dict(item or {})


def _unique_label(base: str, used: set) -> str:
    label = (base or "configuration").strip() or "configuration"
    candidate = label
    suffix = 2
    while candidate in used:
        candidate = f"{label} #{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


async def _require_project(owner_id: str, project_id: Optional[str]) -> str:
    if not project_id or not await repo.projects.get(project_id, owner_id):
        raise ArenaV6Error("Project not found")
    return project_id


async def _require_frozen_dataset_version(
    owner_id: str, project_id: str, dataset_id: str, dataset_version_id: str
) -> Dict[str, Any]:
    if not dataset_id or not dataset_version_id:
        raise ArenaV6Error("A dataset and a frozen dataset version are required")
    dataset = await repo.datasets.get(dataset_id, owner_id)
    if not dataset:
        raise ArenaV6Error("Dataset was not found")
    if dataset.get("project_id") not in (None, project_id):
        raise ArenaV6Error("Dataset belongs to a different project")
    version = await repo.dataset_versions.get(dataset_version_id, owner_id)
    if not version or version.get("dataset_id") != dataset_id:
        raise ArenaV6Error("Dataset version was not found for this dataset")
    if version.get("project_id") not in (None, project_id):
        raise ArenaV6Error("Dataset version belongs to a different project")
    if version.get("status") != "frozen":
        raise ArenaV6Error("Dataset version must be frozen before creating an arena")
    return version


async def _load_agent_version(
    owner_id: str, project_id: str, agent_version_id: str
) -> Dict[str, Any]:
    version = await repo.agent_versions.get(agent_version_id, owner_id)
    if not version:
        raise ArenaV6Error(f"Agent version '{agent_version_id}' was not found")
    if version.get("status") not in ("frozen", "deprecated"):
        raise ArenaV6Error(
            "Agent version must be frozen before it can be used in an arena"
        )
    if version.get("project_id") not in (None, project_id):
        raise ArenaV6Error("Agent version belongs to a different project")
    if not version.get("model_id"):
        raise ArenaV6Error("Agent version has no fixed model binding")
    return version


async def _load_prompt_version(
    owner_id: str, project_id: str, prompt_version_id: str
) -> Dict[str, Any]:
    version = await repo.prompt_versions.get(prompt_version_id, owner_id)
    if not version:
        raise ArenaV6Error(f"Prompt version '{prompt_version_id}' was not found")
    if version.get("status") not in ("frozen", "deprecated"):
        raise ArenaV6Error(
            "Prompt version must be frozen before it can be used in an arena"
        )
    if version.get("project_id") not in (None, project_id):
        raise ArenaV6Error("Prompt version belongs to a different project")
    return version


def _evaluation_spec(body: Dict[str, Any]) -> Dict[str, Any]:
    keys = list(body.get("evaluation_keys") or [])
    rubric_id = body.get("rubric_id")
    judge_model_id = body.get("judge_model_id")
    return {
        "enabled": bool(keys or rubric_id or judge_model_id),
        "evaluator_type": "rule",
        "evaluator_keys": keys,
        "rubric_id": rubric_id,
        "judge_model_id": judge_model_id,
    }


def _protocol(
    body: Dict[str, Any], dataset_id: str, dataset_version_id: str
) -> Dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "dataset_version_id": dataset_version_id,
        "repetitions": int(body.get("repetitions") or 1),
        "evaluation": _evaluation_spec(body),
    }


async def _finish(
    owner_id: str,
    body: Dict[str, Any],
    dataset_id: str,
    dataset_version_id: str,
    experiment_type: str,
    configurations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    experiment = await experiment_service.create_experiment(
        owner_id,
        body.get("project_id"),
        body.get("title") or "Arena",
        objective=body.get("objective") or "",
        hypothesis=body.get("hypothesis") or "",
        experiment_type=experiment_type,
        protocol=_protocol(body, dataset_id, dataset_version_id),
        configurations=configurations,
    )
    ready = await experiment_service.ready_experiment(owner_id, experiment)
    # Mirror the experiments detail shape (frozen configurations + hashes) so a
    # caller can immediately inspect the frozen matrix.
    frozen = await experiment_service.get_configurations(owner_id, ready["id"])
    payload = dict(ready)
    payload["configurations"] = frozen
    payload["hashes"] = {
        "configuration_hash": ready.get("configuration_hash"),
        "protocol_hash": ready.get("protocol_hash"),
        "dataset_version_hash": ready.get("dataset_version_hash"),
    }
    return payload


async def create_agent_arena(owner_id: str, body: Any) -> Dict[str, Any]:
    """Build + freeze an agent arena experiment from frozen agent versions."""
    data = _as_dict(body)
    project_id = await _require_project(owner_id, data.get("project_id"))
    await _require_frozen_dataset_version(
        owner_id,
        project_id,
        data.get("dataset_id"),
        data.get("dataset_version_id"),
    )

    agent_version_ids = list(data.get("agent_version_ids") or [])
    if not agent_version_ids:
        raise ArenaV6Error("At least one agent version is required")
    model_ids = list(data.get("model_ids") or [])

    used: set = set()
    configurations: List[Dict[str, Any]] = []
    order = 0
    for agent_version_id in agent_version_ids:
        version = await _load_agent_version(owner_id, project_id, agent_version_id)
        agent = await repo.agents.get(version.get("agent_id"), owner_id)
        agent_name = (
            (agent or {}).get("name")
            or version.get("name")
            or f"agent-{version.get('agent_id')}"
        )
        # Fixed model binding: absent an explicit model matrix, each
        # configuration uses the agent version's bound model.
        bound_models = model_ids or [version.get("model_id")]
        for model_id in bound_models:
            model = await repo.models.get(model_id, owner_id)
            if not model:
                raise ArenaV6Error(f"Model '{model_id}' was not found")
            model_name = model.get("display_name") or model_id
            label = _unique_label(f"{agent_name} · {model_name}", used)
            configurations.append({
                "label": label,
                "model_id": model_id,
                "agent_version_id": agent_version_id,
                "order": order,
            })
            order += 1

    return await _finish(
        owner_id,
        data,
        data.get("dataset_id"),
        data.get("dataset_version_id"),
        AGENT_ARENA_EXPERIMENT_TYPE,
        configurations,
    )


async def create_prompt_arena(owner_id: str, body: Any) -> Dict[str, Any]:
    """Build + freeze a prompt arena experiment from frozen prompt versions.

    Every configuration shares the same fixed ``model_id`` (and optional
    ``agent_version_id``) and differs ONLY by ``prompt_version_id``.
    """
    data = _as_dict(body)
    project_id = await _require_project(owner_id, data.get("project_id"))
    await _require_frozen_dataset_version(
        owner_id,
        project_id,
        data.get("dataset_id"),
        data.get("dataset_version_id"),
    )

    model_id = data.get("model_id")
    if not model_id:
        raise ArenaV6Error("model_id is required")
    if not await repo.models.get(model_id, owner_id):
        raise ArenaV6Error(f"Model '{model_id}' was not found")

    agent_version_id = data.get("agent_version_id")
    if agent_version_id:
        await _load_agent_version(owner_id, project_id, agent_version_id)

    prompt_version_ids = list(data.get("prompt_version_ids") or [])
    if not prompt_version_ids:
        raise ArenaV6Error("At least one prompt version is required")

    used: set = set()
    configurations: List[Dict[str, Any]] = []
    order = 0
    for prompt_version_id in prompt_version_ids:
        version = await _load_prompt_version(owner_id, project_id, prompt_version_id)
        prompt = await repo.prompts.get(version.get("prompt_id"), owner_id)
        prompt_name = (
            (prompt or {}).get("name")
            or version.get("name")
            or f"prompt-{version.get('prompt_id')}"
        )
        label = _unique_label(
            f"{prompt_name} · v{version.get('version')}", used
        )
        config = {
            "label": label,
            "model_id": model_id,
            "prompt_version_id": prompt_version_id,
            "order": order,
        }
        if agent_version_id:
            config["agent_version_id"] = agent_version_id
        configurations.append(config)
        order += 1

    return await _finish(
        owner_id,
        data,
        data.get("dataset_id"),
        data.get("dataset_version_id"),
        PROMPT_ARENA_EXPERIMENT_TYPE,
        configurations,
    )


async def list_arenas(
    owner_id: str,
    project_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Owner-scoped, newest-first listing of arena experiments."""
    query: Dict[str, Any] = {
        "owner_id": owner_id,
        "experiment_type": {"$in": list(ARENA_EXPERIMENT_TYPES)},
    }
    if project_id:
        query["project_id"] = project_id
    limit = max(int(limit), 1)
    offset = max(int(offset), 0)
    total = await repo.experiments.count(query)
    docs = await repo.experiments.list(
        query,
        sort_field="created_at",
        descending=True,
        limit=limit,
        offset=offset,
    )
    items = [
        {
            "id": doc.get("id"),
            "title": doc.get("title"),
            "experiment_type": doc.get("experiment_type"),
            "project_id": doc.get("project_id"),
            "status": doc.get("status"),
            "dataset_id": doc.get("dataset_id"),
            "dataset_version_id": doc.get("dataset_version_id"),
            "configurations_count": doc.get("configurations_count"),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        }
        for doc in docs
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def arena_summary(owner_id: str, experiment_id: str) -> Dict[str, Any]:
    """Per-configuration arena metrics plus experiment meta."""
    experiment = await repo.experiments.get(experiment_id, owner_id)
    if not experiment:
        raise ArenaV6NotFound("Arena experiment not found")
    if experiment.get("experiment_type") not in ARENA_EXPERIMENT_TYPES:
        raise ArenaV6NotFound("Experiment is not an arena")
    summary = await benchmark_service.arena_summary(owner_id, experiment)
    summary["experiment"] = {
        "id": experiment.get("id"),
        "title": experiment.get("title"),
        "objective": experiment.get("objective"),
        "hypothesis": experiment.get("hypothesis"),
        "experiment_type": experiment.get("experiment_type"),
        "status": experiment.get("status"),
        "project_id": experiment.get("project_id"),
        "dataset_id": experiment.get("dataset_id"),
        "dataset_version_id": experiment.get("dataset_version_id"),
        "configurations_count": experiment.get("configurations_count"),
        "hashes": {
            "configuration_hash": experiment.get("configuration_hash"),
            "protocol_hash": experiment.get("protocol_hash"),
            "dataset_version_hash": experiment.get("dataset_version_hash"),
        },
        "created_at": experiment.get("created_at"),
        "updated_at": experiment.get("updated_at"),
    }
    return summary


def catalog() -> Dict[str, Any]:
    """Static arena catalog: types, supported evaluation keys, binding policy."""
    return {
        "arena_types": list(ARENA_EXPERIMENT_TYPES),
        "evaluation_keys": list(RULE_EVALUATOR_KEYS),
        "model_binding": "fixed",
        "note": (
            "Arena configurations reuse the existing benchmark runner and "
            "evaluation funnel; model binding is fixed per configuration "
            "(no adaptive routing)."
        ),
    }
