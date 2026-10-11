"""Focused tests for immutable execution pricing and cost availability."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app import services
from app.providers import ExecutionResult


class MemoryCollection:
    def __init__(self):
        self.items = []

    async def insert(self, document):
        stored = deepcopy(document)
        self.items.append(stored)
        return stored


class FakeAdapter:
    def __init__(self, result=None, mutate=None):
        self.result = result
        self.mutate = mutate

    async def execute(self, model, request):
        if self.mutate:
            self.mutate(model)
        return self.result

    async def stream(self, model, request):
        yield {"delta": "hello"}
        yield {"delta": "", "done": True}


async def _return(value):
    return value


def make_repo(model):
    return SimpleNamespace(
        models=SimpleNamespace(get=lambda model_id, owner_id: _return(model)),
        providers=SimpleNamespace(get=lambda provider_id, owner_id: _return({
            "id": "provider-1", "name": "Test provider", "provider_type": "test",
            "enabled": True,
        })),
        runs=MemoryCollection(),
        usage_events=MemoryCollection(),
    )


@pytest.mark.parametrize(
    ("pricing", "input_tokens", "output_tokens", "expected"),
    [
        ({"input_cost": 2.0, "output_cost": 4.0}, 1000, 2000, 0.01),
        ({"input_price_per_1m": 3.0, "output_price_per_1m": 5.0, "input_cost": 99.0}, 1000, 2000, 0.013),
        ({"input_price_per_1m": 3.0, "output_price_per_1m": 5.0}, 1000, 2000, 0.013),
        ({"input_cost": 2.0, "output_cost": None}, 1000, 0, 0.002),
        ({"input_cost": 2.0, "output_cost": None}, 1000, 1, None),
        ({"input_cost": None, "output_cost": None}, 1000, 2000, None),
        ({"input_cost": None, "output_cost": None}, 0, 0, None),
    ],
)
def test_estimate_cost_requires_each_nonzero_component(pricing, input_tokens, output_tokens, expected):
    assert services.estimate_cost(pricing, input_tokens, output_tokens) == expected


def test_execute_persists_complete_snapshot_and_uses_captured_rates(monkeypatch):
    model = {
        "id": "model-1", "provider_id": "provider-1", "display_name": "Test model",
        "model_identifier": "test-model", "input_price_per_1m_tokens": 2.0,
        "output_cost": 4.0, "pricing_source": "catalog", "pricing_updated_at": "2026-09-11T00:00:00Z",
    }
    fake_repo = make_repo(model)
    monkeypatch.setattr(services, "repo", fake_repo)
    monkeypatch.setattr(services, "get_adapter", lambda provider: FakeAdapter(
        ExecutionResult(status="success", text="answer", input_tokens=1000, output_tokens=2000),
        mutate=lambda current: current.update({"input_price_per_1m_tokens": 100.0, "output_cost": 200.0}),
    ))

    run = asyncio.run(services.execute_model("owner-1", "model-1", "system", [{"role": "user", "content": "hi"}], {}))
    event = fake_repo.usage_events.items[0]
    snapshot = run["pricing_snapshot"]

    assert snapshot == {
        "input_price_per_1m": 2.0,
        "output_price_per_1m": 4.0,
        "pricing_source": "catalog",
        "pricing_updated_at": "2026-09-11T00:00:00Z",
        "captured_at": snapshot["captured_at"],
        "currency": "USD",
        "unit": "per_1m_tokens",
    }
    assert run["estimated_cost"] == 0.01
    assert run["cost_available"] is True
    assert event["pricing_snapshot"] == snapshot
    assert event["estimated_cost"] == 0.01

    model.update({"input_price_per_1m_tokens": 900.0, "output_cost": 900.0})
    assert fake_repo.runs.items[0]["pricing_snapshot"] == snapshot
    assert fake_repo.usage_events.items[0]["pricing_snapshot"] == snapshot


def test_null_pricing_is_snapshotted_and_cost_is_unavailable(monkeypatch):
    model = {
        "id": "model-1", "provider_id": "provider-1", "display_name": "Universal",
        "model_identifier": "universal", "input_cost": None, "output_cost": None,
    }
    fake_repo = make_repo(model)
    monkeypatch.setattr(services, "repo", fake_repo)
    monkeypatch.setattr(services, "get_adapter", lambda provider: FakeAdapter(
        ExecutionResult(status="success", text="answer", input_tokens=10, output_tokens=20)
    ))

    run = asyncio.run(services.execute_model("owner-1", "model-1", "", [{"role": "user", "content": "hi"}], {}))

    assert run["pricing_snapshot"]["input_price_per_1m"] is None
    assert run["pricing_snapshot"]["output_price_per_1m"] is None
    assert run["estimated_cost"] is None
    assert run["cost_available"] is False
    assert fake_repo.usage_events.items[0]["pricing_snapshot"] == run["pricing_snapshot"]


def test_streaming_unknown_usage_never_reports_zero_cost(monkeypatch):
    model = {
        "id": "model-1", "provider_id": "provider-1", "display_name": "Stream model",
        "model_identifier": "stream-model", "input_cost": 2.0, "output_cost": 4.0,
    }
    fake_repo = make_repo(model)
    monkeypatch.setattr(services, "repo", fake_repo)
    monkeypatch.setattr(services, "get_adapter", lambda provider: FakeAdapter())

    async def collect():
        return [event async for event in services.stream_execution(
            "owner-1", "model-1", "", [{"role": "user", "content": "hi"}], {}
        )]
    events = asyncio.run(collect())
    run = events[-1]["run"]

    assert run["estimated_cost"] is None
    assert run["cost_available"] is False
    assert fake_repo.usage_events.items[0]["estimated_cost"] is None
    assert fake_repo.usage_events.items[0]["pricing_snapshot"] == run["pricing_snapshot"]
