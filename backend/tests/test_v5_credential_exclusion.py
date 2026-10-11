"""V5 credential-exclusion tests for the judge paths.

Pure/offline: every model call is mocked and no database or provider is
touched. Async entry points are driven with ``asyncio.run`` because this suite
has no ``pytest-asyncio``.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.services import evaluation, judge_system, redaction

OPENAI_KEY = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX"
GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
PASSWORD = "hunter2"

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
        }
    ],
}

EXPERIMENT = {"id": "exp-1", "project_id": "proj-1"}


def _valid_response() -> str:
    return json.dumps(
        {
            "dimensions": {"correctness": {"score": 8, "rationale": "Correct."}},
            "overall_result": "pass",
        }
    )


def _judge_run(response: str) -> dict:
    return {
        "id": "judge-run-1",
        "status": "success",
        "normalized_response": response,
        "raw_response": response,
        "model_id": "judge-model",
        "model_name": "Judge Model",
        "model_identifier": "judge/model",
        "provider_id": "prov-1",
        "provider_name": "Judge Provider",
        "family": "judge-family",
        "input_tokens": 10,
        "output_tokens": 5,
        "estimated_cost": 0.001,
        "cost_available": True,
        "error": None,
        "error_message": None,
    }


def test_build_judge_input_deep_redacts_secret_shaped_values():
    result = judge_system.build_judge_input(
        task="Summarize the document.",
        candidate_answer=f"my key is {OPENAI_KEY}",
        evidence_summary={"tool_output": f"token={GITHUB_TOKEN}"},
        extra={"password": PASSWORD},
        rubric=RUBRIC,
    )

    text = json.dumps(result)
    assert redaction.REDACTED in text
    for raw in (OPENAI_KEY, GITHUB_TOKEN, PASSWORD):
        assert raw not in text

    # Every payload section except `extra` is fully secret-free.
    # NOTE: `redaction.contains_secret` cannot be applied to the whole JSON:
    # a sensitive key whose value is already the marker (``"password":
    # "[REDACTED]"``) still matches the generic-credential pattern, so the
    # whole-text check is a false positive in ``redaction.py`` (not owned by
    # judge_system). The raw substrings above are the authoritative check.
    without_extra = json.dumps({k: v for k, v in result.items() if k != "extra"})
    assert not redaction.contains_secret(without_extra)
    assert result["extra"] == {"password": redaction.REDACTED}
    # Non-secret structure and values are preserved.
    assert result["task"] == "Summarize the document."
    assert result["rubric"]["dimensions"][0]["key"] == "correctness"


def test_evaluate_with_judge_redacts_candidate_and_evidence():
    run = {
        "id": "run-1",
        "experiment_id": "exp-1",
        "project_id": "proj-1",
        "status": "success",
        "normalized_response": f"The answer is 42. my key is {OPENAI_KEY}",
        "messages": [{"role": "user", "content": "What is the answer?"}],
    }
    evidence = {
        "tool_calls": {
            "names": ["search"],
            "error": f"tool call failed with token={GITHUB_TOKEN}",
        }
    }
    mock = AsyncMock()
    mock.return_value = _judge_run(_valid_response())

    with patch("app.services.judge_system.execute_model", new=mock):
        result = asyncio.run(
            judge_system.evaluate_with_judge(
                "owner-1",
                EXPERIMENT,
                run,
                judge_model_id="judge-model",
                rubric=RUBRIC,
                evidence_summary=evidence,
            )
        )

    assert result["status"] == "completed"
    judge_input_text = json.dumps(result["judge_input"])
    result_text = json.dumps(result)
    for secret in (OPENAI_KEY, GITHUB_TOKEN):
        assert secret not in judge_input_text
        assert secret not in result_text
    assert redaction.REDACTED in judge_input_text
    assert redaction.REDACTED in result_text
    # The mocked judge still ran with tools disabled.
    assert mock.await_count == 1
    assert mock.call_args.kwargs["enabled_tool_ids"] == []
    assert mock.call_args.kwargs["max_tool_rounds"] == 0


def test_legacy_judge_prompt_redacts_secrets():
    run = {"id": "run-1", "model_name": "Candidate Model"}
    prompt = evaluation._judge_prompt(
        {"input_text": "What is the answer?"},
        f"expected token={GITHUB_TOKEN}",
        f"candidate key {OPENAI_KEY}",
        None,
        run,
    )

    assert isinstance(prompt, str)
    assert OPENAI_KEY not in prompt
    assert GITHUB_TOKEN not in prompt
    assert redaction.REDACTED in prompt
    # Non-secret context is still present.
    assert "What is the answer?" in prompt
