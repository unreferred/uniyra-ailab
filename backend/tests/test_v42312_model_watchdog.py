"""OMNILAB V4.2.3.12 model-call watchdog tests (offline, deterministic).

Deterministic fake-provider tests for :mod:`app.services.model_watchdog` and its
integration into the canonical execution loop. Every provider call is scripted
locally; hangs use large sleeps and the three watchdog timeouts are monkeypatched
to tiny floats, so the suite is fast and never flaky. No network, no real LLM.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from app import config
from app.services import execution as execution_module
from app.services import tools as tools_service
from tests.conftest import call

from _support import create_model, create_project, create_provider, result_success

SEARCH_TOOL_ID = "tool-search-0001"

TERMINAL_TYPES = {"RUN_COMPLETED", "RUN_FAILED", "RUN_CANCELLED"}

REQUIRED_TIMEOUT_FIELDS = {
    "run_id",
    "trace_id",
    "round_index",
    "model_call_id",
    "provider",
    "model",
    "connection_timeout_seconds",
    "idle_timeout_seconds",
    "max_duration_seconds",
    "elapsed_ms",
    "last_progress_age_ms",
    "timeout_kind",
    "had_reasoning",
    "had_content",
    "had_tool_call_delta",
    "usable_evidence",
    "recovery_action",
}


# --------------------------------------------------------------------- helpers
def _timeouts(monkeypatch, *, connection=0.5, idle=0.25, max_duration=3.0):
    monkeypatch.setattr(config, "MODEL_CONNECTION_TIMEOUT_SECONDS", connection)
    monkeypatch.setattr(config, "MODEL_IDLE_TIMEOUT_SECONDS", idle)
    monkeypatch.setattr(config, "MODEL_CALL_MAX_DURATION_SECONDS", max_duration)


class _BaseAdapter:
    supports_tools = True
    supports_vision = False

    async def execute(self, model: Dict[str, Any], req: Any):
        return result_success("answer")


class _ScriptAdapter(_BaseAdapter):
    """Streaming adapter driven by per-call scripts.

    A script item is a stream event dict, a number (``await asyncio.sleep``) or a
    zero-argument callable (invoked inline).
    """

    def __init__(self, scripts: Optional[List[List[Any]]] = None):
        self._scripts = list(scripts or [])
        self._index = 0
        self.stream_calls = 0
        self.requests: List[Any] = []

    async def stream(self, model: Dict[str, Any], req: Any):
        self.stream_calls += 1
        self.requests.append(req)
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


class _CloseTrackingAdapter(_BaseAdapter):
    """Yields one delta then hangs; records that its ``finally`` ran."""

    def __init__(self):
        self.closed = False

    async def stream(self, model: Dict[str, Any], req: Any):
        try:
            yield {"type": "MODEL_OUTPUT_DELTA", "delta": "x", "ttft_ms": 1}
            await asyncio.sleep(5)
        finally:
            self.closed = True


class _EndlessAdapter(_BaseAdapter):
    """An otherwise-healthy stream that never terminates on its own."""

    async def stream(self, model: Dict[str, Any], req: Any):
        while True:
            yield {"type": "MODEL_OUTPUT_DELTA", "delta": "x", "ttft_ms": 1}
            await asyncio.sleep(0.005)


class _CancelAdapter(_BaseAdapter):
    """Sets the cancel event synchronously, then hangs before any yield."""

    def __init__(self, cancel_event: asyncio.Event):
        self.cancel_event = cancel_event

    async def stream(self, model: Dict[str, Any], req: Any):
        self.cancel_event.set()
        await asyncio.sleep(5)
        yield {"type": "MODEL_OUTPUT_DELTA", "delta": "never"}


async def _setup(owner: str):
    provider_id = await create_provider(owner, provider_type="openai_compatible")
    project_id = await create_project(owner)
    model_id = await create_model(
        owner, provider_id=provider_id, capabilities={"tools": True},
    )
    return project_id, model_id


def _install_adapter(monkeypatch, adapter):
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)


def _search_tool_doc() -> Dict[str, Any]:
    return {
        "id": SEARCH_TOOL_ID,
        "name": "web_search",
        "display_name": "Web Search",
        "description": "Search the web.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "permissions": [],
        "tool_type": "function",
        "implementation": "builtin:web_search",
        "enabled": True,
    }


def _tool_call_script(call_id: str, name: str, arguments: Any) -> List[Dict[str, Any]]:
    return [
        {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": call_id,
         "name": name, "arguments_delta": json.dumps(arguments)},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls",
         "tool_calls": []},
    ]


def _output_script(text: str) -> List[Dict[str, Any]]:
    return [
        {"type": "MODEL_OUTPUT_DELTA", "delta": text, "ttft_ms": 1},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop",
         "tool_calls": []},
    ]


async def _run_with_sink(
    owner: str,
    model_id: str,
    project_id: str,
    *,
    enabled_tool_ids: Optional[List[str]] = None,
    cancel_event: Optional[asyncio.Event] = None,
):
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


def _timeout_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in events if e.get("type") == "MODEL_CALL_TIMEOUT"]


def _terminals(events: List[Dict[str, Any]]) -> List[str]:
    return [t for t in _types(events) if t in TERMINAL_TYPES]


def _install_search_tool(monkeypatch, record: Optional[Dict[str, Any]] = None):
    record = record if record is not None else {}

    async def fake_execute(tool, arguments, **kwargs):
        record["calls"] = record.get("calls", 0) + 1
        record["arguments"] = arguments
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

    mock_execute = AsyncMock(side_effect=fake_execute)

    async def _run(owner, model_id, project_id, *, adapter=None, cancel_event=None,
                   max_rounds=None):
        with patch.object(
            tools_service, "resolve_enabled_tools",
            new=AsyncMock(return_value=[_search_tool_doc()]),
        ), patch.object(tools_service, "execute_tool", new=mock_execute):
            return await _run_with_sink(
                owner, model_id, project_id,
                enabled_tool_ids=[SEARCH_TOOL_ID], cancel_event=cancel_event,
            )
    return _run, mock_execute


# ================================================= 1: provider never starts
def test_connection_timeout_when_provider_never_starts(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=0.15, idle=5.0, max_duration=5.0)
    adapter = _ScriptAdapter([[5.0]])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)

    assert run["status"] == "error"
    assert run["error"]["code"] == "MODEL_CONNECTION_TIMEOUT"
    assert run["error"]["category"] == "TIMEOUT"
    timeout_events = _timeout_events(events)
    assert len(timeout_events) == 1
    assert timeout_events[0]["timeout_kind"] == "connection"
    assert run["model_call_timeout_count"] == 1


# =========================================== 2: one delta then hang -> idle
def test_idle_timeout_after_first_delta(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=5.0, idle=0.15, max_duration=5.0)
    adapter = _ScriptAdapter([[{"type": "MODEL_OUTPUT_DELTA", "delta": "hi"}, 5.0]])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)

    assert run["status"] == "error"
    assert run["error"]["code"] == "MODEL_IDLE_TIMEOUT"
    evt = _timeout_events(events)[0]
    assert evt["timeout_kind"] == "idle"
    assert evt["had_content"] is True


# ================================= 3: continuous reasoning resets the idle clock
def test_continuous_reasoning_resets_idle(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=0.5, idle=0.25, max_duration=5.0)
    script: List[Any] = []
    for i in range(8):
        script.append({"type": "MODEL_REASONING_DELTA", "delta": "",
                       "reasoning_delta": f"reason {i}"})
        script.append(0.01)
    script.extend(_output_script("reasoning-driven final"))
    adapter = _ScriptAdapter([script])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)

    assert run["status"] == "success", run.get("error")
    assert run["normalized_response"] == "reasoning-driven final"
    assert not _timeout_events(events)


# =================================== 4: continuous output deltas reset the clock
def test_continuous_output_deltas_reset_idle(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=0.5, idle=0.25, max_duration=5.0)
    script: List[Any] = []
    for i in range(8):
        script.append({"type": "MODEL_OUTPUT_DELTA", "delta": f"w{i} "})
        script.append(0.01)
    script.append({"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop",
                   "tool_calls": []})
    adapter = _ScriptAdapter([script])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)

    assert run["status"] == "success", run.get("error")
    assert run["normalized_response"] == "w0 w1 w2 w3 w4 w5 w6 w7 "
    assert not _timeout_events(events)


# ========================= 5: fragmented tool-call deltas execute exactly once
def test_fragmented_tool_call_executes_once(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=0.5, idle=0.25, max_duration=5.0)
    script = [
        {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": "call_x",
         "name": "web_search", "arguments_delta": '{"que'},
        0.01,
        {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": None, "name": None,
         "arguments_delta": 'ry": "om'},
        0.01,
        {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": None, "name": None,
         "arguments_delta": 'nilab"}'},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls",
         "tool_calls": []},
    ]
    adapter = _ScriptAdapter([script, _output_script("done")])
    _install_adapter(monkeypatch, adapter)
    _run, mock_execute = _install_search_tool(monkeypatch)

    run, events = call(app_client, _run, owner, model_id, project_id, adapter=adapter)

    assert run["status"] == "success", run.get("error")
    assert mock_execute.await_count == 1
    assert mock_execute.await_args.args[1] == {"query": "omnilab"}
    assert not _timeout_events(events)


# =========================== 6: incomplete tool-call fragments never execute
def test_incomplete_tool_call_fragments_never_execute(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=5.0, idle=0.2, max_duration=5.0)
    script = [
        {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": "call_x",
         "name": "web_search", "arguments_delta": '{"query": "om'},
        0.01,
        {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": None, "name": None,
         "arguments_delta": 'nilab"'},
        5.0,
    ]
    adapter = _ScriptAdapter([script])
    _install_adapter(monkeypatch, adapter)
    _run, mock_execute = _install_search_tool(monkeypatch)

    run, events = call(app_client, _run, owner, model_id, project_id, adapter=adapter)

    assert run["status"] == "error"
    assert run["error"]["code"] == "MODEL_IDLE_TIMEOUT"
    assert mock_execute.await_count == 0
    assert _timeout_events(events)[0]["had_tool_call_delta"] is True


# ================= 7: timeout after usable evidence -> forced finalization
def test_timeout_with_evidence_degrades_to_forced_finalization(
    app_client, as_user, monkeypatch,
):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=0.5, idle=0.2, max_duration=5.0)
    hang_script = [{"type": "MODEL_OUTPUT_DELTA", "delta": "partial"}, 5.0]
    adapter = _ScriptAdapter([
        _tool_call_script("s1", "web_search", {"query": "alpha"}),
        hang_script,
        _output_script("final answer from evidence"),
    ])
    _install_adapter(monkeypatch, adapter)
    _run, mock_execute = _install_search_tool(monkeypatch)

    run, events = call(app_client, _run, owner, model_id, project_id, adapter=adapter)

    assert mock_execute.await_count == 1
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    assert run["finalization_reason"] == "provider_error"
    assert run["normalized_response"] == "final answer from evidence"
    evt = _timeout_events(events)[0]
    assert evt["recovery_action"] == "forced_finalization"
    assert evt["usable_evidence"] is True
    assert _terminals(events) == ["RUN_COMPLETED"]


# ========================= 8: timeout without evidence -> structured Run error
def test_timeout_without_evidence_is_terminal_error(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=0.15, idle=5.0, max_duration=5.0)
    adapter = _ScriptAdapter([[5.0]])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)

    assert run["status"] == "error"
    assert run["error"]["code"].startswith("MODEL_")
    assert run["error"]["category"] == "TIMEOUT"
    assert _timeout_events(events)[0]["recovery_action"] == "terminal_error"
    assert _terminals(events) == ["RUN_FAILED"]


# ======================= 9: watchdog closes the stream (no zombie generator)
def test_watchdog_closes_underlying_stream(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=5.0, idle=0.15, max_duration=5.0)
    adapter = _CloseTrackingAdapter()
    _install_adapter(monkeypatch, adapter)

    run, _ = call(app_client, _run_with_sink, owner, model_id, project_id)

    assert run["status"] == "error"
    assert run["error"]["code"] == "MODEL_IDLE_TIMEOUT"
    assert adapter.closed is True


# ===================== 10: global max duration bounds an active endless stream
def test_global_max_duration_terminates_endless_stream(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=5.0, idle=5.0, max_duration=0.15)
    adapter = _EndlessAdapter()
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)

    assert run["status"] == "error"
    assert run["error"]["code"] == "MODEL_CALL_MAX_DURATION"
    evt = _timeout_events(events)[0]
    assert evt["timeout_kind"] == "max_duration"
    assert evt["elapsed_ms"] < 2000


# ================ 11: Stop Generation + watchdog -> one RUN_CANCELLED only
def test_cancellation_wins_and_emits_single_terminal(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=0.15, idle=0.15, max_duration=5.0)
    cancel_event = asyncio.Event()
    adapter = _CancelAdapter(cancel_event)
    _install_adapter(monkeypatch, adapter)

    run, events = call(
        app_client, _run_with_sink, owner, model_id, project_id,
        cancel_event=cancel_event,
    )

    assert run["status"] == "cancelled"
    assert run["error"]["code"] == "CANCELLED"
    assert _terminals(events) == ["RUN_CANCELLED"]
    assert not _timeout_events(events)


# ===================== 12: exactly one terminal event in timeout scenarios
def test_exactly_one_terminal_event_on_timeout(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=0.15, idle=5.0, max_duration=5.0)
    adapter = _ScriptAdapter([[5.0]])
    _install_adapter(monkeypatch, adapter)

    run, events = call(app_client, _run_with_sink, owner, model_id, project_id)

    terminals = _terminals(events)
    assert len(terminals) == 1
    assert terminals[0] == "RUN_FAILED"
    assert run["status"] == "error"


# ================ MODEL_CALL_TIMEOUT event fields + no secrets
def test_model_call_timeout_event_fields_and_no_secrets(
    app_client, as_user, monkeypatch,
):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    _timeouts(monkeypatch, connection=0.15, idle=5.0, max_duration=5.0)
    adapter = _ScriptAdapter([[5.0]])
    _install_adapter(monkeypatch, adapter)

    _run, events = call(app_client, _run_with_sink, owner, model_id, project_id)

    evt = _timeout_events(events)[0]
    assert REQUIRED_TIMEOUT_FIELDS.issubset(set(evt.keys()))
    assert evt["timeout_kind"] == "connection"
    assert evt["recovery_action"] == "terminal_error"
    blob = json.dumps(evt, default=str).lower()
    for forbidden in ("authorization", "api_key", "apikey", "x-api-key", "bearer", "secret", "password"):
        assert forbidden not in blob
