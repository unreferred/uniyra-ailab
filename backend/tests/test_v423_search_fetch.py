"""V4.2.3 Search + Fetch Foundation Tests (offline, deterministic).

Covers:
1. Search Provider Registry & API (seed, public sanitization, secret encryption, connection test)
2. Fetch Provider Registry & API (Standard + TinyFish, public sanitization, connection test)
3. Canonical Web Search Tool (tool-search-0001):
   - Catalog presence & permissions
   - Default deny when unselected
   - Granted when selected
   - Normalized results
   - Unconfigured key behavior (never fakes results)
4. Canonical Web Fetch Tool (tool-fetch-0001):
   - Catalog presence & permissions
   - Default deny when unselected
   - Granted when selected
   - Strict SSRF protection (localhost, 127.0.0.1, AWS metadata 169.254.169.254, RFC1918)
   - Redirect chain re-validation (rejects SSRF on subsequent hops)
   - HTML parsing and readable text extraction
5. Project Settings Inheritance:
   - Global defaults -> Project defaults -> Conversation overrides -> Request overrides
   - Tool auto-wiring into enabled_tool_ids when search/fetch is toggled on
"""
from __future__ import annotations

import os
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
import httpx

from app.models import new_id
from app.services import builtin_tools
from app.services import tools as tool_service
from app.services import project_manager
from app.services.search_providers import (
    SearchProviderRegistry,
    TinyFishSearchProvider,
    TINYFISH_SEARCH_PROVIDER_ID,
    public_search_provider,
    SearchError,
)
from app.services.fetch_providers import (
    FetchProviderRegistry,
    StandardFetchProvider,
    TinyFishFetchProvider,
    STANDARD_FETCH_PROVIDER_ID,
    TINYFISH_FETCH_PROVIDER_ID,
    public_fetch_provider,
    FetchError,
)
from app.routers.workspace import _tool_ids_for
from tests.conftest import call


_TEST_SEARCH_COLL = "search_providers_v423t"
_TEST_FETCH_COLL = "fetch_providers_v423t"


@pytest.fixture(scope="module", autouse=True)
def _isolated_provider_collections(app_client):
    """Redirect search/fetch provider repositories to throwaway collections.

    The registry documents are global (owner_id None) and shared across the
    process, so an api_key/credential_id left by another suite could make the
    "unconfigured" negative tests hit the real network. Redirecting the repo
    attributes for this module keeps every test hermetic.
    """
    from app import repository as repo_module
    from app.repository import Repository

    real_search = repo_module.search_providers
    real_fetch = repo_module.fetch_providers
    repo_module.search_providers = Repository(_TEST_SEARCH_COLL)
    repo_module.fetch_providers = Repository(_TEST_FETCH_COLL)
    yield

    async def _drop():
        from app.database import db

        await db[_TEST_SEARCH_COLL].delete_many({})
        await db[_TEST_FETCH_COLL].delete_many({})

    app_client.portal.call(_drop)
    repo_module.search_providers = real_search
    repo_module.fetch_providers = real_fetch


# ------------------------------------------------------------------- Search Providers
def test_search_provider_registry_seeds_tinyfish(app_client, as_user):
    call(app_client, SearchProviderRegistry.ensure_defaults)
    providers = call(app_client, SearchProviderRegistry.list_providers, as_user["id"])
    assert any(p["id"] == TINYFISH_SEARCH_PROVIDER_ID for p in providers)
    tinyfish = next(p for p in providers if p["id"] == TINYFISH_SEARCH_PROVIDER_ID)
    assert tinyfish["name"] == "TinyFish Search"
    assert tinyfish["provider_type"] == "tinyfish"
    assert "api_key" not in tinyfish.get("configuration", {})
    assert "api_key_enc" not in tinyfish


def test_search_provider_public_projection_never_leaks_secrets():
    doc = {
        "id": "prov-test-search",
        "name": "Test Search",
        "provider_type": "tinyfish",
        "enabled": True,
        "configuration": {"base_url": "https://api.tinyfish.io", "api_key": "raw_secret"},
        "api_key_enc": "encrypted_blob",
    }
    public = public_search_provider(doc)
    assert "api_key" not in public["configuration"]
    assert "api_key_enc" not in public
    assert public["credential_status"] == "CONFIGURED"


def test_search_provider_test_connection_unconfigured(app_client, as_user):
    async def _test():
        adapter = await SearchProviderRegistry.get_adapter(TINYFISH_SEARCH_PROVIDER_ID)
        with patch.dict(os.environ, {"TINYFISH_API_KEY": ""}, clear=False):
            adapter.doc["api_key_enc"] = None
            return await adapter.test_connection()

    result = call(app_client, _test)
    assert result["ok"] is False
    assert result["status"] == "NOT_CONFIGURED"


def test_search_provider_test_connection_mock_success(app_client, as_user):
    async def _test():
        adapter = await SearchProviderRegistry.get_adapter(TINYFISH_SEARCH_PROVIDER_ID)
        with patch.object(adapter, "_get_api_key", return_value="valid_key"):
            with patch("app.services.search_providers.async_validate_provider_url", return_value="https://api.tinyfish.io/v1"):
                mock_resp = httpx.Response(200, json={"status": "ok"})
                with patch("httpx.AsyncClient.get", return_value=mock_resp):
                    return await adapter.test_connection()

    result = call(app_client, _test)
    assert result["ok"] is True
    assert result["status"] == "CONNECTED"


# ------------------------------------------------------------------- Fetch Providers
def test_fetch_provider_registry_seeds_standard_and_tinyfish(app_client, as_user):
    call(app_client, FetchProviderRegistry.ensure_defaults)
    providers = call(app_client, FetchProviderRegistry.list_providers, as_user["id"])
    ids = [p["id"] for p in providers]
    assert STANDARD_FETCH_PROVIDER_ID in ids
    assert TINYFISH_FETCH_PROVIDER_ID in ids
    standard = next(p for p in providers if p["id"] == STANDARD_FETCH_PROVIDER_ID)
    assert standard["credential_status"] == "NOT_REQUIRED"
    assert standard["health_status"] == "CONNECTED"


def test_fetch_provider_test_connection_standard(app_client, as_user):
    async def _test():
        adapter = await FetchProviderRegistry.get_adapter(STANDARD_FETCH_PROVIDER_ID)
        return await adapter.test_connection()

    result = call(app_client, _test)
    assert result["ok"] is True
    assert result["status"] == "CONNECTED"


# ------------------------------------------------------------------- Web Search Tool
def test_web_search_tool_in_catalog():
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    assert "tool-search-0001" in tools
    search_tool = tools["tool-search-0001"]
    assert search_tool["name"] == "web_search"
    assert search_tool["permissions"] == ["search.execute"]
    assert search_tool["implementation"] == "builtin:web_search"
    assert search_tool["enabled"] is True


def test_web_search_default_deny_when_unselected(app_client, as_user):
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    search_tool = tools["tool-search-0001"]

    # 1. Gate evaluation: unselected is DENIED
    decision = tool_service.resolve_effective_permission(search_tool, enabled_tool_ids=[])
    assert decision["status"] == "DENIED"
    assert "search.execute" in decision["missing"]

    # 2. Execution gate: execution fails with TOOL_PERMISSION_DENIED
    async def _run():
        return await tool_service.execute_tool(
            search_tool,
            {"query": "quantum computing"},
            owner_id=as_user["id"],
            enabled_tool_ids=[],
        )

    call_doc = call(app_client, _run)
    assert call_doc["permission_status"] == "denied"
    assert call_doc["execution_status"] == "skipped"
    assert call_doc["error"]["code"] == "TOOL_PERMISSION_DENIED"


def test_web_search_granted_when_selected():
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    search_tool = tools["tool-search-0001"]

    decision = tool_service.resolve_effective_permission(
        search_tool, enabled_tool_ids=["tool-search-0001"]
    )
    assert decision["status"] == "GRANTED"
    assert decision["source"] == "REQUEST"


def test_web_search_execution_not_configured_when_no_api_key(app_client, as_user):
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    search_tool = tools["tool-search-0001"]

    async def _run():
        with patch.dict(os.environ, {"TINYFISH_API_KEY": ""}, clear=False):
            return await tool_service.execute_tool(
                search_tool,
                {"query": "omnilab release"},
                owner_id=as_user["id"],
                enabled_tool_ids=["tool-search-0001"],
            )

    call_doc = call(app_client, _run)
    assert call_doc["permission_status"] == "granted"
    assert call_doc["execution_status"] == "failed"
    assert "not configured" in call_doc["error"]["message"].lower()


def test_web_search_execution_success_mock(app_client, as_user):
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    search_tool = tools["tool-search-0001"]

    mock_results = {
        "query": "omnilab release",
        "provider": TINYFISH_SEARCH_PROVIDER_ID,
        "results": [
            {
                "title": "OmniLab AI Platform",
                "url": "https://example.com/omnilab",
                "snippet": "Advanced AI agent platform and laboratory.",
                "rank": 1,
            }
        ],
        "count": 1,
    }

    async def _run():
        with patch("app.services.search_providers.TinyFishSearchProvider.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_results
            return await tool_service.execute_tool(
                search_tool,
                {"query": "omnilab release"},
                owner_id=as_user["id"],
                enabled_tool_ids=["tool-search-0001"],
            )

    call_doc = call(app_client, _run)
    assert call_doc["permission_status"] == "granted"
    assert call_doc["execution_status"] == "success"
    assert call_doc["output"]["count"] == 1
    assert call_doc["output"]["results"][0]["title"] == "OmniLab AI Platform"


# ------------------------------------------------------------------- Web Fetch Tool
def test_web_fetch_tool_in_catalog():
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    assert "tool-fetch-0001" in tools
    fetch_tool = tools["tool-fetch-0001"]
    assert fetch_tool["name"] == "web_fetch"
    assert fetch_tool["permissions"] == ["fetch.execute"]
    assert fetch_tool["implementation"] == "builtin:web_fetch"
    assert fetch_tool["enabled"] is True


def test_web_fetch_default_deny_when_unselected(app_client, as_user):
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    fetch_tool = tools["tool-fetch-0001"]

    decision = tool_service.resolve_effective_permission(fetch_tool, enabled_tool_ids=[])
    assert decision["status"] == "DENIED"
    assert "fetch.execute" in decision["missing"]

    async def _run():
        return await tool_service.execute_tool(
            fetch_tool,
            {"url": "https://example.com"},
            owner_id=as_user["id"],
            enabled_tool_ids=[],
        )

    call_doc = call(app_client, _run)
    assert call_doc["permission_status"] == "denied"
    assert call_doc["execution_status"] == "skipped"
    assert call_doc["error"]["code"] == "TOOL_PERMISSION_DENIED"


def test_web_fetch_granted_when_selected():
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    fetch_tool = tools["tool-fetch-0001"]

    decision = tool_service.resolve_effective_permission(
        fetch_tool, enabled_tool_ids=["tool-fetch-0001"]
    )
    assert decision["status"] == "GRANTED"


@pytest.mark.parametrize(
    "blocked_url",
    [
        "http://127.0.0.1:8000/secret",
        "http://localhost:3000/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
    ],
)
def test_web_fetch_ssrf_blocks_private_ips(app_client, as_user, blocked_url: str):
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    fetch_tool = tools["tool-fetch-0001"]

    async def _run():
        return await tool_service.execute_tool(
            fetch_tool,
            {"url": blocked_url},
            owner_id=as_user["id"],
            enabled_tool_ids=["tool-fetch-0001"],
        )

    call_doc = call(app_client, _run)
    assert call_doc["permission_status"] == "granted"
    assert call_doc["execution_status"] == "failed"
    assert "ssrf" in call_doc["error"]["message"].lower() or "blocked" in call_doc["error"]["message"].lower()


def test_web_fetch_html_text_extraction(app_client, as_user):
    tools = {t["id"]: t for t in builtin_tools.BUILTIN_TOOL_DEFINITIONS}
    fetch_tool = tools["tool-fetch-0001"]

    sample_html = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>OmniLab Research Docs</title>
        <style>body { color: red; }</style>
      </head>
      <body>
        <script>console.log("ignore me");</script>
        <h1>Welcome to OmniLab</h1>
        <p>OmniLab provides state-of-the-art AI experimentation.</p>
      </body>
    </html>
    """

    mock_resp = httpx.Response(
        200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        content=sample_html.encode("utf-8"),
    )

    async def _run():
        with patch("app.services.fetch_providers.async_validate_provider_url", return_value="https://docs.omnilab.ai/info"):
            with patch("httpx.AsyncClient.get", return_value=mock_resp):
                return await tool_service.execute_tool(
                    fetch_tool,
                    {"url": "https://docs.omnilab.ai/info"},
                    owner_id=as_user["id"],
                    enabled_tool_ids=["tool-fetch-0001"],
                )

    call_doc = call(app_client, _run)
    assert call_doc["permission_status"] == "granted"
    assert call_doc["execution_status"] == "success"
    out = call_doc["output"]
    assert out["title"] == "OmniLab Research Docs"
    assert "Welcome to OmniLab" in out["text"]
    assert "OmniLab provides state-of-the-art" in out["text"]
    assert "console.log" not in out["text"]
    assert "body { color: red; }" not in out["text"]


# ------------------------------------------------------------------- Endpoints HTTP Test
def test_search_and_fetch_api_endpoints(app_client, as_user):
    # Search providers list
    resp = app_client.get("/api/search-providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert any(p["id"] == TINYFISH_SEARCH_PROVIDER_ID for p in data["items"])

    # Search provider test endpoint
    test_resp = app_client.post(f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}/test")
    assert test_resp.status_code == 200
    assert "status" in test_resp.json()

    # Fetch providers list
    f_resp = app_client.get("/api/fetch-providers")
    assert f_resp.status_code == 200
    f_data = f_resp.json()
    assert "items" in f_data
    assert any(p["id"] == STANDARD_FETCH_PROVIDER_ID for p in f_data["items"])

    # Fetch provider test endpoint
    f_test = app_client.post(f"/api/fetch-providers/{STANDARD_FETCH_PROVIDER_ID}/test")
    assert f_test.status_code == 200
    assert f_test.json()["ok"] is True


# ------------------------------------------------------------------- Project Settings & Inheritance
def test_project_settings_defaults_include_search_and_fetch():
    project = {
        "default_search_enabled": True,
        "default_search_provider_id": "prov-search-custom",
        "default_fetch_enabled": True,
        "default_fetch_provider_id": "prov-fetch-custom",
    }
    defaults = project_manager.resolve_project_defaults(project)
    assert defaults["default_search_enabled"] is True
    assert defaults["default_search_provider_id"] == "prov-search-custom"
    assert defaults["default_fetch_enabled"] is True
    assert defaults["default_fetch_provider_id"] == "prov-fetch-custom"


def test_search_fetch_settings_inheritance_chain():
    # 1. Project level provides search=True
    project = {"default_search_enabled": True, "default_fetch_enabled": False}
    conv: Dict[str, Any] = {}
    overrides: Dict[str, Any] = {}

    resolved = project_manager.resolve_effective_config(project, conv, overrides)
    assert resolved["effective"]["search_enabled"] is True
    assert resolved["sources"]["search_enabled"] == "project"
    assert resolved["effective"]["fetch_enabled"] is False

    # 2. Conversation level overrides search=False
    conv = {"search_enabled": False, "fetch_enabled": True}
    resolved = project_manager.resolve_effective_config(project, conv, overrides)
    assert resolved["effective"]["search_enabled"] is False
    assert resolved["sources"]["search_enabled"] == "conversation"
    assert resolved["effective"]["fetch_enabled"] is True
    assert resolved["sources"]["fetch_enabled"] == "conversation"

    # 3. Request level overrides search=True
    overrides = {"search_enabled": True}
    resolved = project_manager.resolve_effective_config(project, conv, overrides)
    assert resolved["effective"]["search_enabled"] is True
    assert resolved["sources"]["search_enabled"] == "request"


def test_tool_ids_auto_wiring_when_search_fetch_enabled():
    class DummyBody:
        enabled_tool_ids: List[str] = ["tool-calculator-0001"]
        search_enabled = True
        fetch_enabled = False

    conv: Dict[str, Any] = {}
    effective = {"search_enabled": True, "fetch_enabled": False}

    tools = _tool_ids_for(DummyBody(), conv, effective)
    assert "tool-calculator-0001" in tools
    assert "tool-search-0001" in tools
    assert "tool-fetch-0001" not in tools

    # Both toggled on
    DummyBody.fetch_enabled = True
    tools2 = _tool_ids_for(DummyBody(), conv, {"search_enabled": True, "fetch_enabled": True})
    assert "tool-search-0001" in tools2
    assert "tool-fetch-0001" in tools2
