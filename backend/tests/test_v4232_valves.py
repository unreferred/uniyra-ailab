"""OMNILAB V4.2.3.2 valve-layer tests (offline, deterministic).

Coverage:
  1. Valve schema validation: value types, integer bounds (min/max) and the
     "keep existing" blank-secret convention.
  2. Hard security ceiling: an ``app.config`` ceiling can never be bypassed by a
     caller, and lowering the ceiling tightens validation (monkeypatched).
  3. TinyFish valve configuration over the real HTTP API: 4-state status
     (enabled / configured / available / healthy) transitions from
     NOT_CONFIGURED to CONFIGURED after a secret + non-secret valves are saved.
  4. Secret valves are encrypted at rest (vault) and never returned: neither the
     provider endpoint nor the tool-valve endpoint leaks plaintext ciphertext or
     the raw secret, yet the provider adapter resolves the real secret
     server-side.
  5. Replacing a secret rewrites one vault row (ciphertext changes) and the new
     secret resolves; the old plaintext never appears in the response.
  6. Legacy credentials still project correctly: an existing ``api_key_enc``
     provider doc needs no re-entry and resolves without exposing the secret.
  7. Project override hierarchy: a project row overrides the global row while
     the global read stays unchanged.
  8. Disabled provider transitions the tool status to disabled/unavailable with
     the explicit "Provider is disabled" reason.
  9. The Calculator exposes no configurable valves (empty schema, no provider).
 10. Valves are configuration, never authorization: configuring valves never
     grants the tool's declared permission.

Search/fetch provider docs are GLOBAL, so this module swaps
``repo.search_providers`` / ``repo.fetch_providers`` / ``repo.valve_values`` for
throwaway collections. All registry/router code resolves these attributes at
call time, so the swap redirects the whole HTTP stack within this worker only;
the real global registry docs are never touched.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import config
from app import repository as repo
from app import security
from app.models import now_iso
from app.services import search_providers
from app.services import tool_valves
from app.services import tools as tool_service
from app.services import valves
from app.services.search_providers import (
    TINYFISH_SEARCH_PROVIDER_ID,
    SearchProviderRegistry,
)
from tests.conftest import call

SEARCH_TOOL_ID = "tool-search-0001"
CODE_TOOL_ID = "tool-code-interpreter-0001"
CALCULATOR_TOOL_ID = "tool-calculator-0001"
CANONICAL_BASE = "https://api.search.tinyfish.ai"

_TEST_SEARCH_COLL = "search_providers_v4232t"
_TEST_FETCH_COLL = "fetch_providers_v4232t"
_TEST_VALVE_COLL = "valve_values_v4232t"


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
def _v4232_purge(app_client, as_user):
    """Delete owner-scoped vault + valve rows after every test."""
    yield

    async def _do():
        from app.database import db
        await db["tool_credentials"].delete_many({"owner_id": as_user["id"]})
        await db[_TEST_VALVE_COLL].delete_many({"owner_id": as_user["id"]})

    app_client.portal.call(_do)


# --------------------------------------------------------------------- helpers
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


def _resolve_search_key(app_client):
    """Resolve the TinyFish adapter's real key server-side (never returned by HTTP)."""
    async def _resolve():
        adapter = await SearchProviderRegistry.get_adapter(TINYFISH_SEARCH_PROVIDER_ID)
        with patch(
            "app.services.search_providers.async_validate_provider_url",
            return_value=CANONICAL_BASE,
        ):
            return await adapter._resolve_api_key()

    return call(app_client, _resolve)


def _put_provider_values(app_client, values):
    return app_client.put(
        f"/api/tool-valves/{SEARCH_TOOL_ID}", json={"provider_values": values}
    )


# ----------------------------------------------------------- schema validation
def test_valve_validation_types_and_bounds(app_client):
    schema = valves.search_provider_schema("tinyfish")

    clean, errors = valves.validate_values(
        schema, {"api_key": "k", "max_results": 3, "timeout_seconds": 10}
    )
    assert errors == []
    assert clean["max_results"] == 3
    assert clean["timeout_seconds"] == 10

    _, errors = valves.validate_values(schema, {"max_results": 0})
    assert any("at least" in error for error in errors)

    _, errors = valves.validate_values(schema, {"max_results": 999})
    assert any("at most" in error for error in errors)

    _, errors = valves.validate_values(schema, {"timeout_seconds": "abc"})
    assert errors

    clean, _ = valves.validate_values(schema, {"nope": 1})
    assert "nope" not in clean

    clean, errors = valves.validate_values(schema, {"api_key": ""})
    assert errors == []
    assert "api_key" not in clean


# --------------------------------------------------------- hard security ceiling
def test_hard_security_ceiling_cannot_be_bypassed(app_client, monkeypatch):
    schema = valves.code_interpreter_schema()
    definition = schema["timeout_seconds"]
    bound = valves.effective_max(definition)
    assert bound is not None
    assert bound <= 120

    _, errors = valves.validate_values(schema, {"timeout_seconds": 10 ** 6})
    assert errors
    assert any("ceiling" in error for error in errors)

    monkeypatch.setattr(config, "CODE_INTERPRETER_TIMEOUT_SECONDS", 5)
    tightened = valves.code_interpreter_schema()

    _, errors = valves.validate_values(tightened, {"timeout_seconds": 10})
    assert errors

    clean, errors = valves.validate_values(tightened, {"timeout_seconds": 5})
    assert errors == []
    assert clean["timeout_seconds"] == 5


# ------------------------------------------------- 4-state status transitions
def test_tinyfish_not_configured_then_configured_status(app_client, as_user):
    _reset_tinyfish(app_client)

    r = app_client.get(f"/api/tool-valves/{SEARCH_TOOL_ID}")
    assert r.status_code == 200, r.text
    status = r.json()["status"]
    assert status["enabled"] is True
    assert status["configured"] is False
    assert status["available"] is False
    assert "API Key required" in status["reason"]

    valve_probe = "tf-probe-UNIQUE-a1"
    put = _put_provider_values(
        app_client,
        {"api_key": valve_probe, "timeout_seconds": 20, "max_results": 7},
    )
    assert put.status_code == 200, put.text
    provider_values = put.json()["provider_values"]
    assert provider_values["api_key"] == {"stored": True}
    assert provider_values["timeout_seconds"] == 20
    assert provider_values["max_results"] == 7
    assert valve_probe not in put.text
    assert put.json()["status"]["configured"] is True


# ------------------------------------------------ secret encrypted, never returned
def test_secret_valve_encrypted_and_never_returned(app_client, as_user):
    _reset_tinyfish(app_client)
    valve_probe = "tf-probe-UNIQUE-b2c3d4"

    put = _put_provider_values(app_client, {"api_key": valve_probe})
    assert put.status_code == 200, put.text

    for url in (
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        f"/api/tool-valves/{SEARCH_TOOL_ID}",
    ):
        r = app_client.get(url)
        assert r.status_code == 200, r.text
        assert valve_probe not in r.text
        assert "api_key_enc" not in r.text
        assert "encrypted_value" not in r.text

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
    assert vault_doc["encrypted_value"] != valve_probe

    assert _resolve_search_key(app_client) == valve_probe


def test_replacing_secret_works(app_client, as_user):
    _reset_tinyfish(app_client)

    async def _ciphertext():
        provider = await repo.search_providers.find_one(
            {"id": TINYFISH_SEARCH_PROVIDER_ID}
        )
        vault = await repo.tool_credentials.find_one(
            {"id": provider["credential_id"]}
        )
        return vault["encrypted_value"]

    old_secret = "old-UNIQUE-1"
    new_secret = "new-UNIQUE-2"

    first = _put_provider_values(app_client, {"api_key": old_secret})
    assert first.status_code == 200, first.text
    first_cipher = call(app_client, _ciphertext)

    second = _put_provider_values(app_client, {"api_key": new_secret})
    assert second.status_code == 200, second.text
    second_cipher = call(app_client, _ciphertext)

    assert first_cipher != second_cipher
    assert _resolve_search_key(app_client) == new_secret
    assert old_secret not in second.text


# ------------------------------------------------------ legacy credential migration
def test_legacy_credential_migration_projection(app_client, as_user):
    legacy = "legacy-UNIQUE-k"

    async def _insert_legacy():
        from app.database import db
        await db[_TEST_SEARCH_COLL].delete_many({"id": TINYFISH_SEARCH_PROVIDER_ID})
        now = now_iso()
        await repo.search_providers.insert({
            "id": TINYFISH_SEARCH_PROVIDER_ID,
            "name": "TinyFish Search",
            "provider_type": "tinyfish",
            "enabled": True,
            "capabilities": {"search": "SUPPORTED"},
            "configuration": {"base_url": CANONICAL_BASE},
            "api_key_enc": security.encrypt_secret(legacy),
            "credential_id": None,
            "credential_owner_id": None,
            "is_default": True,
            "health_status": None,
            "last_checked_at": None,
            "created_at": now,
            "updated_at": now,
        })

    call(app_client, _insert_legacy)

    r = app_client.get(f"/api/tool-valves/{SEARCH_TOOL_ID}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider_values"]["api_key"] == {"stored": True}
    assert body["status"]["configured"] is True
    assert legacy not in r.text

    assert _resolve_search_key(app_client) == legacy


# ------------------------------------------------------------ project override
def test_project_override_hierarchy(app_client, as_user):
    project = app_client.post("/api/projects", json={"name": "v4232-override"})
    assert project.status_code == 200, project.text
    project_id = project.json()["id"]

    global_put = app_client.put(
        f"/api/tool-valves/{CODE_TOOL_ID}", json={"values": {"timeout_seconds": 10}}
    )
    assert global_put.status_code == 200, global_put.text

    project_put = app_client.put(
        f"/api/tool-valves/{CODE_TOOL_ID}",
        json={"project_id": project_id, "values": {"timeout_seconds": 20}},
    )
    assert project_put.status_code == 200, project_put.text

    global_get = app_client.get(f"/api/tool-valves/{CODE_TOOL_ID}")
    assert global_get.status_code == 200, global_get.text
    assert global_get.json()["values"]["timeout_seconds"] == 10

    override_get = app_client.get(
        f"/api/tool-valves/{CODE_TOOL_ID}", params={"project_id": project_id}
    )
    assert override_get.status_code == 200, override_get.text
    assert override_get.json()["values"]["timeout_seconds"] == 20


# ------------------------------------------------------- disabled provider status
def test_disabled_provider_status_reason(app_client, as_user):
    _reset_tinyfish(app_client)
    put = _put_provider_values(app_client, {"api_key": "disabled-key-UNIQUE"})
    assert put.status_code == 200, put.text

    patched = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"enabled": False},
    )
    assert patched.status_code == 200, patched.text

    r = app_client.get(f"/api/tool-valves/{SEARCH_TOOL_ID}")
    assert r.status_code == 200, r.text
    status = r.json()["status"]
    assert status["enabled"] is False
    assert status["available"] is False
    assert status["reason"] == "Provider is disabled"


# --------------------------------------------------------- calculator has no valves
def test_calculator_has_no_configurable_valves(app_client, as_user):
    r = app_client.get(f"/api/tool-valves/{CALCULATOR_TOOL_ID}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == {}
    assert body["provider_kind"] is None


# ---------------------------------------------------- valves are not authorization
def test_valves_are_not_authorization(app_client, as_user):
    search_tool = call(app_client, tool_service.get_tool, as_user["id"], SEARCH_TOOL_ID)

    valves.validate_values(
        valves.search_provider_schema("tinyfish"), {"max_results": 3}
    )
    before = tool_service.resolve_effective_permission(search_tool, enabled_tool_ids=[])
    assert before["status"] == "DENIED"
    assert "search.execute" in before["required_permission"]

    put = _put_provider_values(
        app_client, {"api_key": "authz-key-UNIQUE", "max_results": 3}
    )
    assert put.status_code == 200, put.text

    after = tool_service.resolve_effective_permission(search_tool, enabled_tool_ids=[])
    assert after["status"] == "DENIED"
