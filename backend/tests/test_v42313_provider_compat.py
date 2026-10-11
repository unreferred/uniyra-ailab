"""OMNILAB V4.2.3.13 section-9 provider compatibility tests (offline).

Focused, deterministic tests for the provider-side surface the finalization
token-budget closure depends on: ``reasoning_effort`` parameter forwarding,
``finish_reason`` preservation on the Run, null-safe token telemetry, and the
per-adapter ``supports_reasoning_effort`` capability. No network, no real LLM.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app import config
from app import providers as providers_module
from app import repository as repo
from app import services
from app.providers import (
    ExecutionRequest,
    ExecutionResult,
    OpenAICompatibleAdapter,
    ProviderAdapter,
)
from app.services import execution as execution_module
from tests.conftest import call

from _support import (
    CapableAdapter,
    create_model,
    create_project,
    create_provider,
    result_success,
    result_with_tools,
)

SEARCH_TOOL_ID = "tool-search-0001"
SEARCH_PATCH = "app.services.search_providers.TinyFishSearchProvider.search"
LARGE_CONTEXT_WINDOW = 200000


def _install_adapter(monkeypatch, executor):
    factory = lambda provider: CapableAdapter(executor, None, tools=True)  # noqa: E731
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


async def _model_setup(owner):
    provider_id = await create_provider(owner)
    project_id = await create_project(owner)
    model_id = await create_model(
        owner, provider_id=provider_id, capabilities={"tools": True},
    )
    return project_id, model_id


async def _set_large_context(owner, model_id, value=LARGE_CONTEXT_WINDOW):
    await repo.models.update(model_id, owner, {"context_window": value})


def _search_results(query, urls):
    return {
        "query": query,
        "provider": "tinyfish",
        "results": [
            {"title": f"Result {i}", "url": url, "snippet": f"Snippet {i}",
             "rank": i + 1}
            for i, url in enumerate(urls)
        ],
        "count": len(urls),
    }


def _search_tool_call(call_id, query):
    return {"id": call_id, "name": "web_search", "arguments": {"query": query}}


async def _execute(
    owner, model_id, project_id, *, params=None, enabled_tool_ids=None,
    max_tool_rounds=1,
):
    return await execution_module.execute_model(
        owner, model_id, "system", [{"role": "user", "content": "hi"}],
        params or {}, project_id=project_id, enabled_tool_ids=enabled_tool_ids,
        max_tool_rounds=max_tool_rounds,
    )


# ------------------------------- reasoning_effort parameter forwarding
def test_params_dict_forwards_reasoning_effort_only_when_set():
    """Section 9: ``_params_dict`` forwards reasoning_effort only when set."""
    base = ExecutionRequest(system_prompt="s", messages=[])
    assert "reasoning_effort" not in providers_module._params_dict(base)

    with_effort = ExecutionRequest(
        system_prompt="s", messages=[], reasoning_effort="low", max_tokens=123,
    )
    params = providers_module._params_dict(with_effort)
    assert params["reasoning_effort"] == "low"
    assert params["max_tokens"] == 123

    none_effort = ExecutionRequest(
        system_prompt="s", messages=[], reasoning_effort=None, max_tokens=7,
    )
    params = providers_module._params_dict(none_effort)
    assert "reasoning_effort" not in params
    assert params["max_tokens"] == 7


# --------------------------------- finish_reason preservation on run
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_provider_finish_reason_preserved_on_run(
    app_client, as_user, monkeypatch, finish_reason,
):
    """Section 9: stop and length are both preserved on the Run."""
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)

    def executor(model, req):
        return result_success("provider final text", finish_reason=finish_reason)

    _install_adapter(monkeypatch, executor)

    run = call(app_client, _execute, owner, model_id, project_id)
    assert run["status"] == "success", run.get("error")
    assert run["finish_reason"] == finish_reason
    assert run["normalized_response"] == "provider final text"


# ------------------------------ all-None usage null-safe telemetry
def test_all_none_usage_yields_all_none_finalization_telemetry(
    app_client, as_user, monkeypatch,
):
    """Section 9: an all-None provider result yields None telemetry, no raise."""
    owner = as_user["id"]
    project_id, model_id = call(app_client, _model_setup, owner)
    call(app_client, _set_large_context, owner, model_id)
    finals: List[Any] = []

    def executor(model, req):
        if req.tools is None:
            finals.append(req)
            if len(finals) == 1:
                return ExecutionResult(
                    status="success", text="", finish_reason="length",
                    input_tokens=None, output_tokens=None, reasoning_tokens=None,
                )
            return result_success("recovered from null usage")
        return result_with_tools(tool_calls=[_search_tool_call("s1", "alpha")])

    _install_adapter(monkeypatch, executor)

    async def _run():
        with patch(SEARCH_PATCH, new_callable=AsyncMock) as m:
            m.return_value = _search_results("alpha", ["https://example.com/a"])
            return await _execute(
                owner, model_id, project_id, enabled_tool_ids=[SEARCH_TOOL_ID],
            )

    run = call(app_client, _run)
    assert run["status"] == "success", run.get("error")
    first = run["finalization_telemetry"][0]
    assert first["input_tokens"] is None
    assert first["output_tokens"] is None
    assert first["reasoning_tokens"] is None
    assert first["total_completion_tokens"] is None
    assert run["finalization_recovery_mode"] == "FINALIZATION_RECOVERY_LENGTH_MODE"
    assert run["normalized_response"] == "recovered from null usage"


# ------------------------------- adapter reasoning-effort capability
def test_adapter_supports_reasoning_effort_flags():
    """Section 9: OpenAI-compatible True; ProviderAdapter base False."""
    provider = {
        "id": "p", "provider_type": "openai_compatible",
        "base_url": "https://api.example.com/v1", "api_key_enc": None,
    }
    openai_adapter = OpenAICompatibleAdapter(provider)
    base_adapter = ProviderAdapter(provider)

    assert OpenAICompatibleAdapter.supports_reasoning_effort is True
    assert ProviderAdapter.supports_reasoning_effort is False

    assert providers_module.adapter_supports_reasoning_effort(openai_adapter) is True
    assert providers_module.adapter_supports_reasoning_effort(base_adapter) is False
    assert providers_module.adapter_supports_reasoning_effort(object()) is False
