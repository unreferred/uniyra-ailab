"""Shared, offline-only test support for the OmniLab backend suite.

Nothing here talks to a real LLM. Provider/model/user/project records are
created against throwaway owner ids prefixed with ``pytest-`` and are always
purged by the fixtures in ``conftest.py``; production data is never touched.
"""
from __future__ import annotations

import inspect
import uuid
from typing import Any, Callable, Dict, Iterable, Optional

from app import ratelimit
from app import repository as repo
from app.database import db
from app.errors import make_error
from app.models import new_id, now_iso
from app.providers import ExecutionResult

TEST_OWNER_PREFIX = "pytest-"

# Collections that carry an ``owner_id`` field. Users are keyed by ``id``.
OWNER_COLLECTIONS = (
    "projects",
    "providers",
    "models",
    "rubrics",
    "conversations",
    "messages",
    "experiments",
    "runs",
    "evaluations",
    "artifacts",
    "usage_events",
    "councils",
    "research_sessions",
    "findings",
    "datasets",
    "dataset_cases",
    "dataset_versions",
    "dataset_version_cases",
    "prompts",
    "prompt_versions",
    "experiment_configs",
    "benchmark_plans",
    "reveal_events",
    "auth_sessions",
    # Phase 3 observability / context / tools. ``tools`` is included even
    # though global builtins carry owner_id None: purge is scoped to the
    # throwaway pytest owner, so real builtins are never touched.
    "traces",
    "trace_spans",
    "context_snapshots",
    "attachments",
    "tool_calls",
    "failures",
    "tools",
    # Phase 4 workspace platform collections scoped by owner id.
    "tool_credentials",
    "valve_values",
    # Phase 5 research quality collections scoped by owner id.
    "regression_runs",
    "rubric_versions",
    "scorecards",
    "certification_candidates",
    # Phase V6 prompt & policy foundation collections scoped by owner id.
    "prompt_blocks",
    "project_policies",
    "project_invariants",
    # Phase V6.2 Context Lab collections scoped by owner id.
    "context_packs",
    "context_pack_versions",
    "context_budget_policies",
    # Phase V6.3 Prompt Compiler collection scoped by owner id.
    "compiled_prompts",
    # Phase V6.4 Agent Registry collections scoped by owner id.
    "agents",
    "agent_versions",
    # Phase V6.14 Workspace Prompt Capture collection scoped by owner id.
    "prompt_captures",
    # Phase V6.14 Planning Decision Memory collections scoped by owner id.
    "planning_decisions",
    "planning_conflicts",
    # Phase V6.15 Structured Planning Graph collections scoped by owner id.
    "planning_nodes",
    "planning_edges",
    # Phase V6.15 Living PRD collection scoped by owner id.
    "planning_prds",
    # Phase V6.9 Drift & Failure Analysis collection scoped by owner id.
    "drift_analyses",
)

# Owner ids still expected to be purged (cleared by purge_owner).
TRACKED_OWNERS: set = set()

# Every owner id ever handed out in this process (never cleared) so the final
# leftover test can prove none of their records survive.
ALLOCATED_OWNERS: set = set()


def new_owner_id() -> str:
    owner_id = f"{TEST_OWNER_PREFIX}{uuid.uuid4().hex}"
    TRACKED_OWNERS.add(owner_id)
    ALLOCATED_OWNERS.add(owner_id)
    return owner_id


async def purge_owner(owner_id: str, emails: Iterable[str] = ()) -> None:
    """Delete every throwaway record scoped to ``owner_id`` (never real data)."""
    for collection in OWNER_COLLECTIONS:
        await db[collection].delete_many({"owner_id": owner_id})
    await db.auth_sessions.delete_many({"user_id": owner_id})
    await db.users.delete_many({"id": owner_id})
    for email in emails:
        identifier = ratelimit._identifier("login", f"testclient:{email}")
        await db.login_attempts.delete_many({"identifier": identifier})
        await ratelimit.reset_rate_limit("login", f"testclient:{email}")
    await ratelimit.reset_rate_limit("register", "testclient")
    TRACKED_OWNERS.discard(owner_id)


async def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    return await repo.users.find_one({"email": email})


async def owner_record_counts(owner_id: str) -> Dict[str, int]:
    """Non-zero collection counts for a throwaway owner (empty when clean)."""
    counts: Dict[str, int] = {}
    for collection in OWNER_COLLECTIONS:
        total = await db[collection].count_documents({"owner_id": owner_id})
        if total:
            counts[collection] = total
    users = await db.users.count_documents({"id": owner_id})
    if users:
        counts["users"] = users
    return counts


# ------------------------------------------------------------------ registry helpers
async def create_provider(
    owner_id: str,
    *,
    name: Optional[str] = None,
    provider_type: str = "openai_compatible",
    base_url: str = "https://api.openai.com/v1",
    enabled: bool = True,
) -> str:
    provider_id = new_id()
    now = now_iso()
    await repo.providers.insert({
        "id": provider_id,
        "owner_id": owner_id,
        "name": name or f"pytest-provider-{provider_id[:8]}",
        "provider_type": provider_type,
        "base_url": base_url,
        "api_key_enc": None,
        "enabled": enabled,
        "seeded": False,
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    })
    return provider_id


async def create_model(
    owner_id: str,
    *,
    provider_id: Optional[str] = None,
    display_name: Optional[str] = None,
    model_identifier: Optional[str] = None,
    enabled: bool = True,
    input_price_per_1m: Optional[float] = None,
    output_price_per_1m: Optional[float] = None,
    family: str = "openai",
    capabilities: Optional[Dict[str, Any]] = None,
) -> str:
    if provider_id is None:
        provider_id = await create_provider(owner_id)
    model_id = new_id()
    now = now_iso()
    await repo.models.insert({
        "id": model_id,
        "owner_id": owner_id,
        "provider_id": provider_id,
        "display_name": display_name or f"pytest-model-{model_id[:8]}",
        "model_identifier": model_identifier or f"pytest/{model_id}",
        "family": family,
        "enabled": enabled,
        "context_window": 8192,
        "input_cost": input_price_per_1m,
        "output_cost": output_price_per_1m,
        "input_price_per_1m": input_price_per_1m,
        "output_price_per_1m": output_price_per_1m,
        "input_price_per_1m_tokens": input_price_per_1m,
        "output_price_per_1m_tokens": output_price_per_1m,
        "capabilities": capabilities or {},
        "modalities": {},
        "provider_metadata": {},
        "tags": [],
        "notes": "",
        "seeded": False,
        "created_at": now,
        "updated_at": now,
    })
    return model_id


async def create_project(owner_id: str, name: Optional[str] = None) -> str:
    project_id = new_id()
    now = now_iso()
    await repo.projects.insert({
        "id": project_id,
        "owner_id": owner_id,
        "name": name or f"pytest-project-{project_id[:8]}",
        "description": "",
        "tags": [],
        "status": "active",
        "created_at": now,
        "updated_at": now,
    })
    return project_id


# ------------------------------------------------------------------ fake adapters
class ScriptedAdapter:
    """Offline provider adapter driven by caller-supplied handlers."""

    supports_model_discovery = False

    def __init__(
        self,
        executor: Callable[[Dict[str, Any], Any], Any],
        streamer: Optional[Callable[[Dict[str, Any], Any], Any]] = None,
    ):
        self._executor = executor
        self._streamer = streamer

    @property
    def capabilities(self) -> Dict[str, bool]:
        return {"model_discovery": False, "streaming": self._streamer is not None}

    async def execute(self, model: Dict[str, Any], req: Any) -> ExecutionResult:
        result = self._executor(model, req)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def stream(self, model: Dict[str, Any], req: Any):
        source = self._streamer(model, req)
        if inspect.isawaitable(source):
            source = await source
        async for event in source:
            yield event

    async def discover_models(self):
        raise AssertionError("discover_models must not be called in offline tests")


def adapter_factory(executor, streamer=None):
    """Return a ``get_adapter`` replacement closure."""
    return lambda provider: ScriptedAdapter(executor, streamer)


def result_success(
    text: str = "generic answer",
    *,
    input_tokens: Optional[int] = 10,
    output_tokens: Optional[int] = 20,
    latency_ms: int = 5,
    ttft_ms: Optional[int] = None,
    finish_reason: Optional[str] = "stop",
    cached_tokens: Optional[int] = None,
    reasoning_tokens: Optional[int] = None,
    streaming: bool = False,
) -> ExecutionResult:
    return ExecutionResult(
        status="success",
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        finish_reason=finish_reason,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
        streaming=streaming,
    )


def result_error(
    category: str = "PROVIDER",
    message: str = "Provider request failed",
    *,
    code: Optional[str] = None,
    retryable: bool = False,
    upstream_status: Optional[int] = None,
    latency_ms: int = 3,
) -> ExecutionResult:
    error = make_error(
        code or f"{category}_ERROR",
        category,
        message,
        retryable=retryable,
        source="pytest",
        upstream_status=upstream_status,
    )
    return ExecutionResult(
        status="error",
        error=error["message"],
        error_detail=error,
        latency_ms=latency_ms,
    )


def lookup(map_by_key: Dict[str, Any], model: Dict[str, Any]) -> Any:
    """Resolve a per-model handler by identifier (falling back to display id)."""
    key = model.get("model_identifier") or model.get("id")
    if key in map_by_key:
        return map_by_key[key]
    return map_by_key.get(model.get("id"))


def adapter_executor(map_by_key: Dict[str, Any]):
    """Build an executor that dispatches to values from a per-model mapping.

    Values may be an ``ExecutionResult`` (returned as-is) or a callable
    ``(model, req) -> ExecutionResult``.
    """
    def executor(model, req):
        value = lookup(map_by_key, model)
        if callable(value):
            return value(model, req)
        if value is None:
            return result_success()
        return value
    return executor


def stream_from_results(*events: Dict[str, Any]):
    async def streamer(model, req):
        for event in events:
            yield event
    return streamer


# ------------------------------------------------------------------ Phase 3 helpers
class CapableAdapter(ScriptedAdapter):
    """``ScriptedAdapter`` that declares tool/vision support for capability gates.

    ``providers``/``execution`` inspect capability support with plain
    ``getattr(adapter, "supports_tools"|"supports_vision", False)``, so a test
    double must expose those attributes as *instance* flags.
    """

    supports_model_discovery = False
    supports_tools = False
    supports_vision = False

    def __init__(
        self,
        executor: Callable[[Dict[str, Any], Any], Any],
        streamer: Optional[Callable[[Dict[str, Any], Any], Any]] = None,
        *,
        tools: bool = False,
        vision: bool = False,
    ):
        super().__init__(executor, streamer)
        self.supports_tools = bool(tools)
        self.supports_vision = bool(vision)

    @property
    def capabilities(self) -> Dict[str, bool]:
        return {
            "model_discovery": bool(self.supports_model_discovery),
            "streaming": self._streamer is not None,
        }


def result_with_tools(
    text: str = "",
    tool_calls: Optional[Iterable[Dict[str, Any]]] = None,
    *,
    input_tokens: Optional[int] = 10,
    output_tokens: Optional[int] = 20,
    latency_ms: int = 5,
    finish_reason: Optional[str] = "tool_calls",
    **kwargs: Any,
) -> ExecutionResult:
    """A successful result carrying one or more normalized tool calls."""
    return ExecutionResult(
        status="success",
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        tool_calls=list(tool_calls or []),
        **kwargs,
    )
