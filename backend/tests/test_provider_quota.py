"""OMNILAB provider-quota tests (offline, deterministic).

Pure in-memory unit tests for ``app.services.provider_quota`` plus adapter
boundary and execution-loop tests proving that concurrent runs share one quota
and that reused tool results consume zero quota.

No DB and no network: the adapter-boundary tests mock ``httpx.AsyncClient`` and
patch the urlguard validators; the execution-loop tests reuse the offline
harness style from ``test_v4234_loop_termination.py``.
"""
from __future__ import annotations

import concurrent.futures
import os
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx
import pytest

import app.services.provider_quota as provider_quota
from app import config
from app import providers as providers_module
from app import services
from app.security import encrypt_secret
from app.services import execution as execution_module
from app.services import fetch_providers
from app.services import search_providers
from app.services.fetch_providers import FetchError, TinyFishFetchProvider
from app.services.search_providers import SearchError, TinyFishSearchProvider
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

SEARCH_LIMITS = {"minute": 30, "hour": 500}
FETCH_LIMITS = {"minute": 150, "day": 1000}


@pytest.fixture(autouse=True)
def _reset_quota():
    provider_quota.reset()
    yield
    provider_quota.reset()


# ------------------------------------------------------------------ helpers
def _search_row(rows, window="minute"):
    return next(
        r for r in rows if r["resource"] == "search" and r["window"] == window
    )


def _fetch_row(rows, window="minute"):
    return next(
        r for r in rows if r["resource"] == "fetch" and r["window"] == window
    )


async def _validate_ok(url, *args, **kwargs):
    return url


class _SearchClient:
    """AsyncClient double serving one valid TinyFish search response."""

    response_json: Dict[str, Any] = {
        "results": [
            {"title": "T", "url": "https://example.com/r", "snippet": "S", "rank": 1}
        ]
    }

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, *, params=None, headers=None, **kwargs):
        return httpx.Response(200, json=type(self).response_json)


class _BatchPostClient:
    """AsyncClient double serving a TinyFish batch fetch response."""

    response_json: Dict[str, Any] = {}
    captured_urls: List[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, headers=None, json=None, **kwargs):
        urls = (json or {}).get("urls") or []
        type(self).captured_urls = list(urls)
        payload = type(self).response_json
        if not payload:
            payload = {
                "results": [
                    {"text": f"body-{i}", "final_url": u, "url": u}
                    for i, u in enumerate(urls)
                ]
            }
        return httpx.Response(200, json=payload)


def _search_doc(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": "prov-search-tinyfish-test",
        "name": "TinyFish Search",
        "provider_type": "tinyfish",
        "enabled": True,
        "configuration": cfg,
        "api_key_enc": encrypt_secret("k"),
        "credential_id": None,
        "credential_owner_id": None,
    }


def _fetch_doc(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": "prov-fetch-tinyfish-test",
        "name": "TinyFish Fetch",
        "provider_type": "tinyfish",
        "enabled": True,
        "configuration": cfg,
        "api_key_enc": encrypt_secret("k"),
        "credential_id": None,
        "credential_owner_id": None,
    }


# =========================================================== pure unit tests
def test_search_minute_allows_1_to_30_then_blocks():
    limits = {"minute": 30, "hour": 500}
    for _ in range(30):
        provider_quota.consume_search(limits, 1, now=1000)
    with pytest.raises(provider_quota.ProviderRateLimited) as exc:
        provider_quota.consume_search(limits, 1, now=1000)
    err = exc.value.error
    assert err["code"] == "PROVIDER_RATE_LIMITED"
    assert err["resource"] == "search"
    assert err["limit"] == 30
    assert err["window_seconds"] == 60
    assert isinstance(err["retry_after_seconds"], int)
    assert err["retry_after_seconds"] >= 1


def test_search_hourly_enforced_independently():
    limits = {"minute": 100000, "hour": 500}
    for _ in range(500):
        provider_quota.consume_search(limits, 1, now=1000)
    with pytest.raises(provider_quota.ProviderRateLimited) as exc:
        provider_quota.consume_search(limits, 1, now=1000)
    err = exc.value.error
    assert err["window_seconds"] == 3600
    assert err["limit"] == 500


def test_rolling_window_resets():
    limits = {"minute": 30, "hour": 500}
    for _ in range(30):
        provider_quota.consume_search(limits, 1, now=1000)
    with pytest.raises(provider_quota.ProviderRateLimited):
        provider_quota.consume_search(limits, 1, now=1000)
    provider_quota.consume_search(limits, 1, now=1061)
    row = _search_row(provider_quota.snapshot(now=1061), "minute")
    assert row["units_consumed"] == 1


def test_fetch_url_units_1_to_150_then_blocks():
    limits = {"minute": 150, "day": 1000}
    for _ in range(150):
        provider_quota.consume_fetch(limits, 1, now=0)
    with pytest.raises(provider_quota.ProviderRateLimited) as exc:
        provider_quota.consume_fetch(limits, 1, now=0)
    err = exc.value.error
    assert err["resource"] == "fetch"
    assert err["limit"] == 150
    assert err["window_seconds"] == 60


def test_fetch_daily_1000_enforced():
    limits = {"minute": 100000, "day": 1000}
    for _ in range(1000):
        provider_quota.consume_fetch(limits, 1, now=0)
    with pytest.raises(provider_quota.ProviderRateLimited) as exc:
        provider_quota.consume_fetch(limits, 1, now=0)
    err = exc.value.error
    assert err["window_seconds"] == 86400
    assert err["limit"] == 1000


def test_batched_consume_counts_each_url():
    limits = {"minute": 150, "day": 1000}
    provider_quota.consume_fetch(limits, units=3, now=0)
    row = _fetch_row(provider_quota.snapshot(now=0), "minute")
    assert row["units_consumed"] == 3
    assert row["window_remaining"] == limits["minute"] - 3


def test_snapshot_reports_safe_telemetry():
    provider_quota.consume_search(SEARCH_LIMITS, 1, now=10)
    provider_quota.consume_fetch(FETCH_LIMITS, 1, now=10)
    rows = provider_quota.snapshot(now=10)
    assert len(rows) == 4
    assert {(r["resource"], r["window"]) for r in rows} == {
        ("search", "minute"),
        ("search", "hour"),
        ("fetch", "minute"),
        ("fetch", "day"),
    }
    expected_keys = {
        "provider",
        "resource",
        "window",
        "window_seconds",
        "units_consumed",
        "window_limit",
        "window_remaining",
        "window_reset_seconds",
    }
    blob = str(rows).lower()
    for row in rows:
        assert set(row.keys()) == expected_keys
        assert row["provider"] == "tinyfish"
    for secret_like in ("api_key", "secret", "token", "password", "encrypted_value"):
        assert secret_like not in blob


def test_concurrent_consume_shares_state_atomically():
    limits = {"minute": 30, "hour": 100000}

    def _one(_index):
        try:
            provider_quota.manager.consume(
                "tinyfish", "search", 1, limits, now=1000.0
            )
            return True
        except provider_quota.ProviderRateLimited:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(_one, range(100)))
    assert sum(outcomes) == 30
    assert outcomes.count(False) == 70
    row = _search_row(provider_quota.snapshot(now=1000.0), "minute")
    assert row["units_consumed"] == 30


def test_two_consumers_share_remaining_quota():
    limits = {"minute": 30, "hour": 500}
    for _ in range(29):
        provider_quota.consume_search(limits, 1, now=1000)
    result = provider_quota.consume_search(limits, 1, now=1000)
    windows = {w["window"]: w for w in result["windows"]}
    assert windows["minute"]["window_remaining"] == 0
    with pytest.raises(provider_quota.ProviderRateLimited):
        provider_quota.consume_search(limits, 1, now=1000)


def test_config_defaults_are_lab_mode():
    env_keys = (
        "MAX_SEARCH_CALLS_PER_RUN",
        "MAX_FETCH_CALLS_PER_RUN",
        "MAX_TOTAL_TOOL_CALLS_PER_RUN",
        "TINYFISH_SEARCH_RPM_LIMIT",
        "TINYFISH_FETCH_URLS_PER_MINUTE_LIMIT",
    )
    if any(os.environ.get(key) is not None for key in env_keys):
        pytest.skip("environment overrides provider/run limit defaults")
    assert config.MAX_SEARCH_CALLS_PER_RUN == 30
    assert config.MAX_FETCH_CALLS_PER_RUN == 100
    assert config.MAX_TOTAL_TOOL_CALLS_PER_RUN == 150
    assert config.TINYFISH_SEARCH_RPM_LIMIT == 30
    assert config.TINYFISH_FETCH_URLS_PER_MINUTE_LIMIT == 150


# ===================================================== adapter boundary tests
def test_search_adapter_consumes_one_unit_and_blocks(app_client, monkeypatch):
    monkeypatch.setattr(
        search_providers, "async_validate_provider_url", _validate_ok
    )
    doc = _search_doc({"search_rpm_limit": 1, "search_hourly_limit": 100})
    provider = TinyFishSearchProvider(doc)
    provider_quota.reset()

    async def _go():
        with patch(
            "app.services.search_providers.httpx.AsyncClient", _SearchClient
        ):
            first = await provider.search("q")
            with pytest.raises(SearchError) as exc:
                await provider.search("q2")
            return first, exc.value

    first, err = call(app_client, _go)
    assert first["count"] == 1
    assert err.code == "PROVIDER_RATE_LIMITED"
    for key in (
        "provider",
        "resource",
        "limit",
        "window_seconds",
        "retry_after_seconds",
    ):
        assert key in err.details
    row = _search_row(provider_quota.snapshot(), "minute")
    assert row["units_consumed"] == 1


def test_fetch_adapter_counts_url_units_batch(app_client, monkeypatch):
    monkeypatch.setattr(
        fetch_providers, "async_validate_provider_url", _validate_ok
    )
    monkeypatch.setattr(
        fetch_providers, "async_validate_remote_target_url", _validate_ok
    )
    urls = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]

    ok_doc = _fetch_doc(
        {"fetch_urls_per_minute_limit": 3, "fetch_urls_per_day_limit": 100}
    )
    provider_quota.reset()

    async def _ok():
        with patch(
            "app.services.fetch_providers.httpx.AsyncClient", _BatchPostClient
        ):
            return await TinyFishFetchProvider(ok_doc).fetch_many(urls)

    records = call(app_client, _ok)
    assert len(records) == 3
    assert all(r["status"] == "success" for r in records)
    row = _fetch_row(provider_quota.snapshot(), "minute")
    assert row["units_consumed"] == 3

    provider_quota.reset()
    small_doc = _fetch_doc(
        {"fetch_urls_per_minute_limit": 2, "fetch_urls_per_day_limit": 100}
    )

    async def _small():
        with patch(
            "app.services.fetch_providers.httpx.AsyncClient", _BatchPostClient
        ):
            with pytest.raises(FetchError) as exc:
                await TinyFishFetchProvider(small_doc).fetch_many(urls)
            return exc.value

    err = call(app_client, _small)
    assert err.code == "PROVIDER_RATE_LIMITED"
    row = _fetch_row(provider_quota.snapshot(), "minute")
    assert row["units_consumed"] == 0


def test_fetch_adapter_single_consumes_one(app_client, monkeypatch):
    monkeypatch.setattr(
        fetch_providers, "async_validate_provider_url", _validate_ok
    )
    monkeypatch.setattr(
        fetch_providers, "async_validate_remote_target_url", _validate_ok
    )
    doc = _fetch_doc(
        {"fetch_urls_per_minute_limit": 10, "fetch_urls_per_day_limit": 100}
    )
    provider_quota.reset()

    async def _go():
        with patch(
            "app.services.fetch_providers.httpx.AsyncClient", _BatchPostClient
        ):
            return await TinyFishFetchProvider(doc).fetch(
                "https://example.com/a"
            )

    out = call(app_client, _go)
    assert out["requested_url"] == "https://example.com/a"
    row = _fetch_row(provider_quota.snapshot(), "minute")
    assert row["units_consumed"] == 1


# ===================================================== execution-loop tests
def _install_adapter(monkeypatch, executor):
    factory = lambda provider: CapableAdapter(executor, None, tools=True)  # noqa: E731
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


async def _model_setup(owner, *, capabilities=None):
    provider_id = await create_provider(owner)
    project_id = await create_project(owner)
    model_id = await create_model(
        owner,
        provider_id=provider_id,
        capabilities=capabilities or {"tools": True},
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
        "provider": "prov-fetch-standard",
        "metadata": {},
    }


def _search_tool_call(call_id, query):
    return {"id": call_id, "name": "web_search", "arguments": {"query": query}}


def _fetch_tool_call(call_id, url):
    return {"id": call_id, "name": "web_fetch", "arguments": {"url": url}}


def _loop_variant_executor(state, prefix="forex news"):
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


def test_reused_search_consumes_zero_quota(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] <= 2:
            return result_with_tools(
                tool_calls=[
                    _search_tool_call(f"s{state['round']}", "omnilab forex")
                ]
            )
        return result_success("final answer")

    _install_adapter(monkeypatch, executor)
    calls = {"n": 0}

    async def fake_search(self, query, **kwargs):
        calls["n"] += 1
        provider_quota.consume_search({"minute": 30, "hour": 500}, 1)
        return _search_results("omnilab forex", ["https://example.com/0"])

    async def _execute():
        with patch.object(
            TinyFishSearchProvider, "search", fake_search
        ):
            return await execution_module.execute_model(
                owner,
                model_id,
                "",
                [{"role": "user", "content": "news"}],
                {},
                project_id=project_id,
                enabled_tool_ids=ALL_TOOLS,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert calls["n"] == 1
    assert run["reused_tool_result_count"] >= 1
    row = _search_row(provider_quota.snapshot(), "minute")
    assert row["units_consumed"] == 1


def test_reused_fetch_consumes_zero_quota(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] <= 2:
            return result_with_tools(
                tool_calls=[
                    _fetch_tool_call(
                        f"f{state['round']}", "https://example.com/a"
                    )
                ]
            )
        return result_success("fetched final")

    _install_adapter(monkeypatch, executor)
    calls = {"n": 0}

    async def fake_fetch(self, url):
        calls["n"] += 1
        provider_quota.consume_fetch({"minute": 150, "day": 1000}, 1)
        return _fetch_result("https://example.com/a")

    async def _execute():
        with patch.object(
            fetch_providers.StandardFetchProvider,
            "fetch",
            fake_fetch,
        ):
            return await execution_module.execute_model(
                owner,
                model_id,
                "",
                [{"role": "user", "content": "fetch it"}],
                {},
                project_id=project_id,
                enabled_tool_ids=ALL_TOOLS,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert calls["n"] == 1
    assert run["reused_tool_result_count"] >= 1
    row = _fetch_row(provider_quota.snapshot(), "minute")
    assert row["units_consumed"] == 1


def test_run_safety_survives_high_ceilings(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    monkeypatch.setattr(config, "MAX_SEARCH_CALLS_PER_RUN", 1000)
    monkeypatch.setattr(config, "MAX_TOTAL_TOOL_CALLS_PER_RUN", 1000)
    state = {"requests": [], "calls": 0}
    _install_adapter(monkeypatch, _loop_variant_executor(state))
    counter = {"n": 0}

    async def fake_search(self, query, **kwargs):
        counter["n"] += 1
        url = f"https://example.com/new-{counter['n']}"
        return _search_results(query, [url])

    async def _execute():
        with patch.object(TinyFishSearchProvider, "search", fake_search):
            return await execution_module.execute_model(
                owner,
                model_id,
                "",
                [{"role": "user", "content": "news"}],
                {},
                project_id=project_id,
                enabled_tool_ids=ALL_TOOLS,
                max_tool_rounds=config.MAX_TOOL_ROUNDS,
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    assert len(state["requests"]) <= config.MAX_TOOL_ROUNDS + 1
    assert run["tool_budget"]["max_search_calls"] == 1000
    assert run.get("forced_finalization") is True
    assert run["finalization_reason"] == "preempt_last_round"
    assert run["search_call_count"] <= config.MAX_TOOL_ROUNDS
