"""Phase 3 Failure Lab: automatic execution-failure capture, human/judge
quality classification, audited reclassification history and aggregate counts.
"""
from __future__ import annotations

import pytest

from app import providers as providers_module
from app import repository as repo
from app import services
from app.services import execution as execution_module

from _support import (
    CapableAdapter,
    create_model,
    create_project,
    create_provider,
    result_error,
    result_success,
)


def _install(monkeypatch, executor):
    factory = lambda provider: CapableAdapter(executor)  # noqa: E731
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


@pytest.fixture
def failure_env(app_client, as_user):
    owner = as_user["id"]

    async def _setup():
        provider_id = await create_provider(owner)
        project_id = await create_project(owner)
        model_id = await create_model(owner, provider_id=provider_id)
        return project_id, model_id

    project_id, model_id = app_client.portal.call(_setup)
    return {"owner": owner, "project_id": project_id, "model_id": model_id}


def _run(app_client, env, *, failing):
    owner = env["owner"]

    async def _execute():
        return await execution_module.execute_model(
            owner, env["model_id"], "",
            [{"role": "user", "content": "hi"}], {},
            project_id=env["project_id"],
        )

    return app_client.portal.call(_execute)


def test_execution_error_records_system_failure(app_client, failure_env, monkeypatch):
    owner = failure_env["owner"]
    _install(monkeypatch, lambda model, req: result_error(category="PROVIDER", message="upstream down"))

    run = _run(app_client, failure_env, failing=True)
    assert run["status"] == "error"

    async def _failure():
        return await repo.failures.find_one({"owner_id": owner, "run_id": run["id"]})

    failure = app_client.portal.call(_failure)
    assert failure is not None
    assert failure["source"] == "SYSTEM"
    assert failure["failure_class"] == "execution"
    assert failure["status"] == "OPEN"
    assert failure["category"] == "PROVIDER"
    assert failure["run_id"] == run["id"]
    assert failure["trace_id"] == run["trace_id"]
    assert failure["history"][0]["action"] == "created"


def test_human_quality_failure_coexists_with_successful_run(app_client, failure_env, monkeypatch):
    owner = failure_env["owner"]
    _install(monkeypatch, lambda model, req: result_success("a confidently wrong answer"))
    run = _run(app_client, failure_env, failing=False)
    assert run["status"] == "success"

    r = app_client.post("/api/failures", json={
        "run_id": run["id"],
        "source": "HUMAN",
        "category": "WRONG_ANSWER",
        "title": "The answer was wrong",
        "description": "Human reviewer flagged it",
    })
    assert r.status_code == 200, r.text
    failure = r.json()
    assert failure["source"] == "HUMAN"
    assert failure["failure_class"] == "quality"
    assert failure["run_id"] == run["id"]

    async def _run_doc():
        return await repo.runs.get(run["id"], owner)

    async def _failures():
        return await repo.failures.list({"owner_id": owner, "run_id": run["id"]}, limit=100)

    assert app_client.portal.call(_run_doc)["status"] == "success"
    failures = app_client.portal.call(_failures)
    assert len(failures) == 1
    assert failures[0]["failure_class"] == "quality"


def test_judge_source_is_persisted(app_client, failure_env):
    project_id = failure_env["project_id"]
    r = app_client.post(
        "/api/failures",
        params={"project_id": project_id},
        json={
            "source": "JUDGE",
            "category": "HALLUCINATION",
            "title": "Judge flagged a hallucination",
        },
    )
    assert r.status_code == 200, r.text
    failure = r.json()
    assert failure["source"] == "JUDGE"
    assert failure["category"] == "HALLUCINATION"
    assert failure["failure_class"] == "quality"


def test_reclassification_appends_history(app_client, failure_env):
    project_id = failure_env["project_id"]
    created = app_client.post(
        "/api/failures",
        params={"project_id": project_id},
        json={"source": "HUMAN", "category": "WRONG_ANSWER", "title": "needs review"},
    ).json()
    assert len(created["history"]) == 1

    r = app_client.patch(
        f"/api/failures/{created['id']}",
        json={
            "category": "HALLUCINATION",
            "status": "RESOLVED",
            "note": "reclassified after review",
        },
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["category"] == "HALLUCINATION"
    assert updated["status"] == "RESOLVED"
    assert updated["resolved_at"]

    history = updated["history"]
    assert len(history) == 2
    assert history[0]["action"] == "created"
    assert history[1]["action"] == "reclassified"
    assert history[1]["previous"]["category"] == "WRONG_ANSWER"
    assert history[1]["previous"]["status"] == "OPEN"
    assert history[1]["note"] == "reclassified after review"
    # The original creation event is preserved verbatim.
    assert created["history"][0] == history[0]


def test_aggregate_counts_are_accurate(app_client, failure_env, monkeypatch):
    project_id = failure_env["project_id"]

    _install(monkeypatch, lambda model, req: result_error(category="PROVIDER", message="boom"))
    error_run = _run(app_client, failure_env, failing=True)
    assert error_run["status"] == "error"

    first = app_client.post(
        "/api/failures",
        params={"project_id": project_id},
        json={"source": "HUMAN", "category": "WRONG_ANSWER", "title": "wrong"},
    )
    second = app_client.post(
        "/api/failures",
        params={"project_id": project_id},
        json={"source": "JUDGE", "category": "HALLUCINATION", "title": "hallucinated"},
    )
    assert first.status_code == 200 and second.status_code == 200

    r = app_client.get("/api/failures/aggregate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert body["execution_count"] == 1
    assert body["quality_count"] == 2

    by_category = {entry["category"]: entry["count"] for entry in body["by_category"]}
    assert by_category.get("PROVIDER") == 1
    assert by_category.get("WRONG_ANSWER") == 1
    assert by_category.get("HALLUCINATION") == 1

    by_source = {entry["source"]: entry["count"] for entry in body["by_source"]}
    assert by_source.get("SYSTEM") == 1
    assert by_source.get("HUMAN") == 1
    assert by_source.get("JUDGE") == 1

    by_status = {entry["status"]: entry["count"] for entry in body["by_status"]}
    assert by_status.get("OPEN") == 3
