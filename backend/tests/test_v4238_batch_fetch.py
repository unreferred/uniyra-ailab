"""OMNILAB V4.2.3.8 batch-fetch capability tests (offline).

Proves the additive batch layer over the canonical fetch providers without
changing the existing single-URL ``fetch()`` contract:

* TinyFish ``fetch_many`` issues exactly ONE provider POST containing every
  (validated, clamped) URL, preserving request order.
* ``config.MAX_BATCH_FETCH_URLS`` clamps the batch.
* Upstream per-URL ``errors[]`` become failed records beside success records.
* ``supports_batch_fetch`` is advertised by TinyFish + Standard and denied by a
  trivial dummy provider; ``public_fetch_provider`` exposes it (including in
  ``capabilities``) without leaking secrets.
* The base default and Standard overlays never raise and preserve order.

All network access is mocked; DNS is monkeypatched. Async provider paths run on
the shared ``app_client`` portal loop via ``tests.conftest.call``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx
import pytest

from app import urlguard
from app.security import encrypt_secret
from app.services import fetch_providers
from app.services.fetch_providers import (
    STANDARD_FETCH_PROVIDER_ID,
    TINYFISH_FETCH_ENDPOINT,
    TINYFISH_FETCH_PROVIDER_ID,
    BaseFetchProvider,
    FetchError,
    StandardFetchProvider,
    TinyFishFetchProvider,
    public_fetch_provider,
)
from tests.conftest import call

PUBLIC_IP = "93.184.216.34"
URL_A = "https://example.com/a"
URL_B = "https://example.com/b"
URL_C = "https://example.com/c"
URL_D = "https://example.com/d"


def _standard_doc() -> Dict[str, Any]:
    return {
        "id": STANDARD_FETCH_PROVIDER_ID,
        "name": "Standard HTTP Fetch",
        "provider_type": "standard",
        "enabled": True,
        "configuration": {},
    }


def _tinyfish_doc() -> Dict[str, Any]:
    """TinyFish doc WITHOUT an embedded key so env resolution is used."""
    return {
        "id": TINYFISH_FETCH_PROVIDER_ID,
        "name": "TinyFish Fetch",
        "provider_type": "tinyfish",
        "enabled": True,
        # Deliberately hostile: the adapter must ignore this.
        "configuration": {"base_url": "https://evil-user-config.example"},
        "api_key_enc": None,
        "credential_id": None,
        "credential_owner_id": None,
    }


class _BatchPostClient:
    """AsyncClient double: serves one queued batch response, captures the POST."""

    captured: Dict[str, Any] = {}
    posts: List[str] = []
    response: Any = {"results": []}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, headers=None, json=None, **kwargs):
        type(self).captured["url"] = str(url)
        type(self).captured["json"] = json
        type(self).captured["headers_present"] = bool(headers)
        type(self).posts.append(str(url))
        payload = type(self).response
        if callable(payload):
            payload = payload(json)
        return httpx.Response(200, json=payload)


class _SinglePostClient:
    """AsyncClient double for the unchanged single-URL TinyFish ``fetch``."""

    captured: Dict[str, Any] = {}
    result_text: str = "single body"

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, headers=None, json=None, **kwargs):
        urls = (json or {}).get("urls") or []
        type(self).captured["url"] = str(url)
        type(self).captured["json"] = json
        return httpx.Response(
            200,
            json={"results": [{
                "text": type(self).result_text,
                "final_url": urls[0] if urls else None,
            }]},
        )


class _DummyProvider(BaseFetchProvider):
    """Trivial provider: must NOT advertise batch support."""

    async def test_connection(self) -> Dict[str, Any]:
        return {"ok": True}

    async def fetch(self, url: str) -> Dict[str, Any]:
        return {"requested_url": url, "status": 200, "text": "dummy"}


class _FlakyProvider(BaseFetchProvider):
    """Minimal provider used to exercise the base default ``fetch_many``."""

    async def test_connection(self) -> Dict[str, Any]:
        return {"ok": True}

    async def fetch(self, url: str) -> Dict[str, Any]:
        if "bad" in url:
            raise FetchError("boom", 502, "NETWORK_ERROR", code="NETWORK_ERROR")
        return {"requested_url": url, "status": 200, "text": "ok"}


@pytest.fixture(autouse=True)
def _reset_clients():
    _BatchPostClient.captured = {}
    _BatchPostClient.posts = []
    _BatchPostClient.response = {"results": []}
    _SinglePostClient.captured = {}
    _SinglePostClient.result_text = "single body"
    yield


def _resolver_public(monkeypatch):
    monkeypatch.setattr(
        urlguard, "_resolve_host_sync", lambda host: [PUBLIC_IP]
    )


def _batch_payload(urls: List[str], text: str = "body") -> Dict[str, Any]:
    return {
        "results": [
            {"text": "%s-%s" % (text, i), "final_url": u, "url": u}
            for i, u in enumerate(urls)
        ]
    }


# ===================================================== ONE POST / ORDER
def test_tinyfish_fetch_many_issues_one_post_in_order(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    provider = TinyFishFetchProvider(_tinyfish_doc())
    urls = [URL_A, URL_B, URL_C]
    _BatchPostClient.response = _batch_payload(urls)

    async def _go():
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "k"}):
            with patch(
                "app.services.fetch_providers.httpx.AsyncClient", _BatchPostClient
            ):
                return await provider.fetch_many(urls)

    records = call(app_client, _go)
    assert len(_BatchPostClient.posts) == 1
    assert _BatchPostClient.captured["url"] == TINYFISH_FETCH_ENDPOINT
    assert _BatchPostClient.captured["json"]["urls"] == urls
    assert [r["url"] for r in records] == urls
    assert all(r["status"] == "success" for r in records)
    assert records[0]["egress_mode"] == "REMOTE"
    assert records[0]["target_url"] == URL_A
    assert records[0]["metadata"]["provider_endpoint_host"] == "api.fetch.tinyfish.ai"


# ===================================================== CLAMPING
def test_max_batch_fetch_urls_clamps_batch(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    monkeypatch.setattr(fetch_providers.config, "MAX_BATCH_FETCH_URLS", 2)
    provider = TinyFishFetchProvider(_tinyfish_doc())
    urls = [URL_A, URL_B, URL_C, URL_D]
    _BatchPostClient.response = _batch_payload([URL_A, URL_B])

    async def _go():
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "k"}):
            with patch(
                "app.services.fetch_providers.httpx.AsyncClient", _BatchPostClient
            ):
                return await provider.fetch_many(urls)

    records = call(app_client, _go)
    assert _BatchPostClient.captured["json"]["urls"] == [URL_A, URL_B]
    assert len(records) == 2
    assert [r["url"] for r in records] == [URL_A, URL_B]


# ===================================================== PARTIAL FAILURE
def test_tinyfish_partial_batch_failure(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    provider = TinyFishFetchProvider(_tinyfish_doc())
    urls = [URL_A, URL_B]
    _BatchPostClient.response = {
        "results": [{"text": "good", "final_url": URL_A, "url": URL_A}],
        "errors": [{
            "url": URL_B,
            "error": {"code": "UPSTREAM_TIMEOUT", "message": "took too long"},
        }],
    }

    async def _go():
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "k"}):
            with patch(
                "app.services.fetch_providers.httpx.AsyncClient", _BatchPostClient
            ):
                return await provider.fetch_many(urls)

    records = call(app_client, _go)
    assert records[0]["url"] == URL_A
    assert records[0]["status"] == "success"
    assert records[0]["text"] == "good"
    assert records[1]["url"] == URL_B
    assert records[1]["status"] == "failed"
    assert records[1]["error"]["code"] == "UPSTREAM_TIMEOUT"
    assert records[1]["error"]["message"] == "took too long"


def test_tinyfish_invalid_target_reported_and_excluded(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    provider = TinyFishFetchProvider(_tinyfish_doc())
    urls = ["http://127.0.0.1/admin", URL_A]
    _BatchPostClient.response = _batch_payload([URL_A], text="ok")

    async def _go():
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "k"}):
            with patch(
                "app.services.fetch_providers.httpx.AsyncClient", _BatchPostClient
            ):
                return await provider.fetch_many(urls)

    records = call(app_client, _go)
    assert _BatchPostClient.captured["json"]["urls"] == [URL_A]
    assert records[0]["url"] == urls[0]
    assert records[0]["status"] == "failed"
    assert records[0]["error"]["code"] == "SSRF_BLOCKED"
    assert records[1]["status"] == "success"


# ===================================================== SUPPORTS FLAG
def test_supports_batch_fetch_flags():
    assert TinyFishFetchProvider(_tinyfish_doc()).supports_batch_fetch is True
    assert StandardFetchProvider(_standard_doc()).supports_batch_fetch is True
    assert _DummyProvider({}).supports_batch_fetch is False


def test_public_fetch_provider_exposes_supports_batch_fetch_without_secrets():
    tiny = _tinyfish_doc()
    tiny["api_key_enc"] = encrypt_secret("super-secret-key")
    public_tiny = public_fetch_provider(tiny)
    assert public_tiny["supports_batch_fetch"] is True
    assert public_tiny["capabilities"]["supports_batch_fetch"] is True
    assert "super-secret-key" not in str(public_tiny)

    public_std = public_fetch_provider(_standard_doc())
    assert public_std["supports_batch_fetch"] is True
    assert public_std["capabilities"]["supports_batch_fetch"] is True

    unknown = {"id": "x", "name": "X", "provider_type": "mystery", "enabled": True}
    public_unknown = public_fetch_provider(unknown)
    assert public_unknown["supports_batch_fetch"] is False
    assert public_unknown["capabilities"]["supports_batch_fetch"] is False


# ===================================================== BASE DEFAULT
def test_base_default_fetch_many_order_and_per_url_failure(app_client):
    provider = _FlakyProvider({})
    urls = ["https://example.com/good", "https://example.com/bad"]

    async def _go():
        return await provider.fetch_many(urls)

    records = call(app_client, _go)
    assert [r["url"] for r in records] == urls
    assert records[0]["status"] == "success"
    assert records[0]["text"] == "ok"
    assert records[1]["status"] == "failed"
    assert records[1]["error"]["code"] == "NETWORK_ERROR"
    assert records[1]["error"]["message"] == "boom"


# ===================================================== SINGLE FETCH UNCHANGED
def test_single_fetch_contract_unchanged(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    provider = TinyFishFetchProvider(_tinyfish_doc())

    async def _go():
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "k"}):
            with patch(
                "app.services.fetch_providers.httpx.AsyncClient", _SinglePostClient
            ):
                return await provider.fetch(URL_A)

    out = call(app_client, _go)
    assert _SinglePostClient.captured["json"]["urls"] == [URL_A]
    assert out["requested_url"] == URL_A
    assert out["final_url"] == URL_A
    assert out["status"] == 200
    assert out["text"] == "single body"
    assert out["egress_mode"] == "REMOTE"
    assert out["target_url"] == URL_A
    assert out["metadata"]["provider_endpoint_host"] == "api.fetch.tinyfish.ai"
