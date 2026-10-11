"""OMNILAB V4.2.3.1 fix #1: sandbox readiness (offline, no docker needed).

Validates: cold sync snapshot, fresh probing, unavailable-sandbox reasons,
TTL cache reuse, force refresh, and probe coalescing under concurrency.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.services import code_interpreter as ci


def _run(coro):
    """Run a coroutine on a dedicated loop (independent of session portal)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def cold_cache():
    ci.reset_availability_cache()
    # reset lazily-created lock so tests never share loop-bound state
    ci._availability_lock = None
    yield
    ci.reset_availability_cache()
    ci._availability_lock = None


def test_cold_cache_sync_snapshot_without_permission_denied_shape(cold_cache):
    snap = ci.check_availability_sync()
    assert snap["available"] is False
    assert snap["reason"] == "Code interpreter status unknown"
    lowered = snap["reason"].lower()
    for pattern in ("permission denied", "eacces", "eperm", "403", "forbidden"):
        assert pattern not in lowered


def test_first_request_probes_and_returns_available(cold_cache, monkeypatch):
    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"[]", b""

    async def _fake_exec(*args, **kwargs):
        return _Proc()

    monkeypatch.setattr(ci.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(ci.shutil, "which", lambda _name: "/usr/bin/docker")

    result = _run(ci.check_availability())
    assert result["available"] is True
    assert result["reason"] == ""


def test_unavailable_sandbox_when_docker_missing(cold_cache, monkeypatch):
    monkeypatch.setattr(ci.shutil, "which", lambda _name: None)
    result = _run(ci.check_availability())
    assert result["available"] is False
    assert result["reason"]


def test_cached_result_reuse_within_ttl(cold_cache, monkeypatch):
    calls = {"n": 0}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"[]", b""

    async def _fake_exec(*args, **kwargs):
        calls["n"] += 1
        return _Proc()

    monkeypatch.setattr(ci.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(ci.shutil, "which", lambda _name: "/usr/bin/docker")

    first = _run(ci.check_availability())
    assert first["available"] is True
    assert calls["n"] == 1
    second = _run(ci.check_availability())
    assert second["available"] is True
    assert calls["n"] == 1, "second call within TTL must not probe again"


def test_force_refresh_bypasses_cache(cold_cache, monkeypatch):
    calls = {"n": 0}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"[]", b""

    async def _fake_exec(*args, **kwargs):
        calls["n"] += 1
        return _Proc()

    monkeypatch.setattr(ci.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(ci.shutil, "which", lambda _name: "/usr/bin/docker")

    assert _run(ci.check_availability())["available"] is True
    assert calls["n"] == 1
    assert _run(ci.check_availability(force=True))["available"] is True
    assert calls["n"] == 2, "force=True must bypass the TTL cache"


def test_concurrent_probes_coalesce(cold_cache, monkeypatch):
    calls = {"n": 0}

    class _Proc:
        returncode = 0

        async def communicate(self):
            await asyncio.sleep(0.05)
            return b"[]", b""

    async def _fake_exec(*args, **kwargs):
        calls["n"] += 1
        return _Proc()

    monkeypatch.setattr(ci.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(ci.shutil, "which", lambda _name: "/usr/bin/docker")
    # stale cache so every caller wants a probe, but exactly one must run
    ci._availability_cache["at"] = time.monotonic() - ci._AVAILABILITY_TTL_SECONDS - 1
    ci._availability_cache["ok"] = False
    ci._availability_cache["reason"] = "stale"

    async def _gather():
        return await asyncio.gather(*[ci.check_availability() for _ in range(10)])

    results = _run(_gather())
    assert all(r["available"] is True for r in results)
    assert calls["n"] == 1, f"expected exactly 1 probe, got {calls['n']}"
