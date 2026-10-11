"""OMNILAB V4.2.3.3 backend closure tests (offline, deterministic).

Covers:
  B. TinyFish request contract + structured error normalization (search + fetch)
     reaching ToolResult without leaking secrets.
  E. Project-scoped provider override + project vault secret precedence at
     execution; fail-closed on a revoked project credential.
  C. Tool-loop continuation, no-progress detection/bounded retry, global round
     cap and round observability.
  D. Code Interpreter valve runtime wiring (ceiling clamps).
  F. Immutable effective config snapshot written once onto the Run.

Search/fetch provider docs are GLOBAL, so this module swaps
``repo.search_providers`` / ``repo.fetch_providers`` / ``repo.valve_values`` for
throwaway collections (as test_v4232_valves.py does). The real global registry
docs are never touched.
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import config
from app import providers as providers_module
from app import repository as repo
from app import security
from app import services
from app.services import builtin_tools
from app.services import code_interpreter as ci
from app.services import execution as execution_module
from app.services import tool_valves
from app.services import tools as tools_service
from app.services import valves
from app.services.fetch_providers import (
    STANDARD_FETCH_PROVIDER_ID,
    TINYFISH_FETCH_PROVIDER_ID,
    FetchProviderRegistry,
    TinyFishFetchProvider,
)
from app.services.search_providers import (
    TINYFISH_SEARCH_PROVIDER_ID,
    SearchError,
    SearchProviderRegistry,
    TinyFishSearchProvider,
)
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
CI_TOOL_ID = "tool-code-interpreter-0001"
CANONICAL_BASE = "https://api.search.tinyfish.ai"

_TEST_SEARCH_COLL = "search_providers_v4233t"
_TEST_FETCH_COLL = "fetch_providers_v4233t"
_TEST_VALVE_COLL = "valve_values_v4233t"


# ------------------------------------------------------------------ isolation
@pytest.fixture(scope="module", autouse=True)
def _isolated_provider_collections(app_client):
    from app import repository as repo_module
    from app.repository import Repository

    real_search = repo_module.search_providers
    real_fetch = repo_module.fetch_providers
    real_valves = repo_module.valve_values
    repo_module.search_providers = Repository(_TEST_SEARCH_COLL)
    repo_module.fetch_providers = Repository(_TEST_FETCH_COLL)
    repo_module.valve_values = Repository(_TEST_VALVE_COLL)
    yield

    async def _drop():
        from app.database import db
        await db[_TEST_SEARCH_COLL].delete_many({})
        await db[_TEST_FETCH_COLL].delete_many({})
        await db[_TEST_VALVE_COLL].delete_many({})

    app_client.portal.call(_drop)
    repo_module.search_providers = real_search
    repo_module.fetch_providers = real_fetch
    repo_module.valve_values = real_valves


@pytest.fixture(autouse=True)
def _purge_owner_rows(app_client, as_user):
    yield

    async def _do():
        from app.database import db
        await db["tool_credentials"].delete_many({"owner_id": as_user["id"]})
        await db[_TEST_VALVE_COLL].delete_many({"owner_id": as_user["id"]})

    app_client.portal.call(_do)


@pytest.fixture(autouse=True)
def _reset_registry(app_client):
    """Reset the throwaway global provider docs to a clean, keyless state."""
    async def _do():
        await SearchProviderRegistry.ensure_defaults()
        await repo.search_providers.update_global(
            TINYFISH_SEARCH_PROVIDER_ID,
            {
                "api_key_enc": None,
                "credential_id": None,
                "credential_owner_id": None,
                "enabled": True,
                "is_default": True,
                "configuration": {"base_url": CANONICAL_BASE},
                "health_status": None,
            },
        )
        await FetchProviderRegistry.ensure_defaults()
        await repo.fetch_providers.update_global(
            TINYFISH_FETCH_PROVIDER_ID,
            {
                "api_key_enc": None,
                "credential_id": None,
                "credential_owner_id": None,
                "enabled": True,
                "configuration": {"base_url": "https://api.fetch.tinyfish.ai"},
            },
        )

    call(app_client, _do)
    yield


# ------------------------------------------------------------- fake transport
class _CapturingTransport:
    """Catch-all httpx.AsyncClient double that records the last request."""

    last: dict = {}
    response: httpx.Response = httpx.Response(200, json={"results": []})

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None):
        type(self).last = {
            "method": "GET", "url": url, "params": params, "headers": headers,
        }
        return type(self).response

    async def post(self, url, params=None, headers=None, json=None):
        type(self).last = {
            "method": "POST", "url": url, "params": params, "headers": headers,
            "json": json,
        }
        return type(self).response


@pytest.fixture(autouse=True)
def _reset_transport():
    _CapturingTransport.last = {}
    _CapturingTransport.response = httpx.Response(200, json={"results": []})
    yield


# ------------------------------------------------------------------ helpers
def _canonical_search_results(query="omnilab", count=1):
    return {
        "query": query,
        "provider": TINYFISH_SEARCH_PROVIDER_ID,
        "results": [
            {
                "title": f"OmniLab Result {i}",
                "url": f"https://example.com/{i}",
                "snippet": f"Snippet {i}",
                "rank": i + 1,
                "provider_metadata": {"source": "tinyfish", "site_name": "example"},
            }
            for i in range(count)
        ],
        "count": count,
    }


def _search_adapter_with_key(key):
    doc = {
        "id": TINYFISH_SEARCH_PROVIDER_ID,
        "name": "TinyFish Search",
        "provider_type": "tinyfish",
        "enabled": True,
        "configuration": {"base_url": CANONICAL_BASE},
        "api_key_enc": security.encrypt_secret(key),
        "credential_id": None,
        "credential_owner_id": None,
        "is_default": True,
    }
    return TinyFishSearchProvider(doc)


def _install_adapter(monkeypatch, executor, *, tools=True):
    factory = lambda provider: CapableAdapter(executor, None, tools=tools)  # noqa: E731
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


async def _set_registry_search_key(owner, key):
    await SearchProviderRegistry.ensure_defaults()
    await repo.search_providers.update_global(
        TINYFISH_SEARCH_PROVIDER_ID,
        {
            "api_key_enc": security.encrypt_secret(key),
            "credential_id": None,
            "credential_owner_id": None,
            "enabled": True,
            "is_default": True,
            "configuration": {"base_url": CANONICAL_BASE},
        },
    )


async def _save_provider_values(owner, kind, provider_id, values, project_id=None):
    await tool_valves.apply_provider_values(owner, kind, provider_id, values, project_id)


# =========================================================== B: request contract
def test_tinyfish_search_request_contract(app_client, as_user):
    key = "contract-key-UNIQUE-b1"
    adapter = _search_adapter_with_key(key)

    async def _run():
        _CapturingTransport.response = httpx.Response(200, json={"results": []})
        with patch(
            "app.services.search_providers.async_validate_provider_url",
            return_value=CANONICAL_BASE,
        ):
            with patch("httpx.AsyncClient", _CapturingTransport):
                return await adapter.search("  spaced query  ")

    call(app_client, _run)
    captured = _CapturingTransport.last
    assert captured["method"] == "GET"
    assert captured["url"] == CANONICAL_BASE
    assert captured["params"]["query"] == "spaced query"
    assert captured["headers"]["X-API-Key"] == key
    assert any(h.lower() == "accept" for h in captured["headers"])


def test_tinyfish_empty_key_is_not_configured(app_client, as_user):
    doc = {
        "id": TINYFISH_SEARCH_PROVIDER_ID,
        "provider_type": "tinyfish",
        "configuration": {"base_url": CANONICAL_BASE},
        "api_key_enc": security.encrypt_secret("   "),
    }
    with patch.dict(os.environ, {"TINYFISH_API_KEY": ""}, clear=False):
        assert call(app_client, TinyFishSearchProvider(doc)._resolve_api_key) is None


def test_tinyfish_http_400_normalization(app_client, as_user):
    key = "err-key-UNIQUE-b2"
    adapter = _search_adapter_with_key(key)

    async def _direct():
        _CapturingTransport.response = httpx.Response(
            400, json={"error": {"code": "INVALID_INPUT", "message": "query is invalid"}}
        )
        with patch(
            "app.services.search_providers.async_validate_provider_url",
            return_value=CANONICAL_BASE,
        ):
            with patch("httpx.AsyncClient", _CapturingTransport):
                with pytest.raises(SearchError) as excinfo:
                    await adapter.search("q")
        return excinfo.value

    exc = call(app_client, _direct)
    assert exc.code == "SEARCH_PROVIDER_ERROR"
    assert exc.provider == "tinyfish"
    assert exc.upstream_status == 400
    assert "query is invalid" in exc.message

    # Through execute_tool the structured detail reaches ToolResult.
    call(app_client, _set_registry_search_key, as_user["id"], key)
    search_tool = next(
        t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS if t["id"] == SEARCH_TOOL_ID
    )

    async def _via_tool():
        _CapturingTransport.response = httpx.Response(
            400, json={"error": {"code": "INVALID_INPUT", "message": "query is invalid"}}
        )
        with patch(
            "app.services.search_providers.async_validate_provider_url",
            return_value=CANONICAL_BASE,
        ):
            with patch("httpx.AsyncClient", _CapturingTransport):
                return await tools_service.execute_tool(
                    search_tool,
                    {"query": "q"},
                    owner_id=as_user["id"],
                    enabled_tool_ids=[SEARCH_TOOL_ID],
                )

    doc = call(app_client, _via_tool)
    assert doc["execution_status"] == "failed"
    assert doc["error"]["code"] == "SEARCH_PROVIDER_ERROR"
    assert doc["error"]["upstream_status"] == 400
    assert doc["error"]["provider"] == "tinyfish"
    assert key not in json.dumps(doc)

    # 401 -> SEARCH_AUTH_ERROR.
    async def _via_tool_401():
        _CapturingTransport.response = httpx.Response(
            401, json={"error": {"code": "UNAUTHORIZED", "message": "bad key"}}
        )
        with patch(
            "app.services.search_providers.async_validate_provider_url",
            return_value=CANONICAL_BASE,
        ):
            with patch("httpx.AsyncClient", _CapturingTransport):
                return await tools_service.execute_tool(
                    search_tool,
                    {"query": "q"},
                    owner_id=as_user["id"],
                    enabled_tool_ids=[SEARCH_TOOL_ID],
                )

    doc401 = call(app_client, _via_tool_401)
    assert doc401["error"]["code"] == "SEARCH_AUTH_ERROR"
    assert doc401["error"]["upstream_status"] == 401


def test_fetch_request_contract_and_error_normalization(app_client, as_user):
    key = "fetch-key-UNIQUE-b3"
    tiny = TinyFishFetchProvider(
        {
            "id": TINYFISH_FETCH_PROVIDER_ID,
            "provider_type": "tinyfish",
            "configuration": {"base_url": "https://api.fetch.tinyfish.ai"},
            "api_key_enc": security.encrypt_secret(key),
        }
    )

    async def _ok():
        _CapturingTransport.response = httpx.Response(
            200, json={"results": [{"text": "hello world", "format": "text/markdown"}]}
        )
        with patch(
            "app.services.fetch_providers.async_validate_provider_url",
            return_value="https://example.com/x",
        ):
            with patch("httpx.AsyncClient", _CapturingTransport):
                return await tiny.fetch("https://example.com/x")

    out = call(app_client, _ok)
    assert out["text"] == "hello world"
    assert _CapturingTransport.last["method"] == "POST"
    assert _CapturingTransport.last["headers"]["X-API-Key"] == key

    async def _fail():
        _CapturingTransport.response = httpx.Response(
            429, json={"error": {"code": "RATE_LIMITED", "message": "slow down"}}
        )
        with patch(
            "app.services.fetch_providers.async_validate_provider_url",
            return_value="https://example.com/x",
        ):
            with patch("httpx.AsyncClient", _CapturingTransport):
                with pytest.raises(Exception) as excinfo:
                    await tiny.fetch("https://example.com/x")
        return excinfo.value

    exc = call(app_client, _fail)
    assert getattr(exc, "code", None) == "FETCH_RATE_LIMITED"
    assert getattr(exc, "upstream_status", None) == 429
    assert key not in str(exc)


# ================================================= C: loop continuation / bounds
def test_tool_result_returned_to_model_continuation(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0, "requests": []}

    def executor(model, req):
        state["requests"].append(req)
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[{
                "id": "call-ws-1",
                "name": "web_search",
                "arguments": {"query": "omnilab"},
            }])
        return result_success("Final answer from search")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = _canonical_search_results("omnilab")
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "search omnilab"}], {},
                project_id=project_id,
                enabled_tool_ids=[SEARCH_TOOL_ID],
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")
    msgs = state["requests"][1].messages
    assistant = [m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")]
    assert assistant, "assistant tool_calls message missing"
    call_id = assistant[0]["tool_calls"][0]["id"]
    assert call_id == "call-ws-1"
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == call_id
    assert "OmniLab Result 0" in tool_msgs[0]["content"]


def test_search_then_final_answer_succeeds(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[{
                "id": "s1", "name": "web_search", "arguments": {"query": "x"},
            }])
        return result_success("done")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = _canonical_search_results("x")
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "x"}], {},
                project_id=project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
            )

    run = call(app_client, _execute)
    assert run["status"] == "success"
    assert run.get("error") is None


def test_search_then_fetch_not_deduplicated(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[{
                "id": "s1", "name": "web_search", "arguments": {"query": "alpha"},
            }])
        if state["round"] == 2:
            return result_with_tools(tool_calls=[{
                "id": "f1", "name": "web_fetch", "arguments": {"url": "https://example.com/a"},
            }])
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
            mock_search.return_value = _canonical_search_results("alpha")
            mock_fetch.return_value = {
                "requested_url": "https://example.com/a",
                "final_url": "https://example.com/a",
                "status": 200,
                "content_type": "text/plain",
                "title": None,
                "text": "fetched text",
                "provider": STANDARD_FETCH_PROVIDER_ID,
                "metadata": {},
            }
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "go"}], {},
                project_id=project_id,
                enabled_tool_ids=[SEARCH_TOOL_ID, FETCH_TOOL_ID],
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")

    async def _calls():
        return await tools_service.list_tool_calls(owner, run_id=run["id"])

    calls = call(app_client, _calls)
    assert len(calls) == 2
    assert {c["tool_name"] for c in calls} == {"web_search", "web_fetch"}


def test_repeated_identical_successful_call_is_bounded(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)

    def executor(model, req):
        return result_with_tools(tool_calls=[{
            "id": "same", "name": "calculator", "arguments": {"expression": "2+2"},
        }])

    _install_adapter(monkeypatch, executor)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "loop"}], {},
            project_id=project_id, enabled_tool_ids=[CALCULATOR_ID],
        )

    run = call(app_client, _execute)
    assert run["status"] == "error"
    assert run["error"]["code"] == "TOOL_LOOP_NO_PROGRESS"
    assert run["tool_loop"] is None  # forced final kept calling tools

    async def _calls():
        return await tools_service.list_tool_calls(owner, run_id=run["id"])

    calls = call(app_client, _calls)
    assert len(calls) == 2


def test_repeated_identical_failed_call_is_bounded(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)

    def executor(model, req):
        return result_with_tools(tool_calls=[{
            "id": "same", "name": "calculator", "arguments": {"expression": "1/0"},
        }])

    _install_adapter(monkeypatch, executor)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "loop"}], {},
            project_id=project_id, enabled_tool_ids=[CALCULATOR_ID],
        )

    run = call(app_client, _execute)
    assert run["status"] == "error"
    assert run["error"]["code"] == "TOOL_LOOP_NO_PROGRESS"
    assert run["error"]["code"] != "TOOL_LOOP_LIMIT_REACHED"

    async def _calls():
        return await tools_service.list_tool_calls(owner, run_id=run["id"])

    calls = call(app_client, _calls)
    assert len(calls) == 2
    assert all(c["execution_status"] == "failed" for c in calls)


def test_global_max_round_guard_remains(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"calls": 0}

    def executor(model, req):
        state["calls"] += 1
        return result_with_tools(tool_calls=[{
            "id": f"c{state['calls']}", "name": "calculator",
            "arguments": {"expression": f"{state['calls']}+1"},
        }])

    _install_adapter(monkeypatch, executor)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "loop"}], {},
            project_id=project_id, enabled_tool_ids=[CALCULATOR_ID], max_tool_rounds=1,
        )

    run = call(app_client, _execute)
    assert run["status"] == "error"
    assert run["error"]["code"] == "TOOL_LOOP_LIMIT_REACHED"


def test_distinct_calls_not_falsely_deduplicated(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[{
                "id": "a", "name": "web_search", "arguments": {"query": "alpha"},
            }])
        if state["round"] == 2:
            return result_with_tools(tool_calls=[{
                "id": "b", "name": "web_search", "arguments": {"query": "beta"},
            }])
        return result_success("two searches done")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        with patch(
            "app.services.search_providers.TinyFishSearchProvider.search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.side_effect = [
                _canonical_search_results("alpha"),
                _canonical_search_results("beta"),
            ]
            return await execution_module.execute_model(
                owner, model_id, "", [{"role": "user", "content": "go"}], {},
                project_id=project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
            )

    run = call(app_client, _execute)
    assert run["status"] == "success", run.get("error")

    async def _calls():
        return await tools_service.list_tool_calls(owner, run_id=run["id"])

    calls = call(app_client, _calls)
    assert len(calls) == 2


def test_round_observability_is_recorded(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[{
                "id": "r1", "name": "calculator", "arguments": {"expression": "3+4"},
            }])
        return result_success("seven")

    _install_adapter(monkeypatch, executor)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "calc"}], {},
            project_id=project_id, enabled_tool_ids=[CALCULATOR_ID],
        )

    run = call(app_client, _execute)
    assert run["status"] == "success"
    assert isinstance(run["tool_rounds"], list)
    assert any("round_index" in entry for entry in run["tool_rounds"])
    exec_entries = [e for e in run["tool_rounds"] if e.get("tool_name") == "calculator"]
    assert exec_entries
    assert exec_entries[0]["execution_status"] == "success"
    assert exec_entries[0]["argument_fingerprint"]
    assert exec_entries[0]["provider_tool_call_id"] == "r1"


# ===================================================== E: project provider override
def test_project_provider_override_at_execution(app_client, as_user):
    owner = as_user["id"]
    project = call(app_client, create_project, owner)
    secret = "projA-secret-UNIQUE-e1"

    put = app_client.put(
        f"/api/tool-valves/{SEARCH_TOOL_ID}",
        json={"project_id": project, "provider_values": {"api_key": secret, "max_results": 5}},
    )
    assert put.status_code == 200, put.text

    async def _resolve(pid):
        return await tool_valves.resolve_provider_execution(owner, "search", pid, project)

    resolved = call(app_client, _resolve, TINYFISH_SEARCH_PROVIDER_ID)
    assert resolved["configuration"]["max_results"] == 5
    assert resolved["credential_id"]
    assert resolved["credential_owner_id"] == owner
    assert resolved["api_key_enc"] is None

    async def _resolve_global():
        return await tool_valves.resolve_provider_execution(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID, None
        )

    global_resolved = call(app_client, _resolve_global)
    assert global_resolved.get("credential_id") is None
    assert global_resolved["configuration"].get("max_results") is None
    assert secret not in json.dumps(global_resolved)


def test_project_secret_isolation(app_client, as_user):
    owner = as_user["id"]
    project_a = call(app_client, create_project, owner, "iso-a")
    project_b = call(app_client, create_project, owner, "iso-b")
    secret_a = "AAA-project-secret-UNIQUE-e2"
    secret_b = "BBB-project-secret-UNIQUE-e3"

    async def _save():
        await _save_provider_values(owner, "search", TINYFISH_SEARCH_PROVIDER_ID,
                                    {"api_key": secret_a, "max_results": 5}, project_a)
        await _save_provider_values(owner, "search", TINYFISH_SEARCH_PROVIDER_ID,
                                    {"api_key": secret_b, "max_results": 10}, project_b)

    call(app_client, _save)

    async def _run(project_id):
        doc = await tool_valves.resolve_provider_execution(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID, project_id
        )
        adapter = SearchProviderRegistry.adapter_from_doc(doc)
        _CapturingTransport.response = httpx.Response(
            200, json={"results": [{"title": "t", "url": "u", "snippet": "s", "position": 1}]}
        )
        with patch(
            "app.services.search_providers.async_validate_provider_url",
            return_value=CANONICAL_BASE,
        ):
            with patch("httpx.AsyncClient", _CapturingTransport):
                await adapter.search("hello")
        return _CapturingTransport.last["headers"]["X-API-Key"]

    assert call(app_client, _run, project_a) == secret_a
    assert call(app_client, _run, project_b) == secret_b


def test_global_provider_fallback_for_project(app_client, as_user):
    owner = as_user["id"]
    project = call(app_client, create_project, owner, "fallback-project")
    global_secret = "GLOBAL-secret-UNIQUE-e4"

    async def _save_global():
        await _save_provider_values(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID,
            {"api_key": global_secret, "max_results": 7}, None,
        )

    call(app_client, _save_global)

    async def _resolve():
        return await tool_valves.resolve_provider_execution(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID, project
        )

    resolved = call(app_client, _resolve)
    assert resolved["configuration"]["max_results"] == 7
    assert resolved["credential_id"]
    assert resolved["credential_owner_id"] == owner


def test_revoked_project_credential_fails_closed(app_client, as_user):
    owner = as_user["id"]
    project = call(app_client, create_project, owner, "revoked-project")
    global_secret = "global-secret-UNIQUE-e5"
    project_secret = "project-secret-UNIQUE-e6"

    async def _save():
        await _save_provider_values(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID,
            {"api_key": global_secret}, None,
        )
        await _save_provider_values(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID,
            {"api_key": project_secret}, project,
        )
        row = await tool_valves._get_row(owner, "provider", TINYFISH_SEARCH_PROVIDER_ID, project)
        ref = (row or {}).get("secret_refs", {}).get("api_key")
        assert ref
        await repo.tool_credentials.delete(ref, owner)
        return ref

    call(app_client, _save)

    async def _resolve_key():
        doc = await tool_valves.resolve_provider_execution(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID, project
        )
        adapter = SearchProviderRegistry.adapter_from_doc(doc)
        with patch.dict(os.environ, {"TINYFISH_API_KEY": ""}, clear=False):
            return await adapter._resolve_api_key()

    assert call(app_client, _resolve_key) is None


# ======================================================= D: code interpreter valves
def test_code_interpreter_valve_runtime_injection(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _save_global():
        tool = await tools_service.get_tool(owner, CI_TOOL_ID)
        await tool_valves.save_tool(
            owner, tool,
            {"values": {
                "timeout_seconds": 15,
                "memory_mb": 256,
                "cpus": 0.5,
                "max_output_bytes": 4096,
            }},
            None,
        )

    call(app_client, _save_global)

    async def _limits(project_id):
        return await ci.resolve_execution_limits(owner, project_id)

    global_limits = call(app_client, _limits, None)
    assert global_limits["timeout_s"] == 15
    assert global_limits["memory_mb"] == 256
    assert global_limits["cpus"] == 0.5
    assert global_limits["max_output_bytes"] == 4096

    project = call(app_client, create_project, owner, "ci-project")

    async def _save_project():
        tool = await tools_service.get_tool(owner, CI_TOOL_ID)
        await tool_valves.save_tool(
            owner, tool, {"values": {"timeout_seconds": 5}}, project
        )

    call(app_client, _save_project)
    assert call(app_client, _limits, project)["timeout_s"] == 5

    other = call(app_client, create_project, owner, "ci-project-2")
    assert call(app_client, _limits, other)["timeout_s"] == 15

    # build_docker_command emits the requested (ceiling-clamped) resources.
    monkeypatch.setattr(config, "CODE_INTERPRETER_CPUS", 2.0)
    cmd = ci.build_docker_command(
        staging_input="/tmp/in", staging_output="/tmp/out", timeout_s=30,
        memory_mb=512, cpus=1.5,
    )
    assert cmd[cmd.index("--memory") + 1] == "512m"
    assert cmd[cmd.index("--memory-swap") + 1] == "512m"
    assert cmd[cmd.index("--cpus") + 1] == "1.5"
    # isolation flags untouched
    assert "--network" in cmd and "none" in cmd
    assert "--cap-drop" in cmd and "ALL" in cmd


def test_code_interpreter_ceiling_enforcement(app_client, as_user):
    owner = as_user["id"]
    schema = valves.code_interpreter_schema()
    _, errors = valves.validate_values(schema, {"timeout_seconds": 10 ** 6})
    assert errors
    assert any("ceiling" in error for error in errors)

    async def _insert_over_ceiling():
        await tool_valves._upsert_row(
            owner, "tool", CI_TOOL_ID, None,
            {"values": {"timeout_seconds": 10 ** 6}, "secret_refs": {}},
        )

    call(app_client, _insert_over_ceiling)

    async def _limits():
        return await ci.resolve_execution_limits(owner, None)

    limits = call(app_client, _limits)
    assert limits["timeout_s"] == int(config.CODE_INTERPRETER_TIMEOUT_SECONDS)

    async def _cmd():
        return ci.build_docker_command(
            staging_input="/tmp/in", staging_output="/tmp/out", timeout_s=30,
            memory_mb=10 ** 9,
        )

    cmd = call(app_client, _cmd)
    assert cmd[cmd.index("--memory") + 1] == f"{int(config.CODE_INTERPRETER_MEMORY_MB)}m"


# ======================================================= F: effective config snapshot
def test_effective_config_snapshot_hash_and_secret_hygiene(app_client, as_user):
    owner = as_user["id"]
    project = call(app_client, create_project, owner, "snapshot-project")
    secret = "snapshot-secret-UNIQUE-f1"

    async def _save():
        await _save_provider_values(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID,
            {"api_key": secret, "max_results": 5}, project,
        )

    call(app_client, _save)

    async def _snapshot():
        return await tool_valves.effective_tool_config_snapshot(
            owner, project,
            search_enabled=True, search_provider_id=TINYFISH_SEARCH_PROVIDER_ID,
            fetch_enabled=False, fetch_provider_id=None,
        )

    first = call(app_client, _snapshot)
    second = call(app_client, _snapshot)
    assert first == second
    assert first["search"]["credential_scope"] == "PROJECT"
    assert first["search"]["credential_ref"]
    assert first["search"]["valve_config_hash"]
    blob = json.dumps(first)
    assert secret not in blob
    assert "stored" not in blob

    async def _change():
        await _save_provider_values(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID,
            {"api_key": secret, "max_results": 9}, project,
        )

    call(app_client, _change)
    third = call(app_client, _snapshot)
    assert third["search"]["valve_config_hash"] != first["search"]["valve_config_hash"]


def test_effective_config_snapshot_immutable_on_run(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    secret = "immutable-secret-UNIQUE-f2"

    async def _seed_global():
        await _save_provider_values(
            owner, "search", TINYFISH_SEARCH_PROVIDER_ID,
            {"api_key": secret, "max_results": 5}, None,
        )

    call(app_client, _seed_global)

    def executor(model, req):
        return result_success("plain answer")

    _install_adapter(monkeypatch, executor, tools=False)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "hi"}], {},
            project_id=project_id, search_enabled=True,
            search_provider_id=TINYFISH_SEARCH_PROVIDER_ID,
        )

    run = call(app_client, _execute)
    assert run["status"] == "success"
    snapshot = run["effective_tool_config"]
    assert snapshot and snapshot["search"]
    original_hash = snapshot["search"]["valve_config_hash"]

    async def _change():
        tool = await tools_service.get_tool(owner, SEARCH_TOOL_ID)
        await tool_valves.save_tool(
            owner, tool, {"provider_values": {"max_results": 11}},
            None,
        )

    # Change the valves AFTER the run; the persisted snapshot must not move.
    call(app_client, _change)

    async def _reload():
        return await repo.runs.get(run["id"], owner)

    persisted = call(app_client, _reload)
    assert persisted["effective_tool_config"]["search"]["valve_config_hash"] == original_hash
    assert persisted["effective_tool_config"] == snapshot


# =========================================================== regression: transport
def test_search_provider_transport_regression(app_client, as_user):
    key = "regression-search-UNIQUE-g1"
    adapter = _search_adapter_with_key(key)

    async def _run():
        # Existing pattern: patch the module-level GET directly.
        mock_resp = httpx.Response(200, json={"results": [{
            "title": "OmniLab", "url": "https://example.com", "snippet": "S", "position": 1,
        }]})
        with patch(
            "app.services.search_providers.async_validate_provider_url",
            return_value=CANONICAL_BASE,
        ):
            with patch("httpx.AsyncClient.get", return_value=mock_resp):
                return await adapter.search("omnilab")

    out = call(app_client, _run)
    assert out["count"] == 1
    assert out["results"][0]["title"] == "OmniLab"
    assert out["results"][0]["rank"] == 1


def test_fetch_provider_transport_regression(app_client, as_user):
    key = "regression-fetch-UNIQUE-g2"
    adapter = TinyFishFetchProvider({
        "id": TINYFISH_FETCH_PROVIDER_ID,
        "provider_type": "tinyfish",
        "configuration": {"base_url": "https://api.fetch.tinyfish.ai"},
        "api_key_enc": security.encrypt_secret(key),
    })

    async def _run():
        _CapturingTransport.response = httpx.Response(
            200, json={"results": [{"text": "body", "final_url": "https://example.com/x"}]}
        )
        with patch(
            "app.services.fetch_providers.async_validate_provider_url",
            return_value="https://example.com/x",
        ):
            with patch("httpx.AsyncClient", _CapturingTransport):
                return await adapter.fetch("https://example.com/x")

    out = call(app_client, _run)
    assert out["text"] == "body"
    assert out["provider"] == TINYFISH_FETCH_PROVIDER_ID
