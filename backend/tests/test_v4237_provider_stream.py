"""OMNILAB V4.2.3.7 agentic-streaming provider tests (offline, pure-unit).

Covers the OpenAI-compatible SSE path without MongoDB, network, or the app:
  * output text delta extraction (string content and content-part lists)
  * reasoning delta extraction from the common vendor keys
  * fragmented tool-call arguments -> ordered MODEL_TOOL_CALL_DELTA events
  * assembly of a full tool call into the terminal RUN_COMPLETE event
  * streaming capability flags/helpers
"""
from __future__ import annotations

import asyncio
import json

import pytest

import app.services  # noqa: F401  (resolves app.providers circular-import ordering)
from app import providers
from app.providers import (
    OpenAICompatibleAdapter,
    _accumulate_stream_tool_calls,
    _finalized_stream_tool_calls,
    _parse_stream_chunk,
    _stream_terminal_event,
    supports_output_stream,
    supports_reasoning_stream,
    supports_stream,
    supports_tool_streaming,
)


# --------------------------------------------------------------- capabilities
def test_capabilities_shape_and_values():
    openai_adapter = OpenAICompatibleAdapter(
        {"provider_type": "openai_compatible", "base_url": "https://api.example.com/v1"}
    )
    assert openai_adapter.capabilities == {
        "model_discovery": True,
        "streaming": True,
        "output_stream": True,
        "reasoning_stream": True,
        "tool_streaming": True,
    }


def test_stream_capability_helpers_are_conservative():
    openai = {"provider_type": "openai_compatible"}

    assert supports_output_stream(openai) is True
    assert supports_reasoning_stream(openai) is True
    assert supports_tool_streaming(openai) is True

    assert supports_output_stream(openai, {"capabilities": {"output_stream": False}}) is False
    assert supports_output_stream(openai, {"capabilities": {"output_stream": True}}) is True
    assert supports_reasoning_stream(openai, {"capabilities": {"reasoning_stream": False}}) is False
    assert supports_reasoning_stream(openai, {"capabilities": {"reasoning_stream": True}}) is True
    assert supports_tool_streaming(openai, {"capabilities": {"tool_streaming": False}}) is False
    assert supports_tool_streaming(openai, {"capabilities": {"tool_streaming": True}}) is True

    # Existing supports_stream() semantics must remain intact.
    assert supports_stream(openai) is True
    assert supports_stream(openai, {"capabilities": {"stream": False}}) is False


# --------------------------------------------------------------- chunk parsing
def test_parse_stream_chunk_string_content_delta():
    events = _parse_stream_chunk({"choices": [{"delta": {"content": "Hello"}}]})
    assert events == [
        {"type": "MODEL_OUTPUT_DELTA", "delta": "Hello", "done": False, "ttft_ms": None}
    ]


def test_parse_stream_chunk_list_content_parts_concatenate_text_only():
    events = _parse_stream_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "content": [
                            {"type": "text", "text": "Hel"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                            {"type": "text", "text": "lo"},
                        ]
                    }
                }
            ]
        }
    )
    assert events == [
        {"type": "MODEL_OUTPUT_DELTA", "delta": "Hello", "done": False, "ttft_ms": None}
    ]


def test_parse_stream_chunk_ignores_empty_and_malformed():
    assert _parse_stream_chunk({}) == []
    assert _parse_stream_chunk(None) == []
    assert _parse_stream_chunk({"choices": []}) == []
    assert _parse_stream_chunk({"choices": [{"delta": {"content": ""}}]}) == []
    assert _parse_stream_chunk({"choices": [{"delta": {"content": [{"type": "image_url"}]}}]}) == []


@pytest.mark.parametrize(
    "key", ["reasoning_content", "reasoning", "reasoning_text", "thought", "thinking"]
)
def test_parse_stream_chunk_reasoning_delta(key):
    events = _parse_stream_chunk({"choices": [{"delta": {key: "think"}}]})
    assert events == [
        {"type": "MODEL_REASONING_DELTA", "delta": "", "reasoning_delta": "think", "done": False}
    ]


def test_parse_stream_chunk_tool_call_fragments_are_ordered():
    events = _parse_stream_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_a",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"q":'},
                            },
                            {
                                "index": 1,
                                "id": "call_b",
                                "type": "function",
                                "function": {"name": "save", "arguments": "{}"},
                            },
                        ]
                    }
                }
            ]
        }
    )
    assert [event["type"] for event in events] == [
        "MODEL_TOOL_CALL_DELTA",
        "MODEL_TOOL_CALL_DELTA",
    ]
    assert events[0] == {
        "type": "MODEL_TOOL_CALL_DELTA",
        "index": 0,
        "id": "call_a",
        "name": "lookup",
        "arguments_delta": '{"q":',
        "done": False,
    }
    assert events[1] == {
        "type": "MODEL_TOOL_CALL_DELTA",
        "index": 1,
        "id": "call_b",
        "name": "save",
        "arguments_delta": "{}",
        "done": False,
    }


def test_parse_stream_chunk_legacy_function_call():
    events = _parse_stream_chunk(
        {"choices": [{"delta": {"function_call": {"name": "legacy", "arguments": '{"x":1}'}}}]}
    )
    assert events == [
        {
            "type": "MODEL_TOOL_CALL_DELTA",
            "index": 0,
            "id": None,
            "name": "legacy",
            "arguments_delta": '{"x":1}',
            "done": False,
        }
    ]


# ------------------------------------------------------- assembly + terminal
CHUNKS = [
    {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"ci'},
                        }
                    ]
                }
            }
        ]
    },
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ty": "Pa'}}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ris"}'}}]}}]},
    {
        "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    },
]


def test_fragmented_tool_call_arguments_assemble_in_order():
    accumulator = {}
    for chunk in CHUNKS:
        _accumulate_stream_tool_calls(accumulator, _parse_stream_chunk(chunk))
    calls = _finalized_stream_tool_calls(accumulator)
    assert calls == [
        {
            "id": "call_1",
            "type": "function",
            "name": "get_weather",
            "arguments": '{"city": "Paris"}',
            "arguments_raw": '{"city": "Paris"}',
        }
    ]
    assert json.loads(calls[0]["arguments"]) == {"city": "Paris"}


def test_terminal_event_carries_assembled_tool_calls():
    accumulator = {}
    for chunk in CHUNKS:
        _accumulate_stream_tool_calls(accumulator, _parse_stream_chunk(chunk))
    terminal = _stream_terminal_event(
        {}, "tool_calls", tool_calls=_finalized_stream_tool_calls(accumulator)
    )
    assert terminal["type"] == "RUN_COMPLETE"
    assert terminal["done"] is True
    assert terminal["delta"] == ""
    assert terminal["finish_reason"] == "tool_calls"
    assert terminal["tool_calls"][0]["id"] == "call_1"
    assert terminal["tool_calls"][0]["name"] == "get_weather"
    assert terminal["tool_calls"][0]["arguments"] == '{"city": "Paris"}'
    assert terminal["tool_calls"][0]["arguments_raw"] == '{"city": "Paris"}'


def test_terminal_event_defaults_and_optional_content():
    terminal = _stream_terminal_event(None, None, output_text="final answer")
    assert terminal["tool_calls"] == []
    assert terminal["content"] == "final answer"
    assert terminal["delta"] == ""
    assert terminal["done"] is True
    assert _stream_terminal_event(None, None)["tool_calls"] == []


# ------------------------------------------------------ _stream_once (faked IO)
def _sse(payload) -> str:
    return "data: " + json.dumps(payload)


class _FakeStreamResponse:
    def __init__(self, lines, status_code=200):
        self.status_code = status_code
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


def _collect_events(lines):
    payload = {"model": "m", "messages": [], "stream": True}
    events = []
    original = providers._new_async_client

    def factory(_timeout):
        class _Cli:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def stream(self, method, url, headers=None, json=None):
                return _FakeStreamResponse(lines)

        return _Cli()

    providers._new_async_client = factory
    try:
        async def run():
            adapter = OpenAICompatibleAdapter(
                {"provider_type": "openai_compatible", "base_url": "https://x.example/v1"}
            )
            async for event in adapter._stream_once("https://x.example/v1", payload, 0.0, True):
                events.append(event)

        asyncio.run(run())
    finally:
        providers._new_async_client = original
    return events


def test_stream_once_assembles_tool_calls_without_name_error():
    lines = [_sse(chunk) for chunk in CHUNKS] + ["data: [DONE]"]
    events = _collect_events(lines)

    tool_events = [event for event in events if event["type"] == "MODEL_TOOL_CALL_DELTA"]
    assert [event["index"] for event in tool_events] == [0, 0, 0]
    assert tool_events[0]["id"] == "call_1"
    assert tool_events[0]["name"] == "get_weather"
    assert [event["arguments_delta"] for event in tool_events] == ['{"ci', 'ty": "Pa', 'ris"}']

    terminal = events[-1]
    assert terminal["type"] == "RUN_COMPLETE"
    assert terminal["done"] is True
    assert terminal["finish_reason"] == "tool_calls"
    assert terminal["usage"] == {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
    assert terminal["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "name": "get_weather",
            "arguments": '{"city": "Paris"}',
            "arguments_raw": '{"city": "Paris"}',
        }
    ]


def test_stream_once_emits_terminal_event_without_choices():
    events = _collect_events(['data: {"usage": {"total_tokens": 3}}', "data: [DONE]"])
    assert events[-1]["type"] == "RUN_COMPLETE"
    assert events[-1]["delta"] == ""
    assert events[-1]["tool_calls"] == []


def test_stream_once_output_and_reasoning_deltas_include_ttft():
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "hmm"}}]}),
        _sse({"choices": [{"delta": {"content": "Hi"}}]}),
        _sse({"choices": [{"delta": {"content": " there"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = _collect_events(lines)
    assert [event["type"] for event in events] == [
        "MODEL_REASONING_DELTA",
        "MODEL_OUTPUT_DELTA",
        "MODEL_OUTPUT_DELTA",
        "RUN_COMPLETE",
    ]
    assert events[1]["ttft_ms"] is not None
    terminal = events[-1]
    assert terminal["content"] == "Hi there"
    assert terminal["reasoning_content"] == "hmm"
    assert terminal["finish_reason"] == "stop"
