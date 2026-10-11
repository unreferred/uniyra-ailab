"""OMNILAB V4.2.3.8 composite ``web_research`` TRUE end-to-end tests (offline).

Unlike ``test_v4238_execution_research`` (which stubs ``execute_tool``), these
tests drive the REAL canonical execution loop through the REAL
``builtin:web_research`` executor. Only the network/provider boundary is
mocked:

  * ``TinyFishSearchProvider.search`` is replaced with an ``AsyncMock`` that
    returns a realistic TinyFish search payload.
  * ``TinyFishFetchProvider.fetch_many`` is replaced with an ``AsyncMock`` that
    records each provider batch call (chunked by the provider batch cap) and
    returns one success + one timeout record per chunk.

Everything else is production code: provider/valve resolution, the composite
``run_research`` orchestration, the research budget, the internal operation
sink projection, child trace spans and reasoning segmentation.

The test therefore proves -- end to end -- that Search -> deterministic
selection -> batched Fetch happens inside a SINGLE model ToolCall with no extra
continuations, that the provider execution batch cap chunks the fetch without
clamping logical breadth, that internal operations are live-projected as
children (never as model ToolCalls) and that a partial fetch failure never
fails the run.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from app import config
from app import repository as repo
from app.services import execution as execution_module
from app.services import tools as tools_service
from app.services.builtin_tools import (
    _normalize_search_results,
    _select_research_sources,
)
from app.services.fetch_providers import (
    TINYFISH_FETCH_PROVIDER_ID,
    TinyFishFetchProvider,
)
from app.services.search_providers import (
    TINYFISH_SEARCH_PROVIDER_ID,
    TinyFishSearchProvider,
)
from tests.conftest import call

from _support import create_model, create_project, create_provider, result_success

RESEARCH_TOOL_ID = "tool-research-0001"
SEARCH_TOOL_ID = "tool-search-0001"
FETCH_TOOL_ID = "tool-fetch-0001"

INTERNAL_EVENT_TYPES = {
    "SEARCH_STARTED", "SEARCH_COMPLETED",
    "FETCH_STARTED", "FETCH_COMPLETED", "FETCH_FAILED",
}

MODEL_TOOL_EVENT_TYPES = {
    "TOOL_CALL_STARTED", "TOOL_CALL_ARGUMENTS", "TOOL_CALL_COMPLETED",
}


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


# ------------------------------------------------------ mocked provider boundary
# Duplicate URL (fragment-only difference) + mixed ranks (beta ranked 1 but is
# the 2nd entry) + three distinct hosts. Exercises dedupe + rank order + domain
# diversity deterministically.
SEARCH_RESULTS: List[Dict[str, Any]] = [
    {"title": "Alpha one", "url": "https://alpha.example/a", "snippet": "sa", "rank": 3},
    {"title": "Beta one", "url": "https://beta.example/b", "snippet": "sb", "rank": 1},
    {"title": "Alpha dup", "url": "https://alpha.example/a#frag", "snippet": "sdup", "rank": 2},
    {"title": "Gamma one", "url": "https://gamma.example/c", "snippet": "sc", "rank": 4},
    {"title": "Alpha two", "url": "https://alpha.example/a2", "snippet": "sa2", "rank": 5},
]

# Expected selection for max_fetches == 3: beta/b (rank 1), alpha/a (rank 3),
# gamma/c (rank 4). alpha/a#frag collapses into alpha/a, so alpha/a2 is dropped
# by the max and by domain diversity.
EXPECTED_SELECTION = [
    "https://beta.example/b",
    "https://alpha.example/a",
    "https://gamma.example/c",
]


def _search_payload(query: str = "omnilab research") -> Dict[str, Any]:
    return {
        "query": query,
        "provider": TINYFISH_SEARCH_PROVIDER_ID,
        "search_provider": TINYFISH_SEARCH_PROVIDER_ID,
        "provider_type": "tinyfish",
        "egress_mode": "REMOTE",
        "location": "ID",
        "language": "id",
        "provider_status": 200,
        "duration_ms": 11,
        "results": [dict(item) for item in SEARCH_RESULTS],
        "count": len(SEARCH_RESULTS),
        "metadata": {
            "source": "tinyfish",
            "egress_mode": "REMOTE",
            "provider_endpoint_host": "api.search.tinyfish.ai",
            "provider_status": 200,
            "duration_ms": 11,
        },
    }


def _fetch_records(urls: List[str], *, timeout_last: bool = True) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    last = len(urls) - 1
    for index, url in enumerate(urls):
        if timeout_last and index == last:
            records.append({
                "url": url,
                "status": "failed",
                "error": {
                    "code": "FETCH_TIMEOUT",
                    "message": f"request to {url} timed out",
                    "timeout_seconds": 20,
                },
            })
            continue
        records.append({
            "url": url,
            "status": "success",
            "title": f"Title {url}",
            "text": f"usable evidence from {url}",
            "final_url": url,
            "fetch_provider": TINYFISH_FETCH_PROVIDER_ID,
            "provider": TINYFISH_FETCH_PROVIDER_ID,
            "egress_mode": "REMOTE",
            "provider_status": 200,
            "duration_ms": 5 + index,
            "metadata": {
                "egress_mode": "REMOTE",
                "provider_endpoint_host": "api.fetch.tinyfish.ai",
                "provider_status": 200,
            },
        })
    return records


def _build_mocks(
    *,
    search_calls: List[Dict[str, Any]],
    fetch_calls: List[List[str]],
    timeout_last: bool = True,
):
    async def fake_search(query, *, max_results=None, location=None, language=None):
        search_calls.append({
            "query": query, "max_results": max_results,
            "location": location, "language": language,
        })
        return _search_payload(query)

    async def fake_fetch_many(urls):
        fetch_calls.append(list(urls))
        return _fetch_records(list(urls), timeout_last=timeout_last)

    return (
        AsyncMock(side_effect=fake_search),
        AsyncMock(side_effect=fake_fetch_many),
    )


def _run_research_e2e(
    app_client,
    owner: str,
    model_id: str,
    project_id: str,
    search_mock: AsyncMock,
    fetch_mock: AsyncMock,
    *,
    enabled_tool_ids: Optional[List[str]] = None,
):
    """Drive the REAL loop + REAL run_research with only the network mocked."""

    async def _scenario():
        events: List[Dict[str, Any]] = []

        async def sink(evt: Dict[str, Any]) -> None:
            events.append(evt)

        with patch.object(TinyFishSearchProvider, "search", new=search_mock), \
             patch.object(TinyFishFetchProvider, "fetch_many", new=fetch_mock):
            run = await execution_module.execute_model(
                owner, model_id, "system",
                [{"role": "user", "content": "research omnilab"}], {},
                project_id=project_id,
                enabled_tool_ids=enabled_tool_ids or [RESEARCH_TOOL_ID],
                search_enabled=True,
                search_provider_id=TINYFISH_SEARCH_PROVIDER_ID,
                fetch_enabled=True,
                fetch_provider_id=TINYFISH_FETCH_PROVIDER_ID,
                event_sink=sink,
                stream_model=True,
            )
        return run, events

    return call(app_client, _scenario)


def _types(events: List[Dict[str, Any]]) -> List[str]:
    return [evt.get("type") for evt in events]


# =================================================== PRIMARY end-to-end scenario
def test_web_research_single_toolcall_end_to_end(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)

    adapter = _StreamAdapter([
        _tool_call_script(
            "call_research", "web_research",
            {"query": "omnilab research", "max_results": 5, "max_fetches": 3},
            reasoning="Decide whether one composite research call is enough.",
        ),
        _final_script(
            "Synthesized answer from the evidence bundle.",
            reasoning="Synthesize the fetched evidence into a final answer.",
        ),
    ])
    _install_adapter(monkeypatch, adapter)

    search_calls: List[Dict[str, Any]] = []
    fetch_calls: List[List[str]] = []
    search_mock, fetch_mock = _build_mocks(
        search_calls=search_calls, fetch_calls=fetch_calls,
    )

    run, events = _run_research_e2e(
        app_client, owner, model_id, project_id, search_mock, fetch_mock,
    )
    types = _types(events)

    # --- (1) ONE model ToolCall; Search->Fetch inside it, no extra rounds ----
    assert run["status"] == "success", run.get("error")
    assert adapter.stream_calls == 2, adapter.stream_calls
    assert types.count("TOOL_CALL_STARTED") == 1, types
    tool_started = next(e for e in events if e["type"] == "TOOL_CALL_STARTED")
    assert tool_started["tool_name"] == "web_research"
    assert tool_started["kind"] == "research"
    assert tool_started["provider_tool_call_id"] == "call_research"
    assert len(search_calls) == 1
    assert search_calls[0]["query"] == "omnilab research"
    assert len(fetch_calls) == 1

    # --- (2) persisted ToolCall output satisfies the evidence contract -------
    async def _tool_call_doc():
        return await repo.tool_calls.find_one({
            "owner_id": owner, "run_id": run["id"], "tool_id": RESEARCH_TOOL_ID,
        })

    doc = call(app_client, _tool_call_doc)
    assert doc is not None
    assert doc["execution_status"] == "success"
    assert doc["provider_tool_call_id"] == "call_research"
    output = doc["output"]

    assert output["query"] == "omnilab research"
    assert output["search"]["provider"] == TINYFISH_SEARCH_PROVIDER_ID
    assert [r["url"] for r in output["search"]["results"]] == [
        item["url"] for item in SEARCH_RESULTS
    ]
    # sources[] has at least one success and one FETCH_TIMEOUT.
    statuses = [s["status"] for s in output["sources"]]
    assert "success" in statuses and "timeout" in statuses, statuses
    timeout_source = next(s for s in output["sources"] if s["status"] == "timeout")
    assert timeout_source["error"]["code"] == "FETCH_TIMEOUT"
    assert timeout_source["error"]["timeout_seconds"] == 20
    success_source = next(s for s in output["sources"] if s["status"] == "success")
    assert success_source["text"]
    assert success_source["fetch_provider"] == TINYFISH_FETCH_PROVIDER_ID
    # summary + outcome
    assert output["summary"] == {
        "searched": len(SEARCH_RESULTS), "selected": 3, "fetched": 2, "failed": 1,
    }
    assert output["outcome"] == "PARTIAL_SUCCESS"
    assert output["internal_budget"] == {
        "search_calls": 1, "fetch_calls": 3, "external_requests": 4,
    }

    # --- (3) partial failure does NOT invalidate the run --------------------
    assert run["status"] == "success"
    assert run["error"] is None
    assert run["tool_outcome"] in ("SUCCESS", "PARTIAL_TOOL_FAILURE")
    assert run["tool_outcome"] != "TOOL_FAILURE"
    assert run["tool_success_count"] == 1
    assert run["tool_failure_count"] == 0

    # --- (4) live internal events carry internal/op metadata + parent call id
    internal = [e for e in events if e.get("internal") is True]
    assert internal, types
    assert {e["type"] for e in internal} == INTERNAL_EVENT_TYPES
    for evt in internal:
        assert evt["provider_tool_call_id"] == "call_research"
        assert evt["operation_kind"] in ("search", "fetch")
        assert evt["operation_id"]
    # exactly one model TOOL_CALL started (web_research) -- no search/fetch call.
    assert types.count("TOOL_CALL_STARTED") == 1
    for evt in events:
        if evt["type"] in MODEL_TOOL_EVENT_TYPES:
            assert evt.get("provider_tool_call_id") == "call_research"
    # internal ops are projected on the search/fetch event names, never as
    # model TOOL_CALL_* events.
    assert not any(
        evt.get("internal") and evt["type"] in MODEL_TOOL_EVENT_TYPES
        for evt in events
    )

    # --- (5) trace hierarchy: internal ops are CHILD spans of TOOL_CALL -----
    async def _spans(span_type: str):
        return await repo.trace_spans.list(
            {"owner_id": owner, "run_id": run["id"], "span_type": span_type},
            sort_field="ordering", descending=False, limit=100,
        )

    tool_spans = call(app_client, _spans, "TOOL_CALL")
    assert len(tool_spans) == 1
    tool_span_id = tool_spans[0]["id"]

    model_spans = call(app_client, _spans, "MODEL_CALL")
    result_spans = call(app_client, _spans, "TOOL_RESULT")
    search_spans = call(app_client, _spans, "SEARCH_OPERATION")
    fetch_spans = call(app_client, _spans, "FETCH_OPERATION")
    assert len(model_spans) == 2
    assert len(result_spans) >= 1
    assert len(search_spans) == 1
    assert len(fetch_spans) == 3
    for sub in search_spans + fetch_spans:
        assert sub["parent_span_id"] == tool_span_id
        assert sub["metadata"]["internal"] is True
        assert sub["metadata"]["provider"] in (
            TINYFISH_SEARCH_PROVIDER_ID, TINYFISH_FETCH_PROVIDER_ID,
        )

    # --- (6) budget counters fold in exactly once ---------------------------
    assert run["research_call_count"] == 1
    assert run["search_call_count"] == 1
    assert run["fetch_call_count"] == 3
    assert run["external_request_count"] == 4  # 1 search + 3 selected fetches

    # --- (7) reasoning segmented per model call, never merged ---------------
    segments = run["reasoning_segments"]
    assert len(segments) == 2
    assert len({s["model_call_id"] for s in segments}) == 2
    assert [s["phase"] for s in segments] == ["initial", "continuation"]
    assert [s["round_index"] for s in segments] == [0, 1]
    assert segments[0]["content"] == "Decide whether one composite research call is enough."
    assert segments[1]["content"] == "Synthesize the fetched evidence into a final answer."
    model_started_ids = [
        e["model_call_id"] for e in events if e["type"] == "MODEL_STARTED"
    ]
    assert model_started_ids == [s["model_call_id"] for s in segments]

    # --- (9) deterministic selection semantics ------------------------------
    normalized = _normalize_search_results(SEARCH_RESULTS)
    expected = _select_research_sources(normalized, 3)
    assert fetch_calls[0] == expected == EXPECTED_SELECTION

    # --- (12) the composite executor never calls the model ------------------
    assert adapter.stream_calls == 2


# ============================================ (11) batch provider sends ONE call
def test_tinyfish_fetch_provider_batches_in_one_call(app_client, as_user, monkeypatch):
    assert TinyFishFetchProvider.SUPPORTS_BATCH_FETCH is True

    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([
        _tool_call_script("call_batch", "web_research",
                          {"query": "batch", "max_fetches": 3}),
        _final_script("batch answer"),
    ])
    _install_adapter(monkeypatch, adapter)

    search_calls: List[Dict[str, Any]] = []
    fetch_calls: List[List[str]] = []
    search_mock, fetch_mock = _build_mocks(
        search_calls=search_calls, fetch_calls=fetch_calls,
    )
    run, _ = _run_research_e2e(
        app_client, owner, model_id, project_id, search_mock, fetch_mock,
    )
    assert run["status"] == "success", run.get("error")
    # ONE batch call for N selected URLs (not N single fetches).
    assert len(fetch_calls) == 1
    assert fetch_calls[0] == EXPECTED_SELECTION


# ================================================ (10) provider batch-cap chunking
def test_protected_max_batch_fetch_chunks_provider_batches(app_client, as_user, monkeypatch):
    monkeypatch.setattr(config, "MAX_BATCH_FETCH_URLS", 2)

    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([
        _tool_call_script("call_cap", "web_research",
                          {"query": "cap", "max_fetches": 5}),
        _final_script("chunked answer"),
    ])
    _install_adapter(monkeypatch, adapter)

    search_calls: List[Dict[str, Any]] = []
    fetch_calls: List[List[str]] = []
    search_mock, fetch_mock = _build_mocks(
        search_calls=search_calls, fetch_calls=fetch_calls,
    )
    run, _ = _run_research_e2e(
        app_client, owner, model_id, project_id, search_mock, fetch_mock,
    )
    assert run["status"] == "success", run.get("error")

    # The provider batch cap (2) chunks execution but does NOT clamp logical
    # breadth: all four unique candidates stay selected (domain-diverse first,
    # then fill) and are fetched in two sequential provider batches.
    normalized = _normalize_search_results(SEARCH_RESULTS)
    expected = _select_research_sources(normalized, 5)
    assert expected == [
        "https://beta.example/b",
        "https://alpha.example/a",
        "https://gamma.example/c",
        "https://alpha.example/a2",
    ]
    assert fetch_calls == [
        ["https://beta.example/b", "https://alpha.example/a"],
        ["https://gamma.example/c", "https://alpha.example/a2"],
    ]
    assert run["fetch_call_count"] == 4
    assert run["external_request_count"] == 5


# ================================================ (8) atomic tools still work
def test_atomic_web_search_and_web_fetch_unchanged(app_client, as_user):
    owner = as_user["id"]

    async def _scenario():
        await tools_service.ensure_builtin_tools()
        search_tool = await tools_service.get_tool(owner, SEARCH_TOOL_ID)
        fetch_tool = await tools_service.get_tool(owner, FETCH_TOOL_ID)

        search_calls: List[str] = []
        fetch_calls: List[str] = []

        async def fake_search(query, *, max_results=None, location=None, language=None):
            search_calls.append(query)
            return _search_payload(query)

        async def fake_fetch(url):
            fetch_calls.append(url)
            return {
                "requested_url": url,
                "final_url": url,
                "status": 200,
                "text": "atomic body",
                "provider": TINYFISH_FETCH_PROVIDER_ID,
            }

        with patch.object(TinyFishSearchProvider, "search",
                          new=AsyncMock(side_effect=fake_search)), \
             patch.object(TinyFishFetchProvider, "fetch",
                          new=AsyncMock(side_effect=fake_fetch)):
            search_doc = await tools_service.execute_tool(
                search_tool, {"query": "atomic query"},
                owner_id=owner, enabled_tool_ids=[SEARCH_TOOL_ID],
                context={"search_provider_id": TINYFISH_SEARCH_PROVIDER_ID},
            )
            fetch_doc = await tools_service.execute_tool(
                fetch_tool, {"url": "https://atomic.example/x"},
                owner_id=owner, enabled_tool_ids=[FETCH_TOOL_ID],
                context={"fetch_provider_id": TINYFISH_FETCH_PROVIDER_ID},
            )
        return search_doc, fetch_doc, search_calls, fetch_calls

    search_doc, fetch_doc, search_calls, fetch_calls = call(app_client, _scenario)

    assert search_doc["execution_status"] == "success"
    assert search_calls == ["atomic query"]
    # run_search returns the provider payload verbatim (unchanged contract).
    assert search_doc["output"]["query"] == "atomic query"
    assert search_doc["output"]["provider"] == TINYFISH_SEARCH_PROVIDER_ID
    assert [r["url"] for r in search_doc["output"]["results"]] == [
        item["url"] for item in SEARCH_RESULTS
    ]

    assert fetch_doc["execution_status"] == "success"
    assert fetch_calls == ["https://atomic.example/x"]
    # run_fetch returns the provider record verbatim (unchanged contract).
    assert fetch_doc["output"] == {
        "requested_url": "https://atomic.example/x",
        "final_url": "https://atomic.example/x",
        "status": 200,
        "text": "atomic body",
        "provider": TINYFISH_FETCH_PROVIDER_ID,
    }
