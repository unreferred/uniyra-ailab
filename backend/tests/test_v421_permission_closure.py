"""V4.2.1 Code Interpreter permission closure (narrow, offline).

Proves the canonical effective-permission resolver drives BOTH selection
gating and execution authorization for ``code.execute``:

1. Granted (explicitly selected): selection view + execution both GRANTED.
2. Missing: resolver DENIED, execution TOOL_PERMISSION_DENIED.
3. Project-disabled: DISABLED-equivalent denial, no bypass.
4. code.execute does NOT grant unrelated high-risk permissions.
5. Default-deny intact for ungranted tools.
"""
from __future__ import annotations

import pytest

from app.services import tools as tool_service

CI_ID = tool_service.CODE_INTERPRETER_TOOL_ID


def _ci_tool(**overrides):
    doc = tool_service._code_interpreter_seed_doc()
    doc.update(overrides)
    return doc


def test_safe_builtin_granted_without_selection():
    tool = {
        "id": "tool-calculator-0001",
        "name": "calculator",
        "enabled": True,
        "permissions": ["calculator.execute"],
    }
    decision = tool_service.resolve_effective_permission(tool)
    assert decision["status"] == "GRANTED"
    assert decision["source"] == "GLOBAL"


def test_code_interpreter_denied_without_selection():
    tool = _ci_tool()
    decision = tool_service.resolve_effective_permission(tool)
    assert decision["status"] == "DENIED"
    assert decision["required_permission"] == ["code.execute"]
    assert decision["missing"] == ["code.execute"]


def test_code_interpreter_granted_when_explicitly_selected():
    tool = _ci_tool()
    decision = tool_service.resolve_effective_permission(
        tool, enabled_tool_ids=[CI_ID]
    )
    assert decision["status"] == "GRANTED"
    assert decision["source"] == "REQUEST"


def test_code_interpreter_granted_via_granted_set():
    tool = _ci_tool()
    decision = tool_service.resolve_effective_permission(
        tool, granted_permissions={"code.execute"}
    )
    assert decision["status"] == "GRANTED"


def test_disabled_tool_denied_even_when_selected():
    tool = _ci_tool(enabled=False)
    decision = tool_service.resolve_effective_permission(
        tool, enabled_tool_ids=[CI_ID]
    )
    assert decision["status"] == "DENIED"


def test_code_execute_does_not_grant_unrelated_permission():
    tool = {
        "id": "tool-evil-1",
        "name": "evil",
        "enabled": True,
        "permissions": ["admin.wipe"],
    }
    decision = tool_service.resolve_effective_permission(
        tool,
        enabled_tool_ids=["tool-evil-1"],
        granted_permissions={"code.execute"},
    )
    # Selection grants the tool's OWN permissions; an unrelated grant set
    # plus a different tool id must not authorize admin.wipe.
    assert decision["status"] == "GRANTED"  # own-permission explicit enable
    other = tool_service.resolve_effective_permission(
        {"id": "tool-other", "name": "other", "enabled": True,
         "permissions": ["admin.wipe"]},
        granted_permissions={"code.execute"},
    )
    assert other["status"] == "DENIED"
    assert other["missing"] == ["admin.wipe"]


def test_selection_and_execution_gates_agree(app_client, as_user):
    """End-to-end through execute_tool: denied bare, granted when selected."""
    owner = as_user["id"]

    async def _seeded():
        return tool_service._code_interpreter_seed_doc()

    tool = app_client.portal.call(_seeded)

    async def _denied():
        return await tool_service.execute_tool(
            dict(tool), {"code": "print(1)"}, owner_id=owner
        )

    doc = app_client.portal.call(_denied)
    assert doc["permission_status"] == "denied"
    assert doc["error"]["code"] == "TOOL_PERMISSION_DENIED"
    assert "code.execute" in (
        doc["error"].get("missing_permissions") or []
    )

    async def _granted():
        return await tool_service.execute_tool(
            dict(tool), {"code": "print(1)"}, owner_id=owner,
            enabled_tool_ids=[CI_ID],
        )

    # Validation passes (code_interpreter schema requires code); permission
    # must be granted so the funnel proceeds past authorization. The sandbox
    # may fail/skip in CI — assert only that it is NOT a permission denial.
    doc2 = app_client.portal.call(_granted)
    assert doc2["permission_status"] == "granted"
    assert (doc2.get("error") or {}).get("code") != "TOOL_PERMISSION_DENIED"


def test_agentic_loop_path_grants_selected_ci(app_client, as_user, monkeypatch):
    """Full execute_model loop with mocked provider + sandbox (mocked test)."""
    from app.services import execution as ex
    from _support import (
        CapableAdapter, create_model, create_provider, create_project,
        result_with_tools, result_success,
    )
    import app.services as services_pkg
    import app.providers as providers_module
    from app.services import code_interpreter as ci

    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        project_id = await create_project(owner)
        model_id = await create_model(
            owner, provider_id=provider_id, capabilities={"tools": True}
        )
        return project_id, model_id

    project_id, model_id = app_client.portal.call(_setup)

    async def _fake_execute_code(**kwargs):
        return {"status": "success", "stdout": "34311", "stderr": "",
                "stdout_truncated": False, "stderr_truncated": False,
                "exit_code": 0, "duration_ms": 2, "files": [],
                "input_manifest": [], "limits": {}, "execution_id": "exec-421",
                "image": "img", "error": None}

    monkeypatch.setattr(ci, "execute_code", _fake_execute_code)

    state = {"round": 0}

    def _exec(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(tool_calls=[{
                "id": "call-421", "name": "code_interpreter",
                "arguments": {"code": "print(137*249)"}}])
        return result_success("34311")

    factory = lambda provider: CapableAdapter(_exec, tools=True)  # noqa: E731
    monkeypatch.setattr(services_pkg, "get_adapter", factory)
    monkeypatch.setattr(ex, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)

    async def _run():
        return await ex.execute_model(
            owner, model_id, "", [{"role": "user", "content": "calc"}], {},
            project_id=project_id, enabled_tool_ids=[CI_ID])

    run = app_client.portal.call(_run)
    assert run["status"] == "success", run.get("error") or run.get("status")

    async def _calls():
        return await tool_service.list_tool_calls(owner, run_id=run["id"])

    calls = app_client.portal.call(_calls)
    assert len(calls) >= 1
    assert calls[0]["permission_status"] == "granted"
    assert calls[0]["execution_status"] == "success"


def test_code_interpreter_public_tool_is_available_to_select():
    tool = _ci_tool()
    # Execution authorization without selection is still DENIED (default deny)
    exec_decision = tool_service.resolve_effective_permission(tool, for_selection=False)
    assert exec_decision["status"] == "DENIED"
    assert "code.execute" in exec_decision["missing"]

    # Pre-selection availability check reports AVAILABLE_TO_SELECT
    select_decision = tool_service.resolve_effective_permission(tool, for_selection=True)
    assert select_decision["status"] == "AVAILABLE_TO_SELECT"
    assert select_decision["missing"] == []

    # _public_tool serialization uses the pre-selection availability
    pub = tool_service._public_tool(tool)
    assert pub["permission_effective"] == "AVAILABLE_TO_SELECT"
    assert "code.execute" in pub["required_permissions"]


def test_tools_api_surfaces_available_to_select(app_client, as_user):
    r = app_client.get("/api/tools")
    assert r.status_code == 200, r.text
    items = r.json().get("items") or []
    ci_items = [i for i in items if i.get("id") == CI_ID]
    assert len(ci_items) == 1
    ci = ci_items[0]
    assert ci["permission_effective"] == "AVAILABLE_TO_SELECT"
    assert "code.execute" in ci["required_permissions"]
