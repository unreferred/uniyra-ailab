"""OMNILAB V4.2.3.4 loop-termination tests (offline, deterministic).

Covers spec section 14: search/fetch dedup reuse, evidence-progress
accounting, per-type budgets, last-round preemption and the single forced
tools-free finalization (with evidence preservation).

Pure loop tests mock ``TinyFishSearchProvider.search`` and
``StandardFetchProvider.fetch`` via ``patch`` (like
``test_v4233_closure.py``), so no provider-registry isolation is needed and
no real network is touched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app import config
from app import providers as providers_module
from app import services
from app.services import execution as execution_module
from app.services.execution import _normalize_fetch_url, _normalize_search_query
from app.services.fetch_providers import STANDARD_FETCH_PROVIDER_ID
from app.services.search_providers import SearchError
from tests.conftest import call

from _support import (
    CapableAdapter,
    create_model,
    create_project,
    create_provider,
    result_success,
    result_with_tools,
)

SEARCH_TOOL_ID = "tool-search-0001"
FETCH_TOOL_ID = "tool-fetch-0001"
CALCULATOR_ID = "tool-calculator-0001"
ALL_TOOLS = [SEARCH_TOOL_ID, FETCH_TOOL_ID, CALCULATOR_ID]


# ------------------------------------------------------------------ helpers
def _install_adapter(monkeypatch, executor):
    factory = lambda provider: CapableAdapter(executor, None, tools=True)  # noqa: E731
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


async def _model_setup(owner, *, capabilities=None):
    provider_id = await create_provider(owner)
    project_id = await create_project(owner)
    model_id = await create_model(
        owner, provider_id=provider_id, capabilities=capabilities or {"tools": True}
    )
    return project_id, model_id


def _search_results(query, urls):
    return {
        "query": query,
        "provider": "tinyfish",
        "results": [
            {
                "title": f"OmniLab Result {i}",
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


def _loop_variant_executor(state, prefix="forex news"):
    """Always request another (distinct) search; answer once tools are gone."""
    def executor(model, req):
        state["requests"].append(req)
        if req.tools is None:
            return result_success("forced final answer")
        n = state["calls"]
        state["calls"] += 1
        return result_with_tools(
            tool_calls=[_search_tool_call(f"loop-{n}", f"{prefix} variant {n}")]
        )
    return executor


# --------------------------------------------------- 1: exact repeat reuse
def test_repeated_same_search_reused(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] <= 2:
            return result_with_tools(
                tool_calls=[_search_tool_call(f"s{state['round']}", "omnilab forex")]
            )
        return result_success("final answer")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = _search_results(
                "omnilab forex", ["https://example.com/0"]
            )
            run = await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "news"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
            )
            return run, mock_search.call_count

    run, search_calls = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert search_calls == 1
    assert run["reused_tool_result_count"] >= 1
    assert any(
        entry.get("reused_result") for entry in run["tool_rounds"]
        if "reused_result" in entry
    )


# -------------------------------------------- 2: normalized-similar reuse
def test_similar_search_same_urls_no_progress(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[_search_tool_call("s1", "Forex News")])
        if state["round"] == 2:
            return result_with_tools(tool_calls=[_search_tool_call("s2", "forex news!!")])
        return result_success("final answer")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = _search_results(
                "forex news", ["https://example.com/0"]
            )
            run = await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "news"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
            )
            return run, mock_search.call_count

    run, search_calls = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert search_calls == 1
    assert run["reused_tool_result_count"] >= 1


# ------------------------------------------------ 3: new urls are progress
def test_new_urls_count_progress(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[_search_tool_call("a", "alpha")])
        if state["round"] == 2:
            return result_with_tools(tool_calls=[_search_tool_call("b", "beta")])
        return result_success("two searches done")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.side_effect = [
                _search_results("alpha", ["https://example.com/alpha-0"]),
                _search_results("beta", ["https://example.com/beta-0"]),
            ]
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "go"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert run["unique_evidence_count"] >= 2
    assert "https://example.com/alpha-0" in run["unique_result_urls"]
    assert "https://example.com/beta-0" in run["unique_result_urls"]


# ---------------------------------------------------- 4: fetch dedup reuse
def test_same_fetch_url_reused(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] <= 2:
            return result_with_tools(
                tool_calls=[_fetch_tool_call(f"f{state['round']}", "https://example.com/a")]
            )
        return result_success("fetched final")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.fetch_providers.StandardFetchProvider.fetch",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = _fetch_result("https://example.com/a")
            run = await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "fetch it"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
            )
            return run, mock_fetch.call_count

    run, fetch_calls = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert fetch_calls == 1
    assert run["reused_tool_result_count"] >= 1


# --------------------------------------- 5: no-progress forced finalization
def test_no_progress_triggers_forced_finalization(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"requests": [], "calls": 0}
    _install_adapter(monkeypatch, _loop_variant_executor(state))

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = _search_results(
                "forex", ["https://example.com/0"]
            )
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "news"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
                max_tool_rounds=8,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    assert run["finalization_reason"] in (
        "consecutive_no_progress",
        "preempt_last_round",
        "search_budget",
        "fetch_budget",
        "total_budget",
        "exact_repeat",
    )


# -------------------------------------------- 6: final call has no tools
def test_finalization_call_has_no_tools(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"requests": [], "calls": 0}
    _install_adapter(monkeypatch, _loop_variant_executor(state))

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = _search_results(
                "forex", ["https://example.com/0"]
            )
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "news"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
                max_tool_rounds=8,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    assert len(state["requests"]) >= 2
    last = state["requests"][-1]
    assert last.tools is None
    assert last.tool_choice is None


# -------------------------------------------- 7: finalization keeps evidence
def test_finalization_preserves_evidence(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"requests": [], "calls": 0}
    _install_adapter(monkeypatch, _loop_variant_executor(state))

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = _search_results(
                "forex", ["https://example.com/0"]
            )
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "news"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
                max_tool_rounds=8,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    last = state["requests"][-1]
    texts = [
        message.get("content", "") for message in last.messages
        if isinstance(message.get("content"), str)
    ]
    blob = "\n".join(texts)
    assert "Gathered evidence summary" in blob
    assert "https://example.com/0" in blob


# ------------------------------------------------ 8: tool_choice never forced
def test_tool_choice_not_forced(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"requests": [], "calls": 0}
    _install_adapter(monkeypatch, _loop_variant_executor(state))

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = _search_results(
                "forex", ["https://example.com/0"]
            )
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "news"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
                max_tool_rounds=8,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert state["requests"]
    for req in state["requests"]:
        assert req.tool_choice in (None, "auto")


# --------------------------------------------- 9: last-round preemption
def test_remaining_round_preemption(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"requests": [], "calls": 0}

    def executor(model, req):
        state["requests"].append(req)
        if req.tools is None:
            return result_success("preempted final")
        n = state["calls"]
        state["calls"] += 1
        return result_with_tools(tool_calls=[_search_tool_call(f"p{n}", f"query-{n}")])

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.side_effect = [
                _search_results("query-0", ["https://example.com/alpha-0"]),
                _search_results("query-1", ["https://example.com/beta-0"]),
            ]
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "go"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
                max_tool_rounds=2,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert run["forced_finalization"] is True
    assert run["finalization_reason"] == "preempt_last_round"


# ------------------------------------------------------- 10: search budget
def test_tool_budget(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    monkeypatch.setattr(config, "MAX_SEARCH_CALLS_PER_RUN", 1)
    state = {"round": 0, "requests": []}

    def executor(model, req):
        state["requests"].append(req)
        if req.tools is None:
            return result_success("budget final answer")
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[_search_tool_call("b1", "alpha")])
        return result_with_tools(tool_calls=[_search_tool_call("b2", "beta")])

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.side_effect = [
                _search_results("alpha", ["https://example.com/alpha-0"]),
                _search_results("beta", ["https://example.com/beta-0"]),
            ]
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "go"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert run["search_call_count"] == 1
    assert run["forced_finalization"] is True
    assert run["finalization_reason"] == "search_budget"
    assert any(
        entry.get("execution_status") == "skipped"
        and entry.get("budget") == "search_budget"
        for entry in run["tool_rounds"]
    )


# ------------------------------------------- 11: genuine search+fetch path
def test_genuine_search_fetch_succeeds(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[_search_tool_call("g1", "alpha")])
        if state["round"] == 2:
            return result_with_tools(
                tool_calls=[_fetch_tool_call("g2", "https://example.com/a")]
            )
        return result_success("combined answer")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search, patch(
            "app.services.fetch_providers.StandardFetchProvider.fetch",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_search.return_value = _search_results(
                "alpha", ["https://example.com/alpha-0"]
            )
            mock_fetch.return_value = _fetch_result("https://example.com/a")
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "go"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert run["search_call_count"] == 1
    assert run["fetch_call_count"] == 1
    assert not (run.get("tool_loop") or {}).get("no_progress")
    assert run["forced_finalization"] is False


# ------------------------------------------------ 12: provider error bounded
def test_provider_error_bounded(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[_search_tool_call("e1", "alpha")])
        return result_success("recovered final")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.side_effect = SearchError("boom", 500, "PROVIDER_ERROR")
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "go"}], {},
                project_id=project_id, enabled_tool_ids=ALL_TOOLS,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert run["tool_failure_count"] >= 1


# --------------------------------------------------- 13: normalize helpers
def test_normalize_helpers():
    assert _normalize_search_query("  Forex NEWS!! ") == "forex news"
    assert (
        _normalize_fetch_url("https://Example.com/a/?x=1#frag")
        == "https://example.com/a?x=1"
    )
