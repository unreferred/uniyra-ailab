"""Structured multi-round Council execution.

Every participant and moderator attempt is an immutable Run created through the
canonical ``execute_model`` funnel, so telemetry/cost flow into observability
identically. A participant or moderator failure is recorded as an error Run and
can never raise out of ``run_council``.

Rollups are explicit about unknown values: unknown cost/usage is counted, never
silently treated as zero.
"""
import asyncio
import json
import math
import statistics
import time
from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..errors import error_message
from ..models import new_id, now_iso
from . import tracing
from .execution import execute_model, persist_exception_run
from .judge import _strip_json

DEFAULTS = {"max_tokens": 700, "temperature": 0.7}


def _telemetry(run: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "latency_ms": run.get("latency_ms"), "input_tokens": run.get("input_tokens"),
        "output_tokens": run.get("output_tokens"), "estimated_cost": run.get("estimated_cost"),
        "cost_available": run.get("cost_available"),
    }


def _msg_from_run(p: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "participant_id": p["participant_id"], "role": p["role"], "model_id": p["model_id"],
        "model_name": run.get("model_name"),
        "provider_id": run.get("provider_id"), "provider_name": run.get("provider_name"),
        "provider_type": run.get("provider_type"),
        "model_identifier": run.get("model_identifier"), "family": run.get("family"),
        "content": run.get("normalized_response") or "",
        "status": run["status"],
        # ``error`` stays a human string for UI compatibility; structured detail
        # is exposed separately.
        "error": run.get("error_message") or error_message(run.get("error")),
        "error_detail": run.get("error") if isinstance(run.get("error"), dict) else None,
        "run_id": run["id"],
        "telemetry": _telemetry(run),
    }


def _cost_rollup(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    known = [r["estimated_cost"] for r in runs if r.get("estimated_cost") is not None]
    total = len(runs)
    return {
        "known_cost_total": round(sum(known), 6) if known else None,
        "unknown_cost_run_count": total - len(known),
        # execution_cost is only defined when every run has a known cost.
        "execution_cost": round(sum(known), 6) if runs and len(known) == total else None,
        "total_run_count": total,
    }


def _tokens_rollup(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    known_usage = [
        r for r in runs
        if r.get("input_tokens") is not None and r.get("output_tokens") is not None
    ]
    def _sum(key: str) -> int:
        return sum(int(r.get(key) or 0) for r in runs)
    return {
        "input_tokens_total": _sum("input_tokens"),
        "output_tokens_total": _sum("output_tokens"),
        "known_usage_run_count": len(known_usage),
        "unknown_usage_run_count": len(runs) - len(known_usage),
        "cached_tokens_total": _sum("cached_tokens"),
        "reasoning_tokens_total": _sum("reasoning_tokens"),
    }


def _percentile(sorted_values: List[int], fraction: float) -> int:
    """Nearest-rank percentile over a pre-sorted list."""
    if not sorted_values:
        raise ValueError("no values")
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


def _latency_summary(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Latency stats over runs with a measured latency only.

    ``p95`` needs a meaningful sample: with fewer than 5 measured runs it is
    reported as ``None`` rather than as a noisy estimate.
    """
    values = sorted(int(r["latency_ms"]) for r in runs if r.get("latency_ms") is not None)
    if not values:
        return {"avg_latency_ms": None, "median_latency_ms": None, "p95_latency_ms": None}
    return {
        "avg_latency_ms": int(round(sum(values) / len(values))),
        "median_latency_ms": int(statistics.median(values)),
        "p95_latency_ms": _percentile(values, 0.95) if len(values) >= 5 else None,
    }


async def run_council(
    owner_id: str, project_id: str, question: str,
    participants: List[Dict[str, Any]], moderator_model_id: str, title: Optional[str] = None,
) -> Dict[str, Any]:
    """Structured 4-round council.

    Synthesis status is deterministic:
      * ``success`` — moderator returned parseable JSON and every participant
        attempt succeeded
      * ``partial`` — at least one participant succeeded, but the moderator
        failed/unparseable or some participant attempts failed
      * ``failed``  — no participant succeeded
    """
    council_id = new_id()
    enabled = [p for p in participants if p.get("enabled", True)]
    if not enabled:
        raise ValueError("All council participants are disabled")
    enabled.sort(key=lambda p: (p.get("order", 0), p.get("role", "")))
    parts = [{
        "participant_id": new_id(), "role": p["role"], "perspective": p.get("perspective", ""),
        "model_id": p["model_id"], "display_name": p.get("display_name", ""),
        "temperature": p.get("temperature"), "max_tokens": p.get("max_tokens"),
        "order": p.get("order", 0), "enabled": True,
    } for p in enabled]

    trace = await tracing.start_trace(
        owner_id, "council", project_id=project_id, council_id=council_id,
        metadata={"participant_count": len(parts), "moderator_model_id": None},
    )
    council_start = time.perf_counter()

    rounds: List[Dict[str, Any]] = []
    participant_runs: List[Dict[str, Any]] = []

    async def exec_for(p, system, prompt):
        per_p = {
            "max_tokens": p.get("max_tokens") if p.get("max_tokens") is not None else DEFAULTS["max_tokens"],
            "temperature": p.get("temperature") if p.get("temperature") is not None else DEFAULTS["temperature"],
        }
        try:
            run = await execute_model(
                owner_id, p["model_id"], system, [{"role": "user", "content": prompt}], per_p,
                council_id=council_id, context="council", project_id=project_id,
                trace_id=trace["id"],
            )
        except Exception as exc:  # pragma: no cover - defensive
            run = await persist_exception_run(
                owner_id, p["model_id"], exc, project_id=project_id, context="council",
                execution_kind="council", system_prompt=system,
                messages=[{"role": "user", "content": prompt}], params=per_p,
            )
        participant_runs.append(run)
        return _msg_from_run(p, run)

    # Round 1 — Independent Opinion
    async def r1(p):
        sys = (f"You are the {p['role']} on an expert council. {p.get('perspective','')}\n"
               "Give your INDEPENDENT expert opinion on the question below. Be concise, specific, and decisive.")
        return await exec_for(p, sys, question)
    r1_msgs = list(await asyncio.gather(*[r1(p) for p in parts]))
    rounds.append({"round": 1, "name": "Independent Opinion", "messages": r1_msgs})
    by_pid = {m["participant_id"]: m for m in r1_msgs}

    def others_block(pid):
        return "\n\n".join([f"[{m['role']}]:\n{m['content']}" for m in r1_msgs if m["participant_id"] != pid and m["status"] == "success"])

    # Round 2 — Cross Critique
    async def r2(p):
        sys = (f"You are the {p['role']} on an expert council. {p.get('perspective','')}\n"
               "Critique the other members' opinions. Identify weaknesses, risks, and points you disagree with. Be constructive but rigorous.")
        prompt = f"QUESTION:\n{question}\n\nOTHER MEMBERS' OPINIONS:\n{others_block(p['participant_id'])}\n\nProvide your critique."
        return await exec_for(p, sys, prompt)
    r2_msgs = list(await asyncio.gather(*[r2(p) for p in parts]))
    rounds.append({"round": 2, "name": "Cross Critique", "messages": r2_msgs})

    # Round 3 — Revision
    critiques_all = "\n\n".join([f"[{m['role']} critiques]:\n{m['content']}" for m in r2_msgs if m["status"] == "success"])
    async def r3(p):
        original = by_pid.get(p["participant_id"], {}).get("content", "")
        sys = (f"You are the {p['role']} on an expert council. {p.get('perspective','')}\n"
               "In light of the critiques, REVISE or DEFEND your position. State clearly what changed and why.")
        prompt = f"QUESTION:\n{question}\n\nYOUR ORIGINAL OPINION:\n{original}\n\nCRITIQUES RAISED:\n{critiques_all}\n\nProvide your revised final position."
        return await exec_for(p, sys, prompt)
    r3_msgs = list(await asyncio.gather(*[r3(p) for p in parts]))
    rounds.append({"round": 3, "name": "Revision", "messages": r3_msgs})

    # Round 4 — Moderator Synthesis
    def round_dump(msgs):
        return "\n\n".join([f"[{m['role']}]:\n{m['content']}" for m in msgs if m["status"] == "success"])
    mod_sys = ("You are the impartial moderator of an expert council. Synthesise the discussion. "
               "Respond ONLY with valid JSON.")
    schema = ('{"consensus":["..."],"disagreements":["..."],"key_risks":["..."],'
              '"critical_unknowns":["..."],"strongest_arguments":["..."],'
              '"recommended_decision":"...","confidence":<0..1>,"conditions":["..."],'
              '"suggested_next_actions":["..."]}')
    mod_prompt = (f"QUESTION:\n{question}\n\nROUND 1 — INDEPENDENT OPINIONS:\n{round_dump(r1_msgs)}\n\n"
                  f"ROUND 2 — CRITIQUES:\n{round_dump(r2_msgs)}\n\nROUND 3 — REVISIONS:\n{round_dump(r3_msgs)}\n\n"
                  f"Return JSON exactly in this shape:\n{schema}")
    mod_params = {"max_tokens": 1500, "temperature": 0.2}
    try:
        mod_run = await execute_model(
            owner_id, moderator_model_id, mod_sys,
            [{"role": "user", "content": mod_prompt}], mod_params,
            council_id=council_id, context="council", execution_kind="council_moderator",
            project_id=project_id, trace_id=trace["id"],
        )
    except Exception as exc:  # pragma: no cover - defensive
        mod_run = await persist_exception_run(
            owner_id, moderator_model_id, exc, project_id=project_id, context="council",
            execution_kind="council_moderator", system_prompt=mod_sys,
            messages=[{"role": "user", "content": mod_prompt}], params=mod_params,
        )

    synthesis, syn_err = {}, None
    if mod_run["status"] == "success" and mod_run.get("normalized_response"):
        try:
            synthesis = json.loads(_strip_json(mod_run["normalized_response"]))
        except Exception as e:
            syn_err = f"Moderator returned unparseable JSON: {e}"
    else:
        syn_err = mod_run.get("error_message") or error_message(mod_run.get("error")) or "Moderator execution failed"
    moderator_status = "success" if mod_run["status"] == "success" else "error"
    rounds.append({"round": 4, "name": "Moderator Synthesis",
                   "messages": [{"participant_id": "moderator", "role": "Moderator",
                                 "model_id": moderator_model_id, "model_name": mod_run.get("model_name"),
                                 "content": mod_run.get("normalized_response") or "", "status": mod_run["status"],
                                 "error": syn_err if mod_run["status"] != "success" else mod_run.get("error_message"),
                                 "run_id": mod_run["id"], "telemetry": _telemetry(mod_run)}]})

    all_runs = participant_runs + [mod_run]
    any_participant_success = any(r.get("status") == "success" for r in participant_runs)
    participant_failure_count = sum(1 for r in participant_runs if r.get("status") != "success")
    moderator_parse_ok = syn_err is None
    if not any_participant_success:
        synthesis_status = "failed"
    elif moderator_parse_ok and participant_failure_count == 0:
        synthesis_status = "success"
    else:
        synthesis_status = "partial"

    council = {
        "id": council_id, "owner_id": owner_id, "project_id": project_id,
        "council_id": council_id,
        "title": title or (question[:60] + ("…" if len(question) > 60 else "")),
        "question": question, "participants": parts,
        "moderator_model_id": moderator_model_id, "moderator_model_name": mod_run.get("model_name"),
        "moderator_status": moderator_status, "moderator_run_id": mod_run["id"],
        "rounds": rounds, "synthesis": synthesis, "synthesis_raw": mod_run.get("normalized_response"),
        "synthesis_error": syn_err, "synthesis_status": synthesis_status,
        "status": "completed" if synthesis_status in ("success", "partial") else "failed",
        "cost_rollup": _cost_rollup(all_runs),
        "tokens_rollup": _tokens_rollup(all_runs),
        "latency_summary": _latency_summary(all_runs),
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await repo.councils.insert(council)
    await tracing.finish_trace(
        trace,
        status="completed" if synthesis_status in ("success", "partial") else "error",
        metadata={"synthesis_status": synthesis_status, "run_count": len(all_runs)},
        started_perf=council_start,
    )
    return council
