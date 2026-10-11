"""V6.1 Prompt Registry: draft/frozen/deprecated versions over the prompt store.

Prompt Specs reuse the existing ``repo.prompts`` collection and versions reuse
the append-only ``repo.prompt_versions`` collection; this module never creates a
parallel prompt store. A version starts as ``draft`` (mutable), can be frozen
exactly once into an immutable snapshot, and may later be deprecated. There is
deliberately no "certified" state.

Every material field (content, system prompt, variables, structured spec and
ordered Prompt Block references) is hashed with the shared canonical hasher so a
frozen version can be pinned by experiments. All lookups are owner-scoped: a
foreign or missing prompt/version resolves to :class:`PromptRegistryNotFound`
(HTTP 404) and a domain validation failure raises :class:`PromptRegistryError`
(HTTP 400).
"""
from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import new_id, now_iso
from .experiments import canonical_hash

# Material fields that define a version's immutable identity. Timestamps and
# workflow fields (status/frozen_at/...) are deliberately excluded.
_EDITABLE_FIELDS = (
    "content", "system_prompt", "variables", "notes", "structured_spec", "blocks",
)
_COMPARE_FIELDS = ("system_prompt", "notes", "structured_spec", "variables", "blocks")


class PromptRegistryError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class PromptRegistryNotFound(PromptRegistryError):
    """Owner-scoped resource missing (HTTP 404)."""


# ------------------------------------------------------------------ helpers
def _content_material(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "content": doc.get("content") or "",
        "system_prompt": doc.get("system_prompt") or "",
        "variables": doc.get("variables") or {},
        "structured_spec": doc.get("structured_spec") or {},
        "blocks": list(doc.get("blocks") or []),
    }


def _content_hash(doc: Dict[str, Any]) -> str:
    """sha256 over the canonical material identity of a version."""
    return canonical_hash(_content_material(doc))


def normalize_version(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a read-shaped version with V6.1 defaults filled in.

    Legacy prompt_versions predate the registry fields; readers must see the
    canonical defaults rather than missing keys.
    """
    if not doc:
        return doc
    out = dict(doc)
    out["status"] = out.get("status") or "draft"
    out["structured_spec"] = out.get("structured_spec") or {}
    out["blocks"] = list(out.get("blocks") or [])
    out["compiler_version"] = out.get("compiler_version")
    out["source_version_id"] = out.get("source_version_id")
    out["created_by"] = out.get("created_by", out.get("owner_id"))
    out["frozen_at"] = out.get("frozen_at")
    out["deprecated_at"] = out.get("deprecated_at")
    out["updated_at"] = out.get("updated_at") or out.get("created_at")
    if not out.get("content_hash"):
        out["content_hash"] = _content_hash(out)
    return out


async def _require_prompt(owner_id: str, prompt_id: str) -> Dict[str, Any]:
    prompt = await repo.prompts.get(prompt_id, owner_id)
    if not prompt:
        raise PromptRegistryNotFound("Prompt not found")
    return prompt


async def _require_version(owner_id: str, prompt_id: str, version_id: str) -> Dict[str, Any]:
    version = await repo.prompt_versions.get(version_id, owner_id)
    if not version or version.get("prompt_id") != prompt_id:
        raise PromptRegistryNotFound("Prompt version not found")
    return version


async def _latest_version(owner_id: str, prompt_id: str) -> Optional[Dict[str, Any]]:
    latest = await repo.prompt_versions.list(
        {"owner_id": owner_id, "prompt_id": prompt_id},
        sort_field="version", descending=True, limit=1,
    )
    return latest[0] if latest else None


async def validate_blocks(
    owner_id: str, project_id: Optional[str], block_ids: Optional[List[str]]
) -> None:
    """Validate that every referenced block exists, is owned and in-project.

    An empty (or absent) list is valid. Any problem is a domain error so the
    caller sees 400 rather than leaking whether a foreign block exists.
    """
    for block_id in block_ids or []:
        block = await repo.prompt_blocks.get(block_id, owner_id)
        if not block:
            raise PromptRegistryError(f"Prompt block '{block_id}' was not found")
        if project_id and block.get("project_id") != project_id:
            raise PromptRegistryError("Prompt block belongs to a different project")


# ------------------------------------------------------------------ reads
async def list_versions(owner_id: str, prompt_id: str) -> List[Dict[str, Any]]:
    await _require_prompt(owner_id, prompt_id)
    docs = await repo.prompt_versions.list(
        {"owner_id": owner_id, "prompt_id": prompt_id},
        sort_field="version", descending=True,
    )
    return [normalize_version(doc) for doc in docs]


async def get_version(owner_id: str, prompt_id: str, version_id: str) -> Dict[str, Any]:
    await _require_prompt(owner_id, prompt_id)
    version = await _require_version(owner_id, prompt_id, version_id)
    return normalize_version(version)


# ------------------------------------------------------------------ writes
async def create_version(
    owner_id: str, prompt: Dict[str, Any], payload: Dict[str, Any]
) -> Dict[str, Any]:
    payload = payload or {}
    project_id = prompt.get("project_id")
    blocks = list(payload.get("blocks") or [])
    await validate_blocks(owner_id, project_id, blocks)

    latest = await _latest_version(owner_id, prompt["id"])
    number = (int(latest.get("version") or 0) + 1) if latest else 1
    now = now_iso()
    version = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "prompt_id": prompt["id"],
        "version": number,
        "content": payload.get("content") or "",
        "system_prompt": payload.get("system_prompt") or "",
        "variables": payload.get("variables") or {},
        "structured_spec": payload.get("structured_spec") or {},
        "blocks": blocks,
        "notes": payload.get("notes") or "",
        "status": "draft",
        "compiler_version": payload.get("compiler_version"),
        "source_version_id": payload.get("source_version_id"),
        "created_by": owner_id,
        "created_at": now,
        "updated_at": now,
        "frozen_at": None,
        "deprecated_at": None,
    }
    version["content_hash"] = _content_hash(version)
    await repo.prompt_versions.insert(version)
    await repo.prompts.update(prompt["id"], owner_id, {"updated_at": now})
    return normalize_version(version)


async def _is_editable(
    owner_id: str, prompt: Dict[str, Any], version: Dict[str, Any]
) -> bool:
    status = version.get("status")
    if status == "draft":
        return True
    if status is not None:
        return False
    # A legacy version without a status is only treated as an editable draft
    # when it is the newest version; older statusless versions are history.
    latest = await _latest_version(owner_id, prompt["id"])
    return bool(latest) and latest.get("id") == version.get("id")


async def update_draft(
    owner_id: str, prompt: Dict[str, Any], version_id: str, patch: Dict[str, Any]
) -> Dict[str, Any]:
    version = await _require_version(owner_id, prompt["id"], version_id)
    if not await _is_editable(owner_id, prompt, version):
        raise PromptRegistryError("Only draft prompt versions can be edited")

    updates: Dict[str, Any] = {}
    for field in _EDITABLE_FIELDS:
        if field in patch and patch[field] is not None:
            updates[field] = patch[field]
    if "blocks" in updates:
        await validate_blocks(owner_id, prompt.get("project_id"), updates["blocks"])

    merged = {**version, **updates}
    now = now_iso()
    updates["updated_at"] = now
    updates["content_hash"] = _content_hash(merged)
    updated = await repo.prompt_versions.update(version_id, owner_id, updates)
    return normalize_version(updated)


async def freeze_version(
    owner_id: str, prompt: Dict[str, Any], version_id: str
) -> Dict[str, Any]:
    version = await _require_version(owner_id, prompt["id"], version_id)
    status = version.get("status")
    if status == "frozen":
        raise PromptRegistryError("Prompt version is already frozen")
    if status == "deprecated":
        raise PromptRegistryError("Deprecated prompt versions cannot be frozen")
    now = now_iso()
    updated = await repo.prompt_versions.update(version_id, owner_id, {
        "status": "frozen",
        "content_hash": _content_hash(version),
        "frozen_at": now,
        "updated_at": now,
    })
    return normalize_version(updated)


async def deprecate_version(
    owner_id: str, prompt: Dict[str, Any], version_id: str
) -> Dict[str, Any]:
    version = await _require_version(owner_id, prompt["id"], version_id)
    status = version.get("status")
    if status == "deprecated":
        raise PromptRegistryError("Prompt version is already deprecated")
    now = now_iso()
    updated = await repo.prompt_versions.update(version_id, owner_id, {
        "status": "deprecated",
        "deprecated_at": now,
        "updated_at": now,
    })
    return normalize_version(updated)


async def clone_version(
    owner_id: str,
    prompt: Dict[str, Any],
    source_version_id: str,
    *,
    restore: bool = False,
) -> Dict[str, Any]:
    source = await _require_version(owner_id, prompt["id"], source_version_id)
    payload = {
        "content": source.get("content") or "",
        "system_prompt": source.get("system_prompt") or "",
        "variables": source.get("variables") or {},
        "structured_spec": source.get("structured_spec") or {},
        "blocks": list(source.get("blocks") or []),
        "notes": source.get("notes") or "",
        "source_version_id": source_version_id,
    }
    if restore:
        marker = f"Restored from v{source.get('version')}"
        existing = (source.get("notes") or "").strip()
        payload["notes"] = f"{marker}\n{existing}" if existing else marker
    return await create_version(owner_id, prompt, payload)


# ------------------------------------------------------------------ compare
def _diff_lines(from_lines: List[str], to_lines: List[str]) -> List[Dict[str, Any]]:
    matcher = difflib.SequenceMatcher(a=from_lines, b=to_lines)
    out: List[Dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, text in enumerate(from_lines[i1:i2]):
                out.append({
                    "op": "equal",
                    "from_line": i1 + offset + 1,
                    "to_line": j1 + offset + 1,
                    "text": text,
                })
        elif tag == "delete":
            for offset, text in enumerate(from_lines[i1:i2]):
                out.append({
                    "op": "delete",
                    "from_line": i1 + offset + 1,
                    "to_line": None,
                    "text": text,
                })
        elif tag == "insert":
            for offset, text in enumerate(to_lines[j1:j2]):
                out.append({
                    "op": "insert",
                    "from_line": None,
                    "to_line": j1 + offset + 1,
                    "text": text,
                })
        else:  # replace -> a delete then an insert, never a fabricated op
            for offset, text in enumerate(from_lines[i1:i2]):
                out.append({
                    "op": "delete",
                    "from_line": i1 + offset + 1,
                    "to_line": None,
                    "text": text,
                })
            for offset, text in enumerate(to_lines[j1:j2]):
                out.append({
                    "op": "insert",
                    "from_line": None,
                    "to_line": j1 + offset + 1,
                    "text": text,
                })
    return out


async def compare_versions(
    owner_id: str,
    prompt: Dict[str, Any],
    from_version_id: str,
    to_version_id: str,
) -> Dict[str, Any]:
    frm = normalize_version(await _require_version(owner_id, prompt["id"], from_version_id))
    to = normalize_version(await _require_version(owner_id, prompt["id"], to_version_id))

    content_diff = _diff_lines(
        (frm.get("content") or "").splitlines(),
        (to.get("content") or "").splitlines(),
    )
    lines_added = sum(1 for d in content_diff if d["op"] == "insert")
    lines_removed = sum(1 for d in content_diff if d["op"] == "delete")

    fields = []
    for field in _COMPARE_FIELDS:
        from_value = frm.get(field)
        to_value = to.get(field)
        fields.append({
            "field": field,
            "changed": from_value != to_value,
            "from": from_value,
            "to": to_value,
        })

    from_vars = frm.get("variables") or {}
    to_vars = to.get("variables") or {}
    variables = {
        "added": sorted(k for k in to_vars if k not in from_vars),
        "removed": sorted(k for k in from_vars if k not in to_vars),
        "changed": [
            {"key": key, "from": from_vars[key], "to": to_vars[key]}
            for key in sorted(set(from_vars) & set(to_vars))
            if from_vars[key] != to_vars[key]
        ],
    }

    from_blocks = list(frm.get("blocks") or [])
    to_blocks = list(to.get("blocks") or [])
    blocks = {
        "added": [b for b in to_blocks if b not in from_blocks],
        "removed": [b for b in from_blocks if b not in to_blocks],
    }

    return {
        "from": {"id": frm.get("id"), "version": frm.get("version"), "status": frm.get("status")},
        "to": {"id": to.get("id"), "version": to.get("version"), "status": to.get("status")},
        "content_diff": content_diff,
        "fields": fields,
        "variables": variables,
        "blocks": blocks,
        "summary": {
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "fields_changed": sum(1 for f in fields if f["changed"]),
        },
    }


# ------------------------------------------------------------------ usage
async def usage(owner_id: str, prompt: Dict[str, Any]) -> Dict[str, Any]:
    """Owner-scoped, bounded reverse lookup of where a prompt's versions are used."""
    versions = await list_versions(owner_id, prompt["id"])
    version_ids = [v["id"] for v in versions]

    configs = await repo.experiment_configs.list(
        {"owner_id": owner_id, "prompt_version_id": {"$in": version_ids}},
        sort_field="created_at", descending=False, limit=5000,
    )

    runs = await repo.runs.list(
        {"owner_id": owner_id}, sort_field="created_at", descending=True, limit=5000,
    )
    matched: List[Tuple[str, Dict[str, Any]]] = []
    for run in runs:
        pv_id = run.get("prompt_version_id") or (
            (run.get("configuration_snapshot") or {}).get("prompt_version_id")
        )
        if pv_id in version_ids:
            matched.append((pv_id, run))

    run_ids = list(dict.fromkeys(run["id"] for _pv, run in matched))
    evaluations_by_run: Dict[str, List[Dict[str, Any]]] = {}
    if run_ids:
        evaluations = await repo.evaluations.list(
            {"owner_id": owner_id, "run_id": {"$in": run_ids}},
            sort_field="created_at", descending=False, limit=5000,
        )
        for evaluation in evaluations:
            evaluations_by_run.setdefault(evaluation.get("run_id"), []).append(evaluation)

    out_versions = []
    total_configs = 0
    total_runs = 0
    total_evaluations = 0
    for version in versions:
        vid = version["id"]
        used_configs = [
            {
                "experiment_id": c.get("experiment_id"),
                "configuration_id": c.get("id"),
                "label": c.get("label"),
            }
            for c in configs if c.get("prompt_version_id") == vid
        ]
        used_runs = []
        for pv_id, run in matched:
            if pv_id != vid:
                continue
            evaluations = evaluations_by_run.get(run["id"], [])
            used_runs.append({
                "run_id": run.get("id"),
                "experiment_id": run.get("experiment_id"),
                "status": run.get("status"),
                "evaluations": [
                    {
                        "id": e.get("id"),
                        "outcome": e.get("outcome"),
                        "evaluator_type": e.get("evaluator_type") or e.get("eval_type"),
                    }
                    for e in evaluations
                ],
            })
        total_configs += len(used_configs)
        total_runs += len(used_runs)
        total_evaluations += sum(len(r["evaluations"]) for r in used_runs)
        out_versions.append({
            "id": vid,
            "version": version.get("version"),
            "status": version.get("status"),
            "used_by": {"experiments": used_configs, "runs": used_runs},
        })

    return {
        "prompt_id": prompt.get("id"),
        "versions": out_versions,
        "totals": {
            "experiments": total_configs,
            "runs": total_runs,
            "evaluations": total_evaluations,
        },
    }
