"""Offline test fixtures.

A single session-scoped ``TestClient`` owns one event loop (via its blocking
portal); all application and Motor (MongoDB) work is funnelled through that
loop with ``client.portal.call``. This keeps the process-wide Motor client bound
to one loop for the whole session and guarantees deterministic DB access under
``-n 0``.

The real startup hooks (index creation / admin seeding) are patched to no-ops so
the suite never mutates production records.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient

import server as server_module
from app.deps import get_current_user
from app.models import now_iso

import app.services.provider_quota as provider_quota

from _support import (
    ALLOCATED_OWNERS,
    TRACKED_OWNERS,
    find_user_by_email,
    new_owner_id,
    purge_owner,
)


async def _noop() -> None:
    return None


@pytest.fixture(scope="session")
def app_client() -> Iterator[TestClient]:
    original_init = server_module.init_indexes
    original_seed = server_module.seed_admin
    server_module.init_indexes = _noop
    server_module.seed_admin = _noop
    client = TestClient(server_module.app, base_url="https://testserver")
    try:
        with client:
            yield client
    finally:
        server_module.init_indexes = original_init
        server_module.seed_admin = original_seed


def call(client: TestClient, func, *args, **kwargs):
    """Run an async callable on the client's portal loop."""
    if kwargs:
        async def _wrapped():
            return await func(*args, **kwargs)
        return client.portal.call(_wrapped)
    return client.portal.call(func, *args)


@pytest.fixture
def as_user(app_client: TestClient) -> Iterator[Dict[str, Any]]:
    """Authenticate as a throwaway researcher and purge everything afterwards."""
    owner_id = new_owner_id()
    user = {
        "id": owner_id,
        "email": f"{owner_id}@example.com",
        "name": "Pytest Researcher",
        "role": "researcher",
        "created_at": now_iso(),
    }
    app_client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield user
    finally:
        app_client.app.dependency_overrides.pop(get_current_user, None)
        call(app_client, purge_owner, owner_id)


@pytest.fixture
def auth_env(app_client: TestClient) -> Iterator[Dict[str, Any]]:
    """Cookie-auth environment with a unique throwaway email and full cleanup."""
    email = f"pytest-{uuid.uuid4().hex}@example.com"
    state: Dict[str, Any] = {"email": email, "user_id": None}
    try:
        yield {"client": app_client, **state}
        state["email"] = email
    finally:
        async def _cleanup():
            user = await find_user_by_email(email)
            if user:
                ALLOCATED_OWNERS.add(user["id"])
                await purge_owner(user["id"], [email])
        call(app_client, _cleanup)


@pytest.fixture(autouse=True)
def _reset_provider_quota():
    provider_quota.reset()
    yield
    provider_quota.reset()


@pytest.fixture(autouse=True, scope="session")
def _session_owner_guard():
    yield
    assert not TRACKED_OWNERS, f"throwaway owners were not purged: {sorted(TRACKED_OWNERS)}"
