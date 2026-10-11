"""Blind Arena: identity suppression, server-side blind_map, reveal semantics,
duplicate-winner handling and partial-failure persistence.

All execution goes through the canonical funnel with a fake, offline adapter.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app import repository as repo
from app import services
from app.services import execution as execution_module

from _support import (
    adapter_factory,
    create_model,
    create_project,
    create_provider,
    result_error,
    result_success,
)

FORBIDDEN_KEYS = {
    "model_id",
    "model_name",
    "provider_id",
    "provider_name",
    "model_identifier",
    "family",
    "pricing_snapshot",
    "estimated_cost",
    "ttft_ms",
    "blind_map",
}


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def _assert_blind_clean(payload, forbidden_values):
    found_keys = set(_walk_keys(payload)) & FORBIDDEN_KEYS
    assert not found_keys, f"blind response leaked identity keys: {found_keys}"
    blob = json.dumps(payload, default=str)
    # ``model_ids`` is deliberately emptied (never a leak); every other forbidden
    # field name must be entirely absent from the serialized response.
    sanitized = blob.replace('"model_ids"', '"omitted_ids"')
    for name in FORBIDDEN_KEYS:
        assert name not in sanitized, f"blind response mentions {name}"
    for value in forbidden_values:
        assert value, "identity value must not be empty"
        assert value not in blob, f"blind response leaked identity value {value!r}"


@pytest.fixture
def arena_env(app_client, as_user, monkeypatch):
    """Return a builder that creates a throwaway provider/models/project and
    installs an offline adapter for the current test."""
    owner = as_user["id"]

    def build(count=3, failing_identifier=None):
        async def _create():
            tag = uuid.uuid4().hex[:8]
            provider_id = await create_provider(owner, name=f"pytest-provider-{tag}")
            project_id = await create_project(owner, f"pytest-arena-project-{tag}")
            docs = []
            for index in range(count):
                letter = chr(ord("a") + index)
                model_id = await create_model(
                    owner,
                    provider_id=provider_id,
                    display_name=f"pytest-display-{letter}-{tag}",
                    model_identifier=f"pytest-ident-{letter}-{tag}",
                )
                docs.append(await repo.models.get(model_id, owner))
            provider = await repo.providers.get(provider_id, owner)
            return project_id, provider, docs

        project_id, provider, docs = app_client.portal.call(_create)

        def executor(model, req):
            if failing_identifier and model.get("model_identifier") == failing_identifier:
                return result_error(message="Provider request failed")
            return result_success("generic answer")

        monkeypatch.setattr(services, "get_adapter", adapter_factory(executor))
        monkeypatch.setattr(execution_module, "get_adapter", adapter_factory(executor))

        forbidden_values = [provider["id"], provider["name"]]
        for doc in docs:
            forbidden_values += [doc["id"], doc["model_identifier"], doc["display_name"]]
        return {
            "owner": owner,
            "project_id": project_id,
            "models": [doc["id"] for doc in docs],
            "forbidden": forbidden_values,
        }

    return build


def _arena_run(app_client, state, blind=True, models=None):
    return app_client.post("/api/arena/run", json={
        "project_id": state["project_id"],
        "title": "pytest arena",
        "task": "Give a short friendly greeting.",
        "system_prompt": "",
        "model_ids": models or state["models"],
        "params": {},
        "blind": blind,
    })


def test_blind_run_hides_identity_and_persists_permutation(app_client, arena_env):
    state = arena_env(count=3)
    r = _arena_run(app_client, state, blind=True)
    assert r.status_code == 200, r.text
    body = r.json()

    assert len(body["runs"]) == 3
    labels = [run["label"] for run in body["runs"]]
    assert set(labels) == {"Response A", "Response B", "Response C"}
    assert all(label.startswith("Response ") for label in labels)
    for run in body["runs"]:
        assert "model_id" not in run
        assert "model_name" not in run
        assert "provider_id" not in run

    experiment = body["experiment"]
    assert experiment["blind"] is True
    assert experiment["revealed"] is False
    assert experiment["model_ids"] == []
    assert "blind_map" not in experiment
    assert experiment["run_count"] == 3
    assert experiment["status"] == "completed"
    assert {run["status"] for run in body["runs"]} == {"success"}
    _assert_blind_clean(body, state["forbidden"])

    owner = state["owner"]
    experiment_id = experiment["id"]

    async def _doc():
        return await repo.experiments.get(experiment_id, owner)

    async def _runs():
        return await repo.runs.list(
            {"owner_id": owner, "experiment_id": experiment_id},
            sort_field="created_at", descending=False, limit=100,
        )

    exp_doc = app_client.portal.call(_doc)
    runs = app_client.portal.call(_runs)
    blind_map = exp_doc["blind_map"]
    assert len(blind_map) == 3
    assert {entry["run_id"] for entry in blind_map} == {run["id"] for run in runs}
    assert {entry["label"] for entry in blind_map} == set(labels)
    assert [entry["label"] for entry in blind_map] == labels


def test_reveal_is_persisted_once_and_legacy_query_reveals(app_client, arena_env):
    state = arena_env(count=3)
    body = _arena_run(app_client, state, blind=True).json()
    experiment_id = body["experiment"]["id"]
    owner = state["owner"]

    detail = app_client.get(f"/api/experiments/{experiment_id}").json()
    assert detail["revealed"] is False
    assert "blind_map" not in detail
    assert detail["model_ids"] == []
    for run in detail["runs"]:
        assert "model_name" not in run
        assert "model_id" not in run
    _assert_blind_clean(detail, state["forbidden"])

    async def _reveal_count(exp_id):
        return await repo.reveal_events.count({"owner_id": owner, "experiment_id": exp_id})

    first = app_client.post(f"/api/experiments/{experiment_id}/reveal")
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["revealed"] is True
    assert len(first_body["blind_map"]) == 3
    assert any(entry.get("model_id") for entry in first_body["blind_map"])
    assert app_client.portal.call(_reveal_count, experiment_id) == 1

    second = app_client.post(f"/api/experiments/{experiment_id}/reveal")
    assert second.status_code == 200
    assert app_client.portal.call(_reveal_count, experiment_id) == 1

    revealed_detail = app_client.get(f"/api/experiments/{experiment_id}").json()
    assert revealed_detail["revealed"] is True
    assert any(run.get("model_name") for run in revealed_detail["runs"])

    legacy_body = _arena_run(app_client, state, blind=True).json()
    legacy_id = legacy_body["experiment"]["id"]
    legacy = app_client.get(f"/api/experiments/{legacy_id}", params={"reveal": "true"})
    assert legacy.status_code == 200
    legacy_json = legacy.json()
    assert legacy_json["revealed"] is True
    assert len(legacy_json["blind_map"]) == 3
    assert app_client.portal.call(_reveal_count, legacy_id) == 1


def test_duplicate_winner_replaced_and_blind_label_stored(app_client, arena_env):
    state = arena_env(count=2)
    body = _arena_run(app_client, state, blind=True).json()
    experiment_id = body["experiment"]["id"]
    owner = state["owner"]
    run0, run1 = body["runs"][0], body["runs"][1]

    r = app_client.post(f"/api/experiments/{experiment_id}/human-eval", json={
        "run_id": run0["id"], "scores": {}, "winner": True, "notes": "",
    })
    assert r.status_code == 200, r.text
    first_eval = r.json()
    assert first_eval["blind_label"] == run0["label"]
    assert first_eval["winner"] is True

    r = app_client.post(f"/api/experiments/{experiment_id}/human-eval", json={
        "run_id": run1["id"], "scores": {}, "winner": True, "notes": "",
    })
    assert r.status_code == 409, r.text

    r = app_client.post(f"/api/experiments/{experiment_id}/human-eval", json={
        "run_id": run1["id"], "scores": {}, "winner": True, "notes": "", "replace": True,
    })
    assert r.status_code == 200, r.text
    second_eval = r.json()

    async def _evals():
        return await repo.evaluations.list(
            {"owner_id": owner, "experiment_id": experiment_id}, limit=100
        )

    evaluations = app_client.portal.call(_evals)
    winners = [e for e in evaluations if e.get("winner")]
    assert len(winners) == 1
    assert winners[0]["id"] == second_eval["id"]
    superseded = [e for e in evaluations if e["id"] == first_eval["id"]][0]
    assert superseded["winner"] is False
    assert superseded["superseded_by"] == second_eval["id"]
    assert superseded["superseded_at"]


def test_partial_arena_failure_persists_both_runs(app_client, arena_env, monkeypatch):
    state = arena_env(count=2)

    async def _idents():
        docs = [await repo.models.get(mid, state["owner"]) for mid in state["models"]]
        return [d["model_identifier"] for d in docs]

    failing = app_client.portal.call(_idents)[0]

    def executor(model, req):
        if model.get("model_identifier") == failing:
            return result_error(category="PROVIDER", message="Provider request failed", retryable=True)
        return result_success("generic answer")

    monkeypatch.setattr(services, "get_adapter", adapter_factory(executor))
    monkeypatch.setattr(execution_module, "get_adapter", adapter_factory(executor))

    r = _arena_run(app_client, state, blind=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["runs"]) == 2
    assert {run["status"] for run in body["runs"]} == {"success", "error"}
    assert body["experiment"]["run_count"] == 2
    assert body["experiment"]["status"] == "completed"
    _assert_blind_clean(body, state["forbidden"])

    owner = state["owner"]
    experiment_id = body["experiment"]["id"]

    async def _runs():
        return await repo.runs.list(
            {"owner_id": owner, "experiment_id": experiment_id}, limit=100
        )

    persisted = app_client.portal.call(_runs)
    assert len(persisted) == 2
    assert any(run["status"] == "success" for run in persisted)
    assert any(run["status"] == "error" for run in persisted)

def test_blind_failure_does_not_leak_identity_via_error_message(app_client, as_user, monkeypatch):
    """A disabled model's resolution error embeds its display name; the blind
    view must expose only category-level text."""
    owner = as_user["id"]

    async def _setup():
        from _support import create_provider, create_project
        provider_id = await create_provider(owner, name=f"pytest-provider-{uuid.uuid4().hex[:8]}")
        project_id = await create_project(owner, "pytest-blind-leak")
        ok = await create_model(
            owner, provider_id=provider_id,
            display_name=f"pytest-display-ok-{uuid.uuid4().hex[:8]}",
            model_identifier=f"pytest/ok-{uuid.uuid4().hex[:8]}",
        )
        disabled = await create_model(
            owner, provider_id=provider_id, enabled=False,
            display_name=f"pytest-display-secret-{uuid.uuid4().hex[:8]}",
            model_identifier=f"pytest/secret-{uuid.uuid4().hex[:8]}",
        )
        docs = [await repo.models.get(mid, owner) for mid in (ok, disabled)]
        return project_id, docs

    project_id, docs = app_client.portal.call(_setup)
    forbidden = []
    for doc in docs:
        forbidden += [doc["id"], doc["display_name"], doc["model_identifier"]]

    monkeypatch.setattr(services, "get_adapter", adapter_factory(lambda model, req: result_success("generic answer")))
    monkeypatch.setattr(execution_module, "get_adapter", adapter_factory(lambda model, req: result_success("generic answer")))

    r = app_client.post("/api/arena/run", json={
        "project_id": project_id, "task": "hello", "system_prompt": "",
        "model_ids": [doc["id"] for doc in docs], "params": {}, "blind": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_blind_clean(body, forbidden)
    error_runs = [run for run in body["runs"] if run["status"] == "error"]
    assert error_runs and error_runs[0]["error_message"]
    assert "secret" not in json.dumps(body).lower()
