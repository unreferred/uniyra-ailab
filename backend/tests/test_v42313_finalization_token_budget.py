"""OMNILAB V4.2.3.13 finalization token-budget closure tests (offline).

Deterministic, offline coverage for the forced-finalization output-budget,
reasoning-effort, length-recovery and evidence-view contract implemented in
``app/services/execution.py``. Every provider call is scripted locally through
the shared ``_support`` doubles; there is no network and no real LLM.

Each test names the section-10 requirement it maps to (``10.x``).
"""
from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app import config
from app import providers as providers_module
from app import repository as repo
from app import services
from app.providers import ExecutionResult, ExecutionRequest
from app.services import execution as execution_module
from app.services import tools as tools_service
from app.services.fetch_providers import STANDARD_FETCH_PROVIDER_ID
from tests.conftest import call

from _support import (
    CapableAdapter,
    create_model,
    create_project,
    create_provider,
    result_success,
    result_with_tools,
)

SEARCH_TOOL_ID = "tool-search-0001"
FETCH_TOOL_ID = "tool-fetch-0001"
ALL_TOOLS = [SEARCH_TOOL_ID, FETCH_TOOL_ID]
SEARCH_PATCH = "app.services.search_providers.TinyFishSearchProvider.search"
FETCH_PATCH = "app.services.fetch_providers.StandardFetchProvider.fetch"

TERMINAL_TYPES = {"RUN_COMPLETED", "RUN_FAILED", "RUN_CANCELLED"}

# Context window big enough that remaining capacity never clamps the
# finalization budget (``_support.create_model`` seeds only 8192).
LARGE_CONTEXT_WINDOW = 200000

LENGTH_MODE = "FINALIZATION_RECOVERY_LENGTH_MODE"


# ------------------------------------------------------------------ helpers
def _install_adapter(monkeypatch, executor):
    factory = lambda provider: CapableAdapter(executor, None, tools=True)  # noqa: E731
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


def _install_shared_adapter(monkeypatch, adapter):
    factory = lambda provider: adapter  # noqa: E731
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


def _install_stream_adapter(monkeypatch, adapter):
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)


async def _model_setup(owner, capabilities=None):
    provider_id = await create_provider(owner)
    project_id = await create_project(owner)
    model_id = await create_model(
        owner,
        provider_id=provider_id,
        capabilities=capabilities if capabilities is not None else {"tools": True},
    )
    return project_id, model_id, provider_id


async def _set_large_context(owner, model_id, value=LARGE_CONTEXT_WINDOW):
    await repo.models.update(model_id, owner, {"context_window": value})


def _search_results(query, urls):
    return {
        "query": query,
        "provider": "tinyfish",
        "results": [
            {
                "title": f"Result {i}",
                "url": url,
                "snippet": f"Snippet {i}",
                "rank": i + 1,
                "provider_metadata": {"source": "tinyfish"},
            }
            for i, url in enumerate(urls)
        ],
        "count": len(urls),
    }


def _fetch_result(url, text="fetched text"):
    return {
        "requested_url": url,
        "final_url": url,
        "status": 200,
        "content_type": "text/plain",
        "title": None,
        "text": text,
        "provider": STANDARD_FETCH_PROVIDER_ID,
        "metadata": {},
    }


def _search_tool_call(call_id, query):
    return {"id": call_id, "name": "web_search", "arguments": {"query": query}}


def _fetch_tool_call(call_id, url):
    return {"id": call_id, "name": "web_fetch", "arguments": {"url": url}}


def _reasoning_length_result(
    *,
    text: str = "",
    reasoning: str = "internal chain of thought",
    output_tokens: Optional[int] = 8192,
    reasoning_tokens: Optional[int] = 4096,
) -> ExecutionResult:
    return ExecutionResult(
        status="success",
        text=text,
        reasoning_content=reasoning,
        finish_reason="length",
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
    )


async def _execute(
    owner,
    model_id,
    project_id,
    *,
    params=None,
    max_tool_rounds=1,
    sink=None,
    stream_model=False,
    cancel_event=None,
    enabled_tool_ids=ALL_TOOLS,
):
    return await execution_module.execute_model(
        owner, model_id, "system", [{"role": "user", "content": "research request"}],
        params or {}, project_id=project_id, enabled_tool_ids=enabled_tool_ids,
        max_tool_rounds=max_tool_rounds, event_sink=sink, stream_model=stream_model,
        cancel_event=cancel_event,
    )


def _finalization_executor(record, final_text="final synthesis"):
    """Research one round, then answer on finalization; records finalization reqs."""
    state = {"n": 0}

    def executor(model, req):
        if req.tools is None:
            record.append(req)
            return result_success(final_text)
        state["n"] += 1
        return result_with_tools(
            tool_calls=[_search_tool_call(f"s{state['n']}", f"alpha {state['n']}")]
        )

    return executor


def _length_recovery_executor(record, recovery_text="recovered visible final"):
    """Attempt-1 finalization is reasoning-only + length; attempt-2 recovers."""
    def executor(model, req):
        if req.tools is None:
            record.append(req)
            if len(record) == 1:
                return _reasoning_length_result()
            return result_success(recovery_text)
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    return executor


def _terminals(events):
    return [e.get("type") for e in events if e.get("type") in TERMINAL_TYPES]


def _timeouts(monkeypatch, *, connection=0.5, idle=2.0, max_duration=5.0):
    monkeypatch.setattr(config, "MODEL_CONNECTION_TIMEOUT_SECONDS", connection)
    monkeypatch.setattr(config, "MODEL_IDLE_TIMEOUT_SECONDS", idle)
    monkeypatch.setattr(config, "MODEL_CALL_MAX_DURATION_SECONDS", max_duration)


# ------------------------------------------------ 10.1 long research run
def test_10_01_long_research_run_reaches_forced_finalization(
    app_client, as_user, monkeypatch,
):
    """10.1 many rounds/evidence -> forced_finalization is True."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    state = {"n": 0}

    def executor(model, req):
        if req.tools is None:
            return result_success("final synthesis from evidence")
        state["n"] += 1
        return result_with_tools(
            tool_calls=[_search_tool_call(f"s{state['n']}", f"alpha query {state['n']}")]
        )

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.side_effect = [
                _search_results(f"alpha query {i}", [f"https://example.com/{i}"])
                for i in range(1, 4)
            ]
            return await _execute(owner, model_id, project_id, max_tool_rounds=3)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    assert run["search_call_count"] == 3
    assert run["unique_evidence_count"] >= 1
    assert run["finalization_attempts"] >= 1
    assert run["normalized_response"] == "final synthesis from evidence"


# --------------------------------- 10.2 attempt-1 reasoning-only length
def test_10_02_attempt_one_reasoning_only_length_records_requested_budget(
    app_client, as_user, monkeypatch,
):
    """10.2 attempt 1 reasoning-only + length; executor records req.max_tokens."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    call(app_client, _set_large_context, owner, model_id)
    final_requests: List[ExecutionRequest] = []
    _install_adapter(monkeypatch, _length_recovery_executor(final_requests))

    run = call(app_client, lambda: _execute_wrapped_search(owner, model_id, project_id))

    assert run["status"] == "success", run.get("error")
    assert len(final_requests) == 2
    assert final_requests[0].max_tokens == 8192
    assert final_requests[0].tools is None
    telemetry = run["finalization_telemetry"]
    assert telemetry[0]["finish_reason"] == "length"
    assert telemetry[0]["had_reasoning"] is True
    assert telemetry[0]["had_visible_content"] is False
    assert telemetry[0]["output_tokens"] == 8192
    assert telemetry[0]["reasoning_tokens"] == 4096
    assert telemetry[0]["requested_max_output_tokens"] == 8192
    assert run["normalized_response"] == "recovered visible final"


async def _execute_wrapped_search(owner, model_id, project_id):
    with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
        m.return_value = _search_results("alpha", ["https://example.com/a"])
        return await _execute(owner, model_id, project_id)


# ------------------------------------- 10.3 attempt 2 length recovery
def test_10_03_attempt_two_enters_length_recovery_mode(
    app_client, as_user, monkeypatch,
):
    """10.3 attempt 2 uses FINALIZATION_RECOVERY_LENGTH_MODE + length notice."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    call(app_client, _set_large_context, owner, model_id)
    final_requests: List[ExecutionRequest] = []
    events: List[Dict[str, Any]] = []

    async def sink(evt):
        events.append(evt)

    _install_adapter(monkeypatch, _length_recovery_executor(final_requests))

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id, sink=sink)

    run = call(app_client, _run)
    assert run["finalization_recovery_mode"] == LENGTH_MODE
    assert run["finalization_recovered"] is True
    assert run["finalization_telemetry"][1]["recovery_mode"] == LENGTH_MODE
    # Second finalization request carries the length-recovery instruction.
    second_messages = final_requests[1].messages
    joined = "\n".join(
        str(m.get("content") or "") for m in second_messages if isinstance(m, dict)
    )
    assert execution_module._FINALIZATION_LENGTH_RECOVERY_INSTRUCTION in joined
    started = [e for e in events if e.get("type") == "FINALIZATION_STARTED"]
    assert len(started) == 2
    assert started[1]["attempt"] == 2
    assert started[1]["recovery"] is True
    assert started[1]["recovery_reason"] == "length"
    assert started[1]["recovery_mode"] == LENGTH_MODE


# -------------------------------- 10.4 distinct effective output budget
def test_10_04_distinct_effective_output_budget_per_attempt(
    app_client, as_user, monkeypatch,
):
    """10.4 with a large context, attempt 1 == 8192 and attempt 2 == 16384."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    call(app_client, _set_large_context, owner, model_id)
    final_requests: List[ExecutionRequest] = []
    _install_adapter(monkeypatch, _length_recovery_executor(final_requests))

    run = call(app_client, lambda: _execute_wrapped_search(owner, model_id, project_id))

    assert final_requests[0].max_tokens == 8192
    assert final_requests[1].max_tokens == 16384
    assert run["finalization_telemetry"][0]["requested_max_output_tokens"] == 8192
    assert run["finalization_telemetry"][1]["requested_max_output_tokens"] == 16384
    assert run["finalization_telemetry"][0]["effective_max_output_tokens"] == 8192
    assert run["finalization_telemetry"][1]["effective_max_output_tokens"] == 16384
    assert run["finalization_requested_max_output_tokens"] == 16384


def test_10_04_resolve_finalization_budget_keys_and_user_override():
    """10.4 helper: explicit params['max_tokens'] raises, never lowers, requested."""
    model = {"context_window": LARGE_CONTEXT_WINDOW}
    budget = execution_module._resolve_finalization_budget(
        {"max_tokens": 32000}, recovery=False, model=model,
        estimated_input_tokens=100,
    )
    assert set(budget) == {
        "requested_max_output_tokens", "effective_max_output_tokens",
        "context_window", "remaining_context_capacity",
    }
    assert budget["requested_max_output_tokens"] == 32000
    assert budget["effective_max_output_tokens"] == 32000
    assert budget["context_window"] == LARGE_CONTEXT_WINDOW
    assert budget["remaining_context_capacity"] == LARGE_CONTEXT_WINDOW - 100
    lower = execution_module._resolve_finalization_budget(
        {"max_tokens": 1}, recovery=False, model=model, estimated_input_tokens=100,
    )
    assert lower["requested_max_output_tokens"] == 8192
    recovery = execution_module._resolve_finalization_budget(
        {}, recovery=True, model=model, estimated_input_tokens=100,
    )
    assert recovery["requested_max_output_tokens"] == 16384


# --------------------------------------- 10.5 reasoning reduction valid
@pytest.mark.parametrize(
    "declared,expected",
    [
        (["low", "high"], "low"),
        (True, "low"),
        (["high"], "high"),
    ],
)
def test_10_05_reasoning_reduction_applied_when_valid(
    app_client, as_user, monkeypatch, declared, expected,
):
    """10.5 provider+model support -> finalization reasoning_effort is set."""
    owner = as_user["id"]
    project_id, model_id, _ = call(
        app_client, _model_setup, owner, {"tools": True, "reasoning_effort": declared},
    )
    call(app_client, _set_large_context, owner, model_id)
    final_requests: List[ExecutionRequest] = []
    adapter = CapableAdapter(
        _finalization_executor(final_requests), None, tools=True,
    )
    adapter.supports_reasoning_effort = True
    _install_shared_adapter(monkeypatch, adapter)

    run = call(app_client, lambda: _execute_wrapped_search(owner, model_id, project_id))

    assert run["status"] == "success", run.get("error")
    assert final_requests[0].reasoning_effort == expected
    assert run["finalization_telemetry"][0]["reasoning_effort"] == expected
    assert run["finalization_telemetry"][0]["reasoning_reduction_applied"] is True


# ---------------------------------- 10.6 unsupported reasoning control
@pytest.mark.parametrize(
    "adapter_flag,capabilities",
    [
        (True, {"tools": True}),
        (False, {"tools": True, "reasoning_effort": ["low", "high"]}),
        (False, {"tools": True}),
    ],
)
def test_10_06_reasoning_control_not_invented(
    app_client, as_user, monkeypatch, adapter_flag, capabilities,
):
    """10.6 no adapter flag or no model capability -> reasoning_effort is None."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner, capabilities)
    call(app_client, _set_large_context, owner, model_id)
    final_requests: List[ExecutionRequest] = []
    executor = _finalization_executor(final_requests)
    if adapter_flag:
        adapter = CapableAdapter(executor, None, tools=True)
        adapter.supports_reasoning_effort = True
        _install_shared_adapter(monkeypatch, adapter)
    else:
        _install_adapter(monkeypatch, executor)

    run = call(app_client, lambda: _execute_wrapped_search(owner, model_id, project_id))

    assert run["status"] == "success", run.get("error")
    assert final_requests[0].reasoning_effort is None
    assert run["finalization_telemetry"][0]["reasoning_reduction_applied"] is False


# --------------------------------- 10.7 evidence view original user task
def test_10_07_evidence_view_preserves_original_user_task():
    """10.7 _build_finalization_evidence_view keeps the user's original task."""
    base_messages = [
        {"role": "system", "content": "you are omni"},
        {"role": "user", "content": "Find the best forex broker for a small account"},
    ]
    messages, _stats = execution_module._build_finalization_evidence_view(
        base_messages, [], [], instruction="FINALIZE NOW",
        tool_failure_count=0, tool_success_count=0,
    )
    assert any(
        isinstance(m, dict) and m.get("role") == "user"
        and "Find the best forex broker for a small account" in str(m.get("content"))
        for m in messages
    )


# ------------------------------- 10.8 evidence view source provenance
def test_10_08_evidence_view_preserves_source_provenance():
    """10.8 search url + fetch url + titles are present in the returned messages."""
    search_evidence = [{
        "query": "forex broker",
        "normalized_query": "forex broker",
        "urls": ["https://search.example.com/a"],
        "titles": ["Search Result Title"],
    }]
    fetch_evidence = [{
        "url": "https://fetch.example.com/page",
        "normalized_url": "https://fetch.example.com/page",
        "title": "Fetch Page Title",
        "text_snippet": "fetched snippet body",
    }]
    messages, stats = execution_module._build_finalization_evidence_view(
        [{"role": "user", "content": "task"}], search_evidence, fetch_evidence,
        instruction="FINALIZE", tool_failure_count=0, tool_success_count=2,
    )
    blob = "\n".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
    assert "https://search.example.com/a" in blob
    assert "https://fetch.example.com/page" in blob
    assert "Search Result Title" in blob
    assert "Fetch Page Title" in blob
    assert stats["evidence_item_count"] == 2
    assert stats["source_count"] == 2


# ------------------------------------- 10.9 duplicate evidence dedup
def test_10_09_duplicate_tool_evidence_deduplicated():
    """10.9 duplicate normalized_query/url entries are deduplicated."""
    search_evidence = [
        {"query": "alpha", "normalized_query": "alpha",
         "urls": ["https://a.example/x", "https://a.example/x"],
         "titles": ["A", "A2"]},
        {"query": "alpha", "normalized_query": "alpha",
         "urls": ["https://a.example/x"], "titles": ["A3"]},
    ]
    fetch_evidence = [
        {"url": "https://f.example/p", "normalized_url": "https://f.example/p",
         "title": "F", "text_snippet": "x"},
        {"url": "https://f.example/p", "normalized_url": "https://f.example/p",
         "title": "F", "text_snippet": "x"},
    ]
    _messages, stats = execution_module._build_finalization_evidence_view(
        [{"role": "user", "content": "task"}], search_evidence, fetch_evidence,
        instruction="FINALIZE", tool_failure_count=0, tool_success_count=4,
    )
    assert stats["deduplicated_item_count"] > 0
    assert stats["source_count"] == 2
    assert stats["evidence_item_count"] == 2


# ------------------------------- 10.10 canonical run/trace complete
def test_10_10_canonical_run_and_trace_complete_in_evidence_view(
    app_client, as_user, monkeypatch,
):
    """10.10 EVIDENCE_VIEW mode keeps tool_rounds + persisted tool calls."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    monkeypatch.setattr(config, "FINALIZATION_EVIDENCE_VIEW_MODE", "evidence_view")
    final_requests: List[ExecutionRequest] = []
    _install_adapter(monkeypatch, _finalization_executor(final_requests))

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["finalization_context_mode"] == "EVIDENCE_VIEW"
    assert run["evidence_item_count"] >= 1
    assert run["source_count"] >= 1
    assert run["trace_id"]
    research_rounds = [
        r for r in run["tool_rounds"]
        if r.get("tool_name") == "web_search" and r.get("execution_status") == "success"
    ]
    assert research_rounds

    async def _list_calls():
        return await tools_service.list_tool_calls(owner, run_id=run["id"])

    calls = call(app_client, _list_calls)
    assert len(calls) >= 1


# --------------------------------- 10.11 null-safe token telemetry
def test_10_11_token_telemetry_null_safe():
    """10.11 None provider token fields yield None telemetry without raising."""
    budget = execution_module._resolve_finalization_budget(
        {}, recovery=False, model={"context_window": LARGE_CONTEXT_WINDOW},
        estimated_input_tokens=100,
    )
    result = ExecutionResult(
        status="success", text=None, input_tokens=None, output_tokens=None,
        reasoning_tokens=None, finish_reason=None,
    )
    telemetry = execution_module._finalization_attempt_telemetry(
        result, attempt=1, recovery=False, budget=budget,
        estimated_context_tokens=100, recovery_mode=None,
        reasoning_effort=None, reasoning_reduction_applied=False,
    )
    assert telemetry["input_tokens"] is None
    assert telemetry["output_tokens"] is None
    assert telemetry["reasoning_tokens"] is None
    assert telemetry["total_completion_tokens"] is None
    assert telemetry["visible_content_tokens"] is None
    assert telemetry["finish_reason"] is None
    assert telemetry["had_reasoning"] is False
    assert telemetry["had_visible_content"] is False


# ------------------------- 10.12 useful partial length answer kept
def test_10_12_length_with_useful_partial_answer_preserved(
    app_client, as_user, monkeypatch,
):
    """10.12 substantial text + finish_reason length is the final answer."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    call(app_client, _set_large_context, owner, model_id)
    long_answer = "Substantive final answer. " * 40  # > FINALIZATION_PARTIAL_MIN_CHARS
    assert len(long_answer) >= config.FINALIZATION_PARTIAL_MIN_CHARS
    final_requests: List[ExecutionRequest] = []

    def executor(model, req):
        if req.tools is None:
            final_requests.append(req)
            return ExecutionResult(
                status="success", text=long_answer, finish_reason="length",
                output_tokens=100, reasoning_tokens=10,
            )
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    _install_adapter(monkeypatch, executor)

    run = call(app_client, lambda: _execute_wrapped_search(owner, model_id, project_id))

    assert run["status"] == "success", run.get("error")
    assert len(final_requests) == 1
    assert run["normalized_response"] == long_answer
    assert run["finalization_truncated"] is True
    assert run["finalization_partial_answer"] is False
    assert run["finalization_error"] is None
    assert run["finalization_attempts"] == 1


# ------------------------------- 10.13 streaming order preserved
def test_10_13_finalization_streaming_ordered(app_client, as_user, monkeypatch):
    """10.13 FINALIZATION_STARTED < DELTA < COMPLETED; deltas concatenate."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    events: List[Dict[str, Any]] = []

    async def sink(evt):
        events.append(evt)

    adapter = _ScriptAdapter([
        _tool_call_script("call-1", "web_search", {"query": "alpha"}),
        [
            {"type": "MODEL_OUTPUT_DELTA", "delta": "Part one. ", "ttft_ms": 1},
            {"type": "MODEL_OUTPUT_DELTA", "delta": "Part two."},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop",
             "tool_calls": []},
        ],
    ])
    _install_stream_adapter(monkeypatch, adapter)

    run = call(
        app_client, _run_stream_tool_round, owner, model_id, project_id, sink,
    )

    types = [e.get("type") for e in events]
    assert "FINALIZATION_STARTED" in types
    assert "FINALIZATION_DELTA" in types
    assert "FINALIZATION_COMPLETED" in types
    assert types.index("FINALIZATION_STARTED") < types.index("FINALIZATION_DELTA")
    assert types.index("FINALIZATION_DELTA") < types.index("FINALIZATION_COMPLETED")
    streamed = "".join(
        e.get("delta") or "" for e in events if e.get("type") == "FINALIZATION_DELTA"
    )
    assert streamed == "Part one. Part two."
    assert run["normalized_response"] == "Part one. Part two."


# ---------------------------------- 10.14 tools disabled both
def test_10_14_tools_disabled_both_attempts(app_client, as_user, monkeypatch):
    """10.14 both finalization attempts have tools is None / tool_choice None."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    call(app_client, _set_large_context, owner, model_id)
    final_requests: List[ExecutionRequest] = []
    _install_adapter(monkeypatch, _length_recovery_executor(final_requests))

    run = call(app_client, lambda: _execute_wrapped_search(owner, model_id, project_id))

    assert run["status"] == "success", run.get("error")
    assert len(final_requests) == 2
    for request in final_requests:
        assert request.tools is None
        assert request.tool_choice is None


# -------------------------------- 10.15 no third attempt
def test_10_15_no_third_finalization_attempt(app_client, as_user, monkeypatch):
    """10.15 the finalization cap is two attempts, never a third."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    call(app_client, _set_large_context, owner, model_id)
    final_requests: List[ExecutionRequest] = []

    def executor(model, req):
        if req.tools is None:
            final_requests.append(req)
            return result_success("")
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    _install_adapter(monkeypatch, executor)

    run = call(app_client, lambda: _execute_wrapped_search(owner, model_id, project_id))

    assert run["finalization_attempts"] <= 2
    assert len(final_requests) == 2
    assert config.MAX_FINALIZATION_ATTEMPTS == 2
    assert run["error"]["code"] == "FINAL_ANSWER_EMPTY"


# ------------------------------- 10.16 watchdog bounds calls
def test_10_16_watchdog_bounds_finalization_calls(app_client, as_user, monkeypatch):
    """10.16 a hanging finalization stream times out but the run ends cleanly."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    _timeouts(monkeypatch, connection=0.15, idle=5.0, max_duration=5.0)
    events: List[Dict[str, Any]] = []

    async def sink(evt):
        events.append(evt)

    adapter = _ScriptAdapter([
        _tool_call_script("call-1", "web_search", {"query": "alpha"}),
        [
            {"type": "MODEL_REASONING_DELTA", "delta": "",
             "reasoning_delta": "hidden"},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "length",
             "reasoning_content": "hidden", "tool_calls": []},
        ],
        [5.0],
    ])
    _install_stream_adapter(monkeypatch, adapter)

    run = call(
        app_client, _run_stream_tool_round, owner, model_id, project_id, sink,
    )

    timeout_events = [e for e in events if e.get("type") == "MODEL_CALL_TIMEOUT"]
    assert timeout_events
    assert timeout_events[0]["timeout_kind"] == "connection"
    assert run["status"] == "error"
    assert run["finalization_attempts"] == 2
    assert len(_terminals(events)) == 1
    assert adapter.stream_calls == 3  # research + two bounded finalization calls


# -------------------------- 10.17 cancellation beats recovery
def test_10_17_cancellation_wins_over_recovery(app_client, as_user, monkeypatch):
    """10.17 cancel after attempt 1 -> RUN_CANCELLED, no recovery MODEL call."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    call(app_client, _set_large_context, owner, model_id)
    cancel_event = asyncio.Event()
    final_requests: List[ExecutionRequest] = []
    all_requests: List[ExecutionRequest] = []

    def executor(model, req):
        all_requests.append(req)
        if req.tools is None:
            final_requests.append(req)
            cancel_event.set()
            return _reasoning_length_result()
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(
                owner, model_id, project_id, cancel_event=cancel_event,
            )

    run = call(app_client, _run)
    assert run["status"] == "cancelled"
    assert run["error"]["code"] == "CANCELLED"
    assert len(final_requests) == 1  # no recovery MODEL call
    assert run["finalization_attempts"] <= 1


# -------------------------------- 10.18 exactly one terminal
def test_10_18_exactly_one_terminal_event(app_client, as_user, monkeypatch):
    """10.18 only one of RUN_COMPLETED/RUN_FAILED/RUN_CANCELLED is emitted."""
    owner = as_user["id"]
    project_id, model_id, _ = call(app_client, _model_setup, owner)
    events: List[Dict[str, Any]] = []

    async def sink(evt):
        events.append(evt)

    def executor(model, req):
        if req.tools is None:
            return result_success("")
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id, sink=sink)

    run = call(app_client, _run)
    terminals = _terminals(events)
    assert len(terminals) == 1
    assert terminals[0] == "RUN_FAILED"
    assert run["error"]["code"] == "FINAL_ANSWER_EMPTY"


# ---------------------------------- 10.19 no secret leakage
def test_10_19_no_secret_leakage(app_client, as_user, monkeypatch):
    """10.19 the provider api key never appears in events or the run."""
    owner = as_user["id"]
    project_id, model_id, provider_id = call(app_client, _model_setup, owner)
    call(app_client, _set_large_context, owner, model_id)
    secret = "sk-live-SUPERSECRET-9f3a2b7c"

    async def _seed_secret():
        await repo.providers.update(provider_id, owner, {"api_key_enc": secret})

    call(app_client, _seed_secret)
    events: List[Dict[str, Any]] = []
    final_requests: List[ExecutionRequest] = []

    async def sink(evt):
        events.append(evt)

    _install_adapter(monkeypatch, _finalization_executor(final_requests))

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id, sink=sink)

    run = call(app_client, _run)
    blob = json.dumps(events, default=str) + json.dumps(run, default=str)
    assert secret not in blob
    for forbidden in ("api_key", "apikey", "x-api-key", "bearer"):
        assert forbidden not in json.dumps(run, default=str).lower()


# --------------------- 10.20 V4.2.3.11/.12 regression invariants
def test_10_20_v42311_v42312_regression_invariants():
    """10.20 attempt cap holds and the prior v42311/v42312 modules import."""
    assert config.MAX_FINALIZATION_ATTEMPTS == 2
    assert execution_module._FINALIZATION_RECOVERY_LENGTH_MODE == LENGTH_MODE
    assert execution_module._FINALIZATION_RECOVERY_EMPTY_MODE == (
        "FINALIZATION_RECOVERY_EMPTY_MODE"
    )
    for name in (
        "_is_length_finish",
        "_visible_content_usable",
        "_estimate_messages_tokens",
        "_finalization_reasoning_effort",
        "_resolve_finalization_budget",
        "_length_failure_kind",
        "_finalization_attempt_telemetry",
        "_build_finalization_evidence_view",
        "_choose_finalization_context_mode",
    ):
        assert callable(getattr(execution_module, name))
    for module_name in (
        "tests.test_v42311_final_answer_reliability",
        "tests.test_v42312_model_watchdog",
        "tests.test_v42312_web_research_lab_mode",
    ):
        assert importlib.import_module(module_name) is not None


# --------------------------------------------------------------- stream kit
class _ScriptAdapter:
    supports_tools = True
    supports_vision = False
    supports_model_discovery = False
    supports_reasoning_effort = False

    def __init__(self, scripts: Optional[List[List[Any]]] = None):
        self._scripts = list(scripts or [])
        self._index = 0
        self.requests: List[Any] = []
        self.stream_calls = 0

    async def execute(self, model, req):
        self.requests.append(req)
        return result_success("answer")

    async def stream(self, model, req):
        self.requests.append(req)
        self.stream_calls += 1
        idx = self._index
        self._index += 1
        script = self._scripts[idx] if idx < len(self._scripts) else []
        for item in script:
            if isinstance(item, bool):
                yield item
            elif isinstance(item, (int, float)):
                await asyncio.sleep(item)
            elif callable(item):
                item()
            else:
                yield item

    async def discover_models(self):
        raise AssertionError("discover_models must not be called in offline tests")


def _tool_call_script(call_id: str, name: str, arguments: Any) -> List[Dict[str, Any]]:
    return [
        {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": call_id,
         "name": name, "arguments_delta": json.dumps(arguments)},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls",
         "tool_calls": []},
    ]


def _tool_docs(*names: str) -> List[Dict[str, Any]]:
    docs = []
    for name in names:
        docs.append({
            "id": SEARCH_TOOL_ID if name == "web_search" else FETCH_TOOL_ID,
            "name": name,
            "display_name": name,
            "description": name,
            "input_schema": {"type": "object", "properties": {}},
            "permissions": [],
            "tool_type": "function",
            "enabled": True,
        })
    return docs


async def _run_stream_tool_round(owner, model_id, project_id, sink):
    async def fake_execute(tool, arguments, **kwargs):
        return {
            "id": "tc-search",
            "provider_tool_call_id": kwargs.get("provider_tool_call_id"),
            "execution_status": "success",
            "output": {
                "query": (arguments or {}).get("query"),
                "provider": "tinyfish",
                "count": 1,
                "results": [{
                    "title": "T", "url": "https://example.com/a", "snippet": "s",
                }],
            },
            "error": None,
            "duration_ms": 2,
        }

    with patch.object(
        tools_service, "resolve_enabled_tools",
        new=AsyncMock(return_value=_tool_docs("web_search")),
    ), patch.object(
        tools_service, "execute_tool", new=AsyncMock(side_effect=fake_execute),
    ):
        return await execution_module.execute_model(
            owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
            project_id=project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
            max_tool_rounds=1, event_sink=sink, stream_model=True,
        )
