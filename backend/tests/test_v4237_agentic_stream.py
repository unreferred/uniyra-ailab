"""OMNILAB V4.2.3.7 agentic streaming tests (offline, deterministic).

The stream is a live projection of the single canonical execution loop
(``execute_model``). These tests drive that loop with a fake adapter and a fake
tool executor so the agentic path (reasoning/output/tools), finalization,
partial tool failure, non-streaming fallback and cancellation are all observed
without any network access.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from app import repository as repo
from app.services import execution as execution_module
from app.services import tools as tools_service
from tests.conftest import call

from _support import create_model, create_project, create_provider, result_success

SEARCH_TOOL_ID = "tool-search-0001"
FETCH_TOOL_ID = "tool-fetch-0001"


# --------------------------------------------------------------------- helpers
class _StreamAdapter:
    """Fake adapter driven by per-model-call event scripts.

    ``scripts`` is a list (one entry per model call) of event lists; a callable
    entry is invoked (e.g. to set the cancel event) and contributes no event.
    """

    supports_tools = True
    supports_vision = False

    def __init__(self, scripts: Optional[List[List[Any]]] = None,
                 execute_result: Optional[Any] = None):
        self._scripts = list(scripts or [])
        self._index = 0
        self.execute_result = execute_result
        self.stream_calls = 0

    async def execute(self, model: Dict[str, Any], req: Any):
        if self.execute_result is not None:
            return self.execute_result
        return result_success("answer")

    async def stream(self, model: Dict[str, Any], req: Any):
        self.stream_calls += 1
        script = self._scripts[self._index] if self._index < len(self._scripts) else []
        self._index += 1
        for evt in script:
            if callable(evt):
                evt()
                continue
            yield evt


async def _setup(owner: str, *, provider_type: str = "openai_compatible",
                 tool_capable: bool = True, stream: bool = True):
    provider_id = await create_provider(owner, provider_type=provider_type)
    project_id = await create_project(owner)
    capabilities = {"tools": True, "stream": stream} if tool_capable else {}
    model_id = await create_model(
        owner, provider_id=provider_id, capabilities=capabilities,
    )
    return project_id, model_id


def _install_adapter(monkeypatch, adapter: _StreamAdapter):
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)


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


def _tool_result(call_kwargs: Dict[str, Any], *, success: bool,
                 output: Any = None, error: Any = None,
                 duration_ms: int = 2) -> Dict[str, Any]:
    return {
        "id": "tc-" + str(call_kwargs.get("provider_tool_call_id") or "x"),
        "provider_tool_call_id": call_kwargs.get("provider_tool_call_id"),
        "execution_status": "success" if success else "failed",
        "output": output,
        "error": error,
        "duration_ms": duration_ms,
    }


def _tool_call_stream(call_id: str, name: str, arguments: Any) -> List[Dict[str, Any]]:
    return [
        {
            "type": "MODEL_TOOL_CALL_DELTA",
            "index": 0,
            "id": call_id,
            "name": name,
            "arguments_delta": json.dumps(arguments),
        },
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls", "tool_calls": []},
    ]


def _final_stream(text: str) -> List[Dict[str, Any]]:
    return [
        {"type": "MODEL_OUTPUT_DELTA", "delta": text, "ttft_ms": 1},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop", "tool_calls": []},
    ]


async def _run_with_sink(owner: str, model_id: str, project_id: str, *,
                         enabled_tool_ids: Optional[List[str]] = None,
                         cancel_event: Optional[asyncio.Event] = None):
    events: List[Dict[str, Any]] = []

    async def sink(evt: Dict[str, Any]) -> None:
        events.append(evt)

    run = await execution_module.execute_model(
        owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
        project_id=project_id, enabled_tool_ids=enabled_tool_ids,
        event_sink=sink, stream_model=True, cancel_event=cancel_event,
    )
    return run, events


def _types(events: List[Dict[str, Any]]) -> List[str]:
    return [evt.get("type") for evt in events]


def _deltas(events: List[Dict[str, Any]], evt_type: str) -> str:
    return "".join(
        evt.get("delta") or "" for evt in events if evt.get("type") == evt_type
    )


# ------------------------------------------------------- 1: output deltas
def test_output_deltas_stream_and_run_text_matches(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([[
        {"type": "MODEL_OUTPUT_DELTA", "delta": "Hello ", "ttft_ms": 5},
        {"type": "MODEL_OUTPUT_DELTA", "delta": "world"},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop", "tool_calls": []},
    ]])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)
    types = _types(events)

    assert "RUN_STARTED" in types
    assert "MODEL_STARTED" in types
    assert "MODEL_COMPLETED" in types
    assert types[-1] == "RUN_COMPLETED"
    assert types.index("MODEL_STARTED") < types.index("MODEL_OUTPUT_DELTA")
    assert types.index("MODEL_OUTPUT_DELTA") < types.index("MODEL_COMPLETED")
    assert _deltas(events, "MODEL_OUTPUT_DELTA") == "Hello world"
    assert run["status"] == "success"
    assert run["normalized_response"] == "Hello world"
    assert run["streaming"] is True
    terminal = events[-1]
    assert terminal["run"]["id"] == run["id"]
    assert terminal["full_text"] == "Hello world"

    started = next(e for e in events if e["type"] == "MODEL_STARTED")
    assert started["streaming"] is True


# ---------------------------------------------------- 2: reasoning deltas
def test_reasoning_delta_kept_distinct(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([[
        {"type": "MODEL_REASONING_DELTA", "delta": "", "reasoning_delta": "hidden chain"},
        {"type": "MODEL_OUTPUT_DELTA", "delta": "The answer"},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop",
         "reasoning_content": "hidden chain", "tool_calls": []},
    ]])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)
    assert _deltas(events, "MODEL_REASONING_DELTA") == "hidden chain"
    assert "hidden chain" not in (run["normalized_response"] or "")
    assert run["normalized_response"] == "The answer"
    assert run["reasoning_content"] == "hidden chain"


# ------------------------------- 3: fragmented tool-call args executed once
def test_fragmented_tool_call_args_reconstructed_and_executed_once(
    app_client, as_user, monkeypatch,
):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    seen: List[Dict[str, Any]] = []

    async def fake_execute(tool, arguments, **kwargs):
        seen.append({"tool": tool.get("name"), "arguments": arguments, "kwargs": kwargs})
        return _tool_result(kwargs, success=True, output={
            "query": "omnilab", "provider": "tinyfish", "count": 1,
            "results": [{"title": "T", "url": "https://example.com/x", "snippet": "s"}],
        })

    adapter = _StreamAdapter([
        [
            {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": "call_x",
             "name": "web_search", "arguments_delta": '{"que'},
            {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": None, "name": None,
             "arguments_delta": 'ry": "om'},
            {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": None, "name": None,
             "arguments_delta": 'nilab"}'},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls",
             "tool_calls": []},
        ],
        _final_stream("done"),
    ])
    _install_adapter(monkeypatch, adapter)

    async def _run():
        with patch.object(tools_service, "resolve_enabled_tools",
                          new=AsyncMock(return_value=_tool_docs("web_search"))), \
             patch.object(tools_service, "execute_tool", new=AsyncMock(side_effect=fake_execute)):
            return await _run_with_sink(
                owner, model_id, project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
            )

    run, events = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert len(seen) == 1
    assert seen[0]["tool"] == "web_search"
    assert seen[0]["arguments"] == {"query": "omnilab"}
    assert seen[0]["kwargs"]["provider_tool_call_id"] == "call_x"

    tool_deltas = [e for e in events if e["type"] == "TOOL_CALL_DELTA"]
    assert len(tool_deltas) == 3
    started = next(e for e in events if e["type"] == "TOOL_CALL_STARTED")
    assert started["provider_tool_call_id"] == "call_x"
    args_evt = next(e for e in events if e["type"] == "TOOL_CALL_ARGUMENTS")
    assert args_evt["arguments"] == {"query": "omnilab"}
    completed = next(e for e in events if e["type"] == "TOOL_RESULT_COMPLETED")
    assert completed["provider_tool_call_id"] == "call_x"
    assert completed["tool_call_id"] == "tc-call_x"
    assert completed["tool_name"] == "web_search"
    assert completed["kind"] == "search"


# ------------------------------- 4: tool result success/failure + spans
def test_tool_result_success_and_failure_stream(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)

    async def fake_execute(tool, arguments, **kwargs):
        if tool.get("name") == "web_search":
            return _tool_result(kwargs, success=True, output={
                "query": "alpha", "provider": "tinyfish", "count": 1,
                "results": [{"title": "T", "url": "https://example.com/a", "snippet": "s"}],
            })
        return _tool_result(kwargs, success=False, error={
            "code": "FETCH_AUTH_ERROR", "message": "auth failed", "provider": "standard",
        })

    adapter = _StreamAdapter([
        _tool_call_stream("s1", "web_search", {"query": "alpha"}),
        _tool_call_stream("f1", "web_fetch", {"url": "https://example.com/b"}),
        _final_stream("combined"),
    ])
    _install_adapter(monkeypatch, adapter)

    async def _run():
        with patch.object(tools_service, "resolve_enabled_tools",
                          new=AsyncMock(return_value=_tool_docs("web_search", "web_fetch"))), \
             patch.object(tools_service, "execute_tool", new=AsyncMock(side_effect=fake_execute)):
            run, events = await _run_with_sink(
                owner, model_id, project_id,
                enabled_tool_ids=[SEARCH_TOOL_ID, FETCH_TOOL_ID],
            )
            return run, events

    run, events = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["tool_success_count"] == 1
    assert run["tool_failure_count"] == 1
    assert run["tool_outcome"] == "PARTIAL_TOOL_FAILURE"

    types = _types(events)
    assert "TOOL_RESULT_COMPLETED" in types
    assert "TOOL_RESULT_FAILED" in types
    failed = next(e for e in events if e["type"] == "TOOL_RESULT_FAILED")
    assert failed["error"]["code"] == "FETCH_AUTH_ERROR"
    assert failed["provider_tool_call_id"] == "f1"

    async def _span():
        return await repo.trace_spans.find_one({
            "owner_id": owner, "run_id": run["id"], "span_type": "TOOL_CALL",
        })

    span = call(app_client, _span)
    assert span is not None
    assert span["metadata"]["provider_tool_call_id"] in ("s1", "f1")


# ------------------------------- 5: fetch timeout -> partial success continues
def test_fetch_timeout_then_success_is_partial(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)

    async def fake_execute(tool, arguments, **kwargs):
        url = (arguments or {}).get("url")
        if url == "https://example.com/timedout":
            return _tool_result(kwargs, success=False, error={
                "code": "FETCH_TIMEOUT",
                "message": "Request to URL timed out",
                "provider": "standard",
                "details": {
                    "url": url, "timeout_seconds": 5, "provider": "standard",
                },
            })
        return _tool_result(kwargs, success=True, output={
            "final_url": url, "requested_url": url, "text": "usable evidence",
            "provider": "standard", "metadata": {},
        })

    adapter = _StreamAdapter([
        _tool_call_stream("t1", "web_fetch", {"url": "https://example.com/timedout"}),
        _tool_call_stream("t2", "web_fetch", {"url": "https://example.com/ok"}),
        _final_stream("answer from evidence"),
    ])
    _install_adapter(monkeypatch, adapter)

    async def _run():
        with patch.object(tools_service, "resolve_enabled_tools",
                          new=AsyncMock(return_value=_tool_docs("web_fetch"))), \
             patch.object(tools_service, "execute_tool", new=AsyncMock(side_effect=fake_execute)):
            return await _run_with_sink(
                owner, model_id, project_id, enabled_tool_ids=[FETCH_TOOL_ID],
            )

    run, events = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["tool_outcome"] == "PARTIAL_TOOL_FAILURE"
    assert run["fetch_timeout_count"] >= 1
    assert run["normalized_response"] == "answer from evidence"

    types = _types(events)
    assert "FETCH_FAILED" in types
    assert "FETCH_COMPLETED" in types
    failure = next(e for e in events if e["type"] == "FETCH_FAILED")
    assert failure["error"]["code"] == "FETCH_TIMEOUT"
    assert failure["error"]["timeout_seconds"] == 5
    assert failure["error"]["url"] == "https://example.com/timedout"


# ------------------------------- 6: non-streaming provider fallback
def test_non_streaming_provider_fallback(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner, stream=False)
    adapter = _StreamAdapter(execute_result=result_success("fallback answer"))
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)
    started = next(e for e in events if e["type"] == "MODEL_STARTED")
    assert started["streaming"] is False
    assert adapter.stream_calls == 0
    assert _deltas(events, "MODEL_OUTPUT_DELTA") == "fallback answer"
    assert _types(events)[-1] == "RUN_COMPLETED"
    assert run["status"] == "success"
    assert run["normalized_response"] == "fallback answer"


# ------------------------------------------------------ 7: cancellation
def test_cancellation_emits_run_cancelled(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    cancel_event = asyncio.Event()

    def _cancel() -> None:
        cancel_event.set()

    adapter = _StreamAdapter([[
        {"type": "MODEL_OUTPUT_DELTA", "delta": "partial "},
        _cancel,
        {"type": "MODEL_OUTPUT_DELTA", "delta": "more"},
        {"type": "RUN_COMPLETE", "done": True, "tool_calls": []},
    ]])
    _install_adapter(monkeypatch, adapter)

    run, events = call(
        app_client, _run_with_sink, owner, model_id, project_id,
        cancel_event=cancel_event,
    )
    types = _types(events)
    assert "RUN_CANCELLED" in types
    assert "RUN_COMPLETED" not in types
    assert types[-1] == "RUN_CANCELLED"
    assert run["status"] == "cancelled"
    assert run["error"]["code"] == "CANCELLED"
    assert run["normalized_response"] == "partial "

    async def _persisted():
        return await repo.runs.find_one({"owner_id": owner, "id": run["id"]})

    stored = call(app_client, _persisted)
    assert stored is not None
    assert stored["status"] == "cancelled"


# ------------------------------------------------------ 8: trace correlation
def test_stream_events_correlate_to_run_and_spans(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([[
        {"type": "MODEL_OUTPUT_DELTA", "delta": "hi"},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop", "tool_calls": []},
    ]])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)
    started = next(e for e in events if e["type"] == "MODEL_STARTED")
    model_call_id = started["model_call_id"]
    assert model_call_id

    for evt in events:
        assert evt.get("run_id") == run["id"]
        assert evt.get("trace_id") == run["trace_id"]

    async def _span():
        return await repo.trace_spans.find_one({"owner_id": owner, "id": model_call_id})

    span = call(app_client, _span)
    assert span is not None
    assert span["span_type"] == "MODEL_CALL"
    assert span["run_id"] == run["id"]

    terminal = events[-1]
    assert terminal["type"] == "RUN_COMPLETED"
    assert terminal["run"]["id"] == run["id"]


# ------------------------------- 9: stream_execution queue pump
def test_stream_execution_is_thin_queue_pump(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([_final_stream("pumped answer")])
    _install_adapter(monkeypatch, adapter)

    async def _collect():
        events: List[Dict[str, Any]] = []
        async for evt in execution_module.stream_execution(
            owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
            project_id=project_id,
        ):
            events.append(evt)
        return events

    events = call(app_client, _collect)
    terminal = events[-1]
    assert terminal["type"] == "RUN_COMPLETED"
    assert terminal["run"]["normalized_response"] == "pumped answer"
    assert terminal["full_text"] == "pumped answer"
    assert terminal["tool_outcome"] == "SUCCESS"


# ------------------------------- 10: forced finalization streams deltas
def test_finalization_streams_finalization_deltas(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)

    async def fake_execute(tool, arguments, **kwargs):
        return _tool_result(kwargs, success=True, output={
            "query": "alpha", "provider": "tinyfish", "count": 1,
            "results": [{"title": "T", "url": "https://example.com/a", "snippet": "s"}],
        })

    adapter = _StreamAdapter([
        _tool_call_stream("r1", "web_search", {"query": "alpha"}),
        _final_stream("forced final answer"),
    ])
    _install_adapter(monkeypatch, adapter)

    # max_tool_rounds is not accepted by _run_with_sink; call directly.
    events: List[Dict[str, Any]] = []

    async def sink(evt):
        events.append(evt)

    async def _run_direct():
        with patch.object(tools_service, "resolve_enabled_tools",
                          new=AsyncMock(return_value=_tool_docs("web_search"))), \
             patch.object(tools_service, "execute_tool", new=AsyncMock(side_effect=fake_execute)):
            return await execution_module.execute_model(
                owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
                project_id=project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
                max_tool_rounds=1, event_sink=sink, stream_model=True,
            )

    run = call(app_client, _run_direct)
    types = _types(events)
    assert run["forced_finalization"] is True
    assert "FINALIZATION_STARTED" in types
    assert "FINALIZATION_DELTA" in types
    assert "FINALIZATION_COMPLETED" in types
    assert _deltas(events, "FINALIZATION_DELTA") == "forced final answer"
    assert run["status"] == "success"
