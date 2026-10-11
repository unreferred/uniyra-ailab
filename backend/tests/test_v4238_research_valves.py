"""OMNILAB V4.2.3.8 web_research valve tests (offline, deterministic).

Coverage:
  1. ``web_research`` resolves the EXISTING ``search`` provider kind through the
     valve-name registry, so TinyFish search configuration is shared and never
     duplicated; provider identity stays internal.
  2. ``fetch_provider_schema`` exposes the protected ``max_urls`` valve
     ("Batch size (provider batch cap)") for both ``tinyfish`` and ``standard``
     fetch providers, clamped by the server-side ``config.MAX_BATCH_FETCH_URLS``
     ceiling on provider execution batch size (never research breadth).
  3. Saving a ``web_research`` tool's ``provider_values`` routes to the search
     provider; a SECRET ``api_key`` is stored encrypted and only ever projects as
     ``{"stored": True}``.
  4. Atomic builtins are unaffected: ``web_search`` -> search, ``web_fetch`` ->
     fetch.

Search/fetch provider docs are GLOBAL, so this module swaps
``repo.search_providers`` / ``repo.fetch_providers`` / ``repo.valve_values`` for
throwaway collections, exactly as ``test_v4232_valves`` does.
"""
from __future__ import annotations

import json

import pytest

from app import config
from app import repository as repo
from app.services import tool_valves
from app.services import valves
from app.services.search_providers import (
    TINYFISH_SEARCH_PROVIDER_ID,
    SearchProviderRegistry,
)
from tests.conftest import call

RESEARCH_TOOL_ID = "tool-research-0001"
CANONICAL_BASE = "https://api.search.tinyfish.ai"

_TEST_SEARCH_COLL = "search_providers_v4238t"
_TEST_FETCH_COLL = "fetch_providers_v4238t"
_TEST_VALVE_COLL = "valve_values_v4238t"


@pytest.fixture(scope="module", autouse=True)
def _isolated_provider_collections(app_client):
    """Redirect provider/valve repositories to throwaway collections for this module."""
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
def _v4238_purge(app_client, as_user):
    """Delete owner-scoped vault + valve rows after every test."""
    yield

    async def _do():
        from app.database import db
        await db["tool_credentials"].delete_many({"owner_id": as_user["id"]})
        await db[_TEST_VALVE_COLL].delete_many({"owner_id": as_user["id"]})

    app_client.portal.call(_do)


# --------------------------------------------------------------------- helpers
def _effective_batch_ceiling() -> float:
    """Mirror the lazy ``config.MAX_BATCH_FETCH_URLS`` ceiling resolution.

    Falls back to the definition bound (100) when the protected attribute is not
    defined, matching ``valves.ceiling_for`` returning ``None``.
    """
    configured = getattr(config, "MAX_BATCH_FETCH_URLS", None)
    if configured is None:
        return 100.0
    return min(100.0, float(configured))


def _reset_tinyfish(app_client, *, enabled=True):
    """Ensure the throwaway registry holds a freshly seeded, keyless TinyFish doc."""
    async def _do():
        await SearchProviderRegistry.ensure_defaults()
        await repo.search_providers.update_global(
            TINYFISH_SEARCH_PROVIDER_ID,
            {
                "api_key_enc": None,
                "credential_id": None,
                "credential_owner_id": None,
                "enabled": enabled,
                "is_default": True,
                "configuration": {"base_url": CANONICAL_BASE},
                "health_status": None,
            },
        )

    call(app_client, _do)


def _research_tool_doc():
    return {
        "id": RESEARCH_TOOL_ID,
        "name": "web_research",
        "display_name": "Web Research",
        "tool_type": "function",
        "enabled": True,
    }


# --------------------------------------------------- web_research kind mapping
def test_web_research_maps_to_search_provider_kind():
    assert valves.TOOL_VALVE_NAMES["web_research"] == "search"
    assert valves.tool_provider_kind({"name": "web_research"}) == "search"
    assert valves.tool_provider_kind({"id": RESEARCH_TOOL_ID, "name": "web_research"}) == "search"


def test_atomic_builtin_kinds_unchanged():
    assert valves.TOOL_VALVE_NAMES["web_search"] == "search"
    assert valves.tool_provider_kind({"name": "web_search"}) == "search"
    assert valves.TOOL_VALVE_NAMES["web_fetch"] == "fetch"
    assert valves.tool_provider_kind({"name": "web_fetch"}) == "fetch"


# ------------------------------------------------------- max_urls valve surface
@pytest.mark.parametrize("provider_type", ["tinyfish", "standard"])
def test_fetch_schema_exposes_max_urls(provider_type):
    schema = valves.fetch_provider_schema(provider_type)
    assert "max_urls" in schema
    definition = schema["max_urls"]
    assert definition.type == "INTEGER"
    assert definition.label == "Batch size (provider batch cap)"
    assert definition.description
    assert definition.default == 5
    assert definition.min == 1
    assert definition.max == 100
    assert definition.ceiling_attr == "MAX_BATCH_FETCH_URLS"
    assert valves.effective_max(definition) == _effective_batch_ceiling()


@pytest.mark.parametrize("provider_type", ["tinyfish", "standard"])
def test_max_urls_ceiling_tightens_and_validates(monkeypatch, provider_type):
    monkeypatch.setattr(config, "MAX_BATCH_FETCH_URLS", 2, raising=False)

    schema = valves.fetch_provider_schema(provider_type)
    assert valves.effective_max(schema["max_urls"]) == 2

    clean, errors = valves.validate_values(
        schema, {"max_urls": 2}, existing_secret_keys={"api_key"}
    )
    assert errors == []
    assert clean["max_urls"] == 2

    _, errors = valves.validate_values(schema, {"max_urls": 99})
    assert errors
    assert any("ceiling" in error for error in errors)

    _, errors = valves.validate_values(schema, {"max_urls": 0})
    assert any("at least" in error for error in errors)


def test_max_urls_default_within_ceiling():
    for provider_type in ("tinyfish", "standard"):
        definition = valves.fetch_provider_schema(provider_type)["max_urls"]
        assert definition.default <= valves.effective_max(definition)


# ------------------------------------------- web_research provider-value routing
def test_web_research_provider_values_route_to_search(app_client, as_user):
    _reset_tinyfish(app_client)
    secret = "research-secret-UNIQUE-v4238"

    body = {
        "provider_id": TINYFISH_SEARCH_PROVIDER_ID,
        "provider_values": {
            "api_key": secret,
            "max_results": 7,
            "timeout_seconds": 20,
        },
    }
    resolved = call(
        app_client,
        tool_valves.save_tool,
        as_user["id"],
        _research_tool_doc(),
        body,
    )

    assert resolved["provider_kind"] == "search"
    assert resolved["provider_id"] == TINYFISH_SEARCH_PROVIDER_ID
    assert resolved["provider_values"]["api_key"] == {"stored": True}
    assert resolved["provider_values"]["max_results"] == 7
    assert resolved["provider_values"]["timeout_seconds"] == 20
    assert secret not in json.dumps(resolved)

    provider_doc = call(
        app_client, repo.search_providers.find_one, {"id": TINYFISH_SEARCH_PROVIDER_ID}
    )
    assert provider_doc is not None
    assert provider_doc.get("credential_id")
    assert provider_doc.get("credential_owner_id") == as_user["id"]

    vault_doc = call(
        app_client, repo.tool_credentials.find_one, {"id": provider_doc["credential_id"]}
    )
    assert vault_doc is not None
    assert vault_doc.get("encrypted_value")
    assert vault_doc["encrypted_value"] != secret


def test_web_research_resolved_schema_is_search_provider(app_client, as_user):
    _reset_tinyfish(app_client)

    resolved = call(
        app_client,
        tool_valves.resolve_tool,
        as_user["id"],
        _research_tool_doc(),
        None,
    )
    assert resolved["provider_kind"] == "search"
    assert "max_results" in resolved["provider_schema"]
    assert "api_key" in resolved["provider_schema"]
    # ``max_urls`` is a fetch-only valve and must not leak into the shared
    # search provider configuration used by web_research.
    assert "max_urls" not in resolved["provider_schema"]


def test_web_fetch_resolved_schema_surfaces_max_urls(app_client, as_user):
    async def _seed():
        from app.services.fetch_providers import FetchProviderRegistry

        await FetchProviderRegistry.ensure_defaults()

    call(app_client, _seed)

    resolved = call(
        app_client,
        tool_valves.resolve_tool,
        as_user["id"],
        {
            "id": "tool-fetch-0001",
            "name": "web_fetch",
            "display_name": "Web Fetch",
            "tool_type": "function",
            "enabled": True,
        },
        None,
    )
    assert resolved["provider_kind"] == "fetch"
    assert "max_urls" in resolved["provider_schema"]
    assert resolved["provider_values"]["max_urls"] == 5
