"""V6.4 Agent Registry & Versioning.

An Agent is a first-class, project-scoped asset with an append-only version
history. Every agent version carries a FIXED model binding plus the V6.0-V6.3
assets it composes: a frozen ``prompt_version`` (V6.1), Context Pack versions
and a budget policy (V6.2), tools, knowledge collections and a rubric. Nothing
here is a parallel store: references are validated against the existing
owner-scoped registries and only ids are persisted.

States are ``draft`` (mutable), ``frozen`` (immutable snapshot) and
``deprecated``. There is deliberately no "certified" state. A frozen or
deprecated version can never be edited; only the newest draft is editable.

The ``routing`` block exists for forward compatibility but V6 accepts exactly
one mode: ``fixed``. An agent version can never silently route to a different
model than the one it pins; a supplied ``routing.model_id`` must equal the
bound ``model_id``.

All lookups are owner-scoped: a foreign or missing agent/version resolves to
:class:`AgentNotFound` (HTTP 404) and a domain validation failure raises
:class:`AgentError` (HTTP 400).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..models import (
    AGENT_ROLES,
    AGENT_ROUTING_MODES,
    AGENT_STATUSES,
    AGENT_VERSION_STATES,
    OUTPUT_CONTRACT_FORMATS,
    new_id,
    now_iso,
)
from .experiments import canonical_hash

# Binding fields that define a version's reproducible identity. Timestamps,
# workflow fields (status/version/frozen_at/...) and free-form metadata are
# deliberately excluded so the hash only changes when the binding changes.
_HASH_FIELDS = (
    "model_id",
    "prompt_version_id",
    "context_policy",
    "tool_policy",
    "knowledge_policy",
    "runtime_policy",
    "output_contract",
    "evaluation_profile",
    "routing",
)
_BINDING_FIELDS = _HASH_FIELDS
# Fields surfaced by compare's field-level view.
_COMPARE_FIELDS = _HASH_FIELDS


class AgentError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class AgentNotFound(AgentError):
    """Owner-scoped resource missing (HTTP 404)."""


# ------------------------------------------------------------------ defaults
def _default_tool_policy() -> Dict[str, Any]:
    return {
        "default_deny": True,
        "allowed_tool_ids": [],
        "denied_tool_ids": [],
        "mode": "allowlist",
    }


def _default_knowledge_policy() -> Dict[str, Any]:
    return {
        "knowledge_enabled": False,
        "allowed_collection_ids": [],
        "retrieval_top_k": 5,
        "citation_required": False,
        "knowledge_only_mode": False,
    }


def _default_context_policy() -> Dict[str, Any]:
    return {
        "context_pack_version_ids": [],
        "budget_policy_id": None,
        "strategy": "FULL",
        "context_options": {},
    }


def _default_runtime_policy() -> Dict[str, Any]:
    return {}


def _default_output_contract() -> Dict[str, Any]:
    return {"format": "text"}


def _default_evaluation_profile() -> Dict[str, Any]:
    return {}


def _default_routing() -> Dict[str, Any]:
    return {"mode": "fixed"}


_DEFAULT_BUILDERS = {
    "context_policy": _default_context_policy,
    "tool_policy": _default_tool_policy,
    "knowledge_policy": _default_knowledge_policy,
    "runtime_policy": _default_runtime_policy,
    "output_contract": _default_output_contract,
    "evaluation_profile": _default_evaluation_profile,
    "routing": _default_routing,
}


def _merge(default_builder, provided: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge a provided policy over its canonical defaults (fresh copies)."""
    out = default_builder()
    if provided:
        out.update(provided)
    # Never share list references with a caller-supplied dict.
    for key in ("context_pack_version_ids", "allowed_tool_ids", "denied_tool_ids",
                "allowed_collection_ids"):
        if key in out and out[key] is not None:
            out[key] = list(out[key])
        elif key in out:
            out[key] = []
    if "context_options" in out and out["context_options"] is not None:
        out["context_options"] = dict(out["context_options"])
    return out


def _normalize_policies(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload or {}
    out: Dict[str, Any] = {}
    for field, builder in _DEFAULT_BUILDERS.items():
        out[field] = _merge(builder, payload.get(field))
    return out


# ------------------------------------------------------------------ primitives
def _check_choice(value: Any, allowed, field: str) -> str:
    if value not in allowed:
        raise AgentError(f"Invalid {field}")
    return value


def _require_name(value: Any, field: str = "name") -> str:
    text = (value or "").strip() if isinstance(value, str) else ""
    if not text:
        raise AgentError(f"{field} is required")
    return text


async def _require_project(owner_id: str, project_id: Optional[str]) -> Dict[str, Any]:
    if not project_id:
        raise AgentError("Project not found")
    project = await repo.projects.get(project_id, owner_id)
    if not project:
        raise AgentError("Project not found")
    return project


def _content_hash(doc: Dict[str, Any]) -> str:
    """sha256 over the canonical material identity of a version."""
    return canonical_hash({field: doc.get(field) for field in _HASH_FIELDS})


def normalize_version(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a read-shaped version with V6.4 defaults filled in."""
    if not doc:
        return doc
    out = dict(doc)
    out["status"] = out.get("status") or "draft"
    out["prompt_version_id"] = out.get("prompt_version_id")
    for field, builder in _DEFAULT_BUILDERS.items():
        out[field] = _merge(builder, out.get(field))
    out["metadata"] = dict(out.get("metadata") or {})
    out["source_version_id"] = out.get("source_version_id")
    out["created_by"] = out.get("created_by", out.get("owner_id"))
    out["frozen_at"] = out.get("frozen_at")
    out["deprecated_at"] = out.get("deprecated_at")
    out["updated_at"] = out.get("updated_at") or out.get("created_at")
    if not out.get("content_hash"):
        out["content_hash"] = _content_hash(out)
    return out


# ------------------------------------------------------------------ validation
async def _validate_binding(
    owner_id: str,
    project_id: Optional[str],
    *,
    model_id: Optional[str],
    prompt_version_id: Optional[str],
    context_policy: Dict[str, Any],
    tool_policy: Dict[str, Any],
    knowledge_policy: Dict[str, Any],
    runtime_policy: Dict[str, Any],
    output_contract: Dict[str, Any],
    evaluation_profile: Dict[str, Any],
    routing: Dict[str, Any],
) -> None:
    """Validate every referenced asset owner+project scoped. Fail with 400."""
    if not model_id:
        raise AgentError("model_id is required")
    model = await repo.models.get(model_id, owner_id)
    if not model:
        raise AgentError("Model not found")

    if prompt_version_id:
        version = await repo.prompt_versions.get(prompt_version_id, owner_id)
        if not version:
            raise AgentError("Prompt version not found")
        if project_id and version.get("project_id") != project_id:
            raise AgentError("Prompt version belongs to a different project")
        if version.get("status") == "draft":
            raise AgentError(
                "Prompt version must be frozen before it can be bound to an agent version"
            )

    context_policy = context_policy or {}
    for version_id in context_policy.get("context_pack_version_ids") or []:
        pack_version = await repo.context_pack_versions.get(version_id, owner_id)
        if not pack_version:
            raise AgentError(f"Context pack version '{version_id}' was not found")
        if project_id and pack_version.get("project_id") != project_id:
            raise AgentError("Context pack version belongs to a different project")
    budget_policy_id = context_policy.get("budget_policy_id")
    if budget_policy_id:
        budget_policy = await repo.context_budget_policies.get(budget_policy_id, owner_id)
        if not budget_policy:
            raise AgentError("Context budget policy not found")
        if project_id and budget_policy.get("project_id") != project_id:
            raise AgentError("Context budget policy belongs to a different project")

    tool_policy = tool_policy or {}
    for tool_id in list(tool_policy.get("allowed_tool_ids") or []) + list(
        tool_policy.get("denied_tool_ids") or []
    ):
        tool = await repo.tools.get(tool_id, owner_id)
        if not tool:
            # Global builtins are intentionally shared (owner_id None); a
            # foreign owner's project tool never matches this fallback.
            tool = await repo.tools.find_one({"id": tool_id, "scope": "global"})
        if not tool:
            raise AgentError(f"Tool '{tool_id}' was not found")

    knowledge_policy = knowledge_policy or {}
    for collection_id in knowledge_policy.get("allowed_collection_ids") or []:
        collection = await repo.knowledge_collections.get(collection_id, owner_id)
        if not collection:
            raise AgentError(f"Knowledge collection '{collection_id}' was not found")
        if project_id and collection.get("project_id") != project_id:
            raise AgentError("Knowledge collection belongs to a different project")

    evaluation_profile = evaluation_profile or {}
    rubric_id = evaluation_profile.get("rubric_id")
    if rubric_id and not await repo.rubrics.get(rubric_id, owner_id):
        raise AgentError("Rubric not found")

    routing = routing or {}
    mode = routing.get("mode")
    if mode is not None and mode not in AGENT_ROUTING_MODES:
        raise AgentError("Invalid routing mode")
    routing_model_id = routing.get("model_id")
    if routing_model_id and routing_model_id != model_id:
        raise AgentError("routing.model_id must match the bound model_id")

    output_contract = output_contract or {}
    fmt = output_contract.get("format")
    if fmt is not None and fmt not in OUTPUT_CONTRACT_FORMATS:
        raise AgentError("Invalid output contract format")


async def _require_agent(owner_id: str, agent_id: str) -> Dict[str, Any]:
    agent = await repo.agents.get(agent_id, owner_id)
    if not agent:
        raise AgentNotFound("Agent not found")
    return agent


async def _require_version(
    owner_id: str, agent_id: str, version_id: str
) -> Dict[str, Any]:
    version = await repo.agent_versions.get(version_id, owner_id)
    if not version or version.get("agent_id") != agent_id:
        raise AgentNotFound("Agent version not found")
    return version


async def _latest_version(owner_id: str, agent_id: str) -> Optional[Dict[str, Any]]:
    """Return the highest-``version`` agent version document, or ``None``."""
    docs = await repo.agent_versions.list(
        {"owner_id": owner_id, "agent_id": agent_id},
        sort_field="version", descending=True, limit=1,
    )
    return docs[0] if docs else None


def _latest_version_fields(
    doc: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Flatten the latest version into unambiguous agent read fields.

    ``latest_version`` is the highest version *number* (int) or ``None``; the
    richer identity is exposed as separate ``latest_version_*`` fields.
    """
    if not doc:
        return {
            "latest_version": None,
            "latest_version_id": None,
            "latest_version_status": None,
            "latest_version_model_id": None,
        }
    return {
        "latest_version": doc.get("version"),
        "latest_version_id": doc.get("id"),
        "latest_version_status": doc.get("status") or "draft",
        "latest_version_model_id": doc.get("model_id"),
    }


# ------------------------------------------------------------------ agents
async def list_agents(
    owner_id: str, project_id: str, *, include_archived: bool = False
) -> List[Dict[str, Any]]:
    await _require_project(owner_id, project_id)
    query: Dict[str, Any] = {"owner_id": owner_id, "project_id": project_id}
    if not include_archived:
        query["status"] = "active"
    agents = await repo.agents.list(query, sort_field="created_at")
    for agent in agents:
        latest = await _latest_version(owner_id, agent["id"])
        agent.update(_latest_version_fields(latest))
    return agents


async def create_agent(owner_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload or {}
    project_id = payload.get("project_id")
    await _require_project(owner_id, project_id)
    name = _require_name(payload.get("name"))
    role = _check_choice(payload.get("role") or "engineer", AGENT_ROLES, "role")
    status = _check_choice(payload.get("status") or "active", AGENT_STATUSES, "status")
    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "name": name,
        "description": payload.get("description") or "",
        "role": role,
        "purpose": payload.get("purpose") or "",
        "responsibilities": list(payload.get("responsibilities") or []),
        "non_responsibilities": list(payload.get("non_responsibilities") or []),
        "status": status,
        "created_by": owner_id,
        "created_at": now,
        "updated_at": now,
    }
    return await repo.agents.insert(doc)


async def get_agent(owner_id: str, agent_id: str) -> Dict[str, Any]:
    agent = await _require_agent(owner_id, agent_id)
    latest = await _latest_version(owner_id, agent_id)
    agent.update(_latest_version_fields(latest))
    return agent


async def update_agent(
    owner_id: str, agent_id: str, patch: Dict[str, Any]
) -> Dict[str, Any]:
    await _require_agent(owner_id, agent_id)
    patch = patch or {}
    updates: Dict[str, Any] = {}
    for field in (
        "name", "description", "role", "purpose",
        "responsibilities", "non_responsibilities", "status",
    ):
        if field in patch and patch[field] is not None:
            updates[field] = patch[field]
    if "name" in updates:
        updates["name"] = _require_name(updates["name"])
    if "role" in updates:
        updates["role"] = _check_choice(updates["role"], AGENT_ROLES, "role")
    if "status" in updates:
        updates["status"] = _check_choice(updates["status"], AGENT_STATUSES, "status")
    if "responsibilities" in updates:
        updates["responsibilities"] = list(updates["responsibilities"] or [])
    if "non_responsibilities" in updates:
        updates["non_responsibilities"] = list(updates["non_responsibilities"] or [])
    updates["updated_at"] = now_iso()
    return await repo.agents.update(agent_id, owner_id, updates)


async def delete_agent(owner_id: str, agent_id: str) -> None:
    await _require_agent(owner_id, agent_id)
    await repo.agent_versions.delete_many({"owner_id": owner_id, "agent_id": agent_id})
    await repo.agents.delete(agent_id, owner_id)


# ------------------------------------------------------------------ versions
async def list_versions(owner_id: str, agent_id: str) -> List[Dict[str, Any]]:
    await _require_agent(owner_id, agent_id)
    docs = await repo.agent_versions.list(
        {"owner_id": owner_id, "agent_id": agent_id},
        sort_field="version", descending=True,
    )
    return [normalize_version(doc) for doc in docs]


async def get_version(
    owner_id: str, agent_id: str, version_id: str
) -> Dict[str, Any]:
    await _require_agent(owner_id, agent_id)
    return normalize_version(await _require_version(owner_id, agent_id, version_id))


async def create_version(
    owner_id: str,
    agent: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    source_version_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload = payload or {}
    model_id = payload.get("model_id")
    policies = _normalize_policies(payload)
    prompt_version_id = payload.get("prompt_version_id")
    await _validate_binding(
        owner_id,
        agent.get("project_id"),
        model_id=model_id,
        prompt_version_id=prompt_version_id,
        **policies,
    )

    latest = await _latest_version(owner_id, agent["id"])
    number = (int(latest.get("version") or 0) + 1) if latest else 1
    now = now_iso()
    version = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": agent.get("project_id"),
        "agent_id": agent["id"],
        "version": number,
        "status": "draft",
        "model_id": model_id,
        "prompt_version_id": prompt_version_id,
        **policies,
        "metadata": dict(payload.get("metadata") or {}),
        "content_hash": "",
        "created_by": owner_id,
        "source_version_id": source_version_id,
        "created_at": now,
        "updated_at": now,
        "frozen_at": None,
        "deprecated_at": None,
    }
    version["content_hash"] = _content_hash(version)
    await repo.agent_versions.insert(version)
    await repo.agents.update(agent["id"], owner_id, {"updated_at": now})
    return normalize_version(version)


async def update_draft(
    owner_id: str, agent: Dict[str, Any], version_id: str, patch: Dict[str, Any]
) -> Dict[str, Any]:
    version = await _require_version(owner_id, agent["id"], version_id)
    if version.get("status") != "draft":
        raise AgentError("Only draft agent versions can be edited")

    patch = patch or {}
    merged = dict(version)
    if patch.get("model_id") is not None:
        merged["model_id"] = patch["model_id"]
    if "prompt_version_id" in patch:
        merged["prompt_version_id"] = patch["prompt_version_id"]
    for field, builder in _DEFAULT_BUILDERS.items():
        if patch.get(field) is not None:
            merged[field] = _merge(builder, patch[field])
    if patch.get("metadata") is not None:
        merged["metadata"] = dict(patch["metadata"])

    await _validate_binding(
        owner_id,
        agent.get("project_id"),
        model_id=merged.get("model_id"),
        prompt_version_id=merged.get("prompt_version_id"),
        **{field: merged.get(field) for field in _DEFAULT_BUILDERS},
    )

    now = now_iso()
    updates: Dict[str, Any] = {field: merged.get(field) for field in _BINDING_FIELDS}
    if patch.get("metadata") is not None:
        updates["metadata"] = merged["metadata"]
    updates["updated_at"] = now
    updates["content_hash"] = _content_hash(merged)
    updated = await repo.agent_versions.update(version_id, owner_id, updates)
    return normalize_version(updated)


async def freeze_version(
    owner_id: str, agent: Dict[str, Any], version_id: str
) -> Dict[str, Any]:
    version = await _require_version(owner_id, agent["id"], version_id)
    status = version.get("status")
    if status == "frozen":
        raise AgentError("Agent version is already frozen")
    if status == "deprecated":
        raise AgentError("Deprecated agent versions cannot be frozen")
    now = now_iso()
    updated = await repo.agent_versions.update(version_id, owner_id, {
        "status": "frozen",
        "content_hash": _content_hash(version),
        "frozen_at": now,
        "updated_at": now,
    })
    return normalize_version(updated)


async def deprecate_version(
    owner_id: str, agent: Dict[str, Any], version_id: str
) -> Dict[str, Any]:
    version = await _require_version(owner_id, agent["id"], version_id)
    if version.get("status") == "deprecated":
        raise AgentError("Agent version is already deprecated")
    now = now_iso()
    updated = await repo.agent_versions.update(version_id, owner_id, {
        "status": "deprecated",
        "deprecated_at": now,
        "updated_at": now,
    })
    return normalize_version(updated)


async def clone_version(
    owner_id: str, agent: Dict[str, Any], source_version_id: str
) -> Dict[str, Any]:
    source = await _require_version(owner_id, agent["id"], source_version_id)
    payload = {
        "model_id": source.get("model_id"),
        "prompt_version_id": source.get("prompt_version_id"),
        **{field: source.get(field) or {} for field in _DEFAULT_BUILDERS},
        "metadata": dict(source.get("metadata") or {}),
    }
    return await create_version(
        owner_id, agent, payload, source_version_id=source_version_id
    )


# ------------------------------------------------------------------ compare
def _changed_fields(
    from_dict: Dict[str, Any], to_dict: Dict[str, Any], *, exclude=()
) -> List[Dict[str, Any]]:
    keys = (set(from_dict or {}) | set(to_dict or {})) - set(exclude)
    return [
        {
            "field": key,
            "from": (from_dict or {}).get(key),
            "to": (to_dict or {}).get(key),
        }
        for key in sorted(keys)
        if (from_dict or {}).get(key) != (to_dict or {}).get(key)
    ]


def _list_added_removed(
    from_list: Optional[List[Any]], to_list: Optional[List[Any]]
) -> Dict[str, List[Any]]:
    from_list = list(from_list or [])
    to_list = list(to_list or [])
    return {
        "added": [item for item in to_list if item not in from_list],
        "removed": [item for item in from_list if item not in to_list],
    }


def _policy_diff(
    from_doc: Dict[str, Any], to_doc: Dict[str, Any]
) -> Dict[str, Any]:
    from_doc = from_doc or {}
    to_doc = to_doc or {}

    context_from = from_doc.get("context_policy") or {}
    context_to = to_doc.get("context_policy") or {}
    context_diff = _list_added_removed(
        context_from.get("context_pack_version_ids"),
        context_to.get("context_pack_version_ids"),
    )
    context_diff["changed"] = _changed_fields(
        context_from, context_to, exclude=("context_pack_version_ids",)
    )

    tools_from = from_doc.get("tool_policy") or {}
    tools_to = to_doc.get("tool_policy") or {}
    allowed = _list_added_removed(
        tools_from.get("allowed_tool_ids"), tools_to.get("allowed_tool_ids")
    )
    denied = _list_added_removed(
        tools_from.get("denied_tool_ids"), tools_to.get("denied_tool_ids")
    )
    tools_diff = {
        "allowed_added": allowed["added"],
        "allowed_removed": allowed["removed"],
        "denied_added": denied["added"],
        "denied_removed": denied["removed"],
    }

    knowledge_from = from_doc.get("knowledge_policy") or {}
    knowledge_to = to_doc.get("knowledge_policy") or {}
    knowledge_diff = _list_added_removed(
        knowledge_from.get("allowed_collection_ids"),
        knowledge_to.get("allowed_collection_ids"),
    )
    knowledge_diff["changed"] = _changed_fields(
        knowledge_from, knowledge_to, exclude=("allowed_collection_ids",)
    )

    return {
        "context": context_diff,
        "tools": tools_diff,
        "knowledge": knowledge_diff,
        "runtime": {
            "changed_fields": _changed_fields(
                from_doc.get("runtime_policy"), to_doc.get("runtime_policy")
            )
        },
        "output_contract": {
            "changed_fields": _changed_fields(
                from_doc.get("output_contract"), to_doc.get("output_contract")
            )
        },
        "evaluation": {
            "changed_fields": _changed_fields(
                from_doc.get("evaluation_profile"), to_doc.get("evaluation_profile")
            )
        },
    }


async def compare_versions(
    owner_id: str,
    agent: Dict[str, Any],
    from_version_id: str,
    to_version_id: str,
) -> Dict[str, Any]:
    frm = normalize_version(
        await _require_version(owner_id, agent["id"], from_version_id)
    )
    to = normalize_version(
        await _require_version(owner_id, agent["id"], to_version_id)
    )

    fields = [
        {
            "field": field,
            "changed": frm.get(field) != to.get(field),
            "from": frm.get(field),
            "to": to.get(field),
        }
        for field in _COMPARE_FIELDS
    ]

    return {
        "from": {
            "id": frm.get("id"),
            "version": frm.get("version"),
            "status": frm.get("status"),
        },
        "to": {
            "id": to.get("id"),
            "version": to.get("version"),
            "status": to.get("status"),
        },
        "fields": fields,
        "policy_diff": _policy_diff(frm, to),
        "summary": {
            "fields_changed": sum(1 for field in fields if field["changed"]),
        },
    }


# ------------------------------------------------------------------ usage
async def usage(
    owner_id: str, agent: Dict[str, Any], version_id: str
) -> Dict[str, Any]:
    """Owner-scoped, bounded reverse lookup of where an agent version is used."""
    await _require_version(owner_id, agent["id"], version_id)

    runs = await repo.runs.list(
        {"owner_id": owner_id, "agent_version_id": version_id},
        sort_field="created_at", descending=True, limit=2000,
    )
    run_ids = [run.get("id") for run in runs if run.get("id")]

    evaluations: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    if run_ids:
        evaluation_docs = await repo.evaluations.list(
            {"owner_id": owner_id, "run_id": {"$in": run_ids}},
            sort_field="created_at", descending=False, limit=5000,
        )
        evaluations = [
            {
                "id": doc.get("id"),
                "run_id": doc.get("run_id"),
                "outcome": doc.get("outcome"),
                "evaluator_type": doc.get("evaluator_type") or doc.get("eval_type"),
            }
            for doc in evaluation_docs
        ]
        failure_docs = await repo.failures.list(
            {"owner_id": owner_id, "run_id": {"$in": run_ids}},
            sort_field="created_at", descending=False, limit=5000,
        )
        findings = [
            {
                "id": doc.get("id"),
                "failure_domain": doc.get("failure_domain"),
                "taxonomy_code": doc.get("taxonomy_code"),
                "severity": doc.get("severity"),
                "status": doc.get("status"),
            }
            for doc in failure_docs
        ]

    scorecards = await repo.scorecards.list(
        {"owner_id": owner_id, "project_id": agent.get("project_id")},
        sort_field="created_at", descending=True, limit=500,
    )

    return {
        "agent_id": agent.get("id"),
        "version_id": version_id,
        "runs": [
            {
                "run_id": run.get("id"),
                "experiment_id": run.get("experiment_id"),
                "status": run.get("status"),
                "created_at": run.get("created_at"),
            }
            for run in runs
        ],
        "evaluations": evaluations,
        "findings": findings,
        "scorecards": [
            {
                "id": doc.get("id"),
                "scope": doc.get("scope"),
                "created_at": doc.get("created_at"),
            }
            for doc in scorecards
        ],
    }


# ------------------------------------------------------------------ catalog
def catalog() -> Dict[str, Any]:
    """Static catalog of Agent Registry vocabularies and lifecycle states."""
    return {
        "roles": list(AGENT_ROLES),
        "statuses": list(AGENT_STATUSES),
        "version_states": list(AGENT_VERSION_STATES),
        "output_formats": list(OUTPUT_CONTRACT_FORMATS),
        "routing_modes": list(AGENT_ROUTING_MODES),
    }
