"""Focused tests for provider model discovery and metadata refresh."""
import asyncio

import pytest

from app import models as domain_models
from app import providers
from app.routers import registry
from app.security import encrypt_secret


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    response = None
    responses = {}
    calls = []
    last_headers = None

    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers):
        FakeClient.calls.append((url, headers))
        FakeClient.last_headers = headers
        if url.endswith("/auth/key") and "auth" in FakeClient.responses:
            return FakeClient.responses["auth"]
        if url.endswith("/models") and "models" in FakeClient.responses:
            return FakeClient.responses["models"]
        return FakeClient.response


def test_openrouter_discovery_normalizes_prices_capabilities_and_safe_metadata(monkeypatch):
    async def scenario():
        FakeClient.calls = []
        FakeClient.responses = {
            "auth": FakeResponse(200, {"key": {"hash": "safe-public-id"}}),
            "models": FakeResponse(200, {"data": [{
            "id": "openai/gpt-test", "name": "GPT Test", "context_length": 128000,
            "pricing": {"prompt": "0.000002", "completion": "not-a-number"},
            "supported_parameters": ["temperature", "tools"],
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            "canonical_slug": "gpt-test", "description": "safe public description",
            "secret": "must-not-survive",
            }]})
        }
        monkeypatch.setattr(providers.httpx, "AsyncClient", FakeClient)
        adapter = providers.OpenAICompatibleAdapter({
            "base_url": "https://openrouter.ai/api/v1/", "api_key_enc": encrypt_secret("sk-secret"),
        })
        items = await adapter.discover_models()
        assert adapter.supports_model_discovery is True
        item = items[0].model_dump()
        assert item["model_identifier"] == "openai/gpt-test"
        assert item["input_cost"] == pytest.approx(2.0)
        assert item["output_cost"] is None
        assert item["input_price_per_1m"] == pytest.approx(2.0)
        assert item["output_price_per_1m"] is None
        assert item["input_price_per_1m_tokens"] == pytest.approx(2.0)
        assert item["context_window"] == 128000
        assert item["capabilities"]["supported_parameters"] == ["temperature", "tools"]
        assert item["modalities"] == {"input": ["text", "image"], "output": ["text"]}
        assert "secret" not in item["provider_metadata"]
        assert "sk-secret" not in str(item)
        assert [url for url, _ in FakeClient.calls] == [
            "https://openrouter.ai/api/v1/auth/key",
            "https://openrouter.ai/api/v1/models",
        ]
        assert all(headers == {"Content-Type": "application/json", "Authorization": "Bearer sk-secret"}
                   for _, headers in FakeClient.calls)
    asyncio.run(scenario())


def test_generic_openai_compatible_providers_support_discovery():
    adapter = providers.OpenAICompatibleAdapter({
        "base_url": "https://api.example.com/v1", "api_key_enc": encrypt_secret("sk-secret"),
    })
    assert adapter.supports_model_discovery is True


def test_generic_openai_compatible_discovery_normalizes_minimum_fields(monkeypatch):
    async def scenario():
        FakeClient.calls = []
        FakeClient.responses = {
            "models": FakeResponse(200, {"object": "list", "data": [
                {"id": " llama-3.1-8b ", "owned_by": "local-gateway"},
                {"name": "missing id"},
            ]}),
        }
        monkeypatch.setattr(providers.httpx, "AsyncClient", FakeClient)
        async def allow_provider_url(value):
            return value.rstrip("/")
        monkeypatch.setattr(providers, "async_validate_provider_url", allow_provider_url)
        adapter = providers.OpenAICompatibleAdapter({
            "base_url": "https://api.example.com/v1", "api_key_enc": encrypt_secret("sk-secret"),
        })
        items = await adapter.discover_models()
        assert [item.model_identifier for item in items] == ["llama-3.1-8b"]
        item = items[0].model_dump()
        assert item["display_name"] == "llama-3.1-8b"
        assert item["context_window"] is None
        assert item["input_cost"] is None
        assert item["output_cost"] is None
        assert item["capabilities"] == {}
        assert item["modalities"] == {}
        assert item["provider_metadata"] == {"source": "openai_compatible", "owned_by": "local-gateway"}
        assert [url for url, _ in FakeClient.calls] == ["https://api.example.com/v1/models"]
        assert "sk-secret" not in str(items)
    asyncio.run(scenario())


def test_generic_discovery_404_is_sanitized(monkeypatch):
    async def scenario():
        FakeClient.responses = {"models": FakeResponse(404, {"secret": "upstream-key"})}
        monkeypatch.setattr(providers.httpx, "AsyncClient", FakeClient)
        async def allow_provider_url(value):
            return value.rstrip("/")
        monkeypatch.setattr(providers, "async_validate_provider_url", allow_provider_url)
        adapter = providers.OpenAICompatibleAdapter({
            "base_url": "https://api.example.com/v1", "api_key_enc": encrypt_secret("sk-secret"),
        })
        with pytest.raises(providers.ProviderDiscoveryError) as error:
            await adapter.discover_models()
        assert error.value.status_code == 502
        assert error.value.code == "DISCOVERY_UNSUPPORTED"
        assert "secret" not in error.value.message
        assert "sk-secret" not in error.value.message
    asyncio.run(scenario())


def test_discovery_requires_encrypted_credential():
    async def scenario():
        adapter = providers.OpenAICompatibleAdapter({"base_url": "https://openrouter.ai/api/v1"})
        with pytest.raises(providers.ProviderDiscoveryError) as error:
            await adapter.discover_models()
        assert error.value.status_code == 400
        assert "key" in error.value.message.lower()
    asyncio.run(scenario())


def test_discovery_auth_and_json_errors_are_safe(monkeypatch):
    async def scenario():
        monkeypatch.setattr(providers.httpx, "AsyncClient", FakeClient)
        adapter = providers.OpenAICompatibleAdapter({
            "base_url": "https://openrouter.ai/api/v1", "api_key_enc": encrypt_secret("sk-secret"),
        })
        for status_code in (401, 403, 500):
            FakeClient.calls = []
            FakeClient.responses = {
                "auth": FakeResponse(status_code, {"secret": "upstream-key"}),
                "models": FakeResponse(200, {"data": []}),
            }
            with pytest.raises(providers.ProviderDiscoveryError) as error:
                await adapter.discover_models()
            assert "authentication" in error.value.message.lower()
            assert error.value.status_code == 502
            assert "sk-secret" not in error.value.message
            assert "upstream-key" not in error.value.message
            assert [url for url, _ in FakeClient.calls] == ["https://openrouter.ai/api/v1/auth/key"]
        FakeClient.calls = []
        FakeClient.responses = {
            "auth": FakeResponse(200, {"ok": True}),
            "models": FakeResponse(200, ValueError()),
        }
        with pytest.raises(providers.ProviderDiscoveryError) as error:
            await adapter.discover_models()
        assert "invalid" in error.value.message.lower()
        assert "sk-secret" not in error.value.message
    asyncio.run(scenario())


class FakeProvidersRepo:
    def __init__(self, provider):
        self.provider = provider

    async def get(self, provider_id, owner_id):
        return self.provider if provider_id == self.provider["id"] and owner_id == "owner" else None


class FakeModelsRepo:
    def __init__(self, model):
        self.model = model
        self.updated = None

    async def get(self, model_id, owner_id):
        return self.model if model_id == self.model["id"] and owner_id == "owner" else None

    async def update(self, model_id, owner_id, patch):
        self.updated = patch
        self.model = {**self.model, **patch}
        return self.model


def test_refresh_updates_metadata_without_credentials_or_runs(monkeypatch):
    async def scenario():
        provider = {"id": "provider-1", "owner_id": "owner", "provider_type": "openai_compatible", "enabled": True}
        model = {
            "id": "model-1", "owner_id": "owner", "provider_id": "provider-1",
            "model_identifier": "openai/gpt-test", "display_name": "Old", "input_cost": None,
            "output_cost": None,
        }
        models_repo = FakeModelsRepo(model)
        monkeypatch.setattr(registry.repo, "providers", FakeProvidersRepo(provider))
        monkeypatch.setattr(registry.repo, "models", models_repo)
        item = domain_models.ProviderModelCatalogItem(
            model_identifier="openai/gpt-test", display_name="New", context_window=64000,
            input_cost=1.25, output_cost=None, capabilities={"supported_parameters": ["tools"]},
            modalities={"input": ["text"], "output": ["text"]}, provider_metadata={"source": "openrouter"},
            pricing_source="openrouter", pricing_updated_at="2026-09-11T00:00:00+00:00",
        )

        class Adapter:
            supports_model_discovery = True

            async def discover_models(self):
                return [item]

        monkeypatch.setattr(registry, "get_adapter", lambda _: Adapter())
        result = await registry.refresh_model_metadata("model-1", {"id": "owner"})
        assert result["refreshed"] is True
        assert models_repo.updated["input_cost"] == 1.25
        assert models_repo.updated["input_price_per_1m"] == 1.25
        assert models_repo.updated["output_cost"] is None
        assert models_repo.updated["output_price_per_1m"] is None
        assert models_repo.updated["output_price_per_1m_tokens"] is None
        assert models_repo.updated["pricing_source"] == "openrouter"
        assert "runs" not in models_repo.updated
    asyncio.run(scenario())


def test_discovery_route_enforces_ownership_and_disabled_provider(monkeypatch):
    async def scenario():
        provider = {"id": "provider-1", "owner_id": "owner", "provider_type": "openai_compatible", "enabled": False}
        monkeypatch.setattr(registry.repo, "providers", FakeProvidersRepo(provider))
        with pytest.raises(registry.HTTPException) as error:
            await registry.discover_provider_models("provider-1", {"id": "owner"})
        assert error.value.status_code == 400
        with pytest.raises(registry.HTTPException) as error:
            await registry.discover_provider_models("provider-1", {"id": "other"})
        assert error.value.status_code == 404
    asyncio.run(scenario())
