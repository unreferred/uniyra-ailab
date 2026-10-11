import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.continuation import ContinuationPolicy, CONTINUATION_INSTRUCTION
from app.services.execution import execute_resumable_model, stream_resumable_execution
import app.services.stream_events as se


import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.continuation import ContinuationPolicy, CONTINUATION_INSTRUCTION
from app.services.execution import execute_resumable_model, stream_resumable_execution
import app.services.stream_events as se


def _run(coro):
    return asyncio.run(coro)


def test_finish_reason_stop_no_continuation():
    """1. finish_reason=stop -> no continuation."""
    async def _test():
        mock_run = {
            "id": "run_1",
            "status": "success",
            "finish_reason": "stop",
            "normalized_response": "Hello world.",
            "output_tokens": 10,
            "input_tokens": 5,
            "trace_id": "tr_1",
            "conversation_id": "c_1",
        }

        with patch("app.services.execution.execute_model", new=AsyncMock(return_value=mock_run)) as mock_exec:
            result = await execute_resumable_model(
                owner_id="u1",
                model_id="m1",
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                params={},
            )
            assert mock_exec.call_count == 1
            assert result["continuation_count"] == 0
            assert result["normalized_response"] == "Hello world."
            assert result["output_limit_exhausted"] is False
            assert result["output_limit"] is False
    _run(_test())


def test_finish_reason_length_automatic_continuation():
    """2. finish_reason=length -> auto-continues until stop."""
    async def _test():
        run_1 = {
            "id": "run_1",
            "status": "success",
            "finish_reason": "length",
            "normalized_response": "Part 1 of the explanation: architecture and security",
            "output_tokens": 50,
            "input_tokens": 10,
            "trace_id": "tr_1",
        }
        run_2 = {
            "id": "run_2",
            "status": "success",
            "finish_reason": "stop",
            "normalized_response": "architecture and security are handled with TLS and RBAC.",
            "output_tokens": 40,
            "input_tokens": 65,
            "trace_id": "tr_1",
        }

        with patch("app.services.execution.execute_model", new=AsyncMock(side_effect=[run_1, run_2])) as mock_exec:
            result = await execute_resumable_model(
                owner_id="u1",
                model_id="m1",
                system_prompt="sys",
                messages=[{"role": "user", "content": "explain"}],
                params={},
            )
            assert mock_exec.call_count == 2
            assert result["continuation_count"] == 1
            assert result["run_chain"] == ["run_1", "run_2"]
            assert result["root_run_id"] == "run_1"
            # Overlap deduplication: "architecture and security" deduplicated
            assert result["normalized_response"] == "Part 1 of the explanation: architecture and security are handled with TLS and RBAC."
            assert result["output_limit"] is False
            assert result["output_limit_exhausted"] is False

            # Verify second call received continuation instruction & parent_run_id
            second_call_kwargs = mock_exec.call_args_list[1][1]
            assert second_call_kwargs["parent_run_id"] == "run_1"
            assert second_call_kwargs["root_run_id"] == "run_1"
            assert second_call_kwargs["continuation_index"] == 1
            assert second_call_kwargs["continuation_reason"] == "output_limit"
    _run(_test())


def test_multiple_length_terminations_up_to_policy_limit():
    """4 & 5. Multiple length terminations continue up to policy limit, then stop safely."""
    async def _test():
        def make_run(idx, reason):
            return {
                "id": f"run_{idx}",
                "status": "success",
                "finish_reason": reason,
                "normalized_response": f" segment_{idx}",
                "output_tokens": 20,
                "input_tokens": 10,
            }

        # Policy limit = 2 continuations (3 runs total)
        runs = [make_run(1, "length"), make_run(2, "length"), make_run(3, "length")]
        policy = ContinuationPolicy(max_auto_continuations=2)

        with patch("app.services.execution.execute_model", new=AsyncMock(side_effect=runs)) as mock_exec:
            result = await execute_resumable_model(
                owner_id="u1",
                model_id="m1",
                system_prompt="sys",
                messages=[{"role": "user", "content": "more"}],
                params={},
                continuation_policy=policy,
            )
            # Ran 1 initial + 2 continuations = 3 runs
            assert mock_exec.call_count == 3
            assert result["continuation_count"] == 2
            assert result["output_limit_exhausted"] is True
            assert result["output_limit"] is True
            assert "exhausted" in result["output_limit_reason"].lower()
    _run(_test())


def test_error_and_cancellation_do_not_auto_continue():
    """12 & 13. Provider/backend error or cancellation -> NO automatic continuation."""
    async def _test():
        error_run = {
            "id": "run_err",
            "status": "error",
            "finish_reason": "error",
            "error": {"code": "PROVIDER_500", "message": "Internal error"},
            "normalized_response": "Partial text before error",
        }
        with patch("app.services.execution.execute_model", new=AsyncMock(return_value=error_run)) as mock_exec:
            result = await execute_resumable_model(
                owner_id="u1",
                model_id="m1",
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                params={},
            )
            assert mock_exec.call_count == 1
            assert result["continuation_count"] == 0
            assert result["status"] == "error"

        cancel_run = {
            "id": "run_cancel",
            "status": "cancelled",
            "finish_reason": "length",  # Even if finish reason was length, cancelled status stops continuation
            "normalized_response": "Partial text",
        }
        with patch("app.services.execution.execute_model", new=AsyncMock(return_value=cancel_run)) as mock_exec:
            result = await execute_resumable_model(
                owner_id="u1",
                model_id="m1",
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                params={},
            )
            assert mock_exec.call_count == 1
            assert result["continuation_count"] == 0
            assert result["status"] == "cancelled"
    _run(_test())


def test_stream_resumable_execution_emits_continuation_events():
    """Streaming projection emits continuation_started notifications and terminal aggregated run."""
    async def _test():
        run_1 = {
            "id": "run_1",
            "status": "success",
            "finish_reason": "length",
            "normalized_response": "Segment one",
            "output_tokens": 15,
            "input_tokens": 5,
        }
        run_2 = {
            "id": "run_2",
            "status": "success",
            "finish_reason": "stop",
            "normalized_response": " and segment two.",
            "output_tokens": 10,
            "input_tokens": 20,
        }

        async def mock_stream_1(*args, **kwargs):
            yield {"type": se.RUN_STARTED, "run_id": "run_1"}
            yield {"type": se.MODEL_OUTPUT_DELTA, "delta": "Segment one"}
            yield se.event(se.RUN_COMPLETED, run_id="run_1", run=run_1, full_text="Segment one")

        async def mock_stream_2(*args, **kwargs):
            yield {"type": se.RUN_STARTED, "run_id": "run_2"}
            yield {"type": se.MODEL_OUTPUT_DELTA, "delta": " and segment two."}
            yield se.event(se.RUN_COMPLETED, run_id="run_2", run=run_2, full_text=" and segment two.")

        with patch("app.services.execution.stream_execution", side_effect=[mock_stream_1(), mock_stream_2()]):
            events = []
            async for evt in stream_resumable_execution(
                owner_id="u1",
                model_id="m1",
                system_prompt="sys",
                messages=[{"role": "user", "content": "go"}],
                params={},
                continuation_policy=ContinuationPolicy(max_auto_continuations=2),
            ):
                events.append(evt)

            event_types = [e.get("type") for e in events]
            assert se.RUN_STARTED in event_types
            assert "continuation_started" in event_types
            assert se.RUN_COMPLETED in event_types

            # Check continuation event
            cont_evt = next(e for e in events if e.get("type") == "continuation_started")
            assert cont_evt["continuation_index"] == 1
            assert "Continuing automatically" in cont_evt["status_text"]

            # Terminal event checks
            term_evt = next(e for e in events if e.get("type") == se.RUN_COMPLETED)
            assert term_evt["continuation_count"] == 1
            assert term_evt["full_text"] == "Segment one and segment two."
            assert term_evt["output_limit"] is False
            assert term_evt["run"]["output_tokens"] == 25
    _run(_test())


def test_manual_continue_starts_a_fresh_bounded_cycle():
    """A new resumable invocation gets a fresh auto-continuation budget."""
    async def _test():
        cycle_a = [
            {"id": "a0", "status": "success", "finish_reason": "length", "normalized_response": "A0"},
            {"id": "a1", "status": "success", "finish_reason": "length", "normalized_response": "A1"},
        ]
        cycle_b = [
            {"id": "b0", "status": "success", "finish_reason": "length", "normalized_response": "B0"},
            {"id": "b1", "status": "success", "finish_reason": "stop", "normalized_response": "B1"},
        ]
        with patch("app.services.execution.execute_model", new=AsyncMock(side_effect=cycle_a)) as mock_exec:
            exhausted = await execute_resumable_model(
                owner_id="u1", model_id="m1", system_prompt="sys",
                messages=[{"role": "user", "content": "first"}], params={},
                continuation_policy=ContinuationPolicy(max_auto_continuations=1),
            )
        assert mock_exec.call_count == 2
        assert exhausted["output_limit_exhausted"] is True
        assert exhausted["continuation_count"] == 1

        with patch("app.services.execution.execute_model", new=AsyncMock(side_effect=cycle_b)) as mock_exec:
            continued = await execute_resumable_model(
                owner_id="u1", model_id="m1", system_prompt="sys",
                messages=[{"role": "user", "content": "first"}], params={},
                parent_run_id="a1",
                continuation_policy=ContinuationPolicy(max_auto_continuations=1),
            )
        assert mock_exec.call_count == 2
        assert continued["output_limit_exhausted"] is False
        assert continued["continuation_count"] == 1
        assert continued["run_chain"] == ["b0", "b1"]
    _run(_test())
