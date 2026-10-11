"""V4.2.3.8 — HONEST before/after efficiency comparison for the agentic web workflow.

This is a **deterministic offline orchestration simulation**, not a live
benchmark. It drives the REAL canonical loop
(``app.services.execution.execute_model``) and the REAL ``web_search`` /
``web_fetch`` / ``web_research`` executors, but replaces the network provider
boundary (``TinyFishSearchProvider.search`` and the fetch providers'
``fetch`` / ``fetch_many``) with deterministic in-process stubs that count every
invocation.

Two scenarios are compared on the REAL forex prompt::

    coba cek berita forex bulan ini berdasarkan forexfactory yang high impact aja

* OLD (baseline, atomic tools): 4 model calls, 2 search provider requests,
  3 single-URL fetch provider requests.
* NEW (``web_research``): 2 model calls, 1 search request, 1 batched fetch
  provider request covering the same N URLs.

Every measured number (model continuations, provider request counts, URLs
fetched, run status, run counters) comes from the real orchestration. Token
counts are the scripted adapter's reported usage (identical per-call constants
in both scenarios, so totals differ only by the real number of continuations).
Cost is estimated with the production pricing layer from a real pricing
snapshot; if no snapshot is available it is reported as ``unavailable``.

A live acceptance run against a real TinyFish key + real model is still
required; the exact command/env is written into the artifact.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, patch

from app import repository as repo
from app.services import execution as execution_module
from app.services.builtin_tools import (
    _normalize_search_results,
    _select_research_sources,
)
from app.services.fetch_providers import (
    TINYFISH_FETCH_PROVIDER_ID,
    TinyFishFetchProvider,
)
from app.services.pricing import estimate_cost
from app.services.search_providers import (
    TINYFISH_SEARCH_PROVIDER_ID,
    TinyFishSearchProvider,
)
from tests.conftest import call

from _support import create_model, create_project, create_provider

# --------------------------------------------------------------------- constants
PROMPT = "coba cek berita forex bulan ini berdasarkan forexfactory yang high impact aja"

RESEARCH_TOOL_ID = "tool-research-0001"
SEARCH_TOOL_ID = "tool-search-0001"
FETCH_TOOL_ID = "tool-fetch-0001"

URL_A = "https://www.forexfactory.com/calendar?month=2026-09"
URL_B = "https://www.fxstreet.com/news/high-impact-forex"
URL_C = "https://www.investing.com/news/forex-high-impact"

# Same three distinct hosts with ranks 1/2/3 -> deterministic selection [A, B, C].
SEARCH_RESULTS: List[Dict[str, Any]] = [
    {"title": "ForexFactory high impact calendar", "url": URL_A,
     "snippet": "high impact events", "rank": 1},
    {"title": "FXStreet high impact forex news", "url": URL_B,
     "snippet": "high impact news", "rank": 2},
    {"title": "Investing.com forex news", "url": URL_C,
     "snippet": "forex coverage", "rank": 3},
]
EXPECTED_SELECTION = [URL_A, URL_B, URL_C]

#: Deliberately simple, identical per-continuation usage for BOTH scenarios.
#: These are scripted constants, not measured tokenizer counts, so token totals
#: differ only by the genuine number of model continuations.
INPUT_TOKENS_PER_CALL = 850
OUTPUT_TOKENS_PER_CALL = 120

#: Test-only pricing rates (not vendor prices) used to exercise the real
#: pricing layer end to end.
INPUT_PRICE_PER_1M = 1.00
OUTPUT_PRICE_PER_1M = 2.00

REPORT_PATH = (
    Path(__file__).resolve().parents[2] / "test_reports" / "v4238_research_efficiency.md"
)
_HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "benchmark_web_research.py"
)
_spec = importlib.util.spec_from_file_location("benchmark_web_research", _HELPER_PATH)
benchmark = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(benchmark)


# --------------------------------------------------------------------- adapter
class _UsageStreamAdapter:
    """Scripted per-model-call adapter that accumulates reported usage.

    ``scripts`` is one event list per model call (callables are invoked and
    contribute no event). Every terminal ``RUN_COMPLETE`` event is stamped with
    the configured per-call usage and the adapter sums it across calls.
    """

    supports_tools = True
    supports_vision = False

    def __init__(
        self,
        scripts: List[List[Any]],
        *,
        input_tokens_per_call: int = INPUT_TOKENS_PER_CALL,
        output_tokens_per_call: int = OUTPUT_TOKENS_PER_CALL,
    ) -> None:
        self._scripts = list(scripts)
        self._index = 0
        self._in = int(input_tokens_per_call)
        self._out = int(output_tokens_per_call)
        self.stream_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.per_call_usage: List[Dict[str, int]] = []

    async def execute(self, model: Dict[str, Any], req: Any):
        raise AssertionError("streaming path expected")

    async def stream(self, model: Dict[str, Any], req: Any):
        self.stream_calls += 1
        script = self._scripts[self._index] if self._index < len(self._scripts) else []
        self._index += 1
        for evt in script:
            if callable(evt):
                evt()
                continue
            if isinstance(evt, dict) and evt.get("done"):
                self.input_tokens += self._in
                self.output_tokens += self._out
                self.per_call_usage.append(
                    {"input_tokens": self._in, "output_tokens": self._out}
                )
                stamped = dict(evt)
                stamped.setdefault("input_tokens", self._in)
                stamped.setdefault("output_tokens", self._out)
                stamped.setdefault("total_tokens", self._in + self._out)
                stamped.setdefault("usage", {
                    "prompt_tokens": self._in,
                    "completion_tokens": self._out,
                    "total_tokens": self._in + self._out,
                })
                yield stamped
                continue
            yield evt


# --------------------------------------------------------------------- counters
class _Counters:
    """Genuine provider-boundary invocation counter (mocked network only)."""

    def __init__(self) -> None:
        self.search_requests = 0
        self.fetch_provider_requests = 0
        self.search_queries: List[str] = []
        self.fetched_urls: List[str] = []

    @property
    def external_requests(self) -> int:
        return self.search_requests + self.fetch_provider_requests


# --------------------------------------------------------------------- payloads
def _search_payload(query: str) -> Dict[str, Any]:
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


def _single_fetch_record(url: str) -> Dict[str, Any]:
    """Shape returned by the atomic fetch provider ``fetch(url)`` (HTTP 200)."""
    return {
        "requested_url": url,
        "final_url": url,
        "status": 200,
        "title": f"High impact evidence: {url}",
        "text": f"high impact forex evidence from {url}",
        "provider": TINYFISH_FETCH_PROVIDER_ID,
        "egress_mode": "REMOTE",
        "provider_status": 200,
        "duration_ms": 5,
        "metadata": {
            "egress_mode": "REMOTE",
            "provider_endpoint_host": "api.fetch.tinyfish.ai",
            "provider_status": 200,
        },
    }


def _batch_fetch_record(url: str) -> Dict[str, Any]:
    """Shape returned per URL by the batch provider ``fetch_many(urls)``."""
    return {
        "url": url,
        "status": "success",
        "title": f"High impact evidence: {url}",
        "text": f"high impact forex evidence from {url}",
        "final_url": url,
        "fetch_provider": TINYFISH_FETCH_PROVIDER_ID,
        "provider": TINYFISH_FETCH_PROVIDER_ID,
        "egress_mode": "REMOTE",
        "provider_status": 200,
        "duration_ms": 5,
        "metadata": {
            "egress_mode": "REMOTE",
            "provider_endpoint_host": "api.fetch.tinyfish.ai",
            "provider_status": 200,
        },
    }


def _provider_stubs(counters: _Counters) -> Tuple[AsyncMock, AsyncMock, AsyncMock]:
    async def fake_search(query, *, max_results=None, location=None, language=None):
        counters.search_requests += 1
        counters.search_queries.append(query)
        return _search_payload(query)

    async def fake_fetch(url):
        counters.fetch_provider_requests += 1
        counters.fetched_urls.append(url)
        return _single_fetch_record(url)

    async def fake_fetch_many(urls):
        counters.fetch_provider_requests += 1
        counters.fetched_urls.extend(list(urls))
        return [_batch_fetch_record(u) for u in urls]

    return (
        AsyncMock(side_effect=fake_search),
        AsyncMock(side_effect=fake_fetch),
        AsyncMock(side_effect=fake_fetch_many),
    )


# --------------------------------------------------------------------- scripts
def _tool_call_events(calls: List[Tuple[str, str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for index, (call_id, name, arguments) in enumerate(calls):
        events.append({
            "type": "MODEL_TOOL_CALL_DELTA",
            "index": index,
            "id": call_id,
            "name": name,
            "arguments_delta": json.dumps(arguments),
        })
    events.append({
        "type": "RUN_COMPLETE", "done": True,
        "finish_reason": "tool_calls", "tool_calls": [],
    })
    return events


def _final_events(text: str) -> List[Dict[str, Any]]:
    return [
        {"type": "MODEL_OUTPUT_DELTA", "delta": text, "ttft_ms": 1},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop", "tool_calls": []},
    ]


def _old_scripts() -> List[List[Any]]:
    """Observed atomic pattern: search; refined search + fetch A; fetch B + C; answer."""
    return [
        _tool_call_events([
            ("s1", "web_search", {"query": "forexfactory high impact forex news this month"}),
        ]),
        _tool_call_events([
            ("s2", "web_search", {"query": "forexfactory high impact USD EUR calendar"}),
            ("fA", "web_fetch", {"url": URL_A}),
        ]),
        _tool_call_events([
            ("fB", "web_fetch", {"url": URL_B}),
            ("fC", "web_fetch", {"url": URL_C}),
        ]),
        _final_events(
            "Rangkuman berita forex high impact bulan ini dari ForexFactory: "
            "A/B/C evidence synthesized."
        ),
    ]


def _new_scripts() -> List[List[Any]]:
    """Composite pattern: one web_research call; then the final answer."""
    return [
        _tool_call_events([
            ("r1", "web_research", {"query": PROMPT, "max_results": 5, "max_fetches": 3}),
        ]),
        _final_events(
            "Rangkuman berita forex high impact bulan ini dari ForexFactory: "
            "bundled A/B/C evidence synthesized."
        ),
    ]


# --------------------------------------------------------------------- driver
async def _setup(owner: str) -> Tuple[str, str]:
    provider_id = await create_provider(owner, provider_type="openai_compatible")
    project_id = await create_project(owner)
    model_id = await create_model(
        owner,
        provider_id=provider_id,
        capabilities={"tools": True},
        input_price_per_1m=INPUT_PRICE_PER_1M,
        output_price_per_1m=OUTPUT_PRICE_PER_1M,
    )
    return project_id, model_id


async def _run_scenario(
    owner: str,
    model_id: str,
    project_id: str,
    adapter: _UsageStreamAdapter,
    counters: _Counters,
    *,
    enabled_tool_ids: List[str],
):
    search_mock, fetch_mock, fetch_many_mock = _provider_stubs(counters)
    events: List[Dict[str, Any]] = []

    async def sink(evt: Dict[str, Any]) -> None:
        events.append(evt)

    async def _go():
        with patch.object(TinyFishSearchProvider, "search", new=search_mock), \
             patch.object(TinyFishFetchProvider, "fetch", new=fetch_mock), \
             patch.object(TinyFishFetchProvider, "fetch_many", new=fetch_many_mock):
            return await execution_module.execute_model(
                owner, model_id, "You are a forex research assistant.",
                [{"role": "user", "content": PROMPT}], {},
                project_id=project_id,
                enabled_tool_ids=enabled_tool_ids,
                search_enabled=True,
                search_provider_id=TINYFISH_SEARCH_PROVIDER_ID,
                fetch_enabled=True,
                fetch_provider_id=TINYFISH_FETCH_PROVIDER_ID,
                event_sink=sink,
                stream_model=True,
            )

    started = time.perf_counter()
    run = await _go()
    wall_ms = int((time.perf_counter() - started) * 1000)
    return run, events, wall_ms


def _install_adapter(monkeypatch, adapter: _UsageStreamAdapter) -> None:
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)


def _orchestration_model_calls(events: List[Dict[str, Any]]) -> int:
    """Model continuations observed in the REAL orchestration stream."""
    return sum(1 for evt in events if evt.get("type") == "MODEL_STARTED")


def _build_metrics(run, events, adapter, counters, wall_ms):
    snapshot = run.get("pricing_snapshot")
    cost = None
    if isinstance(snapshot, dict):
        cost = estimate_cost(
            snapshot,
            input_tokens=adapter.input_tokens,
            output_tokens=adapter.output_tokens,
        )
    return {
        "model_call_count": _orchestration_model_calls(events),
        "search_requests": counters.search_requests,
        "fetch_provider_requests": counters.fetch_provider_requests,
        "urls_fetched": len(counters.fetched_urls),
        "external_requests": int(run.get("external_request_count") or 0),
        "provider_requests_total": counters.external_requests,
        "input_tokens": adapter.input_tokens,
        "output_tokens": adapter.output_tokens,
        "total_tokens": adapter.input_tokens + adapter.output_tokens,
        "wall_ms": wall_ms,
        "cost_usd": cost,
        "tool_success_count": int(run.get("tool_success_count") or 0),
        "status": run.get("status"),
    }


# ============================================================ primary comparison
def test_web_research_efficiency_before_after(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)

    # ---- OLD: atomic tools, repeated continuations -------------------------
    old_counts = _Counters()
    old_adapter = _UsageStreamAdapter(_old_scripts())
    _install_adapter(monkeypatch, old_adapter)
    old_run, old_events, old_wall = call(
        app_client, _run_scenario, owner, model_id, project_id,
        old_adapter, old_counts,
        enabled_tool_ids=[SEARCH_TOOL_ID, FETCH_TOOL_ID],
    )
    old = _build_metrics(old_run, old_events, old_adapter, old_counts, old_wall)

    # ---- NEW: composite web_research ---------------------------------------
    new_counts = _Counters()
    new_adapter = _UsageStreamAdapter(_new_scripts())
    _install_adapter(monkeypatch, new_adapter)
    new_run, new_events, new_wall = call(
        app_client, _run_scenario, owner, model_id, project_id,
        new_adapter, new_counts,
        enabled_tool_ids=[RESEARCH_TOOL_ID],
    )
    new = _build_metrics(new_run, new_events, new_adapter, new_counts, new_wall)

    # ---- model continuations are real orchestration events -----------------
    assert old["model_call_count"] == old_adapter.stream_calls == 4
    assert new["model_call_count"] == new_adapter.stream_calls == 2

    # ---- health of both runs ----------------------------------------------
    assert old_run["status"] == "success", old_run.get("error")
    assert new_run["status"] == "success", new_run.get("error")
    assert new["tool_success_count"] >= 1

    # ---- the efficiency claims that must hold for PASS ---------------------
    assert new["model_call_count"] < old["model_call_count"]
    assert new["search_requests"] == 1
    assert new["fetch_provider_requests"] == 1  # ONE batched fetch request
    assert old["fetch_provider_requests"] == 3  # three single-URL requests
    assert new["urls_fetched"] == old["urls_fetched"]  # same evidence coverage
    assert new["external_requests"] <= old["external_requests"]
    # The ORCHESTRATION's own folded external-request counter (real run field).
    assert (old["external_requests"], new["external_requests"]) == (5, 4)
    assert old_run["external_request_count"] == 5
    assert new_run["external_request_count"] == 4
    # Provider-boundary requests are genuine too: 1 batch request vs 3 singles.
    assert new["provider_requests_total"] < old["provider_requests_total"]
    assert (old["provider_requests_total"], new["provider_requests_total"]) == (5, 2)
    # Orchestration run fields (real counters) stay internally consistent:
    # external_request_count == search_call_count + fetch_call_count.
    assert old_run["search_call_count"] == 2 and old_run["fetch_call_count"] == 3
    assert new_run["fetch_call_count"] == 3 and new_run["research_call_count"] == 1
    assert old_run["external_request_count"] == (
        old_run["search_call_count"] + old_run["fetch_call_count"]
    )
    assert new_run["external_request_count"] == (
        new_run["search_call_count"] + new_run["fetch_call_count"]
    )
    assert old_run["unique_fetched_urls"] == new_run["unique_fetched_urls"]
    assert sorted(old_counts.fetched_urls) == sorted(new_counts.fetched_urls)

    # ---- evidence is genuinely the same set/order --------------------------
    assert old_counts.fetched_urls == EXPECTED_SELECTION
    assert new_counts.fetched_urls == EXPECTED_SELECTION
    assert old_run["unique_fetched_urls"] == EXPECTED_SELECTION
    assert new_run["unique_fetched_urls"] == EXPECTED_SELECTION

    # ---- web_research selection determinism (same selected URLs each time) -
    normalized = _normalize_search_results(SEARCH_RESULTS)
    assert _select_research_sources(normalized, 3) == EXPECTED_SELECTION

    repeat_counts = _Counters()
    repeat_adapter = _UsageStreamAdapter(_new_scripts())
    _install_adapter(monkeypatch, repeat_adapter)
    repeat_run, repeat_events, _ = call(
        app_client, _run_scenario, owner, model_id, project_id,
        repeat_adapter, repeat_counts,
        enabled_tool_ids=[RESEARCH_TOOL_ID],
    )
    assert repeat_run["status"] == "success", repeat_run.get("error")
    assert repeat_counts.fetched_urls == new_counts.fetched_urls == EXPECTED_SELECTION
    assert repeat_counts.search_requests == 1
    assert repeat_counts.fetch_provider_requests == 1
    assert repeat_run["unique_fetched_urls"] == new_run["unique_fetched_urls"]

    # ---- cost is real (pricing snapshot) or explicitly unavailable ---------
    old_snapshot = old_run.get("pricing_snapshot")
    new_snapshot = new_run.get("pricing_snapshot")
    assert isinstance(old_snapshot, dict) and isinstance(new_snapshot, dict)
    assert old_snapshot.get("input_price_per_1m") == INPUT_PRICE_PER_1M
    assert old_snapshot.get("output_price_per_1m") == OUTPUT_PRICE_PER_1M
    if old["cost_usd"] is None or new["cost_usd"] is None:
        cost_note = (
            "Cost: **unavailable**. The scripted model carried no priced pricing "
            "snapshot, so no cost is invented."
        )
    else:
        cost_note = (
            "Cost is estimated with the production pricing layer. A real "
            "`pricing_snapshot(model)` is captured on each Run at execution time, and "
            "`estimate_cost(snapshot, input_tokens, output_tokens)` applies the model's "
            f"configured rates (input ${INPUT_PRICE_PER_1M:.2f}/1M, output "
            f"${OUTPUT_PRICE_PER_1M:.2f}/1M) to the adapter-reported token totals summed "
            "across all model continuations. The rates are deliberately simple test "
            "rates, not vendor prices."
        )

    # ---- write the deterministic artifact ----------------------------------
    written = benchmark.write_report(
        REPORT_PATH, old=old, new=new, prompt=PROMPT, cost_note=cost_note,
    )
    text = Path(written).read_text(encoding="utf-8")
    assert PROMPT in text
    assert "deterministic offline orchestration simulation" in text
    assert "live acceptance run" in text.lower()
    assert "web_research" in text
    # The deterministic counters must be present verbatim in the table.
    assert f"| model calls | {old['model_call_count']} | {new['model_call_count']}" in text
    assert f"| fetch provider requests | {old['fetch_provider_requests']} | {new['fetch_provider_requests']}" in text
    assert REPORT_PATH.exists()

    # ---- deterministic rendering check (wall time excluded from equality) --
    # Re-rendering the same counters produces byte-identical markdown.
    assert benchmark.render_report(old=old, new=new, prompt=PROMPT, cost_note=cost_note) == text
