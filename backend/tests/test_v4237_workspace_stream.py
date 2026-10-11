"""V4.2.3.7 workspace agentic-streaming integration tests (offline).

Covers the new SSE route plumbing around the canonical execution loop:

* the process-local cancellation registry (:mod:`app.services.run_cancellation`),
* the ``POST /api/runs/{run_id}/cancel`` endpoint contract,
* the ``_assistant_message`` partial-success/tooling telemetry fields,
* the agentic ``/conversations/{id}/messages/stream`` route for a tools-enabled
  run (fragmented tool call executed exactly once, canonical event ordering,
  single persisted assistant message), and
* the explicit ``streaming: false`` single-shot SSE path.

TestClient streaming note
-------------------------
``app_client.post(...)`` is used and the whole SSE body is read from
``response.text``. This exercises the route's queue-drain loop and SSE framing
against a real httpx ASGI transport. Disconnect detection
(``request.is_disconnected``) itself is not reliably observable through
TestClient, so the disconnect branch is exercised defensively (it is a no-op
while the connection stays open) rather than asserted here.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from app.models import new_id, now_iso
from app import repository as repo
from app.routers import workspace as workspace_module
from app.services import execution as execution_module
from app.services import run_cancellation
from app.services import tools as tools_service
from tests.conftest import call

from _support import create_model, create_project, create_provider, result_success

CALCULATOR_ID = "tool-calculator-0001"


# --------------------------------------------------------------------- helpers
def _parse_sse(text: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _types(events: List[Dict[str, Any]]) -> List[str]:
    return [evt.get("type") for evt in events]


class _ScriptedStreamAdapter:
    """Fake adapter: one event script per model call (offline)."""

    supports_tools = True
    supports_vision = False

    def __init__(self, scripts: Optional[List[List[Dict[str, Any]]]] = None,
                 execute_text: str = "fallback answer"):
        self._scripts = list(scripts or [])
        self._index = 0
        self._execute_text = execute_text
        self.stream_calls = 0

    async def execute(self, model: Dict[str, Any], req: Any):
        return result_success(self._execute_text)

    async def stream(self, model: Dict[str, Any], req: Any):
        self.stream_calls += 1
        script = self._scripts[self._index] if self._index < len(self._scripts) else []
        self._index += 1
        for evt in script:
            yield evt


def _tool_docs(*names: str) -> List[Dict[str, Any]]:
    return [{
        "id": CALCULATOR_ID,
        "name": name,
        "display_name": name,
        "description": name,
        "input_schema": {"type": "object", "properties": {}},
        "permissions": [],
        "tool_type": "function",
        "enabled": True,
    } for name in names]


def _insert_run(app_client, owner: str, project_id: str, *, status: str = "running") -> str:
    run_id = new_id()

    async def _do():
        await repo.runs.insert({
            "id": run_id, "owner_id": owner, "project_id": project_id,
            "status": status, "created_at": now_iso(),
        })

    app_client.portal.call(_do)
    return run_id


def _conversation(app_client, project_id: str) -> dict:
    r = app_client.post("/api/conversations", json={"project_id": project_id})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------- 1: cancellation registry
def test_registry_lifecycle_and_tolerance():
    run_id = new_id()
    assert run_cancellation.is_registered(run_id) is False
    assert run_cancellation.cancel(run_id) is False

    event = asyncio.Event()
    run_cancellation.register(run_id, event)
    assert run_cancellation.is_registered(run_id) is True
    assert run_cancellation.cancel(run_id) is True
    assert event.is_set() is True

    run_cancellation.unregister(run_id)
    assert run_cancellation.is_registered(run_id) is False
    # Duplicate/missing/empty ids must be tolerated, never raise.
    run_cancellation.unregister(run_id)
    run_cancellation.unregister("")
    run_cancellation.register("", event)
    run_cancellation.register(run_id, None)
    assert run_cancellation.is_registered(run_id) is False
    assert run_cancellation.cancel("") is False
    assert run_cancellation.cancel(None) is False


def test_registry_duplicate_register_replaces_event():
    run_id = new_id()
    first, second = asyncio.Event(), asyncio.Event()
    run_cancellation.register(run_id, first)
    run_cancellation.register(run_id, second)
    try:
        assert run_cancellation.cancel(run_id) is True
        assert second.is_set() is True
        assert first.is_set() is False
    finally:
        run_cancellation.unregister(run_id)


# ------------------------------------------------------ 2: cancel endpoint
def test_cancel_endpoint_no_live_stream_returns_not_running(app_client, as_user):
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    run_id = _insert_run(app_client, owner, project_id, status="success")

    r = app_client.post(f"/api/runs/{run_id}/cancel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] == run_id
    assert body["cancelled"] is False
    assert body["reason"] == "not_running"


def test_cancel_endpoint_sets_registered_event(app_client, as_user):
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    run_id = _insert_run(app_client, owner, project_id, status="running")
    event = asyncio.Event()
    run_cancellation.register(run_id, event)
    try:
        r = app_client.post(f"/api/runs/{run_id}/cancel")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cancelled"] is True
        assert body["run_id"] == run_id
        assert event.is_set() is True
    finally:
        run_cancellation.unregister(run_id)


def test_cancel_endpoint_unknown_run_404(app_client, as_user):
    r = app_client.post(f"/api/runs/{new_id()}/cancel")
    assert r.status_code == 404


def test_cancel_endpoint_foreign_run_404(app_client, as_user):
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    foreign_owner = "pytest-foreign-" + new_id()[:8]
    run_id = new_id()

    async def _insert():
        await repo.runs.insert({
            "id": run_id, "owner_id": foreign_owner, "project_id": project_id,
            "status": "running", "created_at": now_iso(),
        })

    app_client.portal.call(_insert)
    try:
        r = app_client.post(f"/api/runs/{run_id}/cancel")
        assert r.status_code == 404
    finally:
        call(app_client, repo.runs.delete, run_id, foreign_owner)


# ------------------------------------------- 3: assistant message telemetry
def test_assistant_message_carries_tooling_telemetry():
    run = {
        "id": "run-1", "trace_id": "trace-1", "status": "success",
        "normalized_response": "final answer",
        "tool_outcome": "PARTIAL_TOOL_FAILURE",
        "tool_success_count": 2,
        "tool_failure_count": 1,
        "fetch_timeout_count": 1,
        "search_timeout_count": 0,
        "forced_finalization": True,
        "finalization_reason": "consecutive_no_progress",
    }
    msg = workspace_module._assistant_message("conv", "owner", "proj", "model", run)

    assert msg["content"] == "final answer"
    assert msg["tool_outcome"] == "PARTIAL_TOOL_FAILURE"
    assert msg["tool_success_count"] == 2
    assert msg["tool_failure_count"] == 1
    assert msg["fetch_timeout_count"] == 1
    assert msg["search_timeout_count"] == 0
    assert msg["forced_finalization"] is True
    assert msg["finalization_reason"] == "consecutive_no_progress"

    for key in (
        "tool_outcome", "tool_success_count", "tool_failure_count",
        "fetch_timeout_count", "search_timeout_count", "forced_finalization",
        "finalization_reason",
    ):
        assert msg["telemetry"][key] == msg[key]


def test_assistant_message_output_limit_and_finish_reason():
    # Test length finish_reason
    run_length = {
        "id": "run-len", "status": "success",
        "finish_reason": "length",
    }
    msg_len = workspace_module._assistant_message("conv", "owner", "proj", "model", run_length)
    assert msg_len["finish_reason"] == "length"
    assert msg_len["output_limit"] is True
    assert msg_len["output_limit_reason"] == "length"
    assert msg_len["telemetry"]["finish_reason"] == "length"

    # Test provider_finish_reason fallback and truncation
    run_trunc = {
        "id": "run-trunc", "status": "success",
        "provider_finish_reason": "max_tokens",
        "finalization_truncated": True,
    }
    msg_trunc = workspace_module._assistant_message("conv", "owner", "proj", "model", run_trunc)
    assert msg_trunc["finish_reason"] == "max_tokens"
    assert msg_trunc["output_limit"] is True
    assert msg_trunc["output_limit_reason"] == "max_tokens"

    # Test normal stop finish_reason
    run_normal = {
        "id": "run-normal", "status": "success",
        "finish_reason": "stop",
    }
    msg_norm = workspace_module._assistant_message("conv", "owner", "proj", "model", run_normal)
    assert msg_norm["finish_reason"] == "stop"
    assert msg_norm["output_limit"] is False
    assert msg_norm["output_limit_reason"] is None
    assert msg_norm["telemetry"]["finish_reason"] == "stop"


# ------------------------------------------- 4: agentic streaming integration
def test_stream_route_tools_run_executes_once(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    provider_id = call(app_client, create_provider, owner)
    model_id = call(
        app_client, create_model, owner,
        provider_id=provider_id, capabilities={"tools": True},
    )
    conversation = _conversation(app_client, project_id)

    executed: List[Dict[str, Any]] = []

    async def fake_execute(tool, arguments, **kwargs):
        executed.append({
            "name": tool.get("name"),
            "arguments": arguments,
            "provider_tool_call_id": kwargs.get("provider_tool_call_id"),
        })
        return {
            "id": "tc-1",
            "provider_tool_call_id": kwargs.get("provider_tool_call_id"),
            "execution_status": "success",
            "output": {"result": 4},
            "error": None,
            "duration_ms": 1,
        }

    adapter = _ScriptedStreamAdapter([
        [
            {"type": "MODEL_OUTPUT_DELTA", "delta": "let me compute "},
            {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": "call_1",
             "name": "calculator", "arguments_delta": '{"expr'},
            {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": None,
             "name": None, "arguments_delta": 'ession": "2+2"}'},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls",
             "tool_calls": []},
        ],
        [
            {"type": "MODEL_OUTPUT_DELTA", "delta": "The answer is 4", "ttft_ms": 1},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop",
             "tool_calls": []},
        ],
    ])
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    with patch.object(tools_service, "resolve_enabled_tools",
                      new=AsyncMock(return_value=_tool_docs("calculator"))), \
         patch.object(tools_service, "execute_tool",
                      new=AsyncMock(side_effect=fake_execute)):
        r = app_client.post(
            f"/api/conversations/{conversation['id']}/messages/stream",
            json={
                "model_id": model_id,
                "content": "what is 2+2?",
                "enabled_tool_ids": [CALCULATOR_ID],
            },
        )

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers.get("x-accel-buffering") == "no"

    events = _parse_sse(r.text)
    types = _types(events)
    assert types[0] == "RUN_STARTED"
    assert types.index("RUN_STARTED") < types.index("MODEL_STARTED")
    assert types.index("MODEL_STARTED") < types.index("MODEL_OUTPUT_DELTA")
    assert "TOOL_CALL_STARTED" in types
    assert "TOOL_CALL_ARGUMENTS" in types
    assert "TOOL_CALL_COMPLETED" in types
    assert types[-1] == "RUN_COMPLETED"

    terminal = events[-1]
    assert terminal["done"] is True
    assert terminal["assistant_message"]["content"] == "The answer is 4"
    assert terminal["tool_outcome"] == "SUCCESS"
    assert terminal["run"]["status"] == "success"

    assert len(executed) == 1
    assert executed[0]["name"] == "calculator"
    assert executed[0]["arguments"] == {"expression": "2+2"}
    assert executed[0]["provider_tool_call_id"] == "call_1"

    async def _messages():
        return await repo.messages.list(
            {"owner_id": owner, "conversation_id": conversation["id"]},
            sort_field="created_at", descending=False, limit=1000,
        )

    messages = call(app_client, _messages)
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == "The answer is 4"
    assert assistant[0]["tool_outcome"] == "SUCCESS"
    assert assistant[0]["tool_call_count"] >= 1


def test_stream_route_explicit_non_streaming_single_shot(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    provider_id = call(app_client, create_provider, owner)
    model_id = call(app_client, create_model, owner, provider_id=provider_id)
    conversation = _conversation(app_client, project_id)

    adapter = _ScriptedStreamAdapter([], execute_text="single shot answer")
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    r = app_client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"model_id": model_id, "content": "hello", "streaming": False},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    types = _types(events)
    assert types == ["MODEL_STARTED", "MODEL_OUTPUT_DELTA", "RUN_COMPLETED"]
    assert events[0]["streaming"] is False
    assert events[1]["delta"] == "single shot answer"
    assert events[2]["done"] is True
    assert events[2]["assistant_message"]["content"] == "single shot answer"
    assert adapter.stream_calls == 0

    async def _messages():
        return await repo.messages.list(
            {"owner_id": owner, "conversation_id": conversation["id"]},
            sort_field="created_at", descending=False, limit=1000,
        )

    messages = call(app_client, _messages)
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == "single shot answer"


def test_stream_route_non_streaming_provider_falls_back(app_client, as_user, monkeypatch):
    """A provider without native streaming still streams via stream_execution."""
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    provider_id = call(
        app_client, create_provider, owner, provider_type="openai_compatible",
    )
    model_id = call(app_client, create_model, owner, provider_id=provider_id, capabilities={"stream": False})
    conversation = _conversation(app_client, project_id)

    adapter = _ScriptedStreamAdapter([], execute_text="fallback answer")
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    r = app_client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"model_id": model_id, "content": "hi"},
    )
    assert r.status_code == 200, r.text

    events = _parse_sse(r.text)
    started = next(e for e in events if e["type"] == "MODEL_STARTED")
    assert started["streaming"] is False
    deltas = "".join(
        e.get("delta") or "" for e in events if e["type"] == "MODEL_OUTPUT_DELTA"
    )
    assert deltas == "fallback answer"
    assert events[-1]["type"] == "RUN_COMPLETED"
    assert events[-1]["assistant_message"]["content"] == "fallback answer"
    assert adapter.stream_calls == 0


# ------------------------------- 5: end-to-end search + partial fetch failure
def _tool_call_script(call_id: str, name: str, arguments: dict) -> List[Dict[str, Any]]:
    return [
        {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": call_id,
         "name": name, "arguments_delta": json.dumps(arguments)},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls",
         "tool_calls": []},
    ]


def test_stream_route_search_then_partial_fetch_timeout(app_client, as_user, monkeypatch):
    """End-to-end acceptance: search, one FETCH_TIMEOUT, one successful fetch.

    Drives the real SSE route through ``stream_execution`` with a fake adapter
    (one tool call per model round) and a fake tool executor. Verifies the live
    event order, the structured timeout failure, that subsequent fetches still
    run, and that a single failed fetch does not fail the run (partial success).
    """
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    provider_id = call(app_client, create_provider, owner)
    model_id = call(
        app_client, create_model, owner,
        provider_id=provider_id, capabilities={"tools": True},
    )
    conversation = _conversation(app_client, project_id)

    timeout_url = "https://example.com/timedout"
    ok_url = "https://example.com/ok"
    executed: List[Dict[str, Any]] = []

    async def fake_execute(tool, arguments, **kwargs):
        name = tool.get("name")
        provider_tool_call_id = kwargs.get("provider_tool_call_id")
        executed.append({
            "name": name, "arguments": arguments,
            "provider_tool_call_id": provider_tool_call_id,
        })
        if name == "web_search":
            return {
                "id": "tc-search", "provider_tool_call_id": provider_tool_call_id,
                "execution_status": "success",
                "output": {
                    "query": "omnilab", "provider": "tinyfish", "count": 1,
                    "results": [{"title": "T", "url": "https://example.com/a",
                                 "snippet": "s"}],
                },
                "error": None, "duration_ms": 1,
            }
        url = (arguments or {}).get("url")
        if url == timeout_url:
            return {
                "id": "tc-timeout", "provider_tool_call_id": provider_tool_call_id,
                "execution_status": "failed", "output": None,
                "error": {
                    "code": "FETCH_TIMEOUT",
                    "message": "Request to URL timed out",
                    "provider": "standard",
                    "details": {"url": url, "timeout_seconds": 5,
                                "provider": "standard"},
                },
                "duration_ms": 5000,
            }
        return {
            "id": "tc-ok", "provider_tool_call_id": provider_tool_call_id,
            "execution_status": "success",
            "output": {"final_url": url, "requested_url": url,
                       "text": "usable evidence", "provider": "standard"},
            "error": None, "duration_ms": 3,
        }

    adapter = _ScriptedStreamAdapter([
        _tool_call_script("s1", "web_search", {"query": "omnilab"}),
        _tool_call_script("t1", "web_fetch", {"url": timeout_url}),
        _tool_call_script("t2", "web_fetch", {"url": ok_url}),
        [
            {"type": "MODEL_OUTPUT_DELTA", "delta": "answer ", "ttft_ms": 1},
            {"type": "MODEL_OUTPUT_DELTA", "delta": "from evidence"},
            {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop",
             "tool_calls": []},
        ],
    ])
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    with patch.object(tools_service, "resolve_enabled_tools",
                      new=AsyncMock(return_value=_tool_docs("web_search", "web_fetch"))), \
         patch.object(tools_service, "execute_tool",
                      new=AsyncMock(side_effect=fake_execute)):
        r = app_client.post(
            f"/api/conversations/{conversation['id']}/messages/stream",
            json={
                "model_id": model_id,
                "content": "research omnilab",
                "enabled_tool_ids": [CALCULATOR_ID],
            },
        )

    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    types = _types(events)

    def first(event_type: str) -> int:
        return types.index(event_type)

    assert types[0] == "RUN_STARTED"
    assert first("RUN_STARTED") < first("MODEL_STARTED")
    assert first("MODEL_STARTED") < first("TOOL_CALL_STARTED")
    assert first("TOOL_CALL_STARTED") < first("TOOL_CALL_ARGUMENTS")
    assert first("TOOL_CALL_ARGUMENTS") < first("TOOL_CALL_COMPLETED")
    assert first("TOOL_CALL_COMPLETED") < first("TOOL_RESULT_STARTED")
    assert first("TOOL_RESULT_STARTED") < first("SEARCH_STARTED")
    assert first("SEARCH_STARTED") < first("SEARCH_COMPLETED")
    assert first("SEARCH_COMPLETED") < first("FETCH_STARTED")
    assert first("FETCH_STARTED") < first("FETCH_FAILED")
    assert first("FETCH_FAILED") < first("TOOL_RESULT_FAILED")
    second_fetch = types.index("FETCH_STARTED", first("FETCH_STARTED") + 1)
    assert first("TOOL_RESULT_FAILED") < second_fetch
    assert second_fetch < first("FETCH_COMPLETED")
    assert first("FETCH_COMPLETED") < first("MODEL_OUTPUT_DELTA")
    assert first("MODEL_OUTPUT_DELTA") < len(types) - 1
    assert types[-1] == "RUN_COMPLETED"

    assert types.count("SEARCH_STARTED") == 1
    assert types.count("SEARCH_COMPLETED") == 1
    assert types.count("FETCH_STARTED") == 2
    assert types.count("FETCH_FAILED") == 1
    assert types.count("FETCH_COMPLETED") == 1

    fetch_failed = next(e for e in events if e["type"] == "FETCH_FAILED")
    assert fetch_failed["error"]["code"] == "FETCH_TIMEOUT"
    assert fetch_failed["error"]["url"] == timeout_url
    assert fetch_failed["error"]["timeout_seconds"] == 5

    failed = next(e for e in events if e["type"] == "TOOL_RESULT_FAILED")
    assert failed["error"]["code"] == "FETCH_TIMEOUT"
    assert failed["error"]["url"] == timeout_url
    assert failed["error"]["timeout_seconds"] == 5

    terminal = events[-1]
    assert terminal["run"]["status"] == "success"
    assert terminal["tool_outcome"] == "PARTIAL_TOOL_FAILURE"
    assert terminal["run"]["fetch_timeout_count"] >= 1
    assert terminal["assistant_message"]["content"] == "answer from evidence"
    assert terminal["assistant_message"]["fetch_timeout_count"] >= 1

    assert [e["name"] for e in executed] == ["web_search", "web_fetch", "web_fetch"]
    assert [e["provider_tool_call_id"] for e in executed] == ["s1", "t1", "t2"]

    async def _persisted():
        return await repo.runs.find_one(
            {"owner_id": owner, "id": terminal["run"]["id"]})

    stored = call(app_client, _persisted)
    assert stored is not None
    assert stored["status"] == "success"
    assert stored["tool_outcome"] == "PARTIAL_TOOL_FAILURE"
    assert stored["fetch_timeout_count"] >= 1


# -------------------------------- 6: Phase 9 long-stream regression test
def test_stream_route_synthetic_long_stream_regression(app_client, as_user, monkeypatch):
    """Phase 9: Deterministic synthetic long-stream test.
    Generates 1,000+ content chunks across headings, markdown lists, paragraphs,
    code blocks, reasoning events, telemetry events, and terminal RUN_COMPLETED.
    Verifies full stream survival, exact answer concatenation, separate reasoning,
    terminal event preservation, and single assistant message persistence.
    """
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    provider_id = call(app_client, create_provider, owner)
    model_id = call(
        app_client, create_model, owner,
        provider_id=provider_id, capabilities={"tools": True},
    )
    conversation = _conversation(app_client, project_id)

    # Build 1000+ chunks
    # Structure:
    # Heading 1
    # Paragraphs
    # Lists
    # Code block
    chunks = []
    chunks.append("# Comprehensive Report on Quantum Computing Systems\n\n")
    chunks.append("## Executive Summary\n\n")
    for i in range(250):
        chunks.append(f"Paragraph {i+1}: Detailed analysis of quantum coherence and state superposition principles.\n\n")
    chunks.append("## Key Findings\n\n")
    for i in range(500):
        chunks.append(f"- Point {i+1}: Coherence metric threshold reached for qubit cluster {i % 16}.\n")
    chunks.append("\n## Source Code Implementation\n\n```python\n")
    for i in range(300):
        chunks.append(f"def quantum_node_{i}(qubit):\n    return qubit.apply_hadamard({i})\n")
    chunks.append("```\n\nConclusion: Synthesis complete across all modules.\n")

    assert len(chunks) >= 1050
    expected_full_text = "".join(chunks)

    events_script = []
    events_script.append({"type": "MODEL_REASONING_DELTA", "delta": "Formulating quantum analysis breakdown...\n"})
    events_script.append({"type": "MODEL_REASONING_DELTA", "delta": "Validating cluster coherence formulas...\n"})
    for c in chunks:
        events_script.append({"type": "MODEL_OUTPUT_DELTA", "delta": c})
    events_script.append({"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop", "tool_calls": []})

    adapter = _ScriptedStreamAdapter([events_script])
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)

    r = app_client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"model_id": model_id, "content": "Run long analysis"},
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    types = _types(events)

    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_COMPLETED"
    assert "MODEL_REASONING_DELTA" in types
    assert types.count("MODEL_REASONING_DELTA") == 2

    # Reasoning remains separate
    reasoning_deltas = [e["delta"] for e in events if e.get("type") == "MODEL_REASONING_DELTA"]
    assert "".join(reasoning_deltas) == "Formulating quantum analysis breakdown...\nValidating cluster coherence formulas...\n"

    # All content chunks survive
    output_deltas = [e["delta"] for e in events if e.get("type") == "MODEL_OUTPUT_DELTA"]
    assert len(output_deltas) == len(chunks)
    assert "".join(output_deltas) == expected_full_text

    # Terminal event does not shorten answer
    terminal = events[-1]
    assert terminal["assistant_message"]["content"] == expected_full_text
    assert terminal["run"]["status"] == "success"
    assert terminal["run"]["finish_reason"] == "stop"

    # Persisted message in repo matches exactly
    async def _messages():
        return await repo.messages.list(
            {"owner_id": owner, "conversation_id": conversation["id"]},
            sort_field="created_at", descending=False, limit=1000,
        )

    messages = call(app_client, _messages)
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == expected_full_text
    assert assistant[0]["finish_reason"] == "stop"
