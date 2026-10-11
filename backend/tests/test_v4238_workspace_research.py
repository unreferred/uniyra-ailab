"""OMNILAB V4.2.3.8 composite ``web_research`` workspace-wiring tests.

Coverage:
  1. ``_tool_ids_for`` exposes ``tool-research-0001`` ONLY when BOTH
     ``search_enabled`` and ``fetch_enabled`` are true, and never removes the
     atomic ``tool-search-0001`` / ``tool-fetch-0001``. The composite tool is
     offered alongside the atomic ones so the MODEL chooses.
  2. An explicitly selected ``enabled_tool_ids`` is preserved and de-duplicated.
  3. The request-resolution chain (``_resolve_effective`` + ``_tool_ids_for``)
     does not drop the new tool id.
  4. ``_assistant_message`` copies the run's ``reasoning_segments`` and the
     additive ``research_call_count`` / ``external_request_count`` counters while
     leaving the nested ``telemetry`` block unchanged.

All offline; the only fixture-based test uses a throwaway project.
"""
from __future__ import annotations

from types import SimpleNamespace

from _support import create_project
from app.routers.workspace import (
    _assistant_message,
    _resolve_effective,
    _tool_ids_for,
)
from tests.conftest import call

SEARCH = "tool-search-0001"
FETCH = "tool-fetch-0001"
RESEARCH = "tool-research-0001"
CALC = "tool-calculator-0001"

WEB_TOOLS = (SEARCH, FETCH, RESEARCH)


def _body(*, enabled_tool_ids=None, search_enabled=None, fetch_enabled=None):
    return SimpleNamespace(
        enabled_tool_ids=enabled_tool_ids,
        search_enabled=search_enabled,
        fetch_enabled=fetch_enabled,
    )


# ------------------------------------------------------- _tool_ids_for gating
def test_both_capabilities_expose_search_fetch_and_research():
    ids = _tool_ids_for(_body(search_enabled=True, fetch_enabled=True), {})
    assert SEARCH in ids
    assert FETCH in ids
    assert RESEARCH in ids


def test_search_only_does_not_expose_research_or_fetch():
    ids = _tool_ids_for(_body(search_enabled=True, fetch_enabled=False), {})
    assert SEARCH in ids
    assert RESEARCH not in ids
    assert FETCH not in ids


def test_fetch_only_does_not_expose_research_or_search():
    ids = _tool_ids_for(_body(search_enabled=False, fetch_enabled=True), {})
    assert FETCH in ids
    assert RESEARCH not in ids
    assert SEARCH not in ids


def test_neither_capability_exposes_no_web_tools():
    ids = _tool_ids_for(_body(search_enabled=False, fetch_enabled=False), {})
    assert ids == []
    for tool_id in WEB_TOOLS:
        assert tool_id not in ids


def test_research_is_never_forced_when_only_one_capability_is_on():
    for body in (
        _body(search_enabled=True, fetch_enabled=False),
        _body(search_enabled=False, fetch_enabled=True),
    ):
        assert RESEARCH not in _tool_ids_for(body, {})


def test_capabilities_resolve_from_effective_then_conversation():
    effective = {"search_enabled": True, "fetch_enabled": True}
    ids = _tool_ids_for(_body(), {}, effective)
    assert RESEARCH in ids
    assert SEARCH in ids
    assert FETCH in ids

    ids_conv = _tool_ids_for(_body(), {"search_enabled": True, "fetch_enabled": True})
    assert RESEARCH in ids_conv


# -------------------------------------------- explicit selection + dedupe
def test_explicit_selection_preserved_and_deduplicated():
    body = _body(
        enabled_tool_ids=[CALC, SEARCH, FETCH],
        search_enabled=True,
        fetch_enabled=True,
    )
    ids = _tool_ids_for(body, {})
    # Explicit order preserved; auto-wired tools appended once, no duplicates.
    assert ids[:3] == [CALC, SEARCH, FETCH]
    assert ids.count(SEARCH) == 1
    assert ids.count(FETCH) == 1
    assert ids.count(RESEARCH) == 1
    assert ids[3] == RESEARCH


def test_explicit_selection_without_capabilities_is_untouched():
    body = _body(enabled_tool_ids=[CALC], search_enabled=False, fetch_enabled=False)
    assert _tool_ids_for(body, {}) == [CALC]


# -------------------------------------------- request-resolution chain
def test_resolved_request_config_preserves_research_id(app_client, as_user):
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    body = _body(search_enabled=True, fetch_enabled=True)

    async def _resolve():
        return await _resolve_effective(owner, project_id, {}, body)

    resolved = call(app_client, _resolve)
    effective = resolved.get("effective") or {}
    ids = _tool_ids_for(body, {}, effective)
    assert SEARCH in ids
    assert FETCH in ids
    assert RESEARCH in ids


# ------------------------------------------------------- _assistant_message
def test_assistant_message_copies_reasoning_segments_and_counters():
    segments = [
        {
            "model_call_id": "mc-1",
            "round_index": 0,
            "phase": "tool_loop",
            "content": "weighing options",
            "duration_ms": 12,
        },
        {
            "model_call_id": "mc-2",
            "round_index": 1,
            "phase": "final",
            "content": "final synthesis",
            "duration_ms": 7,
        },
    ]
    run = {
        "id": "run-1",
        "status": "success",
        "normalized_response": "answer",
        "reasoning_segments": segments,
        "research_call_count": 2,
        "external_request_count": 5,
        "tool_success_count": 1,
    }
    msg = _assistant_message("c1", "o1", "p1", "m1", run)

    # reasoning_segments copied verbatim, never merged.
    assert msg["reasoning_segments"] == segments
    # Additive counters.
    assert msg["research_call_count"] == 2
    assert msg["external_request_count"] == 5
    # Existing fields remain.
    assert msg["content"] == "answer"
    assert msg["tool_success_count"] == 1
    # Nested telemetry block is unchanged (no new keys).
    assert msg["telemetry"]["tool_success_count"] == 1
    assert "research_call_count" not in msg["telemetry"]
    assert "external_request_count" not in msg["telemetry"]
    assert "reasoning_segments" not in msg["telemetry"]


def test_assistant_message_defaults_missing_run_fields():
    msg = _assistant_message("c1", "o1", "p1", "m1", {"id": "run-2"})
    assert msg["reasoning_segments"] == []
    assert msg["research_call_count"] == 0
    assert msg["external_request_count"] == 0
