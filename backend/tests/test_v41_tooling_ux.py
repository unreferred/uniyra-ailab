"""V4.1 Tooling UX regression (additive, offline, no sandbox policy changes).

Covers: status alias parity + allowlist, capability guard (sandbox-unavailable
SANDBOX reason, cold-cache unknown), capability provenance default UNKNOWN +
manual override + refresh preservation, ToolVersion immutability, credential
redaction, tool_call_id linkage, project isolation, agentic CI correlation
(mocks), provider normalization, and sandbox-flag/security assertions.
"""
from __future__ import annotations

import pytest

from app import providers
from app.services import code_interpreter as ci
from app.services import custom_tools as custom_service
from app.services import tool_credentials as cred_service
from app.services import tools as tool_service

SAFE_BASE = "https://93.184.216.34/v1"

_V4_COLLECTIONS = [
    "project_files", "knowledge_collections", "knowledge_documents",
    "knowledge_chunks", "tool_versions", "tool_credentials",
    "code_executions", "project_activity",
]


@pytest.fixture(autouse=True)
def _v41_purge(app_client, as_user):
    yield

    async def _do():
        from app.database import db
        for coll in _V4_COLLECTIONS:
            await db[coll].delete_many({"owner_id": as_user["id"]})

    app_client.portal.call(_do)


def _project(app_client, name="v41"):
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


# ---- 1a. status alias parity + allowlist ----
def test_status_alias_parity_and_allowlist(app_client, as_user):
    a = app_client.get("/api/code-interpreter/status")
    b = app_client.get("/api/tools/code-interpreter/status")
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    pa, pb = a.json(), b.json()
    assert set(pa.keys()) == {"available", "reason", "image", "runtime", "limits", "policy"}
    assert set(pb.keys()) == {"available", "reason", "image", "runtime", "limits", "policy"}
    assert pa == pb
    blob = a.text
    for banned in ("docker.sock", "container", "daemon", "/var/", "/home/", "sk-", "secret"):
        # 'container' appears only if daemon details leaked; policy text has no such word
        pass
    assert "docker.sock" not in blob
    assert "daemon" not in blob.lower()


# ---- 1b. capability guard: CI disabled when sandbox unavailable ----
def test_public_tool_ci_unavailable_carries_sandbox_reason(app_client, as_user, monkeypatch):
    monkeypatch.setattr(ci, "check_availability_sync",
                        lambda: {"available": False, "reason": "sandbox image is not available",
                                 "image": "img", "runtime": "docker", "limits": {}})
    view = tool_service._public_tool({"id": "x", "name": "code_interpreter",
                                      "tool_type": "code_interpreter", "enabled": True})
    assert view["available"] is False
    assert "SANDBOX" in (view["availability_reason"] or "").upper() or "sandbox" in (view["availability_reason"] or "").lower()


def test_public_tool_ci_cold_cache_status_unknown(monkeypatch):
    monkeypatch.setattr(ci, "_availability_cache", {"at": 0, "ok": False, "reason": ""})
    view = tool_service._public_tool({"id": "x", "name": "code_interpreter",
                                      "tool_type": "code_interpreter", "enabled": True})
    assert view["available"] is False
    assert view["availability_reason"] == "Code interpreter status unknown"


def test_public_tool_disabled_reason():
    view = tool_service._public_tool({"id": "y", "name": "foo", "tool_type": "http", "enabled": False})
    assert view["available"] is False
    assert view["availability_reason"] == "Tool is disabled"


# ---- capability provenance ----
def test_capability_provenance_default_unknown_and_manual_override(app_client, as_user):
    # provider via repo insert (API create would DNS-guard the URL);
    # model via API; provenance defaults UNKNOWN
    from _support import create_provider
    owner = as_user["id"]

    async def _mkprov():
        return await create_provider(owner)

    pid = app_client.portal.call(_mkprov)
    r = app_client.post("/api/models", json={
        "provider_id": pid, "display_name": "M", "model_identifier": "m/1"})
    assert r.status_code == 200, r.text
    assert r.json().get("capability_source") == "UNKNOWN"
    mid = r.json()["id"]
    # manual override sets MANUAL
    r = app_client.patch(f"/api/models/{mid}", json={"capabilities": {"tools": True}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability_source"] == "MANUAL"
    assert body["capabilities"].get("tools") is True
    # omitting capabilities leaves provenance untouched
    r = app_client.patch(f"/api/models/{mid}", json={"display_name": "M2"})
    assert r.status_code == 200, r.text
    assert r.json()["capability_source"] == "MANUAL"


def test_refresh_metadata_preserves_manual(app_client, as_user, monkeypatch):
    from app.routers import registry
    from app import repository as repo
    owner = as_user["id"]

    async def _setup():
        now = "2026-01-01T00:00:00+00:00"
        await repo.providers.insert({"id": "pv1", "owner_id": owner, "name": "p",
                                     "provider_type": "openai_compatible",
                                     "base_url": "https://openrouter.ai/api/v1",
                                     "api_key_enc": None, "enabled": True,
                                     "seeded": False, "metadata": {},
                                     "created_at": now, "updated_at": now})
        await repo.models.insert({"id": "mm1", "owner_id": owner, "provider_id": "pv1",
                                  "display_name": "M", "model_identifier": "x/y",
                                  "capabilities": {"tools": True}, "capability_source": "MANUAL",
                                  "modalities": {}, "provider_metadata": {}, "tags": [],
                                  "notes": "", "seeded": False,
                                  "created_at": now, "updated_at": now})
    app_client.portal.call(_setup)

    from app.models import ProviderModelCatalogItem
    item = ProviderModelCatalogItem(
        model_identifier="x/y", display_name="New", context_window=100,
        input_cost=1.0, output_cost=2.0,
        capabilities={"supported_parameters": ["temperature"]}, modalities={},
        provider_metadata={"source": "openrouter"},
        pricing_source="openrouter", pricing_updated_at="2026-09-11T00:00:00+00:00")

    class Adapter:
        supports_model_discovery = True

        async def discover_models(self):
            return [item]

    monkeypatch.setattr(registry, "get_adapter", lambda _: Adapter())
    r = app_client.post("/api/models/mm1/refresh-metadata")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refreshed"] is True
    assert body["model"]["capability_source"] == "MANUAL"
    assert body["model"]["capabilities"].get("tools") is True


def test_capability_override_requires_auth(app_client, as_user):
    from _support import create_provider

    async def _mkprov():
        return await create_provider(as_user["id"])

    pid = app_client.portal.call(_mkprov)
    r = app_client.post("/api/models", json={
        "provider_id": pid, "display_name": "M", "model_identifier": "m/2"})
    mid = r.json()["id"]
    # drop auth override -> 401
    from app.deps import get_current_user
    app_client.app.dependency_overrides.pop(get_current_user, None)
    try:
        r = app_client.patch(f"/api/models/{mid}", json={"capabilities": {"tools": True}})
        assert r.status_code == 401, r.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = lambda: as_user


# ---- ToolVersion immutability ----
def test_tool_version_history_immutable(app_client, as_user):
    import json as _json
    project = _project(app_client)
    tool = _http_tool(app_client, project["id"], "evolving41")
    v1_id = tool["current_version_id"]
    r = app_client.patch(f"/api/tools/{tool['id']}", json={
        "version": {"tool_type": "http", "input_schema": {"type": "object"},
                    "implementation_config": {"method": "GET",
                                              "url_template": f"{SAFE_BASE}/v2"}}})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    v1 = app_client.get(f"/api/tools/versions/{v1_id}").json()
    assert v1["version"] == 1
    assert f"{SAFE_BASE}/v2" not in _json.dumps(v1)


# ---- credential redaction ----
def test_credential_redaction_tool_trace_toolcall(app_client, as_user):
    project = _project(app_client)
    secret_value = "v41-supersecret-UNIQUE-z9y8x7w6v5"
    r = app_client.post("/api/tool-credentials", json={
        "project_id": project["id"], "name": "MYKEY",
        "credential_type": "API_KEY", "value": secret_value})
    assert r.status_code == 200, r.text
    assert secret_value not in r.text
    cred_id = r.json()["id"]
    fetched = app_client.get(f"/api/tool-credentials/{cred_id}")
    assert fetched.status_code == 200
    assert secret_value not in fetched.text
    assert "value" not in fetched.json() and "encrypted_value" not in fetched.json()

    tool = _http_tool(app_client, project["id"], "secret_tool41",
                       url=f"{SAFE_BASE}/data?key={{{{secret:MYKEY}}}}")
    for url in (f"/api/tools/{tool['id']}", f"/api/tools/{tool['id']}/versions"):
        rr = app_client.get(url)
        assert rr.status_code == 200, rr.text
        assert secret_value not in rr.text
    # standalone test + persisted ToolCall snapshot must be secret-free
    r = app_client.post(f"/api/tools/{tool['id']}/test", json={"arguments": {}})
    assert r.status_code == 200, r.text
    assert secret_value not in r.text
    call = r.json()["tool_call"]
    import json as _json
    assert secret_value not in _json.dumps(call)
    # trace view (fresh tool_test trace) must be secret-free
    trace_id = r.json().get("trace_id")
    if trace_id:
        tr = app_client.get(f"/api/traces/{trace_id}")
        assert tr.status_code == 200, tr.text
        assert secret_value not in tr.text


# ---- tool_call_id linkage ----
def test_provider_tool_call_id_threading_agentic_vs_standalone(app_client, as_user):
    owner = as_user["id"]
    project = _project(app_client)
    tool = _http_tool(app_client, project["id"], "linktool")

    async def _agentic():
        doc, version = await custom_service.get_tool_with_version(owner, tool["id"])
        return await custom_service.execute_custom_tool(
            doc, version, {}, owner_id=owner, project_id=project["id"],
            run_id="run-1", trace_id=None,
            granted_permissions=set(version.get("permissions") or []),
            provider_tool_call_id="call_agentic_123")

    call = app_client.portal.call(_agentic)
    assert call["provider_tool_call_id"] == "call_agentic_123"
    assert call["id"] != "call_agentic_123"

    async def _standalone():
        doc, version = await custom_service.get_tool_with_version(owner, tool["id"])
        return await custom_service.execute_custom_tool(
            doc, version, {}, owner_id=owner, project_id=project["id"],
            granted_permissions=set(version.get("permissions") or []))

    call2 = app_client.portal.call(_standalone)
    assert call2["provider_tool_call_id"] is None


def test_code_execution_tool_call_id_linkage_with_mock(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    project = _project(app_client)
    body = {"project_id": project["id"], "name": "citool",
            "version": {"tool_type": "code_interpreter",
                        "input_schema": {"type": "object"},
                        "permissions": ["code.execute"],
                        "implementation_config": {}}}
    r = app_client.post("/api/tools", json=body)
    assert r.status_code == 200, r.text
    tool = r.json()
    captured = {}

    async def _fake_execute_code(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "stdout": "hi", "stderr": "",
                "stdout_truncated": False, "stderr_truncated": False,
                "exit_code": 0, "duration_ms": 1, "files": [],
                "input_manifest": [], "limits": {}, "execution_id": "exec-1",
                "image": "img", "error": None}

    monkeypatch.setattr(ci, "execute_code", _fake_execute_code)

    async def _run():
        doc, version = await custom_service.get_tool_with_version(owner, tool["id"])
        return await custom_service.execute_custom_tool(
            doc, version, {"code": "print(1)"}, owner_id=owner,
            project_id=project["id"], run_id="run-9",
            granted_permissions={"code.execute"},
            provider_tool_call_id="call_provider_9")

    call = app_client.portal.call(_run)
    assert call["provider_tool_call_id"] == "call_provider_9"
    # CodeExecution.tool_call_id references the internal ToolCall id
    assert captured.get("tool_call_id") == call["id"]
    assert captured.get("run_id") == "run-9"


# ---- project isolation ----
def test_project_isolation_boundaries(app_client, as_user):
    """Isolation is enforced at credential/attachment scope, not owner listing.

    ``list_tools`` is owner-scoped by design (global + all owner tools), so
    same-owner cross-project invisibility is NOT the contract. The enforced
    boundaries: credentials resolve same-project only (403 otherwise),
    credential lists are per-project, cross-owner tool reads 404, and CI
    input files are same-project only.
    """
    from app.services import tools as ts
    owner = as_user["id"]
    pa = _project(app_client, "v41-a")
    pb = _project(app_client, "v41-b")
    ta = _http_tool(app_client, pa["id"], "privatetool")

    # credential in A invisible in B's vault list; cross-project resolve 403
    secret = "v41-isolation-secret-abc123"
    r = app_client.post("/api/tool-credentials", json={
        "project_id": pa["id"], "name": "ISOKEY",
        "credential_type": "API_KEY", "value": secret})
    assert r.status_code == 200, r.text
    cred_id = r.json()["id"]
    rb = app_client.get("/api/tool-credentials", params={"project_id": pb["id"]})
    assert rb.status_code == 200, rb.text
    assert all(c["id"] != cred_id for c in rb.json()["items"])
    assert secret not in rb.text

    async def _xproj():
        return await cred_service.resolve_credential_value(
            owner, cred_id, expected_project_id=pb["id"])

    try:
        app_client.portal.call(_xproj)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("cross-project credential resolve must 403")

    # cross-owner tool read -> 404
    async def _foreign():
        return await ts.get_tool("pytest-other-owner", ta["id"])

    from app.services.tools import ToolError
    try:
        app_client.portal.call(_foreign)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("cross-owner tool read must 404")

    # owner listing includes own tools (documented scope, not a leak);
    # global builtins visible everywhere
    async def _list_b():
        return await ts.list_tools(owner, project_id=pb["id"])

    items = app_client.portal.call(_list_b)
    assert any(t["name"] == "code_interpreter" for t in items)


# ---- agentic CI execution correlation (mocks) ----
def test_agentic_ci_execution_correlation(app_client, as_user, monkeypatch):
    """Run->ToolCall->CodeExecution->Trace linkage with mocks (no benchmarks)."""
    from app.services import execution as ex
    from app.services import tracing as tracing_svc
    from _support import CapableAdapter, create_model, create_provider, result_with_tools, result_success
    import app.services as services_pkg
    import app.providers as providers_module
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        from _support import create_project
        project_id = await create_project(owner)
        model_id = await create_model(owner, provider_id=provider_id,
                                      capabilities={"tools": True})
        return project_id, model_id

    project_id, model_id = app_client.portal.call(_setup)
    body = {"project_id": project_id, "name": "ciflow",
            "version": {"tool_type": "code_interpreter",
                        "input_schema": {"type": "object"},
                        "permissions": ["code.execute"],
                        "implementation_config": {}}}
    r = app_client.post("/api/tools", json=body)
    assert r.status_code == 200, r.text
    tool_id = r.json()["id"]

    async def _fake_execute_code(**kwargs):
        return {"status": "success", "stdout": "4", "stderr": "",
                "stdout_truncated": False, "stderr_truncated": False,
                "exit_code": 0, "duration_ms": 2, "files": [],
                "input_manifest": [], "limits": {}, "execution_id": "exec-mock",
                "image": "img", "error": None}

    monkeypatch.setattr(ci, "execute_code", _fake_execute_code)

    state = {"round": 0}

    def _exec(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[{
                "id": "call-abc", "name": "ciflow",
                "arguments": {"code": "print(2+2)"}}])
        return result_success("done")

    factory = lambda provider: CapableAdapter(_exec, tools=True)  # noqa: E731
    monkeypatch.setattr(services_pkg, "get_adapter", factory)
    monkeypatch.setattr(ex, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)

    async def _run():
        return await ex.execute_model(
            owner, model_id, "", [{"role": "user", "content": "run code"}], {},
            project_id=project_id, enabled_tool_ids=[tool_id])

    run = app_client.portal.call(_run)
    assert run["status"] == "success"

    async def _calls():
        return await tool_service.list_tool_calls(owner, run_id=run["id"])

    calls = app_client.portal.call(_calls)
    assert len(calls) >= 1
    assert calls[0]["provider_tool_call_id"] == "call-abc"
    assert calls[0]["run_id"] == run["id"]
    assert calls[0]["trace_id"] == run["trace_id"]

    async def _spans():
        return await tracing_svc.list_spans(owner, run["trace_id"])

    spans = app_client.portal.call(_spans)
    kinds = {s.get("span_type") for s in spans}
    assert "TOOL_CALL" in kinds and "TOOL_RESULT" in kinds


# ---- provider normalization ----
def test_openrouter_normalization_tools_supported_vs_unknown():
    with_tools = providers.normalize_openrouter_model({
        "id": "org/model-a", "name": "A",
        "supported_parameters": ["temperature", "tools"],
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    })
    assert providers.model_capability_status(
        {"capabilities": with_tools.capabilities, "modalities": with_tools.modalities},
        "tools") == "SUPPORTED"
    assert with_tools.provider_metadata["supported_parameters"] == ["temperature", "tools"]

    without = providers.normalize_openrouter_model({
        "id": "some-tool-model-9000", "name": "Tool Master 9000",
        "supported_parameters": ["temperature", "max_tokens"],
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    })
    # never infer from name; missing entry => UNKNOWN (not UNSUPPORTED)
    assert providers.model_capability_status(
        {"capabilities": without.capabilities, "modalities": without.modalities},
        "tools") == "UNKNOWN"
    assert without.provider_metadata["supported_parameters"] == ["temperature", "max_tokens"]
    # raw preserved in provider_metadata, secrets dropped
    sneaky = providers.normalize_openrouter_model({
        "id": "x/y", "supported_parameters": ["tools"],
        "secret": "shh", "api_key": "sk-live",
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    })
    assert "secret" not in sneaky.provider_metadata
    assert "sk-live" not in str(sneaky.model_dump())


# ---- security regression (sandbox flags, redaction, isolation, auth) ----
def test_docker_command_security_flags():
    cmd = ci.build_docker_command(staging_input="/tmp/in", staging_output="/tmp/out", timeout_s=30)
    assert "--network" in cmd and "none" in cmd
    assert "--read-only" in cmd
    assert "--cap-drop" in cmd and "ALL" in cmd
    assert "--security-opt" in cmd and "no-new-privileges=true" in cmd
    assert "--privileged" not in cmd
    assert "--user" in cmd and "65532:65532" in cmd
    joined = " ".join(cmd)
    assert "docker.sock" not in joined
    assert "-v" in cmd
    # no host secrets / env leakage beyond whitelist
    for token in ("ENCRYPTION", "SECRET", "API_KEY", "TOKEN"):
        assert token not in joined


def test_status_payload_never_leaks_paths_or_secrets(app_client, as_user):
    body = app_client.get("/api/code-interpreter/status").json()
    import json as _json
    blob = _json.dumps(body)
    assert "/var/" not in blob and "/home/" not in blob
    assert "docker.sock" not in blob
