"""OMNILAB V4.2.3.8 composite ``web_research`` execution-loop tests (offline).

Proves the canonical loop integrates the composite ``web_research`` builtin
WITHOUT a second execution engine:

* ``_tool_kind("web_research") == "research"`` -- observed through the projected
  ``TOOL_CALL_STARTED`` ``kind`` and the research-only behaviour.
* ONE model ToolCall fans out to internal search/fetch operations that are
  emitted live as ``SEARCH_*`` / ``FETCH_*`` events carrying ``internal=True``,
  ``operation_id`` and the parent ``provider_tool_call_id`` -- never as extra
  model ToolCalls and with no per-operation model call.
* The executor's ``internal_budget`` folds exactly once into the run's shared
  search/fetch/external counters.
* Internal operations nest as CHILD spans (``SEARCH_OPERATION`` /
  ``FETCH_OPERATION``) under the outer ``TOOL_CALL`` span.
* Reasoning is segmented one entry per model call (distinct ``model_call_id``).
* ``PARTIAL_SUCCESS`` stays a successful run.
* An exhausted search/fetch budget skips the composite call before any internal
  operation runs.

All network access is mocked: ``execute_tool`` is replaced with an AsyncMock so
the real ``builtin:web_research`` executor is never required.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from app import config
from app import repository as repo
from app.services import execution as execution_module
from app.services import tools as tools_service
from tests.conftest import call

from _support import create_model, create_project, create_provider, result_success

RESEARCH_TOOL_ID = "tool-research-0001"

INTERNAL_EVENT_TYPES = (
    "SEARCH_STARTED", "SEARCH_COMPLETED",
    "FETCH_STARTED", "FETCH_COMPLETED", "FETCH_FAILED",
)


# --------------------------------------------------------------------- helpers
class _StreamAdapter:
    """Fake adapter driven by per-model-call event scripts (offline)."""

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


async def _setup(owner: str):
    provider_id = await create_provider(owner, provider_type="openai_compatible")
    project_id = await create_project(owner)
    model_id = await create_model(
        owner, provider_id=provider_id, capabilities={"tools": True},
    )
    return project_id, model_id


def _install_adapter(monkeypatch, adapter: _StreamAdapter):
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)


def _research_tool_doc() -> Dict[str, Any]:
    return {
        "id": RESEARCH_TOOL_ID,
        "name": "web_research",
        "display_name": "Web Research",
        "description": "Composite search + fetch research.",
        "input_schema": {"type": "object", "properties": {}},
        "permissions": [],
        "tool_type": "function",
        "enabled": True,
    }


def _research_fixture(
    *,
    outcome: str = "SUCCESS",
    search_calls: int = 2,
    fetch_calls: int = 1,
    external_requests: int = 3,
    failed_source: bool = False,
):
    """Return ``(output, ops)`` matching the FROZEN web_research output shape."""
    ops: List[Dict[str, Any]] = [
        {"op": "start", "kind": "search", "operation_id": "op-s1",
         "query": "omnilab", "provider": "tinyfish"},
        {"op": "complete", "kind": "search", "operation_id": "op-s1",
         "query": "omnilab", "provider": "tinyfish", "count": 1,
         "duration_ms": 4, "status": "success"},
        {"op": "start", "kind": "fetch", "operation_id": "op-f1",
         "url": "https://example.com/a", "provider": "standard"},
        {"op": "complete", "kind": "fetch", "operation_id": "op-f1",
         "url": "https://example.com/a", "provider": "standard",
         "final_url": "https://example.com/a", "duration_ms": 5,
         "status": "success"},
    ]
    sources: List[Dict[str, Any]] = [{
        "url": "https://example.com/a", "title": "A", "status": "success",
        "text": "evidence text about omnilab", "fetch_provider": "standard",
        "duration_ms": 5,
    }]
    if failed_source:
        ops.extend([
            {"op": "start", "kind": "fetch", "operation_id": "op-f2",
             "url": "https://example.com/b", "provider": "standard"},
            {"op": "failed", "kind": "fetch", "operation_id": "op-f2",
             "url": "https://example.com/b", "provider": "standard",
             "duration_ms": 3, "status": "failed",
             "error": {"code": "FETCH_TIMEOUT", "message": "timeout"}},
        ])
        sources.append({
            "url": "https://example.com/b", "status": "failed",
            "error": {"code": "FETCH_TIMEOUT"},
        })
    output = {
        "query": "omnilab",
        "outcome": outcome,
        "search": {
            "provider": "tinyfish",
            "results": [{"url": "https://example.com/a", "title": "A"}],
        },
        "sources": sources,
        "summary": {
            "searched": 1, "selected": len(sources), "fetched": 1,
            "failed": 1 if failed_source else 0,
        },
        "internal_operations": [
            {"type": "SEARCH_OPERATION", "name": "Search: omnilab",
             "provider": "tinyfish", "status": "success", "duration_ms": 4,
             "metadata": {"query": "omnilab"}},
            {"type": "FETCH_OPERATION", "name": "Fetch: https://example.com/a",
             "provider": "standard", "status": "success", "duration_ms": 5,
             "metadata": {"url": "https://example.com/a"}},
        ],
        "internal_budget": {
            "search_calls": search_calls,
            "fetch_calls": fetch_calls,
            "external_requests": external_requests,
        },
        "provider": "tinyfish",
        "search_provider": "tinyfish",
        "fetch_provider": "standard",
        "duration_ms": 12,
    }
    return output, ops


def _research_executor(output: Dict[str, Any], ops: List[Dict[str, Any]], record: Dict[str, Any]):
    async def fake_execute(tool, arguments, **kwargs):
        ctx = kwargs.get("context") or {}
        record["called"] = True
        record["tool_name"] = tool.get("name")
        record["arguments"] = arguments
        record["research_budget"] = ctx.get("research_budget")
        sink = ctx.get("operation_sink")
        if sink is not None:
            for op in ops:
                await sink(op)
        return {
            "id": "tc-" + str(kwargs.get("provider_tool_call_id") or "x"),
            "provider_tool_call_id": kwargs.get("provider_tool_call_id"),
            "execution_status": "success",
            "output": output,
            "error": None,
            "duration_ms": 12,
        }
    return fake_execute


def _tool_call_script(call_id: str, name: str, arguments: Any,
                      reasoning: Optional[str] = None) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    if reasoning:
        events.append({"type": "MODEL_REASONING_DELTA", "delta": "",
                       "reasoning_delta": reasoning})
    events.append({"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": call_id,
                   "name": name, "arguments_delta": json.dumps(arguments)})
    events.append({"type": "RUN_COMPLETE", "done": True,
                   "finish_reason": "tool_calls", "tool_calls": []})
    return events


def _final_script(text: str, reasoning: Optional[str] = None) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    if reasoning:
        events.append({"type": "MODEL_REASONING_DELTA", "delta": "",
                       "reasoning_delta": reasoning})
    events.append({"type": "MODEL_OUTPUT_DELTA", "delta": text, "ttft_ms": 1})
    events.append({"type": "RUN_COMPLETE", "done": True,
                   "finish_reason": "stop", "tool_calls": []})
    return events


async def _run_with_sink(owner: str, model_id: str, project_id: str, *,
                         enabled_tool_ids: Optional[List[str]] = None):
    events: List[Dict[str, Any]] = []

    async def sink(evt: Dict[str, Any]) -> None:
        events.append(evt)

    run = await execution_module.execute_model(
        owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
        project_id=project_id, enabled_tool_ids=enabled_tool_ids,
        event_sink=sink, stream_model=True,
    )
    return run, events


def _drive(owner, model_id, project_id, adapter, execute_side_effect):
    """Patch tools on the shared portal loop and run one research execution."""
    async def _run():
        with patch.object(
            tools_service, "resolve_enabled_tools",
            new=AsyncMock(return_value=[_research_tool_doc()]),
        ), patch.object(
            tools_service, "execute_tool",
            new=AsyncMock(side_effect=execute_side_effect),
        ):
            return await _run_with_sink(
                owner, model_id, project_id, enabled_tool_ids=[RESEARCH_TOOL_ID],
            )
    return _run


def _types(events: List[Dict[str, Any]]) -> List[str]:
    return [evt.get("type") for evt in events]


def _internal_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in events if e.get("type") in INTERNAL_EVENT_TYPES]


# ====================================== 1: kind + internal live events
def test_research_kind_and_internal_events(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    output, ops = _research_fixture()
    record: Dict[str, Any] = {}
    fake_execute = _research_executor(output, ops, record)

    adapter = _StreamAdapter([
        _tool_call_script("r1", "web_research", {"query": "omnilab"}),
        _final_script("answer from research"),
    ])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _drive(
        owner, model_id, project_id, adapter, fake_execute))

    assert run["status"] == "success", run.get("error")

    # ``_tool_kind("web_research")`` resolved to research.
    tool_started = next(e for e in events if e["type"] == "TOOL_CALL_STARTED")
    assert tool_started["tool_name"] == "web_research"
    assert tool_started["kind"] == "research"
    assert run["tool_budget"]["max_research_calls"] >= 0

    # Exactly ONE model ToolCall executed the composite.
    assert record["called"] is True
    assert record["tool_name"] == "web_research"
    assert record["arguments"] == {"query": "omnilab"}

    # Live internal operations are children of THIS call, not model ToolCalls.
    types = _types(events)
    for expected in ("SEARCH_STARTED", "SEARCH_COMPLETED",
                     "FETCH_STARTED", "FETCH_COMPLETED"):
        assert expected in types, (expected, types)
    internals = _internal_events(events)
    assert len(internals) == 4
    for evt in internals:
        assert evt["internal"] is True
        assert evt["operation_id"] in ("op-s1", "op-f1")
        assert evt["provider_tool_call_id"] == "r1"
        assert evt["operation_kind"] in ("search", "fetch")
    started_search = next(e for e in internals if e["type"] == "SEARCH_STARTED")
    assert started_search["operation_kind"] == "search"
    assert started_search["query"] == "omnilab"
    assert started_search["provider"] == "tinyfish"
    completed_fetch = next(e for e in internals if e["type"] == "FETCH_COMPLETED")
    assert completed_fetch["url"] == "https://example.com/a"
    assert completed_fetch["final_url"] == "https://example.com/a"
    assert completed_fetch["provider"] == "standard"

    # No per-operation model calls: exactly the tool round + the answer round.
    assert adapter.stream_calls == 2


# ====================================== 2: internal budget folded in
def test_internal_budget_folded_into_run(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    output, ops = _research_fixture(
        search_calls=2, fetch_calls=1, external_requests=3)
    record: Dict[str, Any] = {}
    fake_execute = _research_executor(output, ops, record)

    adapter = _StreamAdapter([
        _tool_call_script("r1", "web_research", {"query": "omnilab"}),
        _final_script("done"),
    ])
    _install_adapter(monkeypatch, adapter)

    run, _ = call(app_client, _drive(
        owner, model_id, project_id, adapter, fake_execute))

    assert run["status"] == "success", run.get("error")
    assert run["research_call_count"] == 1
    assert run["search_call_count"] == 2
    assert run["fetch_call_count"] == 1
    assert run["external_request_count"] == 3
    # Remaining composite budget is threaded to the executor.
    assert record["research_budget"]["search_remaining"] == max(
        0, config.MAX_SEARCH_CALLS_PER_RUN - 0)
    assert record["research_budget"]["provider_batch_cap_ceiling"] == config.MAX_BATCH_FETCH_URLS


# ====================================== 3: child spans on the trace
def test_internal_operations_are_child_spans(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    output, ops = _research_fixture()
    record: Dict[str, Any] = {}
    fake_execute = _research_executor(output, ops, record)

    adapter = _StreamAdapter([
        _tool_call_script("r1", "web_research", {"query": "omnilab"}),
        _final_script("done"),
    ])
    _install_adapter(monkeypatch, adapter)

    run, _ = call(app_client, _drive(
        owner, model_id, project_id, adapter, fake_execute))

    async def _spans(span_type):
        return await repo.trace_spans.list(
            {"owner_id": owner, "run_id": run["id"], "span_type": span_type},
            sort_field="ordering", descending=False, limit=100,
        )

    tool_spans = call(app_client, _spans, "TOOL_CALL")
    assert len(tool_spans) == 1
    tool_span_id = tool_spans[0]["id"]

    search_spans = call(app_client, _spans, "SEARCH_OPERATION")
    fetch_spans = call(app_client, _spans, "FETCH_OPERATION")
    assert len(search_spans) == 1
    assert len(fetch_spans) == 1
    for sub in search_spans + fetch_spans:
        assert sub["parent_span_id"] == tool_span_id
        assert sub["status"] == "completed"
        assert sub["metadata"]["internal"] is True
        assert sub["metadata"]["provider"] in ("tinyfish", "standard")
    assert fetch_spans[0]["metadata"]["duration_ms"] == 5


# ====================================== 4: reasoning segmentation
def test_reasoning_segmented_per_model_call(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    output, ops = _research_fixture()
    record: Dict[str, Any] = {}
    fake_execute = _research_executor(output, ops, record)

    adapter = _StreamAdapter([
        _tool_call_script("r1", "web_research", {"query": "omnilab"},
                          reasoning="weighing search vs fetch"),
        _final_script("final synthesis", reasoning="final synthesis reasoning"),
    ])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _drive(
        owner, model_id, project_id, adapter, fake_execute))

    segments = run["reasoning_segments"]
    assert len(segments) == 2
    assert len({s["model_call_id"] for s in segments}) == 2
    assert [s["phase"] for s in segments] == ["initial", "continuation"]
    assert [s["round_index"] for s in segments] == [0, 1]
    assert segments[0]["content"] == "weighing search vs fetch"
    assert segments[1]["content"] == "final synthesis reasoning"

    model_started = [e for e in events if e["type"] == "MODEL_STARTED"]
    assert [e["model_call_id"] for e in model_started] == [
        s["model_call_id"] for s in segments
    ]


# ====================================== 5: PARTIAL_SUCCESS stays success
def test_partial_success_does_not_fail_run(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    output, ops = _research_fixture(outcome="PARTIAL_SUCCESS", failed_source=True)
    assert output["outcome"] == "PARTIAL_SUCCESS"
    record: Dict[str, Any] = {}
    fake_execute = _research_executor(output, ops, record)

    adapter = _StreamAdapter([
        _tool_call_script("r1", "web_research", {"query": "omnilab"}),
        _final_script("partial answer"),
    ])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _drive(
        owner, model_id, project_id, adapter, fake_execute))

    assert run["status"] == "success", run.get("error")
    assert run["error"] is None
    assert run["tool_success_count"] == 1
    # The failed fetch op is projected but never fails the run.
    assert "FETCH_FAILED" in _types(events)
    failed = next(e for e in _internal_events(events) if e["type"] == "FETCH_FAILED")
    assert failed["error"]["code"] == "FETCH_TIMEOUT"
    assert failed["provider_tool_call_id"] == "r1"


# ====================================== 6: exhausted budget skips research
def test_exhausted_search_budget_skips_research(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    monkeypatch.setattr(config, "MAX_SEARCH_CALLS_PER_RUN", 0)
    output, ops = _research_fixture()
    record: Dict[str, Any] = {}
    fake_execute = _research_executor(output, ops, record)

    adapter = _StreamAdapter([
        _tool_call_script("r1", "web_research", {"query": "omnilab"}),
        _final_script("answer without tools"),
    ])
    _install_adapter(monkeypatch, adapter)

    mock_execute = AsyncMock(side_effect=fake_execute)
    events: List[Dict[str, Any]] = []

    async def _run():
        async def sink(evt):
            events.append(evt)
        with patch.object(
            tools_service, "resolve_enabled_tools",
            new=AsyncMock(return_value=[_research_tool_doc()]),
        ), patch.object(tools_service, "execute_tool", new=mock_execute):
            return await execution_module.execute_model(
                owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
                project_id=project_id, enabled_tool_ids=[RESEARCH_TOOL_ID],
                event_sink=sink, stream_model=True,
            )

    run = call(app_client, _run)

    # The composite call was never executed: no internal operations at all.
    assert mock_execute.await_count == 0
    assert record.get("called") is None
    assert not (set(_types(events)) & set(INTERNAL_EVENT_TYPES))
    assert "TOOL_CALL_STARTED" not in _types(events)

    budget_records = [r for r in run["tool_rounds"] if r.get("budget")]
    assert budget_records, run["tool_rounds"]
    assert budget_records[-1]["budget"] == "search_budget"
    assert budget_records[-1]["execution_status"] == "skipped"
    assert run["finalization_reason"] == "search_budget"
    assert run["status"] == "success", run.get("error")


# ====================================== 7: max research calls bound
def test_max_research_calls_bound(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    # No research calls are allowed at all.
    monkeypatch.setattr(config, "MAX_RESEARCH_CALLS_PER_RUN", 0)
    output, ops = _research_fixture()
    record: Dict[str, Any] = {}
    fake_execute = _research_executor(output, ops, record)

    adapter = _StreamAdapter([
        _tool_call_script("r1", "web_research", {"query": "omnilab"}),
        _final_script("answer"),
    ])
    _install_adapter(monkeypatch, adapter)

    mock_execute = AsyncMock(side_effect=fake_execute)
    events: List[Dict[str, Any]] = []

    async def _run():
        async def sink(evt):
            events.append(evt)
        with patch.object(
            tools_service, "resolve_enabled_tools",
            new=AsyncMock(return_value=[_research_tool_doc()]),
        ), patch.object(tools_service, "execute_tool", new=mock_execute):
            return await execution_module.execute_model(
                owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
                project_id=project_id, enabled_tool_ids=[RESEARCH_TOOL_ID],
                event_sink=sink, stream_model=True,
            )

    run = call(app_client, _run)
    assert mock_execute.await_count == 0
    budget_records = [r for r in run["tool_rounds"] if r.get("budget")]
    assert budget_records[-1]["budget"] == "research_budget"
    assert not (set(_types(events)) & set(INTERNAL_EVENT_TYPES))
