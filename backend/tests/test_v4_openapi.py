"""OmniLab V4 OpenAPI import/generate (offline API tests)."""
from __future__ import annotations

import pytest

import json


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


SPEC_BASE = "https://93.184.216.34/api"


def _project(app_client, name="v4-openapi"):
    r = app_client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _two_op_spec():
    return {
        "openapi": "3.0.0",
        "info": {"title": "Widget API", "version": "1.0.0"},
        "servers": [{"url": SPEC_BASE}],
        "paths": {
            "/users/{id}": {
                "get": {
                    "operationId": "getUser",
                    "summary": "Get user",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "summary": "Create order",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"sku": {"type": "string"}},
                                    "required": ["sku"],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }


def test_import_json_spec_previews_two_ops(app_client, as_user):
    project = _project(app_client)
    r = app_client.post(
        "/api/tools/import-openapi",
        json={"project_id": project["id"], "spec": json.dumps(_two_op_spec())},
    )
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["operation_count"] == 2
    assert {op["operation_id"] for op in preview["operations"]} == {
        "getUser",
        "createOrder",
    }


def test_generate_selected_op_creates_executable_tool(app_client, as_user):
    project = _project(app_client)
    r = app_client.post(
        "/api/tools/generate-from-openapi",
        json={
            "project_id": project["id"],
            "spec": json.dumps(_two_op_spec()),
            "operations": [{"operation_id": "getUser", "include": True}],
        },
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert len(payload["items"]) == 1 and not payload["errors"]
    tool = payload["items"][0]
    schema = tool["input_schema"]
    assert schema.get("type") == "object"
    assert "id" in (schema.get("properties") or {})
    assert "id" in (schema.get("required") or [])

    stored = app_client.get(f"/api/tools/{tool['id']}")
    assert stored.status_code == 200, stored.text
    assert stored.json()["tool_type"] in ("http", "openapi")


def test_yaml_spec_import(app_client, as_user):
    project = _project(app_client)
    yaml_spec = (
        "openapi: 3.0.0\n"
        "info:\n"
        "  title: Ping API\n"
        "  version: '1'\n"
        "servers:\n"
        f"  - url: {SPEC_BASE}\n"
        "paths:\n"
        "  /ping:\n"
        "    get:\n"
        "      operationId: ping\n"
        "      summary: Ping\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n"
    )
    r = app_client.post(
        "/api/tools/import-openapi",
        json={"project_id": project["id"], "spec": yaml_spec},
    )
    assert r.status_code == 200, r.text
    assert r.json()["operation_count"] == 1
    assert r.json()["operations"][0]["operation_id"] == "ping"


def test_malformed_spec_422(app_client, as_user):
    project = _project(app_client)
    r = app_client.post(
        "/api/tools/import-openapi",
        json={"project_id": project["id"], "spec": "not json at all {{{"},
    )
    assert r.status_code == 422, r.text


def test_swagger_2_rejected_422(app_client, as_user):
    project = _project(app_client)
    r = app_client.post(
        "/api/tools/import-openapi",
        json={
            "project_id": project["id"],
            "spec": json.dumps({"swagger": "2.0", "info": {"title": "old"}}),
        },
    )
    assert r.status_code == 422, r.text
    assert "3.x" in r.text


def test_oversized_spec_limit_documented(app_client, as_user):
    # Sending a real >2MB payload is wasteful; pin the enforced constant
    # (services/openapi_tools.parse_spec caps utf-8 bytes at
    # OPENAPI_MAX_SPEC_BYTES) and prove a small spec still passes.
    from app import config
    from app.services import openapi_tools as openapi_service

    assert int(config.OPENAPI_MAX_SPEC_BYTES) == 2 * 1024 * 1024
    oversized = "x" * (int(config.OPENAPI_MAX_SPEC_BYTES) + 1)
    try:
        app_client.portal.call(openapi_service.parse_spec, oversized)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "maximum size" in (getattr(exc, "message", None) or str(exc))
    else:
        raise AssertionError("oversized spec must be rejected")

    project = _project(app_client)
    r = app_client.post(
        "/api/tools/import-openapi",
        json={"project_id": project["id"], "spec": json.dumps(_two_op_spec())},
    )
    assert r.status_code == 200, r.text


def test_url_import_ssrf_blocked_400(app_client, as_user):
    project = _project(app_client)
    r = app_client.post(
        "/api/tools/import-openapi",
        json={
            "project_id": project["id"],
            "spec_url": "http://169.254.169.254/spec.json",
        },
    )
    assert r.status_code == 400, r.text
