"""OMNILAB V4.2.3.1 provider-backend tests (offline, deterministic).

Covers:
 1. Vault-reference ownership (link, live resolve without plaintext exposure,
    clear, invalid id -> 404, cross-owner mismatch via repo-level check).
 2. Direct api_key entry still works.
 3. Ciphertext reuse both directions (fetch<-search, search<-fetch).
 4. Health persistence after POST .../test.
 5. is_default switching for the fetch registry.
 6. Project settings: accept search/fetch defaults, reject unknown ids and
    non-bool enabled flags.
 7. Public projections never contain api_key/api_key_enc/plaintext.
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import repository as repo
from app.services.search_providers import (
    TINYFISH_SEARCH_PROVIDER_ID,
    SearchProviderRegistry,
)
from app.services.fetch_providers import (
    STANDARD_FETCH_PROVIDER_ID,
    TINYFISH_FETCH_PROVIDER_ID,
    FetchProviderRegistry,
)
from tests.conftest import call

SECRET = "v4231-supersecret-UNIQUE-k7j6h5g4f3"
TINY_KEY = "tinyfish-live-key-UNIQUE-q1w2e3r4"
SAFE_BASE = "https://93.184.216.34/v1"

_V4231_COLLECTIONS = [
    "project_files", "knowledge_collections", "knowledge_documents",
    "knowledge_chunks", "tool_versions", "tool_credentials",
    "code_executions", "project_activity",
]

# Search/fetch provider docs are GLOBAL (owner_id None), so they are shared
# across xdist workers via MongoDB. To keep this module race-free against
# suites that read the real registries (e.g. test_v423_search_fetch.py under
# ``-n 2 --dist loadscope``), every test here runs against throwaway
# collections. All registry/router code resolves ``repo.search_providers`` /
# ``repo.fetch_providers`` by attribute at call time, so swapping the
# attributes redirects the whole HTTP stack transparently within this worker
# process only. The real ``search_providers`` / ``fetch_providers``
# collections are never touched by this file.
_TEST_SEARCH_COLL = "search_providers_v4231t"
_TEST_FETCH_COLL = "fetch_providers_v4231t"


@pytest.fixture(scope="module", autouse=True)
def _isolated_provider_collections(app_client):
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


@pytest.fixture(autouse=True)
def _v4231_purge(app_client, as_user):
    yield

    async def _do():
        from app.database import db
        for coll in _V4231_COLLECTIONS:
            await db[coll].delete_many({"owner_id": as_user["id"]})
        await db[_TEST_SEARCH_COLL].delete_many({})
        await db[_TEST_FETCH_COLL].delete_many({})

    app_client.portal.call(_do)


def _project(app_client, name="v4231"):
    r = app_client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _credential(app_client, project_id, name="TFKEY", value=TINY_KEY):
    r = app_client.post("/api/tool-credentials", json={
        "project_id": project_id, "name": name,
        "credential_type": "API_KEY", "value": value})
    assert r.status_code == 200, r.text
    assert value not in r.text
    return r.json()


def _assert_no_secrets(payload, *extra_forbidden):
    blob = payload if isinstance(payload, str) else json.dumps(payload)
    for banned in ("api_key_enc", "encrypted_value", *extra_forbidden):
        assert banned not in blob
    return blob


def _clear_direct_keys(app_client):
    async def _do():
        await repo.search_providers.update_global(
            TINYFISH_SEARCH_PROVIDER_ID,
            {"api_key_enc": None, "credential_id": None,
             "credential_owner_id": None})
        await repo.fetch_providers.update_global(
            TINYFISH_FETCH_PROVIDER_ID,
            {"api_key_enc": None, "credential_id": None,
             "credential_owner_id": None})
    call(app_client, _do)


# ------------------------------------------------ vault reference ownership
def test_vault_reference_resolves_live_without_plaintext(app_client, as_user):
    project = _project(app_client)
    cred = _credential(app_client, project["id"])
    _clear_direct_keys(app_client)

    r = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"credential_id": cred["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credential_id"] == cred["id"]
    assert body["has_vault_credential"] is True
    assert body["key_source"] == "vault"
    assert body["credential_status"] == "CONFIGURED"
    _assert_no_secrets(r.text, TINY_KEY)

    async def _search():
        with patch.dict(os.environ, {"TINYFISH_API_KEY": ""}, clear=False):
            adapter = await SearchProviderRegistry.get_adapter(
                TINYFISH_SEARCH_PROVIDER_ID)
            with patch(
                "app.services.search_providers.async_validate_provider_url",
                return_value="https://api.search.tinyfish.ai",
            ):
                captured = {}

                class _FakeResp:
                    status_code = 200

                    def json(self):
                        return {"results": [{
                            "title": "T", "url": "https://example.com/x",
                            "snippet": "S", "position": 1}]}

                class _FakeClient:
                    def __init__(self, *a, **k):
                        pass

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *a):
                        return False

                    async def get(self, url, params=None, headers=None):
                        captured.update(
                            {"auth": (headers or {}).get("X-API-Key"),
                             "url": url})
                        return _FakeResp()

                    async def post(self, url, params=None, headers=None, json=None):
                        captured.update(
                            {"auth": (headers or {}).get("X-API-Key"),
                             "url": url})
                        return _FakeResp()

                with patch("httpx.AsyncClient", _FakeClient):
                    out = await adapter.search("hello world")
                assert captured["auth"] == TINY_KEY
                assert captured["url"] == "https://api.search.tinyfish.ai"
                return out

    out = call(app_client, _search)
    assert out["count"] == 1
    assert out["results"][0]["title"] == "T"

    # Public projection carries the reference but no secret material.
    g = app_client.get(f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}")
    assert g.status_code == 200, g.text
    assert g.json()["credential_id"] == cred["id"]
    _assert_no_secrets(g.text, TINY_KEY)

    # GET list is clean too.
    lst = app_client.get("/api/search-providers")
    assert lst.status_code == 200, lst.text
    _assert_no_secrets(lst.text, TINY_KEY)


def test_vault_reference_clear_and_invalid_id(app_client, as_user):
    project = _project(app_client)
    cred = _credential(app_client, project["id"])
    _clear_direct_keys(app_client)

    r = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"credential_id": cred["id"]})
    assert r.status_code == 200, r.text

    c = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"credential_id": None})
    assert c.status_code == 200, c.text
    assert c.json()["credential_id"] is None
    assert c.json()["has_vault_credential"] is False

    bad = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"credential_id": "does-not-exist-123"})
    assert bad.status_code == 404, bad.text
    assert "credential not found" in bad.text.lower()

    badf = app_client.patch(
        f"/api/fetch-providers/{TINYFISH_FETCH_PROVIDER_ID}",
        json={"credential_id": "does-not-exist-123"})
    assert badf.status_code == 404, badf.text


def test_vault_owner_mismatch_rejected_repo_level(app_client, as_user):
    """credential_owner_id mismatch must never resolve another owner's secret."""
    project = _project(app_client)
    cred = _credential(app_client, project["id"], value="ownerA-secret-UNIQUE-z1z1")
    _clear_direct_keys(app_client)

    async def _poison_and_resolve():
        from app.services import search_providers as sp
        await sp.SearchProviderRegistry.ensure_defaults()
        await repo.search_providers.update_global(
            TINYFISH_SEARCH_PROVIDER_ID,
            {"credential_id": cred["id"],
             "credential_owner_id": "pytest-impostor-owner"})
        doc = await repo.search_providers.find_one(
            {"id": TINYFISH_SEARCH_PROVIDER_ID})
        adapter = sp.TinyFishSearchProvider(doc)
        with patch.dict(os.environ, {"TINYFISH_API_KEY": ""}, clear=False):
            adapter.doc["api_key_enc"] = None
            return await adapter._resolve_api_key()

    assert call(app_client, _poison_and_resolve) is None


def test_vault_scope_hint_mismatch_rejected(app_client, as_user):
    project = _project(app_client)
    other = _project(app_client, name="v4231-other")
    cred = _credential(app_client, project["id"])
    r = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"credential_id": cred["id"], "project_id": other["id"]})
    assert r.status_code == 403, r.text


# ------------------------------------------------ direct key + ciphertext reuse
def test_direct_api_key_still_works(app_client, as_user):
    _clear_direct_keys(app_client)
    raw = "direct-key-UNIQUE-d4e5f6"
    r = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"api_key": raw})
    assert r.status_code == 200, r.text
    assert r.json()["key_source"] == "direct"
    _assert_no_secrets(r.text, raw)

    async def _key():
        adapter = await SearchProviderRegistry.get_adapter(
            TINYFISH_SEARCH_PROVIDER_ID)
        return await adapter._resolve_api_key()

    assert call(app_client, _key) == raw


def test_ciphertext_reuse_both_directions(app_client, as_user):
    _clear_direct_keys(app_client)
    raw = "reuse-key-UNIQUE-r7s8t9"
    r = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"api_key": raw})
    assert r.status_code == 200, r.text
    _assert_no_secrets(r.text, raw)

    f = app_client.patch(
        f"/api/fetch-providers/{TINYFISH_FETCH_PROVIDER_ID}",
        json={"reuse_search_credential": True})
    assert f.status_code == 200, f.text
    _assert_no_secrets(f.text, raw)

    async def _ciphertexts():
        s = await repo.search_providers.find_one(
            {"id": TINYFISH_SEARCH_PROVIDER_ID})
        t = await repo.fetch_providers.find_one(
            {"id": TINYFISH_FETCH_PROVIDER_ID})
        return s.get("api_key_enc"), t.get("api_key_enc")

    s_enc, t_enc = call(app_client, _ciphertexts)
    assert s_enc and t_enc and s_enc == t_enc

    # Reverse direction: wipe fetch key, set it fresh, copy back to search.
    async def _wipe_search():
        await repo.search_providers.update_global(
            TINYFISH_SEARCH_PROVIDER_ID, {"api_key_enc": None})

    call(app_client, _wipe_search)
    raw2 = "reuse-key-2-UNIQUE-a1b2c3"
    rf = app_client.patch(
        f"/api/fetch-providers/{TINYFISH_FETCH_PROVIDER_ID}",
        json={"api_key": raw2})
    assert rf.status_code == 200, rf.text
    rs = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"reuse_fetch_credential": True})
    assert rs.status_code == 200, rs.text
    _assert_no_secrets(rs.text, raw2)
    s_enc2, t_enc2 = call(app_client, _ciphertexts)
    assert s_enc2 and t_enc2 and s_enc2 == t_enc2

    # Empty source -> 400.
    call(app_client, _wipe_search)

    async def _wipe_fetch():
        await repo.fetch_providers.update_global(
            TINYFISH_FETCH_PROVIDER_ID, {"api_key_enc": None})

    call(app_client, _wipe_fetch)
    bad = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"reuse_fetch_credential": True})
    assert bad.status_code == 400, bad.text
    assert "no api key to reuse" in bad.text.lower()


# ------------------------------------------------ health persistence
def test_health_persisted_after_test(app_client, as_user):
    async def _search():
        adapter = await SearchProviderRegistry.get_adapter(
            TINYFISH_SEARCH_PROVIDER_ID)
        with patch.object(adapter, "_resolve_api_key",
                           new=AsyncMock(return_value="k")):
            with patch(
                "app.services.search_providers.async_validate_provider_url",
                return_value=f"{SAFE_BASE}",
            ):
                mock_resp = httpx.Response(200, json={"status": "ok"})
                with patch("httpx.AsyncClient.get", return_value=mock_resp):
                    return await adapter.test_connection()

    assert call(app_client, _search)["status"] == "CONNECTED"

    t = app_client.post(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}/test")
    assert t.status_code == 200, t.text
    status = t.json()["status"]
    g = app_client.get(f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}")
    assert g.status_code == 200, g.text
    assert g.json()["health_status"] == status
    assert g.json()["last_checked_at"]

    f = app_client.post(
        f"/api/fetch-providers/{STANDARD_FETCH_PROVIDER_ID}/test")
    assert f.status_code == 200, f.text
    assert f.json()["ok"] is True
    gf = app_client.get(f"/api/fetch-providers/{STANDARD_FETCH_PROVIDER_ID}")
    assert gf.status_code == 200, gf.text
    assert gf.json()["health_status"] == "CONNECTED"
    assert gf.json()["last_checked_at"]


# ------------------------------------------------ is_default switching
def test_fetch_is_default_switching(app_client, as_user):
    lst = app_client.get("/api/fetch-providers")
    assert lst.status_code == 200, lst.text
    items = {p["id"]: p for p in lst.json()["items"]}
    assert items[STANDARD_FETCH_PROVIDER_ID]["is_default"] is True
    assert items[TINYFISH_FETCH_PROVIDER_ID]["is_default"] is False

    r = app_client.patch(
        f"/api/fetch-providers/{TINYFISH_FETCH_PROVIDER_ID}",
        json={"is_default": True})
    assert r.status_code == 200, r.text
    assert r.json()["is_default"] is True

    lst2 = app_client.get("/api/fetch-providers")
    items2 = {p["id"]: p for p in lst2.json()["items"]}
    assert items2[TINYFISH_FETCH_PROVIDER_ID]["is_default"] is True
    assert items2[STANDARD_FETCH_PROVIDER_ID]["is_default"] is False

    async def _default_adapter():
        adapter = await FetchProviderRegistry.get_adapter(None)
        return adapter.id

    assert call(app_client, _default_adapter) == TINYFISH_FETCH_PROVIDER_ID

    # Search: single provider, is_default stays true.
    s = app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"is_default": True})
    assert s.status_code == 200, s.text
    assert s.json()["is_default"] is True


# ------------------------------------------------ project settings
def test_project_settings_accept_and_reject_search_fetch(app_client, as_user):
    project = _project(app_client)
    ok = app_client.patch(f"/api/projects/{project['id']}/settings", json={
        "default_search_enabled": True,
        "default_search_provider_id": TINYFISH_SEARCH_PROVIDER_ID,
        "default_fetch_enabled": True,
        "default_fetch_provider_id": STANDARD_FETCH_PROVIDER_ID,
    })
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["default_search_enabled"] is True
    assert body["default_search_provider_id"] == TINYFISH_SEARCH_PROVIDER_ID
    assert body["default_fetch_enabled"] is True
    assert body["default_fetch_provider_id"] == STANDARD_FETCH_PROVIDER_ID

    bad_search = app_client.patch(
        f"/api/projects/{project['id']}/settings",
        json={"default_search_provider_id": "prov-search-nope"})
    assert bad_search.status_code == 400, bad_search.text

    bad_fetch = app_client.patch(
        f"/api/projects/{project['id']}/settings",
        json={"default_fetch_provider_id": "prov-fetch-nope"})
    assert bad_fetch.status_code == 400, bad_fetch.text

    bad_flag = app_client.patch(
        f"/api/projects/{project['id']}/settings",
        json={"default_search_enabled": "yes"})
    assert bad_flag.status_code == 400, bad_flag.text


# ------------------------------------------------ projection hygiene sweep
def test_public_projections_never_leak(app_client, as_user):
    raw = "hygiene-key-UNIQUE-h1h2h3"
    app_client.patch(
        f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
        json={"api_key": raw})
    for url in ("/api/search-providers", "/api/fetch-providers",
                f"/api/search-providers/{TINYFISH_SEARCH_PROVIDER_ID}",
                f"/api/fetch-providers/{TINYFISH_FETCH_PROVIDER_ID}",
                f"/api/fetch-providers/{STANDARD_FETCH_PROVIDER_ID}"):
        r = app_client.get(url)
        assert r.status_code == 200, r.text
        blob = _assert_no_secrets(r.text, raw)
        assert "api_key" not in json.loads(
            blob if blob.startswith(("{", "[")) else "{}"
        ).get("configuration", {}) if blob.startswith("{") else True
    docs = app_client.get("/api/search-providers").json()["items"]
    docs += app_client.get("/api/fetch-providers").json()["items"]
    for doc in docs:
        assert "api_key_enc" not in doc
        assert "credential_owner_id" not in doc
        assert doc["key_source"] in ("vault", "direct", "env", "none")
