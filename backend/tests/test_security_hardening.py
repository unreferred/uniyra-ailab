"""Security hardening tests: SSRF guard, safe errors, auth rotation/CSRF, and
cross-project link rejection.

Offline only. Throwaway users/projects/experiments are purged by the fixtures;
production data is never deleted.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

import app.config as config
from app import repository as repo
from app import urlguard
from app import seed as seed_module
from app.errors import classify_exception, classify_http_status, make_error, safe_error_message
from app.security import decode_token

from _support import create_project


# ================================================================== SSRF guard
SCHEME_AND_HOST_REJECTIONS = [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/",
    "ws://example.com/socket",
    "wss://example.com/socket",
    "data:text/plain,hello",
    "http://user:pass@api.openai.com/v1",
    "http://localhost:8000",
    "http://localhost",
    "http://127.0.0.1",
    "http://127.0.0.1:11434",
    "http://[::1]",
    "http://169.254.169.254",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.100.100.200",
    "http://[fd00:ec2::254]",
    "http://10.0.0.1",
    "http://172.16.0.1",
    "http://172.31.255.255",
    "http://192.168.1.1",
    "http://[fc00::1]",
    "http://169.254.1.1",
    "http://[fe80::1]",
    "http://0.0.0.0",
    "http://224.0.0.1",
    "http://240.0.0.1",
]


@pytest.mark.parametrize("url", SCHEME_AND_HOST_REJECTIONS)
def test_urlguard_rejects_non_public_targets(url):
    with pytest.raises(urlguard.UrlValidationError):
        urlguard.validate_provider_url(url)
    assert urlguard.is_url_allowed(url) is False


def test_urlguard_allows_public_https(monkeypatch):
    monkeypatch.setattr(urlguard, "_resolve_host_sync", lambda host: ["93.184.216.34"])
    assert urlguard.validate_provider_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert urlguard.is_url_allowed("https://api.openai.com/v1") is True
    assert urlguard.SAFE_REDIRECTS is False


def test_urlguard_allow_local_permits_gateway_but_not_metadata(monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_ALLOW_LOCAL_URLS", True)
    assert urlguard.validate_provider_url("http://localhost:11434") == "http://localhost:11434"
    assert urlguard.validate_provider_url("http://10.0.0.1") == "http://10.0.0.1"
    for metadata_url in ("http://169.254.169.254", "http://100.100.100.200", "http://[fd00:ec2::254]"):
        with pytest.raises(urlguard.UrlValidationError) as err:
            urlguard.validate_provider_url(metadata_url)
        assert "metadata" in err.value.message.lower()


def test_urlguard_allow_local_still_blocks_metadata_hostname(monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_ALLOW_LOCAL_URLS", True)
    monkeypatch.setattr(urlguard, "_resolve_host_sync", lambda host: ["169.254.169.254"])
    with pytest.raises(urlguard.UrlValidationError) as err:
        urlguard.validate_provider_url("http://metadata.evil.test")
    assert "metadata" in err.value.message.lower()


def test_urlguard_allowlist_permits_explicit_host_only(monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_URL_ALLOWLIST", ["169.254.169.254"])
    assert urlguard.validate_provider_url("http://169.254.169.254") == "http://169.254.169.254"
    with pytest.raises(urlguard.UrlValidationError):
        urlguard.validate_provider_url("http://100.100.100.200")

    monkeypatch.setattr(config, "PROVIDER_URL_ALLOWLIST", ["localhost:11434"])
    assert urlguard.validate_provider_url("http://localhost:11434") == "http://localhost:11434"
    with pytest.raises(urlguard.UrlValidationError):
        urlguard.validate_provider_url("http://localhost:8000")


def test_urlguard_cross_host_redirect_detection():
    assert urlguard.is_cross_host_redirect("https://a.example/v1", "https://b.example/v1") is True
    assert urlguard.is_cross_host_redirect("https://a.example/v1", "https://a.example/other") is False
    # Default port normalization: https 443 is the same host:port.
    assert urlguard.is_cross_host_redirect("https://a.example/v1", "https://a.example:443/x") is False


# ================================================================== safe errors
def test_make_error_rejects_unknown_category():
    with pytest.raises(ValueError):
        make_error("X", "NOT_A_CATEGORY", "boom")
    with pytest.raises(ValueError):
        make_error("X", "PROVIDER", "boom", details_safe=["not", "a", "dict"])


def test_make_error_scrubs_credential_like_details():
    err = make_error(
        "UPSTREAM",
        "PROVIDER",
        "Provider request failed",
        details_safe={
            "api_key": "sk-secret-value",
            "nested": {"authorization": "Bearer abc", "cookie": "sid=1", "safe": 7},
            "access_token": "tok",
            "password": "hunter2",
            "model": "gpt-x",
            "status": 503,
        },
    )
    details = err["details_safe"]
    assert details["nested"] == {"safe": 7}
    assert details["model"] == "gpt-x"
    assert details["status"] == 503
    blob = json.dumps(details)
    for secret in ("sk-secret-value", "Bearer abc", "sid=1", "tok", "hunter2"):
        assert secret not in blob


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, "AUTH", False),
        (403, "AUTH", False),
        (429, "RATE_LIMIT", True),
        (408, "TIMEOUT", True),
        (504, "TIMEOUT", True),
        (404, "MODEL_UNAVAILABLE", False),
        (400, "VALIDATION", False),
        (422, "VALIDATION", False),
        (500, "PROVIDER", True),
        (503, "PROVIDER", True),
        (418, "PROVIDER", False),
    ],
)
def test_classify_http_status_mapping(status, category, retryable):
    err = classify_http_status(status, source="pytest")
    assert err["category"] == category
    assert err["retryable"] is retryable
    assert err["upstream_status"] == status
    assert "sk-" not in json.dumps(err)


def test_classify_exception_mapping_is_secret_free():
    cases = [
        (asyncio.CancelledError(), "CANCELLED", False),
        (httpx.ReadTimeout("slow"), "TIMEOUT", True),
        (httpx.ConnectError("refused"), "NETWORK", True),
        (PermissionError("nope"), "AUTH", False),
        (json.JSONDecodeError("bad", "", 0), "MALFORMED_RESPONSE", False),
        (ValueError("api key sk-live-secret was rejected"), "VALIDATION", False),
        (RuntimeError("unexpected"), "INTERNAL", False),
    ]
    for exc, category, retryable in cases:
        err = classify_exception(exc, source="pytest")
        assert err["category"] == category
        assert err["retryable"] is retryable
        blob = json.dumps(err)
        assert "sk-live-secret" not in blob
        assert "api key" not in blob


def test_safe_error_message_strips_identity_and_raw_text():
    err = make_error("MODEL_DISABLED", "MODEL_UNAVAILABLE", "Model 'SecretModel' is disabled")
    message = safe_error_message(err)
    assert "SecretModel" not in message
    assert "MODEL_UNAVAILABLE" in message
    # Non-structured errors never surface raw upstream text.
    assert safe_error_message("upstream body with sk-secret") is None



# ================================================================== auth
def _register(client, email, password="testpass123", name="Pytest User"):
    return client.post("/api/auth/register", json={"email": email, "password": password, "name": name})


def _decode_jti(token):
    payload = decode_token(token)
    assert payload and payload.get("jti")
    return payload["jti"]


def test_refresh_rotation_revokes_old_jti_and_family(app_client, auth_env):
    email = auth_env["email"]
    r = _register(app_client, email)
    assert r.status_code == 200, r.text
    uid = r.json()["id"]

    r = app_client.post("/api/auth/login", json={"email": email, "password": "testpass123"})
    assert r.status_code == 200, r.text
    login_refresh = app_client.cookies.get("refresh_token")
    csrf = app_client.cookies.get("csrf_token")
    old_jti = _decode_jti(login_refresh)

    r = app_client.post("/api/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    new_jti = _decode_jti(app_client.cookies.get("refresh_token"))
    assert new_jti != old_jti

    async def _sessions():
        return await repo.auth_sessions.list(
            {"owner_id": uid}, sort_field="created_at", descending=False
        )

    sessions = app_client.portal.call(_sessions)
    old = [s for s in sessions if s["jti"] == old_jti]
    assert old, "login session was not persisted"
    assert old[0]["revoked_at"] is not None
    assert old[0]["rotated_to"] == new_jti

    # Reusing the rotated token is rejected and revokes the whole family.
    app_client.cookies.clear()
    r = app_client.post("/api/auth/refresh", cookies={"refresh_token": login_refresh})
    assert r.status_code == 401
    assert "reuse" in r.json()["detail"].lower()
    sessions = app_client.portal.call(_sessions)
    assert sessions and all(s.get("revoked_at") for s in sessions)


def test_logout_revokes_session_and_clears_cookies(app_client, auth_env):
    email = auth_env["email"]
    r = _register(app_client, email)
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    jti = _decode_jti(app_client.cookies.get("refresh_token"))
    csrf = app_client.cookies.get("csrf_token")

    r = app_client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    async def _session():
        return await repo.auth_sessions.find_one({"jti": jti, "user_id": uid})

    session = app_client.portal.call(_session)
    assert session and session["revoked_at"] is not None
    assert app_client.cookies.get("access_token") is None
    assert app_client.cookies.get("refresh_token") is None


def test_admin_seed_is_noop_when_disabled(app_client, monkeypatch):
    email = f"pytest-seed-{uuid.uuid4().hex}@example.com"
    monkeypatch.setattr(config, "ENABLE_ADMIN_SEED", False)
    monkeypatch.setattr(config, "ADMIN_EMAIL", email)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "supersecret")

    async def _seed_and_lookup():
        await seed_module.seed_admin()
        return await repo.users.find_one({"email": email})

    assert app_client.portal.call(_seed_and_lookup) is None

    # Even opted in, missing credentials must never create an account.
    monkeypatch.setattr(config, "ENABLE_ADMIN_SEED", True)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", None)
    assert app_client.portal.call(_seed_and_lookup) is None


# ================================================================== CSRF
def test_csrf_cookie_auth_enforcement(app_client, auth_env):
    email = auth_env["email"]
    r = _register(app_client, email)
    assert r.status_code == 200, r.text
    csrf = app_client.cookies.get("csrf_token")
    assert csrf

    # Cookie-auth unsafe request without the header is rejected.
    r = app_client.post("/api/projects", json={"name": "pytest-csrf-blocked"})
    assert r.status_code == 403

    # Wrong token is rejected too.
    r = app_client.post(
        "/api/projects", json={"name": "pytest-csrf-wrong"},
        headers={"X-CSRF-Token": "not-the-token"},
    )
    assert r.status_code == 403

    # Matching double-submit token succeeds.
    r = app_client.post(
        "/api/projects", json={"name": "pytest-csrf-ok"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text

    # Safe methods never require the header.
    assert app_client.get("/api/projects").status_code == 200

    # Bearer-only clients carry no cookies and are not CSRF-susceptible.
    access = app_client.cookies.get("access_token")
    app_client.cookies.clear()
    r = app_client.post(
        "/api/projects", json={"name": "pytest-bearer-ok"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200, r.text


# ================================================================== cross-project links
def test_cross_project_link_rejection(app_client, as_user):
    owner = as_user["id"]

    async def _setup():
        project_a = await create_project(owner, "pytest-proj-a")
        project_b = await create_project(owner, "pytest-proj-b")
        exp_b = {"id": f"pytest-exp-{uuid.uuid4().hex}", "owner_id": owner,
                 "project_id": project_b, "title": "exp-b", "status": "DRAFT"}
        await repo.experiments.insert(exp_b)
        ds_a = {"id": f"pytest-ds-{uuid.uuid4().hex}", "owner_id": owner,
                "project_id": project_a, "name": "ds-a", "created_at": "2026-01-01T00:00:00+00:00"}
        await repo.datasets.insert(ds_a)
        run_b = {
            "id": f"pytest-run-{uuid.uuid4().hex}", "owner_id": owner,
            "project_id": project_b, "experiment_id": exp_b["id"],
            "status": "success", "created_at": "2026-01-01T00:00:00+00:00",
        }
        await repo.runs.insert(run_b)
        research_a = {
            "id": f"pytest-res-{uuid.uuid4().hex}", "owner_id": owner,
            "project_id": project_a, "title": "res-a", "status": "active",
            "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
        }
        await repo.research_sessions.insert(research_a)
        return project_a, exp_b["id"], ds_a["id"], run_b["id"], research_a["id"]

    project_a, exp_b_id, ds_a_id, run_b_id, research_a_id = app_client.portal.call(_setup)

    # Dataset attach across projects -> 400.
    r = app_client.post(
        f"/api/datasets/{ds_a_id}/attach-experiment", json={"experiment_id": exp_b_id}
    )
    assert r.status_code == 400, r.text

    # Finding evidence pointing at another project's run -> 400.
    r = app_client.post("/api/findings", json={
        "project_id": project_a,
        "title": "pytest-finding",
        "evidence": [{"type": "run", "ref_id": run_b_id}],
    })
    assert r.status_code == 400, r.text

    # Research link to another project's experiment -> 400.
    r = app_client.patch(f"/api/research/{research_a_id}", json={
        "related_experiment_ids": [exp_b_id],
    })
    assert r.status_code == 400, r.text

    # Sanity: the same owner may link same-project resources.
    async def _same_project_experiment():
        exp_a = {"id": f"pytest-exp-{uuid.uuid4().hex}", "owner_id": owner,
                 "project_id": project_a, "title": "exp-a", "status": "DRAFT"}
        await repo.experiments.insert(exp_a)
        return exp_a["id"]

    exp_a_id = app_client.portal.call(_same_project_experiment)
    r = app_client.patch(f"/api/research/{research_a_id}", json={
        "related_experiment_ids": [exp_a_id],
    })
    assert r.status_code == 200, r.text
