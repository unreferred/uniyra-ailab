"""OMNILAB V4.2.3.14 research-breadth policy tests (offline, deterministic).

This module pins the additive V4.2.3.14 contract for ``web_research`` breadth:

  * research breadth defaults/ceilings (5/5 defaults, hard 10 ceiling) are
    resolved from config / valve policy and are DISTINCT from provider batch
    capacity (``MAX_BATCH_FETCH_URLS`` / fetch ``max_urls``);
  * a project research policy overrides global, cannot exceed the hard max, and
    is snapshot-stable;
  * stale builtin tool docs migrate to the canonical schema (revision stamped)
    exactly once (idempotent);
  * logical breadth is never clamped by provider batch capacity: a batch-capable
    adapter is chunked into sequential ``fetch_many`` calls, while provider quota
    is consumed in URL units (never batch units);
  * TinyFish provider quota exhaustion maps to ``PROVIDER_RATE_LIMITED`` and is
    distinct from ``TOOL_BUDGET_EXHAUSTED``;
  * streaming/operation-sink, telemetry shape, watchdog constants and SSRF
    guardrails remain unchanged.

Everything runs offline: search/fetch adapters are local fakes, httpx and DNS are
mocked, and no model call ever happens.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import config
from app import urlguard
from app.services import builtin_tools
from app.services import fetch_providers
from app.services import provider_quota
from app.services import tool_valves
from app.services import tools as tools_service
from app.services.builtin_tools import BUILTIN_TOOL_DEFINITIONS
from app.services.fetch_providers import (
    STANDARD_FETCH_PROVIDER_ID,
    TINYFISH_FETCH_PROVIDER_ID,
    BaseFetchProvider,
    FetchError,
    StandardFetchProvider,
    TinyFishFetchProvider,
    _quota_exceeded_error,
)
from tests.conftest import call

from _support import create_project

SEARCH_PROVIDER = "prov-search-v42314"
FETCH_PROVIDER = "prov-fetch-v42314"
RESEARCH_TOOL_ID = "tool-research-0001"
SENTINEL_PROBE = "sentinel-probe-v42314-UNIQUE"

# Throwaway collections so no global provider/tool/valve row is ever touched.
_SEARCH_COLL = "search_providers_v42314t"
_FETCH_COLL = "fetch_providers_v42314t"
_VALVE_COLL = "valve_values_v42314t"
_TOOLS_COLL = "tools_v42314t"

PUBLIC_IP = "93.184.216.34"


# --------------------------------------------------------------- isolation
@pytest.fixture(scope="module", autouse=True)
def _isolated_collections(app_client):
    """Redirect provider/tool/valve repositories to throwaway collections."""
    from app import repository as repo_module
    from app.repository import Repository

    real = {
        "search_providers": repo_module.search_providers,
        "fetch_providers": repo_module.fetch_providers,
        "valve_values": repo_module.valve_values,
        "tools": repo_module.tools,
    }
    repo_module.search_providers = Repository(_SEARCH_COLL)
    repo_module.fetch_providers = Repository(_FETCH_COLL)
    repo_module.valve_values = Repository(_VALVE_COLL)
    repo_module.tools = Repository(_TOOLS_COLL)
    yield

    async def _drop():
        from app.database import db

        for name in (_SEARCH_COLL, _FETCH_COLL, _VALVE_COLL, _TOOLS_COLL):
            await db[name].delete_many({})

    app_client.portal.call(_drop)
    repo_module.search_providers = real["search_providers"]
    repo_module.fetch_providers = real["fetch_providers"]
    repo_module.valve_values = real["valve_values"]
    repo_module.tools = real["tools"]


@pytest.fixture(autouse=True)
def _purge_owner_rows(app_client, as_user):
    """Drop throwaway owner rows from the isolated collections after each test."""
    yield

    async def _do():
        from app.database import db

        for name in (_VALVE_COLL, _TOOLS_COLL):
            await db[name].delete_many({"owner_id": as_user["id"]})
        await db["tool_credentials"].delete_many({"owner_id": as_user["id"]})

    app_client.portal.call(_do)


@pytest.fixture(autouse=True)
def _isolated_provider_quota():
    provider_quota.reset()
    yield
    provider_quota.reset()


# ------------------------------------------------------------------- fakes
class _FakeSearchAdapter:
    """Minimal search adapter double (no network, records calls)."""

    def __init__(self, results: List[dict], provider: str = SEARCH_PROVIDER):
        self.id = provider
        self._results = list(results)
        self.calls: List[dict] = []

    async def search(self, query, *, max_results=None, location=None, language=None):
        self.calls.append(
            {
                "query": query,
                "max_results": max_results,
                "location": location,
                "language": language,
            }
        )
        return {
            "query": query,
            "provider": self.id,
            "egress_mode": "REMOTE",
            "duration_ms": 5,
            "results": list(self._results),
            "count": len(self._results),
            "metadata": {"provider_endpoint_host": "api.search.tinyfish.ai"},
        }


class _FakeBatchFetchAdapter:
    """Batch-capable fetch adapter double recording each ``fetch_many`` call."""

    supports_batch_fetch = True

    def __init__(
        self,
        records: Any = None,
        provider: str = FETCH_PROVIDER,
        *,
        batch_cap: Optional[int] = None,
        handler: Any = None,
    ):
        self.id = provider
        self.egress_mode = "REMOTE"
        self._records = records
        self._handler = handler
        self.fetch_many_calls: List[List[str]] = []
        if batch_cap is not None:
            self.batch_cap = batch_cap

    async def fetch_many(self, urls):
        self.fetch_many_calls.append(list(urls))
        if self._handler is not None:
            return self._handler(list(urls))
        return list(self._records or [])

    async def fetch(self, url):  # pragma: no cover - batch path never uses it
        return {"requested_url": url, "final_url": url, "text": "unused"}


class _FakeBatchPostClient:
    """AsyncClient double serving a TinyFish-shaped batch response."""

    captured: Dict[str, Any] = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, headers=None, json=None, **kwargs):
        type(self).captured["url"] = str(url)
        type(self).captured["json"] = json
        urls = (json or {}).get("urls") or []
        return httpx.Response(
            200,
            json={
                "results": [
                    {"text": f"body-{i}", "final_url": u, "url": u}
                    for i, u in enumerate(urls)
                ]
            },
        )


class _PlainFetchProvider(BaseFetchProvider):
    """Concrete subclass exercising the inherited ``batch_cap`` (base behavior)."""

    async def test_connection(self) -> Dict[str, Any]:
        return {"ok": True}

    async def fetch(self, url: str) -> Dict[str, Any]:
        return {"requested_url": url, "status": 200, "text": "plain"}


# ----------------------------------------------------------------- helpers
def _urls(n: int) -> List[str]:
    return [f"https://h{i}.example.com/p" for i in range(n)]


def _search_results(urls: List[str]) -> List[dict]:
    return [
        {"title": f"Result {i}", "url": url, "snippet": f"snippet {i}", "rank": i + 1}
        for i, url in enumerate(urls)
    ]


def _success_record(url: str) -> dict:
    return {
        "url": url,
        "status": "success",
        "title": f"Title {url}",
        "text": "body",
        "final_url": url,
        "provider": FETCH_PROVIDER,
        "fetch_provider": FETCH_PROVIDER,
        "egress_mode": "REMOTE",
        "provider_status": 200,
        "duration_ms": 3,
        "metadata": {
            "egress_mode": "REMOTE",
            "provider_endpoint_host": "api.fetch.tinyfish.ai",
            "provider_status": 200,
        },
    }


def _standard_doc(configuration: Optional[dict] = None) -> Dict[str, Any]:
    return {
        "id": STANDARD_FETCH_PROVIDER_ID,
        "name": "Standard HTTP Fetch",
        "provider_type": "standard",
        "enabled": True,
        "configuration": configuration or {},
    }


def _tinyfish_doc(configuration: Optional[dict] = None) -> Dict[str, Any]:
    return {
        "id": TINYFISH_FETCH_PROVIDER_ID,
        "name": "TinyFish Fetch",
        "provider_type": "tinyfish",
        "enabled": True,
        "configuration": configuration or {},
        "api_key_enc": None,
        "credential_id": None,
        "credential_owner_id": None,
    }


def _research_tool_doc() -> Dict[str, Any]:
    return {
        "id": RESEARCH_TOOL_ID,
        "name": "web_research",
        "display_name": "Web Research",
        "tool_type": "function",
        "enabled": True,
    }


def _run_research(
    app_client,
    arguments: dict,
    *,
    search_adapter: Any,
    fetch_adapter: Any = None,
    context: Optional[dict] = None,
):
    """Invoke ``run_research`` with both provider resolvers mocked (offline)."""
    ctx = {} if context is None else context
    with patch.object(
        builtin_tools,
        "_resolve_search_adapter",
        new=AsyncMock(return_value=search_adapter),
    ), patch.object(
        builtin_tools,
        "_resolve_fetch_adapter",
        new=AsyncMock(return_value=fetch_adapter),
    ):
        return call(app_client, builtin_tools.run_research, arguments, ctx)


def _patch_dns_public(monkeypatch):
    monkeypatch.setattr(urlguard, "_resolve_host_sync", lambda host: [PUBLIC_IP])


def _fetch_window(rows, window="minute"):
    return next(
        r
        for r in rows
        if r["resource"] == "fetch" and r["window"] == window
    )


async def _read_tool_doc(tool_id):
    from app.database import db

    return await db[_TOOLS_COLL].find_one({"id": tool_id}, {"_id": 0})


def _stale_research_doc() -> dict:
    return {
        "id": RESEARCH_TOOL_ID,
        "name": "web_research",
        "owner_id": None,
        "builtin": True,
        "enabled": False,
        "description": "Stale custom description",
        "custom_key": {"preserve": True},
        "builtin_schema_revision": 1,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 5,
                },
                "max_fetches": {
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 5,
                },
            },
            "required": ["query"],
        },
        "created_at": "stale-created",
        "updated_at": "stale-updated",
    }


def _seed_stale_research(app_client):
    async def _do():
        from app.database import db

        await db[_TOOLS_COLL].delete_many({"id": RESEARCH_TOOL_ID})
        await db[_TOOLS_COLL].insert_one(_stale_research_doc())

    call(app_client, _do)


# =================================================================== 1-5 defaults
def test_omitted_max_results_defaults_to_5(app_client):
    urls = _urls(3)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client, {"query": "q"}, search_adapter=search, fetch_adapter=fetch
    )

    assert search.calls[0]["max_results"] == 5
    assert out["research"]["default_max_results"] == 5
    assert out["research"]["requested_max_results"] is None
    assert out["research"]["effective_max_results"] == 5


def test_omitted_max_fetches_defaults_to_5(app_client):
    urls = _urls(7)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client, {"query": "q"}, search_adapter=search, fetch_adapter=fetch
    )

    assert out["research"]["default_max_fetches"] == 5
    assert out["research"]["effective_max_fetches"] == 5
    assert out["research"]["fetch_urls_selected"] == 5


def test_explicit_max_fetches_2(app_client):
    urls = _urls(6)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 2},
        search_adapter=search,
        fetch_adapter=fetch,
    )

    assert out["research"]["effective_max_fetches"] == 2
    assert out["research"]["fetch_urls_selected"] == 2
    assert len(fetch.fetch_many_calls[0]) == 2


def test_explicit_max_fetches_5(app_client):
    urls = _urls(6)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 5},
        search_adapter=search,
        fetch_adapter=fetch,
    )

    assert out["research"]["effective_max_fetches"] == 5
    assert out["research"]["fetch_urls_selected"] == 5


def test_explicit_max_fetches_10_allowed(app_client, monkeypatch):
    monkeypatch.setattr(config, "MAX_BATCH_FETCH_URLS", 10)
    urls = _urls(10)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter(
        [_success_record(u) for u in urls], batch_cap=10
    )

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 10},
        search_adapter=search,
        fetch_adapter=fetch,
    )

    assert out["research"]["effective_max_fetches"] == 10
    assert out["research"]["fetch_urls_selected"] == 10
    assert out["research"]["provider_batch_cap"] == 10
    assert out["research"]["provider_batch_count"] == 1


def test_over_canonical_max_normalized_or_rejected():
    tool = next(t for t in BUILTIN_TOOL_DEFINITIONS if t["id"] == RESEARCH_TOOL_ID)
    props = tool["input_schema"]["properties"]

    # Canonical schema: default 5, hard maximum 10 for both dimensions.
    assert props["max_results"]["default"] == 5
    assert props["max_results"]["maximum"] == 10
    assert props["max_fetches"]["default"] == 5
    assert props["max_fetches"]["maximum"] == 10

    errors = tools_service.validate_tool_input(
        tool, {"query": "q", "max_results": 11, "max_fetches": 11}
    )
    assert errors
    assert any("max_results" in error for error in errors)
    assert any("max_fetches" in error for error in errors)

    assert (
        tools_service.validate_tool_input(
            tool, {"query": "q", "max_results": 10, "max_fetches": 10}
        )
        == []
    )


# ============================================================= 7-9 research config
def test_project_default_overrides_global(app_client, as_user):
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    tool = _research_tool_doc()

    call(
        app_client,
        tool_valves.save_tool,
        owner,
        tool,
        {
            "values": {
                "research_default_max_results": 6,
                "research_default_max_fetches": 4,
                "research_max_results_ceiling": 8,
                "research_max_fetches_ceiling": 8,
            }
        },
        None,
    )
    call(
        app_client,
        tool_valves.save_tool,
        owner,
        tool,
        {
            "values": {
                "research_default_max_results": 7,
                "research_default_max_fetches": 3,
                "research_max_results_ceiling": 9,
                "research_max_fetches_ceiling": 9,
            }
        },
        project_id,
    )

    policy = call(app_client, tool_valves.resolve_research_policy, owner, project_id)
    assert policy["source"] == "PROJECT"
    assert policy["default_max_results"] == 7
    assert policy["default_max_fetches"] == 3
    assert policy["max_results_ceiling"] == 9
    assert policy["max_fetches_ceiling"] == 9


def test_project_ceiling_cannot_exceed_hard_max(app_client, as_user):
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    tool = _research_tool_doc()

    with pytest.raises(tool_valves.ValveError):
        call(
            app_client,
            tool_valves.save_tool,
            owner,
            tool,
            {"values": {"research_max_fetches_ceiling": 99}},
            project_id,
        )

    call(
        app_client,
        tool_valves.save_tool,
        owner,
        tool,
        {"values": {"research_max_fetches_ceiling": 8}},
        project_id,
    )
    policy = call(app_client, tool_valves.resolve_research_policy, owner, project_id)
    assert policy["source"] == "PROJECT"
    assert policy["max_fetches_ceiling"] == 8

    # The hard server bound is unchanged by the rejected write.
    assert config.RESEARCH_MAX_FETCHES_CEILING == 10


def test_run_snapshot_preserves_effective_policy(app_client, as_user):
    owner = as_user["id"]
    project_id = call(app_client, create_project, owner)
    tool = _research_tool_doc()

    call(
        app_client,
        tool_valves.save_tool,
        owner,
        tool,
        {
            "values": {
                "research_default_max_results": 6,
                "research_default_max_fetches": 6,
                "research_max_results_ceiling": 10,
                "research_max_fetches_ceiling": 10,
            }
        },
        None,
    )
    call(
        app_client,
        tool_valves.save_tool,
        owner,
        tool,
        {"values": {"research_default_max_fetches": 4}},
        project_id,
    )

    policy = call(app_client, tool_valves.resolve_research_policy, owner, project_id)
    snapshot = call(
        app_client,
        tool_valves.effective_tool_config_snapshot,
        owner,
        project_id,
        search_enabled=False,
        fetch_enabled=False,
        search_provider_id=None,
        fetch_provider_id=None,
    )

    assert "research" in snapshot
    assert snapshot["research"] == policy

    # Deterministic: resolving twice yields the identical policy dict.
    assert (
        call(app_client, tool_valves.resolve_research_policy, owner, project_id)
        == policy
    )


# ====================================================== 10-11 builtin migration
def test_stale_builtin_schema_migrates_from_3_5(app_client):
    _seed_stale_research(app_client)
    call(app_client, tools_service.ensure_builtin_tools)

    doc = call(app_client, _read_tool_doc, RESEARCH_TOOL_ID)
    props = doc["input_schema"]["properties"]
    assert props["max_results"]["default"] == 5
    assert props["max_results"]["maximum"] == 10
    assert props["max_fetches"]["default"] == 5
    assert props["max_fetches"]["maximum"] == 10
    assert doc["builtin_schema_revision"] == tools_service.BUILTIN_SCHEMA_REVISION

    # Non-canonical user state is preserved.
    assert doc["enabled"] is False
    assert doc["description"] == "Stale custom description"
    assert doc["custom_key"] == {"preserve": True}
    assert doc["created_at"] == "stale-created"


def test_builtin_migration_is_idempotent(app_client):
    _seed_stale_research(app_client)
    call(app_client, tools_service.ensure_builtin_tools)
    first = call(app_client, _read_tool_doc, RESEARCH_TOOL_ID)

    call(app_client, tools_service.ensure_builtin_tools)
    second = call(app_client, _read_tool_doc, RESEARCH_TOOL_ID)

    assert second["updated_at"] == first["updated_at"]
    assert second["input_schema"] == first["input_schema"]
    assert second["builtin_schema_revision"] == tools_service.BUILTIN_SCHEMA_REVISION


# ========================================================= 12-16 breadth/batching
def test_logical_breadth_10_with_batch_cap_5_uses_two_provider_batches(app_client):
    urls = _urls(10)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter(
        [_success_record(u) for u in urls], batch_cap=5
    )

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 10},
        search_adapter=search,
        fetch_adapter=fetch,
    )

    assert [len(call_urls) for call_urls in fetch.fetch_many_calls] == [5, 5]
    assert out["research"]["provider_batch_cap"] == 5
    assert out["research"]["provider_batch_count"] == 2
    assert out["research"]["fetch_urls_selected"] == 10


def test_quota_consumption_is_url_units_not_batches(app_client):
    urls = _urls(10)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter(
        [_success_record(u) for u in urls], batch_cap=5
    )

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 10},
        search_adapter=search,
        fetch_adapter=fetch,
    )

    # Ten URL units observed even though the provider executes two batches.
    assert out["research"]["fetch_urls_attempted"] == 10
    assert out["internal_budget"]["fetch_calls"] == 10
    assert out["research"]["provider_batch_count"] == 2


def test_partial_batch_failure_preserves_partial_success(app_client):
    urls = _urls(10)
    search = _FakeSearchAdapter(_search_results(urls))
    state = {"n": 0}

    def _handler(chunk_urls):
        state["n"] += 1
        if state["n"] == 1:
            raise FetchError("chunk transport down", 502, "NETWORK_ERROR", code="NETWORK_ERROR")
        return [_success_record(u) for u in chunk_urls]

    fetch = _FakeBatchFetchAdapter(batch_cap=5, handler=_handler)

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 10},
        search_adapter=search,
        fetch_adapter=fetch,
    )

    assert out["outcome"] == "PARTIAL_SUCCESS"
    assert out["research"]["fetch_urls_attempted"] == 10
    assert out["research"]["fetch_urls_succeeded"] == 5
    assert out["research"]["fetch_urls_failed"] == 5
    assert out["summary"]["fetched"] == 5
    assert out["summary"]["failed"] == 5
    assert out["research"]["provider_batch_count"] == 2


def test_fewer_candidates_than_requested_reduces_effective_fetches(app_client):
    urls = _urls(2)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 5},
        search_adapter=search,
        fetch_adapter=fetch,
    )

    assert out["research"]["effective_max_fetches"] == 2
    assert out["research"]["fetch_urls_selected"] == 2


def test_remaining_run_budget_reduces_effective_breadth(app_client):
    urls = _urls(5)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 5},
        search_adapter=search,
        fetch_adapter=fetch,
        context={"research_budget": {"search_remaining": 5, "fetch_remaining": 1}},
    )

    assert out["research"]["fetch_urls_selected"] == 1
    assert out["research"]["remaining_fetch_budget"] == 1
    assert len(fetch.fetch_many_calls[0]) == 1


# ============================================================ 17-19 provider config
def test_standard_fetch_provider_honors_batch_config():
    provider = StandardFetchProvider(_standard_doc({"max_urls": 2}))
    assert provider.batch_cap == 2


def test_tinyfish_fetch_provider_honors_batch_config():
    provider = TinyFishFetchProvider(_tinyfish_doc({"max_urls": 3}))
    assert provider.batch_cap == 3


def test_provider_batch_config_cannot_bypass_ceiling(monkeypatch):
    monkeypatch.setattr(config, "MAX_BATCH_FETCH_URLS", 2)
    cfg = {"max_urls": 100}
    assert _PlainFetchProvider(_standard_doc(cfg)).batch_cap == 2
    assert StandardFetchProvider(_standard_doc(cfg)).batch_cap == 2
    assert TinyFishFetchProvider(_tinyfish_doc(cfg)).batch_cap == 2


# ================================================================== 20-23 quota
def test_ten_tinyfish_urls_consume_ten_quota_units(app_client, monkeypatch):
    monkeypatch.setattr(fetch_providers.config, "MAX_BATCH_FETCH_URLS", 10)
    _patch_dns_public(monkeypatch)
    provider = TinyFishFetchProvider(_tinyfish_doc())
    urls = _urls(10)

    async def _go():
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "test-key"}):
            with patch(
                "app.services.fetch_providers.httpx.AsyncClient", _FakeBatchPostClient
            ):
                return await provider.fetch_many(urls)

    records = call(app_client, _go)
    assert len(records) == 10
    assert all(record["status"] == "success" for record in records)
    assert _fetch_window(provider_quota.snapshot(), "minute")["units_consumed"] == 10
    provider_quota.reset()


def test_quota_cache_reuse_consumes_zero(app_client):
    provider_quota.reset()
    assert _fetch_window(provider_quota.snapshot(), "minute")["units_consumed"] == 0

    # A cached/reused result never reaches the adapter boundary, so it consumes
    # no provider quota. Running the same research twice therefore keeps the
    # fetch window at zero units.
    urls = _urls(3)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    _run_research(app_client, {"query": "q"}, search_adapter=search, fetch_adapter=fetch)
    _run_research(app_client, {"query": "q"}, search_adapter=search, fetch_adapter=fetch)

    assert _fetch_window(provider_quota.snapshot(), "minute")["units_consumed"] == 0


def test_quota_exhaustion_returns_provider_rate_limited():
    provider_quota.reset()
    with pytest.raises(provider_quota.ProviderRateLimited) as excinfo:
        provider_quota.consume_fetch({"minute": 1, "day": 1000}, 2)

    assert excinfo.value.error["code"] == "PROVIDER_RATE_LIMITED"
    mapped = _quota_exceeded_error(excinfo.value)
    assert isinstance(mapped, FetchError)
    assert mapped.code == "PROVIDER_RATE_LIMITED"


def test_provider_rate_limited_distinct_from_tool_budget_exhausted():
    provider_quota.reset()
    with pytest.raises(provider_quota.ProviderRateLimited) as excinfo:
        provider_quota.consume_fetch({"minute": 1, "day": 1000}, 2)

    error = excinfo.value.error
    mapped = _quota_exceeded_error(excinfo.value)

    assert error["code"] == "PROVIDER_RATE_LIMITED"
    assert mapped.code != "TOOL_BUDGET_EXHAUSTED"
    assert "TOOL_BUDGET_EXHAUSTED" not in json.dumps(error, default=str)
    assert "TOOL_BUDGET_EXHAUSTED" not in json.dumps(mapped.details, default=str)
    provider_quota.reset()


# ================================================= 24-25 streaming/telemetry guards
def test_search_and_fetch_operations_still_stream(app_client):
    urls = _urls(2)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])
    events: List[dict] = []

    async def sink(op):
        events.append(op)

    out = _run_research(
        app_client,
        {"query": "q", "max_fetches": 2},
        search_adapter=search,
        fetch_adapter=fetch,
        context={"operation_sink": sink},
    )

    assert [(event["op"], event["kind"]) for event in events] == [
        ("start", "search"),
        ("complete", "search"),
        ("start", "fetch"),
        ("start", "fetch"),
        ("complete", "fetch"),
        ("complete", "fetch"),
    ]
    assert events[1]["status"] == "success"
    assert all(event["status"] == "success" for event in events[4:])
    assert out["internal_operations"][0]["type"] == "SEARCH_OPERATION"


RESEARCH_TELEMETRY_KEYS = {
    "requested_max_results",
    "requested_max_fetches",
    "default_max_results",
    "default_max_fetches",
    "configured_max_results",
    "configured_max_fetches",
    "effective_max_results",
    "effective_max_fetches",
    "candidate_results_returned",
    "fetch_urls_selected",
    "fetch_urls_attempted",
    "fetch_urls_succeeded",
    "fetch_urls_failed",
    "fetch_provider",
    "provider_batch_cap",
    "provider_batch_count",
    "provider_quota_units_consumed",
    "remaining_search_budget",
    "remaining_fetch_budget",
    "research_config_source",
}


def test_research_telemetry_shape(app_client):
    urls = _urls(3)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client, {"query": "q"}, search_adapter=search, fetch_adapter=fetch
    )

    telemetry = out["research"]
    assert set(telemetry) == RESEARCH_TELEMETRY_KEYS

    # The top-level policy source must be the resolved enum string, never a
    # fetch-source dict rebound by the fetch loop.
    assert telemetry["research_config_source"] in {
        "GLOBAL",
        "PROJECT",
        "REQUEST_DEFAULT",
    }
    assert telemetry["research_config_source"] == "REQUEST_DEFAULT"

    project_out = _run_research(
        app_client,
        {"query": "q"},
        search_adapter=search,
        fetch_adapter=fetch,
        context={"research_policy": {"source": "PROJECT"}},
    )
    assert project_out["research"]["research_config_source"] == "PROJECT"

    for key in telemetry:
        assert not any(
            token in key.lower()
            for token in ("secret", "token", "password", "api_key", "credential")
        )
    assert SENTINEL_PROBE not in json.dumps(out, default=str)


# ==================================================== 26 watchdog/finalization
def test_watchdog_and_finalization_constants_unchanged():
    for name in (
        "MODEL_CONNECTION_TIMEOUT_SECONDS",
        "MODEL_IDLE_TIMEOUT_SECONDS",
        "MODEL_CALL_MAX_DURATION_SECONDS",
        "MAX_TOOL_ROUNDS",
    ):
        value = getattr(config, name, None)
        assert value is not None, f"{name} missing"
        assert value > 0, f"{name} must be positive (got {value})"


# =========================================================== 27-28 security guards
def test_ssrf_remote_egress_still_enforced(app_client, monkeypatch):
    assert TinyFishFetchProvider(_tinyfish_doc()).egress_mode == "REMOTE"
    assert StandardFetchProvider(_standard_doc()).egress_mode == "LOCAL"

    _patch_dns_public(monkeypatch)
    provider = TinyFishFetchProvider(_tinyfish_doc())
    local = "http://127.0.0.1/admin"
    good = "https://example.com/ok"

    async def _go():
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "test-key"}):
            with patch(
                "app.services.fetch_providers.httpx.AsyncClient", _FakeBatchPostClient
            ):
                return await provider.fetch_many([local, good])

    records = call(app_client, _go)
    assert records[0]["url"] == local
    assert records[0]["status"] == "failed"
    assert records[0]["error"]["code"] == "SSRF_BLOCKED"
    assert records[1]["url"] == good
    assert records[1]["status"] == "success"


def test_no_secret_in_research_output(app_client, monkeypatch):
    monkeypatch.setenv("TINYFISH_API_KEY", SENTINEL_PROBE)
    urls = _urls(2)
    search = _FakeSearchAdapter(_search_results(urls))
    fetch = _FakeBatchFetchAdapter([_success_record(u) for u in urls])

    out = _run_research(
        app_client, {"query": "q"}, search_adapter=search, fetch_adapter=fetch
    )

    blob = json.dumps(out, default=str)
    assert SENTINEL_PROBE not in blob
    assert "api_key" not in blob
