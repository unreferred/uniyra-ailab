"""Canonical execution funnel: structured error persistence, disabled
model/provider handling, Council resilience, streaming usage truthfulness.
"""
from __future__ import annotations

import pytest

from app import repository as repo
from app import services
from app.providers import supports_stream
from app.services import execution as execution_module

from _support import (
    adapter_factory,
    create_model,
    create_project,
    create_provider,
    result_error,
    result_success,
)


def _install(monkeypatch, executor, streamer=None):
    factory = adapter_factory(executor, streamer)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)


def test_failure_persists_structured_error_and_error_usage_event(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(owner, provider_id=provider_id)  # unknown pricing

    model_id = app_client.portal.call(_setup)
    _install(monkeypatch, lambda model, req: result_error(
        category="PROVIDER", message="Provider request failed",
        retryable=True, upstream_status=503,
    ))

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "system", [{"role": "user", "content": "hi"}], {}
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "error"
    assert isinstance(run["error"], dict)
    assert run["error"]["category"] == "PROVIDER"
    assert run["error"]["retryable"] is True
    assert run["error"]["upstream_status"] == 503
    assert run["error_message"]
    assert run["estimated_cost"] is None
    assert run["cost_available"] is False

    async def _event():
        return await repo.usage_events.find_one({"owner_id": owner, "run_id": run["id"]})

    event = app_client.portal.call(_event)
    assert event and event["status"] == "error"
    assert event["estimated_cost"] is None
    assert event["error"]["category"] == "PROVIDER"


def test_disabled_model_is_model_unavailable(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(owner, provider_id=provider_id, enabled=False)

    model_id = app_client.portal.call(_setup)
    _install(monkeypatch, lambda model, req: result_success())

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "hi"}], {}
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "error"
    assert run["error"]["category"] == "MODEL_UNAVAILABLE"
    assert run["error"]["code"] == "MODEL_DISABLED"


def test_disabled_provider_is_validation_error(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner, enabled=False)
        return await create_model(owner, provider_id=provider_id, enabled=True)

    model_id = app_client.portal.call(_setup)
    _install(monkeypatch, lambda model, req: result_success())

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "hi"}], {}
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "error"
    assert run["error"]["category"] in ("VALIDATION", "PROVIDER")
    assert run["error"]["category"] == "VALIDATION"
    assert run["error"]["code"] == "PROVIDER_DISABLED"


def test_stream_support_flags_and_disabled_fallback(app_client, as_user, monkeypatch):
    assert supports_stream({"provider_type": "openai_compatible"}, {"capabilities": {"stream": False}}) is False
    assert supports_stream({"provider_type": "openai_compatible"}, {"capabilities": {}}) is True

    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner, enabled=False)
        return await create_model(owner, provider_id=provider_id, enabled=True)

    model_id = app_client.portal.call(_setup)
    _install(monkeypatch, lambda model, req: result_success())

    async def _stream():
        return await execution_module.execute_model_stream(
            owner, model_id, "system", [{"role": "user", "content": "hi"}], {}
        )

    run, text = app_client.portal.call(_stream)
    assert run["status"] == "error"
    assert run["error"]["code"] == "PROVIDER_DISABLED"
    assert text == ""


def test_council_moderator_and_participant_failures_are_resilient(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        project_id = await create_project(owner)
        good_one = await create_model(owner, provider_id=provider_id, model_identifier="pytest/good-one")
        good_two = await create_model(owner, provider_id=provider_id, model_identifier="pytest/good-two")
        bad = await create_model(owner, provider_id=provider_id, model_identifier="pytest/bad")
        moderator_fail = await create_model(owner, provider_id=provider_id, model_identifier="pytest/mod-fail")
        moderator_ok = await create_model(owner, provider_id=provider_id, model_identifier="pytest/mod-ok")
        return project_id, good_one, good_two, bad, moderator_fail, moderator_ok

    project_id, good_one, good_two, bad, moderator_fail, moderator_ok = app_client.portal.call(_setup)

    def executor(model, req):
        identifier = model.get("model_identifier")
        if identifier in ("pytest/bad", "pytest/mod-fail"):
            return result_error(message="Provider request failed")
        if identifier == "pytest/mod-ok":
            return result_success('{"consensus": ["aligned"], "recommended_decision": "ship", "confidence": 0.8}')
        return result_success("independent opinion")

    _install(monkeypatch, executor)

    async def _council_one():
        return await services.run_council(
            owner, project_id, "Should we ship?",
            participants=[
                {"role": "Optimist", "model_id": good_one},
                {"role": "Skeptic", "model_id": good_two},
            ],
            moderator_model_id=moderator_fail,
        )

    council = app_client.portal.call(_council_one)
    assert council["synthesis_status"] in ("partial", "failed")
    assert council["synthesis_status"] == "partial"
    assert council["moderator_status"] == "error"
    for key in ("cost_rollup", "tokens_rollup", "latency_summary"):
        assert key in council

    async def _runs(council_id):
        return await repo.runs.list({"owner_id": owner, "council_id": council_id}, limit=100)

    runs = app_client.portal.call(_runs, council["id"])
    moderator_runs = [r for r in runs if r["execution_kind"] == "council_moderator"]
    assert len(moderator_runs) == 1
    assert moderator_runs[0]["status"] == "error"

    # Participant failure is persisted without destroying the council.
    async def _council_two():
        return await services.run_council(
            owner, project_id, "Should we ship?",
            participants=[
                {"role": "Optimist", "model_id": good_one},
                {"role": "Skeptic", "model_id": bad},
            ],
            moderator_model_id=moderator_ok,
        )

    council_two = app_client.portal.call(_council_two)
    assert council_two["synthesis_status"] in ("partial", "failed")
    assert council_two["participants"]  # participants preserved
    runs_two = app_client.portal.call(_runs, council_two["id"])
    bad_runs = [r for r in runs_two if r["model_id"] == bad]
    assert bad_runs and all(r["status"] == "error" for r in bad_runs)
    good_runs = [r for r in runs_two if r["model_id"] == good_one]
    assert good_runs and any(r["status"] == "success" for r in good_runs)


def test_streaming_unknown_usage_keeps_nulls(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(
            owner, provider_id=provider_id,
            input_price_per_1m=2.0, output_price_per_1m=4.0,
        )

    model_id = app_client.portal.call(_setup)

    async def streamer(model, req):
        yield {"delta": "Hel", "ttft_ms": 12}
        yield {"delta": "lo"}
        yield {"delta": "", "done": True}

    _install(monkeypatch, lambda model, req: result_success(), streamer)

    async def _collect():
        return [event async for event in execution_module.stream_execution(
            owner, model_id, "system", [{"role": "user", "content": "hi"}], {}
        )]

    events = app_client.portal.call(_collect)
    run = events[-1]["run"]
    assert run["streaming"] is True
    assert run["ttft_ms"] == 12
    assert run["input_tokens"] is None
    assert run["output_tokens"] is None
    assert run["cached_tokens"] is None
    assert run["reasoning_tokens"] is None
    assert run["estimated_cost"] is None
    assert run["cost_available"] is False

    async def _event():
        return await repo.usage_events.find_one({"owner_id": owner, "run_id": run["id"]})

    event = app_client.portal.call(_event)
    assert event and event["input_tokens"] is None
    assert event["estimated_cost"] is None


def test_streaming_reported_usage_is_persisted_verbatim(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(
            owner, provider_id=provider_id,
            input_price_per_1m=2.0, output_price_per_1m=4.0,
        )

    model_id = app_client.portal.call(_setup)

    async def streamer(model, req):
        yield {"delta": "Hi", "ttft_ms": 5}
        yield {
            "delta": "",
            "done": True,
            "finish_reason": "stop",
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_tokens": 7,
            "reasoning_tokens": 3,
        }

    _install(monkeypatch, lambda model, req: result_success(), streamer)

    async def _collect():
        return [event async for event in execution_module.stream_execution(
            owner, model_id, "system", [{"role": "user", "content": "hi"}], {}
        )]

    events = app_client.portal.call(_collect)
    run = events[-1]["run"]
    assert run["input_tokens"] == 100
    assert run["output_tokens"] == 50
    assert run["cached_tokens"] == 7
    assert run["reasoning_tokens"] == 3
    assert run["finish_reason"] == "stop"
    assert run["estimated_cost"] == pytest.approx(0.0004)
    assert run["cost_available"] is True


def test_arena_partial_failure_persisted(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        project_id = await create_project(owner)
        ok = await create_model(owner, provider_id=provider_id, model_identifier="pytest/ok")
        bad = await create_model(owner, provider_id=provider_id, model_identifier="pytest/bad")
        return project_id, ok, bad

    project_id, ok, bad = app_client.portal.call(_setup)

    def executor(model, req):
        if model.get("model_identifier") == "pytest/bad":
            return result_error(message="Provider request failed")
        return result_success()

    _install(monkeypatch, executor)

    async def _arena():
        return await services.run_arena(
            owner, project_id, "hello", "", [ok, bad], {}, blind=False,
        )

    result = app_client.portal.call(_arena)
    assert result["experiment"]["run_count"] == 2
    statuses = {run["status"] for run in result["runs"]}
    assert statuses == {"success", "error"}
