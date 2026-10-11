"""Blind Arena execution.

All candidate models are executed through the canonical ``execute_model`` funnel
in parallel. When blind, the server shuffles presentation order, assigns opaque
``Response A/B/...`` labels, and keeps the label -> run/model mapping
(``blind_map``) *only* on the server-side experiment document. Callers receive
identity-free views until an explicit reveal.
"""
import asyncio
import random
import time
from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..errors import safe_error_message
from ..models import new_id, now_iso
from . import tracing
from .execution import execute_model, persist_exception_run

_LABELS = [f"Response {c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]


def blind_run_view(run: Dict[str, Any], label: str) -> Dict[str, Any]:
    """Identity-free projection of a Run for blind evaluation.

    Deliberately whitelists only non-identifying fields. It must never expose
    model/provider identity, pricing, telemetry beyond latency/tokens, prompts,
    params, or the blind_map.
    """
    return {
        "id": run.get("id"),
        "label": label,
        "status": run.get("status"),
        "content": run.get("normalized_response"),
        "latency_ms": run.get("latency_ms"),
        "input_tokens": run.get("input_tokens"),
        "output_tokens": run.get("output_tokens"),
        # Never echo a raw message: resolution errors embed the model/provider
        # name and would break blindness. Only category-level text is exposed.
        "error_message": safe_error_message(run.get("error")) or None,
        "created_at": run.get("created_at"),
        "finish_reason": run.get("finish_reason"),
    }


def blind_experiment_view(exp: Dict[str, Any]) -> Dict[str, Any]:
    """Experiment copy with the identity leaks removed.

    Removes ``blind_map`` and empties ``model_ids``; ``revealed`` reflects
    whether the experiment has actually been revealed server-side.
    """
    view = {k: v for k, v in exp.items() if k != "blind_map"}
    view["model_ids"] = []
    view["revealed"] = bool(exp.get("revealed_at"))
    return view


async def _one(
    owner_id: str,
    project_id: str,
    experiment_id: str,
    system_prompt: str,
    messages: List[Dict[str, str]],
    params: Dict[str, Any],
    model_id: str,
    trace_id: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    attachment_ids: Optional[List[str]] = None,
    precomputed_retrieval: Optional[Dict[str, Any]] = None,
    knowledge_collection_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    try:
        return await execute_model(
            owner_id, model_id, system_prompt, messages, params,
            experiment_id=experiment_id, context="arena", project_id=project_id,
            trace_id=trace_id,
            attachments=list(attachments or []),
            knowledge_collection_ids=list(knowledge_collection_ids or []),
            precomputed_retrieval=precomputed_retrieval,
        )
    except Exception as exc:  # pragma: no cover - execute_model should never raise
        if trace_id:
            try:
                started = time.perf_counter()
                span = await tracing.start_span(
                    owner_id, trace_id, "MODEL_CALL", "Model call (failed)",
                )
                await tracing.finish_span(
                    span, status="error", started_perf=started,
                )
            except Exception:  # pragma: no cover - telemetry is best-effort
                pass
        return await persist_exception_run(
            owner_id, model_id, exc, experiment_id=experiment_id,
            project_id=project_id, context="arena", execution_kind="arena",
            system_prompt=system_prompt, messages=messages, params=params,
        )


async def run_arena(
    owner_id: str,
    project_id: str,
    task: str,
    system_prompt: str,
    model_ids: List[str],
    params: Dict[str, Any],
    blind: bool,
    title: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    attachment_ids: Optional[List[str]] = None,
    knowledge_collection_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute the same task against N models in parallel and persist an
    Experiment plus one immutable Run per model (failures included).

    Returns ``{"experiment", "runs"}``. Blind experiments return blinded run and
    experiment views; non-blind experiments return full documents.
    """
    experiment_id = new_id()
    messages = [{"role": "user", "content": task}]
    attachment_docs = list(attachments or [])

    # Fairness gate: every competitor must support image input when the
    # shared turn carries images. Checked BEFORE running so no model gets
    # a head start on a degraded input.
    try:
        from . import attachments as attachments_service
        from ..providers import model_capability_status
        needs_vision = attachments_service.attachment_requires_vision(attachment_docs)
    except Exception:
        needs_vision = False
    if needs_vision:
        for candidate_id in model_ids:
            try:
                candidate = await repo.models.get(candidate_id, owner_id)
            except Exception:
                candidate = None
            status = None
            try:
                status = model_capability_status(candidate or {}, "vision")
            except Exception:
                status = None
            if status != "SUPPORTED":
                name = (candidate or {}).get("display_name") or candidate_id
                raise ValueError(
                    f"Model '{name}' does not support image input; arena runs with "
                    "image attachments require vision-capable models"
                )

    # Shared parent retrieval: retrieve ONCE, pass the same component +
    # provenance to every competitor so evidence is identical.
    shared_retrieval: Optional[Dict[str, Any]] = None
    if knowledge_collection_ids:
        try:
            from . import retrieval as retrieval_service
            payload = await retrieval_service.retrieve(
                owner_id, project_id, task,
                list(knowledge_collection_ids),
            )
            provenance = retrieval_service.retrieval_provenance(payload)
            shared_retrieval = {
                "component": retrieval_service.retrieved_context_component(
                    payload.get("results") or [], provenance
                ),
                "provenance": provenance,
            }
        except Exception:
            shared_retrieval = None

    trace = await tracing.start_trace(
        owner_id, "arena", project_id=project_id, experiment_id=experiment_id,
        metadata={"blind": bool(blind), "model_count": len(model_ids)},
    )
    arena_start = time.perf_counter()

    shared_attachment_ids = list(attachment_ids or [d.get("id") for d in attachment_docs if isinstance(d, dict) and d.get("id")])
    gathered = await asyncio.gather(
        *[_one(
            owner_id, project_id, experiment_id, system_prompt, messages, params, mid,
            trace_id=trace["id"],
            attachments=attachment_docs,
            attachment_ids=shared_attachment_ids,
            precomputed_retrieval=shared_retrieval,
            knowledge_collection_ids=list(knowledge_collection_ids or []),
          )
          for mid in model_ids],
        return_exceptions=True,
    )
    runs: List[Dict[str, Any]] = []
    for idx, item in enumerate(gathered):
        if isinstance(item, BaseException):  # defensive: persist if gather returned an exception
            runs.append(await persist_exception_run(
                owner_id, model_ids[idx], item, experiment_id=experiment_id,
                project_id=project_id, context="arena", execution_kind="arena",
                system_prompt=system_prompt, messages=messages, params=params,
            ))
        else:
            runs.append(item)

    if runs:
        try:
            await repo.traces.update(trace["id"], owner_id, {"root_run_id": runs[0]["id"]})
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass

    any_success = any(r.get("status") == "success" for r in runs)
    await tracing.finish_trace(
        trace,
        status="completed" if any_success else "error",
        metadata={
            "run_count": len(runs),
            "success_count": sum(1 for r in runs if r.get("status") == "success"),
        },
        started_perf=arena_start,
    )

    # Randomize server-side presentation order, then label in that order.
    order = list(range(len(runs)))
    random.shuffle(order)
    blind_map: List[Dict[str, Any]] = []
    if blind:
        for position, run_index in enumerate(order):
            run = runs[run_index]
            blind_map.append({
                "label": _LABELS[position] if position < len(_LABELS) else f"Response {position + 1}",
                "run_id": run.get("id"),
                "model_id": run.get("model_id"),
                "model_name": run.get("model_name"),
                "provider_id": run.get("provider_id"),
                "provider_name": run.get("provider_name"),
                "model_identifier": run.get("model_identifier"),
            })

    experiment = {
        "id": experiment_id,
        "owner_id": owner_id,
        "project_id": project_id,
        "title": title or (task[:60] + ("…" if len(task) > 60 else "")),
        "objective": "",
        "hypothesis": "",
        "experiment_type": "model_comparison",
        "system_prompt": system_prompt,
        "user_prompt": task,
        "params": params,
        "model_ids": model_ids,
        "blind": blind,
        "blind_map": blind_map,
        "attachment_ids": shared_attachment_ids,
        "attachment_hashes": [
            d.get("sha256") for d in attachment_docs
            if isinstance(d, dict) and d.get("sha256")
        ],
        "knowledge_provenance": (shared_retrieval or {}).get("provenance"),
        "knowledge_collection_ids": list(knowledge_collection_ids or []),
        "rubric_id": None,
        "status": "completed" if any_success else "failed",
        "run_count": len(runs),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await repo.experiments.insert(experiment)

    if not blind:
        return {"experiment": experiment, "runs": runs}

    ordered_runs = [runs[i] for i in order]
    labels = [entry["label"] for entry in blind_map]
    return {
        "experiment": blind_experiment_view(experiment),
        "runs": [blind_run_view(run, label) for run, label in zip(ordered_runs, labels)],
    }
