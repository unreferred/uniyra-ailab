"""OMNILAB V4.2.3.5 fetch-URL closure tests (offline, deterministic).

Covers the urlguard/fetch_providers patch: query strings allowed+preserved
(host/IP/protocol validated; query text never blocks), fragment stripped,
distinct error codes (INVALID_URL / DNS_RESOLUTION_FAILED / SSRF_BLOCKED /
REDIRECT_BLOCKED / UPSTREAM_403 / UPSTREAM_429 / UPSTREAM_5XX /
CONTENT_TOO_LARGE / UNSUPPORTED_CONTENT_TYPE), TinyFish target passed
unchanged, and secret-free DNS diagnostics.

All DNS is monkeypatched via ``urlguard._resolve_host_sync`` — no real
network. Async provider paths run via the shared ``app_client`` portal loop
(``tests.conftest.call``), matching existing suite style. DB-free except for
the module autouse isolation already required by other suites; this module
itself needs no provider registry DB access.
"""
from __future__ import annotations

import socket
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx
import pytest

from app import config as app_config
from app import urlguard
from app.security import encrypt_secret
from app.services.fetch_providers import (
    STANDARD_FETCH_PROVIDER_ID,
    TINYFISH_FETCH_PROVIDER_ID,
    FetchError,
    StandardFetchProvider,
    TinyFishFetchProvider,
)
from tests.conftest import call

PUBLIC_IP = "93.184.216.34"
PUBLIC_IPV6 = "2606:2800:220:1:248:1893:25c8:1946"


def _public(monkeypatch, *ips: str):
    monkeypatch.setattr(urlguard, "_resolve_host_sync", lambda host: list(ips or [PUBLIC_IP]))


def _standard_doc() -> Dict[str, Any]:
    return {
        "id": STANDARD_FETCH_PROVIDER_ID,
        "name": "Standard HTTP Fetch",
        "provider_type": "standard",
        "enabled": True,
        "configuration": {},
    }


def _tinyfish_doc(key: str = "tf-key-4235") -> Dict[str, Any]:
    return {
        "id": TINYFISH_FETCH_PROVIDER_ID,
        "name": "TinyFish Fetch",
        "provider_type": "tinyfish",
        "enabled": True,
        "configuration": {"base_url": "https://api.fetch.tinyfish.ai"},
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
    """httpx.AsyncClient double capturing GET URLs; serves queued responses."""

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


@pytest.fixture(autouse=True)
def _reset_get_client():
    _GetClient.seen = []
    _GetClient.queue = []
    yield
    _GetClient.seen = []
    _GetClient.queue = []


def _run_standard(app_client, monkeypatch, url: str, responses: List[Any]) -> Any:
    _GetClient.queue = list(responses)
    provider = StandardFetchProvider(_standard_doc())

    async def _go():
        with patch("app.services.fetch_providers.httpx.AsyncClient", _GetClient):
            return await provider.fetch(url)

    return call(app_client, _go)


def _run_standard_expect_error(app_client, monkeypatch, url: str, responses: List[Any]) -> FetchError:
    _GetClient.queue = list(responses)
    provider = StandardFetchProvider(_standard_doc())

    async def _go():
        with patch("app.services.fetch_providers.httpx.AsyncClient", _GetClient):
            try:
                await provider.fetch(url)
            except FetchError as exc:
                return exc
            raise AssertionError("expected FetchError for %s" % url)

    return call(app_client, _go)


# ------------------------------------------------------- urlguard: query handling
def test_public_url_without_query_accepted_preserved(monkeypatch):
    _public(monkeypatch)
    assert urlguard.validate_provider_url("https://example.com/path") == "https://example.com/path"


@pytest.mark.parametrize("url", [
    "https://example.com/search?q=forex",
    "https://example.com/calendar?month=sep.2026",
    "https://example.com/a?id=123&page=2",
])
def test_public_url_with_query_accepted_preserved(monkeypatch, url):
    _public(monkeypatch)
    out = urlguard.validate_provider_url(url)
    assert out == url


def test_fragment_stripped_query_kept(monkeypatch):
    _public(monkeypatch)
    out = urlguard.validate_provider_url("https://example.com/p?a=1#section")
    assert out == "https://example.com/p?a=1"
    assert "#" not in out


def test_localhost_with_query_blocked(monkeypatch):
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("http://localhost:3000/admin?q=1")
    assert excinfo.value.code == "SSRF_BLOCKED"


def test_loopback_with_query_blocked(monkeypatch):
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("http://127.0.0.1/admin?q=1")
    assert excinfo.value.code == "SSRF_BLOCKED"


def test_query_text_pointing_at_loopback_does_not_block(monkeypatch):
    # The query is opaque text; only the real host is resolved/checked.
    _public(monkeypatch)
    out = urlguard.validate_provider_url("https://example.com/x?url=http://127.0.0.1")
    assert out == "https://example.com/x?url=http://127.0.0.1"


def test_private_ip_with_query_blocked(monkeypatch):
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("http://10.0.0.1/a?x=1")
    assert excinfo.value.code == "SSRF_BLOCKED"


def test_metadata_with_query_blocked(monkeypatch):
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("http://169.254.169.254/latest/meta-data?x=1")
    assert excinfo.value.code == "SSRF_BLOCKED"


def test_non_http_scheme_blocked():
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("ftp://example.com/x?q=1")
    assert excinfo.value.code == "INVALID_URL"


def test_embedded_credentials_blocked_with_query(monkeypatch):
    _public(monkeypatch)
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("https://user:pass@example.com/x?a=1")
    assert excinfo.value.code == "INVALID_URL"


# ------------------------------------------------------------------ redirects
def test_validate_redirect_same_and_cross_host_public_allowed(monkeypatch):
    _public(monkeypatch)
    same = urlguard.validate_redirect(
        "https://example.com/a", "https://example.com/b?x=1")
    assert same == "https://example.com/b?x=1"
    cross = urlguard.validate_redirect(
        "https://forexfactory.com/calendar", "https://www.forexfactory.com/calendar")
    assert cross == "https://www.forexfactory.com/calendar"


def test_validate_redirect_private_target_blocked(monkeypatch):
    _public(monkeypatch)
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_redirect("https://example.com/a", "http://10.9.9.9/x")
    assert excinfo.value.code == "SSRF_BLOCKED"


# ------------------------------------------------------- multi-IP / DNS failure
def test_multi_ip_all_public_accepted(monkeypatch):
    monkeypatch.setattr(urlguard, "_resolve_host_sync", lambda host: [PUBLIC_IP, PUBLIC_IPV6])
    out = urlguard.validate_provider_url("https://example.com/x?month=sep.2026")
    assert out == "https://example.com/x?month=sep.2026"


def test_multi_ip_one_private_blocked(monkeypatch):
    monkeypatch.setattr(urlguard, "_resolve_host_sync", lambda host: [PUBLIC_IP, "10.0.0.1"])
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("https://example.com/x?month=sep.2026")
    assert excinfo.value.code == "SSRF_BLOCKED"


def test_dns_failure_code_dns_resolution_failed(monkeypatch):
    def _boom(host):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(urlguard, "_resolve_host_sync", _boom)
    with pytest.raises(urlguard.UrlValidationError) as excinfo:
        urlguard.validate_provider_url("https://nonexistent-4235.invalid/x")
    assert excinfo.value.code == "DNS_RESOLUTION_FAILED"


# ------------------------------------------------------- StandardFetchProvider
def test_standard_query_survives_to_httpx(app_client, monkeypatch):
    _public(monkeypatch)
    out = _run_standard(
        app_client, monkeypatch,
        "https://example.com/calendar?month=sep.2026",
        [_Resp(200, headers={"Content-Type": "text/plain"}, content=b"hello")],
    )
    assert _GetClient.seen and _GetClient.seen[0] == "https://example.com/calendar?month=sep.2026"
    assert out["requested_url"] == "https://example.com/calendar?month=sep.2026"
    assert out["final_url"] == "https://example.com/calendar?month=sep.2026"


def test_standard_403_maps_upstream_403(app_client, monkeypatch):
    _public(monkeypatch)
    exc = _run_standard_expect_error(
        app_client, monkeypatch, "https://example.com/x",
        [_Resp(403, headers={"Content-Type": "text/html"}, content=b"denied")],
    )
    assert exc.error_category == "UPSTREAM_403"
    assert exc.code == "UPSTREAM_403"
    assert exc.upstream_status == 403


def test_standard_429_maps_upstream_429(app_client, monkeypatch):
    _public(monkeypatch)
    exc = _run_standard_expect_error(
        app_client, monkeypatch, "https://example.com/x",
        [_Resp(429, headers={"Content-Type": "text/plain"}, content=b"slow")],
    )
    assert exc.error_category == "UPSTREAM_429"
    assert exc.code == "UPSTREAM_429"
    assert exc.upstream_status == 429


def test_standard_500_maps_upstream_5xx(app_client, monkeypatch):
    _public(monkeypatch)
    exc = _run_standard_expect_error(
        app_client, monkeypatch, "https://example.com/x",
        [_Resp(500, headers={"Content-Type": "text/plain"}, content=b"boom")],
    )
    assert exc.error_category == "UPSTREAM_5XX"
    assert exc.code == "UPSTREAM_5XX"
    assert exc.upstream_status == 500


def test_standard_oversize_maps_content_too_large(app_client, monkeypatch):
    _public(monkeypatch)
    big = b"x" * (500 * 1024 + 1)
    exc = _run_standard_expect_error(
        app_client, monkeypatch, "https://example.com/x",
        [_Resp(200, headers={"Content-Type": "text/plain"}, content=big)],
    )
    assert exc.error_category == "CONTENT_TOO_LARGE"
    assert exc.code == "CONTENT_TOO_LARGE"


def test_standard_image_maps_unsupported_content_type(app_client, monkeypatch):
    _public(monkeypatch)
    png = bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"0" * 64
    exc = _run_standard_expect_error(
        app_client, monkeypatch, "https://example.com/x",
        [_Resp(200, headers={"Content-Type": "image/png"}, content=png)],
    )
    assert exc.error_category == "UNSUPPORTED_CONTENT_TYPE"
    assert exc.code == "UNSUPPORTED_CONTENT_TYPE"


def test_standard_validation_dns_error_maps_dns_resolution_failed(app_client, monkeypatch):
    async def _go():
        provider = StandardFetchProvider(_standard_doc())
        with patch(
            "app.services.fetch_providers.async_validate_provider_url",
            side_effect=urlguard.UrlValidationError(
                "Provider URL host could not be resolved", code="DNS_RESOLUTION_FAILED"),
        ):
            with patch("app.services.fetch_providers.httpx.AsyncClient", _GetClient):
                try:
                    await provider.fetch("https://nonexistent-4235.invalid/x")
                except FetchError as exc:
                    return exc
                raise AssertionError("expected FetchError")

    exc = call(app_client, _go)
    assert exc.error_category == "DNS_RESOLUTION_FAILED"
    assert exc.code == "DNS_RESOLUTION_FAILED"


def test_standard_redirect_hop_revalidated(app_client, monkeypatch):
    _public(monkeypatch)
    redir = _Resp(302, headers={"Location": "https://www.forexfactory.com/calendar?month=sep.2026"})
    final = _Resp(200, headers={"Content-Type": "text/html"},
                  content=b"<html><head><title>T</title></head><body>cal</body></html>")
    out = _run_standard(
        app_client, monkeypatch,
        "https://forexfactory.com/calendar?month=sep.2026",
        [redir, final],
    )
    assert out["requested_url"] == "https://forexfactory.com/calendar?month=sep.2026"
    assert out["final_url"] == "https://www.forexfactory.com/calendar?month=sep.2026"
    assert out["metadata"]["redirect_count"] == 1
    assert _GetClient.seen[0] == "https://forexfactory.com/calendar?month=sep.2026"
    assert _GetClient.seen[1] == "https://www.forexfactory.com/calendar?month=sep.2026"


# ------------------------------------------------------------------ TinyFish
def test_tinyfish_target_passed_unchanged_with_query(app_client, monkeypatch):
    _public(monkeypatch)
    captured: Dict[str, Any] = {}

    class _PostClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers=None, json=None, **kwargs):
            captured["json"] = json
            return httpx.Response(
                200, json={"results": [{"text": "cal", "final_url": json["urls"][0]}]})

    provider = TinyFishFetchProvider(_tinyfish_doc())

    async def _go():
        with patch("app.services.fetch_providers.httpx.AsyncClient", _PostClient):
            return await provider.fetch("https://example.com/calendar?month=sep.2026")

    out = call(app_client, _go)
    assert captured["json"]["urls"][0] == "https://example.com/calendar?month=sep.2026"
    assert out["final_url"] == "https://example.com/calendar?month=sep.2026"


def test_tinyfish_target_dns_failure_is_never_local_dns(app_client, monkeypatch):
    """V4.2.3.6 contract: a REMOTE target with an unresolvable public hostname is
    forwarded unchanged and can no longer surface as an OmniLab target DNS
    error. Local resolution is never attempted for the target."""
    calls = {"target": 0}

    def _resolver(host):
        if "nonexistent-4235" in host:
            calls["target"] += 1
            raise socket.gaierror(-2, "Name or service not known")
        return [PUBLIC_IP]

    monkeypatch.setattr(urlguard, "_resolve_host_sync", _resolver)
    captured: Dict[str, Any] = {}

    class _PostClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            return httpx.Response(
                200, json={"results": [{"text": "ok", "final_url": json["urls"][0]}]})

    provider = TinyFishFetchProvider(_tinyfish_doc())

    async def _go():
        with patch("app.services.fetch_providers.httpx.AsyncClient", _PostClient):
            return await provider.fetch("https://nonexistent-4235.invalid/x?a=1")

    out = call(app_client, _go)
    assert captured["json"]["urls"] == ["https://nonexistent-4235.invalid/x?a=1"]
    assert out["egress_mode"] == "REMOTE"
    assert out["final_url"] == "https://nonexistent-4235.invalid/x?a=1"
    # OmniLab never performed local DNS resolution for the remote target.
    assert calls["target"] == 0


# ------------------------------------------------------- diagnostics hygiene
def test_resolve_host_diagnostics_shape_never_raises_never_echoes_credentials(monkeypatch):
    real = socket.getaddrinfo

    def _fake(host, port, *args, **kwargs):
        assert "secret" not in str(host).lower(), "credentials leaked into resolver input"
        return real("localhost", port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _fake)
    diag = urlguard.resolve_host_diagnostics("https://user:s3cret@example.com/x?month=sep.2026")
    for key in ("hostname", "addresses", "address_count", "families", "validation"):
        assert key in diag
    blob = str(diag)
    assert "s3cret" not in blob
    assert "user:s3cret" not in blob

    def _boom(host, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    diag2 = urlguard.resolve_host_diagnostics("https://example.com/x?month=sep.2026")
    assert diag2["validation"] == "DNS_RESOLUTION_FAILED"
    assert diag2["addresses"] == []
    assert diag2["address_count"] == 0
