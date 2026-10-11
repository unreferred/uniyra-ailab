"""V6.5 Agent Runtime.

The runtime resolves ONE frozen (or deprecated) agent version into the existing
runtime configuration and executes it through the canonical
:func:`app.services.execution.execute_model` funnel. It is deliberately NOT a
second execution engine: there is no parallel tool loop, no parallel trace
framework and no parallel prompt/context assembler. Every referenced asset
(prompt version, context packs, budget policy, tools, knowledge collections) is
resolved through its owning V6 service.

What the runtime adds is *resolution + provenance*:

* a frozen agent version's fixed model binding drives the model call;
* its composed prompt version is compiled with the real V6.3 compiler;
* its context packs and budget policy are resolved via V6.2 Context Lab;
* its tool policy is resolved through the canonical tool resolver;
* its knowledge policy is passed to the canonical context/knowledge funnel;
* its runtime policy is applied ONLY where the existing runtime genuinely
  supports it (temperature, output tokens, tool rounds, and reasoning effort
  only when the adapter AND model both declare support). Anything the runtime
  cannot enforce is reported as ``unsupported`` with a reason and never
  silently claimed as applied;
* after execution the resolved configuration is snapshotted onto the run with an
  additive ``repo.runs.update`` so the run is reproducible after the agent
  version changes.

All lookups are owner-scoped: a foreign or missing agent/version resolves to
:class:`AgentRuntimeNotFound` (HTTP 404); every other domain failure raises
:class:`AgentRuntimeError` (HTTP 400).
"""
from __future__ import annotations

import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from .. import providers
from .. import repository as repo
from . import agents as agents_service
from . import agent_permissions
from . import context_lab
from . import execution as execution_service
from . import prompt_compiler
from . import redaction
from . import tracing

__all__ = [
    "AgentRuntimeError",
    "AgentRuntimeNotFound",
    "resolve_runtime_policy",
    "run_agent_version",
    "list_agent_runs",
    "get_agent_run",
]


class AgentRuntimeError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class AgentRuntimeNotFound(AgentRuntimeError):
    """Owner-scoped resource missing (HTTP 404)."""


#: Runtime-policy fields this runtime intentionally cannot honor. They are
#: surfaced as ``unsupported`` with an explicit reason rather than dropped.
_UNSUPPORTED_RUNTIME_FIELDS = ("max_tool_calls", "max_runtime_seconds", "max_cost")

#: Reason string used when a requested effort cannot be forwarded because the
#: resolved adapter and/or model does not declare reasoning-effort support.
_REASON_NOT_SUPPORTED_BY_MODEL = "not_supported_by_model"
_REASON_NOT_SUPPORTED_BY_RUNTIME = "not_supported_by_runtime"


# ------------------------------------------------------------------ helpers
def _adapter_for(provider: Dict[str, Any]):
    """Resolve the adapter through the services package seam.

    Mirrors ``execution._adapter_for`` so a test that monkeypatches
    ``app.services.get_adapter`` is observed here too.
    """
    pkg = sys.modules.get(__package__)
    factory = (
        getattr(pkg, "get_adapter", providers.get_adapter)
        if pkg is not None
        else providers.get_adapter
    )
    return factory(provider)


def _target_for(enabled_tool_ids: List[str]) -> str:
    """The compiler target for a resolved tool policy."""
    return "openai_tools" if enabled_tool_ids else "generic_chat"


def _redact_strings(value: Any) -> Any:
    """Redact secret-shaped STRING values while preserving numeric telemetry.

    ``redaction.redact`` treats any key containing ``token`` (for example
    ``max_context_tokens``) as sensitive and replaces its *value* with the
    redaction marker, which would destroy the context budget provenance. This
    walker instead scrubs only string content, so ``*_tokens`` ints survive
    while secret-shaped text is still removed.
    """
    if isinstance(value, str):
        return redaction.redact_text(value)
    if isinstance(value, dict):
        return {key: _redact_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_strings(item) for item in value]
    return value


def _is_reasoning_effort_supported(adapter: Any, model: Any) -> bool:
    """Adapter AND model must both declare reasoning-effort support."""
    try:
        if not providers.adapter_supports_reasoning_effort(adapter):
            return False
        return bool(providers.model_reasoning_effort_capability(model))
    except Exception:  # pragma: no cover - capability probing is defensive
        return False


# ------------------------------------------------------------------ context packs
async def _resolve_context_packs(
    owner_id: str, project_id: str, version: Dict[str, Any]
) -> Tuple[Optional[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve + concatenate a frozen agent version's context packs.

    Every referenced pack version must exist owner-and-project scoped and be
    ``frozen`` or ``deprecated``; a ``draft`` pack version raises (it is not a
    stable, reproducible input). Items are concatenated deterministically: pack
    reference order first, then each pack's item order/key.

    Each item's content passes through :func:`redaction.redact_text` before it
    is used, so secret-shaped pack content can never reach the model-visible
    system prompt or the persisted project context. Returns the concatenated
    (redacted) project-context text, the applied reference descriptors, and the
    redacted items shaped for the prompt compiler's untrusted boundary.
    """
    policy = version.get("context_policy") or {}
    version_ids = list(policy.get("context_pack_version_ids") or [])
    if not version_ids:
        return None, [], []

    parts: List[str] = []
    applied: List[Dict[str, Any]] = []
    untrusted_items: List[Dict[str, Any]] = []
    for version_id in version_ids:
        pack_version = await repo.context_pack_versions.get(version_id, owner_id)
        if not pack_version or pack_version.get("project_id") != project_id:
            raise AgentRuntimeError(
                f"Context pack version '{version_id}' was not found"
            )
        status = pack_version.get("status")
        if status not in ("frozen", "deprecated"):
            raise AgentRuntimeError(
                "Context pack version must be frozen before execution"
            )
        pack = await repo.context_packs.get(pack_version.get("pack_id"), owner_id)
        name = (
            (pack or {}).get("name")
            or pack_version.get("pack_id")
            or version_id
        )
        items = [item for item in (pack_version.get("items") or []) if isinstance(item, dict)]
        items = sorted(
            items,
            key=lambda item: (
                int(item.get("order") or 0),
                str(item.get("key") or ""),
            ),
        )
        lines = [f"## Context Pack: {name} (version {pack_version.get('version')})"]
        if pack_version.get("description"):
            lines.append(str(pack_version.get("description")))
        redaction_applied = False
        for item in items:
            title = item.get("title") or item.get("key") or "item"
            raw_content = str(item.get("content") or "")
            safe_content = redaction.redact_text(raw_content)
            if safe_content != raw_content:
                redaction_applied = True
            lines.append(f"### {title}")
            lines.append(safe_content)
            untrusted_items.append({
                "source": "context_pack",
                "title": f"{name} / {title}",
                "content": safe_content,
            })
        parts.append("\n".join(lines))
        applied.append({
            "pack_version_id": version_id,
            "pack_id": pack_version.get("pack_id"),
            "name": name,
            "version": pack_version.get("version"),
            "status": status,
            "item_count": len(items),
            "redaction_applied": redaction_applied,
        })
    return "\n\n".join(parts), applied, untrusted_items


# ------------------------------------------------------------------ runtime policy
def resolve_runtime_policy(
    runtime_policy: Optional[Dict[str, Any]],
    *,
    model: Any,
    enabled_tool_ids: List[str],
    requested_max_context_tokens: Optional[int],
    adapter: Any = None,
) -> Dict[str, Any]:
    """Map a declared runtime policy onto what the existing runtime supports.

    Returns ``{"applied", "unsupported", "params"}``. Only honestly enforceable
    fields are placed in ``applied``/``params``; every other declared field is
    reported in ``unsupported`` with an explicit reason. Absent fields are
    omitted entirely (never fabricated).

    ``enabled_tool_ids`` is accepted so callers resolve the policy against the
    same tool configuration the run will use; ``max_tool_rounds`` is only
    meaningful when tools are enabled but is recorded whenever declared.
    """
    policy = runtime_policy if isinstance(runtime_policy, dict) else {}
    applied: Dict[str, Any] = {}
    unsupported: List[Dict[str, str]] = []
    params: Dict[str, Any] = {}

    if policy.get("temperature") is not None:
        params["temperature"] = policy["temperature"]
        applied["temperature"] = policy["temperature"]

    if policy.get("max_output_tokens") is not None:
        params["max_tokens"] = policy["max_output_tokens"]
        applied["max_output_tokens"] = policy["max_output_tokens"]

    rounds = policy.get("max_tool_rounds")
    if rounds is None:
        rounds = policy.get("max_iterations")
    if rounds is not None:
        try:
            applied["max_tool_rounds"] = int(rounds)
        except (TypeError, ValueError):
            applied["max_tool_rounds"] = rounds

    if policy.get("max_input_tokens") is not None:
        applied["max_context_tokens"] = policy["max_input_tokens"]
    elif requested_max_context_tokens is not None:
        applied["max_context_tokens"] = requested_max_context_tokens

    if policy.get("reasoning_effort") is not None:
        if _is_reasoning_effort_supported(adapter, model):
            params["reasoning_effort"] = policy["reasoning_effort"]
            applied["reasoning_effort"] = policy["reasoning_effort"]
        else:
            unsupported.append({
                "field": "reasoning_effort",
                "reason": _REASON_NOT_SUPPORTED_BY_MODEL,
            })

    for field in _UNSUPPORTED_RUNTIME_FIELDS:
        if policy.get(field) is not None:
            unsupported.append({
                "field": field,
                "reason": _REASON_NOT_SUPPORTED_BY_RUNTIME,
            })

    return {"applied": applied, "unsupported": unsupported, "params": params}


# ------------------------------------------------------------------ loading
async def _load(owner_id: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    agent_version_id = payload.get("agent_version_id")
    project_id = payload.get("project_id")

    version = (
        await repo.agent_versions.get(agent_version_id, owner_id)
        if agent_version_id
        else None
    )
    # V6.8: agent_id is optional. When the caller omits it (arena/benchmark
    # dispatch) it is derived from the loaded agent version; a foreign or
    # mismatched agent id still resolves to 404 exactly as before.
    agent_id = payload.get("agent_id") or (version or {}).get("agent_id")
    agent = await repo.agents.get(agent_id, owner_id) if agent_id else None
    if not agent:
        raise AgentRuntimeNotFound("Agent not found")
    if not version or version.get("agent_id") != agent["id"]:
        raise AgentRuntimeNotFound("Agent version not found")

    if not project_id or not await repo.projects.get(project_id, owner_id):
        raise AgentRuntimeError("Project not found")
    if agent.get("project_id") != project_id or version.get("project_id") != project_id:
        raise AgentRuntimeError("Agent version does not belong to project")

    return agent, agents_service.normalize_version(version), project_id


# ------------------------------------------------------------------ main
async def run_agent_version(
    owner_id: str,
    payload: Dict[str, Any],
    *,
    experiment: Optional[Dict[str, Any]] = None,
    experiment_id: Optional[str] = None,
    configuration_id: Optional[str] = None,
    dataset_case_id: Optional[str] = None,
    dataset_version_id: Optional[str] = None,
    repetition_index: Optional[int] = None,
    benchmark_plan_id: Optional[str] = None,
    attempt_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Resolve one frozen agent version, execute it, snapshot provenance.

    The keyword-only benchmark context (V6.8) lets the existing benchmark runner
    dispatch an agent-bound configuration through this runtime while preserving
    the exact Run identity (configuration/case/repetition/attempt/plan) the
    runner uses for idempotency, retries, cancellation and auto-evaluation.
    Non-benchmark callers are unaffected: every keyword defaults to ``None`` and
    payload values are used as before.
    """
    payload = payload or {}
    agent, version, project_id = await _load(owner_id, payload)

    status = version.get("status")
    if status not in ("frozen", "deprecated"):
        raise AgentRuntimeError("Agent version must be frozen before execution")

    # V6.8 Prompt Arena: an explicit prompt override lets one agent version run
    # several frozen prompt variants while keeping its fixed model/tool/knowledge
    # binding. Direct runtime callers without an override are unaffected.
    prompt_version_id = payload.get("prompt_version_id") or version.get(
        "prompt_version_id"
    )
    if not prompt_version_id:
        raise AgentRuntimeError(
            "Agent version must bind a prompt version before execution"
        )

    # --- context packs + budget policy -----------------------------------
    context_policy = version.get("context_policy") or {}
    project_context_text, context_pack_refs, context_pack_untrusted = (
        await _resolve_context_packs(owner_id, project_id, version)
    )
    requested_max_context_tokens: Optional[int] = None
    budget_policy = None
    budget_policy_id = context_policy.get("budget_policy_id")
    if budget_policy_id:
        try:
            budget_policy = await context_lab.resolve_budget_policy(
                owner_id, project_id, budget_policy_id
            )
        except context_lab.ContextLabError as exc:
            raise AgentRuntimeError(str(exc))
        if budget_policy is not None:
            requested_max_context_tokens = budget_policy.get("max_context_tokens")

    # --- model + adapter (resolved early so capability gates are truthful) ---
    model = await repo.models.get(version.get("model_id"), owner_id)
    adapter = None
    try:
        provider = await repo.providers.get((model or {}).get("provider_id"), owner_id)
        if provider is not None:
            adapter = _adapter_for(provider)
    except Exception:  # pragma: no cover - adapter probing is best-effort
        adapter = None

    # --- permission boundary (V6.6) ---------------------------------------
    # Permissions come ONLY from the frozen version configuration. Task text,
    # compiled prompt, context packs and tool output are never consulted, so
    # prompt injection cannot escalate tool or knowledge authority. Capability
    # (what the model can call) is reported separately from permission (what
    # the version allows); a mismatch becomes an explicit denial.
    permission_resolution = await agent_permissions.resolve_agent_permissions(
        owner_id,
        project_id,
        version,
        model=model,
        adapter=adapter,
    )
    tool_policy_applied = permission_resolution["tool_policy_applied"]
    knowledge_policy_applied = permission_resolution["knowledge_policy_applied"]
    permission_denials = list(permission_resolution["permission_denials"])
    permission_unsupported = list(permission_resolution["policy_unsupported"])

    default_deny = bool(tool_policy_applied["default_deny"])
    allowed_tool_ids = list(tool_policy_applied["allowed_tool_ids"])
    denied_tool_ids = list(tool_policy_applied["denied_tool_ids"])
    enabled_tool_ids = list(tool_policy_applied["enabled_tool_ids"])

    knowledge_enabled = bool(knowledge_policy_applied["knowledge_enabled"])
    knowledge_collection_ids = list(
        knowledge_policy_applied["allowed_collection_ids"]
    )
    retrieval_top_k = knowledge_policy_applied.get("retrieval_top_k")
    retrieval_options: Dict[str, Any] = {}
    if retrieval_top_k is not None:
        retrieval_options["top_k"] = retrieval_top_k

    # --- prompt compilation (real V6.3 compiler) --------------------------
    try:
        compiled = await prompt_compiler.compile_prompt(
            owner_id,
            {
                "project_id": project_id,
                "prompt_version_id": prompt_version_id,
                "agent_version_id": version["id"],
                "target": _target_for(enabled_tool_ids),
                "mode": "structured",
                "variables": dict(payload.get("variables") or {}),
                "block_ids": [],
                "policy_ids": [],
                "include_policies": True,
                "include_invariants": True,
                "context_pack_version_ids": list(
                    context_policy.get("context_pack_version_ids") or []
                ),
                "untrusted_context": context_pack_untrusted,
                "tool_outputs": [],
                "tool_definitions": [],
            },
            persist=True,
        )
    except prompt_compiler.PromptCompilerError as exc:
        raise AgentRuntimeError(str(exc))

    compiled_messages = list(compiled.get("messages") or [])
    system_prompt = compiled_messages[0].get("content") if compiled_messages else ""
    messages = compiled_messages[1:] + [
        {"role": "user", "content": payload.get("task") or ""}
    ]

    # --- runtime policy (honest apply/report) -----------------------------
    runtime_policy = version.get("runtime_policy") or {}
    runtime_resolution = resolve_runtime_policy(
        runtime_policy,
        model=model or {},
        enabled_tool_ids=enabled_tool_ids,
        requested_max_context_tokens=requested_max_context_tokens,
        adapter=adapter,
    )
    # Merge V6.6 permission-policy ``unsupported`` fields (dedupe by field) so
    # the snapshot never claims to enforce something the runtime does not.
    _unsupported_fields = {
        entry.get("field") for entry in runtime_resolution["unsupported"]
    }
    for entry in permission_unsupported:
        if entry.get("field") not in _unsupported_fields:
            runtime_resolution["unsupported"].append(entry)
            _unsupported_fields.add(entry.get("field"))

    params = dict(runtime_resolution["params"])
    max_tool_rounds = runtime_resolution["applied"].get("max_tool_rounds")

    reserved_output_tokens = payload.get("reserved_output_tokens")
    if reserved_output_tokens is None and budget_policy is not None:
        reserved_output_tokens = budget_policy.get("reserve_output_tokens")

    if experiment_id is None:
        experiment_id = payload.get("experiment_id")
    if experiment_id is None and experiment is not None:
        experiment_id = experiment.get("id")
    if configuration_id is None:
        configuration_id = payload.get("configuration_id")
    if dataset_case_id is None:
        dataset_case_id = payload.get("dataset_case_id")
    if dataset_version_id is None:
        dataset_version_id = payload.get("dataset_version_id")
    if repetition_index is None:
        repetition_index = payload.get("repetition_index")
    if benchmark_plan_id is None:
        benchmark_plan_id = payload.get("benchmark_plan_id")
    if attempt_index is None:
        attempt_index = payload.get("attempt_index")
    # Benchmark-dispatched agent runs are persisted with execution_kind
    # "benchmark" so the existing runner queries (idempotency/auto-eval) and
    # evaluation aggregation observe them; a direct runtime call stays "agent".
    execution_kind = "benchmark" if benchmark_plan_id is not None else "agent"
    context_strategy = context_policy.get("strategy") or "FULL"
    context_options = context_policy.get("context_options") or {}
    project_context = (
        {"context_packs": project_context_text} if project_context_text else None
    )

    # --- tracing (best-effort; the passed trace is reused by execute_model) ---
    trace: Optional[Dict[str, Any]] = None
    try:
        trace = await tracing.start_trace(
            owner_id,
            "agent",
            project_id=project_id,
            experiment_id=experiment_id,
            metadata=redaction.redact({
                "agent_id": agent["id"],
                "agent_version_id": version["id"],
                "compiled_prompt_id": compiled.get("id"),
                "prompt_version_id": prompt_version_id,
                "model_id": version.get("model_id"),
            }),
        )
        config_span = await tracing.start_span(
            owner_id,
            trace["id"],
            "AGENT_CONFIGURATION",
            "agent_configuration",
            metadata=_redact_strings({
                "tool_policy_applied": {
                    "default_deny": default_deny,
                    "allowed_tool_ids": allowed_tool_ids,
                    "denied_tool_ids": denied_tool_ids,
                    "enabled_tool_ids": enabled_tool_ids,
                    "model_capability": dict(
                        tool_policy_applied.get("model_capability") or {}
                    ),
                },
                "knowledge_policy_applied": {
                    "knowledge_enabled": knowledge_enabled,
                    "allowed_collection_ids": knowledge_collection_ids,
                    "requested_collection_ids": list(
                        knowledge_policy_applied.get("requested_collection_ids")
                        or []
                    ),
                    "retrieval_top_k": retrieval_top_k,
                },
                "context_policy_applied": {
                    "context_pack_version_ids": [
                        ref["pack_version_id"] for ref in context_pack_refs
                    ],
                    "strategy": context_strategy,
                },
                "runtime_policy_applied": {
                    "applied": dict(runtime_resolution["applied"]),
                    "unsupported": list(runtime_resolution["unsupported"]),
                },
                "permission_denial_count": len(permission_denials),
            }),
        )
        await tracing.finish_span(config_span)
    except Exception:  # pragma: no cover - telemetry is best-effort
        trace = trace if isinstance(trace, dict) else None

    execution_span_started = time.perf_counter()
    execution_span = None
    if trace is not None:
        try:
            execution_span = await tracing.start_span(
                owner_id,
                trace["id"],
                "AGENT_EXECUTION",
                "agent_execution",
                metadata={
                    "agent_id": agent["id"],
                    "agent_version_id": version["id"],
                    "model_id": version.get("model_id"),
                },
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            execution_span = None

    # --- canonical execution ---------------------------------------------
    run = await execution_service.execute_model(
        owner_id,
        version.get("model_id"),
        system_prompt,
        messages,
        params,
        project_id=project_id,
        experiment_id=experiment_id,
        execution_kind=execution_kind,
        trace_id=(trace or {}).get("id"),
        trace_type="agent",
        configuration_id=configuration_id,
        dataset_case_id=dataset_case_id,
        dataset_version_id=dataset_version_id,
        repetition_index=repetition_index,
        benchmark_plan_id=benchmark_plan_id,
        attempt_index=attempt_index,
        enabled_tool_ids=enabled_tool_ids,
        max_tool_rounds=max_tool_rounds,
        knowledge_enabled=knowledge_enabled,
        knowledge_collection_ids=knowledge_collection_ids,
        retrieval_options=retrieval_options,
        context_strategy=context_strategy,
        context_options=context_options,
        project_context=project_context,
        reserved_output_tokens=reserved_output_tokens,
        attachments=payload.get("attachments") or None,
    )

    run_id = run.get("id")
    run_status = run.get("status")

    if execution_span is not None:
        try:
            await tracing.finish_span(
                execution_span,
                status="completed" if run_status == "success" else "error",
                metadata={
                    "run_id": run_id,
                    "status": run_status,
                    "tool_call_count": run.get("tool_call_count") or 0,
                    "latency_ms": run.get("latency_ms"),
                },
                started_perf=execution_span_started,
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass

    # The shared trace is owned by this runtime (execute_model never finalizes a
    # supplied trace_id), so finalize it exactly once here.
    if trace is not None:
        try:
            await tracing.finish_trace(
                trace, status="completed" if run_status == "success" else "error"
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass

    # --- additive provenance snapshot on the run --------------------------
    applied_context_tokens = runtime_resolution["applied"].get("max_context_tokens")
    if applied_context_tokens is None:
        applied_context_tokens = requested_max_context_tokens

    provenance = {
        "agent_id": agent["id"],
        "agent_version_id": version["id"],
        "agent_version_snapshot": {
            "id": version["id"],
            "version": version.get("version"),
            "status": version.get("status"),
            "model_id": version.get("model_id"),
            "prompt_version_id": prompt_version_id,
            "content_hash": version.get("content_hash"),
            "output_contract": version.get("output_contract") or {},
            "routing": version.get("routing") or {},
        },
        "compiled_prompt_id": compiled.get("id"),
        "prompt_version_id": prompt_version_id,
        "runtime_policy": _redact_strings(runtime_policy),
        "runtime_policy_applied": runtime_resolution,
        "output_contract": version.get("output_contract") or {},
        "context_policy_applied": {
            "context_pack_version_ids": [
                ref["pack_version_id"] for ref in context_pack_refs
            ],
            "context_packs": context_pack_refs,
            "max_context_tokens": applied_context_tokens,
            "strategy": context_strategy,
            "budget_policy_id": budget_policy_id,
            "context_options": context_options,
        },
        "tool_policy_applied": {
            "default_deny": default_deny,
            "allowed_tool_ids": allowed_tool_ids,
            "denied_tool_ids": denied_tool_ids,
            "enabled_tool_ids": enabled_tool_ids,
            "resolved_tool_ids": list(enabled_tool_ids),
            "model_capability": dict(
                tool_policy_applied.get("model_capability") or {}
            ),
            "permission_denials": permission_denials,
        },
        "knowledge_policy_applied": {
            "knowledge_enabled": knowledge_enabled,
            "allowed_collection_ids": knowledge_collection_ids,
            "requested_collection_ids": list(
                knowledge_policy_applied.get("requested_collection_ids") or []
            ),
            "retrieval_top_k": retrieval_top_k,
            "citation_required": bool(
                knowledge_policy_applied.get("citation_required", False)
            ),
            "knowledge_only_mode": bool(
                knowledge_policy_applied.get("knowledge_only_mode", False)
            ),
            "permission_denials": permission_denials,
        },
        "permission_denials": permission_denials,
    }
    provenance = _redact_strings(provenance)

    updated_run = run
    try:
        patched = await repo.runs.update(run_id, owner_id, provenance)
        if patched is not None:
            updated_run = patched
    except Exception:  # pragma: no cover - provenance snapshot is best-effort
        updated_run = run

    return {
        "run": updated_run,
        "run_id": run_id,
        "trace_id": run.get("trace_id"),
        "compiled_prompt_id": compiled.get("id"),
        "agent_id": agent["id"],
        "agent_version_id": version["id"],
        "runtime_policy_applied": runtime_resolution,
        "output_contract": version.get("output_contract") or {},
    }


# ------------------------------------------------------------------ reads
_RUN_LIST_FIELDS = (
    "id",
    "owner_id",
    "project_id",
    "experiment_id",
    "agent_id",
    "agent_version_id",
    "compiled_prompt_id",
    "prompt_version_id",
    "model_id",
    "model_name",
    "status",
    "execution_kind",
    "enabled_tool_ids",
    "knowledge_enabled",
    "knowledge_collection_ids",
    "context_strategy",
    "tool_call_count",
    "latency_ms",
    "trace_id",
    "created_at",
)


def _run_summary(run: Dict[str, Any]) -> Dict[str, Any]:
    summary = {field: run.get(field) for field in _RUN_LIST_FIELDS}
    summary["runtime_policy_applied"] = run.get("runtime_policy_applied")
    summary["output_contract"] = run.get("output_contract")
    return _redact_strings(summary)


async def list_agent_runs(
    owner_id: str,
    *,
    agent_version_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Owner-scoped, newest-first listing of agent runs."""
    query: Dict[str, Any] = {"owner_id": owner_id}
    if agent_version_id:
        query["agent_version_id"] = agent_version_id
    if agent_id:
        query["agent_id"] = agent_id
    if project_id:
        query["project_id"] = project_id

    limit = max(int(limit), 1)
    offset = max(int(offset), 0)
    total = await repo.runs.count(query)
    docs = await repo.runs.list(
        query,
        sort_field="created_at",
        descending=True,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_run_summary(doc) for doc in docs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def get_agent_run(owner_id: str, run_id: str) -> Dict[str, Any]:
    """Owner-scoped agent run detail with its compiled prompt and trace presence."""
    run = await repo.runs.get(run_id, owner_id)
    if not run:
        raise AgentRuntimeNotFound("Agent run not found")

    compiled_prompt = None
    compiled_prompt_id = run.get("compiled_prompt_id")
    if compiled_prompt_id:
        compiled_prompt = await repo.compiled_prompts.get(compiled_prompt_id, owner_id)

    trace = None
    trace_id = run.get("trace_id")
    if trace_id:
        trace_doc = await tracing.get_trace(owner_id, trace_id)
        trace = (
            {
                "id": trace_doc.get("id"),
                "trace_type": trace_doc.get("trace_type"),
                "status": trace_doc.get("status"),
                "span_count": trace_doc.get("span_count"),
            }
            if trace_doc
            else {"id": trace_id, "trace_type": None, "status": None, "span_count": None}
        )

    return {
        "run": _redact_strings(run),
        "run_id": run.get("id"),
        "compiled_prompt": _redact_strings(compiled_prompt) if compiled_prompt else None,
        "trace": trace,
        "runtime_policy_applied": run.get("runtime_policy_applied"),
        "output_contract": run.get("output_contract"),
        # V6.6 permission-boundary provenance (redacted).
        "permission_denials": _redact_strings(run.get("permission_denials") or []),
        "tool_policy_applied": _redact_strings(run.get("tool_policy_applied") or {}),
        "knowledge_policy_applied": _redact_strings(
            run.get("knowledge_policy_applied") or {}
        ),
    }
