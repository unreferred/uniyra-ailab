"""OMNILAB V4.2.3.12 web-research Lab-mode ceiling + quota semantics (offline).

Proves the V4.2.3.12 additive changes:

* ``MAX_RESEARCH_CALLS_PER_RUN`` is now an emergency-only circuit breaker (30),
  not the practical throttle (TinyFish provider quota is).
* A long (>=15) composite ``web_research`` run completes without hitting
  ``TOOL_BUDGET_EXHAUSTED`` -- in particular call #4 is no longer throttled.
* The emergency ceiling still fires when explicitly lowered.
* Provider quota is consumed exactly once at the adapter boundary (1 search +
  N fetched URLs) with no double counting; run-local cache/reuse consumes
  nothing; StandardFetch consumes no TinyFish quota.
* ``PROVIDER_RATE_LIMITED`` stays distinct from ``TOOL_BUDGET_EXHAUSTED``.
* No-progress and total-tool emergency ceilings still work.
* V4.2.3.11 forced finalization still succeeds after a long research run.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from app import config
from app.services import execution as execution_module
from app.services import provider_quota
from app.services import tools as tools_service
from app.services.builtin_tools import (
    _normalize_search_results,
    _select_research_sources,
)
from app.services.fetch_providers import (
    STANDARD_FETCH_PROVIDER_ID,
    TINYFISH_FETCH_PROVIDER_ID,
    StandardFetchProvider,
    TinyFishFetchProvider,
)
from app.services.search_providers import (
    TINYFISH_SEARCH_PROVIDER_ID,
    SearchError,
    TinyFishSearchProvider,
)
from tests.conftest import call

from _support import create_model, create_project, create_provider, result_success

RESEARCH_TOOL_ID = "tool-research-0001"
SEARCH_TOOL_ID = "tool-search-0001"
FETCH_TOOL_ID = "tool-fetch-0001"

SEARCH_LIMITS = {"minute": 30, "hour": 500}
FETCH_LIMITS = {"minute": 150, "day": 1000}


# --------------------------------------------------------------------- helpers
def _search_row(rows, window="minute"):
    return next(r for r in rows if r["resource"] == "search" and r["window"] == window)


def _fetch_row(rows, window="minute"):
    return next(r for r in rows if r["resource"] == "fetch" and r["window"] == window)


class _StreamAdapter:
    """Fake streaming adapter driven by per-model-call scripts (offline)."""

    supports_tools = True
    supports_vision = False

    def __init__(self, scripts: Optional[List[List[Any]]] = None):
        self._scripts = list(scripts or [])
        self._index = 0
        self.stream_calls = 0
        self.requests: List[Any] = []

    async def execute(self, model: Dict[str, Any], req: Any):
        return result_success("answer")

    async def stream(self, model: Dict[str, Any], req: Any):
        self.stream_calls += 1
        self.requests.append(req)
        script = self._scripts[self._index] if self._index < len(self._scripts) else []
        self._index += 1
        for evt in script:
            if callable(evt):
                evt()
                continue
            yield evt


def _install_adapter(monkeypatch, adapter: _StreamAdapter):
    monkeypatch.setattr(execution_module, "_adapter_for", lambda provider: adapter)


async def _setup(owner: str):
    provider_id = await create_provider(owner, provider_type="openai_compatible")
    project_id = await create_project(owner)
    model_id = await create_model(
        owner, provider_id=provider_id, capabilities={"tools": True},
    )
    return project_id, model_id


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


def _fetch_tool_doc() -> Dict[str, Any]:
    return {
        "id": FETCH_TOOL_ID,
        "name": "web_fetch",
        "display_name": "Web Fetch",
        "description": "Fetch a URL.",
        "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}},
        "permissions": [],
        "tool_type": "function",
        "enabled": True,
    }


def _tool_call_script(call_id: str, name: str, arguments: Any) -> List[Dict[str, Any]]:
    return [
        {"type": "MODEL_TOOL_CALL_DELTA", "index": 0, "id": call_id,
         "name": name, "arguments_delta": json.dumps(arguments)},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "tool_calls",
         "tool_calls": []},
    ]


def _final_script(text: str) -> List[Dict[str, Any]]:
    return [
        {"type": "MODEL_OUTPUT_DELTA", "delta": text, "ttft_ms": 1},
        {"type": "RUN_COMPLETE", "done": True, "finish_reason": "stop",
         "tool_calls": []},
    ]


def _research_output(n: int) -> Dict[str, Any]:
    url = f"https://example.com/research/{n}"
    return {
        "query": f"query {n}",
        "outcome": "SUCCESS",
        "search": {"provider": "tinyfish",
                   "results": [{"url": url, "title": f"T{n}"}]},
        "sources": [{
            "url": url, "title": f"T{n}", "status": "success",
            "text": f"evidence {n}", "fetch_provider": "standard", "duration_ms": 3,
        }],
        "summary": {"searched": 1, "selected": 1, "fetched": 1, "failed": 0},
        "internal_operations": [],
        "internal_budget": {"search_calls": 1, "fetch_calls": 1, "external_requests": 2},
        "provider": "tinyfish",
        "search_provider": "tinyfish",
        "fetch_provider": "standard",
        "duration_ms": 5,
    }


def _research_executor(counter: Dict[str, int]):
    async def fake_execute(tool, arguments, **kwargs):
        counter["n"] += 1
        return {
            "id": f"tc-{counter['n']}",
            "provider_tool_call_id": kwargs.get("provider_tool_call_id"),
            "execution_status": "success",
            "output": _research_output(counter["n"]),
            "error": None,
            "duration_ms": 5,
        }
    return fake_execute


async def _run_with_sink(
    owner: str,
    model_id: str,
    project_id: str,
    *,
    enabled_tool_ids: Optional[List[str]] = None,
    max_tool_rounds: Optional[int] = None,
    search_provider_id: Optional[str] = None,
    fetch_provider_id: Optional[str] = None,
):
    events: List[Dict[str, Any]] = []

    async def sink(evt: Dict[str, Any]) -> None:
        events.append(evt)

    run = await execution_module.execute_model(
        owner, model_id, "system", [{"role": "user", "content": "hi"}], {},
        project_id=project_id, enabled_tool_ids=enabled_tool_ids,
        max_tool_rounds=max_tool_rounds,
        search_enabled=bool(search_provider_id), search_provider_id=search_provider_id,
        fetch_enabled=bool(fetch_provider_id), fetch_provider_id=fetch_provider_id,
        event_sink=sink, stream_model=True,
    )
    return run, events


def _drive_research(owner, model_id, project_id, adapter, fake_execute, *, max_rounds=None):
    async def _run():
        with patch.object(
            tools_service, "resolve_enabled_tools",
            new=AsyncMock(return_value=[_research_tool_doc()]),
        ), patch.object(
            tools_service, "execute_tool",
            new=AsyncMock(side_effect=fake_execute),
        ):
            return await _run_with_sink(
                owner, model_id, project_id,
                enabled_tool_ids=[RESEARCH_TOOL_ID], max_tool_rounds=max_rounds,
            )
    return _run


def _types(events: List[Dict[str, Any]]) -> List[str]:
    return [evt.get("type") for evt in events]


def _budget_rounds(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in run.get("tool_rounds") or [] if r.get("budget")]


def _messages_blob(adapter: _StreamAdapter, index: int = -1) -> str:
    try:
        req = adapter.requests[index]
        return json.dumps([m.get("content") for m in req.messages], default=str)
    except Exception:
        return ""


# ====================================================== 1: config ceiling = 30
def test_research_ceiling_default_is_thirty():
    assert config.MAX_RESEARCH_CALLS_PER_RUN == 30


# ====================================== 2: 15 research rounds, no budget error
def test_fifteen_research_rounds_execute(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    scripts = [
        _tool_call_script(f"r{i}", "web_research", {"query": f"query {i}"})
        for i in range(15)
    ]
    scripts.append(_final_script("final synthesis after many research calls"))
    adapter = _StreamAdapter(scripts)
    _install_adapter(monkeypatch, adapter)
    counter = {"n": 0}

    run, events = call(app_client, _drive_research(
        owner, model_id, project_id, adapter, _research_executor(counter),
        max_rounds=16,
    ))

    assert run["status"] == "success", run.get("error")
    assert run["research_call_count"] == 15
    assert run["tool_budget"]["max_research_calls"] == 30
    assert "TOOL_BUDGET_EXHAUSTED" not in _messages_blob(adapter)
    assert not _budget_rounds(run), _budget_rounds(run)
    assert run["normalized_response"] == "final synthesis after many research calls"
    assert "MODEL_CALL_TIMEOUT" not in _types(events)


# ====================================== 3: call #4 is never budget throttled
def test_fourth_research_call_is_not_budget_exhausted(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    scripts = [
        _tool_call_script(f"r{i}", "web_research", {"query": f"query {i}"})
        for i in range(4)
    ]
    scripts.append(_final_script("answer after four research calls"))
    adapter = _StreamAdapter(scripts)
    _install_adapter(monkeypatch, adapter)
    counter = {"n": 0}

    run, events = call(app_client, _drive_research(
        owner, model_id, project_id, adapter, _research_executor(counter),
        max_rounds=5,
    ))

    assert run["status"] == "success", run.get("error")
    assert run["research_call_count"] == 4
    assert counter["n"] == 4
    assert not any(
        r.get("budget") == "research_budget" for r in run["tool_rounds"]
    )
    assert not any(
        "TOOL_BUDGET_EXHAUSTED" in json.dumps(e, default=str) for e in events
    )


# ==================================== 4: lowered emergency ceiling still fires
def test_lowered_research_ceiling_enforced(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    monkeypatch.setattr(config, "MAX_RESEARCH_CALLS_PER_RUN", 2)
    scripts = [
        _tool_call_script(f"r{i}", "web_research", {"query": f"query {i}"})
        for i in range(3)
    ]
    scripts.append(_final_script("final after emergency ceiling"))
    adapter = _StreamAdapter(scripts)
    _install_adapter(monkeypatch, adapter)
    counter = {"n": 0}

    run, events = call(app_client, _drive_research(
        owner, model_id, project_id, adapter, _research_executor(counter),
        max_rounds=6,
    ))

    assert run["status"] == "success", run.get("error")
    assert counter["n"] == 2  # third composite call skipped before execution
    budget_rounds = _budget_rounds(run)
    assert budget_rounds, run["tool_rounds"]
    assert budget_rounds[-1]["budget"] == "research_budget"
    assert budget_rounds[-1]["execution_status"] == "skipped"
    assert run["finalization_reason"] == "research_budget"
    # The canonical budget error was handed to the model for finalization.
    assert "TOOL_BUDGET_EXHAUSTED" in _messages_blob(adapter)


# ============================== 5/6: research quota = 1 search + 3 fetches
_SEARCH_RESULTS: List[Dict[str, Any]] = [
    {"title": "Alpha one", "url": "https://alpha.example/a", "snippet": "sa", "rank": 3},
    {"title": "Beta one", "url": "https://beta.example/b", "snippet": "sb", "rank": 1},
    {"title": "Alpha dup", "url": "https://alpha.example/a#frag", "snippet": "sdup", "rank": 2},
    {"title": "Gamma one", "url": "https://gamma.example/c", "snippet": "sc", "rank": 4},
    {"title": "Alpha two", "url": "https://alpha.example/a2", "snippet": "sa2", "rank": 5},
]
_EXPECTED_SELECTION = [
    "https://beta.example/b",
    "https://alpha.example/a",
    "https://gamma.example/c",
]


def _search_payload(query: str) -> Dict[str, Any]:
    return {
        "query": query,
        "provider": TINYFISH_SEARCH_PROVIDER_ID,
        "search_provider": TINYFISH_SEARCH_PROVIDER_ID,
        "provider_type": "tinyfish",
        "egress_mode": "REMOTE",
        "provider_status": 200,
        "duration_ms": 11,
        "results": [dict(item) for item in _SEARCH_RESULTS],
        "count": len(_SEARCH_RESULTS),
    }


def _fetch_records(urls: List[str]) -> List[Dict[str, Any]]:
    return [{
        "url": url,
        "status": "success",
        "title": f"Title {url}",
        "text": f"usable evidence from {url}",
        "final_url": url,
        "fetch_provider": TINYFISH_FETCH_PROVIDER_ID,
        "provider": TINYFISH_FETCH_PROVIDER_ID,
        "duration_ms": 5,
    } for url in urls]


def test_research_consumes_exactly_one_search_and_three_fetches(
    app_client, as_user, monkeypatch,
):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([
        _tool_call_script(
            "call_research", "web_research",
            {"query": "omnilab research", "max_results": 5, "max_fetches": 3},
        ),
        _final_script("synthesized answer"),
    ])
    _install_adapter(monkeypatch, adapter)

    search_calls: List[str] = []
    fetch_calls: List[List[str]] = []

    async def fake_search(self, query, *, max_results=None, location=None, language=None):
        provider_quota.consume_search(SEARCH_LIMITS, 1)
        search_calls.append(query)
        return _search_payload(query)

    async def fake_fetch_many(self, urls):
        provider_quota.consume_fetch(FETCH_LIMITS, len(urls))
        fetch_calls.append(list(urls))
        return _fetch_records(list(urls))

    async def _run():
        await tools_service.ensure_builtin_tools()
        with patch.object(TinyFishSearchProvider, "search", fake_search), \
             patch.object(TinyFishFetchProvider, "fetch_many", fake_fetch_many):
            return await _run_with_sink(
                owner, model_id, project_id,
                enabled_tool_ids=[RESEARCH_TOOL_ID],
                search_provider_id=TINYFISH_SEARCH_PROVIDER_ID,
                fetch_provider_id=TINYFISH_FETCH_PROVIDER_ID,
            )

    run, events = call(app_client, _run)

    assert run["status"] == "success", run.get("error")
    assert len(search_calls) == 1
    assert len(fetch_calls) == 1
    assert fetch_calls[0] == _EXPECTED_SELECTION
    # Exactly 1 search and exactly 3 fetch URL units (never 2 / 6).
    assert _search_row(provider_quota.snapshot(), "minute")["units_consumed"] == 1
    assert _fetch_row(provider_quota.snapshot(), "minute")["units_consumed"] == 3
    assert run["search_call_count"] == 1
    assert run["fetch_call_count"] == 3


# ================================== 7: cache/reuse consumes zero extra quota
def test_reused_search_and_fetch_consume_zero_extra_quota(
    app_client, as_user, monkeypatch,
):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    search_adapter = _StreamAdapter([
        _tool_call_script("s1", "web_search", {"query": "omnilab forex"}),
        _tool_call_script("s2", "web_search", {"query": "omnilab forex"}),
        _final_script("reused final"),
    ])
    _install_adapter(monkeypatch, search_adapter)
    search_calls = {"n": 0}

    async def fake_search(self, query, **kwargs):
        search_calls["n"] += 1
        provider_quota.consume_search(SEARCH_LIMITS, 1)
        return _search_payload(query)

    async def _run_search():
        await tools_service.ensure_builtin_tools()
        with patch.object(TinyFishSearchProvider, "search", fake_search):
            return await _run_with_sink(
                owner, model_id, project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
                search_provider_id=TINYFISH_SEARCH_PROVIDER_ID,
            )

    run, _ = call(app_client, _run_search)
    assert run["status"] == "success", run.get("error")
    assert search_calls["n"] == 1
    assert run["reused_tool_result_count"] >= 1
    assert _search_row(provider_quota.snapshot(), "minute")["units_consumed"] == 1

    provider_quota.reset()

    fetch_adapter = _StreamAdapter([
        _tool_call_script("f1", "web_fetch", {"url": "https://example.com/a"}),
        _tool_call_script("f2", "web_fetch", {"url": "https://example.com/a"}),
        _final_script("reused fetch final"),
    ])
    _install_adapter(monkeypatch, fetch_adapter)
    fetch_calls = {"n": 0}

    async def fake_fetch(self, url):
        fetch_calls["n"] += 1
        provider_quota.consume_fetch(FETCH_LIMITS, 1)
        return {
            "requested_url": url, "final_url": url, "status": 200,
            "text": "body", "provider": TINYFISH_FETCH_PROVIDER_ID,
        }

    async def _run_fetch():
        await tools_service.ensure_builtin_tools()
        with patch.object(TinyFishFetchProvider, "fetch", fake_fetch):
            return await _run_with_sink(
                owner, model_id, project_id, enabled_tool_ids=[FETCH_TOOL_ID],
                fetch_provider_id=TINYFISH_FETCH_PROVIDER_ID,
            )

    run2, _ = call(app_client, _run_fetch)
    assert run2["status"] == "success", run2.get("error")
    assert fetch_calls["n"] == 1
    assert run2["reused_tool_result_count"] >= 1
    assert _fetch_row(provider_quota.snapshot(), "minute")["units_consumed"] == 1


# ============================== 8: Standard Fetch consumes no TinyFish quota
def test_standard_fetch_consumes_no_tinyfish_quota(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([
        _tool_call_script("f1", "web_fetch", {"url": "https://example.com/a"}),
        _final_script("standard fetch final"),
    ])
    _install_adapter(monkeypatch, adapter)

    async def fake_standard_fetch(self, url):
        return {
            "requested_url": url, "final_url": url, "status": 200,
            "text": "local body", "provider": STANDARD_FETCH_PROVIDER_ID,
        }

    async def _run():
        await tools_service.ensure_builtin_tools()
        with patch.object(StandardFetchProvider, "fetch", fake_standard_fetch):
            return await _run_with_sink(
                owner, model_id, project_id, enabled_tool_ids=[FETCH_TOOL_ID],
                fetch_provider_id=STANDARD_FETCH_PROVIDER_ID,
            )

    run, _ = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert _fetch_row(provider_quota.snapshot(), "minute")["units_consumed"] == 0


# ============== 9: PROVIDER_RATE_LIMITED is distinct from TOOL_BUDGET_EXHAUSTED
def test_provider_rate_limited_is_distinct_from_budget(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([
        _tool_call_script("s1", "web_search", {"query": "quota query"}),
        _final_script("final after rate limit"),
    ])
    _install_adapter(monkeypatch, adapter)

    async def fake_search(self, query, **kwargs):
        try:
            provider_quota.consume_search({"minute": 1, "hour": 1}, 2)
        except provider_quota.ProviderRateLimited as exc:
            raise SearchError(
                exc.error.get("message") or "quota exceeded",
                429, "RATE_LIMIT", code="PROVIDER_RATE_LIMITED",
                details=dict(exc.error),
            ) from None
        return _search_payload(query)

    async def _run():
        await tools_service.ensure_builtin_tools()
        with patch.object(TinyFishSearchProvider, "search", fake_search):
            return await _run_with_sink(
                owner, model_id, project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
                search_provider_id=TINYFISH_SEARCH_PROVIDER_ID,
            )

    run, events = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    tool_failures = [e for e in events if e.get("type") == "TOOL_RESULT_FAILED"]
    assert tool_failures, _types(events)
    assert tool_failures[0]["error"]["code"] == "PROVIDER_RATE_LIMITED"
    assert "TOOL_BUDGET_EXHAUSTED" not in json.dumps(events, default=str)
    assert not _budget_rounds(run)


# ================================== 10: no-progress protection still works
def test_no_progress_protection_still_works(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    adapter = _StreamAdapter([
        _tool_call_script("s1", "web_search", {"query": "!!!"}),
        _tool_call_script("s2", "web_search", {"query": "!!!"}),
        _tool_call_script("s3", "web_search", {"query": "!!!"}),
        _final_script("answer from evidence"),
    ])
    _install_adapter(monkeypatch, adapter)

    async def fake_search(self, query, **kwargs):
        return _search_payload(query)

    async def _run():
        with patch.object(
            tools_service, "resolve_enabled_tools",
            new=AsyncMock(return_value=[_search_tool_doc()]),
        ), patch.object(TinyFishSearchProvider, "search", fake_search):
            return await _run_with_sink(
                owner, model_id, project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
                search_provider_id=TINYFISH_SEARCH_PROVIDER_ID, max_tool_rounds=8,
            )

    run, _ = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    assert run["finalization_reason"] == "exact_repeat"
    assert "TOOL_LOOP_NO_PROGRESS" in _messages_blob(adapter)


# ================================== 11: total tool emergency ceiling still works
def test_total_tool_ceiling_still_works(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    monkeypatch.setattr(config, "MAX_TOTAL_TOOL_CALLS_PER_RUN", 1)
    adapter = _StreamAdapter([
        _tool_call_script("s1", "web_search", {"query": "first"}),
        _tool_call_script("s2", "web_search", {"query": "second"}),
        _final_script("final after total budget"),
    ])
    _install_adapter(monkeypatch, adapter)

    async def fake_search(self, query, **kwargs):
        return _search_payload(query)

    async def _run():
        with patch.object(
            tools_service, "resolve_enabled_tools",
            new=AsyncMock(return_value=[_search_tool_doc()]),
        ), patch.object(TinyFishSearchProvider, "search", fake_search):
            return await _run_with_sink(
                owner, model_id, project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
                search_provider_id=TINYFISH_SEARCH_PROVIDER_ID, max_tool_rounds=6,
            )

    run, _ = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    budget_rounds = _budget_rounds(run)
    assert budget_rounds and budget_rounds[-1]["budget"] == "total_budget"
    assert run["finalization_reason"] == "total_budget"
    assert "TOOL_BUDGET_EXHAUSTED" in _messages_blob(adapter)


# ============ 12: V4.2.3.11 finalization succeeds after a long research run
def test_finalization_succeeds_after_long_research_run(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _setup, owner)
    scripts = [
        _tool_call_script(f"r{i}", "web_research", {"query": f"query {i}"})
        for i in range(6)
    ]
    scripts.append(_final_script("long-run forced final answer"))
    adapter = _StreamAdapter(scripts)
    _install_adapter(monkeypatch, adapter)
    counter = {"n": 0}

    run, _ = call(app_client, _drive_research(
        owner, model_id, project_id, adapter, _research_executor(counter),
        max_rounds=7,
    ))

    assert run["status"] == "success", run.get("error")
    assert run["research_call_count"] == 6
    assert (run.get("normalized_response") or "").strip()
