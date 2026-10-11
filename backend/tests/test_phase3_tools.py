"""Phase 3 tools: registry toggle, resolution, capability gating, schema
validation, permission default-deny, tool-call persistence/tracing, loop
bounding and failure handling.
"""
from __future__ import annotations

from app import providers as providers_module
from app import repository as repo
from app import services
from app.models import new_id, now_iso
from app.services import execution as execution_module
from app.services import tools as tools_service
from app.services import tracing

from _support import (
    CapableAdapter,
    create_model,
    create_project,
    create_provider,
    result_success,
    result_with_tools,
)

CALCULATOR_ID = "tool-calculator-0001"


def _install(monkeypatch, executor, *, tools=False, vision=False, streamer=None):
    factory = lambda provider: CapableAdapter(  # noqa: E731
        executor, streamer, tools=tools, vision=vision
    )
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


def _ensure_calculator(app_client):
    async def _do():
        await tools_service.ensure_builtin_tools()
        await repo.tools.update(CALCULATOR_ID, None, {"enabled": True})

    app_client.portal.call(_do)


def _make_owner_tool(app_client, owner, *, enabled=True, permissions=None,
                     implementation="builtin:calculator", input_schema=None):
    tool_id = new_id()

    async def _do():
        now = now_iso()
        await repo.tools.insert({
            "id": tool_id,
            "owner_id": owner,
            "project_id": None,
            "name": f"pytest_tool_{tool_id[:8]}",
            "display_name": "Pytest Tool",
            "description": "owner-scoped pytest tool",
            "version": "1.0.0",
            "tool_type": "function",
            "input_schema": input_schema if input_schema is not None else {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
            "output_schema": {},
            "enabled": enabled,
            "risk_level": "low",
            "permissions": list(permissions if permissions is not None else ["calculator.execute"]),
            "implementation": implementation,
            "scope": "owner",
            "builtin": False,
            "tags": [],
            "created_at": now,
            "updated_at": now,
        })

    app_client.portal.call(_do)
    return tool_id


def _model_setup(app_client, owner, *, capabilities):
    async def _setup():
        provider_id = await create_provider(owner)
        project_id = await create_project(owner)
        model_id = await create_model(owner, provider_id=provider_id, capabilities=capabilities)
        return project_id, model_id

    return app_client.portal.call(_setup)


def test_enabled_toggle_is_persisted_via_router(app_client, as_user):
    owner = as_user["id"]
    _ensure_calculator(app_client)
    tool_id = _make_owner_tool(app_client, owner, enabled=True)

    r = app_client.patch(f"/api/tools/{tool_id}", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False

    async def _get():
        return await repo.tools.get(tool_id, owner)

    doc = app_client.portal.call(_get)
    assert doc is not None
    assert doc["enabled"] is False

    r = app_client.patch(f"/api/tools/{tool_id}", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    assert app_client.portal.call(_get)["enabled"] is True


def test_disabled_tool_is_unavailable(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    _ensure_calculator(app_client)
    tool_id = _make_owner_tool(app_client, owner, enabled=False)
    project_id, model_id = _model_setup(app_client, owner, capabilities={"tools": True})
    _install(monkeypatch, lambda model, req: result_success("unused"), tools=True)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "calculate"}], {},
            project_id=project_id, enabled_tool_ids=[tool_id],
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "error"
    assert run["error"]["code"] == "TOOL_RESOLUTION_FAILED"
    assert run["error"]["category"] == "VALIDATION"


def test_model_without_tool_capability_gets_no_tools(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    _ensure_calculator(app_client)
    project_id, model_id = _model_setup(app_client, owner, capabilities={})
    recorded = []

    def executor(model, req):
        recorded.append(req)
        return result_success("unexpected")

    _install(monkeypatch, executor, tools=True)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "calculate"}], {},
            project_id=project_id, enabled_tool_ids=[CALCULATOR_ID],
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "error"
    assert run["error"]["code"] == "TOOL_CALLING_UNSUPPORTED"
    assert run["error"]["category"] == "MODEL_UNAVAILABLE"
    # The adapter must never have been invoked, so no tool schema was sent.
    assert recorded == []
    assert all((req.tools or []) == [] for req in recorded)


def test_calculator_schema_validation_failure(app_client, as_user):
    owner = as_user["id"]
    _ensure_calculator(app_client)

    async def _tool():
        return await tools_service.get_tool(owner, CALCULATOR_ID)

    tool = app_client.portal.call(_tool)

    async def _execute():
        return await tools_service.execute_tool(tool, {}, owner_id=owner)

    doc = app_client.portal.call(_execute)
    assert doc["validation_status"] == "failed"
    assert doc["execution_status"] == "skipped"
    assert doc["error"]["code"] == "TOOL_VALIDATION_FAILED"
    assert doc["error"]["errors"]

    async def _persisted():
        return await repo.tool_calls.get(doc["id"], owner)

    assert app_client.portal.call(_persisted) is not None


def test_unsafe_permission_is_denied(app_client, as_user):
    owner = as_user["id"]
    tool_id = _make_owner_tool(
        app_client,
        owner,
        permissions=["danger.execute"],
        implementation="builtin:not-a-real-tool",
        input_schema={"type": "object", "properties": {}},
    )

    async def _tool():
        return await repo.tools.get(tool_id, owner)

    tool = app_client.portal.call(_tool)

    async def _execute():
        return await tools_service.execute_tool(tool, {}, owner_id=owner)

    doc = app_client.portal.call(_execute)
    assert doc["validation_status"] == "passed"
    assert doc["permission_status"] == "denied"
    assert doc["execution_status"] == "skipped"
    assert doc["error"]["code"] == "TOOL_PERMISSION_DENIED"


def test_tool_call_is_persisted_and_traced(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    _ensure_calculator(app_client)
    project_id, model_id = _model_setup(app_client, owner, capabilities={"tools": True})
    state = {"round": 0, "requests": []}

    def executor(model, req):
        state["requests"].append(req)
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(
                tool_calls=[{
                    "id": "call-1",
                    "name": "calculator",
                    "arguments": {"expression": "2+2"},
                }]
            )
        return result_success("The answer is 4")

    _install(monkeypatch, executor, tools=True)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "what is 2+2?"}], {},
            project_id=project_id, enabled_tool_ids=[CALCULATOR_ID],
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "success"
    assert run["tool_call_count"] >= 1

    async def _calls():
        return await tools_service.list_tool_calls(owner, run_id=run["id"])

    async def _spans():
        return await tracing.list_spans(owner, run["trace_id"])

    calls = app_client.portal.call(_calls)
    assert len(calls) == 1
    assert calls[0]["execution_status"] == "success"
    assert calls[0]["validation_status"] == "passed"
    assert calls[0]["permission_status"] == "granted"
    assert calls[0]["output"]["result"] == 4

    span_types = [span["span_type"] for span in app_client.portal.call(_spans)]
    assert "TOOL_CALL" in span_types
    assert "TOOL_RESULT" in span_types

    assert state["requests"][0].tools
    assert state["requests"][0].tools[0]["name"] == "calculator"


def test_tool_loop_limit_is_bounded(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    _ensure_calculator(app_client)
    project_id, model_id = _model_setup(app_client, owner, capabilities={"tools": True})
    state = {"calls": 0}

    def executor(model, req):
        state["calls"] += 1
        return result_with_tools(
            tool_calls=[{
                "id": f"call-{state['calls']}",
                "name": "calculator",
                "arguments": {"expression": f"{state['calls']}+1"},
            }]
        )

    _install(monkeypatch, executor, tools=True)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "loop"}], {},
            project_id=project_id, enabled_tool_ids=[CALCULATOR_ID], max_tool_rounds=1,
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "error"
    assert run["error"]["code"] == "TOOL_LOOP_LIMIT_REACHED"

    async def _calls():
        return await tools_service.list_tool_calls(owner, run_id=run["id"])

    calls = app_client.portal.call(_calls)
    assert 0 <= len(calls) <= 2


def test_failed_tool_execution_is_recorded(app_client, as_user, monkeypatch):
    owner = as_user["id"]
    _ensure_calculator(app_client)
    project_id, model_id = _model_setup(app_client, owner, capabilities={"tools": True})
    state = {"round": 0}

    def executor(model, req):
        state["round"] += 1
        if state["round"] == 1:
            return result_with_tools(
                tool_calls=[{
                    "id": "call-1",
                    "name": "calculator",
                    "arguments": {"expression": "1/0"},
                }]
            )
        return result_success("I could not divide by zero")

    _install(monkeypatch, executor, tools=True)

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "1/0"}], {},
            project_id=project_id, enabled_tool_ids=[CALCULATOR_ID],
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "success"

    async def _calls():
        return await tools_service.list_tool_calls(owner, run_id=run["id"])

    calls = app_client.portal.call(_calls)
    assert len(calls) == 1
    assert calls[0]["execution_status"] == "failed"
    assert calls[0]["error"]["code"] == "TOOL_EXECUTION_FAILED"
    assert "zero" in calls[0]["error"]["message"].lower()
