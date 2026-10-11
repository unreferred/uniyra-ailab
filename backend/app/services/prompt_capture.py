"""V6.14 Workspace Prompt Capture / Composer service.

A capture promotes an existing Workspace conversation (or a single assistant
response) into a first-class Prompt Draft. The service is a deterministic,
LLM-free composer: it extracts a bounded structured spec from conversation text
with keyword heuristics, redacts secret-shaped material, links the result to the
project's existing Prompt Lab assets, and writes the outcome through the EXISTING
prompt store (``repo.prompts`` / ``repo.prompt_versions``) via
:mod:`app.services.prompt_registry`. It never creates a parallel prompt, block,
policy or invariant store.

Security properties
-------------------
* **Redaction everywhere.** Every captured string is passed through
  :mod:`app.services.redaction` before it is returned or persisted, and the
  extracted structure is deep-redacted so no secret survives in any field.
* **Untrusted input stays untrusted.** Conversation content is never silently
  promoted to a trusted policy or invariant. Policy/invariant linking comes ONLY
  from project assets the caller explicitly selected (``include_*_ids``), never
  from auto-creation. Blocks may be auto-mapped to EXISTING equivalent blocks;
  new blocks are created only when ``create_missing_blocks`` is explicitly true
  and only after an equivalence re-check, so duplicates cannot be fabricated.
* **Template safety.** Literal ``{{`` / ``}}`` in captured text is split so it
  can never be interpreted as an invalid compiler template expression.
* **Owner/project isolation.** Every read is owner-scoped; a foreign resource
  resolves to not-found and cross-project references are rejected.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import (
    CAPTURE_KINDS,
    CAPTURE_MODES,
    PROMPT_COMPILER_MODES,
    PROMPT_COMPILER_TARGETS,
    new_id,
    now_iso,
)
from . import planning_state, prompt_assets, prompt_compiler, prompt_registry, redaction
from .prompt_assets import PromptAssetError, PromptAssetNotFound
from .prompt_compiler import PromptCompilerError
from .prompt_registry import PromptRegistryError, PromptRegistryNotFound


class PromptCaptureError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class PromptCaptureNotFound(PromptCaptureError):
    """Owner-scoped resource missing (HTTP 404)."""


# ------------------------------------------------------------------ constants
_SECTION_KEYS = (
    "role", "objective", "scope", "constraints", "architecture_rules",
    "design_rules", "security_rules", "phases", "validation", "expected_output",
)

# Normalized heading text -> canonical section key.
_HEADING_ALIASES: Dict[str, str] = {
    "role": "role",
    "persona": "role",
    "objective": "objective",
    "goal": "objective",
    "task": "objective",
    "mission": "objective",
    "aim": "objective",
    "scope": "scope",
    "in scope": "scope",
    "out of scope": "scope",
    "boundaries": "scope",
    "constraints": "constraints",
    "constraint": "constraints",
    "requirements": "constraints",
    "rules": "constraints",
    "architecture": "architecture_rules",
    "architecture rules": "architecture_rules",
    "architectural rules": "architecture_rules",
    "design": "design_rules",
    "design rules": "design_rules",
    "ui rules": "design_rules",
    "security": "security_rules",
    "security rules": "security_rules",
    "phases": "phases",
    "execution phases": "phases",
    "steps": "phases",
    "plan": "phases",
    "approach": "phases",
    "validation": "validation",
    "verification": "validation",
    "tests": "validation",
    "testing": "validation",
    "expected output": "expected_output",
    "output": "expected_output",
    "deliverable": "expected_output",
    "deliverables": "expected_output",
    "report": "expected_output",
    "definition of done": "expected_output",
}

_CHATTER = (
    "thanks", "thank you", "sure", "great", "got it", "let me know", "i hope",
    "hope this helps", "as an ai", "here is", "here's", "of course",
    "happy to help", "feel free",
)

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_WS_RE = re.compile(r"\s+")


# ------------------------------------------------------------------ helpers
def _strip_code_fences(text: Any) -> str:
    """Remove ```...``` fenced blocks; never raises on odd input."""
    if not isinstance(text, str):
        return ""
    try:
        return _FENCE_RE.sub("", text)
    except Exception:
        return text


def _normalize_heading(text: Any) -> str:
    """Lowercase a heading and strip markdown markers / numbering / trailing colon."""
    if not isinstance(text, str):
        return ""
    t = text.strip()
    t = re.sub(r"^[#*>\s]+", "", t)
    t = re.sub(r"^\d+[.)]\s*", "", t)
    t = t.strip()
    t = t.strip("*`").strip()
    t = t.rstrip(":").strip()
    t = t.strip("*`").strip()
    return _WS_RE.sub(" ", t).lower()


def _clean_line(line: Any) -> str:
    """Strip bullets, blockquote markers, numbering and emphasis from a line."""
    if not isinstance(line, str):
        return ""
    t = line.strip()
    t = re.sub(r"^>+\s*", "", t)
    t = re.sub(r"^[-*\u2022]\s+", "", t)
    t = re.sub(r"^\d+[.)]\s+", "", t)
    t = t.strip()
    t = t.strip("`").strip()
    t = t.strip("*").strip()
    t = t.rstrip(":").strip()
    return _WS_RE.sub(" ", t)


def _is_chatter(line: Any) -> bool:
    """True for empty, too-short, punctuation-only or clearly conversational lines."""
    text = _clean_line(line)
    if not text:
        return True
    if len(text) < 3:
        return True
    if not re.search(r"[A-Za-z0-9]", text):
        return True
    low = text.lower().strip(" .!?,;:")
    for filler in _CHATTER:
        if low == filler:
            return True
        if low.startswith(filler + " ") and len(low) <= len(filler) + 20:
            return True
    return False


def _hit_any(text: str, keywords: Tuple[str, ...]) -> bool:
    """Prefix/word-boundary keyword match (conservative; avoids substring noise)."""
    for keyword in keywords:
        if " " in keyword:
            if keyword in text:
                return True
        elif keyword == "ui":
            if re.search(r"\bui\b", text):
                return True
        else:
            if re.search(r"\b" + re.escape(keyword), text):
                return True
    return False


def _classify_line(line: Any) -> Optional[str]:
    """Classify a stray line into a section key, else None. Order is normative."""
    if _is_chatter(line):
        return None
    text = _clean_line(line)
    if not text:
        return None
    low = text.lower()

    if _hit_any(low, ("secret", "credential", "password", "auth", "ownership",
                       "isolat", "injection")):
        return "security_rules"
    if _hit_any(low, ("architecture", "service", "router", "repository",
                       "collection", "module", "schema", "migration",
                       "reuse existing")):
        return "architecture_rules"
    if _hit_any(low, ("design", "ui", "interface", "component", "style", "pattern")):
        return "design_rules"
    if _hit_any(low, ("test", "validate", "verify", "regression", "assert")):
        return "validation"
    if _hit_any(low, ("must", "should", "do not", "don't", "never", "only",
                       "constraint", "bounded", "avoid", "require")):
        return "constraints"
    if re.match(r"^\d+[.)]\s", line.strip() if isinstance(line, str) else ""):
        return "phases"
    if _hit_any(low, ("inspect", "implement", "create", "add", "update", "run",
                       "report", "then")):
        return "phases"
    if _hit_any(low, ("output", "deliverable", "report", "format", "return",
                       "produce")):
        return "expected_output"
    if _hit_any(low, ("scope", "out of scope", "in scope", "only the", "beyond")):
        return "scope"
    if _hit_any(low, ("goal", "objective", "task", "purpose")):
        return "objective"
    return None


def _dedupe_cap(items: List[str]) -> List[str]:
    """Ordered case-insensitive de-dupe, truncating items to 500 chars (cap 50)."""
    seen: set = set()
    out: List[str] = []
    for item in items:
        text = (item or "").strip()
        if not text:
            continue
        text = text[:500]
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= 50:
            break
    return out


def _first_string(items: List[str]) -> str:
    for item in items:
        text = (item or "").strip()
        if text:
            return text
    return ""


def extract_structure(text: Any) -> Dict[str, Any]:
    """Deterministic structure extraction from conversation text (never raises)."""
    result: Dict[str, Any] = {
        key: ("" if key in ("role", "objective") else []) for key in _SECTION_KEYS
    }
    if not isinstance(text, str) or not text.strip():
        return result

    stripped = _strip_code_fences(text)
    heading_content: Dict[str, List[str]] = {key: [] for key in _SECTION_KEYS}
    pending: List[str] = []
    current: Optional[str] = None

    for raw in stripped.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        key = _HEADING_ALIASES.get(_normalize_heading(line))
        if key:
            current = key
            continue
        if ":" in line:
            label, _, rest = line.partition(":")
            label_key = _HEADING_ALIASES.get(_normalize_heading(label))
            if label_key:
                current = label_key
                rest_clean = _clean_line(rest)
                if rest_clean and not _is_chatter(rest):
                    heading_content[label_key].append(rest_clean)
                continue
        if current is not None:
            clean = _clean_line(line)
            if clean and not _is_chatter(line):
                heading_content[current].append(clean)
        else:
            pending.append(line)

    # PASS 2: classify lines that never fell under a recognized heading.
    classified: Dict[str, List[str]] = {key: [] for key in _SECTION_KEYS}
    first_primary = ""
    for raw in pending:
        if _is_chatter(raw):
            continue
        clean = _clean_line(raw)
        if not clean:
            continue
        if not first_primary:
            first_primary = clean
        key = _classify_line(raw)
        if key:
            classified[key].append(clean)

    result["role"] = _first_string(heading_content["role"])
    objective = _first_string(heading_content["objective"])
    if not objective:
        objective = _first_string(classified["objective"])
    if not objective:
        objective = first_primary
    result["objective"] = objective

    for key in _SECTION_KEYS:
        if key in ("role", "objective"):
            continue
        result[key] = _dedupe_cap(heading_content[key] + classified[key])
    return result


_CONSTRAINT_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("inspect_existing_architecture", (
        "inspect", "read the existing", "understand the existing",
        "locate the", "before writing", "before changing", "read before",
    )),
    ("preserve_security", (
        "secret", "credential", "password", "auth", "ownership", "isolat",
        "injection", "permission",
    )),
    ("no_false_pass", (
        "false pass", "report success", "unverified", "skipped",
        "not performed", "stub", "without evidence",
    )),
    ("tests_required", ("test", "regression", "coverage", "assert")),
    ("migration_safety", (
        "migration", "migrate", "index", "backward compat", "schema change",
        "additive",
    )),
    ("reuse_existing_services", (
        "reuse", "existing service", "helper", "duplicate", "extend existing",
    )),
    ("no_parallel_system", (
        "parallel", "competing", "fork", "separate store", "parallel system",
    )),
    ("report_changes", ("report", "files changed", "deviation", "summarize")),
    ("bounded_scope", (
        "bounded", "scope", "only", "do not refactor", "opportunistic",
        "limited", "no extra",
    )),
)


def _constraint_category(text: Any) -> Optional[str]:
    """Map a constraint/rule line to a canonical Prompt Block category key."""
    if _is_chatter(text):
        return None
    clean = _clean_line(text).lower()
    if not clean:
        return None
    for category, keywords in _CONSTRAINT_RULES:
        if _hit_any(clean, keywords):
            return category
    if _hit_any(clean, ("must", "should", "never", "only", "avoid", "require")):
        return "other"
    return None


_SECTION_TITLES: Tuple[Tuple[str, str], ...] = (
    ("role", "## Role"),
    ("objective", "## Objective"),
    ("scope", "## Scope"),
    ("constraints", "## Constraints"),
    ("architecture_rules", "## Architecture Rules"),
    ("design_rules", "## Design Rules"),
    ("security_rules", "## Security Rules"),
    ("phases", "## Execution Phases"),
    ("validation", "## Validation"),
    ("expected_output", "## Expected Output"),
)


def compose_content(structure: Dict[str, Any]) -> str:
    """Deterministic markdown composition over the non-empty structured sections."""
    blocks: List[str] = []
    for key, title in _SECTION_TITLES:
        value = (structure or {}).get(key)
        if key in ("role", "objective"):
            text = (value or "").strip() if isinstance(value, str) else ""
            if text:
                blocks.append(f"{title}\n{text}")
            continue
        items = [item for item in (value or []) if (item or "").strip()]
        if items:
            body = "\n".join(f"- {item.strip()}" for item in items)
            blocks.append(f"{title}\n{body}")
    return "\n\n".join(blocks)


def _sanitize_template_braces(text: Any) -> str:
    """Split literal ``{{`` / ``}}`` so captured text is never a template expression."""
    if not isinstance(text, str):
        return ""
    return text.replace("{{", "{ {").replace("}}", "} }")


def _merge_unique(first: Any, second: Any) -> List[str]:
    """Ordered, case-insensitive union of two string lists (dedupes, keeps order)."""
    seen: set = set()
    out: List[str] = []
    for item in list(first or []) + list(second or []):
        text = item.strip() if isinstance(item, str) else ""
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _decision_markdown(fields: Dict[str, Any]) -> str:
    """Deterministic decision-ledger markdown appended to captured content.

    Assumptions, open questions and verification items are rendered in their own
    sections and are never presented as confirmed facts.
    """
    fields = fields or {}
    sections: List[str] = []
    for key, title in (
        ("confirmed_decisions", "## Confirmed Decisions"),
        ("rejected_options",
         "## Rejected Options (explicit negative constraints; NOT active requirements)"),
        ("assumptions", "## Assumptions (not facts; confirm before relying on them)"),
        ("open_questions", "## Open Questions"),
        ("verification_items", "## Needs Verification"),
    ):
        items = [
            str(item).strip() for item in (fields.get(key) or []) if str(item).strip()
        ]
        if items:
            body = "\n".join(f"- {item}" for item in items)
            sections.append(f"{title}\n{body}")
    return "\n\n".join(sections)


def _derive_block_name(line: str) -> str:
    """Derive a bounded (<=80 char) block name from a constraint line."""
    clean = _clean_line(line)
    clean = clean.strip(" .!?,;:")
    if len(clean) > 80:
        clean = clean[:77].rstrip() + "..."
    return clean


async def _link_assets(
    owner_id: str, project_id: str, structure: Dict[str, Any]
) -> Dict[str, Any]:
    """Link extracted rules to EXISTING project assets (never auto-creates)."""
    blocks = await prompt_assets.list_blocks(owner_id, project_id)
    blocks_by_category: Dict[str, Dict[str, Any]] = {}
    for block in blocks:
        category = block.get("category")
        if category and category not in blocks_by_category:
            blocks_by_category[category] = block

    rule_lines: List[str] = []
    for key in ("constraints", "architecture_rules", "design_rules", "security_rules"):
        rule_lines.extend([line for line in (structure.get(key) or []) if line])

    linked_blocks: List[Dict[str, Any]] = []
    linked_block_ids: set = set()
    suggested: List[Dict[str, Any]] = []
    suggested_keys: set = set()

    for line in rule_lines:
        category = _constraint_category(line)
        if not category:
            continue
        match = blocks_by_category.get(category)
        matched_by = f"category_match:{category}"
        if match is None:
            name = _derive_block_name(line)
            match = await prompt_assets.find_equivalent_block(
                owner_id, project_id, name=name, content=line, category=category,
            )
            matched_by = "equivalent"
        if match is not None:
            block_id = match.get("id")
            if block_id not in linked_block_ids:
                linked_block_ids.add(block_id)
                linked_blocks.append({
                    "id": block_id,
                    "name": match.get("name"),
                    "category": match.get("category"),
                    "version": match.get("version"),
                    "matched_by": matched_by,
                })
            continue
        key = (category, _WS_RE.sub(" ", _clean_line(line)).lower())
        if key in suggested_keys:
            continue
        suggested_keys.add(key)
        suggested.append({
            "name": _derive_block_name(line),
            "category": category,
            "content": line,
            "reason": "no equivalent project block",
        })

    policies = await prompt_assets.list_policies(owner_id, project_id)
    relevant_domains: set = set()
    if structure.get("architecture_rules"):
        relevant_domains.add("ARCHITECTURE")
    if structure.get("design_rules"):
        relevant_domains.add("DESIGN")
    if structure.get("security_rules"):
        relevant_domains.add("SECURITY")

    linked_policies: List[Dict[str, Any]] = []
    linked_policy_ids: set = set()
    for policy in policies:
        domain = policy.get("domain")
        if domain in relevant_domains and policy.get("id") not in linked_policy_ids:
            linked_policy_ids.add(policy.get("id"))
            linked_policies.append({
                "id": policy.get("id"),
                "name": policy.get("name"),
                "domain": domain,
                "version": policy.get("version"),
                "matched_by": f"domain_match:{domain}",
            })

    invariants = await prompt_assets.list_invariants(
        owner_id, project_id, status="active"
    )
    composed = compose_content(structure).lower()
    linked_invariants: List[Dict[str, Any]] = []
    linked_invariant_ids: set = set()
    for invariant in invariants:
        domain = invariant.get("domain")
        code = invariant.get("code") or ""
        by_domain = domain in relevant_domains
        by_code = bool(code) and code.lower() in composed
        if not (by_domain or by_code) or invariant.get("id") in linked_invariant_ids:
            continue
        linked_invariant_ids.add(invariant.get("id"))
        linked_invariants.append({
            "id": invariant.get("id"),
            "code": code,
            "domain": domain,
            "severity": invariant.get("severity"),
            "title": invariant.get("title"),
            "matched_by": "domain_match" if by_domain else "code_present",
        })

    return {
        "blocks": {
            "linked": linked_blocks,
            "suggested_created": suggested,
            "candidates": [
                {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "category": block.get("category"),
                    "version": block.get("version"),
                }
                for block in blocks
            ],
        },
        "policies": {
            "linked": linked_policies,
            "candidates": policies,
        },
        "invariants": {
            "linked": linked_invariants,
            "candidates": [
                {
                    "id": invariant.get("id"),
                    "code": invariant.get("code"),
                    "domain": invariant.get("domain"),
                    "severity": invariant.get("severity"),
                    "title": invariant.get("title"),
                }
                for invariant in invariants
            ],
        },
    }


async def _require_project(owner_id: str, project_id: Optional[str]) -> Dict[str, Any]:
    project = await repo.projects.get(project_id, owner_id) if project_id else None
    if not project:
        raise PromptCaptureNotFound("Project not found")
    return project


async def _load_source(
    owner_id: str,
    project_id: str,
    conversation_id: str,
    message_ids: List[str],
    mode: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Owner-scoped load of the conversation and the selected messages."""
    conversation = await repo.conversations.get(conversation_id, owner_id)
    if not conversation:
        raise PromptCaptureNotFound("Conversation not found")
    if conversation.get("project_id") != project_id:
        raise PromptCaptureError("Conversation belongs to a different project")

    all_messages = await repo.messages.list(
        {"owner_id": owner_id, "conversation_id": conversation_id},
        sort_field="created_at", descending=False, limit=1000,
    )

    if message_ids:
        by_id = {m.get("id"): m for m in all_messages}
        for message_id in message_ids:
            if message_id not in by_id:
                raise PromptCaptureError("message not found in conversation")
        requested = set(message_ids)
        selection = [m for m in all_messages if m.get("id") in requested]
    else:
        selection = list(all_messages)

    if mode == "response":
        assistants = [m for m in selection if m.get("role") == "assistant"]
        if not assistants:
            assistants = [m for m in all_messages if m.get("role") == "assistant"]
        if not assistants:
            raise PromptCaptureError("No assistant response to capture")
        return conversation, assistants
    return conversation, selection


def _source_text(messages: List[Dict[str, Any]], mode: str) -> str:
    """Build source text from selected messages (assistant-only or role-labelled)."""
    if mode == "response":
        return "\n\n".join(
            (m.get("content") or "") for m in messages if (m.get("content") or "")
        )
    lines: List[str] = []
    for message in messages:
        content = message.get("content") or ""
        if not content:
            continue
        role = message.get("role")
        if role == "user":
            label = "User"
        elif role == "assistant":
            label = "Assistant"
        elif isinstance(role, str) and role:
            label = role.capitalize()
        else:
            label = "System"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _first_non_chatter(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    for raw in text.splitlines():
        if _is_chatter(raw):
            continue
        cleaned = _clean_line(raw)
        if cleaned:
            return cleaned
    return ""


def _preview_payload(
    project_id: str,
    *,
    content: str,
    system_prompt: str,
    variables: Dict[str, Any],
    structured_spec: Dict[str, Any],
    block_ids: List[str],
    policy_ids: List[str],
    invariant_ids: List[str],
    target: str,
    mode: str,
) -> Dict[str, Any]:
    return {
        "project_id": project_id,
        "version": {
            "content": content,
            "system_prompt": system_prompt,
            "variables": variables,
            "structured_spec": structured_spec,
            "blocks": list(block_ids),
        },
        "block_ids": list(block_ids),
        "policy_ids": list(policy_ids),
        "include_policies": False,
        "invariant_ids": list(invariant_ids),
        "include_invariants": False,
        "target": target,
        "mode": mode,
    }


async def _run_preview(
    owner_id: str, payload: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        return await prompt_compiler.preview_inline(owner_id, payload), None
    except PromptCompilerError as exc:
        return None, str(exc)


async def analyze(owner_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministically analyze a conversation into a structured prompt draft."""
    payload = payload or {}
    mode = payload.get("mode") or "conversation"
    if mode not in CAPTURE_MODES:
        raise PromptCaptureError("Invalid mode")
    project_id = payload.get("project_id")
    await _require_project(owner_id, project_id)
    conversation_id = payload.get("conversation_id") or ""
    message_ids = list(payload.get("message_ids") or [])

    conversation, messages = await _load_source(
        owner_id, project_id, conversation_id, message_ids, mode
    )
    raw_text = _source_text(messages, mode)

    try:
        decision_state = await planning_state.state_for_capture(
            owner_id, project_id, conversation_id)
    except Exception:
        decision_state = {
            "confirmed_decisions": [], "constraints": [], "rejected_options": [],
            "assumptions": [], "open_questions": [], "verification_items": [],
            "conflicts": [], "resolved_topics": [], "counts": {},
        }
    capture_fields = planning_state.render_capture_fields(decision_state)

    redacted_text = redaction.redact_text(raw_text)
    redaction_matches = sum(
        1 for m in messages if redaction.contains_secret(m.get("content") or "")
    )
    redacted = redaction.contains_secret(raw_text) or redaction_matches > 0

    structure = extract_structure(redacted_text)
    if not isinstance(structure, dict):
        structure = {
            key: ("" if key in ("role", "objective") else []) for key in _SECTION_KEYS
        }
    redacted_structure = redaction.redact(structure)
    if isinstance(redacted_structure, dict):
        structure = redacted_structure

    title = payload.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else ""
    if title:
        title = redaction.redact_text(title)
    else:
        derived = ""
        for message in messages:
            if message.get("role") == "user":
                derived = _first_non_chatter(message.get("content") or "")
                if derived:
                    break
        if not derived:
            derived = _first_non_chatter(raw_text)
        title = redaction.redact_text(derived[:80]) if derived else ""
        if not title:
            title = redaction.redact_text(conversation.get("title") or "") or "Captured prompt"

    purpose = payload.get("purpose")
    purpose = purpose.strip() if isinstance(purpose, str) and purpose.strip() else ""
    if not purpose:
        purpose = structure.get("objective") or title
    purpose = redaction.redact_text(purpose)

    content = compose_content(structure)
    decision_sections = _decision_markdown(capture_fields)
    if decision_sections:
        content = f"{content}\n\n{decision_sections}" if content else decision_sections
    content = redaction.redact_text(_sanitize_template_braces(content))
    system_prompt = redaction.redact_text(
        _sanitize_template_braces(structure.get("role") or "")
    )

    assets = await _link_assets(owner_id, project_id, structure)
    linked_block_ids = [
        entry["id"] for entry in assets["blocks"]["linked"] if entry.get("id")
    ]
    linked_policy_ids = [
        entry["id"] for entry in assets["policies"]["linked"] if entry.get("id")
    ]
    linked_invariant_ids = [
        entry["id"] for entry in assets["invariants"]["linked"] if entry.get("id")
    ]

    captured_at = now_iso()
    structured_spec: Dict[str, Any] = {
        "objective": structure.get("objective") or "",
        "role": structure.get("role") or "",
        "scope": list(structure.get("scope") or []),
        "constraints": _merge_unique(
            structure.get("constraints"), capture_fields.get("constraints")),
        "confirmed_decisions": list(capture_fields.get("confirmed_decisions") or []),
        "rejected_options": list(capture_fields.get("rejected_options") or []),
        "assumptions": list(capture_fields.get("assumptions") or []),
        "open_questions": list(capture_fields.get("open_questions") or []),
        "verification_items": list(capture_fields.get("verification_items") or []),
        "architecture_rules": list(structure.get("architecture_rules") or []),
        "design_rules": list(structure.get("design_rules") or []),
        "security_rules": list(structure.get("security_rules") or []),
        "phases": list(structure.get("phases") or []),
        "validation": list(structure.get("validation") or []),
        "output_contract": {
            "format": "markdown",
            "required_sections": list(structure.get("expected_output") or []),
        },
        "source": {
            "type": "conversation_capture",
            "conversation_id": conversation_id,
            "message_ids": [m.get("id") for m in messages],
            "captured_at": captured_at,
            "untrusted": True,
        },
        "linked": {
            "blocks": linked_block_ids,
            "policies": linked_policy_ids,
            "invariants": linked_invariant_ids,
        },
        "variables": {},
    }
    for _field in (
        "confirmed_decisions", "rejected_options", "assumptions",
        "open_questions", "verification_items", "constraints",
    ):
        structured_spec[_field] = redaction.redact(structured_spec[_field])

    preview, preview_error = await _run_preview(
        owner_id,
        _preview_payload(
            project_id,
            content=content,
            system_prompt=system_prompt,
            variables={},
            structured_spec=structured_spec,
            block_ids=linked_block_ids,
            policy_ids=linked_policy_ids,
            invariant_ids=linked_invariant_ids,
            target="generic_chat",
            mode="structured",
        ),
    )

    message_id_list = [m.get("id") for m in messages]
    return {
        "project_id": project_id,
        "conversation_id": conversation_id,
        "message_ids": message_id_list,
        "mode": mode,
        "title": title,
        "purpose": purpose,
        "source": {
            "conversation_id": conversation_id,
            "message_ids": message_id_list,
            "message_count": len(messages),
        },
        "structure": structure,
        "decision_state": decision_state,
        "capture_fields": capture_fields,
        "content": content,
        "system_prompt": system_prompt,
        "variables": {},
        "structured_spec": structured_spec,
        "blocks": assets["blocks"],
        "policies": assets["policies"],
        "invariants": assets["invariants"],
        "redaction": {"redacted": bool(redacted), "matches": int(redaction_matches)},
        "preview": preview,
        "preview_error": preview_error,
    }


async def _confirmed_blocks(
    owner_id: str, project_id: str, block_ids: List[str]
) -> List[str]:
    confirmed: List[str] = []
    for block_id in block_ids:
        try:
            block = await prompt_assets.get_block(owner_id, block_id)
        except PromptAssetNotFound as exc:
            raise PromptCaptureNotFound(str(exc)) from exc
        if block.get("project_id") != project_id:
            raise PromptCaptureError("Prompt block belongs to a different project")
        if block_id not in confirmed:
            confirmed.append(block_id)
    return confirmed


async def _confirmed_policies(
    owner_id: str, project_id: str, policy_ids: List[str]
) -> List[str]:
    confirmed: List[str] = []
    for policy_id in policy_ids:
        try:
            policy = await prompt_assets.get_policy(owner_id, policy_id)
        except PromptAssetNotFound as exc:
            raise PromptCaptureNotFound(str(exc)) from exc
        if policy.get("project_id") != project_id:
            raise PromptCaptureError("Project policy belongs to a different project")
        if policy_id not in confirmed:
            confirmed.append(policy_id)
    return confirmed


async def _confirmed_invariants(
    owner_id: str, project_id: str, invariant_ids: List[str]
) -> List[str]:
    confirmed: List[str] = []
    for invariant_id in invariant_ids:
        try:
            invariant = await prompt_assets.get_invariant(owner_id, invariant_id)
        except PromptAssetNotFound as exc:
            raise PromptCaptureNotFound(str(exc)) from exc
        if invariant.get("project_id") != project_id:
            raise PromptCaptureError("Project invariant belongs to a different project")
        if invariant_id not in confirmed:
            confirmed.append(invariant_id)
    return confirmed


async def save(owner_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist an analyzed capture as a Prompt Draft (optionally frozen)."""
    payload = payload or {}
    kind = payload.get("kind") or "prompt"
    if kind not in CAPTURE_KINDS:
        raise PromptCaptureError("Invalid kind")
    mode = payload.get("mode") or "conversation"
    if mode not in CAPTURE_MODES:
        raise PromptCaptureError("Invalid mode")
    project_id = payload.get("project_id")
    await _require_project(owner_id, project_id)

    conversation_id = payload.get("conversation_id") or ""
    message_ids = list(payload.get("message_ids") or [])

    try:
        analysis = await analyze(owner_id, {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "message_ids": message_ids,
            "mode": mode,
            "title": payload.get("title"),
            "purpose": payload.get("purpose"),
        })

        block_ids = await _confirmed_blocks(
            owner_id, project_id, list(payload.get("include_block_ids") or [])
        )
        policy_ids = await _confirmed_policies(
            owner_id, project_id, list(payload.get("include_policy_ids") or [])
        )
        invariant_ids = await _confirmed_invariants(
            owner_id, project_id, list(payload.get("include_invariant_ids") or [])
        )

        if kind == "planning_template":
            # Auto-map suggested constraints to EXISTING equivalent blocks. This
            # is the only implicit linking path and it never creates anything
            # unless ``create_missing_blocks`` is explicitly true. Policies and
            # invariants are NEVER auto-created or auto-linked from content.
            # The analyzer already resolved existing category/equivalent matches
            # (``linked``); persist them so the saved version keeps the mapping.
            for entry in analysis["blocks"]["linked"]:
                block_id = entry.get("id")
                if block_id and block_id not in block_ids:
                    block_ids.append(block_id)
            suggested = analysis["blocks"]["suggested_created"]
            for entry in suggested:
                match = await prompt_assets.find_equivalent_block(
                    owner_id, project_id,
                    name=entry.get("name") or "",
                    content=entry.get("content") or "",
                    category=entry.get("category"),
                )
                if match and match.get("id") not in block_ids:
                    block_ids.append(match["id"])
            if payload.get("create_missing_blocks"):
                for entry in suggested:
                    # Re-check equivalence immediately before creating so a
                    # concurrent/earlier create can never produce a duplicate.
                    match = await prompt_assets.find_equivalent_block(
                        owner_id, project_id,
                        name=entry.get("name") or "",
                        content=entry.get("content") or "",
                        category=entry.get("category"),
                    )
                    if match:
                        if match.get("id") not in block_ids:
                            block_ids.append(match["id"])
                        continue
                    created = await prompt_assets.create_block(owner_id, {
                        "project_id": project_id,
                        "name": entry.get("name") or "Captured constraint",
                        "description": "Captured constraint from conversation",
                        "category": entry.get("category") or "other",
                        "content": entry.get("content") or "",
                        "status": "active",
                    })
                    block_ids.append(created["id"])

        name = ""
        for candidate in (payload.get("name"), payload.get("title"), analysis["title"]):
            if isinstance(candidate, str) and candidate.strip():
                name = redaction.redact_text(candidate.strip())
                break
        if not name:
            raise PromptCaptureError("name is required")

        prompt_id = payload.get("prompt_id")
        if prompt_id:
            prompt = await prompt_registry._require_prompt(owner_id, prompt_id)
            if prompt.get("project_id") != project_id:
                raise PromptCaptureError("Prompt belongs to a different project")
        else:
            # Mirror app/routers/prompts.py:create_prompt exactly (status active).
            now = now_iso()
            prompt = {
                "id": new_id(),
                "owner_id": owner_id,
                "project_id": project_id,
                "name": name,
                "description": "",
                "purpose": redaction.redact_text(analysis["purpose"] or ""),
                "status": "active",
                "created_by": owner_id,
                "created_at": now,
                "updated_at": now,
            }
            await repo.prompts.insert(prompt)

        variables = redaction.redact(payload.get("variables") or {})
        if not isinstance(variables, dict):
            variables = {}
        notes = payload.get("notes") or f"Captured from conversation {conversation_id}"
        if isinstance(notes, str):
            notes = redaction.redact_text(notes)

        final_spec = {
            **(analysis["structured_spec"] or {}),
            "linked": {
                "blocks": list(block_ids),
                "policies": list(policy_ids),
                "invariants": list(invariant_ids),
            },
        }
        for _field in (
            "confirmed_decisions", "rejected_options", "assumptions",
            "open_questions", "verification_items", "constraints",
        ):
            if _field in final_spec:
                final_spec[_field] = redaction.redact(final_spec[_field])
        version_payload = {
            "content": analysis["content"],
            "system_prompt": analysis["system_prompt"],
            "variables": variables,
            "notes": notes,
            "structured_spec": final_spec,
            "blocks": list(block_ids),
        }
        version = await prompt_registry.create_version(owner_id, prompt, version_payload)
        if payload.get("freeze_version"):
            # The Create Agent flow may bind only FROZEN prompt versions (the
            # agent runtime rejects drafts); an explicit user request is the only
            # thing that freezes here.
            version = await prompt_registry.freeze_version(
                owner_id, prompt, version["id"]
            )

        preview, preview_error = await _run_preview(
            owner_id,
            _preview_payload(
                project_id,
                content=analysis["content"],
                system_prompt=analysis["system_prompt"],
                variables=variables,
                structured_spec=final_spec,
                block_ids=block_ids,
                policy_ids=policy_ids,
                invariant_ids=invariant_ids,
                target=payload.get("target") or "generic_chat",
                mode=payload.get("compiler_mode") or "structured",
            ),
        )

        now = now_iso()
        capture = {
            "id": new_id(),
            "owner_id": owner_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "message_ids": list(analysis["message_ids"]),
            "mode": mode,
            "kind": kind,
            "title": analysis["title"],
            "purpose": redaction.redact_text(analysis["purpose"] or ""),
            "content": analysis["content"],
            "system_prompt": analysis["system_prompt"],
            "structure": analysis["structure"],
            "structured_spec": final_spec,
            "blocks": list(block_ids),
            "policies": list(policy_ids),
            "invariants": list(invariant_ids),
            "redaction": analysis["redaction"],
            "prompt_id": prompt["id"],
            "prompt_version_id": version["id"],
            "version_status": version.get("status"),
            "preview_checksum": (preview or {}).get("checksum"),
            "created_at": now,
            "updated_at": now,
        }
        await repo.prompt_captures.insert(capture)

        resolved_blocks = [
            await prompt_assets.get_block(owner_id, bid) for bid in block_ids
        ]
        resolved_policies = [
            await prompt_assets.get_policy(owner_id, pid) for pid in policy_ids
        ]
        resolved_invariants = [
            await prompt_assets.get_invariant(owner_id, iid) for iid in invariant_ids
        ]

        return {
            "capture": capture,
            "prompt": prompt,
            "version": version,
            "blocks": resolved_blocks,
            "policies": resolved_policies,
            "invariants": resolved_invariants,
            "preview": preview,
            "preview_error": preview_error,
        }
    except PromptCaptureError:
        raise
    except PromptRegistryNotFound as exc:
        raise PromptCaptureNotFound(str(exc)) from exc
    except (PromptRegistryError, PromptAssetError, PromptCompilerError) as exc:
        raise PromptCaptureError(str(exc)) from exc


async def list_captures(
    owner_id: str, project_id: str, *, limit: int = 50
) -> List[Dict[str, Any]]:
    """Owner+project-scoped capture listing, newest first."""
    query = {"owner_id": owner_id, "project_id": project_id}
    bounded = max(int(limit or 50), 1)
    return await repo.prompt_captures.list(
        query, sort_field="created_at", descending=True, limit=bounded,
    )


async def get_capture(owner_id: str, capture_id: str) -> Dict[str, Any]:
    """Owner-scoped capture lookup; missing/foreign resolves to not-found."""
    capture = await repo.prompt_captures.get(capture_id, owner_id)
    if not capture:
        raise PromptCaptureNotFound("Capture not found")
    return capture


def catalog() -> Dict[str, Any]:
    """Static catalog of capture modes, kinds, sections and compiler vocabularies."""
    return {
        "modes": list(CAPTURE_MODES),
        "kinds": list(CAPTURE_KINDS),
        "sections": list(_SECTION_KEYS),
        "targets": list(PROMPT_COMPILER_TARGETS),
        "compiler_modes": list(PROMPT_COMPILER_MODES),
    }
