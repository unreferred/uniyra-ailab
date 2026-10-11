"""OMNILAB V4.2.3.8 composite ``web_research`` builtin tests (offline).

The composite tool is deterministic orchestration only -- Search, a
deterministic URL-selection pass, then batched Fetch (chunked by the provider
execution batch cap) -- and never makes an LLM/model call. These tests pin:

  * the catalog definition (both permissions + explicit routing description),
  * search -> batched fetch in one invocation,
  * the pure ``_select_research_sources`` algorithm (normalize, dedupe, rank,
    domain diversity, logical breadth cap),
  * provider execution batching: ``config.MAX_BATCH_FETCH_URLS`` is the
    protected ceiling on the provider batch size and never caps logical
    research breadth,
  * partial-failure semantics (timeout mapping, outcomes, summary counts),
  * evidence provenance (url/fetch_provider/duration_ms, budget counters),
  * the frozen ``operation_sink`` op-dict protocol and ordering,
  * that ``run_search`` / ``run_fetch`` are unchanged.

All provider adapters are mocked; no network, no DB writes, no model calls.
"""
from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app import config
from app.services import builtin_tools
from app.services.builtin_tools import (
    BUILTIN_TOOL_DEFINITIONS,
    _select_research_sources,
    run_fetch,
    run_research,
    run_search,
)
from tests.conftest import call


SEARCH_PROVIDER = "prov-search-v4238"
FETCH_PROVIDER = "prov-fetch-v4238"


# ------------------------------------------------------------------- fakes
class _FakeSearchAdapter:
    """Minimal search adapter double (no network, records calls)."""

    def __init__(self, results: List[dict], provider: str = SEARCH_PROVIDER, duration_ms: int = 7):
        self.id = provider
        self._results = results
        self._duration_ms = duration_ms
        self.calls: List[dict] = []

    async def search(self, query, *, max_results=None, location=None, language=None):
        self.calls.append({
            "query": query,
            "max_results": max_results,
            "location": location,
            "language": language,
        })
        return {
            "query": query,
            "provider": self.id,
            "egress_mode": "REMOTE",
            "duration_ms": self._duration_ms,
            "results": list(self._results),
            "count": len(self._results),
            "metadata": {"provider_endpoint_host": "api.search.tinyfish.ai"},
        }


class _FakeBatchFetchAdapter:
    """Batch-capable fetch adapter double: records the single fetch_many call."""

    supports_batch_fetch = True

    def __init__(self, records: List[dict], provider: str = FETCH_PROVIDER):
        self.id = provider
        self.egress_mode = "REMOTE"
        self._records = records
        self.fetch_many_calls: List[List[str]] = []
        self.fetch_calls: List[str] = []

    async def fetch_many(self, urls):
        self.fetch_many_calls.append(list(urls))
        if callable(self._records):
            return self._records(urls)
        return list(self._records)

    async def fetch(self, url):
        self.fetch_calls.append(url)
        return {"requested_url": url, "final_url": url, "text": "should not be used"}


class _FakeSingleFetchAdapter:
    """Non-batch fetch adapter double."""

    supports_batch_fetch = False

    def __init__(self, provider: str = FETCH_PROVIDER):
        self.id = provider
        self.egress_mode = "LOCAL"
        self.fetch_calls: List[str] = []

    async def fetch(self, url):
        self.fetch_calls.append(url)
        return {
            "requested_url": url,
            "final_url": url,
            "text": "body",
            "title": "Single",
            "status": 200,
            "fetch_provider": self.id,
            "egress_mode": "LOCAL",
            "duration_ms": 4,
        }


# ------------------------------------------------------------------- helpers
def _search_results(urls: List[str]) -> List[dict]:
    return [
        {"title": f"Result {i}", "url": url, "snippet": f"snippet {i}", "rank": i + 1}
        for i, url in enumerate(urls)
    ]


def _success_record(url: str, *, text: str = "body", duration_ms: int = 5) -> dict:
    return {
        "url": url,
        "status": "success",
        "title": f"Title {url}",
        "text": text,
        "final_url": url,
        "provider": FETCH_PROVIDER,
        "fetch_provider": FETCH_PROVIDER,
        "egress_mode": "REMOTE",
        "provider_status": 200,
        "duration_ms": duration_ms,
        "metadata": {
            "egress_mode": "REMOTE",
            "provider_endpoint_host": "api.fetch.tinyfish.ai",
            "provider_status": 200,
        },
    }


def _timeout_record(url: str, *, timeout_seconds: int = 20) -> dict:
    return {
        "url": url,
        "status": "failed",
        "error": {
            "code": "FETCH_TIMEOUT",
            "message": "request timed out",
            "timeout_seconds": timeout_seconds,
        },
    }


def _run_research(
    app_client,
    arguments: dict,
    *,
    search_adapter: Any,
    fetch_adapter: Any = None,
    context: Optional[dict] = None,
):
    """Invoke ``run_research`` with both provider resolvers mocked."""
    if context is None:
        context = {}
    with patch.object(
        builtin_tools, "_resolve_search_adapter", new=AsyncMock(return_value=search_adapter)
    ), patch.object(
        builtin_tools, "_resolve_fetch_adapter", new=AsyncMock(return_value=fetch_adapter)
    ):
        return call(app_client, run_research, arguments, context)


# ------------------------------------------------------------------- definition
def test_web_research_tool_definition_present():
    tools = {t["id"]: t for t in BUILTIN_TOOL_DEFINITIONS}
    assert "tool-research-0001" in tools
    tool = tools["tool-research-0001"]

    assert tool["name"] == "web_research"
    assert tool["display_name"] == "Web Research"
    assert tool["scope"] == "global"
    assert tool["builtin"] is True
    assert tool["risk_level"] == "medium"
    assert tool["permissions"] == ["search.execute", "fetch.execute"]
    assert tool["implementation"] == "builtin:web_research"
    assert tool["tags"] == ["web", "search", "fetch", "research"]

    description = tool["description"]
    assert "web_search" in description
    assert "web_fetch" in description
    assert "multiple pages" in description

    schema = tool["input_schema"]
    assert schema["required"] == ["query"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["max_results"]["default"] == 5
    assert schema["properties"]["max_results"]["minimum"] == 1
    assert schema["properties"]["max_results"]["maximum"] == 10
    assert schema["properties"]["max_fetches"]["default"] == 5
    assert schema["properties"]["max_fetches"]["minimum"] == 1
    assert schema["properties"]["max_fetches"]["maximum"] == 10
    assert schema["properties"]["max_results"]["description"] == (
        "Maximum number of search candidates to return"
    )
    assert schema["properties"]["max_fetches"]["description"] == (
        "Maximum number of candidate URLs to fetch for deeper evidence"
    )
    assert "sources" in tool["output_schema"]["properties"]


def test_web_research_definition_via_executor_lookup():
    assert builtin_tools.builtin_implementation("builtin:web_research") is run_research


# ---------------------------------------------------------- single invocation
def test_run_research_search_then_one_batch_fetch(app_client):
    urls = ["https://a.example.com/1", "https://b.example.com/1"]
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client,
        {"query": "quantum", "max_results": 5, "max_fetches": 3, "location": "ID", "language": "id"},
        search_adapter=search,
        fetch_adapter=fetch,
    )

    # Search ran exactly once, then ONE batch call with the selected URLs.
    assert len(search.calls) == 1
    assert search.calls[0]["query"] == "quantum"
    assert search.calls[0]["max_results"] == 5
    assert search.calls[0]["location"] == "ID"
    assert search.calls[0]["language"] == "id"
    assert fetch.fetch_many_calls == [urls]
    assert fetch.fetch_calls == []
    assert fetch.supports_batch_fetch is True

    assert set(out) == {
        "query",
        "outcome",
        "search",
        "sources",
        "summary",
        "internal_operations",
        "internal_budget",
        "provider",
        "search_provider",
        "fetch_provider",
        "duration_ms",
        "research",
    }
    assert out["outcome"] == "SUCCESS"
    assert out["query"] == "quantum"
    assert out["search"]["provider"] == SEARCH_PROVIDER
    assert [r["url"] for r in out["search"]["results"]] == urls
    assert [s["url"] for s in out["sources"]] == urls
    assert out["summary"] == {"searched": 2, "selected": 2, "fetched": 2, "failed": 0}
    assert out["provider"] == SEARCH_PROVIDER
    assert out["search_provider"] == SEARCH_PROVIDER
    assert out["fetch_provider"] == FETCH_PROVIDER
    assert isinstance(out["duration_ms"], int)


# ------------------------------------------------------- deterministic selection
def test_select_research_sources_dedupes_and_preserves_order():
    results = [
        {"url": "https://a.com/one", "rank": 1},
        {"url": "https://a.com/one#dup", "rank": 2},
        {"url": "https://a.com/two", "rank": 3},
        {"url": "https://b.com/one", "rank": 4},
    ]
    assert _select_research_sources(results, 3) == [
        "https://a.com/one",
        "https://b.com/one",
        "https://a.com/two",
    ]
    assert _select_research_sources(results, 2) == [
        "https://a.com/one",
        "https://b.com/one",
    ]


def test_select_research_sources_normalizes_and_ranks():
    results = [
        {"url": "HTTPS://A.COM/Path?Q=1#fragment", "rank": 2},
        {"url": "https://b.com/x", "rank": 1},
        {"url": "   ", "rank": 3},
        {"url": "https://a.com/Path?Q=1", "rank": 4},
    ]
    # Fragment stripped, scheme+host lowercased, blanks dropped, duplicate
    # collapsed, ranked (b.com first).
    assert _select_research_sources(results, 5) == [
        "https://b.com/x",
        "https://a.com/Path?Q=1",
    ]


def test_select_research_sources_zero_and_empty():
    assert _select_research_sources([], 3) == []
    assert _select_research_sources([{"url": "https://a.com"}], 0) == []
    assert _select_research_sources(None, 3) == []


# ------------------------------------------------------- provider batch chunks
def test_protected_max_batch_fetch_urls_chunks_provider_batches(app_client, monkeypatch):
    monkeypatch.setattr(config, "MAX_BATCH_FETCH_URLS", 2)
    urls = [f"https://h{i}.example.com/p" for i in range(4)]
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 5},
        search_adapter=search,
        fetch_adapter=fetch,
    )

    # The protected ceiling bounds provider EXECUTION batch size, not logical
    # breadth: all 4 candidates stay selected but are fetched in two chunks.
    assert out["summary"]["selected"] == 4
    assert fetch.fetch_many_calls == [
        [urls[0], urls[1]],
        [urls[2], urls[3]],
    ]
    assert out["research"]["provider_batch_cap"] == 2
    assert out["research"]["provider_batch_count"] == 2
    assert out["research"]["fetch_urls_selected"] == 4


# ------------------------------------------------------------ partial failure
def test_run_research_partial_failure_timeout(app_client):
    urls = ["https://good.example.com", "https://slow.example.com"]
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(urls[0]), _timeout_record(urls[1])])

    out = _run_research(
        app_client, {"query": "q", "max_fetches": 2}, search_adapter=search, fetch_adapter=fetch
    )

    assert out["outcome"] == "PARTIAL_SUCCESS"
    assert out["summary"] == {"searched": 2, "selected": 2, "fetched": 1, "failed": 1}
    assert out["sources"][0]["status"] == "success"
    assert out["sources"][1]["status"] == "timeout"
    assert out["sources"][1]["error"]["code"] == "FETCH_TIMEOUT"
    assert out["sources"][1]["error"]["timeout_seconds"] == 20


def test_run_research_whole_batch_transport_error_is_partial(app_client):
    from app.services.fetch_providers import FetchError

    urls = ["https://a.example.com", "https://b.example.com"]

    def _raise(_urls):
        raise FetchError("transport down", 502, "NETWORK_ERROR", code="NETWORK_ERROR")

    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter(_raise)

    out = _run_research(
        app_client, {"query": "q", "max_fetches": 2}, search_adapter=search, fetch_adapter=fetch
    )

    assert out["outcome"] == "PARTIAL_SUCCESS"
    assert out["summary"]["fetched"] == 0
    assert out["summary"]["failed"] == 2
    assert all(s["status"] == "failed" for s in out["sources"])
    assert all(s["error"]["code"] == "NETWORK_ERROR" for s in out["sources"])


def test_run_research_non_batch_adapter_loops_fetch(app_client):
    urls = ["https://a.example.com/1", "https://b.example.com/1"]
    search = _FakeSearchAdapter(_search_results(urls))
    single = _FakeSingleFetchAdapter()

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 2},
        search_adapter=search,
        fetch_adapter=single,
    )

    assert single.fetch_calls == urls
    assert out["summary"] == {"searched": 2, "selected": 2, "fetched": 2, "failed": 0}
    assert all(s["fetch_provider"] == FETCH_PROVIDER for s in out["sources"])
    assert [op["type"] for op in out["internal_operations"]] == [
        "SEARCH_OPERATION",
        "FETCH_OPERATION",
        "FETCH_OPERATION",
    ]


def test_run_research_requires_query(app_client):
    with pytest.raises(ValueError):
        _run_research(
            app_client,
            {"max_fetches": 1},
            search_adapter=_FakeSearchAdapter([]),
        )
    with pytest.raises(ValueError):
        _run_research(
            app_client,
            {"query": "   "},
            search_adapter=_FakeSearchAdapter([]),
        )


# ------------------------------------------------------------- provenance
def test_run_research_evidence_provenance_and_budget(app_client):
    urls = ["https://a.example.com/1", "https://b.example.com/2"]
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(urls[0]), _success_record(urls[1], duration_ms=9)])

    out = _run_research(
        app_client, {"query": "provenance", "max_fetches": 3}, search_adapter=search, fetch_adapter=fetch
    )

    for source in out["sources"]:
        assert source["fetch_provider"] == FETCH_PROVIDER
        assert isinstance(source["duration_ms"], int)
    assert out["sources"][1]["duration_ms"] == 9
    assert out["internal_budget"] == {
        "search_calls": 1,
        "fetch_calls": 2,
        "external_requests": 3,
    }
    types = [op["type"] for op in out["internal_operations"]]
    assert types == ["SEARCH_OPERATION", "FETCH_OPERATION", "FETCH_OPERATION"]
    search_op = out["internal_operations"][0]
    assert search_op["provider"] == SEARCH_PROVIDER
    assert search_op["metadata"]["count"] == 2
    assert search_op["metadata"]["egress_mode"] == "REMOTE"
    assert search_op["metadata"]["provider_endpoint_host"] == "api.search.tinyfish.ai"
    fetch_op = out["internal_operations"][1]
    assert fetch_op["provider"] == FETCH_PROVIDER
    assert fetch_op["status"] == "success"
    assert fetch_op["metadata"]["final_url"] == urls[0]


# ------------------------------------------------------------ no nested model
def test_run_research_makes_no_model_or_provider_import(app_client):
    assert "providers" not in builtin_tools.__dict__
    assert "execution" not in builtin_tools.__dict__

    search = _FakeSearchAdapter(_search_results(["https://a.example.com"]))
    fetch = _FakeBatchFetchAdapter([_success_record("https://a.example.com")])
    assert not hasattr(search, "execute")
    assert not hasattr(fetch, "execute")

    out = _run_research(
        app_client, {"query": "q"}, search_adapter=search, fetch_adapter=fetch
    )
    assert out["query"] == "q"
    assert len(search.calls) == 1
    assert len(fetch.fetch_many_calls) == 1


# ------------------------------------------------------------- operation sink
def test_operation_sink_receives_frozen_op_dicts_in_order(app_client):
    urls = ["https://a.example.com/1", "https://b.example.com/1"]
    search = _FakeSearchAdapter(_search_results(urls), duration_ms=11)
    fetch = _FakeBatchFetchAdapter([_success_record(u, duration_ms=3) for u in urls])

    events: List[dict] = []

    async def sink(op):
        events.append(op)

    out = _run_research(
        app_client,
        {"query": "sink q", "max_fetches": 2},
        search_adapter=search,
        fetch_adapter=fetch,
        context={"operation_sink": sink},
    )

    assert [(e["op"], e["kind"]) for e in events] == [
        ("start", "search"),
        ("complete", "search"),
        ("start", "fetch"),
        ("start", "fetch"),
        ("complete", "fetch"),
        ("complete", "fetch"),
    ]
    assert set(events[0]) == {"op", "kind", "operation_id", "query", "provider"}
    assert set(events[1]) == {
        "op", "kind", "operation_id", "query", "provider", "count", "duration_ms", "status",
    }
    assert set(events[2]) == {"op", "kind", "operation_id", "url", "provider"}
    assert set(events[4]) == {
        "op", "kind", "operation_id", "url", "provider", "final_url", "duration_ms", "status",
    }
    assert events[0]["operation_id"] == events[1]["operation_id"]
    assert events[0]["query"] == "sink q"
    assert events[1]["count"] == 2
    assert events[1]["duration_ms"] == 11
    assert events[2]["operation_id"] == events[4]["operation_id"]
    assert events[3]["operation_id"] == events[5]["operation_id"]
    assert out["internal_budget"]["external_requests"] == 3


def test_operation_sink_failed_op_shape(app_client):
    urls = ["https://a.example.com/1"]
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_timeout_record(urls[0])])
    events: List[dict] = []

    async def sink(op):
        events.append(op)

    _run_research(
        app_client,
        {"query": "q", "max_fetches": 1},
        search_adapter=search,
        fetch_adapter=fetch,
        context={"operation_sink": sink},
    )

    failed = events[-1]
    assert failed["op"] == "failed"
    assert failed["status"] == "failed"
    assert set(failed) == {
        "op", "kind", "operation_id", "url", "provider", "duration_ms", "status", "error",
    }
    assert failed["error"]["code"] == "FETCH_TIMEOUT"


def test_operation_sink_exceptions_are_swallowed(app_client):
    urls = ["https://a.example.com/1"]
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(urls[0])])

    async def bad_sink(op):
        raise RuntimeError("sink exploded")

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 1},
        search_adapter=search,
        fetch_adapter=fetch,
        context={"operation_sink": bad_sink},
    )
    assert out["summary"]["fetched"] == 1


# ------------------------------------------------------------- research budget
def test_research_budget_search_remaining_zero_skips_search(app_client):
    search_resolver = AsyncMock(return_value=_FakeSearchAdapter(_search_results(["https://a.example.com"])))
    fetch = _FakeBatchFetchAdapter([])

    with patch.object(builtin_tools, "_resolve_search_adapter", new=search_resolver), patch.object(
        builtin_tools, "_resolve_fetch_adapter", new=AsyncMock(return_value=fetch)
    ):
        out = call(
            app_client,
            run_research,
            {"query": "q"},
            {"research_budget": {"search_remaining": 0, "fetch_remaining": 2}},
        )

    assert search_resolver.await_count == 0
    assert out["internal_budget"]["search_calls"] == 0
    assert out["search"]["results"] == []
    assert out["sources"] == []
    assert out["outcome"] == "SUCCESS"
    assert fetch.fetch_many_calls == []


def test_research_budget_caps_fetch_count(app_client):
    urls = [f"https://h{i}.example.com/p" for i in range(3)]
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 3},
        search_adapter=search,
        fetch_adapter=fetch,
        context={"research_budget": {"search_remaining": 1, "fetch_remaining": 1}},
    )

    assert out["summary"]["selected"] == 1
    assert fetch.fetch_many_calls == [[urls[0]]]
    assert out["internal_budget"]["fetch_calls"] == 1


# --------------------------------------------------- atomic tools unchanged
def test_run_search_and_run_fetch_unchanged(app_client):
    search = _FakeSearchAdapter(_search_results(["https://only.example.com"]))
    with patch.object(
        builtin_tools, "_resolve_search_adapter", new=AsyncMock(return_value=search)
    ):
        out = call(app_client, run_search, {"query": "q", "max_results": 3})
    assert out["results"][0]["url"] == "https://only.example.com"
    assert search.calls[0] == {
        "query": "q",
        "max_results": 3,
        "location": None,
        "language": None,
    }

    single = _FakeSingleFetchAdapter()
    with patch.object(
        builtin_tools, "_resolve_fetch_adapter", new=AsyncMock(return_value=single)
    ):
        out2 = call(app_client, run_fetch, {"url": "https://only.example.com"})
    assert out2["requested_url"] == "https://only.example.com"
    assert out2["final_url"] == "https://only.example.com"
    assert out2["text"] == "body"
    assert single.fetch_calls == ["https://only.example.com"]
