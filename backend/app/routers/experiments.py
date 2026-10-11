from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..deps import get_current_user
from ..models import (ExperimentCreateRequest, ExperimentUpdateRequest,
                      HumanEvalRequest, JudgeRequest, new_id, now_iso)
from ..services import blind_experiment_view, blind_run_view, run_arena, run_judge
from ..services import experiments as experiment_service

router = APIRouter(prefix="/experiments", tags=["experiments"])

# Identity-bearing evaluation fields that must not leak before a blind reveal.
_REDACTED_EVAL_FIELDS = ("model_id", "model_name", "judge_model_id", "judge_model_name")


def _redact_evaluation(evaluation: dict) -> dict:
    return {k: v for k, v in evaluation.items() if k not in _REDACTED_EVAL_FIELDS}


async def _ensure_reveal_event(owner_id: str, exp: dict) -> dict:
    """Return the reveal event for an experiment, creating it at most once."""
    query = {"owner_id": owner_id, "experiment_id": exp["id"]}
    existing = await repo.reveal_events.find_one(query)
    if existing:
        return existing
    event = {
        "id": new_id(),
        "owner_id": owner_id,
        "experiment_id": exp["id"],
        "project_id": exp.get("project_id"),
        "revealed_at": now_iso(),
        "created_at": now_iso(),
    }
    try:
        await repo.reveal_events.insert(event)
    except Exception:
        # Concurrent reveal race: reuse whatever landed first.
        existing = await repo.reveal_events.find_one(query)
        if existing:
            return existing
        raise
    return event


async def _reveal_experiment(owner_id: str, exp: dict) -> tuple[dict, dict]:
    """Persist the reveal event and stamp the experiment (idempotently)."""
    event = await _ensure_reveal_event(owner_id, exp)
    if not exp.get("revealed_at"):
        updated = await repo.experiments.update(exp["id"], owner_id, {
            "revealed_at": event["revealed_at"], "revealed_by": owner_id,
        })
        if updated:
            exp = updated
    return exp, event


async def _combined_extras(owner_id: str, exp: dict, include_configurations: bool = True) -> dict:
    """Protocol/config/hash/benchmark-plan extras for the combined detail shape.

    Works for both legacy Arena experiments (everything is empty/None) and
    protocol experiments; blind views must not leak configurations, so callers
    pass ``include_configurations=False`` before a reveal.
    """
    configurations = []
    if include_configurations:
        configurations = await experiment_service.get_configurations(owner_id, exp["id"])
    benchmark_plan = await repo.benchmark_plans.find_one(
        {"owner_id": owner_id, "experiment_id": exp["id"]}
    )
    return {
        "configurations": configurations,
        "protocol": exp.get("protocol_snapshot") or exp.get("protocol"),
        "hashes": {
            "configuration_hash": exp.get("configuration_hash"),
            "protocol_hash": exp.get("protocol_hash"),
            "dataset_version_hash": exp.get("dataset_version_hash"),
        },
        "benchmark_plan": benchmark_plan,
    }


def _runs_query(owner_id: str, exp: dict) -> dict:
    """Legacy Arena experiments keep the arena/manual context filter; protocol
    experiments include every context so benchmark runs remain visible."""
    query = {"owner_id": owner_id, "experiment_id": exp["id"]}
    if not exp.get("protocol") and not exp.get("protocol_snapshot"):
        query["context"] = {"$in": ["arena", "manual"]}
    return query


async def _experiment_detail(owner_id: str, exp: dict) -> dict:
    """Combined non-blind detail: experiment + runs + evaluations + protocol extras."""
    runs = await repo.runs.list(
        _runs_query(owner_id, exp), sort_field="created_at", descending=False)
    evaluations = await repo.evaluations.list(
        {"owner_id": owner_id, "experiment_id": exp["id"]}, sort_field="created_at")
    label_of = {e.get("run_id"): e.get("label") for e in exp.get("blind_map", [])}
    runs_out = [{**r, "label": label_of.get(r["id"])} for r in runs]
    extras = await _combined_extras(owner_id, exp)
    return {**exp, "runs": runs_out, "evaluations": evaluations, "revealed": True, **extras}


@router.post("")
async def create_experiment(body: ExperimentCreateRequest, user: dict = Depends(get_current_user)):
    """Create a first-class protocol experiment (status DRAFT)."""
    if not await repo.projects.get(body.project_id, user["id"]):
        raise HTTPException(status_code=400, detail="Project not found")
    try:
        exp = await experiment_service.create_experiment(
            user["id"], body.project_id, body.title,
            objective=body.objective, hypothesis=body.hypothesis,
            experiment_type=body.experiment_type, protocol=body.protocol,
            configurations=body.configurations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _experiment_detail(user["id"], exp)


@router.patch("/{experiment_id}")
async def update_experiment(experiment_id: str, body: ExperimentUpdateRequest,
                            user: dict = Depends(get_current_user)):
    """Update title/objective/hypothesis/protocol (and configurations while DRAFT)."""
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    try:
        updated = await experiment_service.update_experiment(
            user["id"], exp, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return await _experiment_detail(user["id"], updated)


@router.post("/{experiment_id}/ready")
async def mark_ready(experiment_id: str, user: dict = Depends(get_current_user)):
    """Validate requirements and freeze the experiment (DRAFT -> READY)."""
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    try:
        updated = await experiment_service.ready_experiment(user["id"], exp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _experiment_detail(user["id"], updated)


@router.get("/{experiment_id}/provenance")
async def get_provenance(experiment_id: str, user: dict = Depends(get_current_user)):
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return await experiment_service.get_provenance(user["id"], exp)


@router.get("")
async def list_experiments(project_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"owner_id": user["id"]}
    if project_id:
        query["project_id"] = project_id
    items = await repo.experiments.list(query, sort_field="created_at")
    # Blind, unrevealed experiments must not leak identity through the list view.
    return [
        blind_experiment_view(exp)
        if exp.get("blind") and not exp.get("revealed_at")
        else exp
        for exp in items
    ]


@router.get("/{experiment_id}")
async def get_experiment(experiment_id: str, reveal: bool = False, user: dict = Depends(get_current_user)):
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    runs = await repo.runs.list(_runs_query(user["id"], exp),
                                sort_field="created_at", descending=False)
    evaluations = await repo.evaluations.list({"owner_id": user["id"], "experiment_id": experiment_id},
                                              sort_field="created_at")
    blind = bool(exp.get("blind"))

    if blind and reveal:
        # Backward-compatible explicit reveal also persists the reveal event.
        exp, _event = await _reveal_experiment(user["id"], exp)

    blind_active = blind and not exp.get("revealed_at")
    if blind_active:
        by_id = {r["id"]: r for r in runs}
        runs_out = []
        for entry in exp.get("blind_map", []):
            run = by_id.pop(entry.get("run_id"), None)
            if run:
                runs_out.append(blind_run_view(run, entry.get("label")))
        # Defensive: never drop a run that the map did not cover.
        for run in by_id.values():
            runs_out.append(blind_run_view(run, None))
        return {
            **blind_experiment_view(exp),
            "runs": runs_out,
            "evaluations": [_redact_evaluation(e) for e in evaluations],
            "revealed": False,
            **(await _combined_extras(user["id"], exp, include_configurations=False)),
        }

    label_of = {e.get("run_id"): e.get("label") for e in exp.get("blind_map", [])}
    runs_out = [{**r, "label": label_of.get(r["id"])} for r in runs]
    extras = await _combined_extras(user["id"], exp)
    return {**exp, "runs": runs_out, "evaluations": evaluations, "revealed": True, **extras}


@router.post("/{experiment_id}/reveal")
async def reveal_experiment(experiment_id: str, user: dict = Depends(get_current_user)):
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    exp, event = await _reveal_experiment(user["id"], exp)
    return {
        **exp,
        "blind_map": exp.get("blind_map", []),
        "revealed": True,
        "revealed_at": event["revealed_at"],
        "reveal_event_id": event["id"],
    }


@router.get("/{experiment_id}/reveal")
async def reveal(experiment_id: str, user: dict = Depends(get_current_user)):
    """Legacy reveal endpoint, kept working and now also persisting an event."""
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    exp, event = await _reveal_experiment(user["id"], exp)
    return {"blind_map": exp.get("blind_map", []), "revealed_at": event["revealed_at"]}


@router.post("/{experiment_id}/judge")
async def judge(experiment_id: str, body: JudgeRequest, user: dict = Depends(get_current_user)):
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if not await repo.models.get(body.judge_model_id, user["id"]):
        raise HTTPException(status_code=400, detail="Judge model not found")
    rubric = await repo.rubrics.get(body.rubric_id, user["id"])
    if not rubric:
        raise HTTPException(status_code=400, detail="Rubric not found")
    runs = await repo.runs.list({"owner_id": user["id"], "experiment_id": experiment_id, "context": "arena"},
                                sort_field="created_at", descending=False)
    successful = [r for r in runs if r.get("status") == "success"]
    if not successful:
        raise HTTPException(status_code=400, detail="No successful runs to evaluate")
    evaluation = await run_judge(user["id"], exp, successful, body.judge_model_id, rubric)
    return evaluation


@router.post("/{experiment_id}/human-eval")
async def human_eval(experiment_id: str, body: HumanEvalRequest, user: dict = Depends(get_current_user)):
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    run = await repo.runs.get(body.run_id, user["id"])
    if not run:
        raise HTTPException(status_code=400, detail="Run not found")
    if run.get("experiment_id") != experiment_id:
        raise HTTPException(status_code=400, detail="Run does not belong to this experiment")
    if run.get("project_id") != exp.get("project_id"):
        raise HTTPException(status_code=400, detail="Run does not belong to this experiment's project")

    blind_active = bool(exp.get("blind")) and not exp.get("revealed_at")
    blind_label = None
    if blind_active:
        for entry in exp.get("blind_map", []):
            if entry.get("run_id") == body.run_id:
                blind_label = entry.get("label")
                break

    evaluation = {
        "id": new_id(), "owner_id": user["id"], "experiment_id": experiment_id,
        "project_id": exp.get("project_id"), "eval_type": "human",
        "run_id": body.run_id, "model_id": run.get("model_id"), "model_name": run.get("model_name"),
        "blind_label": blind_label,
        "scores": body.scores, "winner": body.winner, "notes": body.notes,
        "status": "completed", "created_at": now_iso(),
        "superseded_by": None, "superseded_at": None,
    }

    if body.winner:
        existing_winner = await repo.evaluations.find_one({
            "owner_id": user["id"], "experiment_id": experiment_id,
            "eval_type": "human", "winner": True, "superseded_by": None,
        })
        if existing_winner and not body.replace:
            raise HTTPException(status_code=409, detail="A winner has already been recorded for this experiment")
        if existing_winner and body.replace:
            await repo.evaluations.update(existing_winner["id"], user["id"], {
                "winner": False,
                "superseded_by": evaluation["id"],
                "superseded_at": now_iso(),
            })

    await repo.evaluations.insert(evaluation)
    return evaluation


@router.post("/{experiment_id}/rerun")
async def rerun(experiment_id: str, user: dict = Depends(get_current_user)):
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    result = await run_arena(
        owner_id=user["id"], project_id=exp["project_id"], task=exp["user_prompt"],
        system_prompt=exp.get("system_prompt", ""), model_ids=exp["model_ids"],
        params=exp.get("params", {}), blind=exp.get("blind", False),
        title=f"{exp['title']} (re-run)",
    )
    updated = await repo.experiments.update(result["experiment"]["id"], user["id"],
                                            {"parent_experiment_id": experiment_id})
    result["experiment"] = updated
    return result


@router.post("/{experiment_id}/clone")
async def clone(experiment_id: str, user: dict = Depends(get_current_user)):
    exp = await repo.experiments.get(experiment_id, user["id"])
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found")
    clone_doc = {
        "id": new_id(), "owner_id": user["id"], "project_id": exp["project_id"],
        "title": f"{exp['title']} (clone)", "objective": exp.get("objective", ""),
        "hypothesis": exp.get("hypothesis", ""), "experiment_type": exp["experiment_type"],
        "system_prompt": exp.get("system_prompt", ""), "user_prompt": exp.get("user_prompt", ""),
        "params": exp.get("params", {}), "model_ids": exp.get("model_ids", []),
        "blind": exp.get("blind", False), "blind_map": [], "rubric_id": exp.get("rubric_id"),
        "status": "draft", "run_count": 0, "cloned_from": experiment_id,
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await repo.experiments.insert(clone_doc)
    return clone_doc


@router.delete("/{experiment_id}")
async def delete_experiment(experiment_id: str, user: dict = Depends(get_current_user)):
    if not await repo.experiments.get(experiment_id, user["id"]):
        raise HTTPException(status_code=404, detail="Experiment not found")
    await repo.evaluations.delete_many({"owner_id": user["id"], "experiment_id": experiment_id})
    await repo.experiment_configs.delete_many({"owner_id": user["id"], "experiment_id": experiment_id})
    await repo.benchmark_plans.delete_many({"owner_id": user["id"], "experiment_id": experiment_id})
    await repo.runs.delete_many({"owner_id": user["id"], "experiment_id": experiment_id})
    await repo.reveal_events.delete_many({"owner_id": user["id"], "experiment_id": experiment_id})
    await repo.experiments.delete(experiment_id, user["id"])
    return {"ok": True}
