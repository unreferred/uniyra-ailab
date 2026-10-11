"""V6.0 prompt / policy foundation.

First-class, project-scoped Prompt Blocks, Project Policies (immutable by
version) and Project Invariants. Prompt Specs themselves reuse the existing
prompt store (``repo.prompts`` / ``repo.prompt_versions``); this module only
adds the surrounding foundation assets and never creates a parallel prompt
system.

Every resource is owner+project scoped: a project lookup is always validated
against the caller's owner id, and a foreign (or missing) resource resolves to
:class:`PromptAssetNotFound`. Domain validation failures raise
:class:`PromptAssetError`; routers translate those to 400 and 404 respectively.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import (
    INVARIANT_ENFORCEMENT_MODES,
    INVARIANT_SEVERITIES,
    INVARIANT_STATUSES,
    POLICY_DOMAINS,
    POLICY_STATUSES,
    PROMPT_BLOCK_CATEGORIES,
    PROMPT_BLOCK_STATUSES,
    new_id,
    now_iso,
)


class PromptAssetError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class PromptAssetNotFound(PromptAssetError):
    """Owner-scoped resource missing (HTTP 404)."""


# ------------------------------------------------------------------ catalogs
# Human-readable metadata for the catalog endpoints. Keys mirror the canonical
# enum tuples in ``app.models`` exactly; nothing here is invented behavior.
BLOCK_CATEGORY_CATALOG: Tuple[Dict[str, str], ...] = (
    {
        "key": "inspect_existing_architecture",
        "label": "Inspect existing architecture",
        "description": "Require the agent to read and understand the existing code before changing it.",
    },
    {
        "key": "bounded_scope",
        "label": "Bounded scope",
        "description": "Keep the change limited to the requested surface; no opportunistic rewrites.",
    },
    {
        "key": "reuse_existing_services",
        "label": "Reuse existing services",
        "description": "Call existing services/helpers instead of duplicating their logic.",
    },
    {
        "key": "no_parallel_system",
        "label": "No parallel system",
        "description": "Extend the first-class subsystem rather than adding a competing one.",
    },
    {
        "key": "preserve_security",
        "label": "Preserve security",
        "description": "Keep ownership/authorization boundaries and secrets handling intact.",
    },
    {
        "key": "tests_required",
        "label": "Tests required",
        "description": "Cover the change with tests that fail before and pass after.",
    },
    {
        "key": "no_false_pass",
        "label": "No false pass",
        "description": "Never report success for unverified or partially implemented behavior.",
    },
    {
        "key": "migration_safety",
        "label": "Migration safety",
        "description": "Keep migrations/index changes additive and backward compatible.",
    },
    {
        "key": "report_changes",
        "label": "Report changes",
        "description": "Summarize files changed, evidence, and any deviations from the request.",
    },
    {
        "key": "other",
        "label": "Other",
        "description": "A prompt block that does not fit the canonical categories.",
    },
)
BLOCK_CATEGORIES_BY_KEY: Dict[str, Dict[str, str]] = {
    entry["key"]: entry for entry in BLOCK_CATEGORY_CATALOG
}

POLICY_DOMAIN_CATALOG: Tuple[Dict[str, str], ...] = (
    {"key": "ARCHITECTURE", "label": "Architecture"},
    {"key": "DESIGN", "label": "Design"},
    {"key": "SECURITY", "label": "Security"},
    {"key": "DATA", "label": "Data"},
    {"key": "CODING", "label": "Coding"},
    {"key": "TESTING", "label": "Testing"},
    {"key": "OPERATIONS", "label": "Operations"},
    {"key": "PRODUCT", "label": "Product"},
)
POLICY_DOMAINS_BY_KEY: Dict[str, Dict[str, str]] = {
    entry["key"]: entry for entry in POLICY_DOMAIN_CATALOG
}


# ------------------------------------------------------------------ primitives
def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _block_hash(content: str, variables: Dict[str, Any]) -> str:
    """Stable hash of a block's material (content + variables)."""
    payload = json.dumps(
        {"content": content or "", "variables": variables or {}},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check_choice(value: Any, allowed: Tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise PromptAssetError(f"Invalid {field}")
    return value


async def _require_project(owner_id: str, project_id: Optional[str]) -> Dict[str, Any]:
    if not project_id:
        raise PromptAssetError("Project not found")
    project = await repo.projects.get(project_id, owner_id)
    if not project:
        raise PromptAssetError("Project not found")
    return project


def _require_name(value: Any, field: str = "name") -> str:
    text = (value or "").strip() if isinstance(value, str) else ""
    if not text:
        raise PromptAssetError(f"{field} is required")
    return text


# ------------------------------------------------------------------ blocks
async def list_blocks(
    owner_id: str,
    project_id: str,
    *,
    category: Optional[str] = None,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    await _require_project(owner_id, project_id)
    query: Dict[str, Any] = {"owner_id": owner_id, "project_id": project_id}
    if category is not None:
        _check_choice(category, PROMPT_BLOCK_CATEGORIES, "category")
        query["category"] = category
    if not include_archived:
        query["status"] = "active"
    return await repo.prompt_blocks.list(query, sort_field="created_at")


async def create_block(owner_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    project_id = payload.get("project_id")
    await _require_project(owner_id, project_id)
    name = _require_name(payload.get("name"))
    category = _check_choice(payload.get("category") or "other", PROMPT_BLOCK_CATEGORIES, "category")
    status = _check_choice(payload.get("status") or "active", PROMPT_BLOCK_STATUSES, "status")
    content = payload.get("content") or ""
    variables = payload.get("variables") or {}
    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "name": name,
        "description": payload.get("description") or "",
        "category": category,
        "content": content,
        "variables": variables,
        "status": status,
        "version": 1,
        "content_hash": _block_hash(content, variables),
        # ``created_by`` mirrors the existing versioned-rubric convention: the
        # owner id of the writer (no separate user directory is persisted).
        "created_by": owner_id,
        "created_at": now,
        "updated_at": now,
    }
    return await repo.prompt_blocks.insert(doc)


async def get_block(owner_id: str, block_id: str) -> Dict[str, Any]:
    block = await repo.prompt_blocks.get(block_id, owner_id)
    if not block:
        raise PromptAssetNotFound("Prompt block not found")
    return block


async def update_block(owner_id: str, block_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    block = await get_block(owner_id, block_id)
    updates: Dict[str, Any] = {}
    for field in ("name", "description", "content", "variables", "category", "status"):
        if field in patch and patch[field] is not None:
            updates[field] = patch[field]

    if "name" in updates:
        updates["name"] = _require_name(updates["name"])
    if "category" in updates:
        updates["category"] = _check_choice(updates["category"], PROMPT_BLOCK_CATEGORIES, "category")
    if "status" in updates:
        updates["status"] = _check_choice(updates["status"], PROMPT_BLOCK_STATUSES, "status")

    material_changed = (
        ("content" in updates and updates["content"] != (block.get("content") or ""))
        or ("variables" in updates and updates["variables"] != (block.get("variables") or {}))
    )
    if material_changed:
        content = updates.get("content", block.get("content") or "")
        variables = updates.get("variables", block.get("variables") or {})
        updates["content_hash"] = _block_hash(content, variables)
        updates["version"] = int(block.get("version") or 1) + 1

    updates["updated_at"] = now_iso()
    return await repo.prompt_blocks.update(block_id, owner_id, updates)


async def delete_block(owner_id: str, block_id: str) -> None:
    await get_block(owner_id, block_id)
    await repo.prompt_blocks.delete(block_id, owner_id)


def _normalized_block_text(text: Any) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


async def find_equivalent_block(
    owner_id: str,
    project_id: str,
    *,
    name: str = "",
    content: str = "",
    category: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return an existing ACTIVE block equivalent to the given block, else None.

    Equivalence is defined conservatively: same category (when provided) AND
    (exact normalized name OR exact normalized content OR identical content
    hash). This is the deduplication guard the V6.14 planning-template mapper
    uses so it never creates a duplicate block for equivalent content.
    """
    await _require_project(owner_id, project_id)
    docs = await repo.prompt_blocks.list(
        {"owner_id": owner_id, "project_id": project_id, "status": "active"},
        sort_field="created_at", descending=False, limit=1000,
    )
    norm_name = _normalized_block_text(name)
    norm_content = _normalized_block_text(content)
    target_hash = _block_hash(content or "", {})
    for doc in docs:
        if category and doc.get("category") != category:
            continue
        if norm_name and _normalized_block_text(doc.get("name")) == norm_name:
            return doc
        if norm_content and _normalized_block_text(doc.get("content")) == norm_content:
            return doc
        if doc.get("content_hash") == target_hash:
            return doc
    return None


# The canonical starter set: one block per category except ``other``. Content is
# concrete and self-contained so it can be materialized into a project with no
# further authoring.
_STARTER_BLOCKS: Tuple[Dict[str, str], ...] = (
    {
        "category": "inspect_existing_architecture",
        "name": "Inspect the existing architecture first",
        "description": "Force an explicit read-before-write step.",
        "content": (
            "Before writing any code, inspect the existing implementation and its "
            "callers: locate the relevant routers, services, repository collections "
            "and models, and state how the requested change fits them. Do not propose "
            "a design until you have read the code it touches."
        ),
    },
    {
        "category": "bounded_scope",
        "name": "Keep the change bounded",
        "description": "Limit work to the requested surface.",
        "content": (
            "Implement only what the task requests. Do not perform opportunistic "
            "refactors, renames, formatting sweeps or dependency changes. If you "
            "believe extra work is required, stop and report it instead of doing it."
        ),
    },
    {
        "category": "reuse_existing_services",
        "name": "Reuse existing services",
        "description": "Prefer existing helpers over new ones.",
        "content": (
            "Search for an existing service, helper or repository method that already "
            "solves the problem and call it. Do not duplicate logic that already lives "
            "in another module; extend the existing function if it needs a new case."
        ),
    },
    {
        "category": "no_parallel_system",
        "name": "No parallel system",
        "description": "Extend first-class subsystems, do not fork them.",
        "content": (
            "When a first-class subsystem already exists for this concern (prompts, "
            "policies, invariants, datasets, evaluation), extend it with additive fields "
            "and endpoints. Never build a competing store, table or code path alongside "
            "the existing one."
        ),
    },
    {
        "category": "preserve_security",
        "name": "Preserve security boundaries",
        "description": "Ownership and secret handling must not regress.",
        "content": (
            "Every read and write must stay owner-scoped and project-validated. Never "
            "expose, log or persist secrets, credentials or another owner's records. "
            "A resource that is not visible to the caller must return not-found, not a "
            "partial result."
        ),
    },
    {
        "category": "tests_required",
        "name": "Tests are required",
        "description": "Prove the change with tests.",
        "content": (
            "Add or update tests that exercise the new behavior end-to-end, including "
            "the failure and isolation paths. Run the focused test file plus the nearest "
            "regression suites before reporting completion."
        ),
    },
    {
        "category": "no_false_pass",
        "name": "No false pass",
        "description": "Never claim success without evidence.",
        "content": (
            "Do not report success for behavior that is unimplemented, stubbed, or "
            "unverified. If a test is skipped, a path is untested, or a step was not "
            "performed, say so explicitly in the report."
        ),
    },
    {
        "category": "migration_safety",
        "name": "Migrations must be safe",
        "description": "Additive, idempotent, non-fatal.",
        "content": (
            "Schema and index changes must be additive and idempotent, and must never "
            "abort startup on legacy data. Existing records and default values must keep "
            "working unchanged; guard new indexes with best-effort creation."
        ),
    },
    {
        "category": "report_changes",
        "name": "Report what changed",
        "description": "End with a precise change report.",
        "content": (
            "Finish with a concise report: files changed, the exact commands run and "
            "their result lines, and any deviation from the request. Do not summarize "
            "without naming the concrete files and evidence."
        ),
    },
)


async def materialize_starter_blocks(owner_id: str, project_id: str) -> Dict[str, Any]:
    """Create the canonical starter blocks once per (project, name).

    Idempotent: a second call creates nothing and reports every block as already
    existing.
    """
    await _require_project(owner_id, project_id)
    created: List[Dict[str, Any]] = []
    existing: List[Dict[str, Any]] = []
    for starter in _STARTER_BLOCKS:
        found = await repo.prompt_blocks.find_one({
            "owner_id": owner_id,
            "project_id": project_id,
            "name": starter["name"],
        })
        if found:
            existing.append(found)
            continue
        created.append(await create_block(owner_id, {
            "project_id": project_id,
            "name": starter["name"],
            "description": starter["description"],
            "category": starter["category"],
            "content": starter["content"],
            "variables": {},
            "status": "active",
        }))
    return {"created": created, "existing": existing}


# ------------------------------------------------------------------ policies
async def list_policies(
    owner_id: str,
    project_id: str,
    *,
    domain: Optional[str] = None,
    include_versions: bool = False,
) -> List[Dict[str, Any]]:
    await _require_project(owner_id, project_id)
    query: Dict[str, Any] = {"owner_id": owner_id, "project_id": project_id}
    if domain is not None:
        _check_choice(domain, POLICY_DOMAINS, "domain")
        query["domain"] = domain
    docs = await repo.project_policies.list(query, sort_field="created_at")
    if include_versions:
        return docs
    # Latest non-archived version per family. Superseded versions remain
    # retrievable through the version-history endpoints.
    latest: Dict[str, Dict[str, Any]] = {}
    for doc in docs:
        if doc.get("status") == "archived":
            continue
        family_id = doc.get("family_id") or doc.get("id")
        current = latest.get(family_id)
        if current is None or int(doc.get("version") or 0) > int(current.get("version") or 0):
            latest[family_id] = doc
    return sorted(
        latest.values(), key=lambda d: d.get("created_at") or "", reverse=True
    )


async def create_policy(owner_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    project_id = payload.get("project_id")
    await _require_project(owner_id, project_id)
    name = _require_name(payload.get("name"))
    domain = _check_choice(payload.get("domain"), POLICY_DOMAINS, "domain")
    policy_id = new_id()
    now = now_iso()
    content = payload.get("content") or ""
    doc = {
        "id": policy_id,
        "owner_id": owner_id,
        "project_id": project_id,
        "family_id": policy_id,
        "version": 1,
        "status": "active",
        "name": name,
        "domain": domain,
        "description": payload.get("description") or "",
        "content": content,
        "content_hash": _hash_text(content),
        "created_by": owner_id,
        "created_at": now,
        "updated_at": now,
    }
    return await repo.project_policies.insert(doc)


async def get_policy(owner_id: str, policy_id: str) -> Dict[str, Any]:
    policy = await repo.project_policies.get(policy_id, owner_id)
    if not policy:
        raise PromptAssetNotFound("Project policy not found")
    return policy


async def list_policy_versions(owner_id: str, policy_id: str) -> List[Dict[str, Any]]:
    policy = await get_policy(owner_id, policy_id)
    family_id = policy.get("family_id") or policy_id
    return await repo.project_policies.list(
        {"owner_id": owner_id, "family_id": family_id},
        sort_field="version", descending=False,
    )


async def create_policy_version(
    owner_id: str,
    policy_id: str,
    content: str,
    description: str = "",
) -> Dict[str, Any]:
    policy = await get_policy(owner_id, policy_id)
    family_id = policy.get("family_id") or policy_id
    latest_docs = await repo.project_policies.list(
        {"owner_id": owner_id, "family_id": family_id},
        sort_field="version", descending=True, limit=1,
    )
    latest = latest_docs[0] if latest_docs else policy
    number = int(latest.get("version") or 0) + 1
    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": policy.get("project_id"),
        "family_id": family_id,
        "version": number,
        "status": "active",
        "name": policy.get("name"),
        "domain": policy.get("domain"),
        "description": description or "",
        "content": content,
        "content_hash": _hash_text(content),
        "created_by": owner_id,
        "created_at": now,
        "updated_at": now,
    }
    await repo.project_policies.update(latest["id"], owner_id, {
        "status": "superseded", "updated_at": now,
    })
    return await repo.project_policies.insert(doc)


async def update_policy_status(owner_id: str, policy_id: str, status: str) -> Dict[str, Any]:
    _check_choice(status, POLICY_STATUSES, "status")
    await get_policy(owner_id, policy_id)
    return await repo.project_policies.update(policy_id, owner_id, {
        "status": status, "updated_at": now_iso(),
    })


# ------------------------------------------------------------------ invariants
async def list_invariants(
    owner_id: str,
    project_id: str,
    *,
    domain: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    await _require_project(owner_id, project_id)
    query: Dict[str, Any] = {"owner_id": owner_id, "project_id": project_id}
    if domain is not None:
        _check_choice(domain, POLICY_DOMAINS, "domain")
        query["domain"] = domain
    if status is not None:
        _check_choice(status, INVARIANT_STATUSES, "status")
        query["status"] = status
    return await repo.project_invariants.list(query, sort_field="created_at")


async def create_invariant(owner_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    project_id = payload.get("project_id")
    await _require_project(owner_id, project_id)
    code = _require_name(payload.get("code"), "code").upper()
    title = _require_name(payload.get("title"), "title")
    domain = _check_choice(payload.get("domain"), POLICY_DOMAINS, "domain")
    severity = _check_choice(
        payload.get("severity") or "medium", INVARIANT_SEVERITIES, "severity"
    )
    enforcement_mode = _check_choice(
        payload.get("enforcement_mode") or "informational",
        INVARIANT_ENFORCEMENT_MODES,
        "enforcement_mode",
    )
    status = _check_choice(payload.get("status") or "active", INVARIANT_STATUSES, "status")
    duplicate = await repo.project_invariants.find_one({
        "owner_id": owner_id,
        "project_id": project_id,
        "code": code,
        "status": "active",
    })
    if duplicate:
        raise PromptAssetError(
            f"An active invariant with code '{code}' already exists in this project"
        )
    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "code": code,
        "domain": domain,
        "title": title,
        "description": payload.get("description") or "",
        "severity": severity,
        "enforcement_mode": enforcement_mode,
        "status": status,
        "created_by": owner_id,
        "created_at": now,
        "updated_at": now,
    }
    return await repo.project_invariants.insert(doc)


async def get_invariant(owner_id: str, invariant_id: str) -> Dict[str, Any]:
    invariant = await repo.project_invariants.get(invariant_id, owner_id)
    if not invariant:
        raise PromptAssetNotFound("Project invariant not found")
    return invariant


async def update_invariant(owner_id: str, invariant_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    invariant = await get_invariant(owner_id, invariant_id)
    updates: Dict[str, Any] = {}
    for field in (
        "code", "domain", "title", "description",
        "severity", "enforcement_mode", "status",
    ):
        if field in patch and patch[field] is not None:
            updates[field] = patch[field]

    if "code" in updates:
        updates["code"] = _require_name(updates["code"], "code").upper()
    if "title" in updates:
        updates["title"] = _require_name(updates["title"], "title")
    if "domain" in updates:
        updates["domain"] = _check_choice(updates["domain"], POLICY_DOMAINS, "domain")
    if "severity" in updates:
        updates["severity"] = _check_choice(updates["severity"], INVARIANT_SEVERITIES, "severity")
    if "enforcement_mode" in updates:
        updates["enforcement_mode"] = _check_choice(
            updates["enforcement_mode"], INVARIANT_ENFORCEMENT_MODES, "enforcement_mode"
        )
    if "status" in updates:
        updates["status"] = _check_choice(updates["status"], INVARIANT_STATUSES, "status")

    new_code = updates.get("code", invariant.get("code"))
    new_status = updates.get("status", invariant.get("status"))
    if new_status == "active":
        duplicate = await repo.project_invariants.find_one({
            "owner_id": owner_id,
            "project_id": invariant.get("project_id"),
            "code": new_code,
            "status": "active",
            "id": {"$ne": invariant_id},
        })
        if duplicate:
            raise PromptAssetError(
                f"An active invariant with code '{new_code}' already exists in this project"
            )

    updates["updated_at"] = now_iso()
    return await repo.project_invariants.update(invariant_id, owner_id, updates)


async def delete_invariant(owner_id: str, invariant_id: str) -> None:
    await get_invariant(owner_id, invariant_id)
    await repo.project_invariants.delete(invariant_id, owner_id)
