"""V6.15 Structured Planning Graph service.

A deterministic, LLM-free graph view over the V6.14 Planning Decision Memory
ledger. The graph is **derived**, never authoritative: every decision-bearing
node references its ``planning_decisions`` row through ``decision_id``, and the
whole structure is rebuilt from the ledger on each :func:`sync_graph` call. The
ledger is only ever read here — this module never writes a decision.

Security properties
------------------
* **Owner + project isolation.** Every read/write is scoped by both; a foreign
  project or node resolves to not-found.
* **Redaction everywhere.** Node/edge public views run every string through
  :mod:`app.services.redaction`.
* **Never raises on malformed docs.** Serializers tolerate odd shapes.
* **Stickiness.** ``review_required`` flags are sticky: sync flags them but
  never clears them (only :func:`clear_review` does).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .. import repository as repo
from ..models import (
    PLANNING_NODE_STATUSES,
    PLANNING_NODE_TYPES,
    PLANNING_RELATIONS,
    new_id,
    now_iso,
)
from . import planning_state, redaction
from .experiments import canonical_hash


class PlanningGraphError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class PlanningGraphNotFound(PlanningGraphError):
    """Owner-scoped resource missing (HTTP 404)."""


# ------------------------------------------------------------------ constants
GRAPH_SCHEMA_VERSION = "v6.15.1"

NODE_TYPES = PLANNING_NODE_TYPES
RELATIONS = PLANNING_RELATIONS
NODE_STATUSES = PLANNING_NODE_STATUSES

#: Ordered domain key -> human title. ``dict`` preserves insertion order.
DEFAULT_SECTIONS: Dict[str, str] = {
    "product": "Product",
    "actors": "Users / Actors",
    "workflows": "Core Workflows",
    "business_rules": "Business Rules",
    "ux": "UX",
    "platform": "Platform",
    "architecture": "Architecture",
    "data": "Data",
    "auth": "Authentication",
    "authorization": "Authorization",
    "security": "Security",
    "integrations": "Integrations",
    "operations": "Operations",
    "constraints": "Constraints",
    "risks": "Risks",
}

#: V6.14 ledger category -> top-level section. Bounded; fallback ``product``.
#: A category that names a section key resolves to that section (bounded,
#: additive alias) so every PRD readiness dimension is reachable from a manual
#: or extracted item.
_CATEGORY_TO_SECTION: Dict[str, str] = {
    "platform": "platform",
    "device": "platform",
    "integration": "integrations",
    "integrations": "integrations",
    "auth": "auth",
    "biometrics": "security",
    "location": "security",
    "security": "security",
    "data": "data",
    "attendance": "business_rules",
    "business_rules": "business_rules",
    "admin": "authorization",
    "authorization": "authorization",
    "enrollment": "actors",
    "actors": "actors",
    "notification": "operations",
    "operations": "operations",
    "backup": "operations",
    "sync": "operations",
    "scale": "operations",
    "product": "product",
    "workflow": "workflows",
    "workflows": "workflows",
    "ux": "ux",
    "architecture": "architecture",
    "constraints": "constraints",
}
_SECTION_FALLBACK = "product"

#: Optional progressive sub-sections: only created when >=1 decision maps here.
_TOPIC_TO_SUBDOMAIN: Dict[str, str] = {
    "platform.mobile": "Mobile",
    "platform.android": "Mobile",
    "platform.ios": "Mobile",
    "integration.hr_api": "HR API",
}

#: Bounded dependency map used for impact analysis. Every target is a valid
#: :data:`DEFAULT_SECTIONS` key.
_SECTION_DEPENDENCIES: Dict[str, List[str]] = {
    "product": ["actors", "workflows", "ux", "business_rules"],
    "actors": ["authorization", "workflows"],
    "workflows": ["business_rules", "data"],
    "business_rules": ["data", "operations"],
    "ux": ["workflows"],
    "platform": ["architecture", "security", "operations"],
    "architecture": ["data", "integrations"],
    "data": ["architecture", "integrations"],
    "auth": ["authorization", "security", "data"],
    "authorization": ["security"],
    "security": ["architecture"],
    "integrations": ["security", "data", "operations"],
    "operations": ["architecture", "security"],
    "risks": ["security", "operations"],
}

#: Ledger kind -> graph node type.
_KIND_TO_NODE_TYPE: Dict[str, str] = {
    "decision": "DECISION",
    "constraint": "CONSTRAINT",
    "assumption": "ASSUMPTION",
    "open_question": "OPEN_QUESTION",
    "verification_item": "REQUIREMENT",
    "rejected_option": "DECISION",
}

#: Bounded category overrides (rejected options always stay DECISION/REJECTED).
_CATEGORY_NODE_TYPE_OVERRIDES: Dict[str, str] = {
    "integration": "INTEGRATION",
}

_WS_RE = re.compile(r"[^a-z0-9]+")


# ------------------------------------------------------------------ helpers
def _redact_str(value: Any) -> Any:
    return redaction.redact_text(value) if isinstance(value, str) else value


def _slug(text: Any) -> str:
    try:
        return _WS_RE.sub("_", str(text or "").lower()).strip("_")[:60]
    except Exception:
        return ""


def _truncate(value: Any, limit: int = 160) -> str:
    try:
        text = value.strip() if isinstance(value, str) else ""
        return text[:limit]
    except Exception:
        return ""


def section_node_key(section_key: str) -> str:
    return f"section:{section_key}"


def subdomain_node_key(section_key: str, subdomain: str) -> str:
    return f"section:{section_key}:{_slug(subdomain)}"


def decision_node_key(item_id: str) -> str:
    return f"decision:{item_id}"


def decision_id_from_node_key(node_key: Any) -> Optional[str]:
    """Return the ledger item id encoded in a decision node key, else ``None``."""
    if isinstance(node_key, str) and node_key.startswith("decision:"):
        rest = node_key[len("decision:"):]
        return rest or None
    return None


def _section_for(category: Any) -> str:
    key = category.lower() if isinstance(category, str) else ""
    return _CATEGORY_TO_SECTION.get(key, _SECTION_FALLBACK)


def _node_type_for(item: Dict[str, Any]) -> str:
    kind = item.get("kind") or "decision"
    if kind == "rejected_option":
        return "DECISION"
    override = _CATEGORY_NODE_TYPE_OVERRIDES.get(
        (item.get("category") or "").lower()
        if isinstance(item.get("category"), str) else ""
    )
    if override:
        return override
    return _KIND_TO_NODE_TYPE.get(kind, "DECISION")


def _node_status_for(item: Dict[str, Any]) -> str:
    status = item.get("status") or "CONFIRMED_BY_USER"
    return status if status in PLANNING_NODE_STATUSES else "CONFIRMED_BY_USER"


def _fingerprint(item: Dict[str, Any]) -> str:
    """Stable hash of the decision-material fields. Empty item -> "" (never raises)."""
    try:
        return canonical_hash({
            "status": item.get("status"),
            "value": item.get("value"),
            "verification_status": item.get("verification_status"),
            "kind": item.get("kind"),
            "category": item.get("category"),
        })
    except Exception:
        return ""


def _is_structural(node: Dict[str, Any]) -> bool:
    return node.get("type") in ("ROOT", "SECTION") or node.get("node_key") == "root"


# ------------------------------------------------------------------ serializers
def _public_node(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        doc = doc or {}
        return {
            "id": doc.get("id"),
            "project_id": _redact_str(doc.get("project_id")),
            "node_key": _redact_str(doc.get("node_key") or ""),
            "type": _redact_str(doc.get("type") or ""),
            "title": _redact_str(doc.get("title") or ""),
            "content": _redact_str(doc.get("content") or ""),
            "status": _redact_str(doc.get("status") or ""),
            "decision_id": doc.get("decision_id"),
            "topic_key": _redact_str(doc.get("topic_key") or ""),
            "category": _redact_str(doc.get("category") or ""),
            "section_key": _redact_str(doc.get("section_key") or ""),
            "subdomain": _redact_str(doc.get("subdomain") or ""),
            "conversation_id": _redact_str(doc.get("conversation_id")),
            "source_message_id": _redact_str(doc.get("source_message_id")),
            "order": doc.get("order") or 0,
            "review_required": bool(doc.get("review_required")),
            "review_reason": _redact_str(doc.get("review_reason") or ""),
            "review_at": doc.get("review_at"),
            "decision_fingerprint": doc.get("decision_fingerprint") or "",
            "provenance": redaction.redact(doc.get("provenance") or {}),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        }
    except Exception:
        return {"id": (doc or {}).get("id") if isinstance(doc, dict) else None}


def _public_edge(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        doc = doc or {}
        return {
            "id": doc.get("id"),
            "project_id": _redact_str(doc.get("project_id")),
            "source_node_id": doc.get("source_node_id"),
            "target_node_id": doc.get("target_node_id"),
            "relation": _redact_str(doc.get("relation") or ""),
            "provenance": redaction.redact(doc.get("provenance") or {}),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        }
    except Exception:
        return {"id": (doc or {}).get("id") if isinstance(doc, dict) else None}


# ------------------------------------------------------------------ catalog
def catalog() -> Dict[str, Any]:
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "node_types": list(NODE_TYPES),
        "relations": list(RELATIONS),
        "statuses": list(NODE_STATUSES),
        "sections": [
            {"key": key, "title": title} for key, title in DEFAULT_SECTIONS.items()
        ],
    }


# ------------------------------------------------------------------ guards
async def _require_project(owner_id: str, project_id: Optional[str]) -> Dict[str, Any]:
    project = await repo.projects.get(project_id, owner_id) if project_id else None
    if not project:
        raise PlanningGraphNotFound("Project not found")
    return project


async def _require_node(owner_id: str, node_id: Optional[str]) -> Dict[str, Any]:
    node = await repo.planning_nodes.get(node_id, owner_id) if node_id else None
    if not node:
        raise PlanningGraphNotFound("Planning node not found")
    return node


# ------------------------------------------------------------------ upsert
async def _upsert_node(
    owner_id: str,
    project_id: str,
    *,
    node_key: str,
    fields: Dict[str, Any],
    review_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert a node keyed by ``(owner, project, node_key)``.

    Existing node ids and sticky review flags are always preserved. When
    ``review_reason`` is given the node is flagged (but an existing reason is
    never overwritten).
    """
    now = now_iso()
    existing = await repo.planning_nodes.find_one({
        "owner_id": owner_id,
        "project_id": project_id,
        "node_key": node_key,
    })
    if existing:
        patch = dict(fields)
        patch["updated_at"] = now
        if review_reason:
            patch["review_required"] = True
            if not existing.get("review_required"):
                patch["review_reason"] = review_reason
                patch["review_at"] = now
        doc = await repo.planning_nodes.update(existing["id"], owner_id, patch)
        return doc or {**existing, **patch}

    doc: Dict[str, Any] = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "node_key": node_key,
        "type": "SECTION",
        "title": "",
        "content": "",
        "status": "STRUCTURAL",
        "decision_id": None,
        "topic_key": "",
        "category": "",
        "section_key": "",
        "subdomain": "",
        "conversation_id": None,
        "source_message_id": None,
        "provenance": {},
        "order": 0,
        "review_required": False,
        "review_reason": "",
        "review_at": None,
        "decision_fingerprint": "",
        "created_at": now,
        "updated_at": now,
    }
    doc.update(fields)
    if review_reason:
        doc["review_required"] = True
        doc["review_reason"] = review_reason
        doc["review_at"] = now
    await repo.planning_nodes.insert(doc)
    return doc


def _subsection_order(section_key: str) -> int:
    try:
        base = list(DEFAULT_SECTIONS).index(section_key)
    except ValueError:
        base = 0
    return base * 10 + 5


# ------------------------------------------------------------------ sync core
async def _sync_core(
    owner_id: str,
    project_id: str,
    project: Dict[str, Any],
    *,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Rebuild the derived graph for ``(owner, project)`` from the ledger."""
    # 2. Active ledger items (never superseded) -- READ ONLY.
    items = await repo.planning_decisions.list(
        {
            "owner_id": owner_id,
            "project_id": project_id,
            "status": {"$ne": "SUPERSEDED"},
        },
        sort_field="created_at",
        descending=False,
        limit=2000,
    )
    if conversation_id:
        items = [i for i in items if i.get("conversation_id") == conversation_id]

    # 3. Open conflicts (project-wide) drive CONFLICTS_WITH edges.
    conflicts = await repo.planning_conflicts.list(
        {"owner_id": owner_id, "project_id": project_id, "status": "open"},
        sort_field="created_at",
        descending=False,
        limit=2000,
    )

    # 4. ROOT.
    root_title = _redact_str(project.get("name") or "Product")
    await _upsert_node(
        owner_id, project_id, node_key="root",
        fields={
            "type": "ROOT", "title": root_title, "content": "",
            "status": "STRUCTURAL", "decision_id": None, "topic_key": "",
            "category": "", "section_key": "", "subdomain": "",
            "conversation_id": None, "source_message_id": None,
            "provenance": {}, "order": 0, "decision_fingerprint": "",
        },
    )

    # 5. One SECTION per DEFAULT_SECTIONS key.
    for index, (key, title) in enumerate(DEFAULT_SECTIONS.items()):
        await _upsert_node(
            owner_id, project_id, node_key=section_node_key(key),
            fields={
                "type": "SECTION", "title": title, "content": "",
                "status": "STRUCTURAL", "decision_id": None, "topic_key": "",
                "category": "", "section_key": key, "subdomain": "",
                "conversation_id": None, "source_message_id": None,
                "provenance": {}, "order": (index + 1) * 10,
                "decision_fingerprint": "",
            },
        )

    # 6. Decision-bearing nodes.
    flagged_sections: set = set()
    for index, item in enumerate(items):
        item_id = item.get("id")
        if not item_id:
            continue
        section_key = _section_for(item.get("category"))
        topic_key = item.get("topic_key") or ""
        subdomain = _TOPIC_TO_SUBDOMAIN.get(topic_key, "")
        node_key = decision_node_key(item_id)
        fingerprint = _fingerprint(item)

        existing = await repo.planning_nodes.find_one({
            "owner_id": owner_id,
            "project_id": project_id,
            "node_key": node_key,
        })
        if (existing and existing.get("decision_fingerprint")
                and existing.get("decision_fingerprint") != fingerprint):
            flagged_sections.add(section_key)
            flagged_sections.update(_SECTION_DEPENDENCIES.get(section_key, []))

        await _upsert_node(
            owner_id, project_id, node_key=node_key,
            fields={
                "type": _node_type_for(item),
                "title": _redact_str(_truncate(item.get("value"))),
                "content": _redact_str(item.get("value") or ""),
                "status": _node_status_for(item),
                "decision_id": item_id,
                "topic_key": _redact_str(topic_key),
                "category": _redact_str(item.get("category") or ""),
                "section_key": section_key,
                "subdomain": subdomain,
                "conversation_id": _redact_str(item.get("conversation_id")),
                "source_message_id": _redact_str(item.get("source_message_id")),
                "provenance": redaction.redact(item.get("provenance") or {}),
                "order": 100 + index,
                "decision_fingerprint": fingerprint,
            },
        )
        if subdomain:
            await _upsert_node(
                owner_id, project_id,
                node_key=subdomain_node_key(section_key, subdomain),
                fields={
                    "type": "SECTION",
                    "title": _redact_str(subdomain),
                    "content": "", "status": "STRUCTURAL", "decision_id": None,
                    "topic_key": "", "category": "", "section_key": section_key,
                    "subdomain": subdomain, "conversation_id": None,
                    "source_message_id": None, "provenance": {},
                    "order": _subsection_order(section_key),
                    "decision_fingerprint": "",
                },
            )

    # Flag sections whose decisions changed (sticky, ledger untouched).
    now = now_iso()
    for key in flagged_sections:
        if key not in DEFAULT_SECTIONS:
            continue
        sec = await repo.planning_nodes.find_one({
            "owner_id": owner_id,
            "project_id": project_id,
            "node_key": section_node_key(key),
        })
        if sec and not sec.get("review_required"):
            await repo.planning_nodes.update(sec["id"], owner_id, {
                "review_required": True,
                "review_reason": "decision changed",
                "review_at": now,
                "updated_at": now,
            })

    # Mark derived nodes whose ledger item was superseded: the node stays for
    # provenance/history but is surfaced as SUPERSEDED, never as active.
    active_ids = {i.get("id") for i in items if i.get("id")}
    existing_nodes = await repo.planning_nodes.list(
        {"owner_id": owner_id, "project_id": project_id}, limit=5000,
    )
    for node in existing_nodes:
        decision_id = node.get("decision_id")
        if not decision_id or decision_id in active_ids:
            continue
        if node.get("status") == "SUPERSEDED":
            continue
        ledger = await repo.planning_decisions.get(decision_id, owner_id)
        if ledger and ledger.get("status") == "SUPERSEDED":
            await repo.planning_nodes.update(node["id"], owner_id, {
                "status": "SUPERSEDED", "updated_at": now,
            })

    # 7. Rebuild derived edges deterministically.
    edges = await _rebuild_edges(owner_id, project_id, conflicts)
    return {"synced_at": now_iso(), "items": len(items), "edges": len(edges)}


async def _rebuild_edges(
    owner_id: str, project_id: str, conflicts: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    await repo.planning_edges.delete_many({
        "owner_id": owner_id, "project_id": project_id,
    })
    nodes = await repo.planning_nodes.list(
        {"owner_id": owner_id, "project_id": project_id},
        sort_field="order", descending=False, limit=5000,
    )
    by_key = {n.get("node_key"): n for n in nodes if n.get("node_key")}
    root = by_key.get("root")
    section_nodes = {k: by_key.get(section_node_key(k)) for k in DEFAULT_SECTIONS}

    now = now_iso()
    pending: List[Dict[str, Any]] = []

    def add(source: Optional[Dict[str, Any]], target: Optional[Dict[str, Any]],
            relation: str, provenance: Optional[Dict[str, Any]] = None) -> None:
        if not source or not target or relation not in PLANNING_RELATIONS:
            return
        pending.append({
            "id": new_id(),
            "owner_id": owner_id,
            "project_id": project_id,
            "source_node_id": source.get("id"),
            "target_node_id": target.get("id"),
            "relation": relation,
            "provenance": redaction.redact(provenance or {}),
            "created_at": now,
            "updated_at": now,
        })

    # ROOT -> section
    for section in section_nodes.values():
        add(root, section, "CONTAINS")
    # section -> dependent sections
    for key, deps in _SECTION_DEPENDENCIES.items():
        for dep in deps:
            add(section_nodes.get(key), section_nodes.get(dep), "DEPENDS_ON")

    # section/subsection -> decision, and decision -> section (DERIVED_FROM)
    decision_nodes = [
        n for n in nodes
        if n.get("decision_id") and decision_id_from_node_key(n.get("node_key"))
    ]
    for node in decision_nodes:
        section_key = node.get("section_key")
        section = section_nodes.get(section_key)
        subdomain = node.get("subdomain")
        sub_node = (
            by_key.get(subdomain_node_key(section_key, subdomain))
            if section_key and subdomain else None
        )
        add(sub_node or section, node, "CONTAINS")
        add(node, section, "DERIVED_FROM")

    # open conflict -> CONFLICTS_WITH between the conflicted decision and its
    # previous/active decision for the same topic.
    for conflict in conflicts:
        new_item_id = conflict.get("new_item_id")
        prev_item_id = conflict.get("previous_item_id")
        conflicted = by_key.get(decision_node_key(new_item_id)) if new_item_id else None
        if conflicted is None:
            continue
        previous = by_key.get(decision_node_key(prev_item_id)) if prev_item_id else None
        if previous is None:
            topic = conflict.get("topic_key") or ""
            previous = next(
                (n for n in decision_nodes
                 if n.get("topic_key") == topic and n is not conflicted),
                None,
            )
        add(conflicted, previous, "CONFLICTS_WITH",
            provenance={"conflict_id": conflict.get("id")})

    for edge in pending:
        try:
            await repo.planning_edges.insert(edge)
        except Exception:
            # Unique-key collisions are impossible after delete_many, but never
            # let a single malformed edge abort the whole rebuild.
            continue
    return pending


# ------------------------------------------------------------------ assemble
async def _assemble(
    owner_id: str, project_id: str, conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    nodes = await repo.planning_nodes.list(
        {"owner_id": owner_id, "project_id": project_id},
        sort_field="order", descending=False, limit=5000,
    )
    edges = await repo.planning_edges.list(
        {"owner_id": owner_id, "project_id": project_id},
        sort_field="created_at", descending=False, limit=20000,
    )

    if conversation_id:
        visible = [
            n for n in nodes
            if _is_structural(n) or n.get("conversation_id") == conversation_id
        ]
    else:
        visible = list(nodes)
    visible_ids = {n.get("id") for n in visible}
    visible_edges = [
        e for e in edges
        if e.get("source_node_id") in visible_ids
        and e.get("target_node_id") in visible_ids
    ]

    by_key = {n.get("node_key"): n for n in visible if n.get("node_key")}
    decision_nodes = [n for n in visible if n.get("decision_id")]

    sections_payload: List[Dict[str, Any]] = []
    for key, title in DEFAULT_SECTIONS.items():
        section = by_key.get(section_node_key(key))
        members = [n for n in decision_nodes if n.get("section_key") == key]
        counts = {
            "confirmed": sum(
                1 for n in members
                if n.get("status") in ("CONFIRMED_BY_USER", "TECHNICALLY_VERIFIED")
            ),
            "assumed": sum(1 for n in members if n.get("status") == "ASSUMED_BY_MODEL"),
            "needs_verification": sum(
                1 for n in members
                if n.get("status") == "NEEDS_VERIFICATION"
                and n.get("type") != "OPEN_QUESTION"
            ),
            "rejected": sum(1 for n in members if n.get("status") == "REJECTED"),
            "open": sum(1 for n in members if n.get("type") == "OPEN_QUESTION"),
            "review_required": sum(
                1 for n in members if n.get("review_required")
            ),
        }
        review_required = bool((section or {}).get("review_required")) or (
            counts["review_required"] > 0
        )
        sections_payload.append({
            "key": key,
            "title": title,
            "node_id": (section or {}).get("id"),
            "counts": counts,
            "review_required": review_required,
        })

    counts_total = {
        "nodes": len(visible),
        "edges": len(visible_edges),
        "decisions": sum(1 for n in decision_nodes if n.get("type") == "DECISION"),
        "assumptions": sum(1 for n in decision_nodes if n.get("type") == "ASSUMPTION"),
        "needs_verification": sum(
            1 for n in decision_nodes
            if n.get("status") == "NEEDS_VERIFICATION"
            and n.get("type") != "OPEN_QUESTION"
        ),
        "open_questions": sum(
            1 for n in decision_nodes if n.get("type") == "OPEN_QUESTION"
        ),
        "conflicts": sum(
            1 for e in visible_edges if e.get("relation") == "CONFLICTS_WITH"
        ),
        "review_required": sum(
            1 for n in visible if n.get("review_required")
        ),
    }

    synced_at = max(
        (n.get("updated_at") for n in nodes if n.get("updated_at")), default=None
    )
    return {
        "project_id": project_id,
        "conversation_id": conversation_id,
        "schema_version": GRAPH_SCHEMA_VERSION,
        "synced_at": synced_at,
        "nodes": [
            _public_node(n)
            for n in sorted(visible, key=lambda x: (x.get("order") or 0))
        ],
        "edges": [_public_edge(e) for e in visible_edges],
        "sections": sections_payload,
        "counts": counts_total,
    }


# ------------------------------------------------------------------ public API
async def sync_graph(
    owner_id: str,
    project_id: str,
    *,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Idempotently rebuild the derived graph, then return its assembled view."""
    project = await _require_project(owner_id, project_id)
    await _sync_core(
        owner_id, project_id, project, conversation_id=conversation_id
    )
    return await _assemble(owner_id, project_id, conversation_id)


async def get_graph(
    owner_id: str,
    project_id: str,
    *,
    conversation_id: Optional[str] = None,
    sync: bool = True,
) -> Dict[str, Any]:
    """Assemble the graph, optionally syncing from the ledger first.

    The sync is always project-wide so the derived edges stay complete; the
    ``conversation_id`` only filters the returned view (structure is kept).
    """
    project = await _require_project(owner_id, project_id)
    if sync:
        await _sync_core(owner_id, project_id, project)
    return await _assemble(owner_id, project_id, conversation_id)


async def _node_history(
    owner_id: str, node: Dict[str, Any]
) -> List[Dict[str, Any]]:
    topic_key = node.get("topic_key")
    if not topic_key:
        return []
    query: Dict[str, Any] = {
        "owner_id": owner_id,
        "project_id": node.get("project_id"),
        "topic_key": topic_key,
    }
    if node.get("conversation_id"):
        query["conversation_id"] = node["conversation_id"]
    docs = await repo.planning_decisions.list(
        query, sort_field="created_at", descending=False, limit=20,
    )
    return [
        {
            "decision_id": d.get("id"),
            "status": _redact_str(d.get("status") or ""),
            "value": _redact_str(d.get("value") or ""),
            "created_at": d.get("created_at"),
            "superseded_by_id": d.get("superseded_by_id"),
        }
        for d in docs
    ]


async def get_node(
    owner_id: str, node_id: str, *, project_id: Optional[str] = None
) -> Dict[str, Any]:
    """Owner-scoped node detail with authoritative decision, links and history.

    When ``project_id`` is given the node must belong to that project, else the
    resource is reported as not-found: node ids can never be used to read across
    projects.
    """
    node = await _require_node(owner_id, node_id)
    node_project_id = node.get("project_id")
    if project_id and node_project_id != project_id:
        raise PlanningGraphNotFound("Planning node not found")
    project_id = node_project_id

    decision: Optional[Dict[str, Any]] = None
    decision_id = node.get("decision_id")
    if decision_id:
        doc = await repo.planning_decisions.get(decision_id, owner_id)
        if doc and doc.get("project_id") == project_id:
            decision = planning_state._public_item(doc)

    edges = await repo.planning_edges.list(
        {"owner_id": owner_id, "project_id": project_id},
        sort_field="created_at", descending=False, limit=5000,
    )
    dependencies: List[str] = []
    dependents: List[str] = []
    affected: List[str] = []
    for edge in edges:
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")
        relation = edge.get("relation")
        if source == node_id:
            if relation == "CONTAINS":
                dependencies.append(target)
            elif relation in ("AFFECTS", "RELATES_TO"):
                affected.append(target)
        if target == node_id:
            if relation in ("CONTAINS", "DEPENDS_ON"):
                dependents.append(source)
            if relation in ("AFFECTS", "RELATES_TO"):
                affected.append(source)

    other_ids = [i for i in (dependencies + dependents + affected) if i]
    node_docs: Dict[str, Dict[str, Any]] = {}
    if other_ids:
        docs = await repo.planning_nodes.list(
            {
                "owner_id": owner_id,
                "project_id": project_id,
                "id": {"$in": list(dict.fromkeys(other_ids))},
            },
            limit=1000,
        )
        node_docs = {d.get("id"): d for d in docs}

    def collect(ids: List[str]) -> List[Dict[str, Any]]:
        seen: set = set()
        out: List[Dict[str, Any]] = []
        for i in ids:
            if i in seen or i not in node_docs:
                continue
            seen.add(i)
            out.append(_public_node(node_docs[i]))
        return out

    return {
        "node": _public_node(node),
        "decision": decision,
        "provenance": redaction.redact(node.get("provenance") or {}),
        "dependencies": collect(dependencies),
        "dependents": collect(dependents),
        "affected": collect(affected),
        "history": await _node_history(owner_id, node),
    }


async def analyze_impact(
    owner_id: str,
    project_id: str,
    *,
    decision_id: Optional[str] = None,
    node_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Flag planning areas that may need revalidation after a decision change.

    Read-only with respect to the ledger: only derived graph node review flags
    are set, never a ``planning_decisions`` row.
    """
    await _require_project(owner_id, project_id)

    target: Optional[Dict[str, Any]] = None
    if node_id:
        candidate = await repo.planning_nodes.get(node_id, owner_id)
        if candidate and candidate.get("project_id") == project_id:
            target = candidate
    elif decision_id:
        docs = await repo.planning_nodes.list(
            {
                "owner_id": owner_id,
                "project_id": project_id,
                "decision_id": decision_id,
            },
            limit=5,
        )
        target = docs[0] if docs else None
        if target is None:
            # The graph may not have been built yet; derive it, then retry.
            project = await _require_project(owner_id, project_id)
            await _sync_core(owner_id, project_id, project)
            docs = await repo.planning_nodes.list(
                {
                    "owner_id": owner_id,
                    "project_id": project_id,
                    "decision_id": decision_id,
                },
                limit=5,
            )
            target = docs[0] if docs else None

    if target is None:
        raise PlanningGraphNotFound("Planning node not found")

    section_key = target.get("section_key") or ""
    ordered: List[str] = []
    if section_key in DEFAULT_SECTIONS:
        for key in [section_key, *_SECTION_DEPENDENCIES.get(section_key, [])]:
            if key in DEFAULT_SECTIONS and key not in ordered:
                ordered.append(key)

    all_nodes = await repo.planning_nodes.list(
        {"owner_id": owner_id, "project_id": project_id}, limit=5000,
    )
    by_key = {n.get("node_key"): n for n in all_nodes if n.get("node_key")}
    changed_section_node = by_key.get(section_node_key(section_key)) if section_key else None

    now = now_iso()
    affected_areas: List[Dict[str, Any]] = []
    for key in ordered:
        section = by_key.get(section_node_key(key))
        if section:
            if not section.get("review_required"):
                updated = await repo.planning_nodes.update(section["id"], owner_id, {
                    "review_required": True,
                    "review_reason": "possible impact from changed decision",
                    "review_at": now,
                    "updated_at": now,
                })
                section = updated or section
            affected_areas.append({
                "node_id": section.get("id"),
                "section_key": key,
                "title": _redact_str(section.get("title") or DEFAULT_SECTIONS.get(key, "")),
                "reason": "section may require revalidation",
                "review_required": bool(section.get("review_required")),
            })
        for node in all_nodes:
            if node.get("decision_id") and node.get("section_key") == key:
                affected_areas.append({
                    "node_id": node.get("id"),
                    "section_key": key,
                    "title": _redact_str(node.get("title") or ""),
                    "reason": "possible impact from changed decision",
                    "review_required": bool(node.get("review_required")),
                })

    decision: Optional[Dict[str, Any]] = None
    target_decision_id = target.get("decision_id")
    if target_decision_id:
        doc = await repo.planning_decisions.get(target_decision_id, owner_id)
        if doc and doc.get("project_id") == project_id:
            decision = planning_state._public_item(doc)

    changed_section = None
    if changed_section_node:
        changed_section = {
            "key": section_key,
            "title": _redact_str(changed_section_node.get("title") or ""),
            "node_id": changed_section_node.get("id"),
        }

    count = len(affected_areas)
    return {
        "project_id": project_id,
        "decision": decision,
        "changed_section": changed_section,
        "affected_areas": affected_areas,
        "count": count,
        "message": f"{count} planning areas may require revalidation.",
    }


async def clear_review(
    owner_id: str, project_id: str, node_id: str
) -> Dict[str, Any]:
    """Clear the sticky review flag on one owner+project scoped node."""
    await _require_project(owner_id, project_id)
    node = await repo.planning_nodes.get(node_id, owner_id) if node_id else None
    if not node or node.get("project_id") != project_id:
        raise PlanningGraphNotFound("Planning node not found")
    now = now_iso()
    doc = await repo.planning_nodes.update(node_id, owner_id, {
        "review_required": False,
        "review_reason": "",
        "review_at": None,
        "updated_at": now,
    })
    return _public_node(doc or node)
