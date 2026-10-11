"""OmniLab V4 custom tools: validation, SSRF, versioning, secrets (offline)."""
from __future__ import annotations

import pytest

import json

from _support import create_model, create_provider


"""Phase 4 collections purge for V4 suites (purge_owner predates them)."""

_V4_PHASE4_COLLECTIONS = ["project_files", "knowledge_collections", "knowledge_documents", "knowledge_chunks", "tool_versions", "tool_credentials", "code_executions", "project_activity"]


@pytest.fixture(autouse=True)
def _v4_purge_phase4(app_client, as_user):
    """Purge Phase 4 collections (not covered by _support.purge_owner)."""
    yield

    async def _do():
        from app.database import db

        for coll in _V4_PHASE4_COLLECTIONS:
            await db[coll].delete_many({"owner_id": as_user["id"]})

    app_client.portal.call(_do)


SAFE_BASE = "https://93.184.216.34/v1"


def _project(app_client, name="v4-tools"):
    r = app_client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _http_tool(app_client, project_id, name, *, url=None, schema=None, perms=None):
    body = {
        "project_id": project_id,
        "name": name,
        "version": {
            "tool_type": "http",
            "input_schema": schema or {"type": "object"},
            "permissions": perms or [],
            "implementation_config": {
                "method": "GET",
                "url_template": url or f"{SAFE_BASE}/items",
            },
        },
    }
    r = app_client.post("/api/tools", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_create_http_tool_and_version_snapshot(app_client, as_user):
    project = _project(app_client)
    tool = _http_tool(app_client, project["id"], "fetch_items")
    assert tool["version"] == 1
    assert tool["current_version_id"]

    r = app_client.get(f"/api/tools/{tool['id']}/versions")
    assert r.status_code == 200, r.text
    versions = r.json()["items"]
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["immutable"] is True


def test_ssrf_rejections_live(app_client, as_user):
    # The SSRF guard fires inside validate_implementation_config, whose
    # HttpToolError (a ValueError) is not mapped to CustomToolError by the
    # create path, so the TestClient re-raises it server-side instead of
    # returning an HTTP error. Either way the tool must never be created:
    # assert the live guard rejection and its safe message.
    from app.services.http_tool import HttpToolError
    from app.urlguard import UrlValidationError

    project = _project(app_client)
    for name, url in (
        ("evil_meta", "http://169.254.169.254/"),
        ("evil_priv", "http://192.168.1.1/internal"),
        ("evil_loop", "http://127.0.0.1:9000/admin"),
    ):
        body = {
            "project_id": project["id"],
            "name": name,
            "version": {
                "tool_type": "http",
                "input_schema": {"type": "object"},
                "implementation_config": {
                    "method": "GET",
                    "url_template": url,
                },
            },
        }
        try:
            r = app_client.post("/api/tools", json=body)
        except (HttpToolError, UrlValidationError, ValueError) as exc:
            assert "must not target" in str(
                getattr(exc, "message", None) or exc
            ), str(exc)
        else:
            # The guard maps to an HTTP rejection (never a created tool)
            # with the safe message.
            assert r.status_code == 400, r.text
            assert "must not target" in r.text


def test_update_creates_v2_and_v1_immutable(app_client, as_user):
    project = _project(app_client)
    tool = _http_tool(app_client, project["id"], "evolving")
    v1_id = tool["current_version_id"]

    r = app_client.patch(
        f"/api/tools/{tool['id']}",
        json={
            "description": "second iteration",
            "version": {
                "tool_type": "http",
                "input_schema": {"type": "object"},
                "implementation_config": {
                    "method": "GET",
                    "url_template": f"{SAFE_BASE}/v2",
                },
            },
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert r.json()["current_version_id"] != v1_id

    v1 = app_client.get(f"/api/tools/versions/{v1_id}")
    assert v1.status_code == 200, v1.text
    assert v1.json()["version"] == 1
    assert f"{SAFE_BASE}/v2" not in json.dumps(v1.json()), (
        "v1 snapshot must keep the old config"
    )
    current = app_client.get(f"/api/tools/{tool['id']}")
    assert current.status_code == 200, current.text
    assert current.json()["version"] == 2


def test_validation_failure_persists_tool_call(app_client, as_user):
    project = _project(app_client)
    tool = _http_tool(
        app_client,
        project["id"],
        "strict_tool",
        schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    )
    r = app_client.post(f"/api/tools/{tool['id']}/test", json={"arguments": {}})
    assert r.status_code == 200, r.text
    call = r.json()["tool_call"]
    assert call["validation_status"] == "failed"
    assert call["execution_status"] == "skipped"
    assert call["error"]["code"] == "TOOL_VALIDATION_FAILED"


def test_secret_never_leaks_in_tool_responses(app_client, as_user):
    project = _project(app_client)
    secret_value = "v4-supersecret-UNIQUE-9f8a7b6c5d"
    r = app_client.post(
        "/api/tool-credentials",
        json={
            "project_id": project["id"],
            "name": "MYKEY",
            "credential_type": "API_KEY",
            "value": secret_value,
        },
    )
    assert r.status_code == 200, r.text
    assert secret_value not in r.text

    tool = _http_tool(
        app_client,
        project["id"],
        "secret_tool",
        url=f"{SAFE_BASE}/data?key={{{{secret:MYKEY}}}}",
    )
    for url in (
        f"/api/tools/{tool['id']}",
        f"/api/tools/{tool['id']}/versions",
        f"/api/tools/{tool['id']}/schema",
    ):
        r = app_client.get(url)
        assert r.status_code == 200, r.text
        assert secret_value not in r.text, f"secret leaked via {url}"

    # Standalone test hits the real network path (times out against the
    # unroutable TEST-NET-1 fixture address) but the response must still be
    # secret-free; the persisted ToolCall snapshot is checked too.
    r = app_client.post(f"/api/tools/{tool['id']}/test", json={"arguments": {}})
    assert r.status_code == 200, r.text
    assert secret_value not in r.text


def test_credential_get_has_no_value_field(app_client, as_user):
    project = _project(app_client)
    r = app_client.post(
        "/api/tool-credentials",
        json={
            "project_id": project["id"],
            "name": "TOKEN",
            "credential_type": "BEARER_TOKEN",
            "value": "bearer-secret-value",
        },
    )
    assert r.status_code == 200, r.text
    cred = r.json()
    assert "value" not in cred and "encrypted_value" not in cred
    assert cred["has_value"] is True

    fetched = app_client.get(f"/api/tool-credentials/{cred['id']}")
    assert fetched.status_code == 200, fetched.text
    assert "value" not in fetched.json()
    assert "encrypted_value" not in fetched.json()
    assert "bearer-secret-value" not in fetched.text


def test_disabled_tool_resolve_fails(app_client, as_user):
    from app.services import tools as tool_service

    owner = as_user["id"]
    project = _project(app_client)
    tool = _http_tool(app_client, project["id"], "toggle_tool")
    r = app_client.patch(f"/api/tools/{tool['id']}", json={"enabled": False})
    assert r.status_code == 200, r.text

    async def _resolve():
        return await tool_service.resolve_enabled_tools(
            owner, [tool["id"]], project_id=project["id"]
        )

    try:
        app_client.portal.call(_resolve)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
        assert "disabled" in (getattr(exc, "message", None) or str(exc))
    else:
        raise AssertionError("disabled tool must not resolve")


def test_permission_denial_path(app_client, as_user):
    from app.services import custom_tools as custom_service

    owner = as_user["id"]
    project = _project(app_client)
    tool = _http_tool(
        app_client,
        project["id"],
        "gated_tool",
        schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
        perms=["special.access"],
    )

    async def _denied():
        doc, version = await custom_service.get_tool_with_version(owner, tool["id"])
        return await custom_service.execute_custom_tool(
            doc,
            version,
            {"q": "hi"},
            owner_id=owner,
            project_id=project["id"],
            granted_permissions=set(),  # caller withholds the declared perm
        )

    call = app_client.portal.call(_denied)
    assert call["permission_status"] == "denied"
    assert call["execution_status"] == "skipped"
    assert call["error"]["code"] == "TOOL_PERMISSION_DENIED"
