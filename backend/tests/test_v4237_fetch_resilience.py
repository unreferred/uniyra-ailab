"""OMNILAB V4.2.3.7 "Fetch Resilience" tests (offline, deterministic).

Covers the formal timeout hierarchy and structured timeout errors:

* ``app.config`` defines the three timeout layers with safe defaults.
* Provider ``timeout_seconds`` valves carry a server-side ``ceiling_attr`` so
  ``effective_max`` can never exceed the protected ceiling, and validation
  rejects values above it.
* Standard and TinyFish fetch providers raise a structured
  ``FetchError(code="FETCH_TIMEOUT")`` with ``url``/``timeout_seconds``/
  ``provider`` details, and their ``_timeout()`` honors both the valve value and
  the server ceiling.
* TinyFish search raises a structured ``SearchError(code="SEARCH_TIMEOUT")``
  with ``query``/``timeout_seconds``/``provider`` details.
* ``execute_tool`` persists a failed ToolCall with the provider ``error.code``
  and secret-free timeout metadata.

Network is fully mocked; DNS is monkeypatched. Async paths run on the shared
``app_client`` portal loop via ``tests.conftest.call``.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional
from unittest.mock import patch

import httpx
import pytest

from app import config
from app import urlguard
from app.services import tools as tool_service
from app.services import valves
from app.services.fetch_providers import (
    FetchError,
    StandardFetchProvider,
    TinyFishFetchProvider,
)
from app.services.search_providers import (
    SearchError,
    TinyFishSearchProvider,
)
from tests.conftest import call

PUBLIC_IP = "93.184.216.34"
TARGET_URL = "https://example.com/slow"


# ----------------------------------------------------------------- fake clients
class _TimeoutGetClient:
    """AsyncClient double whose GET always times out."""

    last_timeout: Any = None

    def __init__(self, *args, **kwargs):
        type(self).last_timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kwargs):
        raise httpx.TimeoutException("simulated timeout")


class _TimeoutPostClient:
    """AsyncClient double whose POST always times out."""

    last_timeout: Any = None

    def __init__(self, *args, **kwargs):
        type(self).last_timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        raise httpx.TimeoutException("simulated timeout")


def _resolver_public(monkeypatch) -> None:
    monkeypatch.setattr(
        urlguard, "_resolve_host_sync", lambda host: [PUBLIC_IP]
    )


def _standard_doc(timeout: Optional[float] = None) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if timeout is not None:
        cfg["timeout_seconds"] = timeout
    return {
        "id": "prov-fetch-standard",
        "name": "Standard HTTP Fetch",
        "provider_type": "standard",
        "enabled": True,
        "configuration": cfg,
    }


def _tinyfish_fetch_doc(timeout: Optional[float] = None) -> Dict[str, Any]:
    from app.security import encrypt_secret

    cfg: Dict[str, Any] = {}
    if timeout is not None:
        cfg["timeout_seconds"] = timeout
    return {
        "id": "prov-fetch-tinyfish",
        "name": "TinyFish Fetch",
        "provider_type": "tinyfish",
        "enabled": True,
        "configuration": cfg,
        "api_key_enc": encrypt_secret("tf-key-v4237"),
        "credential_id": None,
        "credential_owner_id": None,
    }


def _tinyfish_search_doc(timeout: Optional[float] = None) -> Dict[str, Any]:
    from app.security import encrypt_secret

    cfg: Dict[str, Any] = {}
    if timeout is not None:
        cfg["timeout_seconds"] = timeout
    return {
        "id": "prov-search-tinyfish",
        "name": "TinyFish Search",
        "provider_type": "tinyfish",
        "enabled": True,
        "configuration": cfg,
        "api_key_enc": encrypt_secret("tf-search-key-v4237"),
        "credential_id": None,
        "credential_owner_id": None,
    }


# ============================================================ config hierarchy
def test_timeout_hierarchy_config_vars_present():
    expected = {
        "PROVIDER_CONNECTION_TIMEOUT_SECONDS": 120,
        "FETCH_REQUEST_TIMEOUT_MAX_SECONDS": 60,
        "SEARCH_REQUEST_TIMEOUT_MAX_SECONDS": 60,
        "AGENT_TOOL_BUDGET_SECONDS": 180,
    }
    for name, default in expected.items():
        value = getattr(config, name)
        assert isinstance(value, int), name
        assert value >= 1, name
        if name not in os.environ:
            assert value == default, name


# ============================================================== valve ceilings
def test_valve_timeout_definitions_declare_server_ceiling():
    assert (
        valves.fetch_provider_schema("tinyfish")["timeout_seconds"].ceiling_attr
        == "FETCH_REQUEST_TIMEOUT_MAX_SECONDS"
    )
    assert (
        valves.fetch_provider_schema("standard")["timeout_seconds"].ceiling_attr
        == "FETCH_REQUEST_TIMEOUT_MAX_SECONDS"
    )
    assert (
        valves.search_provider_schema("tinyfish")["timeout_seconds"].ceiling_attr
        == "SEARCH_REQUEST_TIMEOUT_MAX_SECONDS"
    )


def test_valve_effective_max_clamped_by_server_ceiling(monkeypatch):
    monkeypatch.setattr(config, "FETCH_REQUEST_TIMEOUT_MAX_SECONDS", 30)
    schema = valves.fetch_provider_schema("tinyfish")
    assert valves.effective_max(schema["timeout_seconds"]) == 30
    _, errors = valves.validate_values(schema, {"timeout_seconds": 31})
    assert errors and any("ceiling" in error for error in errors)
    clean, errors = valves.validate_values(
        schema, {"timeout_seconds": 30}, existing_secret_keys={"api_key"}
    )
    assert errors == []
    assert clean["timeout_seconds"] == 30

    monkeypatch.setattr(config, "FETCH_REQUEST_TIMEOUT_MAX_SECONDS", 10)
    standard = valves.fetch_provider_schema("standard")
    assert valves.effective_max(standard["timeout_seconds"]) == 10
    _, errors = valves.validate_values(standard, {"timeout_seconds": 11})
    assert errors

    monkeypatch.setattr(config, "SEARCH_REQUEST_TIMEOUT_MAX_SECONDS", 25)
    search = valves.search_provider_schema("tinyfish")
    assert valves.effective_max(search["timeout_seconds"]) == 25
    _, errors = valves.validate_values(search, {"timeout_seconds": 26})
    assert errors


# ======================================================== provider _timeout()
def test_standard_timeout_honors_valve_and_server_ceiling(monkeypatch):
    assert StandardFetchProvider(_standard_doc())._timeout() == 15.0

    monkeypatch.setattr(config, "FETCH_REQUEST_TIMEOUT_MAX_SECONDS", 30)
    assert StandardFetchProvider(_standard_doc(5))._timeout() == 5.0
    assert StandardFetchProvider(_standard_doc(45))._timeout() == 30.0
    assert StandardFetchProvider(_standard_doc())._timeout() == 15.0


def test_tinyfish_fetch_timeout_default_and_ceiling(monkeypatch):
    assert TinyFishFetchProvider(_tinyfish_fetch_doc())._timeout() == 20.0

    monkeypatch.setattr(config, "FETCH_REQUEST_TIMEOUT_MAX_SECONDS", 25)
    assert TinyFishFetchProvider(_tinyfish_fetch_doc(40))._timeout() == 25.0
    assert TinyFishFetchProvider(_tinyfish_fetch_doc(5))._timeout() == 5.0


def test_search_timeout_default_and_ceiling(monkeypatch):
    assert TinyFishSearchProvider(_tinyfish_search_doc())._timeout() == 15.0

    monkeypatch.setattr(config, "SEARCH_REQUEST_TIMEOUT_MAX_SECONDS", 25)
    assert TinyFishSearchProvider(_tinyfish_search_doc(40))._timeout() == 25.0
    assert TinyFishSearchProvider(_tinyfish_search_doc(5))._timeout() == 5.0


# ==================================================== structured timeout errors
def test_standard_fetch_timeout_is_structured(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    provider = StandardFetchProvider(_standard_doc(15))

    async def _go():
        with patch(
            "app.services.fetch_providers.httpx.AsyncClient", _TimeoutGetClient
        ):
            return await provider.fetch(TARGET_URL)

    with pytest.raises(FetchError) as excinfo:
        call(app_client, _go)
    exc = excinfo.value
    assert exc.code == "FETCH_TIMEOUT"
    assert exc.status_code == 504
    assert exc.provider == "standard"
    assert exc.details["url"] == TARGET_URL
    assert exc.details["timeout_seconds"] == 15.0
    assert exc.details["provider"] == "standard"
    assert _TimeoutGetClient.last_timeout == 15.0


def test_standard_fetch_timeout_respects_lowered_ceiling(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    monkeypatch.setattr(config, "FETCH_REQUEST_TIMEOUT_MAX_SECONDS", 10)
    provider = StandardFetchProvider(_standard_doc(45))

    async def _go():
        with patch(
            "app.services.fetch_providers.httpx.AsyncClient", _TimeoutGetClient
        ):
            return await provider.fetch(TARGET_URL)

    with pytest.raises(FetchError) as excinfo:
        call(app_client, _go)
    assert excinfo.value.details["timeout_seconds"] == 10.0
    assert _TimeoutGetClient.last_timeout == 10.0


def test_tinyfish_fetch_timeout_is_structured(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    provider = TinyFishFetchProvider(_tinyfish_fetch_doc(20))

    async def _go():
        with patch(
            "app.services.fetch_providers.httpx.AsyncClient", _TimeoutPostClient
        ):
            return await provider.fetch(TARGET_URL)

    with pytest.raises(FetchError) as excinfo:
        call(app_client, _go)
    exc = excinfo.value
    assert exc.code == "FETCH_TIMEOUT"
    assert exc.status_code == 504
    assert exc.provider == "tinyfish"
    assert exc.details["url"] == TARGET_URL
    assert exc.details["timeout_seconds"] == 20.0
    assert exc.details["provider"] == "tinyfish"


def test_search_timeout_is_structured(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    provider = TinyFishSearchProvider(_tinyfish_search_doc(15))

    async def _go():
        with patch(
            "app.services.search_providers.httpx.AsyncClient", _TimeoutGetClient
        ):
            return await provider.search("forex resilience")

    with pytest.raises(SearchError) as excinfo:
        call(app_client, _go)
    exc = excinfo.value
    assert exc.code == "SEARCH_TIMEOUT"
    assert exc.status_code == 504
    assert exc.provider == "tinyfish"
    assert exc.details["query"] == "forex resilience"
    assert exc.details["timeout_seconds"] == 15.0
    assert exc.details["provider"] == "tinyfish"


# ===================================================== execute_tool resilience
def test_safe_error_details_preserves_timeout_metadata_drops_secrets():
    cleaned = tool_service._safe_error_details({
        "url": TARGET_URL,
        "timeout_seconds": 15,
        "provider": "standard",
        "query": "forex",
        "api_key": "SECRET-SHOULD-NOT-APPEAR",
        "Authorization": "Bearer SECRET",
    })
    assert cleaned["url"] == TARGET_URL
    assert cleaned["timeout_seconds"] == 15
    assert cleaned["provider"] == "standard"
    assert cleaned["query"] == "forex"
    assert "api_key" not in cleaned
    assert "Authorization" not in cleaned


def test_execute_tool_fetch_timeout_persists_failed_toolcall(app_client, as_user):
    tool = {
        "id": "tool-fetch-0001",
        "name": "web_fetch",
        "display_name": "Web Fetch",
        "version": 1,
        "tool_type": "function",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        "permissions": ["fetch.execute"],
        "implementation": "builtin:web_fetch",
        "enabled": True,
    }

    async def _timeout_executor(arguments, context=None):
        raise FetchError(
            "Request to URL timed out",
            504,
            "FETCH_TIMEOUT",
            code="FETCH_TIMEOUT",
            provider="standard",
            details={
                "url": arguments.get("url"),
                "timeout_seconds": 15,
                "provider": "standard",
                "api_key": "SECRET-SHOULD-NOT-APPEAR",
            },
        )

    async def _run():
        with patch.object(
            tool_service, "_resolve_executor", return_value=_timeout_executor
        ):
            return await tool_service.execute_tool(
                tool,
                {"url": TARGET_URL},
                owner_id=as_user["id"],
                enabled_tool_ids=["tool-fetch-0001"],
            )

    doc = call(app_client, _run)
    assert doc["permission_status"] == "granted"
    assert doc["execution_status"] == "failed"
    assert doc["error"]["code"] == "FETCH_TIMEOUT"
    assert doc["error"]["provider"] == "standard"
    details = doc["error"]["details"]
    assert details["url"] == TARGET_URL
    assert details["timeout_seconds"] == 15
    assert details["provider"] == "standard"
    assert "api_key" not in details
    assert "SECRET-SHOULD-NOT-APPEAR" not in json.dumps(doc)

    async def _fetch_persisted():
        from app.database import db

        return await db["tool_calls"].find_one({"id": doc["id"]})

    stored = call(app_client, _fetch_persisted)
    assert stored is not None
    assert stored["execution_status"] == "failed"
    assert stored["error"]["code"] == "FETCH_TIMEOUT"
    assert stored["error"]["details"]["url"] == TARGET_URL
