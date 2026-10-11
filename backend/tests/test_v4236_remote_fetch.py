"""OMNILAB V4.2.3.6 remote-fetch trust-boundary closure tests (offline).

Proves the LOCAL vs REMOTE egress split:

* STANDARD FETCH (LOCAL_EGRESS) keeps full local SSRF enforcement: localhost,
  literal RFC1918, DNS-resolved private targets and unsafe redirect hops are
  blocked.
* TINYFISH FETCH (REMOTE_EGRESS) validates the *target* syntactically/policy
  only and NEVER resolves ordinary public hostnames locally, while still fully
  SSRF-validating its own provider endpoint.

All network access is mocked; DNS is monkeypatched. Async provider paths run on
the shared ``app_client`` portal loop via ``tests.conftest.call``.
"""
from __future__ import annotations

import socket
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx
import pytest

from app import urlguard
from app.security import encrypt_secret
from app.services.fetch_providers import (
    TINYFISH_FETCH_ENDPOINT,
    TINYFISH_FETCH_PROVIDER_ID,
    STANDARD_FETCH_PROVIDER_ID,
    FetchError,
    StandardFetchProvider,
    TinyFishFetchProvider,
)
from app.services.search_providers import (
    TINYFISH_SEARCH_ENDPOINT,
    TinyFishSearchProvider,
)
from tests.conftest import call

PUBLIC_IP = "93.184.216.34"
FOREX_URL = "https://www.forexfactory.com/calendar?month=this"
FOREX_RANGE_URL = (
    "https://www.forexfactory.com/calendar?range=sep14.2026-sep20.2026"
)


def _standard_doc() -> Dict[str, Any]:
    return {
        "id": STANDARD_FETCH_PROVIDER_ID,
        "name": "Standard HTTP Fetch",
        "provider_type": "standard",
        "enabled": True,
        "configuration": {},
    }


def _tinyfish_doc(key: str = "tf-key-4236") -> Dict[str, Any]:
    return {
        "id": TINYFISH_FETCH_PROVIDER_ID,
        "name": "TinyFish Fetch",
        "provider_type": "tinyfish",
        "enabled": True,
        # Deliberately hostile: the adapter must ignore this.
        "configuration": {"base_url": "https://evil-user-config.example"},
        "api_key_enc": encrypt_secret(key),
        "credential_id": None,
        "credential_owner_id": None,
    }


class _Resp:
    """Minimal httpx.Response stand-in for StandardFetchProvider.get flows."""

    def __init__(self, status: int, *, headers: Optional[Dict[str, str]] = None,
                 content: bytes = b"", encoding: str = "utf-8"):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.encoding = encoding


class _GetClient:
    """AsyncClient double capturing GET URLs; serves queued responses."""

    seen: List[str] = []
    queue: List[Any] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        type(self).seen.append(str(url))
        assert type(self).queue, "no queued response for GET %s" % url
        return type(self).queue.pop(0)


class _PostCaptureClient:
    """AsyncClient double capturing the POSTed JSON body for TinyFish fetch."""

    captured: Dict[str, Any] = {}
    posts: List[str] = []
    result_text: str = "forex calendar"

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
        urls = (json or {}).get("urls") or []
        return httpx.Response(
            200,
            json={
                "results": [{
                    "text": type(self).result_text,
                    "final_url": urls[0] if urls else None,
                }]
            },
        )


class _GetCaptureClient:
    """AsyncClient double capturing GET query params for TinyFish search."""

    captured: Dict[str, Any] = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None, **kwargs):
        type(self).captured["url"] = str(url)
        type(self).captured["params"] = dict(params or {})
        return httpx.Response(
            200,
            json={"results": [{
                "title": "T", "url": "https://example.com/x",
                "snippet": "S", "position": 1,
            }]},
        )


@pytest.fixture(autouse=True)
def _reset_clients():
    _GetClient.seen = []
    _GetClient.queue = []
    _PostCaptureClient.captured = {}
    _PostCaptureClient.posts = []
    _GetCaptureClient.captured = {}
    yield


def _resolver_public(monkeypatch, *ips: str):
    monkeypatch.setattr(
        urlguard, "_resolve_host_sync", lambda host: list(ips or [PUBLIC_IP])
    )


# ============================================================ REMOTE urlguard
def test_remote_accepts_public_hostname_without_any_local_dns(monkeypatch):
    calls: List[str] = []

    def _must_not_resolve(host):
        calls.append(host)
        raise AssertionError("REMOTE validation must not resolve %r locally" % host)

    monkeypatch.setattr(urlguard, "_resolve_host_sync", _must_not_resolve)

    out = urlguard.validate_remote_target_url(FOREX_URL)
    assert out == FOREX_URL
    assert calls == []


def test_remote_query_preserved_fragment_stripped(monkeypatch):
    calls: List[str] = []
    monkeypatch.setattr(
        urlguard, "_resolve_host_sync", lambda host: calls.append(host) or [PUBLIC_IP]
    )
    out = urlguard.validate_remote_target_url(
        "https://example.com/calendar?month=this&x=1#section"
    )
    assert out == "https://example.com/calendar?month=this&x=1"
    assert "#" not in out
    assert calls == []


def test_remote_never_produces_dns_resolution_failed(monkeypatch):
    def _boom(host):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(urlguard, "_resolve_host_sync", _boom)
    # The resolver would fail, but REMOTE validation accepts the public host.
    assert urlguard.validate_remote_target_url(FOREX_URL) == FOREX_URL
    assert urlguard.validate_remote_target_url(FOREX_RANGE_URL) == FOREX_RANGE_URL


def test_remote_literal_loopback_blocked():
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_remote_target_url("http://127.0.0.1/admin?a=1")
    assert excinfo.value.code == "SSRF_BLOCKED"


def test_remote_literal_rfc1918_blocked():
    for url in ("http://10.0.0.1/a", "http://192.168.1.1/b?x=1"):
        with pytest.raises(urlguard.UrlValidationError) as excinfo:
            urlguard.validate_remote_target_url(url)
        assert excinfo.value.code == "SSRF_BLOCKED"


def test_remote_metadata_literal_blocked():
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_remote_target_url("http://169.254.169.254/latest/meta-data")
    assert excinfo.value.code == "SSRF_BLOCKED"


def test_remote_localhost_name_blocked():
    for url in ("http://localhost:3000/x", "http://api.localhost/x"):
        with pytest.raises(urlguard.UrlValidationError) as excinfo:
            urlguard.validate_remote_target_url(url)
        assert excinfo.value.code == "SSRF_BLOCKED"


def test_remote_embedded_credentials_blocked():
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_remote_target_url("https://user:pass@example.com/x")
    assert excinfo.value.code == "INVALID_URL"


def test_remote_malformed_scheme_is_invalid_url():
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_remote_target_url("ftp://example.com/x")
    assert excinfo.value.code == "INVALID_URL"


def test_remote_allow_local_flag_permits_loopback(monkeypatch):
    monkeypatch.setattr(urlguard, "_allow_local_enabled", lambda flag: True)
    out = urlguard.validate_remote_target_url("http://127.0.0.1/x?a=1", allow_local=True)
    assert out == "http://127.0.0.1/x?a=1"


# ============================================================== STANDARD LOCAL
def test_standard_egress_is_local():
    assert StandardFetchProvider(_standard_doc()).egress_mode == "LOCAL"


def test_standard_localhost_blocked(monkeypatch):
    _resolver_public(monkeypatch)
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("http://localhost:3000/admin")
    assert excinfo.value.code == "SSRF_BLOCKED"


def test_standard_literal_rfc1918_blocked(monkeypatch):
    for url in ("http://10.0.0.1/x", "http://192.168.1.1/x"):
        with pytest.raises(urlguard.UrlValidationError) as excinfo:
            urlguard.validate_provider_url(url)
        assert excinfo.value.code == "SSRF_BLOCKED"


def test_standard_dns_resolved_private_target_blocked(monkeypatch):
    monkeypatch.setattr(urlguard, "_resolve_host_sync", lambda host: ["10.0.0.1"])
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("https://public-looking.example/x")
    assert excinfo.value.code == "SSRF_BLOCKED"


def test_standard_unsafe_redirect_hop_blocked(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    _GetClient.queue = [
        _Resp(302, headers={"Location": "http://10.0.0.1/internal"}),
    ]
    provider = StandardFetchProvider(_standard_doc())

    async def _go():
        with patch("app.services.fetch_providers.httpx.AsyncClient", _GetClient):
            try:
                await provider.fetch("https://example.com/start")
            except FetchError as exc:
                return exc
            raise AssertionError("expected FetchError on unsafe redirect hop")

    exc = call(app_client, _go)
    assert exc.code == "SSRF_BLOCKED"
    assert exc.status_code == 403


# ============================================================ TINYFISH REMOTE
def test_tinyfish_fetch_egress_is_remote():
    provider = TinyFishFetchProvider(_tinyfish_doc())
    assert provider.egress_mode == "REMOTE"
    assert provider._get_base_url() == TINYFISH_FETCH_ENDPOINT


def test_tinyfish_canonical_endpoint_ignores_user_config_base_url():
    doc = _tinyfish_doc()
    doc["configuration"] = {"base_url": "https://attacker.example/redirect"}
    provider = TinyFishFetchProvider(doc)
    assert provider._get_base_url() == TINYFISH_FETCH_ENDPOINT


def test_tinyfish_targets_accepted_without_local_dns(app_client, monkeypatch):
    # Local resolver cannot see the target; endpoint resolves normally.
    monkeypatch.setattr(
        urlguard, "_resolve_host_sync", lambda host: [PUBLIC_IP]
    )
    for target in (FOREX_URL, FOREX_RANGE_URL):
        _PostCaptureClient.captured = {}
        provider = TinyFishFetchProvider(_tinyfish_doc())

        async def _go(t=target):
            with patch(
                "app.services.fetch_providers.httpx.AsyncClient", _PostCaptureClient
            ):
                return await provider.fetch(t)

        out = call(app_client, _go)
        assert _PostCaptureClient.captured["json"]["urls"] == [target]
        assert _PostCaptureClient.captured["url"] == TINYFISH_FETCH_ENDPOINT
        assert out["egress_mode"] == "REMOTE"
        assert out["target_url"] == target
        assert out["metadata"]["provider_endpoint_host"] == "api.fetch.tinyfish.ai"
        assert out["metadata"]["target_url"] == target


def test_tinyfish_literal_loopback_and_rfc1918_blocked(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    provider = TinyFishFetchProvider(_tinyfish_doc())
    for target in (
        "http://127.0.0.1/admin",
        "http://10.0.0.1/internal",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost:3000/x",
    ):
        async def _go(t=target):
            return await provider.fetch(t)

        with pytest.raises(FetchError) as excinfo:
            call(app_client, _go)
        assert excinfo.value.code == "SSRF_BLOCKED", target


def test_tinyfish_embedded_credentials_blocked(app_client, monkeypatch):
    _resolver_public(monkeypatch)
    provider = TinyFishFetchProvider(_tinyfish_doc())

    async def _go():
        return await provider.fetch("https://user:pass@example.com/x")

    with pytest.raises(FetchError) as excinfo:
        call(app_client, _go)
    assert excinfo.value.code == "INVALID_URL"


def test_tinyfish_provider_endpoint_is_full_validated(app_client, monkeypatch):
    # A metadata/localhost endpoint supplied via env override must be rejected
    # by FULL endpoint validation before OmniLab contacts it.
    monkeypatch.setenv("TINYFISH_FETCH_BASE_URL", "http://169.254.169.254")
    captured_calls: List[str] = []

    class _NeverClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            captured_calls.append("post")
            raise AssertionError("must not contact a blocked endpoint")

    provider = TinyFishFetchProvider(_tinyfish_doc())

    async def _go():
        with patch(
            "app.services.fetch_providers.httpx.AsyncClient", _NeverClient
        ):
            return await provider.fetch("https://example.com/x")

    with pytest.raises(FetchError) as excinfo:
        call(app_client, _go)
    assert excinfo.value.code == "SSRF_BLOCKED"
    assert excinfo.value.provider == "tinyfish"
    assert captured_calls == []


# ================================================== CRITICAL ForexFactory test
def test_critical_forexfactory_forwarded_despite_local_dns_failure(
    app_client, monkeypatch
):
    """getaddrinfo('www.forexfactory.com') raises, yet the URL is still POSTed."""
    resolver_calls: List[str] = []

    def _fake_getaddrinfo(host, *args, **kwargs):
        resolver_calls.append(str(host))
        if str(host) == "www.forexfactory.com":
            raise socket.gaierror(-2, "Name or service not known")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    provider = TinyFishFetchProvider(_tinyfish_doc())

    async def _go():
        with patch(
            "app.services.fetch_providers.httpx.AsyncClient", _PostCaptureClient
        ):
            return await provider.fetch(FOREX_URL)

    out = call(app_client, _go)
    assert _PostCaptureClient.captured["json"]["urls"] == [FOREX_URL]
    assert _PostCaptureClient.captured["url"] == TINYFISH_FETCH_ENDPOINT
    assert out["text"] == "forex calendar"
    assert out["final_url"] == FOREX_URL
    # The target host was never passed to the local resolver.
    assert "www.forexfactory.com" not in resolver_calls


# ======================================== location / language (search, REMOTE)
def _search_doc(location: Optional[str] = None, language: Optional[str] = None):
    cfg: Dict[str, Any] = {}
    if location is not None:
        cfg["location"] = location
    if language is not None:
        cfg["language"] = language
    return {
        "id": "prov-search-tinyfish",
        "name": "TinyFish Search",
        "provider_type": "tinyfish",
        "enabled": True,
        "configuration": cfg,
        "api_key_enc": encrypt_secret("tf-search-key-4236"),
        "credential_id": None,
        "credential_owner_id": None,
    }


def test_search_egress_and_default_location_language(app_client, monkeypatch):
    monkeypatch.setattr(urlguard, "_resolve_host_sync", lambda host: [PUBLIC_IP])
    provider = TinyFishSearchProvider(_search_doc())

    async def _go():
        with patch("app.services.search_providers.httpx.AsyncClient", _GetCaptureClient):
            return await provider.search("forex news")

    out = call(app_client, _go)
    assert provider.egress_mode == "REMOTE"
    assert out["egress_mode"] == "REMOTE"
    assert out["location"] == "ID"
    assert out["language"] == "id"
    assert _GetCaptureClient.captured["url"] == TINYFISH_SEARCH_ENDPOINT
    assert _GetCaptureClient.captured["params"] == {
        "query": "forex news", "location": "ID", "language": "id",
    }


def test_search_tool_arguments_override_valve_defaults(app_client, monkeypatch):
    monkeypatch.setattr(urlguard, "_resolve_host_sync", lambda host: [PUBLIC_IP])
    provider = TinyFishSearchProvider(_search_doc(location="JP", language="ja"))

    async def _go():
        with patch("app.services.search_providers.httpx.AsyncClient", _GetCaptureClient):
            return await provider.search("forex", location="US", language="en")

    out = call(app_client, _go)
    assert out["location"] == "US"
    assert out["language"] == "en"
    assert _GetCaptureClient.captured["params"]["location"] == "US"
    assert _GetCaptureClient.captured["params"]["language"] == "en"


def test_search_valve_schema_exposes_location_language():
    from app.services import valves

    schema = valves.search_provider_schema("tinyfish")
    assert schema["location"].default == "ID"
    assert schema["language"].default == "id"
    # base_url must never be a valve.
    assert "base_url" not in schema


# ============================================== provider trace metadata helper
def test_provider_trace_metadata_is_secret_free_and_informative():
    from app.services.execution import _provider_trace_metadata

    doc = {
        "execution_status": "success",
        "output": {
            "provider": "prov-fetch-tinyfish",
            "provider_type": "tinyfish",
            "egress_mode": "REMOTE",
            "provider_status": 200,
            "target_url": FOREX_URL,
            "duration_ms": 42,
            "text": "SECRET-PAGE-TEXT",
            "metadata": {
                "provider_endpoint_host": "api.fetch.tinyfish.ai",
                "egress_mode": "REMOTE",
                "provider_status": 200,
                "duration_ms": 42,
                "target_url": FOREX_URL,
            },
        },
    }
    meta = _provider_trace_metadata(doc)
    assert meta["egress_mode"] == "REMOTE"
    assert meta["provider_endpoint_host"] == "api.fetch.tinyfish.ai"
    assert meta["target_url"] == FOREX_URL
    assert "text" not in meta
    assert "SECRET-PAGE-TEXT" not in str(meta)


def test_provider_trace_metadata_error_shape():
    from app.services.execution import _provider_trace_metadata

    doc = {
        "execution_status": "failed",
        "error": {"code": "FETCH_AUTH_ERROR", "provider": "tinyfish",
                  "upstream_status": 401, "message": "nope"},
    }
    meta = _provider_trace_metadata(doc)
    assert meta["provider"] == "tinyfish"
    assert meta["error_code"] == "FETCH_AUTH_ERROR"
    assert meta["upstream_status"] == 401
