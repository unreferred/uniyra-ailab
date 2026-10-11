"""OmniLab V4 code-interpreter contract (offline API tests).

The sandbox is Docker-backed: when docker/the image is absent the API must
degrade to structured ``unavailable`` payloads (never raise). Live execution
is attempted only when the service reports itself available.
"""
from __future__ import annotations

import pytest

from app import config
from app.services import attachments as attachments_service
from app.services import code_interpreter as ci_service


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



def _project(app_client, name="v4-ci"):
    r = app_client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_status_reports_availability_and_policy(app_client, as_user):
    r = app_client.get("/api/code-interpreter/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["available"], bool)
    assert body["policy"]["network"] == "off"
    assert set(ci_service.PACKAGES) <= set(body["policy"]["packages"])
    assert body["policy"]["tool"] == ci_service.TOOL_NAME
    if not body["available"]:
        assert body["reason"], "unavailable status must carry a reason"


def test_execute_when_unavailable_returns_structured_body(app_client, as_user):
    status = app_client.get("/api/code-interpreter/status").json()
    if status["available"]:
        import pytest

        pytest.skip("sandbox available; unavailable-path not applicable")
    project = _project(app_client)
    r = app_client.post(
        "/api/code-interpreter/execute",
        json={"project_id": project["id"], "code": "print('hi')"},
    )
    # Never raises: HTTP 200 with a structured unavailable payload.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["execution_id"]
    assert body["error"]


def test_empty_code_rejected(app_client, as_user):
    project = _project(app_client)
    # Pydantic min_length=1 rejects the empty string at the schema layer.
    r = app_client.post(
        "/api/code-interpreter/execute",
        json={"project_id": project["id"], "code": ""},
    )
    assert r.status_code == 422, r.text
    # Whitespace-only code reaches the service validator -> 400.
    r = app_client.post(
        "/api/code-interpreter/execute",
        json={"project_id": project["id"], "code": "   "},
    )
    assert r.status_code == 400, r.text


def test_oversize_code_rejected_413(app_client, as_user):
    project = _project(app_client)
    big = "x" * (int(config.CODE_INTERPRETER_MAX_CODE_CHARS) + 1)
    r = app_client.post(
        "/api/code-interpreter/execute",
        json={"project_id": project["id"], "code": big},
    )
    assert r.status_code == 413, r.text


def test_input_file_scoping(app_client, as_user):
    status = app_client.get("/api/code-interpreter/status").json()
    project = _project(app_client)
    r = app_client.post(
        "/api/code-interpreter/execute",
        json={
            "project_id": project["id"],
            "code": "print('hi')",
            "input_file_ids": ["attachment-that-does-not-exist"],
        },
    )
    if status["available"]:
        # Inputs stage after the availability gate: unknown id -> 404.
        assert r.status_code == 404, r.text
    else:
        # Availability gate runs first: unknown input never reached, but the
        # call still returns a structured unavailable body (never raises).
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "unavailable"


def test_filename_sanitizer_unit():
    assert attachments_service.sanitize_filename("../../etc/passwd") == "passwd"
    assert attachments_service.sanitize_filename("a/b\\c.txt") == "c.txt"
    assert attachments_service.sanitize_filename("") == "attachment"


def test_docker_command_hardening():
    cmd = ci_service.build_docker_command(
        staging_input="/tmp/in", staging_output="/tmp/out", timeout_s=30
    )
    assert "--network" in cmd and "none" in cmd
    assert "--read-only" in cmd
    assert "--cap-drop" in cmd and "ALL" in cmd
    assert "--security-opt" in cmd
    assert "no-new-privileges=true" in cmd
    assert "--privileged" not in cmd
    # Regression: -i (stdin) is load-bearing — `python -` reads the program
    # from STDIN; without it every execution silently no-ops.
    assert "-i" in cmd
    joined = " ".join(cmd)
    assert "docker.sock" not in joined


def test_live_execution_when_available(app_client, as_user):
    status = app_client.get("/api/code-interpreter/status").json()
    if not status["available"]:
        import pytest

        pytest.skip(f"sandbox unavailable: {status.get('reason')}")
    project = _project(app_client)
    r = app_client.post(
        "/api/code-interpreter/execute",
        json={"project_id": project["id"], "code": "print('hi')"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert "hi" in (body["stdout"] or "")
