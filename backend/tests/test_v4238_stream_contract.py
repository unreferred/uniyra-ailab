"""OMNILAB V4.2.3.8 composite stream-event contract tests (pure unit, offline).

Additive contract for the composite ``web_research`` tool: the new ``research``
tool kind, the two internal sub-operation span types, the
``COMPOSITE_OPERATION_EVENT_TYPES`` group, and the internal-operation payload
shape emitted through :func:`app.services.stream_events.event`. Also re-checks
that :func:`app.services.tracing._normalize_span_type` accepts the new span
types. No MongoDB, no app fixtures.
"""
from __future__ import annotations

from app import models
from app.services import stream_events as se
from app.services import tracing


# ------------------------------------------------------- TOOL_KINDS
def test_research_is_a_recognized_tool_kind():
    assert "research" in se.TOOL_KINDS
    # Additive only: the previously supported kinds remain present.
    for kind in ("search", "fetch", "generic"):
        assert kind in se.TOOL_KINDS


# ------------------------------------------------------- SPAN_TYPES
def test_span_types_include_composite_operation_types():
    assert "SEARCH_OPERATION" in models.SPAN_TYPES
    assert "FETCH_OPERATION" in models.SPAN_TYPES


def test_normalize_span_type_accepts_composite_operation_types():
    assert tracing._normalize_span_type("SEARCH_OPERATION") == "SEARCH_OPERATION"
    assert tracing._normalize_span_type("FETCH_OPERATION") == "FETCH_OPERATION"


# ------------------------------------------- internal operation events
def test_composite_operation_event_types_cover_five_events():
    expected = {
        se.SEARCH_STARTED,
        se.SEARCH_COMPLETED,
        se.FETCH_STARTED,
        se.FETCH_COMPLETED,
        se.FETCH_FAILED,
    }
    assert len(expected) == 5
    assert set(se.COMPOSITE_OPERATION_EVENT_TYPES) == expected


def test_internal_operation_event_payload_shape():
    evt = se.event(
        se.FETCH_STARTED,
        run_id="r",
        provider_tool_call_id="c",
        internal=True,
        operation_id="op1",
        operation_kind="fetch",
        url="https://x.test",
    )
    assert evt["type"] == se.FETCH_STARTED
    # Correlation keys flow through.
    assert evt["run_id"] == "r"
    assert evt["provider_tool_call_id"] == "c"
    # Internal-operation payload flows through unchanged.
    assert evt["internal"] is True
    assert evt["operation_id"] == "op1"
    assert evt["operation_kind"] == "fetch"
    assert evt["url"] == "https://x.test"
    # event() omits None-valued correlations, and no None-valued junk is added
    # beyond that existing filtering.
    for key in se.CORRELATION_KEYS:
        if key not in ("run_id", "provider_tool_call_id"):
            assert key not in evt
    assert all(value is not None for value in evt.values())


def test_internal_operation_event_is_never_a_model_tool_call():
    # Internal operations use the search/fetch event names, not the model
    # TOOL_CALL_* / TOOL_RESULT_* protocol.
    assert se.FETCH_STARTED not in se.TOOL_CALL_EVENT_TYPES
    assert se.SEARCH_STARTED not in se.TOOL_RESULT_EVENT_TYPES
    assert se.FETCH_STARTED not in se.TOOL_CALL_EVENT_TYPES


# --------------------------------------------- tool_result_payload()
def test_tool_result_payload_success_shape():
    doc = {"execution_status": "success", "output": {"text": "page body"}}
    assert se.tool_result_payload(doc) == {
        "success": True,
        "result": {"text": "page body"},
    }


def test_tool_result_payload_failure_structured_error_shape():
    doc = {
        "execution_status": "failed",
        "output": None,
        "error": {"code": "FETCH_TIMEOUT", "message": "fetch timed out"},
    }
    out = se.tool_result_payload(doc)
    assert out == {
        "success": False,
        "error": {"code": "FETCH_TIMEOUT", "message": "fetch timed out"},
    }
