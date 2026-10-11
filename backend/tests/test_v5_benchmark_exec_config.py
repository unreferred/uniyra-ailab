"""V5: the benchmark runner threads per-configuration execution config.

Each ExperimentConfiguration may pin knowledge/tool/search/fetch settings. This
proves process_item forwards them unchanged into execute_model, that the
provider request and persisted Run/trace reflect them, and that the frozen
snapshot keeps them. Offline only: retrieval and provider execution are
deterministic doubles; no network call is made.
"""
from __future__ import annotations

import uuid

import pytest

from app import repository as repo
from app import services
from app import providers as providers_module
from app.services import benchmark as benchmark_service
from app.services import execution as execution_module
from app.services import retrieval as retrieval_service
from app.services import tracing

from _support import (
    adapter_factory,
    create_model,
    create_provider,
    result_success,
)

FACT = "The blue fact is 42."
MARKER = "<untrusted_knowledge_evidence>"

# ``as_user`` purges OWNER_COLLECTIONS, which does not include the knowledge
# collections; clean them up explicitly for this throwaway owner.
PHASE4_COLLECTIONS = (
    "knowledge_collections",
    "knowledge_documents",
    "knowledge_chunks",
    "project_files",
)


@pytest.fixture(autouse=True)
def purge_knowledge(app_client, as_user):
    yield

    async def cleanup():
        from app.database import db

        for name in PHASE4_COLLECTIONS:
            await db[name].delete_many({"owner_id": as_user["id"]})

    app_client.portal.call(cleanup)


def _install(monkeypatch, executor):
    factory = adapter_factory(executor)
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


def _make_project(client, name):
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_collection(client, project_id, name):
    r = client.post("/api/knowledge", json={"project_id": project_id, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_dataset(client, project_id, case_input):
    r = client.post(
        "/api/datasets",
        json={"project_id": project_id, "name": f"pytest-ds-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code == 200, r.text
    dataset_id = r.json()["id"]
    version = client.get(f"/api/datasets/{dataset_id}/versions").json()[0]
    r = client.post(
        f"/api/datasets/{dataset_id}/versions/{version['id']}/cases",
        json={"input_text": case_input, "expected_output": FACT, "ordering": 0},
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/datasets/{dataset_id}/versions/{version['id']}/freeze")
    assert r.status_code == 200, r.text
    return dataset_id, version["id"]


def test_benchmark_threads_knowledge_and_tool_execution_config(
    app_client, as_user, monkeypatch
):
    owner = as_user["id"]
    project_id = _make_project(app_client, "pytest-v5-exec-config")
    collection_id = _make_collection(app_client, project_id, "v5-exec")

    async def _setup_model():
        provider_id = await create_provider(owner)
        return await create_model(owner, provider_id=provider_id)

    model_id = app_client.portal.call(_setup_model)
    dataset_id, version_id = _make_dataset(
        app_client, project_id, "What is the blue fact?"
    )

    payload = {
        "collection_ids": [collection_id],
        "result_count": 1,
        "latency_ms": 3,
        "embedding_models": ["local-hashing-v1"],
        "results": [
            {
                "chunk_id": "c1",
                "content": FACT,
                "source": {"project_file_id": "f1", "display_name": "guide.txt"},
                "score": 0.9,
                "token_estimate": 5,
            }
        ],
    }
    retrieval_calls = []

    async def _retrieve(owner_id, project, query, collection_ids, *, top_k, score_threshold):
        retrieval_calls.append((query, list(collection_ids), top_k))
        return payload

    monkeypatch.setattr(retrieval_service, "retrieve", _retrieve)

    captured = []

    def executor(_model, request):
        captured.append(request)
        return result_success(f"{FACT} [K1]")

    _install(monkeypatch, executor)

    body = {
        "project_id": project_id,
        "title": f"pytest-v5-exec-{uuid.uuid4().hex[:6]}",
        "experiment_type": "benchmark",
        "protocol": {
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "repetitions": 1,
        },
        "configurations": [
            {
                "label": "knowledge_on",
                "model_id": model_id,
                "max_tokens": 64,
                "knowledge_enabled": True,
                "knowledge_collection_ids": [collection_id],
                "enabled_tool_ids": [],
                "order": 0,
            },
            {
                "label": "knowledge_off",
                "model_id": model_id,
                "max_tokens": 64,
                "knowledge_enabled": False,
                "enabled_tool_ids": [],
                "order": 1,
            },
        ],
    }
    r = app_client.post("/api/experiments", json=body)
    assert r.status_code == 200, r.text
    exp = r.json()
    config_ids = {c["label"]: c["id"] for c in exp["configurations"]}

    r = app_client.post(f"/api/experiments/{exp['id']}/ready")
    assert r.status_code == 200, r.text

    r = app_client.post(f"/api/experiments/{exp['id']}/benchmark/plan")
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]

    async def _run():
        await benchmark_service.run_benchmark(plan_id, owner)

    app_client.portal.call(_run)

    async def _runs():
        return await repo.runs.list(
            {"owner_id": owner, "experiment_id": exp["id"], "execution_kind": "benchmark"},
            sort_field="created_at",
            descending=False,
            limit=100,
        )

    runs = app_client.portal.call(_runs)
    assert len(runs) == 2
    by_config = {run["configuration_id"]: run for run in runs}
    on_run = by_config[config_ids["knowledge_on"]]
    off_run = by_config[config_ids["knowledge_off"]]
    assert on_run["status"] == "success" and off_run["status"] == "success"

    # 1. Retrieved evidence only reaches the knowledge_on provider request.
    on_requests = [req for req in captured if MARKER in (req.system_prompt or "")]
    off_requests = [req for req in captured if MARKER not in (req.system_prompt or "")]
    assert len(on_requests) == 1 and len(off_requests) == 1
    assert FACT in on_requests[0].system_prompt
    assert "guide.txt" in on_requests[0].system_prompt
    assert FACT not in (off_requests[0].system_prompt or "")
    assert retrieval_calls and retrieval_calls[0][1] == [collection_id]

    # 2. The persisted run and trace reflect the per-configuration switch.
    assert on_run["knowledge_enabled"] is True
    assert off_run["knowledge_enabled"] is False
    on_types = {
        s["span_type"]
        for s in app_client.portal.call(tracing.list_spans, owner, on_run["trace_id"])
    }
    off_types = {
        s["span_type"]
        for s in app_client.portal.call(tracing.list_spans, owner, off_run["trace_id"])
    }
    assert {"KNOWLEDGE_RETRIEVAL", "KNOWLEDGE_CONTEXT_INJECTION"} <= on_types
    assert "KNOWLEDGE_RETRIEVAL" not in off_types
    assert "KNOWLEDGE_CONTEXT_INJECTION" not in off_types

    # 3. No tools are exposed for either configuration and none were called.
    for run in (on_run, off_run):
        assert run["tool_call_count"] == 0
    for req in captured:
        assert not getattr(req, "tools", None)

    # 4. The frozen configuration is pinned on each run's snapshot.
    assert on_run["configuration_snapshot"]["knowledge_enabled"] is True
    assert on_run["configuration_snapshot"]["enabled_tool_ids"] == []
    assert on_run["configuration_snapshot"]["knowledge_collection_ids"] == [collection_id]
    assert off_run["configuration_snapshot"]["knowledge_enabled"] is False
    assert off_run["configuration_snapshot"]["enabled_tool_ids"] == []
