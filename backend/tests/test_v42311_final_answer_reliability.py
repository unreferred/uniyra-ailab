"""OMNILAB V4.2.3.11 final-answer reliability tests (offline, deterministic).

Covers the canonical per-turn termination states, the bounded forced-
finalization recovery, the streaming accumulator audit, and the structured
FINAL_ANSWER_EMPTY terminal failure. No network, no real LLM.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app import providers as providers_module
from app import services
from app.providers import ExecutionResult
from app.services import execution as execution_module
from app.services import tools as tools_service
from app.services.fetch_providers import STANDARD_FETCH_PROVIDER_ID
from tests.conftest import call

from _support import (
    CapableAdapter,
    create_model,
    create_project,
    create_provider,
    result_error,
    result_success,
    result_with_tools,
)

SEARCH_TOOL_ID = "tool-search-0001"
FETCH_TOOL_ID = "tool-fetch-0001"
ALL_TOOLS = [SEARCH_TOOL_ID, FETCH_TOOL_ID]
SEARCH_PATCH = "app.services.search_providers.TinyFishSearchProvider.search"
FETCH_PATCH = "app.services.fetch_providers.StandardFetchProvider.fetch"


# ------------------------------------------------------------------ helpers
def _install_adapter(monkeypatch, executor):
    factory = lambda provider: CapableAdapter(executor, None, tools=True)  # noqa: E731
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


async def _model_setup(owner):
    provider_id = await create_provider(owner)
    project_id = await create_project(owner)
    model_id = await create_model(owner, provider_id=provider_id, capabilities={"tools": True})
    return project_id, model_id


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


def _reasoning_only(text: str = "internal chain of thought") -> ExecutionResult:
    return ExecutionResult(
        status="success", text="", reasoning_content=text, finish_reason="stop",
    )


async def _execute(owner, model_id, project_id, *, max_tool_rounds=None):
    return await execution_module.execute_model(
        owner, model_id, "", [{"role": "user", "content": "research request"}], {},
        project_id=project_id, enabled_tool_ids=ALL_TOOLS,
        max_tool_rounds=max_tool_rounds,
    )


# ------------------------------------------------- 1: tools then final answer
def test_tools_succeed_then_final_answer(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])
        return result_success("Final forex summary")

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["normalized_response"] == "Final forex summary"
    assert run["forced_finalization"] is False
    assert run["finalization_attempts"] == 0


# --------------------------------------------- 2: many searches then final
def test_many_searches_then_final_answer(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"n": 0}

    def executor(model, req):
        if req.tools is None:
            return result_success("multi-search final")
        state["n"] += 1
        if state["n"] <= 4:
            return result_with_tools(
                tool_calls=[_search_tool_call(f"s{state['n']}", f"alpha {state['n']}")]
            )
        return result_success("multi-search final")

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.side_effect = [
                _search_results(f"alpha {i}", [f"https://example.com/{i}"]) for i in range(1, 5)
            ]
            return await _execute(owner, model_id, project_id)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["search_call_count"] == 4
    assert run["normalized_response"] == "multi-search final"
    assert run["forced_finalization"] is False


# ---------------------------------------------- 3: many fetches then final
def test_many_fetches_then_final_answer(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"n": 0}

    def executor(model, req):
        if req.tools is None:
            return result_success("multi-fetch final")
        state["n"] += 1
        if state["n"] <= 3:
            return result_with_tools(
                tool_calls=[_fetch_tool_call(f"f{state['n']}", f"https://example.com/{state['n']}")]
            )
        return result_success("multi-fetch final")

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(FETCH_PATCH, new_callable=AsyncMock) as m:
            m.side_effect = [
                _fetch_result(f"https://example.com/{i}", f"text {i}") for i in range(1, 4)
            ]
            return await _execute(owner, model_id, project_id)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["fetch_call_count"] == 3
    assert run["normalized_response"] == "multi-fetch final"
    assert run["forced_finalization"] is False


# ---------------------------------------- 4: max rounds -> forced finalization
def test_max_rounds_forced_finalization(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"n": 0}

    def executor(model, req):
        if req.tools is None:
            return result_success("forced final")
        state["n"] += 1
        return result_with_tools(tool_calls=[_search_tool_call(f"s{state['n']}", f"alpha {state['n']}")])

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id, max_tool_rounds=1)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    assert run["finalization_reason"] == "preempt_last_round"
    assert run["normalized_response"] == "forced final"


# -------------------------------------------- 5: no progress -> finalization
def test_no_progress_forced_finalization(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)

    def executor(model, req):
        if req.tools is None:
            return result_success("no-progress final")
        return result_with_tools(tool_calls=[_search_tool_call("loop", "same query")])

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("same query", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id, max_tool_rounds=8)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    assert run["finalization_reason"] in ("exact_repeat", "consecutive_no_progress")


# ------------------------- 6: provider quota after evidence -> finalization
def test_provider_quota_with_evidence_forced_finalization(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"calls": 0}

    def executor(model, req):
        state["calls"] += 1
        if req.tools is None:
            return result_success("degraded final from evidence")
        if state["calls"] == 1:
            return result_with_tools(tool_calls=[_search_tool_call("q1", "alpha")])
        return result_error(category="RATE_LIMIT", message="provider quota reached")

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    assert run["finalization_reason"] == "provider_error"
    assert run["normalized_response"] == "degraded final from evidence"


# --------------------------- 7: first finalization empty -> second succeeds
def test_first_finalization_empty_then_recovery_succeeds(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"final_calls": 0}

    def executor(model, req):
        if req.tools is None:
            state["final_calls"] += 1
            if state["final_calls"] == 1:
                return result_success("")
            return result_success("recovered final")
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id, max_tool_rounds=1)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["finalization_attempts"] == 2
    assert run["finalization_recovered"] is True
    assert run["normalized_response"] == "recovered final"
    assert any(
        entry.get("finalization_classification") == "EMPTY_RESPONSE"
        for entry in run["tool_rounds"]
    )


# ----------------------- 8: first reasoning-only -> second succeeds
def test_first_finalization_reasoning_only_then_recovery_succeeds(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"final_calls": 0}

    def executor(model, req):
        if req.tools is None:
            state["final_calls"] += 1
            if state["final_calls"] == 1:
                return _reasoning_only("hidden reasoning")
            return result_success("visible recovered final")
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id, max_tool_rounds=1)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["finalization_attempts"] == 2
    assert run["normalized_response"] == "visible recovered final"
    assert "hidden reasoning" not in (run["normalized_response"] or "")
    assert any(
        entry.get("finalization_classification") == "REASONING_ONLY"
        for entry in run["tool_rounds"]
    )


# --------------------- 9/16: both attempts empty -> FINAL_ANSWER_EMPTY
def test_both_finalizations_empty_yields_final_answer_empty(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)

    def executor(model, req):
        if req.tools is None:
            return result_success("")
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id, max_tool_rounds=1)

    run = call(app_client, _run)
    assert run["status"] == "error"
    assert run["error"]["code"] == "FINAL_ANSWER_EMPTY"
    assert run["error"]["message"] == (
        "Research completed, but the model failed to generate the final response."
    )
    assert run["finalization_attempts"] == 2
    assert run["finalization_error"] == "FINAL_ANSWER_EMPTY"
    # Evidence must survive a missing final answer.
    assert run["unique_evidence_count"] >= 1
    assert run["search_call_count"] >= 1
    assert run["tool_success_count"] >= 1
    assert run["tool_rounds"]
    assert run["trace_id"]
    assert run["normalized_response"] in (None, "")


# ----------------------- 10: reasoning delta then content delta preserved
def test_reasoning_delta_then_content_delta_content_preserved(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    events: List[Dict[str, Any]] = []

    async def sink(evt):
        events.append(evt)

    adapter = _StreamAdapter([[
        {"type": "MODEL_REASONING_DELTA", "delta": "", "reasoning_delta": "chain of thought"},
        {"type": "MODEL_OUTPUT_DELTA", "delta": "Visible answer"},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop",
         "reasoning_content": "chain of thought", "tool_calls": []},
    ]])
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    run = call(app_client, _run_stream, owner, model_id, project_id, sink)
    assert run["status"] == "success", run.get("error")
    assert run["normalized_response"] == "Visible answer"
    assert run["reasoning_content"] == "chain of thought"
    assert "chain of thought" not in (run["normalized_response"] or "")


# --------------------------------- 11: reasoning never becomes final answer
def test_reasoning_never_becomes_final_answer(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)

    adapter = _StreamAdapter([[
        {"type": "MODEL_REASONING_DELTA", "delta": "", "reasoning_delta": "secret hidden chain"},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop",
         "reasoning_content": "secret hidden chain", "tool_calls": []},
    ]])
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    run = call(app_client, _run_stream, owner, model_id, project_id, None)
    assert "secret hidden chain" not in (run["normalized_response"] or "")
    assert run["normalized_response"] in (None, "")


# --------------------- 12: tool-call fragments do not erase content
def test_tool_call_delta_does_not_erase_content(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)

    adapter = _StreamAdapter([
        [
            {"type": "MODEL_OUTPUT_DELTA", "delta": "Part A commentary"},
            {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": "r1",
             "name": "web_search", "arguments_delta": json.dumps({"query": "alpha"})},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls",
             "tool_calls": []},
        ],
        [
            {"type": "MODEL_OUTPUT_DELTA", "delta": "Final B"},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop", "tool_calls": []},
        ],
    ])
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    run = call(app_client, _run_stream, owner, model_id, project_id, None)
    assert run["status"] == "success", run.get("error")
    assert run["normalized_response"] == "Final B"
    assistant_msgs = [
        m for m in adapter.requests[-1].messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert assistant_msgs and assistant_msgs[0]["content"] == "Part A commentary"


# ------------------- 13: commentary is not mistaken for the final answer
def test_intermediate_commentary_not_final_answer(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(
                text="I'll begin the stress test...",
                tool_calls=[_search_tool_call("s1", "alpha")],
            )
        return result_success("FINAL: supported answer")

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["normalized_response"] == "FINAL: supported answer"
    assert "I'll begin" not in (run["normalized_response"] or "")


# -------------------------- 14: finalization has tools disabled
def test_finalization_has_tools_disabled(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    requests: List[Any] = []

    def executor(model, req):
        requests.append(req)
        if req.tools is None:
            return result_success("forced final")
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(owner, model_id, project_id, max_tool_rounds=1)

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert requests[-1].tools is None
    assert requests[-1].tool_choice is None


# ----------------------- 15: finalization cannot execute another tool
def test_finalization_cannot_execute_another_tool(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"n": 0}

    def executor(model, req):
        state["n"] += 1
        return result_with_tools(
            tool_calls=[_search_tool_call(f"s{state['n']}", f"alpha {state['n']}")]
        )

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.side_effect = [
                _search_results("alpha 1", ["https://example.com/1"]),
                _search_results("alpha 2", ["https://example.com/2"]),
            ]
            return await _execute(owner, model_id, project_id, max_tool_rounds=2)

    run = call(app_client, _run)
    assert run["status"] == "error"
    assert run["error"]["code"] == "FINAL_ANSWER_EMPTY"

    async def _calls():
        return await tools_service.list_tool_calls(owner, run_id=run["id"])

    calls = call(app_client, _calls)
    # Only the two genuine research calls executed; finalization tool calls are
    # never executed.
    assert len(calls) == 2


# ------------------- 17: streaming final answer correlated with trace
def test_streaming_final_answer_correlated_with_trace(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    events: List[Dict[str, Any]] = []

    async def sink(evt):
        events.append(evt)

    adapter = _StreamAdapter([
        [
            {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": "r1",
             "name": "web_search", "arguments_delta": json.dumps({"query": "alpha"})},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls",
             "tool_calls": []},
        ],
        [
            {"type": "MODEL_OUTPUT_DELTA", "delta": "forced final answer"},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop", "tool_calls": []},
        ],
    ])
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    async def fake_execute(tool, arguments, **kwargs):
        return {
            "id": "tc-r1",
            "provider_tool_call_id": kwargs.get("provider_tool_call_id"),
            "execution_status": "success",
            "output": {
                "query": "alpha", "provider": "tinyfish", "count": 1,
                "results": [{"title": "T", "url": "https://example.com/a", "snippet": "s"}],
            },
            "error": None,
            "duration_ms": 2,
        }

    async def _run():
        with patch.object(tools_service, "resolve_enabled_tools",
                          new=AsyncMock(return_value=_tool_docs("web_search"))), \
             patch.object(tools_service, "execute_tool", new=AsyncMock(side_effect=fake_execute)):
            return await execution_module.execute_model(
                owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
                project_id=project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
                max_tool_rounds=1, event_sink=sink, stream_model=True,
            )

    run = call(app_client, _run)
    types = [e.get("type") for e in events]
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    assert "FINALIZATION_STARTED" in types
    assert "FINALIZATION_DELTA" in types
    assert "FINALIZATION_COMPLETED" in types
    assert "".join(
        e.get("delta") or "" for e in events if e.get("type") == "FINALIZATION_DELTA"
    ) == "forced final answer"
    assert run["normalized_response"] == "forced final answer"
    for evt in events:
        if str(evt.get("type", "")).startswith("FINALIZATION"):
            assert evt.get("trace_id") == run["trace_id"]
    started = next(e for e in events if e.get("type") == "FINALIZATION_STARTED")
    assert started.get("tools_enabled") is False


# ----------------------------- extra: empty stream is an empty response
def test_empty_stream_is_empty_response(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)

    adapter = _StreamAdapter([[]])
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    run = call(app_client, _run_stream, owner, model_id, project_id, None)
    assert run["normalized_response"] in (None, "")
    assert run["forced_finalization"] is False


# ----------------------------- extra: finish_reason variants preserved
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_stream_finish_reason_variants_preserved(app_client, as_user, monkeypatch, finish_reason):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)

    adapter = _StreamAdapter([[
        {"type": "MODEL_OUTPUT_DELTA", "delta": "final text"},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": finish_reason, "tool_calls": []},
    ]])
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    run = call(app_client, _run_stream, owner, model_id, project_id, None)
    assert run["normalized_response"] == "final text"
    assert run["finish_reason"] == finish_reason


# --------------------------------------------------------------- stream kit
class _StreamAdapter:
    supports_tools = True
    supports_vision = False
    supports_model_discovery = False

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
        script = self._scripts[self._index] if self._index < len(self._scripts) else []
        self._index += 1
        for evt in script:
            if callable(evt):
                evt()
                continue
            yield evt

    async def discover_models(self):
        raise AssertionError("discover_models must not be called in offline tests")


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


async def _run_stream(owner, model_id, project_id, sink):
    return await execution_module.execute_model(
        owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
        project_id=project_id, event_sink=sink, stream_model=True,
    )
