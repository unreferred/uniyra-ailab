"""V6.14 Workspace modes: Chat / Plan / Research / Audit.

Modes are composition presets layered over the EXISTING Workspace execution
path. They never create a separate runtime, store or agent: each mode only
augments the effective system prompt with a bounded, deterministic scaffold
before the canonical ``execute_model`` / ``stream_execution`` funnel runs.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..models import WORKSPACE_MODES
from . import redaction

DEFAULT_MODE = "chat"

# Deterministic, self-contained scaffolds. No caller input is interpolated.
_MODE_SCAFFOLDS: Dict[str, str] = {
    "chat": "",
    "plan": (
        "WORKSPACE MODE: PLAN\n"
        "Plan incrementally; never regenerate the whole plan on every turn.\n"
        "Reply with only the sections that changed, using this shape:\n"
        "- Updated decisions: new or changed confirmed decisions.\n"
        "- New constraints: newly established hard constraints.\n"
        "- Plan changes: how the incremental change affects the plan.\n"
        "- Remaining open questions: only genuinely unresolved, high-value questions.\n"
        "Ask at most 3 high-value questions per turn, and only when the answer materially affects product behavior, architecture, security, permissions/authorization, data ownership or model, workflows, integrations, compliance, cost, an irreversible choice, an actual contradiction, or a missing requirement that blocks a coherent PRD.\n"
        "For every non-blocking or cosmetic matter (colors, fonts, icons, animations, spacing, theming) choose a sensible default and record it as an assumption (ASSUMED_BY_MODEL) instead of asking.\n"
        "STOP interviewing when the decision state has no unresolved core topics and no blockers, and say the plan is ready to compile into a PRD.\n"
        "Never ask about a topic that is already decided or rejected in the WORKSPACE DECISION STATE block below.\n"
        "Never present an assumption, an inferred preference, or an unverified technical claim as a confirmed fact; label it as an assumption or a needs-verification item instead.\n"
        "Produce a full consolidated plan only when the user explicitly asks for a consolidated plan."
    ),
    "research": (
        "WORKSPACE MODE: RESEARCH\n"
        "Investigate before concluding. Separate observed evidence from "
        "inference, and cite the concrete source (file path, document, run id) "
        "for every claim. When evidence is missing or ambiguous, say so "
        "explicitly rather than fabricating a confident answer. Finish with a "
        "short list of open questions and the next concrete investigation step."
    ),
    "audit": (
        "WORKSPACE MODE: AUDIT\n"
        "Review the provided material adversarially for correctness, security "
        "and regression risk. Report findings as concrete items, each with: "
        "severity, the exact location or evidence, why it is a problem, and the "
        "recommended fix. Explicitly flag ownership/isolation, secret handling, "
        "and false-pass risks. Never report success for behavior you did not "
        "verify; state what you could not check."
    ),
}

_MODE_CATALOG = (
    {"key": "chat", "label": "Chat", "description": "Freeform conversation; no added scaffold."},
    {"key": "plan", "label": "Plan", "description": "Structured planning only; no execution."},
    {"key": "research", "label": "Research", "description": "Evidence-first investigation with explicit uncertainty."},
    {"key": "audit", "label": "Audit", "description": "Adversarial review with severity-tagged findings."},
)

# Decision-state rendering bounds (deterministic).
_DECISION_VALUE_CAP = 300
_DECISION_ENTRIES_CAP = 40
_DECISION_TOPIC_CAP = 120
_DECISION_RESOLVED_CAP = 40
_INTERVIEW_TOPIC_CAP = 12


def catalog() -> Dict[str, Any]:
    """Static catalog of Workspace modes (mirrors the canonical enum)."""
    return {"modes": [dict(entry) for entry in _MODE_CATALOG], "default": DEFAULT_MODE}


def validate_mode(mode: Any) -> str:
    """Return a valid mode; empty/None -> default; anything else raises ValueError."""
    if mode is None or mode == "":
        return DEFAULT_MODE
    if mode not in WORKSPACE_MODES:
        raise ValueError("Invalid workspace mode")
    return mode


def _safe_text(value: Any, cap: int = _DECISION_VALUE_CAP) -> str:
    """Redact and bound a scalar for the decision block. Never raises."""
    try:
        if value is None:
            return ""
        text = redaction.redact_text(str(value)).strip()
        return text[:cap]
    except Exception:
        return ""


def _item_value(item: Any) -> str:
    """Safely read an item's ``value``. Never raises."""
    try:
        return _safe_text(item.get("value")) if isinstance(item, dict) else ""
    except Exception:
        return ""


def _item_topic(item: Any) -> str:
    """Safely read an item's ``topic_key``. Never raises."""
    try:
        return _safe_text(item.get("topic_key"), _DECISION_TOPIC_CAP) if isinstance(item, dict) else ""
    except Exception:
        return ""


def _render_items(title: str, items: Any) -> List[str]:
    lines: List[str] = []
    for item in items or []:
        value = _item_value(item)
        if not value:
            continue
        lines.append(f"- [{_item_topic(item)}] {value}")
        if len(lines) >= _DECISION_ENTRIES_CAP:
            break
    if not lines:
        return []
    return [title, *lines]


def _render_conflicts(items: Any) -> List[str]:
    lines: List[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        previous = _safe_text(item.get("previous_value"))
        new = _safe_text(item.get("new_value"))
        if not previous and not new:
            continue
        lines.append(f"- [{_item_topic(item)}] {previous} -> {new}")
        if len(lines) >= _DECISION_ENTRIES_CAP:
            break
    if not lines:
        return []
    return ["Open conflicts (surface these; do not silently resolve):", *lines]


def _render_interview_status(state: Dict[str, Any]) -> str:
    """Bounded, redacted interview-status line derived from the ledger.

    Uses the deterministic :func:`app.services.planning_state.assess_interview_status`
    so the PLAN prompt can tell whether interviewing may stop. Never raises.
    """
    try:
        from .planning_state import assess_interview_status
        status = assess_interview_status(state)
    except Exception:
        return ""
    try:
        bits = [
            "stopped" if status.get("stopped") else "interviewing",
            f"prd_ready={'yes' if status.get('prd_ready') else 'no'}",
        ]
        unresolved = status.get("unresolved_core_topics") or []
        if unresolved:
            bits.append(
                "unresolved core topics: "
                + ", ".join(str(topic) for topic in unresolved[:_INTERVIEW_TOPIC_CAP])
            )
        blockers = status.get("blocking") or []
        if blockers:
            bits.append(f"blockers: {len(blockers)}")
        reason = _safe_text(status.get("reason"))
        if reason:
            bits.append(reason)
        return redaction.redact_text("INTERVIEW STATUS: " + " | ".join(bits))
    except Exception:
        return ""


def render_decision_state_block(state: Dict[str, Any]) -> str:
    """Render the authoritative decision ledger as a bounded prompt block.

    Deterministic and never raises. Only non-empty groups are rendered; an
    empty/absent state yields ``""`` so callers can append it unconditionally.
    """
    try:
        if not isinstance(state, dict):
            state = {}
        sections: List[str] = []
        sections.extend(_render_items(
            "Confirmed decisions:", state.get("confirmed_decisions")))
        sections.extend(_render_items(
            "Rejected options (never propose or ask about these again):",
            state.get("rejected_options")))
        sections.extend(_render_items(
            "Open questions (unresolved):", state.get("open_questions")))
        sections.extend(_render_items(
            "Assumptions (NOT facts; confirm before relying on them):",
            state.get("assumptions")))
        sections.extend(_render_items(
            "Needs verification:", state.get("verification_items")))
        sections.extend(_render_conflicts(state.get("conflicts")))

        resolved_topics = state.get("resolved_topics") or []
        resolved_keys = sorted({
            key for key in (_safe_text(topic, _DECISION_TOPIC_CAP)
                            for topic in resolved_topics) if key
        })
        if resolved_keys:
            sections.append(
                "Already resolved topics \u2014 do NOT ask about these again: "
                + ", ".join(resolved_keys[:_DECISION_RESOLVED_CAP])
            )

        if not sections:
            return ""
        status_line = _render_interview_status(state)
        if status_line:
            sections.append(status_line)
        header = "WORKSPACE DECISION STATE (authoritative, do not contradict)"
        return "\n".join([header, *sections])
    except Exception:
        return ""


_READINESS_DIMENSION_CAP = 12


def render_readiness_block(readiness: Dict[str, Any]) -> str:
    """Render PRD readiness as a bounded, redacted prompt block.

    Deterministic and never raises. Only explicit per-dimension states are
    surfaced (no fabricated percentage); an empty/absent payload yields ``""``
    so callers can append it unconditionally.
    """
    try:
        if not isinstance(readiness, dict):
            return ""
        dimensions = readiness.get("dimensions") or []
        lines: List[str] = []
        for dimension in dimensions:
            if not isinstance(dimension, dict):
                continue
            key = _safe_text(dimension.get("key"), _DECISION_TOPIC_CAP)
            state = _safe_text(dimension.get("state"), 40)
            if not key or not state:
                continue
            detail = _safe_text(dimension.get("detail"))
            suffix = f" ({detail})" if detail else ""
            lines.append(f"- {key}: {state}{suffix}")
            if len(lines) >= _READINESS_DIMENSION_CAP:
                break
        if not lines:
            return ""
        summary = _safe_text(readiness.get("summary"))
        blocker_count = readiness.get("blocker_count")
        header = (
            "PRD READINESS (derived; decisions above remain authoritative, "
            "never present an assumption or unverified claim as confirmed)"
        )
        tail = [f"Ready to generate: {'yes' if readiness.get('ready') else 'no'}"]
        if blocker_count is not None:
            tail.append(f"blockers: {blocker_count}")
        if summary:
            tail.append(summary)
        return redaction.redact_text("\n".join([header, *lines, " | ".join(tail)]))
    except Exception:
        return ""


def compose_system_prompt(
    mode: Any, system_prompt: Any, decision_block: str = ""
) -> str:
    """Return the effective system prompt for a mode.

    CHAT returns the base prompt unchanged. Other modes append their scaffold
    after a blank line so the caller's prompt always takes precedence. PLAN mode
    additionally appends the authoritative decision-state block, when present.
    """
    resolved = validate_mode(mode)
    scaffold = _MODE_SCAFFOLDS.get(resolved) or ""
    base = (system_prompt or "").rstrip()
    if not scaffold:
        return base
    if not base:
        composed = scaffold
    else:
        composed = f"{base}\n\n{scaffold}"
    block = (decision_block or "").strip() if isinstance(decision_block, str) else ""
    if resolved == "plan" and block:
        composed = f"{composed}\n\n{block}"
    return composed
