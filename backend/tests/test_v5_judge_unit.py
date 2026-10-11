"""Unit tests for the V5 structured judge system.

The judge model call is always mocked; no database or provider is touched. Async
entry points are driven with ``asyncio.run`` because this suite has no
``pytest-asyncio``.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.services import judge_system
from app.services.judge_system import (
    build_judge_input,
    build_judge_prompt,
    validate_judge_output,
)


RUBRIC = {
    "id": "rubric-1",
    "version": 1,
    "dimensions": [
        {
            "key": "correctness",
            "label": "Correctness",
            "description": "Is the answer correct?",
            "max_score": 10,
            "weight": 1.0,
        },
        {
            "key": "grounding",
            "label": "Grounding",
            "description": "Is the answer grounded?",
            "max_score": 10,
            "weight": 1.0,
        },
    ],
}

EXPERIMENT = {"id": "exp-1", "project_id": "proj-1"}


def _candidate_run():
    return {
        "id": "run-1",
        "experiment_id": "exp-1",
        "project_id": "proj-1",
        "status": "success",
        "normalized_response": "The answer is 42.",
        "messages": [{"role": "user", "content": "What is the answer?"}],
        "trace_id": "trace-run",
    }


def _judge_run(response, *, status="success", **overrides):
    run = {
        "id": "judge-run-1",
        "status": status,
        "normalized_response": response,
        "raw_response": response,
        "model_id": "judge-model",
        "model_name": "Judge Model",
        "model_identifier": "judge/model",
        "provider_id": "prov-1",
        "provider_name": "Judge Provider",
        "family": "judge-family",
        "pricing_snapshot": {},
        "input_tokens": 120,
        "output_tokens": 45,
        "estimated_cost": 0.002,
        "cost_available": True,
        "error": None,
        "error_message": None,
        "trace_id": "trace-judge",
    }
    run.update(overrides)
    return run


def _valid_response():
    return json.dumps(
        {
            "dimensions": {
                "correctness": {"score": 8, "rationale": "Correct."},
                "grounding": {"score": 6, "rationale": "Mostly grounded."},
            },
            "findings": [
                {
                    "category": "grounding",
                    "severity": "low",
                    "message": "One claim lacks a citation.",
                }
            ],
            "overall_result": "pass",
            "confidence": 0.9,
        }
    )


def _evaluate(
    mock,
    *,
    response=None,
    judge_run=None,
    rubric=RUBRIC,
    run=None,
    **kwargs,
):
    """Run evaluate_with_judge inside a fresh event loop with a mocked judge call."""
    if judge_run is None:
        judge_run = _judge_run(_valid_response() if response is None else response)
    mock.return_value = judge_run
    with patch("app.services.judge_system.execute_model", new=mock):
        result = asyncio.run(
            judge_system.evaluate_with_judge(
                "owner-1",
                EXPERIMENT,
                run if run is not None else _candidate_run(),
                judge_model_id="judge-model",
                rubric=rubric,
                **kwargs,
            )
        )
    return result


# ---------------------------------------------------------------- happy path


def test_valid_structured_output_is_normalized():
    mock = AsyncMock()
    result = _evaluate(mock)

    assert result["status"] == "completed"
    assert result["outcome"] == "PASS"
    assert result["passed"] is True
    assert abs(result["score"] - 0.7) < 1e-9
    assert abs(result["scores"]["correctness"] - 0.8) < 1e-9
    assert abs(result["scores"]["grounding"] - 0.6) < 1e-9
    assert len(result["findings"]) == 1
    assert result["findings"][0]["category"] == "grounding"
    assert result["findings"][0]["severity"] == "low"
    assert result["judge"]["tools_enabled"] is False
    assert result["judge"]["temperature"] == 0
    assert result["judge"]["run_id"] == "judge-run-1"
    assert result["judge"]["model_id"] == "judge-model"
    assert result["usage"]["input_tokens"] == 120
    assert result["usage"]["output_tokens"] == 45
    assert result["usage"]["cost"] == 0.002
    assert result["usage"]["cost_available"] is True
    assert result["evaluator_type"] == "judge_model"
    assert result["evaluator"]["implementation"] == "app.services.judge_system"
    assert result["confidence"] == 0.9
    assert result["judge"]["input_hash"]
    assert result["normalized_result"] is not None


def test_judge_call_disables_tools():
    mock = AsyncMock()
    _evaluate(mock)

    assert mock.await_count == 1
    kwargs = mock.call_args.kwargs
    assert kwargs["enabled_tool_ids"] == []
    assert kwargs["max_tool_rounds"] == 0
    assert kwargs["context"] == "judge"
    assert kwargs["execution_kind"] == "judge"
    assert kwargs["experiment_id"] == "exp-1"
    assert kwargs["project_id"] == "proj-1"
    assert kwargs["parent_run_id"] == "run-1"
    # position: owner, model, system, messages, params
    assert mock.call_args.args[1] == "judge-model"
    assert mock.call_args.args[4] == {"temperature": 0}


# ---------------------------------------------------------------- malformed


def test_prose_output_errors_and_is_not_coerced_to_pass():
    mock = AsyncMock()
    result = _evaluate(
        mock, response="The answer looks good. I would score it highly overall."
    )

    assert result["status"] == "error"
    assert result["outcome"] == "ERROR"
    assert result["passed"] is None
    assert result["score"] is None
    assert result["error"].startswith("judge_output_unparseable_json:")
    assert result["raw_result"].startswith("The answer looks good")


def test_missing_rubric_dimension_errors():
    mock = AsyncMock()
    response = json.dumps(
        {
            "dimensions": {"correctness": {"score": 8, "rationale": "ok"}},
            "overall_result": "pass",
        }
    )
    result = _evaluate(mock, response=response)

    assert result["status"] == "error"
    assert result["outcome"] == "ERROR"
    assert result["error"] == "judge_output_missing_dimension:grounding"
    assert result["passed"] is None


def test_unknown_rubric_dimension_errors():
    mock = AsyncMock()
    response = json.dumps(
        {
            "dimensions": {
                "correctness": {"score": 8, "rationale": "ok"},
                "grounding": {"score": 7, "rationale": "ok"},
                "verbosity": {"score": 5, "rationale": "extra"},
            },
            "overall_result": "pass",
        }
    )
    result = _evaluate(mock, response=response)

    assert result["status"] == "error"
    assert result["error"] == "judge_output_unknown_dimension:verbosity"


def test_non_numeric_dimension_score_errors():
    mock = AsyncMock()
    response = json.dumps(
        {
            "dimensions": {
                "correctness": {"score": "excellent", "rationale": "ok"},
                "grounding": {"score": 7, "rationale": "ok"},
            },
            "overall_result": "pass",
        }
    )
    result = _evaluate(mock, response=response)

    assert result["status"] == "error"
    assert result["error"] == "judge_output_bad_dimension_score:correctness"


def test_bad_overall_result_string_errors():
    mock = AsyncMock()
    response = json.dumps(
        {
            "dimensions": {
                "correctness": {"score": 8, "rationale": "ok"},
                "grounding": {"score": 7, "rationale": "ok"},
            },
            "overall_result": "maybe",
        }
    )
    result = _evaluate(mock, response=response)

    assert result["status"] == "error"
    assert result["error"] == "judge_output_bad_overall_result"


def test_overall_result_only_schema_passes():
    mock = AsyncMock()
    result = _evaluate(mock, response=json.dumps({"overall_result": "pass"}))

    assert result["status"] == "completed"
    assert result["passed"] is True
    assert result["outcome"] == "PASS"
    assert result["scores"] == {}


def test_judge_execution_failure_errors():
    mock = AsyncMock()
    failed_run = _judge_run(
        None, status="error", error_message="judge provider exploded"
    )
    result = _evaluate(mock, judge_run=failed_run)

    assert result["status"] == "error"
    assert result["outcome"] == "ERROR"
    assert result["passed"] is None
    assert result["error"] == "judge provider exploded"
    assert result["judge_run_id"] == "judge-run-1"
    assert result["usage"]["input_tokens"] == 120


# ---------------------------------------------------------------- privacy


def test_hidden_reasoning_is_never_sent_to_judge():
    secret_cot = "SECRET_CHAIN_OF_THOUGHT_MARKER"
    secret_segments = "SECRET_REASONING_SEGMENT_MARKER"
    secret_loop = "SECRET_TOOL_LOOP_MARKER"
    run = _candidate_run()
    run["reasoning_content"] = secret_cot
    run["reasoning_segments"] = [secret_segments]
    run["tool_loop"] = {"steps": [secret_loop]}

    captured = {}

    async def fake_execute(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _judge_run(_valid_response())

    with patch("app.services.judge_system.execute_model", new=fake_execute):
        result = asyncio.run(
            judge_system.evaluate_with_judge(
                "owner-1",
                EXPERIMENT,
                run,
                judge_model_id="judge-model",
                rubric=RUBRIC,
            )
        )

    assert result["status"] == "completed"
    system_prompt = captured["args"][2]
    messages = captured["args"][3]
    prompt = messages[0]["content"]
    judge_input_text = json.dumps(result["judge_input"])

    for secret in (secret_cot, secret_segments, secret_loop):
        assert secret not in prompt
        assert secret not in system_prompt
        assert secret not in judge_input_text

    # The task is still derived from the observable user message.
    assert result["judge_input"]["task"] == "What is the answer?"
    assert result["judge_input"]["candidate_answer"] == "The answer is 42."


def test_absent_confidence_is_none_not_zero():
    mock = AsyncMock()
    response = json.dumps(
        {
            "dimensions": {
                "correctness": {"score": 9, "rationale": "ok"},
                "grounding": {"score": 9, "rationale": "ok"},
            },
            "overall_result": "pass",
        }
    )
    result = _evaluate(mock, response=response)

    assert result["status"] == "completed"
    assert result["confidence"] is None


# ---------------------------------------------------------------- rubric guards


def test_rubric_missing_returns_error_without_calling_judge():
    mock = AsyncMock()
    result = _evaluate(mock, rubric={})

    assert result["status"] == "error"
    assert result["outcome"] == "ERROR"
    assert result["error"] == "rubric_required_for_judge"
    assert result["passed"] is None
    assert mock.await_count == 0


def test_rubric_with_empty_dimensions_returns_error():
    mock = AsyncMock()
    result = _evaluate(mock, rubric={"id": "rubric-empty", "dimensions": []})

    assert result["status"] == "error"
    assert result["error"] == "rubric_required_for_judge"


# ---------------------------------------------------------------- threshold


def _half_score_response():
    return json.dumps(
        {
            "dimensions": {
                "correctness": {"score": 5, "rationale": "partial"},
                "grounding": {"score": 5, "rationale": "partial"},
            },
            "overall_result": "pass",
        }
    )


def test_threshold_above_score_fails():
    mock = AsyncMock()
    result = _evaluate(mock, response=_half_score_response(), threshold=0.7)

    assert result["status"] == "completed"
    assert abs(result["score"] - 0.5) < 1e-9
    assert result["threshold"] == 0.7
    assert result["passed"] is False
    assert result["outcome"] == "FAIL"


def test_threshold_below_score_passes():
    mock = AsyncMock()
    result = _evaluate(mock, response=_half_score_response(), threshold=0.3)

    assert result["status"] == "completed"
    assert result["threshold"] == 0.3
    assert result["passed"] is True
    assert result["outcome"] == "PASS"


# ---------------------------------------------------------------- direct units


def test_validate_rejects_non_object():
    normalized, error = validate_judge_output(["not", "a", "dict"], RUBRIC)
    assert normalized is None
    assert error == "judge_output_not_object"


def test_validate_rejects_bool_score_and_nan():
    normalized, error = validate_judge_output(
        {
            "dimensions": {
                "correctness": {"score": True, "rationale": "x"},
                "grounding": {"score": 5, "rationale": "x"},
            }
        },
        RUBRIC,
    )
    assert normalized is None
    assert error == "judge_output_bad_dimension_score:correctness"


def test_validate_rejects_out_of_range_confidence():
    normalized, error = validate_judge_output(
        {
            "dimensions": {
                "correctness": {"score": 5, "rationale": "x"},
                "grounding": {"score": 5, "rationale": "x"},
            },
            "confidence": 1.5,
        },
        RUBRIC,
    )
    assert normalized is None
    assert error == "judge_output_bad_confidence"


def test_build_judge_input_is_json_safe_and_explicit():
    judge_input = build_judge_input(
        task="Do the thing",
        candidate_answer="done",
        expected_behavior="should work",
        evidence_summary={"tool_calls": {"count": 1}},
        rubric=RUBRIC,
    )
    # Must serialize without special encoders.
    text = json.dumps(judge_input)
    assert "Do the thing" in text
    assert judge_input["rubric"]["dimensions"][0]["key"] == "correctness"
    assert judge_input["extra"] == {}
    prompt = build_judge_prompt(judge_input, RUBRIC)
    assert "correctness" in prompt
    assert "grounding" in prompt
    assert "UNTRUSTED DATA" in prompt.upper() or "untrusted data" in prompt
