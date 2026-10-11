"""Phase 3 observability: traces, spans, linking, partial-failure traces,
Arena/Council trace grouping and blind redaction.

Every execution goes through the canonical funnel with an offline adapter.
"""
from __future__ import annotations

import json

import pytest

from app import providers as providers_module
from app import repository as repo
from app import services
from app.models import now_iso
from app.services import execution as execution_module
from app.services import trace_views
from app.services import tracing

from _support import (
    CapableAdapter,
    create_model,
    create_project,
    create_provider,
    result_error,
    result_success,
)


def _install(monkeypatch, executor, *, tools=False, vision=False, streamer=None):
    factory = lambda provider: CapableAdapter(  # noqa: E731
        executor, streamer, tools=tools, vision=vision
    )
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


@pytest.fixture
def workspace_model(app_client, as_user):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        model_id = await create_model(owner, provider_id=provider_id)
        return model_id

    return owner, app_client.portal.call(_setup)


def test_workspace_execution_records_trace_and_links_run(app_client, workspace_model, monkeypatch):
    owner, model_id = workspace_model
    _install(monkeypatch, lambda model, req: result_success("hello"))

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "system", [{"role": "user", "content": "hi"}], {}
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "success"
    assert run["trace_id"]

    async def _trace():
        return await repo.traces.get(run["trace_id"], owner)

    trace = app_client.portal.call(_trace)
    assert trace is not None
    assert trace["id"] == run["trace_id"]
    assert trace["trace_type"] == "workspace"
    assert trace["root_run_id"] == run["id"]
    assert trace["status"] == "completed"
    assert trace["completed_at"]
    assert trace["span_count"] >= 3


def test_spans_are_ordered_and_model_call_has_duration(app_client, workspace_model, monkeypatch):
    owner, model_id = workspace_model
    _install(monkeypatch, lambda model, req: result_success("hello"))

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "system", [{"role": "user", "content": "hi"}], {}
        )

    run = app_client.portal.call(_execute)

    async def _spans():
        return await tracing.list_spans(owner, run["trace_id"])

    spans = app_client.portal.call(_spans)
    assert spans
    types = [span["span_type"] for span in spans]
    assert "REQUEST" in types
    assert "MODEL_CALL" in types
    assert types.index("REQUEST") < types.index("MODEL_CALL")

    model_spans = [span for span in spans if span["span_type"] == "MODEL_CALL"]
    assert model_spans
    assert all(span["duration_ms"] is not None for span in model_spans)
    assert all(span["status"] == "completed" for span in model_spans)

    expected = sorted(spans, key=lambda s: (s.get("ordering") or 0, s.get("started_at") or ""))
    assert [span["id"] for span in spans] == [span["id"] for span in expected]


def test_failed_execution_records_error_trace_and_failure(app_client, workspace_model, monkeypatch):
    owner, model_id = workspace_model
    _install(monkeypatch, lambda model, req: result_error(category="PROVIDER", message="upstream boom"))

    async def _execute():
        return await execution_module.execute_model(
            owner, model_id, "", [{"role": "user", "content": "hi"}], {}
        )

    run = app_client.portal.call(_execute)
    assert run["status"] == "error"

    async def _trace():
        return await repo.traces.get(run["trace_id"], owner)

    async def _failure():
        return await repo.failures.find_one({"owner_id": owner, "run_id": run["id"]})

    trace = app_client.portal.call(_trace)
    failure = app_client.portal.call(_failure)
    assert trace is not None and trace["status"] == "error"
    assert failure is not None
    assert failure["source"] == "SYSTEM"
    assert failure["failure_class"] == "execution"

    async def _spans():
        return await tracing.list_spans(owner, run["trace_id"])

    spans = app_client.portal.call(_spans)
    assert any(span["status"] == "error" for span in spans)


def test_partial_arena_records_single_arena_trace(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        project_id = await create_project(owner)
        ok = await create_model(owner, provider_id=provider_id, model_identifier="pytest/trace-ok")
        bad = await create_model(owner, provider_id=provider_id, model_identifier="pytest/trace-bad")
        return project_id, ok, bad

    project_id, ok, bad = app_client.portal.call(_setup)

    def executor(model, req):
        if model.get("model_identifier") == "pytest/trace-bad":
            return result_error(category="PROVIDER", message="boom")
        return result_success("fine")

    _install(monkeypatch, executor)

    async def _arena():
        return await services.run_arena(owner, project_id, "hello", "", [ok, bad], {}, blind=False)

    result = app_client.portal.call(_arena)
    experiment_id = result["experiment"]["id"]
    statuses = {run["status"] for run in result["runs"]}
    assert statuses == {"success", "error"}

    async def _traces():
        return await repo.traces.list({"owner_id": owner, "experiment_id": experiment_id}, limit=100)

    async def _runs():
        return await repo.runs.list({"owner_id": owner, "experiment_id": experiment_id}, limit=100)

    traces = app_client.portal.call(_traces)
    runs = app_client.portal.call(_runs)
    assert len(traces) == 1
    trace = traces[0]
    assert trace["trace_type"] == "arena"
    assert len(runs) == 2
    assert all(run["trace_id"] == trace["id"] for run in runs)

    metadata_blob = json.dumps(trace["metadata"], default=str)
    for run in runs:
        assert run["model_name"] and run["model_name"] not in metadata_blob
        assert run["model_identifier"] and run["model_identifier"] not in metadata_blob


def test_council_records_single_council_trace(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        project_id = await create_project(owner)
        first = await create_model(owner, provider_id=provider_id, model_identifier="pytest/council-a")
        second = await create_model(owner, provider_id=provider_id, model_identifier="pytest/council-b")
        moderator = await create_model(owner, provider_id=provider_id, model_identifier="pytest/council-mod")
        return project_id, first, second, moderator

    project_id, first, second, moderator = app_client.portal.call(_setup)

    def executor(model, req):
        if model.get("model_identifier") == "pytest/council-mod":
            return result_success('{"consensus": ["ship"], "recommended_decision": "ship", "confidence": 0.9}')
        return result_success("independent opinion")

    _install(monkeypatch, executor)

    async def _council():
        return await services.run_council(
            owner,
            project_id,
            "Should we ship?",
            participants=[
                {"role": "Optimist", "model_id": first},
                {"role": "Skeptic", "model_id": second},
            ],
            moderator_model_id=moderator,
        )

    council = app_client.portal.call(_council)
    assert council["synthesis_status"] == "success"

    async def _traces():
        return await repo.traces.list({"owner_id": owner, "council_id": council["id"]}, limit=100)

    async def _runs():
        return await repo.runs.list({"owner_id": owner, "council_id": council["id"]}, limit=100)

    traces = app_client.portal.call(_traces)
    runs = app_client.portal.call(_runs)
    assert len(traces) == 1
    trace = traces[0]
    assert trace["trace_type"] == "council"
    # 2 participants x 3 rounds + 1 moderator = 7 immutable runs.
    assert len(runs) == 7
    assert all(run["trace_id"] == trace["id"] for run in runs)

    kinds = {run["execution_kind"] for run in runs}
    assert "council_moderator" in kinds
    assert "council" in kinds


def test_blind_trace_is_redacted_then_revealed(app_client, as_user, monkeypatch):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        project_id = await create_project(owner)
        models = []
        for letter in ("a", "b"):
            model_id = await create_model(
                owner,
                provider_id=provider_id,
                display_name=f"pytest-blind-display-{letter}",
                model_identifier=f"pytest/blind-ident-{letter}",
            )
            models.append(await repo.models.get(model_id, owner))
        return project_id, models

    project_id, models = app_client.portal.call(_setup)
    forbidden = []
    for doc in models:
        forbidden += [doc["id"], doc["display_name"], doc["model_identifier"]]

    _install(monkeypatch, lambda model, req: result_success("generic answer"))
    for doc in models:
        provider = app_client.portal.call(repo.providers.get, doc["provider_id"], owner)
        forbidden.append(provider["name"])

    async def _arena():
        return await services.run_arena(
            owner, project_id, "hello", "", [doc["id"] for doc in models], {}, blind=True
        )

    result = app_client.portal.call(_arena)
    experiment_id = result["experiment"]["id"]

    async def _trace():
        return await repo.traces.find_one({"owner_id": owner, "experiment_id": experiment_id})

    trace = app_client.portal.call(_trace)
    assert trace is not None

    async def _view():
        return await trace_views.build_trace_view(owner, trace)

    blind_view = app_client.portal.call(_view)
    assert blind_view["blind"] is True
    assert blind_view["runs"]
    blob = json.dumps(blind_view, default=str)
    for value in forbidden:
        assert value not in blob, f"blind trace view leaked {value!r}"
    for run in blind_view["runs"]:
        assert "model_name" not in run
        assert "model_id" not in run
        assert "provider_name" not in run

    async def _reveal():
        await repo.experiments.update(experiment_id, owner, {"revealed_at": now_iso()})

    app_client.portal.call(_reveal)
    revealed_view = app_client.portal.call(_view)
    assert revealed_view["blind"] is False
    assert any(run.get("model_name") for run in revealed_view["runs"])
