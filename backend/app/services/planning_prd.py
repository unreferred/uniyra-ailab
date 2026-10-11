"""V6.15.x Living PRD: readiness, compilation, versioning, diff and review.

The Living PRD is a **compiled artifact** derived from the authoritative V6.14
Planning Decision Memory ledger and the derived V6.15 planning graph. It never
rewrites a decision: every compiled PRD item carries the ``decision_id`` of the
ledger row it came from, assumptions and unverified claims stay labelled as such,
and approval is a *document review* workflow only.

APPROVED PRD != CERTIFIED asset. Approving a PRD version records a review
decision on a compiled document. It does NOT certify, freeze or otherwise couple
to the V7 / Phase-5 certification pipeline: no certification candidate, asset or
evidence record is ever created here.

Security properties
-------------------
* **Owner + project isolation.** Every read/write is scoped by both; a foreign
  project or version resolves to not-found.
* **Redaction everywhere.** Titles, markdown, items, planning state and every
  public field run through :mod:`app.services.redaction`.
* **No new inference.** Compilation is deterministic and LLM-free; readiness is
  a set of explicit dimension states, never a fabricated percentage.
* **Never raises on malformed docs.** Serializers tolerate odd shapes.
"""
from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import PRD_STATUSES, READINESS_STATES, new_id, now_iso
from . import planning_graph, planning_state, redaction
from .experiments import canonical_hash


class PlanningPrdError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class PlanningPrdNotFound(PlanningPrdError):
    """Owner-scoped resource missing (HTTP 404)."""


PRD_COMPILER_VERSION = "v6.15.0"

#: Explicit readiness dimensions. Readiness is NEVER a fake percentage: each
#: dimension is reported independently with a bounded, deterministic state.
_DIMENSIONS: Tuple[Dict[str, Any], ...] = (
    {"key": "product_goal", "label": "Product goal",
     "section_keys": ("product",)},
    {"key": "primary_users", "label": "Primary users / actors",
     "section_keys": ("actors",)},
    {"key": "core_workflows", "label": "Core workflows",
     "section_keys": ("workflows",)},
    {"key": "critical_business_rules", "label": "Critical business rules",
     "section_keys": ("business_rules",)},
    {"key": "security_boundaries", "label": "Security boundaries",
     "section_keys": ("security", "auth")},
    {"key": "data_ownership", "label": "Data ownership",
     "section_keys": ("data",)},
    {"key": "integration_requirements", "label": "Integration requirements",
     "section_keys": ("integrations",)},
    {"key": "technical_constraints", "label": "Technical constraints",
     "section_keys": ("platform", "architecture", "constraints")},
)

#: An item in one of these statuses makes a dimension READY.
_READY_STATUSES = ("CONFIRMED_BY_USER", "TECHNICALLY_VERIFIED", "REJECTED")
#: Dimension states that still permit generation (verification is visible work,
#: not a blocker).
_READY_DIMENSION_STATES = ("READY", "NEEDS_VERIFICATION")

#: section key -> dimension keys that own it. Built once from ``_DIMENSIONS``.
_SECTION_TO_DIMENSIONS: Dict[str, List[str]] = {}
for _dim in _DIMENSIONS:
    for _sk in _dim["section_keys"]:
        _SECTION_TO_DIMENSIONS.setdefault(_sk, []).append(_dim["key"])

#: PRD section order + node selectors. A selector is
#: ``(section_key, allowed_node_types | None)`` where ``None`` means any type.
_PRD_SECTIONS: Tuple[Tuple[str, str, Tuple[Tuple[str, Optional[Tuple[str, ...]]], ...]], ...] = (
    ("product", "Product", (("product", None),)),
    ("actors", "Users / Actors", (("actors", None),)),
    ("functional_requirements", "Functional Requirements", (
        ("workflows", ("DECISION", "REQUIREMENT")),
        ("integrations", ("REQUIREMENT",)),
    )),
    ("workflows", "Core Workflows", (("workflows", None),)),
    ("business_rules", "Business Rules", (("business_rules", None),)),
    ("authorization", "Permissions / Authorization", (("authorization", None),)),
    ("integrations", "Integrations", (("integrations", None),)),
    ("ux", "UX Requirements", (("ux", None),)),
    ("technical_requirements", "Technical Requirements", (
        ("platform", None), ("architecture", None), ("data", None),
    )),
    ("security_requirements", "Security Requirements", (
        ("security", None), ("auth", None),
    )),
    ("operations", "Operational / Non-functional Requirements", (("operations", None),)),
)
_PLANNING_STATE_KEY = "planning_state"
_PLANNING_STATE_TITLE = "Planning State"
_PLANNING_STATE_GROUPS: Tuple[Tuple[str, str], ...] = (
    ("confirmed_decisions", "Confirmed Decisions"),
    ("assumptions", "Assumptions"),
    ("verification_items", "Needs Verification"),
    ("conflicts", "Conflicts"),
    ("open_questions", "Remaining Open Questions"),
)

#: status -> evidence class. A model assumption/unverified claim can never be
#: promoted to CONFIRMED_FACT here.
_EVIDENCE_BY_STATUS: Dict[str, str] = {
    "CONFIRMED_BY_USER": "CONFIRMED_FACT",
    "TECHNICALLY_VERIFIED": "CONFIRMED_FACT",
    "ASSUMED_BY_MODEL": "ASSUMPTION",
    "NEEDS_VERIFICATION": "NEEDS_VERIFICATION",
    "REJECTED": "REJECTED",
    "CONFLICTED": "NEEDS_VERIFICATION",
}

#: Deterministic markdown prefixes so a reader can never mistake an assumption or
#: an unverified claim for a settled fact.
_MARKDOWN_PREFIX: Dict[str, str] = {
    "ASSUMPTION": "Assumption (not confirmed): ",
    "NEEDS_VERIFICATION": "Needs verification: ",
    "REJECTED": "Out of scope / rejected: ",
    "OPEN_QUESTION": "Open question: ",
}

_TITLE_CAP = 200
_TEXT_CAP = 600


# ------------------------------------------------------------------ helpers
def _redact_str(value: Any) -> Any:
    return redaction.redact_text(value) if isinstance(value, str) else value


def _bounded(value: Any, limit: int) -> str:
    """Redact, strip and truncate a scalar for prompt/document output."""
    try:
        if value is None:
            return ""
        text = redaction.redact_text(str(value)).strip()
        return text[:limit]
    except Exception:
        return ""


def _node_section_key(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    return _redact_str(node.get("section_key") or "")


def _dimension_for_section(section_key: str) -> str:
    dims = _SECTION_TO_DIMENSIONS.get(section_key) or []
    return dims[0] if dims else ""


def _blocker_section(blocker: Dict[str, Any], nodes_by_decision: Dict[str, Dict[str, Any]]) -> str:
    """Resolve a blocker to a graph section via its decision node, else category."""
    item_id = blocker.get("new_item_id") or blocker.get("id")
    node = nodes_by_decision.get(item_id) if item_id else None
    section = _node_section_key(node) if node else ""
    if not section:
        section = planning_graph._section_for(blocker.get("category"))
    return section


def _evidence_class(node: Dict[str, Any]) -> str:
    """Derive an evidence class from a graph node (type + status)."""
    node_type = node.get("type")
    if node_type == "OPEN_QUESTION":
        return "OPEN_QUESTION"
    status = node.get("status")
    if node_type in ("ROOT", "SECTION") or status == "STRUCTURAL":
        return "STRUCTURAL"
    # Unknown/odd statuses must never be over-claimed as confirmed facts.
    return _EVIDENCE_BY_STATUS.get(status, "NEEDS_VERIFICATION")


def _prd_item(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "text": _bounded(node.get("content") or node.get("title") or "", _TEXT_CAP),
        "status": _redact_str(node.get("status") or ""),
        "evidence_class": _evidence_class(node),
        "decision_id": node.get("decision_id"),
        "topic_key": _redact_str(node.get("topic_key") or ""),
        "provenance": redaction.redact(node.get("provenance") or {}),
    }


# ------------------------------------------------------------------ guards
async def _require_project(owner_id: str, project_id: Optional[str]) -> Dict[str, Any]:
    project = await repo.projects.get(project_id, owner_id) if project_id else None
    if not project:
        raise PlanningPrdNotFound("Project not found")
    return project


async def _require_version(
    owner_id: str, version_id: Optional[str], *, project_id: Optional[str] = None
) -> Dict[str, Any]:
    """Owner-scoped version lookup, optionally narrowed to one project.

    When ``project_id`` is supplied the version must belong to that project,
    else it is reported as not-found: a version id can never be used to read
    across the owner's other projects.
    """
    doc = await repo.planning_prds.get(version_id, owner_id) if version_id else None
    if not doc:
        raise PlanningPrdNotFound("PRD version not found")
    if project_id and doc.get("project_id") != project_id:
        raise PlanningPrdNotFound("PRD version not found")
    return doc


async def _load_graph(
    owner_id: str, project_id: str, conversation_id: Optional[str]
) -> Dict[str, Any]:
    """Derive/sync the planning graph, translating graph errors to PRD errors."""
    try:
        return await planning_graph.get_graph(
            owner_id, project_id, conversation_id=conversation_id, sync=True
        )
    except planning_graph.PlanningGraphNotFound as exc:
        raise PlanningPrdNotFound(str(exc)) from exc
    except planning_graph.PlanningGraphError as exc:
        raise PlanningPrdError(str(exc)) from exc


async def _latest_version(owner_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    latest = await repo.planning_prds.list(
        {"owner_id": owner_id, "project_id": project_id},
        sort_field="version", descending=True, limit=1,
    )
    return latest[0] if latest else None


# ------------------------------------------------------------------ catalog
def catalog() -> Dict[str, Any]:
    return {
        "statuses": list(PRD_STATUSES),
        "readiness_states": list(READINESS_STATES),
        "compiler_version": PRD_COMPILER_VERSION,
        "dimensions": [
            {
                "key": dim["key"],
                "label": dim["label"],
                "section_keys": list(dim["section_keys"]),
            }
            for dim in _DIMENSIONS
        ],
    }


# ------------------------------------------------------------------ readiness
def _dimension_detail(
    members: List[Dict[str, Any]], dim_blockers: List[Dict[str, Any]]
) -> str:
    try:
        if dim_blockers:
            return f"{len(dim_blockers)} blocker(s) pending"
        if not members:
            return "No items yet"
        confirmed = sum(
            1 for m in members
            if m.get("status") in ("CONFIRMED_BY_USER", "TECHNICALLY_VERIFIED")
        )
        assumed = sum(1 for m in members if m.get("status") == "ASSUMED_BY_MODEL")
        needs = sum(1 for m in members if m.get("status") == "NEEDS_VERIFICATION")
        parts: List[str] = []
        if confirmed:
            parts.append(f"{confirmed} confirmed")
        if assumed:
            parts.append(f"{assumed} assumption(s)")
        if needs:
            parts.append(f"{needs} need verification")
        return ", ".join(parts) or f"{len(members)} item(s)"
    except Exception:
        return ""


async def get_readiness(
    owner_id: str,
    project_id: str,
    *,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Explicit, per-dimension PRD readiness derived from the graph + ledger.

    Readiness is never a percentage. ``ready`` means: zero open conflicts/open
    questions project-wide AND every dimension has at least a confirmed/rejected
    item or a visible needs-verification item. Cosmetic choices never block.
    """
    graph = await _load_graph(owner_id, project_id, conversation_id)
    nodes = [
        n for n in (graph.get("nodes") or [])
        if isinstance(n, dict) and n.get("decision_id")
    ]

    by_section: Dict[str, List[Dict[str, Any]]] = {}
    nodes_by_decision: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        section = _node_section_key(node)
        by_section.setdefault(section, []).append(node)
        decision_id = node.get("decision_id")
        if decision_id:
            nodes_by_decision[decision_id] = node

    # Blockers are project-wide: an open conflict or an open question anywhere in
    # the project blocks readiness even if it originated in another conversation.
    conflicts = await repo.planning_conflicts.list(
        {"owner_id": owner_id, "project_id": project_id, "status": "open"},
        sort_field="created_at", descending=False, limit=2000,
    )
    questions = await repo.planning_decisions.list(
        {
            "owner_id": owner_id,
            "project_id": project_id,
            "kind": "open_question",
            "status": {"$ne": "SUPERSEDED"},
        },
        sort_field="created_at", descending=False, limit=2000,
    )

    blockers: List[Dict[str, Any]] = []
    for conflict in conflicts:
        section = _blocker_section(conflict, nodes_by_decision)
        blockers.append({
            "id": conflict.get("id"),
            "source": "conflict",
            "topic_key": _redact_str(conflict.get("topic_key") or ""),
            "category": _redact_str(conflict.get("category") or ""),
            "detail": _bounded(
                f"{conflict.get('previous_value') or ''} -> "
                f"{conflict.get('new_value') or ''}", 300,
            ),
            "section_key": _redact_str(section),
            "dimension_key": _dimension_for_section(section),
        })
    for question in questions:
        topic_key = question.get("topic_key") or ""
        category = question.get("category") or ""
        # A cosmetic / low-value / deferred open question is visible work, never
        # a blocker: a favicon, animation or spacing question must not stop PRD
        # generation. Only genuinely high-value unresolved questions block
        # (mirrors planning_state.assess_interview_status).
        if not planning_state._is_high_value_topic_key(topic_key):
            if not isinstance(category, str) or (
                category not in planning_state.HIGH_VALUE_CATEGORIES
            ):
                continue
        section = _blocker_section(question, nodes_by_decision)
        blockers.append({
            "id": question.get("id"),
            "source": "open_question",
            "topic_key": _redact_str(topic_key),
            "category": _redact_str(category),
            "detail": _bounded(question.get("value") or "", 300),
            "section_key": _redact_str(section),
            "dimension_key": _dimension_for_section(section),
        })

    dimensions_payload: List[Dict[str, Any]] = []
    incomplete = 0
    for dim in _DIMENSIONS:
        members: List[Dict[str, Any]] = []
        for section_key in dim["section_keys"]:
            members.extend(by_section.get(section_key) or [])
        dim_blockers = [b for b in blockers if b["dimension_key"] == dim["key"]]

        if dim_blockers:
            state = "BLOCKED"
        elif any(m.get("status") == "NEEDS_VERIFICATION" for m in members):
            state = "NEEDS_VERIFICATION"
        elif any(m.get("status") in _READY_STATUSES for m in members):
            state = "READY"
        else:
            state = "INCOMPLETE"
        if state == "INCOMPLETE":
            incomplete += 1

        dimensions_payload.append({
            "key": dim["key"],
            "label": dim["label"],
            "state": state,
            "detail": _dimension_detail(members, dim_blockers),
            "section_key": dim["section_keys"][0],
            "section_keys": list(dim["section_keys"]),
            "item_count": len(members),
            "blockers": dim_blockers,
        })

    blocker_count = len(blockers)
    ready = blocker_count == 0 and all(
        d["state"] in _READY_DIMENSION_STATES for d in dimensions_payload
    )
    summary = (
        "Ready to generate PRD" if ready
        else f"{blocker_count} blockers / {incomplete} incomplete areas"
    )
    return {
        "project_id": project_id,
        "conversation_id": conversation_id,
        "dimensions": dimensions_payload,
        "blockers": blockers,
        "blocker_count": blocker_count,
        "ready": ready,
        "summary": summary,
    }


# ------------------------------------------------------------------ planning state
def _planning_state_view(state: Dict[str, Any]) -> Dict[str, Any]:
    """Bounded, redacted projection of a ledger state for PRD compilation."""
    state = state if isinstance(state, dict) else {}

    def group(key: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for entry in state.get(key) or []:
            if not isinstance(entry, dict):
                continue
            text = entry.get("value") or entry.get("previous_value") or ""
            out.append({
                "text": _bounded(text, 300),
                "topic_key": _redact_str(entry.get("topic_key") or ""),
                "status": _redact_str(entry.get("status") or ""),
                "decision_id": entry.get("id"),
                "provenance": redaction.redact(entry.get("provenance") or {}),
            })
        return out

    conflicts: List[Dict[str, Any]] = []
    for entry in state.get("conflicts") or []:
        if not isinstance(entry, dict):
            continue
        conflicts.append({
            "text": _bounded(
                f"{entry.get('previous_value') or ''} -> "
                f"{entry.get('new_value') or ''}", 300,
            ),
            "topic_key": _redact_str(entry.get("topic_key") or ""),
            "status": _redact_str(entry.get("status") or ""),
            "decision_id": entry.get("new_item_id"),
            "provenance": {},
        })

    return {
        "confirmed_decisions": group("confirmed_decisions"),
        "rejected_options": group("rejected_options"),
        "assumptions": group("assumptions"),
        "open_questions": group("open_questions"),
        "verification_items": group("verification_items"),
        "constraints": group("constraints"),
        "conflicts": conflicts,
    }


def _group_active(
    active: List[Dict[str, Any]], conflicts: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Group project-wide active ledger items the same way ``get_state`` does."""
    groups: Dict[str, List[Dict[str, Any]]] = {
        key: [] for key in planning_state._GROUP_KEYS
    }
    resolved_topics: set = set()
    for doc in active:
        status = doc.get("status")
        kind = doc.get("kind")
        topic_key = doc.get("topic_key")
        if (status in planning_state.RESOLVED_STATUSES
                and kind != "open_question" and topic_key):
            resolved_topics.add(topic_key)
        if status == "CONFLICTED":
            continue
        pub = planning_state._public_item(doc)
        if kind == "open_question":
            groups["open_questions"].append(pub)
        elif status == "NEEDS_VERIFICATION" and kind in ("open_question", "verification_item"):
            groups["verification_items"].append(pub)
        elif kind == "rejected_option" or status == "REJECTED":
            groups["rejected_options"].append(pub)
        elif kind == "constraint":
            groups["constraints"].append(pub)
        elif kind == "assumption":
            groups["assumptions"].append(pub)
        elif kind == "verification_item":
            groups["verification_items"].append(pub)
        else:
            groups["confirmed_decisions"].append(pub)
    return {
        **groups,
        "conflicts": [planning_state._public_conflict(c) for c in conflicts],
        "resolved_topics": sorted(resolved_topics),
    }


async def _load_planning_state(
    owner_id: str, project_id: str, conversation_id: Optional[str]
) -> Dict[str, Any]:
    if conversation_id:
        try:
            state = await planning_state.get_state(owner_id, project_id, conversation_id)
        except Exception as exc:
            raise PlanningPrdNotFound("Conversation not found") from exc
        return _planning_state_view(state)
    active = await repo.planning_decisions.list(
        {
            "owner_id": owner_id,
            "project_id": project_id,
            "status": {"$ne": "SUPERSEDED"},
        },
        sort_field="created_at", descending=False, limit=2000,
    )
    conflicts = await repo.planning_conflicts.list(
        {"owner_id": owner_id, "project_id": project_id, "status": "open"},
        sort_field="created_at", descending=False, limit=2000,
    )
    return _planning_state_view(_group_active(active, conflicts))


# ------------------------------------------------------------------ compiler
def _planning_state_items(view: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence_by_group = {
        "confirmed_decisions": "CONFIRMED_FACT",
        "rejected_options": "REJECTED",
        "assumptions": "ASSUMPTION",
        "verification_items": "NEEDS_VERIFICATION",
        "conflicts": "NEEDS_VERIFICATION",
        "open_questions": "OPEN_QUESTION",
    }
    items: List[Dict[str, Any]] = []
    for group_key, evidence in evidence_by_group.items():
        for entry in view.get(group_key) or []:
            if not isinstance(entry, dict):
                continue
            text = entry.get("text") or ""
            if not text:
                continue
            topic = entry.get("topic_key") or ""
            items.append({
                "text": f"[{topic}] {text}" if topic else text,
                "status": entry.get("status") or "",
                "evidence_class": evidence,
                "decision_id": entry.get("decision_id"),
                "topic_key": topic,
                "provenance": entry.get("provenance") or {},
            })
    return items


def _section_payload(
    key: str, title: str, items: List[Dict[str, Any]]
) -> Dict[str, Any]:
    counts = {
        "confirmed": 0, "assumed": 0, "needs_verification": 0,
        "rejected": 0, "open_questions": 0,
    }
    for item in items:
        evidence = item.get("evidence_class")
        if evidence == "CONFIRMED_FACT":
            counts["confirmed"] += 1
        elif evidence == "ASSUMPTION":
            counts["assumed"] += 1
        elif evidence == "NEEDS_VERIFICATION":
            counts["needs_verification"] += 1
        elif evidence == "REJECTED":
            counts["rejected"] += 1
        elif evidence == "OPEN_QUESTION":
            counts["open_questions"] += 1
    return {"key": key, "title": title, "counts": counts, "items": items}


def _build_sections(
    graph: Dict[str, Any], view: Dict[str, Any]
) -> List[Dict[str, Any]]:
    nodes = [
        n for n in (graph.get("nodes") or [])
        if isinstance(n, dict)
        and n.get("decision_id")
        and n.get("status") != "SUPERSEDED"
    ]
    sections: List[Dict[str, Any]] = []
    for key, title, selectors in _PRD_SECTIONS:
        items: List[Dict[str, Any]] = []
        for section_key, allowed_types in selectors:
            for node in nodes:
                if _node_section_key(node) != section_key:
                    continue
                if allowed_types is not None and node.get("type") not in allowed_types:
                    continue
                items.append(_prd_item(node))
        if not items:
            continue
        sections.append(_section_payload(key, title, items))
    # Planning State is always included, even when every group is empty.
    sections.append(_section_payload(
        _PLANNING_STATE_KEY, _PLANNING_STATE_TITLE, _planning_state_items(view)
    ))
    return sections


def _render_item_line(item: Dict[str, Any]) -> str:
    prefix = _MARKDOWN_PREFIX.get(item.get("evidence_class") or "", "")
    return f"{prefix}{_redact_str(item.get('text') or '')}"


def _markdown(
    title: str, sections: List[Dict[str, Any]], view: Dict[str, Any]
) -> str:
    lines: List[str] = [f"# {_redact_str(title)}", ""]
    for section in sections:
        if section.get("key") == _PLANNING_STATE_KEY:
            continue
        items = section.get("items") or []
        if not items:
            continue
        lines.append(f"## {_redact_str(section.get('title') or '')}")
        for item in items:
            lines.append(f"- {_render_item_line(item)}")
        lines.append("")

    lines.append(f"## {_PLANNING_STATE_TITLE}")
    rendered_any = False
    for group_key, label in _PLANNING_STATE_GROUPS:
        entries = [
            e for e in (view.get(group_key) or [])
            if isinstance(e, dict) and (e.get("text") or "")
        ]
        if not entries:
            continue
        rendered_any = True
        lines.append(f"### {label}")
        for entry in entries:
            topic = entry.get("topic_key") or ""
            text = entry.get("text") or ""
            body = f"[{topic}] {text}" if topic else text
            lines.append(f"- {_redact_str(body)}")
        lines.append("")
    if not rendered_any:
        lines.append("- (none)")
    return redaction.redact_text("\n".join(lines).rstrip() + "\n")


def _totals(nodes: List[Dict[str, Any]], view: Dict[str, Any]) -> Dict[str, int]:
    counts = {
        "confirmed": 0, "assumed": 0, "needs_verification": 0,
        "open_questions": 0, "conflicts": 0,
    }
    seen: set = set()
    for node in nodes:
        decision_id = node.get("decision_id")
        if not decision_id or decision_id in seen:
            continue
        seen.add(decision_id)
        evidence = _evidence_class(node)
        if evidence == "CONFIRMED_FACT":
            counts["confirmed"] += 1
        elif evidence == "ASSUMPTION":
            counts["assumed"] += 1
        elif evidence == "NEEDS_VERIFICATION":
            counts["needs_verification"] += 1
        elif evidence == "OPEN_QUESTION":
            counts["open_questions"] += 1
    counts["conflicts"] = len(
        [c for c in (view.get("conflicts") or []) if isinstance(c, dict)]
    )
    return counts


def _source_revision(
    nodes: List[Dict[str, Any]], view: Dict[str, Any]
) -> Dict[str, Any]:
    decision_ids = sorted({
        n.get("decision_id") for n in nodes if n.get("decision_id")
    })
    checksum_material = [
        {
            "node_key": n.get("node_key"),
            "status": n.get("status"),
            "fingerprint": n.get("decision_fingerprint") or "",
        }
        for n in sorted(nodes, key=lambda x: x.get("node_key") or "")
    ]
    return {
        "decision_ids": decision_ids,
        "decision_count": len(decision_ids),
        "graph_checksum": canonical_hash(checksum_material),
        "planning_state_hash": canonical_hash(view),
    }


async def compile_prd(
    owner_id: str, project_id: str, conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """Compile + persist a new DRAFT PRD version from graph + ledger.

    Deterministic and LLM-free. The compiler only READS the ledger; it never
    writes, supersedes or resolves a decision.
    """
    project = await _require_project(owner_id, project_id)
    graph = await _load_graph(owner_id, project_id, conversation_id)
    view = await _load_planning_state(owner_id, project_id, conversation_id)
    sections = _build_sections(graph, view)

    decision_nodes = [
        n for n in (graph.get("nodes") or [])
        if isinstance(n, dict) and n.get("decision_id") and n.get("status") != "SUPERSEDED"
    ]
    counts = _totals(decision_nodes, view)

    latest = await _latest_version(owner_id, project_id)
    version = (int(latest.get("version") or 0) + 1) if latest else 1

    project_name = _bounded(project.get("name") or "Product", 120)
    title = _bounded(f"{project_name} - Living PRD v{version}", _TITLE_CAP)
    source_revision = _source_revision(decision_nodes, view)
    content = {"sections": sections, "planning_state": view}
    content_hash = canonical_hash({
        "sections": sections,
        "planning_state": view,
        "source_revision": source_revision,
    })
    markdown = _markdown(title, sections, view)

    now = now_iso()
    doc: Dict[str, Any] = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "version": version,
        "status": "DRAFT",
        "title": title,
        "markdown": markdown,
        "content": content,
        "source_revision": source_revision,
        "section_count": len(sections),
        "counts": counts,
        "compiler_version": PRD_COMPILER_VERSION,
        "content_hash": content_hash,
        "created_by": owner_id,
        "created_at": now,
        "updated_at": now,
        "approved_at": None,
        "superseded_at": None,
    }
    await repo.planning_prds.insert(doc)
    return _public_version(doc, include_content=True)


# ------------------------------------------------------------------ public views
def _public_version(doc: Optional[Dict[str, Any]], *, include_content: bool = True) -> Dict[str, Any]:
    doc = doc or {}
    out: Dict[str, Any] = {
        "id": doc.get("id"),
        "project_id": _redact_str(doc.get("project_id")),
        "conversation_id": _redact_str(doc.get("conversation_id")),
        "version": doc.get("version"),
        "status": _redact_str(doc.get("status") or ""),
        "title": _redact_str(doc.get("title") or ""),
        "section_count": doc.get("section_count") or 0,
        "counts": redaction.redact(doc.get("counts") or {}),
        "source_revision": redaction.redact(doc.get("source_revision") or {}),
        "compiler_version": doc.get("compiler_version"),
        "content_hash": doc.get("content_hash"),
        "created_by": _redact_str(doc.get("created_by")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "approved_at": doc.get("approved_at"),
        "superseded_at": doc.get("superseded_at"),
    }
    if include_content:
        out["markdown"] = _redact_str(doc.get("markdown") or "")
        out["content"] = redaction.redact(doc.get("content") or {})
    return out


def _summary(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Lightweight list entry: never carries the heavy content/markdown."""
    doc = doc or {}
    return {
        "id": doc.get("id"),
        "project_id": _redact_str(doc.get("project_id")),
        "conversation_id": _redact_str(doc.get("conversation_id")),
        "version": doc.get("version"),
        "status": _redact_str(doc.get("status") or ""),
        "title": _redact_str(doc.get("title") or ""),
        "section_count": doc.get("section_count") or 0,
        "counts": redaction.redact(doc.get("counts") or {}),
        "source_revision": redaction.redact(doc.get("source_revision") or {}),
        "compiler_version": doc.get("compiler_version"),
        "content_hash": doc.get("content_hash"),
        "created_by": _redact_str(doc.get("created_by")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "approved_at": doc.get("approved_at"),
        "superseded_at": doc.get("superseded_at"),
    }


async def list_prd_versions(owner_id: str, project_id: str) -> Dict[str, Any]:
    await _require_project(owner_id, project_id)
    docs = await repo.planning_prds.list(
        {"owner_id": owner_id, "project_id": project_id},
        sort_field="version", descending=True, limit=1000,
    )
    return {
        "versions": [_summary(doc) for doc in docs],
        "latest_version": (docs[0].get("version") if docs else None),
    }


async def get_prd_version(
    owner_id: str, version_id: str, *, project_id: Optional[str] = None
) -> Dict[str, Any]:
    """Owner + (optional) project scoped PRD version detail."""
    doc = await _require_version(owner_id, version_id, project_id=project_id)
    return _public_version(doc, include_content=True)


# ------------------------------------------------------------------ compare
def _diff_lines(from_lines: List[str], to_lines: List[str]) -> List[Dict[str, Any]]:
    """Line-level diff copied from the Prompt Registry compare helper."""
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


def _section_map(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    content = doc.get("content") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for section in content.get("sections") or []:
        if isinstance(section, dict) and section.get("key"):
            out[section["key"]] = section
    return out


def _section_texts(section: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(section, dict):
        return []
    return [
        str(item.get("text") or "")
        for item in (section.get("items") or [])
        if isinstance(item, dict)
    ]


def _section_signature(section: Optional[Dict[str, Any]]) -> List[Tuple[str, str, str]]:
    if not isinstance(section, dict):
        return []
    return [
        (
            str(item.get("text") or ""),
            str(item.get("status") or ""),
            str(item.get("evidence_class") or ""),
        )
        for item in (section.get("items") or [])
        if isinstance(item, dict)
    ]


def _fields_diff(
    frm: Optional[Dict[str, Any]], to: Optional[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    from_counts = (frm or {}).get("counts") or {}
    to_counts = (to or {}).get("counts") or {}
    from_statuses = _section_statuses(frm)
    to_statuses = _section_statuses(to)
    return [
        {
            "field": "counts",
            "changed": from_counts != to_counts,
            "from": from_counts,
            "to": to_counts,
        },
        {
            "field": "statuses",
            "changed": from_statuses != to_statuses,
            "from": from_statuses,
            "to": to_statuses,
        },
    ]


def _section_statuses(section: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(section, dict):
        return []
    return [
        str(item.get("status") or "")
        for item in (section.get("items") or [])
        if isinstance(item, dict)
    ]


async def compare_prd_versions(
    owner_id: str,
    project_id: str,
    from_version_id: str,
    to_version_id: str,
) -> Dict[str, Any]:
    """Owner+project scoped section-level diff between two PRD versions."""
    await _require_project(owner_id, project_id)
    frm = await _require_version(owner_id, from_version_id)
    to = await _require_version(owner_id, to_version_id)
    if frm.get("project_id") != project_id or to.get("project_id") != project_id:
        raise PlanningPrdNotFound("PRD version not found")

    frm_sections = _section_map(frm)
    to_sections = _section_map(to)
    ordered = list(frm_sections)
    ordered += [key for key in to_sections if key not in frm_sections]

    sections_out: List[Dict[str, Any]] = []
    affected: List[Dict[str, Any]] = []
    summary = {
        "sections_added": 0, "sections_removed": 0, "sections_changed": 0,
        "lines_added": 0, "lines_removed": 0,
    }
    for key in ordered:
        frm_section = frm_sections.get(key)
        to_section = to_sections.get(key)
        if frm_section is None:
            change = "added"
            from_texts: List[str] = []
            to_texts = _section_texts(to_section)
        elif to_section is None:
            change = "removed"
            from_texts = _section_texts(frm_section)
            to_texts = []
        else:
            from_texts = _section_texts(frm_section)
            to_texts = _section_texts(to_section)
            change = (
                "changed"
                if _section_signature(frm_section) != _section_signature(to_section)
                else "unchanged"
            )

        title = (to_section or frm_section or {}).get("title") or ""
        entry: Dict[str, Any] = {
            "key": key,
            "title": _redact_str(title),
            "change": change,
            "from_count": len(_section_texts(frm_section)),
            "to_count": len(_section_texts(to_section)),
            "line_diff": [],
            "fields": [],
        }
        if change != "unchanged":
            entry["line_diff"] = _diff_lines(from_texts, to_texts)
            entry["fields"] = _fields_diff(frm_section, to_section)
            summary["lines_added"] += sum(
                1 for d in entry["line_diff"] if d["op"] == "insert"
            )
            summary["lines_removed"] += sum(
                1 for d in entry["line_diff"] if d["op"] == "delete"
            )
            if change == "added":
                summary["sections_added"] += 1
            elif change == "removed":
                summary["sections_removed"] += 1
            else:
                summary["sections_changed"] += 1
            affected.append({"key": key, "title": entry["title"], "change": change})
        sections_out.append(entry)

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
        "sections": sections_out,
        "summary": summary,
        "affected_sections": affected,
    }


# ------------------------------------------------------------------ approval
async def approve_prd_version(
    owner_id: str, version_id: str, *, project_id: Optional[str] = None
) -> Dict[str, Any]:
    """Approve one PRD version, superseding any prior APPROVED version.

    When ``project_id`` is supplied the version must belong to that project,
    else the request resolves to not-found before any mutation: a same-owner
    caller cannot approve a version id from another one of their projects.

    History is retained: a superseded version keeps its row and only changes
    status. APPROVED PRD != CERTIFIED asset (see the module docstring): this is
    a document review workflow with no certification coupling.
    """
    doc = await _require_version(owner_id, version_id, project_id=project_id)
    project_id = doc.get("project_id")
    now = now_iso()

    previously_approved = await repo.planning_prds.list(
        {"owner_id": owner_id, "project_id": project_id, "status": "APPROVED"},
        sort_field="version", descending=True, limit=1000,
    )
    for previous in previously_approved:
        if previous.get("id") == version_id:
            continue
        await repo.planning_prds.update(previous["id"], owner_id, {
            "status": "SUPERSEDED",
            "superseded_at": now,
            "updated_at": now,
        })

    updated = await repo.planning_prds.update(version_id, owner_id, {
        "status": "APPROVED",
        "approved_at": now,
        "superseded_at": None,
        "updated_at": now,
    })
    return _public_version(updated or doc, include_content=True)
