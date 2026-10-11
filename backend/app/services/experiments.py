"""First-class Experiment protocol, configuration snapshots and provenance.

A protocol Experiment is a reproducible plan: optionally a frozen dataset
version, an ordered set of model configurations (each snapshotting model /
provider identity and pricing at save time), a protocol snapshot, and the
hashes that pin all of it. Nothing here may rely on the live registry once a
configuration has been frozen.
"""
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import new_id, now_iso
from .pricing import pricing_snapshot

RUNNER_VERSION = "1"
SCHEMA_VERSION = "1"

# Allowed lifecycle transitions. Keys/values are canonical uppercase statuses;
# legacy lowercase Arena statuses normalize onto these via normalize_status().
_TRANSITIONS: Dict[str, set] = {
    "DRAFT": {"READY", "ARCHIVED"},
    "READY": {"RUNNING", "DRAFT", "ARCHIVED"},
    "RUNNING": {"COMPLETED", "FAILED", "ARCHIVED"},
    "COMPLETED": {"ARCHIVED"},
    "FAILED": {"ARCHIVED"},
    "ARCHIVED": set(),
}


class ExperimentError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


def normalize_status(status: Optional[str]) -> str:
    """Canonicalize a status, accepting legacy lowercase Arena values."""
    return (status or "").strip().upper()


def canonical_hash(obj: Any) -> str:
    """sha256 over canonical JSON (sorted keys, no insignificant whitespace)."""
    payload = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_configuration_hash(config: Dict[str, Any]) -> str:
    """Stable hash of a configuration's reproducible identity.

    Deliberately excludes timestamps and the pricing capture time so the hash
    never changes just because a snapshot was re-taken.
    """
    snapshot = config.get("model_snapshot") or {}
    prompt_snapshot = config.get("prompt_version_snapshot") or {}
    material = {
        "label": config.get("label"),
        "model_id": config.get("model_id"),
        "model_identifier": snapshot.get("model_identifier"),
        "temperature": config.get("temperature"),
        "max_tokens": config.get("max_tokens"),
        "top_p": config.get("top_p"),
        "system_prompt": config.get("system_prompt", ""),
        "prompt_version_id": config.get("prompt_version_id"),
        "prompt_version": prompt_snapshot.get("version"),
        "metadata": config.get("metadata", {}),
        "is_control": config.get("is_control", False),
        "order": config.get("order", 0),
        "enabled_tool_ids": config.get("enabled_tool_ids"),
        "knowledge_enabled": config.get("knowledge_enabled"),
        "knowledge_collection_ids": config.get("knowledge_collection_ids") or [],
        "retrieval_options": config.get("retrieval_options") or {},
        "search_enabled": config.get("search_enabled"),
        "search_provider_id": config.get("search_provider_id"),
        "fetch_enabled": config.get("fetch_enabled"),
        "fetch_provider_id": config.get("fetch_provider_id"),
    }
    # Additive (V6.8 Agent Arena): only mixed in for agent-bound configurations
    # so pre-existing (agent_version_id is None) hashes are byte-for-byte
    # unchanged. The snapshot excludes timestamps by construction.
    agent_version_id = config.get("agent_version_id")
    if agent_version_id:
        material["agent_version_id"] = agent_version_id
        material["agent_version_snapshot"] = config.get("agent_version_snapshot")
    return canonical_hash(material)


def compute_protocol_hash(
    configs: List[Dict[str, Any]],
    dataset_version_hash: Optional[str],
    protocol: Dict[str, Any],
    runner_version: str = RUNNER_VERSION,
    schema_version: str = SCHEMA_VERSION,
) -> str:
    """Hash the full reproducible experiment definition."""
    ordered = sorted(configs, key=lambda c: (c.get("order", 0), c.get("label", "")))
    material = {
        "configurations": [
            {
                "label": c.get("label"),
                "order": c.get("order", 0),
                "configuration_hash": c.get("configuration_hash")
                or compute_configuration_hash(c),
            }
            for c in ordered
        ],
        "dataset_version_hash": dataset_version_hash,
        "protocol": protocol,
        "runner_version": runner_version,
        "schema_version": schema_version,
    }
    return canonical_hash(material)


def transition_status(exp: Dict[str, Any], target: str) -> str:
    """Validate a lifecycle transition and return the canonical target status."""
    current = normalize_status(exp.get("status") or "DRAFT")
    target_status = normalize_status(target)
    if current not in _TRANSITIONS:
        raise ExperimentError(f"Unknown experiment status: {exp.get('status')!r}")
    if target_status not in _TRANSITIONS[current]:
        raise ExperimentError(f"Invalid status transition: {current} -> {target_status}")
    return target_status


# ------------------------------------------------------------------ snapshots
async def _snapshot_model(owner_id: str, model_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Snapshot model identity + pricing from the registry at this moment."""
    model = await repo.models.get(model_id, owner_id)
    if not model:
        raise ExperimentError(f"Model '{model_id}' was not found")
    if not model.get("enabled", True):
        raise ExperimentError(
            f"Model '{model.get('display_name') or model_id}' is disabled"
        )
    provider = await repo.providers.get(model.get("provider_id"), owner_id)
    if not provider:
        raise ExperimentError(f"Provider for model '{model_id}' was not found")
    if not provider.get("enabled", True):
        raise ExperimentError(f"Provider '{provider.get('name')}' is disabled")
    identity = {
        "model_id": model.get("id") or model_id,
        "model_identifier": model.get("model_identifier"),
        "display_name": model.get("display_name"),
        "provider_id": provider.get("id"),
        "provider_name": provider.get("name"),
        "provider_type": provider.get("provider_type"),
        "family": model.get("family"),
    }
    return identity, pricing_snapshot(model)


async def _resolve_prompt_snapshot(
    owner_id: str, prompt_version_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    if not prompt_version_id:
        return None
    version = await repo.prompt_versions.get(prompt_version_id, owner_id)
    if not version:
        raise ExperimentError(f"Prompt version '{prompt_version_id}' was not found")
    # V6.1 immutability: experiments may only pin frozen (or legacy statusless)
    # versions. A draft is still mutable, so resolving it would let later edits
    # silently change an already-frozen experiment.
    status = version.get("status")
    if status == "draft":
        raise ExperimentError(
            "Prompt version must be frozen before it can be used in an experiment"
        )
    return {
        "id": version.get("id"),
        "version": version.get("version"),
        "content": version.get("content", ""),
        "status": status,
    }


async def _resolve_agent_version_snapshot(
    owner_id: str, project_id: str, agent_version_id: str
) -> Dict[str, Any]:
    """Load and validate a frozen agent version for an arena configuration.

    Only frozen (or legacy deprecated) versions are reproducible inputs; a draft
    is still mutable and is rejected. The returned snapshot pins identity without
    timestamps so it can participate in the configuration hash.
    """
    version = await repo.agent_versions.get(agent_version_id, owner_id)
    if not version:
        raise ExperimentError(f"Agent version '{agent_version_id}' was not found")
    status = version.get("status")
    if status not in ("frozen", "deprecated"):
        raise ExperimentError(
            "Agent version must be frozen before it can be used in an experiment"
        )
    if project_id and version.get("project_id") != project_id:
        raise ExperimentError("Agent version belongs to a different project")
    return {
        "id": version.get("id"),
        "version": version.get("version"),
        "status": status,
        "agent_id": version.get("agent_id"),
        "model_id": version.get("model_id"),
        "prompt_version_id": version.get("prompt_version_id"),
        "content_hash": version.get("content_hash"),
    }


async def _build_config(
    owner_id: str,
    project_id: str,
    experiment_id: str,
    config: Dict[str, Any],
    now: str,
) -> Dict[str, Any]:
    agent_version_id = config.get("agent_version_id")
    agent_snapshot: Optional[Dict[str, Any]] = None
    model_id = config.get("model_id")
    if agent_version_id:
        agent_snapshot = await _resolve_agent_version_snapshot(
            owner_id, project_id, agent_version_id
        )
        # Fixed model binding (V6.8): an agent arena configuration never routes
        # adaptively; when no explicit model is given it uses the frozen agent
        # version's bound model.
        if not model_id:
            model_id = agent_snapshot.get("model_id")
    if not model_id:
        raise ExperimentError("Each configuration requires a model_id")
    identity, pricing = await _snapshot_model(owner_id, model_id)
    prompt_snapshot = await _resolve_prompt_snapshot(owner_id, config.get("prompt_version_id"))
    system_prompt = config.get("system_prompt") or ""
    if not system_prompt and prompt_snapshot:
        system_prompt = prompt_snapshot.get("content") or ""
    doc = {
        "id": config.get("id") or new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "experiment_id": experiment_id,
        "label": config["label"],
        "model_id": model_id,
        "agent_version_id": agent_version_id,
        "agent_version_snapshot": agent_snapshot,
        "model_snapshot": identity,
        "pricing_snapshot": pricing,
        "temperature": config.get("temperature"),
        "max_tokens": config.get("max_tokens"),
        "top_p": config.get("top_p"),
        "system_prompt": system_prompt,
        "prompt_version_id": config.get("prompt_version_id"),
        "prompt_version_snapshot": prompt_snapshot,
        "metadata": config.get("metadata") or {},
        "is_control": bool(config.get("is_control", False)),
        "order": config.get("order", 0),
        "enabled_tool_ids": config.get("enabled_tool_ids"),
        "knowledge_enabled": config.get("knowledge_enabled"),
        "knowledge_collection_ids": list(config.get("knowledge_collection_ids") or []),
        "retrieval_options": dict(config.get("retrieval_options") or {}),
        "search_enabled": config.get("search_enabled"),
        "search_provider_id": config.get("search_provider_id"),
        "fetch_enabled": config.get("fetch_enabled"),
        "fetch_provider_id": config.get("fetch_provider_id"),
        "configuration_hash": "",
        "created_at": now,
        "updated_at": now,
    }
    doc["configuration_hash"] = compute_configuration_hash(doc)
    return doc


def _as_dict(item: Any) -> Dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return dict(item)


# ------------------------------------------------------------------ lifecycle
async def create_experiment(
    owner_id: str,
    project_id: str,
    title: str,
    objective: str = "",
    hypothesis: str = "",
    experiment_type: str = "benchmark",
    protocol: Optional[Any] = None,
    configurations: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    protocol = _as_dict(protocol) if protocol is not None else {}
    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "title": title,
        "objective": objective or protocol.get("objective", ""),
        "hypothesis": hypothesis or protocol.get("hypothesis", ""),
        "experiment_type": experiment_type or "benchmark",
        "system_prompt": "",
        "user_prompt": "",
        "params": {},
        "model_ids": [],
        "blind": False,
        "blind_map": [],
        "rubric_id": protocol.get("rubric_id"),
        "dataset_id": protocol.get("dataset_id"),
        "dataset_version_id": protocol.get("dataset_version_id"),
        "status": "DRAFT",
        "run_count": 0,
        "configurations_count": 0,
        "protocol": protocol,
        "protocol_snapshot": None,
        "protocol_hash": None,
        "configuration_hash": None,
        "dataset_version_hash": None,
        "runner_version": None,
        "schema_version": None,
        "rubric_snapshot": None,
        "judge_model_snapshot": None,
        "judge_rubric_snapshot": None,
        "created_at": now,
        "updated_at": now,
    }
    await repo.experiments.insert(doc)
    if configurations:
        await set_configurations(owner_id, doc, configurations)
        doc = await repo.experiments.get(doc["id"], owner_id) or doc
    return doc


async def set_configurations(
    owner_id: str, exp: Dict[str, Any], configurations: List[Any]
) -> List[Dict[str, Any]]:
    """Replace an experiment's configurations. DRAFT only."""
    status = normalize_status(exp.get("status") or "DRAFT")
    if status != "DRAFT":
        raise ExperimentError("Configurations can only be edited while the experiment is DRAFT")
    raw = [_as_dict(c) for c in (configurations or [])]
    labels = [c.get("label") for c in raw]
    if len(set(labels)) != len(labels):
        raise ExperimentError("Configuration labels must be unique")
    now = now_iso()
    docs: List[Dict[str, Any]] = []
    for c in raw:
        if not c.get("model_id") and not c.get("agent_version_id"):
            raise ExperimentError("Each configuration requires a model_id")
        if not c.get("label"):
            raise ExperimentError("Each configuration requires a label")
        docs.append(await _build_config(owner_id, exp["project_id"], exp["id"], c, now))
    await repo.experiment_configs.delete_many(
        {"owner_id": owner_id, "experiment_id": exp["id"]}
    )
    for d in docs:
        await repo.experiment_configs.insert(d)
    model_ids = list(dict.fromkeys(d["model_id"] for d in docs))
    await repo.experiments.update(exp["id"], owner_id, {
        "model_ids": model_ids,
        "configurations_count": len(docs),
        "updated_at": now,
    })
    return docs


async def update_experiment(
    owner_id: str, exp: Dict[str, Any], patch: Dict[str, Any]
) -> Dict[str, Any]:
    status = normalize_status(exp.get("status") or "DRAFT")
    update: Dict[str, Any] = {}
    for field in ("title", "objective", "hypothesis"):
        if patch.get(field) is not None:
            update[field] = patch[field]
    if patch.get("protocol") is not None:
        update["protocol"] = _as_dict(patch["protocol"])
    if patch.get("configurations") is not None:
        if status != "DRAFT":
            raise ExperimentError("Configurations can only be edited while the experiment is DRAFT")
        await set_configurations(owner_id, exp, patch["configurations"])
    if patch.get("status") is not None:
        target = normalize_status(patch["status"])
        if target == "READY":
            # PATCHing straight to READY must run the same validation/freeze
            # path as POST /experiments/{id}/ready; a bare status flip would
            # leave the experiment without snapshots or hashes.
            if update:
                exp = await repo.experiments.update(exp["id"], owner_id, update) or exp
                update = {}
            return await ready_experiment(owner_id, exp)
        update["status"] = transition_status(exp, patch["status"])
    update["updated_at"] = now_iso()
    return await repo.experiments.update(exp["id"], owner_id, update)


async def get_configurations(owner_id: str, experiment_id: str) -> List[Dict[str, Any]]:
    return await repo.experiment_configs.list(
        {"owner_id": owner_id, "experiment_id": experiment_id},
        sort_field="order",
        descending=False,
    )


async def ready_experiment(owner_id: str, exp: Dict[str, Any]) -> Dict[str, Any]:
    """Validate requirements, freeze snapshots, and move DRAFT -> READY."""
    status = normalize_status(exp.get("status") or "DRAFT")
    if status != "DRAFT":
        raise ExperimentError(f"Experiment can only become READY from DRAFT (current: {status})")

    protocol = exp.get("protocol") or {}
    configs = await get_configurations(owner_id, exp["id"])
    if not configs:
        raise ExperimentError("At least one configuration is required before READY")
    labels = [c.get("label") for c in configs]
    if len(set(labels)) != len(labels):
        raise ExperimentError("Configuration labels must be unique")

    repetitions = protocol.get("repetitions", 1) or 1
    if repetitions < 1:
        raise ExperimentError("repetitions must be >= 1")

    # Re-snapshot each configuration at freeze time; missing/disabled models are
    # rejected here and the frozen identity/pricing is authoritative.
    now = now_iso()
    fresh: List[Dict[str, Any]] = []
    for c in configs:
        built = await _build_config(owner_id, exp["project_id"], exp["id"], c, now)
        built["id"] = c.get("id")
        built["created_at"] = c.get("created_at", now)
        built["updated_at"] = now
        await repo.experiment_configs.update(c["id"], owner_id, {
            "model_snapshot": built["model_snapshot"],
            "pricing_snapshot": built["pricing_snapshot"],
            "prompt_version_snapshot": built["prompt_version_snapshot"],
            "agent_version_id": built.get("agent_version_id"),
            "agent_version_snapshot": built.get("agent_version_snapshot"),
            "system_prompt": built["system_prompt"],
            "configuration_hash": built["configuration_hash"],
            "updated_at": now,
        })
        fresh.append(built)

    # Dataset requirement: a frozen version is mandatory when a dataset is set.
    dataset_id = protocol.get("dataset_id") or exp.get("dataset_id")
    dataset_version = None
    dataset_version_hash = None
    if dataset_id:
        dataset = await repo.datasets.get(dataset_id, owner_id)
        if not dataset or dataset.get("project_id") not in (None, exp.get("project_id")):
            raise ExperimentError("Protocol dataset was not found in this project")
        version_id = protocol.get("dataset_version_id") or exp.get("dataset_version_id")
        version = None
        if version_id:
            version = await repo.dataset_versions.get(version_id, owner_id)
            if not version or version.get("dataset_id") != dataset_id:
                raise ExperimentError("Dataset version was not found for this dataset")
        else:
            frozen = await repo.dataset_versions.list(
                {"owner_id": owner_id, "dataset_id": dataset_id, "status": "frozen"},
                sort_field="version",
                descending=True,
                limit=1,
            )
            version = frozen[0] if frozen else None
        if version is None:
            raise ExperimentError("A frozen dataset version is required before READY")
        if version.get("status") != "frozen":
            raise ExperimentError("Dataset version must be frozen before READY")
        dataset_version_hash = version.get("content_hash")
        dataset_version = {
            "id": version.get("id"),
            "version": version.get("version"),
            "content_hash": version.get("content_hash"),
            "case_count": version.get("case_count"),
            "status": version.get("status"),
            "dataset_id": version.get("dataset_id"),
        }

    # Rubric + judge identity snapshots.
    rubric_snapshot = None
    rubric_id = protocol.get("rubric_id")
    if rubric_id:
        rubric = await repo.rubrics.get(rubric_id, owner_id)
        if not rubric:
            raise ExperimentError("Rubric was not found")
        rubric_snapshot = rubric

    judge_model_snapshot = None
    judge_model_id = protocol.get("judge_model_id")
    if judge_model_id:
        judge_model_snapshot, _jp = await _snapshot_model(owner_id, judge_model_id)

    judge_rubric_snapshot = None
    judge_rubric_id = protocol.get("judge_rubric_id")
    if judge_rubric_id:
        judge_rubric = await repo.rubrics.get(judge_rubric_id, owner_id)
        if not judge_rubric:
            raise ExperimentError("Judge rubric was not found")
        judge_rubric_snapshot = {
            "id": judge_rubric.get("id"),
            "name": judge_rubric.get("name"),
            "dimensions": judge_rubric.get("dimensions", []),
        }

    configuration_hash = canonical_hash([c["configuration_hash"] for c in fresh])
    protocol_hash = compute_protocol_hash(
        fresh, dataset_version_hash, protocol, RUNNER_VERSION, SCHEMA_VERSION
    )

    updated = await repo.experiments.update(exp["id"], owner_id, {
        "status": "READY",
        "protocol_snapshot": dict(protocol),
        "dataset_id": dataset_id,
        "dataset_version_id": dataset_version.get("id") if dataset_version else None,
        "dataset_version_hash": dataset_version_hash,
        "configuration_hash": configuration_hash,
        "protocol_hash": protocol_hash,
        "runner_version": RUNNER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "rubric_id": protocol.get("rubric_id"),
        "rubric_snapshot": rubric_snapshot,
        "judge_model_snapshot": judge_model_snapshot,
        "judge_rubric_snapshot": judge_rubric_snapshot,
        "configurations_count": len(fresh),
        "ready_at": now,
        "updated_at": now,
    })
    return updated


def _provenance_config(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "configuration_id": c.get("id"),
        "label": c.get("label"),
        "model_id": c.get("model_id"),
        "model_snapshot": c.get("model_snapshot"),
        "pricing_snapshot": c.get("pricing_snapshot"),
        "temperature": c.get("temperature"),
        "max_tokens": c.get("max_tokens"),
        "top_p": c.get("top_p"),
        "system_prompt": c.get("system_prompt"),
        "prompt_version_id": c.get("prompt_version_id"),
        "prompt_version_snapshot": c.get("prompt_version_snapshot"),
        "agent_version_id": c.get("agent_version_id"),
        "agent_version_snapshot": c.get("agent_version_snapshot"),
        "metadata": c.get("metadata"),
        "is_control": c.get("is_control"),
        "order": c.get("order"),
        "configuration_hash": c.get("configuration_hash"),
    }


async def get_provenance(owner_id: str, exp: Dict[str, Any]) -> Dict[str, Any]:
    """Full reproducibility snapshot. Legacy Arena experiments return what
    exists with ``legacy: true`` instead of erroring."""
    configs = await get_configurations(owner_id, exp["id"])
    protocol = exp.get("protocol_snapshot") or exp.get("protocol")
    legacy = not configs and not exp.get("protocol")

    dataset_version = None
    dv_id = exp.get("dataset_version_id") or (protocol or {}).get("dataset_version_id")
    if dv_id:
        version = await repo.dataset_versions.get(dv_id, owner_id)
        if version:
            dataset_version = {
                "id": version.get("id"),
                "version": version.get("version"),
                "content_hash": version.get("content_hash"),
                "case_count": version.get("case_count"),
                "status": version.get("status"),
                "dataset_id": version.get("dataset_id"),
            }

    return {
        "experiment_id": exp.get("id"),
        "title": exp.get("title"),
        "status": exp.get("status"),
        "legacy": legacy,
        "experiment_type": exp.get("experiment_type"),
        "protocol": protocol,
        "dataset_version": dataset_version,
        "dataset_version_hash": exp.get("dataset_version_hash"),
        "configurations": [_provenance_config(c) for c in configs],
        "repetitions": (protocol or {}).get("repetitions", 1),
        "runner_version": exp.get("runner_version"),
        "schema_version": exp.get("schema_version"),
        "rubric_snapshot": exp.get("rubric_snapshot"),
        "judge_model_snapshot": exp.get("judge_model_snapshot"),
        "judge_rubric_snapshot": exp.get("judge_rubric_snapshot"),
        "hashes": {
            "configuration_hash": exp.get("configuration_hash"),
            "protocol_hash": exp.get("protocol_hash"),
            "dataset_version_hash": exp.get("dataset_version_hash"),
        },
        "created_at": exp.get("created_at"),
        "updated_at": exp.get("updated_at"),
        "ready_at": exp.get("ready_at"),
    }
