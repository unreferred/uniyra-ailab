"""AI judge service: score candidate runs against a rubric via ``execute_model``."""
import json
import time
from typing import Any, Dict, List

from .. import repository as repo
from ..errors import error_message
from ..models import new_id, now_iso
from . import tracing
from .execution import execute_model


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    return t.strip()


async def run_judge(
    owner_id: str,
    experiment: Dict[str, Any],
    candidate_runs: List[Dict[str, Any]],
    judge_model_id: str,
    rubric: Dict[str, Any],
) -> Dict[str, Any]:
    """Ask a judge model to score candidate runs against a rubric. The judge
    result is stored as a separate Evaluation — candidate runs are untouched."""
    dims = rubric["dimensions"]
    dim_desc = "\n".join([f"- {d['key']} ({d['label']}, 0-{d.get('max_score',10)}): {d.get('description','')}" for d in dims])
    blocks = []
    for r in candidate_runs:
        if r.get("status") != "success":
            continue
        blocks.append(f"### {r['id']}\n{r.get('normalized_response','')}")
    candidates_text = "\n\n".join(blocks)

    system = (
        "You are a rigorous, impartial AI evaluation judge. Score each candidate "
        "response against every rubric dimension. Respond ONLY with valid JSON."
    )
    schema = (
        '{"evaluations":[{"run_id":"<id>","scores":{"<dim_key>":<int>},'
        '"total":<number>,"rationale":"<short>"}],'
        '"ranking":["<run_id best first>"],"winner_run_id":"<id>",'
        '"confidence":<0..1>,"summary":"<short>"}'
    )
    prompt = (
        f"TASK GIVEN TO THE MODELS:\n{experiment.get('user_prompt','')}\n\n"
        f"RUBRIC DIMENSIONS:\n{dim_desc}\n\n"
        f"CANDIDATE RESPONSES (identified by run_id):\n{candidates_text}\n\n"
        f"Return JSON exactly in this shape:\n{schema}"
    )

    run = await execute_model(
        owner_id, judge_model_id, system,
        [{"role": "user", "content": prompt}],
        {"max_tokens": 2000, "temperature": 0},
        experiment_id=experiment["id"], context="judge",
        project_id=experiment.get("project_id"),
    )

    parsed: Dict[str, Any] = {}
    parse_error = None
    if run["status"] == "success" and run.get("normalized_response"):
        try:
            parsed = json.loads(_strip_json(run["normalized_response"]))
        except Exception as e:
            parse_error = f"Judge returned unparseable JSON: {e}"
    elif run["status"] != "success":
        parse_error = run.get("error_message") or error_message(run.get("error")) or "Judge execution failed"

    evaluation = {
        "id": new_id(),
        "owner_id": owner_id,
        "experiment_id": experiment["id"],
        "project_id": experiment.get("project_id"),
        "eval_type": "ai_judge",
        "judge_run_id": run["id"],
        "judge_model_id": judge_model_id,
        "judge_model_name": run.get("model_name"),
        "rubric_id": rubric["id"],
        "rubric_snapshot": rubric,
        "result": parsed,
        "raw_output": run.get("normalized_response"),
        "status": "completed" if not parse_error else "error",
        "error": parse_error,
        "created_at": now_iso(),
    }
    await repo.evaluations.insert(evaluation)
    trace_id = run.get("trace_id")
    if trace_id:
        try:
            started = time.perf_counter()
            span = await tracing.start_span(
                owner_id,
                trace_id,
                "EVALUATION",
                "Judge evaluation",
                run_id=run["id"],
                metadata={
                    "evaluation_id": evaluation.get("id"),
                    "evaluator_type": evaluation.get("eval_type"),
                },
            )
            await tracing.finish_span(
                span,
                status="completed" if evaluation.get("status") != "error" else "error",
                started_perf=started,
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass
    return evaluation
