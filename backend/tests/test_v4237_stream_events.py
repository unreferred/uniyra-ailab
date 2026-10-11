"""OMNILAB V4.2.3.7 agentic streaming protocol tests (pure unit, offline).

Covers the canonical :mod:`app.services.stream_events` module only: the
``event()`` builder (correlation keys + payload + JSON safety), terminal-event
detection, ``sse_frame()`` wire encoding round-trip, and the success/failure
shapes of ``tool_result_payload()``. No MongoDB, no app fixtures.
"""
from __future__ import annotations

import json

from app.services import stream_events


# --------------------------------------------------------------- event()
def test_event_minimal_is_type_only():
    evt = stream_events.event(stream_events.RUN_STARTED)
    assert evt == {"type": "RUN_STARTED"}


def test_event_includes_only_provided_correlation_keys():
    evt = stream_events.event(
        stream_events.MODEL_STARTED,
        run_id="run-1",
        round_index=0,
        model_call_id="mc-1",
        tool_call_id=None,
        provider_tool_call_id=None,
    )
    assert evt["type"] == "MODEL_STARTED"
    assert evt["run_id"] == "run-1"
    assert evt["round_index"] == 0
    assert evt["model_call_id"] == "mc-1"
    # None-valued correlation keys are omitted entirely.
    for key in stream_events.CORRELATION_KEYS:
        if key not in ("run_id", "round_index", "model_call_id"):
            assert key not in evt


def test_event_carries_payload_after_correlation_keys():
    evt = stream_events.event(
        stream_events.TOOL_RESULT_COMPLETED,
        run_id="run-1",
        tool_call_id="tc-1",
        success=True,
        duration_ms=12,
    )
    assert evt["success"] is True
    assert evt["duration_ms"] == 12
    assert json.loads(json.dumps(evt)) == evt


def test_event_is_json_serializable_with_default_str():
    evt = stream_events.event(stream_events.RUN_FAILED, error={"code": "X"})
    assert json.loads(json.dumps(evt, default=str))["error"] == {"code": "X"}


# ---------------------------------------------------------- is_terminal()
def test_is_terminal_true_for_terminal_types():
    for event_type in (
        stream_events.RUN_COMPLETED,
        stream_events.RUN_FAILED,
        stream_events.RUN_CANCELLED,
    ):
        assert event_type in stream_events.TERMINAL_EVENT_TYPES
        assert stream_events.is_terminal({"type": event_type}) is True


def test_is_terminal_false_for_non_terminal_types():
    for event_type in (
        stream_events.RUN_STARTED,
        stream_events.MODEL_COMPLETED,
        stream_events.TOOL_RESULT_FAILED,
        stream_events.FINALIZATION_COMPLETED,
    ):
        assert stream_events.is_terminal({"type": event_type}) is False
    assert stream_events.is_terminal({}) is False


# ------------------------------------------------------------ sse_frame()
def test_sse_frame_is_valid_data_frame_and_round_trips():
    evt = stream_events.event(
        stream_events.MODEL_OUTPUT_DELTA,
        run_id="run-1",
        model_call_id="mc-1",
        delta="hello",
    )
    frame = stream_events.sse_frame(evt)
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    payload = frame[len("data: "):-2]
    assert "\n" not in payload
    assert json.loads(payload) == evt


# ---------------------------------------------------- tool_result_payload()
def test_tool_result_payload_success_shape():
    doc = {
        "execution_status": "success",
        "output": {"text": "page body"},
        "error": None,
    }
    out = stream_events.tool_result_payload(doc)
    assert out == {"success": True, "result": {"text": "page body"}}


def test_tool_result_payload_failure_shape_with_optionals():
    doc = {
        "execution_status": "failed",
        "output": None,
        "error": {
            "code": "FETCH_TIMEOUT",
            "message": "fetch timed out",
            "url": "https://example.com/x",
            "timeout_seconds": 15,
            "provider": "standard",
        },
    }
    out = stream_events.tool_result_payload(doc)
    assert out["success"] is False
    err = out["error"]
    assert err["code"] == "FETCH_TIMEOUT"
    assert err["message"] == "fetch timed out"
    assert err["url"] == "https://example.com/x"
    assert err["timeout_seconds"] == 15
    assert err["provider"] == "standard"


def test_tool_result_payload_is_defensive_on_missing_keys():
    assert stream_events.tool_result_payload({}) == {
        "success": False,
        "error": {"code": "TOOL_EXECUTION_FAILED", "message": "Tool execution failed"},
    }
    assert stream_events.tool_result_payload(None)["success"] is False


# ----------------------------------------------------------- constants
def test_constant_groups_cover_all_event_types():
    grouped = (
        stream_events.LIFECYCLE_EVENT_TYPES
        + stream_events.MODEL_EVENT_TYPES
        + stream_events.TOOL_CALL_EVENT_TYPES
        + stream_events.TOOL_RESULT_EVENT_TYPES
        + stream_events.SEARCH_FETCH_EVENT_TYPES
        + stream_events.FINALIZATION_EVENT_TYPES
    )
    assert set(grouped) == set(stream_events.ALL_EVENT_TYPES)
    assert len(grouped) == len(stream_events.ALL_EVENT_TYPES)


def test_tool_kinds_outcomes_and_statuses():
    assert stream_events.TOOL_KINDS == ("search", "fetch", "research", "generic")
    assert stream_events.RUN_TOOL_OUTCOMES == (
        "SUCCESS", "PARTIAL_TOOL_FAILURE", "TOOL_FAILURE",
    )
    assert stream_events.RUN_STATUSES == ("success", "error", "cancelled")
