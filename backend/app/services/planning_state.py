"""V6.14 Planning Decision Memory service.

Deterministic, LLM-free decision ledger for Workspace Plan mode. It records what
the user CONFIRMED, REJECTED, ASSUMED, left OPEN, or must VERIFY during a
planning conversation, with provenance back to the originating message. It is
NOT a second prompt or conversation system: consolidation reuses the existing
Prompt Lab assets (``repo.project_policies`` / ``repo.project_invariants`` via
:mod:`app.services.prompt_assets`) and the capture bridge only exposes the
ledger's active fields to the existing prompt-capture flow.

Security properties
-------------------
* **Redaction everywhere.** Every value, excerpt, evidence string and rendered
  markdown body passes through :mod:`app.services.redaction` before it is
  persisted or returned.
* **Owner + project + conversation isolation.** Every read is owner-scoped; a
  foreign resource resolves to not-found and a conversation from another project
  is rejected.
* **Never raises on malformed input.** Heuristic extraction tolerates non-string
  and oddly shaped input and returns bounded, empty-safe structures.
* **Statuses never collapse.** A model assumption, a rejected option and a
  confirmed decision are distinct rows and can never be silently merged into a
  single generic note.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .. import repository as repo
from ..models import (
    PLANNING_CONFLICT_RESOLUTIONS,
    PLANNING_CONFLICT_STATUSES,
    PLANNING_ITEM_KINDS,
    PLANNING_ITEM_STATUSES,
    PLANNING_SOURCE_TYPES,
    PLANNING_VERIFICATION_STATUSES,
    new_id,
    now_iso,
)
from . import prompt_assets, redaction
from .prompt_assets import PromptAssetError, PromptAssetNotFound


class PlanningStateError(ValueError):
    """Domain validation error; routers translate this to HTTP 400."""


class PlanningStateNotFound(PlanningStateError):
    """Owner-scoped resource missing (HTTP 404)."""


# ------------------------------------------------------------------ constants
ITEM_STATUSES = PLANNING_ITEM_STATUSES
ITEM_KINDS = PLANNING_ITEM_KINDS
SOURCE_TYPES = PLANNING_SOURCE_TYPES
VERIFICATION_STATUSES = PLANNING_VERIFICATION_STATUSES
CONFLICT_RESOLUTIONS = PLANNING_CONFLICT_RESOLUTIONS

# An item in one of these statuses is a settled, authoritative outcome.
RESOLVED_STATUSES = ("CONFIRMED_BY_USER", "TECHNICALLY_VERIFIED", "REJECTED")
_AUTHORITATIVE_STATUSES = ("CONFIRMED_BY_USER", "TECHNICALLY_VERIFIED")

_WS_RE = re.compile(r"\s+")
_NEGATIVE_RE = re.compile(r"\b(no|not|never|without|don't|dont|avoid|exclude|reject)\b")
_ONLY_RE = re.compile(r"\bonly\b")
_CLAUSE_SPLIT_RE = re.compile(r"[\n\r;.!?]+")

# A question containing one of these markers asks for more detail than a
# resolved topic can answer, so it is always allowed through.
_REFINEMENT_MARKERS = (
    "which version",
    "what version",
    "how many",
    "which model",
    "how often",
    "which android",
    "which provider",
    "which api",
    "which employee",
    "which backend",
    "when ",
    "where ",
)

# Short generic keywords that would otherwise match far too eagerly.
_GENERIC_TOPIC_KEYWORDS = (
    "sync", "backup", "email", "retention", "admin", "enroll", "shift",
)

_PERSISTENCE_VERBS = ("store", "persist", "save", "map", "index", "associate", "link")
_TECHNICAL_NOUNS = (
    "employee_id", "biometric", "fingerprint", "schema", "collection",
    "table", "database", "endpoint", "api",
)
_VERIFICATION_PHRASES = (
    "needs verification",
    "need verification",
    "unverified",
    "to be verified",
    "must be verified",
    "not verified",
)
_ASSUMPTION_PREFIXES = ("assumption", "assumptions", "we assume", "assuming")
_HEDGE_PHRASES = (
    "maybe", "perhaps", "probably", "i think", "we might",
    "not sure", "tbd", "to be decided", "unclear",
)

# Ordered topic rules, MOST-SPECIFIC-FIRST. FIRST MATCH WINS per clause, so
# specific rules precede generic-keyword rules that would otherwise shadow them.
# ``platform.mobile`` stays first so any clause mentioning android/ios resolves
# mobile; ``platform.ios`` / ``platform.android`` exist so the extractor can
# emit an explicit REJECTED alternative for an "android only" / "ios only"
# clause. Generic "backup" was removed from ``backup.drive`` so "Google Drive
# daily backup" is not shadowed by it.
_TOPIC_RULES: Tuple[Dict[str, Any], ...] = (
    {
        "key": "platform.mobile", "category": "platform", "label": "Mobile platform",
        "keywords": ("android", "ios", "mobile platform", "mobile os", "iphone", "mobile app", "phone platform"),
    },
    {
        "key": "platform.ios", "category": "platform", "label": "iOS",
        "keywords": ("ios", "iphone", "ipad"),
    },
    {
        "key": "platform.android", "category": "platform", "label": "Android",
        "keywords": ("android",),
    },
    {
        "key": "device.model", "category": "device", "label": "Device model",
        "keywords": ("shared company phone", "shared phone", "company phone", "one shared", "shared device", "one device", "device model", "company device", "single device"),
    },
    {
        "key": "biometrics.multiple_fingerprints", "category": "biometrics", "label": "Multiple fingerprints / biometric mapping",
        "keywords": ("multiple fingerprints", "fingerprint mapping", "biometric id", "biometric_id", "employee_id", "fingerprint id"),
    },
    {
        "key": "auth.method", "category": "auth", "label": "Authentication method",
        "keywords": ("fingerprint", "biometric login", "fingerprint only", "authentication", "login method", "password", "pin code", "face id"),
    },
    {
        "key": "biometrics.selfie", "category": "biometrics", "label": "Selfie / face photo",
        "keywords": ("selfie", "face photo", "face capture", "photo verification"),
    },
    {
        "key": "enrollment.method", "category": "enrollment", "label": "Enrollment method",
        "keywords": ("enroll", "enrollment", "onboard", "register employee", "registration", "who enrolls"),
    },
    {
        "key": "admin.owner", "category": "admin", "label": "Administrator",
        "keywords": ("hr backoffice", "hr admin", "hr-assisted", "hr assisted", "admin", "administrator", "back office", "backoffice", "who manages", "hr manages"),
    },
    {
        "key": "location.geofence", "category": "location", "label": "GPS / geofence",
        "keywords": ("gps", "geofence", "geo-fence", "location tracking", "geolocation", "geo location"),
    },
    {
        "key": "sync.offline", "category": "sync", "label": "Offline sync",
        "keywords": ("offline sync", "offline mode", "offline", "sync when online", "synchronize", "sync"),
    },
    {
        "key": "integration.hr_api", "category": "integration", "label": "HR integration API",
        "keywords": ("hr api", "hr integration", "integration api", "open api", "api integration", "hr system", "backoffice api"),
    },
    {
        "key": "notification.email", "category": "notification", "label": "Email notifications",
        "keywords": ("email notification", "email notifications", "email alert", "email lateness", "notify by email", "email"),
    },
    {
        "key": "backup.encryption", "category": "backup", "label": "Backup encryption",
        "keywords": ("backup encryption", "encrypt backup", "encrypted backup", "encryption at rest", "encryption"),
    },
    {
        "key": "backup.drive", "category": "backup", "label": "Backup destination",
        "keywords": ("google drive", "drive backup", "cloud backup", "backup destination"),
    },
    {
        "key": "backup.frequency", "category": "backup", "label": "Backup frequency",
        "keywords": ("daily backup", "backup frequency", "weekly backup", "every day"),
    },
    {
        "key": "data.retention", "category": "data", "label": "Data retention",
        "keywords": ("data retention", "retention policy", "retention period", "how long to keep", "keep data", "retention"),
    },
    {
        "key": "attendance.rules", "category": "attendance", "label": "Attendance business rules",
        "keywords": ("attendance rules", "shift", "shifts", "business rules", "working hours", "lateness rule"),
    },
    {
        "key": "employee.source_of_truth", "category": "data", "label": "Employee master / source of truth",
        "keywords": ("source of truth", "master data", "employee master", "system of record", "master record"),
    },
    {
        "key": "scale.saas_limits", "category": "scale", "label": "SaaS / tenant limits",
        "keywords": ("saas limits", "saas limit", "saas"),
    },
    {
        "key": "scale.employee_limit", "category": "scale", "label": "Employee limit",
        "keywords": ("unlimited employees", "employee limit", "number of employees", "how many employees"),
    },
)

# Fast key -> rule lookup for policy classification.
_TOPIC_RULES_BY_KEY: Dict[str, Dict[str, Any]] = {
    rule["key"]: rule for rule in _TOPIC_RULES
}

# Category -> policy domain used for automatic consolidated-plan linking.
_CATEGORY_POLICY_DOMAINS: Dict[str, str] = {
    "auth": "SECURITY",
    "biometrics": "SECURITY",
    "location": "SECURITY",
    "data": "DATA",
    "platform": "ARCHITECTURE",
    "device": "ARCHITECTURE",
    "integration": "ARCHITECTURE",
    "backup": "OPERATIONS",
    "sync": "OPERATIONS",
    "notification": "OPERATIONS",
    "scale": "OPERATIONS",
    "attendance": "PRODUCT",
    "admin": "PRODUCT",
    "enrollment": "PRODUCT",
}

# ------------------------------------------------------------------ V6.15 question policy
#: Hard cap on how many high-value questions a planner may ask in one turn.
MAX_HIGH_VALUE_QUESTIONS_PER_TURN = 3

#: Topic categories that materially affect the plan: product behavior,
#: architecture, security, permissions/authorization, data ownership/model,
#: workflows, integrations and operational cost. Mirrors the ``category`` names
#: used by :data:`_TOPIC_RULES`.
HIGH_VALUE_CATEGORIES: Tuple[str, ...] = (
    "platform", "device", "auth", "biometrics", "location", "data",
    "integration", "attendance", "admin", "enrollment", "sync", "backup",
    "scale", "notification",
)

#: Cosmetic / non-blocking concerns. A question mentioning any of these is
#: auto-assumed rather than asked and must never block PRD generation.
LOW_VALUE_TOPIC_KEYWORDS: Tuple[str, ...] = (
    "favicon", "animation", "animated", "color", "colour", "palette",
    "font", "typography", "spacing", "margin", "padding", "styling", "css",
    "theme", "dark mode", "logo", "icon", "decoration", "hover", "shadow",
    "border radius", "placeholder text", "loading spinner style", "skeleton",
    "micro-interaction", "illustration", "badge style",
)

#: Bounded set of topic keys whose coverage makes a coherent PRD possible.
CORE_PLANNING_TOPICS: Tuple[str, ...] = (
    "platform.mobile",
    "device.model",
    "auth.method",
    "enrollment.method",
    "admin.owner",
    "attendance.rules",
    "data.retention",
    "employee.source_of_truth",
    "integration.hr_api",
    "biometrics.multiple_fingerprints",
    "location.geofence",
)

#: Bounded denominator for the policy payload exposed over HTTP.
_POLICY_LOW_VALUE_CAP = 20

_GROUP_KEYS = (
    "confirmed_decisions", "constraints", "rejected_options",
    "assumptions", "open_questions", "verification_items",
)


# ------------------------------------------------------------------ primitives
def _normalize(text: Any) -> str:
    """Lowercase and collapse whitespace. Never raises."""
    try:
        if not isinstance(text, str):
            return ""
        return _WS_RE.sub(" ", text).strip().lower()
    except Exception:
        return ""


def _redact_str(value: Any) -> Any:
    return redaction.redact_text(value) if isinstance(value, str) else value


def _clean_value(value: Any) -> str:
    """Strip bullets/numbering/punctuation and cap at 300 chars. Never raises."""
    try:
        if not isinstance(value, str):
            return ""
        text = value.strip()
        text = re.sub(r"^[>\-*\u2022]+\s*", "", text)
        text = re.sub(r"^\d+[.)]\s*", "", text)
        text = text.strip(" \t.,;:!?\"'`")
        text = _WS_RE.sub(" ", text).strip()
        return text[:300]
    except Exception:
        return ""


def _clauses(text: Any) -> List[str]:
    """Split text into bounded, cleaned clauses. Never raises."""
    try:
        if not isinstance(text, str) or not text.strip():
            return []
        raw = text.replace("\r\n", "\n").replace("\r", "\n")
        out: List[str] = []
        for part in _CLAUSE_SPLIT_RE.split(raw):
            cleaned = _clean_value(part)
            if not cleaned:
                continue
            out.append(cleaned[:400])
            if len(out) >= 200:
                break
        return out
    except Exception:
        return []


def _assistant_lines(text: Any) -> List[str]:
    if not isinstance(text, str):
        return []
    out: List[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = _clean_value(raw)
        if cleaned:
            out.append(cleaned)
        if len(out) >= 200:
            break
    return out


def _match_topic(clause: Any) -> Optional[Dict[str, Any]]:
    """First topic rule whose keyword is a substring of the normalized clause."""
    try:
        norm = _normalize(clause)
        if not norm:
            return None
        for rule in _TOPIC_RULES:
            for keyword in rule["keywords"]:
                if keyword not in norm:
                    continue
                if keyword in _GENERIC_TOPIC_KEYWORDS and len(norm) > 200:
                    continue
                return rule
        return None
    except Exception:
        return None


def _is_negative(clause: Any) -> bool:
    try:
        return bool(_NEGATIVE_RE.search(_normalize(clause)))
    except Exception:
        return False


def _is_hedged(clause: Any) -> bool:
    try:
        norm = _normalize(clause)
        return any(phrase in norm for phrase in _HEDGE_PHRASES)
    except Exception:
        return False


def _is_only(clause: Any) -> bool:
    try:
        return bool(_ONLY_RE.search(_normalize(clause)))
    except Exception:
        return False


def _is_technical_claim(norm: str) -> bool:
    if not norm:
        return False
    has_verb = any(re.search(r"\b" + re.escape(v), norm) for v in _PERSISTENCE_VERBS)
    has_noun = any(noun in norm for noun in _TECHNICAL_NOUNS)
    return has_verb and has_noun


def _fallback_topic(text: Any) -> str:
    """Deterministic ``generic.<tok>.<tok>.<tok>`` key, or ``""`` when empty."""
    try:
        norm = _normalize(text)
        tokens = re.findall(r"[a-z0-9]+", norm)
        if not tokens:
            return ""
        bounded = [token[:24] for token in tokens[:3]]
        return ("generic." + ".".join(bounded))[:80]
    except Exception:
        return ""


def _canonical_value(rule: Dict[str, Any], clause: Any) -> str:
    norm = _normalize(clause)
    if rule.get("key") == "platform.mobile":
        has_android = "android" in norm
        has_ios = "ios" in norm or "iphone" in norm
        if has_android and has_ios:
            return "Android + iOS"
        if has_android:
            return "Android"
        if has_ios:
            return "iOS"
    if "unlimited employees" in norm:
        return "unlimited employees"
    if "google drive" in norm:
        return "Google Drive"
    return _clean_value(clause)


# ------------------------------------------------------------------ extraction
def extract_user_items(text: Any) -> List[Dict[str, Any]]:
    """Deterministic user-message extraction into bounded item stubs. Never raises."""
    items: List[Dict[str, Any]] = []
    seen: set = set()

    def add(topic_key: str, category: str, kind: str, value: Any,
            status: str, confidence: float, excerpt: str) -> None:
        val = _clean_value(value)
        if not val:
            return
        key = (topic_key, val.lower())
        if key in seen:
            return
        seen.add(key)
        items.append({
            "topic_key": topic_key,
            "category": category,
            "kind": kind,
            "value": val,
            "status": status,
            "confidence": confidence,
            "excerpt": excerpt,
        })

    for clause in _clauses(text):
        rule = _match_topic(clause)
        if not rule:
            continue
        norm = _normalize(clause)
        excerpt = _clean_value(clause)
        value = _canonical_value(rule, clause)

        if _is_negative(clause):
            kind, status, val, conf = "rejected_option", "REJECTED", rule["label"], 0.95
        elif _is_hedged(clause):
            kind, status, val, conf = "assumption", "ASSUMED_BY_MODEL", value, 0.4
        elif _is_only(clause):
            kind, status, val, conf = "decision", "CONFIRMED_BY_USER", value, 0.95
        else:
            kind, status, val, conf = "decision", "CONFIRMED_BY_USER", value, 0.8

        if rule["key"] == "scale.saas_limits" and status != "REJECTED":
            kind, status, val, conf = "open_question", "NEEDS_VERIFICATION", excerpt, 0.5

        add(rule["key"], rule["category"], kind, val, status, conf, excerpt)

        if rule["key"] == "platform.mobile" and _is_only(clause):
            if val == "Android":
                add("platform.ios", "platform", "rejected_option", "iOS", "REJECTED", 0.95, excerpt)
            elif val == "iOS":
                add("platform.android", "platform", "rejected_option", "Android", "REJECTED", 0.95, excerpt)

    return items


def extract_assistant_items(text: Any) -> List[Dict[str, Any]]:
    """Deterministic assistant-message extraction into item stubs. Never raises."""
    items: List[Dict[str, Any]] = []
    seen: set = set()

    def add(topic_key: str, category: str, kind: str, value: Any,
            status: str, confidence: float, excerpt: str) -> None:
        val = _clean_value(value)
        if not val:
            return
        key = (topic_key, val.lower())
        if key in seen:
            return
        seen.add(key)
        items.append({
            "topic_key": topic_key,
            "category": category,
            "kind": kind,
            "value": val,
            "status": status,
            "confidence": confidence,
            "excerpt": excerpt,
        })

    for line in _assistant_lines(text):
        rule = _match_topic(line)
        topic_key = _fallback_topic(line)   # always deterministic assistant topic
        category = rule["category"] if rule else "other"
        norm = _normalize(line)
        excerpt = _clean_value(line)
        captured = False

        if norm.startswith(_ASSUMPTION_PREFIXES):
            add(topic_key, category, "assumption", excerpt, "ASSUMED_BY_MODEL", 0.5, excerpt)
            captured = True
        if not captured and any(phrase in norm for phrase in _VERIFICATION_PHRASES):
            add(topic_key, category, "verification_item", excerpt, "NEEDS_VERIFICATION", 0.5, excerpt)
            captured = True
        if not captured and _is_technical_claim(norm):
            add(topic_key, category, "verification_item", excerpt, "NEEDS_VERIFICATION", 0.3, excerpt)

    return items


def topic_key_for_question(question: Any) -> str:
    """Resolve a question to a topic rule key, else a deterministic fallback."""
    rule = _match_topic(question)
    if rule:
        return rule["key"]
    return _fallback_topic(question)


# ------------------------------------------------------------------ public views
def _public_item(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    doc = doc or {}
    return {
        "id": doc.get("id"),
        "project_id": _redact_str(doc.get("project_id")),
        "conversation_id": _redact_str(doc.get("conversation_id")),
        "topic_key": _redact_str(doc.get("topic_key") or ""),
        "category": _redact_str(doc.get("category") or ""),
        "kind": _redact_str(doc.get("kind") or ""),
        "value": _redact_str(doc.get("value") or ""),
        "status": _redact_str(doc.get("status") or ""),
        "source_type": _redact_str(doc.get("source_type") or ""),
        "confidence": doc.get("confidence"),
        "verification_status": _redact_str(doc.get("verification_status") or ""),
        "source_message_id": _redact_str(doc.get("source_message_id")),
        "provenance": redaction.redact(doc.get("provenance") or {}),
        "supersedes_id": doc.get("supersedes_id"),
        "superseded_by_id": doc.get("superseded_by_id"),
        "conflict_id": doc.get("conflict_id"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def _public_conflict(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    doc = doc or {}
    return {
        "id": doc.get("id"),
        "project_id": _redact_str(doc.get("project_id")),
        "conversation_id": _redact_str(doc.get("conversation_id")),
        "topic_key": _redact_str(doc.get("topic_key") or ""),
        "category": _redact_str(doc.get("category") or ""),
        "previous_item_id": doc.get("previous_item_id"),
        "new_item_id": doc.get("new_item_id"),
        "previous_value": _redact_str(doc.get("previous_value") or ""),
        "new_value": _redact_str(doc.get("new_value") or ""),
        "status": _redact_str(doc.get("status") or ""),
        "resolution": _redact_str(doc.get("resolution")),
        "resolved_value": _redact_str(doc.get("resolved_value") or ""),
        "resolved_item_id": doc.get("resolved_item_id"),
        "source_message_id": _redact_str(doc.get("source_message_id")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "resolved_at": doc.get("resolved_at"),
    }


# ------------------------------------------------------------------ state
async def list_items(
    owner_id: str,
    project_id: str,
    conversation_id: str,
    *,
    include_inactive: bool = False,
) -> List[Dict[str, Any]]:
    """Owner+project+conversation scoped items, oldest first."""
    query: Dict[str, Any] = {
        "owner_id": owner_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
    }
    if not include_inactive:
        query["status"] = {"$ne": "SUPERSEDED"}
    return await repo.planning_decisions.list(
        query, sort_field="created_at", descending=False, limit=1000,
    )


async def get_state(
    owner_id: str, project_id: str, conversation_id: str
) -> Dict[str, Any]:
    """Assemble the grouped decision ledger for one conversation."""
    conversation = await repo.conversations.get(conversation_id, owner_id)
    if not conversation:
        raise PlanningStateNotFound("Conversation not found")
    if conversation.get("project_id") != project_id:
        # Reads are not-found for a foreign-project conversation so existence is
        # never revealed (write paths report this mismatch as a 400 instead).
        raise PlanningStateNotFound("Conversation not found")

    all_items = await list_items(
        owner_id, project_id, conversation_id, include_inactive=True
    )
    active = [doc for doc in all_items if doc.get("status") != "SUPERSEDED"]
    superseded = [doc for doc in all_items if doc.get("status") == "SUPERSEDED"]

    groups: Dict[str, List[Dict[str, Any]]] = {key: [] for key in _GROUP_KEYS}
    resolved_topics: set = set()
    for doc in active:
        status = doc.get("status")
        kind = doc.get("kind")
        topic_key = doc.get("topic_key")
        if status in RESOLVED_STATUSES and kind != "open_question" and topic_key:
            resolved_topics.add(topic_key)
        if status == "CONFLICTED":
            continue
        pub = _public_item(doc)
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

    open_conflicts = await repo.planning_conflicts.list(
        {
            "owner_id": owner_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "status": "open",
        },
        sort_field="created_at", descending=False, limit=1000,
    )
    public_conflicts = [_public_conflict(doc) for doc in open_conflicts]

    counts = {name: len(groups[name]) for name in _GROUP_KEYS}
    counts["conflicts"] = len(public_conflicts)

    return {
        "project_id": project_id,
        "conversation_id": conversation_id,
        "confirmed_decisions": groups["confirmed_decisions"],
        "constraints": groups["constraints"],
        "rejected_options": groups["rejected_options"],
        "assumptions": groups["assumptions"],
        "open_questions": groups["open_questions"],
        "verification_items": groups["verification_items"],
        "conflicts": public_conflicts,
        "superseded": [_public_item(doc) for doc in superseded],
        "resolved_topics": sorted(resolved_topics),
        "counts": counts,
    }


# ------------------------------------------------------------------ write logic
def _build_item(
    owner_id: str,
    project_id: str,
    conversation_id: str,
    *,
    topic_key: str,
    category: str,
    kind: str,
    value: str,
    status: str,
    source_type: str,
    confidence: float,
    source_message_id: Optional[str],
    provenance: Dict[str, Any],
    supersedes_id: Optional[str] = None,
    conflict_id: Optional[str] = None,
) -> Dict[str, Any]:
    now = now_iso()
    return {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "topic_key": topic_key,
        "category": category,
        "kind": kind,
        "value": value,
        "status": status,
        "source_type": source_type,
        "confidence": confidence,
        "verification_status": (
            "VERIFIED" if status == "TECHNICALLY_VERIFIED" else "UNVERIFIED"
        ),
        "source_message_id": source_message_id,
        "provenance": provenance,
        "supersedes_id": supersedes_id,
        "superseded_by_id": None,
        "conflict_id": conflict_id,
        "created_at": now,
        "updated_at": now,
    }


def _merge_provenance(
    current: Any, incoming: Dict[str, Any]
) -> Dict[str, Any]:
    base = dict(current) if isinstance(current, dict) else {}
    history = list(base.get("history") or [])
    history.append(dict(incoming or {}))
    base.update({
        "conversation_id": incoming.get("conversation_id") or base.get("conversation_id"),
        "message_id": incoming.get("message_id") or base.get("message_id"),
        "excerpt": incoming.get("excerpt") or base.get("excerpt"),
        "source_type": incoming.get("source_type") or base.get("source_type"),
        "recorded_at": incoming.get("recorded_at") or base.get("recorded_at"),
        "history": history[-20:],
    })
    return base


async def _find_active_by_topic(
    owner_id: str, project_id: str, conversation_id: str, topic_key: str
) -> Optional[Dict[str, Any]]:
    """First active item for a topic, preferring authoritative statuses."""
    docs = await repo.planning_decisions.list(
        {
            "owner_id": owner_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "topic_key": topic_key,
        },
        sort_field="created_at", descending=False, limit=1000,
    )
    fallback: Optional[Dict[str, Any]] = None
    for doc in docs:
        status = doc.get("status")
        if status == "SUPERSEDED" or status == "CONFLICTED":
            continue
        if status in _AUTHORITATIVE_STATUSES:
            return doc
        if fallback is None:
            fallback = doc
    return fallback


async def _record(
    owner_id: str,
    project_id: str,
    conversation_id: str,
    stub: Dict[str, Any],
    *,
    source_type: str,
    source_message_id: Optional[str],
    source_excerpt: str = "",
) -> Dict[str, Any]:
    """Record one extracted/manual item, superseding or conflicting as needed."""
    stub = stub or {}
    kind = stub.get("kind") or "decision"
    status = stub.get("status") or "CONFIRMED_BY_USER"
    if kind not in ITEM_KINDS:
        raise PlanningStateError("Invalid kind")
    if status not in ITEM_STATUSES:
        raise PlanningStateError("Invalid status")
    if source_type not in SOURCE_TYPES:
        raise PlanningStateError("Invalid source_type")

    topic_key = stub.get("topic_key") or ""
    category = _redact_str(stub.get("category") or "other")
    value = redaction.redact_text(_clean_value(stub.get("value") or ""))
    excerpt = redaction.redact_text(
        _clean_value(source_excerpt or stub.get("excerpt") or "")
    )
    try:
        confidence = float(stub.get("confidence"))
    except (TypeError, ValueError):
        confidence = 1.0

    now = now_iso()
    provenance = {
        "conversation_id": conversation_id,
        "message_id": source_message_id,
        "excerpt": excerpt,
        "source_type": source_type,
        "recorded_at": now,
    }

    existing = await _find_active_by_topic(
        owner_id, project_id, conversation_id, topic_key
    )

    if existing is None:
        item = _build_item(
            owner_id, project_id, conversation_id,
            topic_key=topic_key, category=category, kind=kind, value=value,
            status=status, source_type=source_type, confidence=confidence,
            source_message_id=source_message_id, provenance=provenance,
        )
        await repo.planning_decisions.insert(item)
        return {"item": item, "conflict": None}

    existing_key = _normalize(existing.get("value") or "")
    new_key = _normalize(value)
    if existing_key and existing_key == new_key:
        merged = _merge_provenance(existing.get("provenance"), provenance)
        updated = await repo.planning_decisions.update(
            existing["id"], owner_id,
            {"updated_at": now, "provenance": merged},
        )
        return {"item": updated or existing, "conflict": None}

    if existing.get("status") in _AUTHORITATIVE_STATUSES:
        if status != "CONFIRMED_BY_USER":
            # A weaker incoming item (ASSUMED_BY_MODEL / NEEDS_VERIFICATION / ...)
            # must never supersede or overwrite an authoritative decision.
            return {"item": existing, "conflict": None}
        conflict_id = new_id()
        new_item = _build_item(
            owner_id, project_id, conversation_id,
            topic_key=topic_key, category=category or existing.get("category") or "other",
            kind=kind, value=value, status="CONFLICTED",
            source_type=source_type, confidence=confidence,
            source_message_id=source_message_id, provenance=provenance,
            conflict_id=conflict_id,
        )
        await repo.planning_decisions.insert(new_item)
        conflict = {
            "id": conflict_id,
            "owner_id": owner_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "topic_key": topic_key,
            "category": new_item.get("category"),
            "previous_item_id": existing["id"],
            "new_item_id": new_item["id"],
            "previous_value": existing.get("value") or "",
            "new_value": value,
            "status": "open",
            "resolution": None,
            "resolved_value": "",
            "resolved_item_id": None,
            "source_message_id": source_message_id,
            "created_at": now,
            "updated_at": now,
            "resolved_at": None,
        }
        await repo.planning_conflicts.insert(conflict)
        return {"item": new_item, "conflict": conflict}

    new_item_id = new_id()
    await repo.planning_decisions.update(
        existing["id"], owner_id,
        {
            "status": "SUPERSEDED",
            "superseded_by_id": new_item_id,
            "updated_at": now,
        },
    )
    new_item = _build_item(
        owner_id, project_id, conversation_id,
        topic_key=topic_key, category=category or existing.get("category") or "other",
        kind=kind, value=value, status=status, source_type=source_type,
        confidence=confidence, source_message_id=source_message_id,
        provenance=provenance, supersedes_id=existing["id"],
    )
    new_item["id"] = new_item_id
    await repo.planning_decisions.insert(new_item)
    return {"item": new_item, "conflict": None}


# ------------------------------------------------------------------ ingestion
async def ingest_user_message(
    owner_id: str, project_id: str, conversation_id: str, message: Dict[str, Any]
) -> Dict[str, Any]:
    message = message or {}
    content = message.get("content") or ""
    summary: Dict[str, List[Any]] = {"recorded": [], "conflicts": []}
    for stub in extract_user_items(content):
        result = await _record(
            owner_id, project_id, conversation_id, stub,
            source_type="user", source_message_id=message.get("id"),
            source_excerpt=stub.get("excerpt") or "",
        )
        summary["recorded"].append(_public_item(result["item"]))
        if result.get("conflict"):
            summary["conflicts"].append(_public_conflict(result["conflict"]))
    return summary


async def ingest_assistant_message(
    owner_id: str, project_id: str, conversation_id: str, message: Dict[str, Any]
) -> Dict[str, Any]:
    message = message or {}
    content = message.get("content") or ""
    summary: Dict[str, List[Any]] = {"recorded": [], "conflicts": []}
    for stub in extract_assistant_items(content):
        result = await _record(
            owner_id, project_id, conversation_id, stub,
            source_type="assistant", source_message_id=message.get("id"),
            source_excerpt=stub.get("excerpt") or "",
        )
        summary["recorded"].append(_public_item(result["item"]))
        if result.get("conflict"):
            summary["conflicts"].append(_public_conflict(result["conflict"]))
    return summary


async def ingest_conversation(
    owner_id: str,
    project_id: str,
    conversation_id: str,
    message_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    conversation = await repo.conversations.get(conversation_id, owner_id)
    if not conversation:
        raise PlanningStateNotFound("Conversation not found")
    if conversation.get("project_id") != project_id:
        raise PlanningStateError("Conversation belongs to a different project")

    all_messages = await repo.messages.list(
        {"owner_id": owner_id, "conversation_id": conversation_id},
        sort_field="created_at", descending=False, limit=1000,
    )

    requested = list(message_ids or [])
    if requested:
        by_id = {m.get("id"): m for m in all_messages}
        for message_id in requested:
            if message_id not in by_id:
                raise PlanningStateError("message not found in conversation")
        selection = [m for m in all_messages if m.get("id") in set(requested)]
    else:
        selection = list(all_messages)

    result: Dict[str, Any] = {"user": [], "assistant": []}
    for message in selection:
        role = message.get("role")
        if role == "user":
            result["user"].append(
                await ingest_user_message(owner_id, project_id, conversation_id, message)
            )
        elif role == "assistant":
            result["assistant"].append(
                await ingest_assistant_message(owner_id, project_id, conversation_id, message)
            )
    result["state"] = await get_state(owner_id, project_id, conversation_id)
    return result


# ------------------------------------------------------------------ manual ops
async def _require_project(owner_id: str, project_id: Optional[str]) -> Dict[str, Any]:
    project = await repo.projects.get(project_id, owner_id) if project_id else None
    if not project:
        raise PlanningStateNotFound("Project not found")
    return project


async def _require_item(owner_id: str, item_id: Optional[str]) -> Dict[str, Any]:
    item = await repo.planning_decisions.get(item_id, owner_id) if item_id else None
    if not item:
        raise PlanningStateNotFound("Planning item not found")
    return item


async def create_item(
    owner_id: str, project_id: str, conversation_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    payload = payload or {}
    await _require_project(owner_id, project_id)
    conversation = await repo.conversations.get(conversation_id, owner_id)
    if not conversation:
        raise PlanningStateNotFound("Conversation not found")
    if conversation.get("project_id") != project_id:
        raise PlanningStateError("Conversation belongs to a different project")

    kind = payload.get("kind") or "decision"
    status = payload.get("status") or "CONFIRMED_BY_USER"
    if kind not in ITEM_KINDS:
        raise PlanningStateError("Invalid kind")
    if status not in ITEM_STATUSES:
        raise PlanningStateError("Invalid status")
    source_type = payload.get("source_type") or "manual"
    if source_type not in SOURCE_TYPES:
        raise PlanningStateError("Invalid source_type")

    stub = {
        "topic_key": payload.get("topic_key") or "",
        "category": payload.get("category") or "other",
        "kind": kind,
        "value": payload.get("value") or "",
        "status": status,
        "confidence": payload.get("confidence", 1.0),
        "excerpt": payload.get("excerpt") or "",
    }
    result = await _record(
        owner_id, project_id, conversation_id, stub,
        source_type=source_type,
        source_message_id=payload.get("source_message_id"),
        source_excerpt=payload.get("excerpt") or "",
    )
    return {
        "item": _public_item(result["item"]),
        "conflict": _public_conflict(result["conflict"]) if result.get("conflict") else None,
    }


async def get_item(owner_id: str, item_id: str) -> Dict[str, Any]:
    return _public_item(await _require_item(owner_id, item_id))


async def update_item(
    owner_id: str, item_id: str, patch: Dict[str, Any]
) -> Dict[str, Any]:
    patch = patch or {}
    await _require_item(owner_id, item_id)
    updates: Dict[str, Any] = {}
    if patch.get("topic_key") is not None:
        updates["topic_key"] = redaction.redact_text(str(patch["topic_key"]))
    if patch.get("category") is not None:
        updates["category"] = redaction.redact_text(str(patch["category"]))
    if patch.get("kind") is not None:
        if patch["kind"] not in ITEM_KINDS:
            raise PlanningStateError("Invalid kind")
        updates["kind"] = patch["kind"]
    if patch.get("value") is not None:
        updates["value"] = redaction.redact_text(_clean_value(str(patch["value"])))
    if patch.get("status") is not None:
        if patch["status"] not in ITEM_STATUSES:
            raise PlanningStateError("Invalid status")
        updates["status"] = patch["status"]
    if patch.get("confidence") is not None:
        try:
            updates["confidence"] = float(patch["confidence"])
        except (TypeError, ValueError):
            raise PlanningStateError("Invalid confidence")
    updates["updated_at"] = now_iso()
    doc = await repo.planning_decisions.update(item_id, owner_id, updates)
    if not doc:
        raise PlanningStateNotFound("Planning item not found")
    return _public_item(doc)


async def reopen_item(owner_id: str, item_id: str) -> Dict[str, Any]:
    item = await _require_item(owner_id, item_id)
    updates: Dict[str, Any] = {
        "status": "NEEDS_VERIFICATION",
        "verification_status": "UNVERIFIED",
        "updated_at": now_iso(),
    }
    if item.get("kind") == "decision":
        updates["kind"] = "open_question"
    doc = await repo.planning_decisions.update(item_id, owner_id, updates)
    return _public_item(doc)


async def verify_item(
    owner_id: str, item_id: str, verification_status: str, evidence: str = ""
) -> Dict[str, Any]:
    item = await _require_item(owner_id, item_id)
    if verification_status not in ("TECHNICALLY_VERIFIED", "NEEDS_VERIFICATION"):
        raise PlanningStateError("Invalid verification_status")
    provenance = dict(item.get("provenance") or {})
    redacted_evidence = redaction.redact_text(evidence or "")
    if redacted_evidence:
        provenance["evidence"] = redacted_evidence
    updates: Dict[str, Any] = {
        "provenance": provenance,
        "updated_at": now_iso(),
    }
    if verification_status == "TECHNICALLY_VERIFIED":
        updates["status"] = "TECHNICALLY_VERIFIED"
        updates["verification_status"] = "VERIFIED"
    else:
        updates["status"] = "NEEDS_VERIFICATION"
        updates["verification_status"] = "UNVERIFIED"
    doc = await repo.planning_decisions.update(item_id, owner_id, updates)
    return _public_item(doc)


async def list_conflicts(
    owner_id: str,
    project_id: str,
    conversation_id: str,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {
        "owner_id": owner_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
    }
    if status is not None:
        if status not in PLANNING_CONFLICT_STATUSES:
            raise PlanningStateError("Invalid status")
        query["status"] = status
    docs = await repo.planning_conflicts.list(
        query, sort_field="created_at", descending=False, limit=1000,
    )
    return [_public_conflict(doc) for doc in docs]


async def resolve_conflict(
    owner_id: str,
    conflict_id: str,
    resolution: str,
    value: str = "",
) -> Dict[str, Any]:
    conflict = await repo.planning_conflicts.get(conflict_id, owner_id)
    if not conflict:
        raise PlanningStateNotFound("Conflict not found")
    if conflict.get("status") != "open":
        raise PlanningStateError("Conflict is already resolved")
    if resolution not in CONFLICT_RESOLUTIONS:
        raise PlanningStateError("Invalid resolution")

    previous = await repo.planning_decisions.get(conflict.get("previous_item_id"), owner_id)
    new = await repo.planning_decisions.get(conflict.get("new_item_id"), owner_id)
    if not previous or not new:
        raise PlanningStateNotFound("Conflict item not found")

    now = now_iso()
    resolved_item_id = conflict.get("resolved_item_id")
    resolved_value = value or ""
    touched_ids: List[str] = []

    if resolution == "accept_new":
        await repo.planning_decisions.update(
            previous["id"], owner_id,
            {"status": "SUPERSEDED", "superseded_by_id": new["id"], "updated_at": now},
        )
        await repo.planning_decisions.update(
            new["id"], owner_id,
            {
                "status": "CONFIRMED_BY_USER",
                "conflict_id": None,
                "verification_status": "UNVERIFIED",
                "updated_at": now,
            },
        )
        resolved_value = new.get("value") or ""
        touched_ids = [previous["id"], new["id"]]
    elif resolution == "keep_previous":
        await repo.planning_decisions.update(
            new["id"], owner_id,
            {"status": "SUPERSEDED", "superseded_by_id": previous["id"], "updated_at": now},
        )
        resolved_value = previous.get("value") or ""
        touched_ids = [previous["id"], new["id"]]
    else:  # edit_manual
        manual_value = redaction.redact_text(_clean_value(value or ""))
        if not manual_value:
            raise PlanningStateError("value is required for edit_manual")
        created_id = new_id()
        created = _build_item(
            owner_id,
            conflict.get("project_id"),
            conflict.get("conversation_id"),
            topic_key=conflict.get("topic_key") or "",
            category=conflict.get("category") or "other",
            kind="decision",
            value=manual_value,
            status="CONFIRMED_BY_USER",
            source_type="manual",
            confidence=1.0,
            source_message_id=None,
            provenance={
                "conversation_id": conflict.get("conversation_id"),
                "message_id": None,
                "excerpt": "",
                "source_type": "manual",
                "recorded_at": now,
                "note": "resolved planning conflict via edit_manual",
            },
            supersedes_id=previous["id"],
        )
        created["id"] = created_id
        await repo.planning_decisions.update(
            previous["id"], owner_id,
            {"status": "SUPERSEDED", "superseded_by_id": created_id, "updated_at": now},
        )
        await repo.planning_decisions.update(
            new["id"], owner_id,
            {"status": "SUPERSEDED", "superseded_by_id": created_id, "updated_at": now},
        )
        await repo.planning_decisions.insert(created)
        resolved_item_id = created_id
        resolved_value = manual_value
        touched_ids = [previous["id"], new["id"], created_id]

    updates: Dict[str, Any] = {
        "status": "resolved",
        "resolution": resolution,
        "resolved_value": resolved_value,
        "resolved_at": now,
        "updated_at": now,
    }
    if resolved_item_id:
        updates["resolved_item_id"] = resolved_item_id
    updated_conflict = await repo.planning_conflicts.update(conflict_id, owner_id, updates)

    items = []
    for item_id in touched_ids:
        doc = await repo.planning_decisions.get(item_id, owner_id)
        if doc:
            items.append(_public_item(doc))

    state = await get_state(
        owner_id, conflict.get("project_id"), conflict.get("conversation_id")
    )
    return {
        "conflict": _public_conflict(updated_conflict or conflict),
        "items": items,
        "state": state,
    }


# ------------------------------------------------------------------ questions
def is_topic_resolved(state: Dict[str, Any], topic_key: str) -> bool:
    if not topic_key:
        return False
    if topic_key in (state.get("resolved_topics") or []):
        return True
    for group in ("confirmed_decisions", "rejected_options"):
        for item in state.get(group) or []:
            if isinstance(item, dict) and item.get("topic_key") == topic_key:
                return True
    return False


def _is_high_value_topic_key(topic_key: Any) -> bool:
    """True when ``topic_key`` names a core or high-value planning topic."""
    try:
        key = topic_key if isinstance(topic_key, str) else ""
        if not key:
            return False
        if key in CORE_PLANNING_TOPICS:
            return True
        rule = _TOPIC_RULES_BY_KEY.get(key)
        return bool(rule and rule.get("category") in HIGH_VALUE_CATEGORIES)
    except Exception:
        return False


def classify_question(question: Any) -> Dict[str, Any]:
    """Deterministic value classification for a single question.

    Returns ``HIGH_VALUE`` when the question materially affects the plan,
    ``LOW_VALUE`` when it is a cosmetic/non-blocking concern that should be
    auto-assumed, and ``DEFERRED`` otherwise. Never raises.
    """
    try:
        cleaned = (
            redaction.redact_text(question.strip())
            if isinstance(question, str) else ""
        )
        norm = _normalize(cleaned)
        if not norm:
            return {
                "question": cleaned,
                "topic_key": "",
                "value": "DEFERRED",
                "category": "",
                "reason": "empty or non-string question",
            }

        topic_key = topic_key_for_question(cleaned)

        for keyword in LOW_VALUE_TOPIC_KEYWORDS:
            if keyword in norm:
                return {
                    "question": cleaned,
                    "topic_key": topic_key,
                    "value": "LOW_VALUE",
                    "category": "cosmetic",
                    "reason": f"cosmetic/non-blocking concern: '{keyword}'",
                }

        rule = _match_topic(cleaned)
        if rule is not None:
            category = rule["category"]
            if category in HIGH_VALUE_CATEGORIES:
                return {
                    "question": cleaned,
                    "topic_key": rule["key"],
                    "value": "HIGH_VALUE",
                    "category": category,
                    "reason": f"materially affects the plan ({category})",
                }

        if topic_key in CORE_PLANNING_TOPICS:
            return {
                "question": cleaned,
                "topic_key": topic_key,
                "value": "HIGH_VALUE",
                "category": (rule or {}).get("category") or "other",
                "reason": "core planning topic",
            }

        # Refinement markers ("which version", "how many", "when ", ...) ask for
        # more detail on a material decision: always allowed through, never
        # blocked by a resolved topic. They consume the per-turn cap.
        if any(marker in norm for marker in _REFINEMENT_MARKERS):
            return {
                "question": cleaned,
                "topic_key": topic_key,
                "value": "HIGH_VALUE",
                "category": (rule or {}).get("category") or "architecture",
                "reason": "refinement question on a material decision",
            }

        return {
            "question": cleaned,
            "topic_key": topic_key,
            "value": "DEFERRED",
            "category": (rule or {}).get("category") or "other",
            "reason": "not a core blocking topic",
        }
    except Exception:
        return {
            "question": "",
            "topic_key": "",
            "value": "DEFERRED",
            "category": "",
            "reason": "unclassifiable question",
        }


_ACTIVE_GROUPS = (
    "confirmed_decisions", "constraints", "rejected_options",
    "assumptions", "open_questions", "verification_items",
)


def assess_interview_status(state: Any) -> Dict[str, Any]:
    """Pure, deterministic interview status for a decision-ledger state.

    ``stopped``/``prd_ready`` become True only when every core planning topic is
    covered and no blockers remain. Ordinary ``ASSUMED_BY_MODEL`` assumptions and
    generic NEEDS_VERIFICATION verification items are never blockers. Never
    raises and tolerates missing/odd state keys.
    """
    try:
        if not isinstance(state, dict):
            state = {}
        core = list(CORE_PLANNING_TOPICS)
        resolved = {
            topic for topic in (state.get("resolved_topics") or [])
            if isinstance(topic, str)
        }
        covered: set = set()
        for group in _ACTIVE_GROUPS:
            for item in state.get(group) or []:
                if not isinstance(item, dict):
                    continue
                topic_key = item.get("topic_key")
                if isinstance(topic_key, str) and topic_key in core:
                    covered.add(topic_key)
        covered |= (resolved & set(core))

        unresolved = [topic for topic in core if topic not in covered]

        blocking: List[Dict[str, Any]] = []
        for conflict in state.get("conflicts") or []:
            if isinstance(conflict, dict) and conflict.get("status") == "open":
                blocking.append({
                    "topic_key": _redact_str(conflict.get("topic_key") or ""),
                    "kind": "conflict",
                    "reason": "open conflict must be resolved",
                })
        for item in state.get("open_questions") or []:
            if not isinstance(item, dict):
                continue
            if item.get("kind") != "open_question":
                continue
            if item.get("status") != "NEEDS_VERIFICATION":
                continue
            topic_key = item.get("topic_key")
            if _is_high_value_topic_key(topic_key):
                blocking.append({
                    "topic_key": _redact_str(topic_key or ""),
                    "kind": "open_question",
                    "reason": "unresolved high-value question",
                })

        stopped = not unresolved and not blocking
        if stopped:
            reason = (
                "all core planning topics covered and no blockers; "
                "plan is ready to compile into a PRD"
            )
        else:
            parts: List[str] = []
            if unresolved:
                parts.append(f"{len(unresolved)} unresolved core topic(s)")
            if blocking:
                parts.append(f"{len(blocking)} blocker(s)")
            reason = "keep interviewing: " + "; ".join(parts)

        return {
            "covered_topics": [topic for topic in core if topic in covered],
            "unresolved_core_topics": unresolved,
            "blocking": blocking,
            "stopped": stopped,
            "prd_ready": stopped,
            "reason": reason,
        }
    except Exception:
        return {
            "covered_topics": [],
            "unresolved_core_topics": list(CORE_PLANNING_TOPICS),
            "blocking": [],
            "stopped": False,
            "prd_ready": False,
            "reason": "unable to assess interview status",
        }


def question_policy() -> Dict[str, Any]:
    """Bounded, serializable V6.15 policy vocabulary for the HTTP surface."""
    return {
        "max_high_value_questions_per_turn": MAX_HIGH_VALUE_QUESTIONS_PER_TURN,
        "high_value_categories": list(HIGH_VALUE_CATEGORIES),
        "low_value_topics": list(LOW_VALUE_TOPIC_KEYWORDS[:_POLICY_LOW_VALUE_CAP]),
    }


def filter_repeated_questions(
    questions: List[str], state: Dict[str, Any]
) -> Dict[str, Any]:
    """Partition questions into allowed / blocked / assumptions / deferred.

    ``allowed`` keeps the original contract: high-value, unresolved, non-repeated
    questions in input order, capped at
    :data:`MAX_HIGH_VALUE_QUESTIONS_PER_TURN`. ``blocked`` keeps entries with
    ``question`` / ``topic_key`` / ``reason``. Cosmetic LOW_VALUE questions are
    auto-assumed (``assumptions``) and DEFERRED or over-cap questions are held
    for later (``deferred``). Never raises.
    """
    state = state if isinstance(state, dict) else {}
    allowed: List[str] = []
    blocked: List[Dict[str, Any]] = []
    assumptions: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    for question in questions or []:
        if not isinstance(question, str) or not question.strip():
            continue
        cleaned = redaction.redact_text(question.strip())
        norm = _normalize(cleaned)
        classification = classify_question(cleaned)
        topic_key = classification["topic_key"]
        value = classification["value"]
        is_refinement = any(marker in norm for marker in _REFINEMENT_MARKERS)

        entry = {
            "question": cleaned,
            "topic_key": topic_key,
            "reason": classification["reason"],
        }

        if value == "LOW_VALUE":
            assumptions.append(entry)
            continue
        if value == "DEFERRED":
            deferred.append(entry)
            continue

        # HIGH_VALUE: refinement markers bypass the resolved-topic block.
        if not is_refinement and topic_key and is_topic_resolved(state, topic_key):
            blocked.append({
                "question": cleaned,
                "topic_key": topic_key,
                "reason": f"topic '{topic_key}' already resolved",
            })
            continue
        if len(allowed) >= MAX_HIGH_VALUE_QUESTIONS_PER_TURN:
            deferred.append({
                "question": cleaned,
                "topic_key": topic_key,
                "reason": (
                    "high-value question limit "
                    f"({MAX_HIGH_VALUE_QUESTIONS_PER_TURN}) reached for this turn"
                ),
            })
            continue
        allowed.append(cleaned)
    return {
        "allowed": allowed,
        "blocked": blocked,
        "assumptions": assumptions,
        "deferred": deferred,
    }


async def check_questions(
    owner_id: str,
    project_id: str,
    conversation_id: str,
    questions: List[str],
) -> Dict[str, Any]:
    state = await get_state(owner_id, project_id, conversation_id)
    filtered = filter_repeated_questions(questions, state)
    status = assess_interview_status(state)
    return {
        "questions": [
            redaction.redact_text(q.strip())
            for q in (questions or [])
            if isinstance(q, str) and q.strip()
        ],
        "allowed": filtered["allowed"],
        "blocked": filtered["blocked"],
        "assumptions": filtered["assumptions"],
        "deferred": filtered["deferred"],
        "resolved_topics": state.get("resolved_topics") or [],
        "policy": question_policy(),
        "unresolved_core_topics": status["unresolved_core_topics"],
        "blocking": status["blocking"],
        "stopped": status["stopped"],
        "prd_ready": status["prd_ready"],
        "reason": status["reason"],
    }


# ------------------------------------------------------------------ capture bridge
def _values(state: Dict[str, Any], key: str) -> List[str]:
    out: List[str] = []
    for item in state.get(key) or []:
        value = item.get("value") if isinstance(item, dict) else None
        if isinstance(value, str) and value.strip():
            out.append(value)
    return out


def render_capture_fields(state: Dict[str, Any]) -> Dict[str, List[str]]:
    """Active ledger values for the existing prompt-capture composer.

    Assumptions are returned in their own list and must never be merged into
    ``confirmed_decisions`` or ``constraints`` by callers.
    """
    state = state or {}
    return {
        "confirmed_decisions": _values(state, "confirmed_decisions"),
        "constraints": _values(state, "constraints"),
        "rejected_options": _values(state, "rejected_options"),
        "assumptions": _values(state, "assumptions"),
        "open_questions": _values(state, "open_questions"),
        "verification_items": _values(state, "verification_items"),
    }


async def state_for_capture(
    owner_id: str, project_id: str, conversation_id: str
) -> Dict[str, Any]:
    return await get_state(owner_id, project_id, conversation_id)


# ------------------------------------------------------------------ consolidated plan
def _link_policy(policy: Dict[str, Any], matched_by: str) -> Dict[str, Any]:
    return {
        "id": policy.get("id"),
        "name": redaction.redact_text(policy.get("name") or ""),
        "domain": policy.get("domain"),
        "version": policy.get("version"),
        "matched_by": matched_by,
    }


def _link_invariant(invariant: Dict[str, Any], matched_by: str) -> Dict[str, Any]:
    return {
        "id": invariant.get("id"),
        "code": redaction.redact_text(invariant.get("code") or ""),
        "domain": invariant.get("domain"),
        "severity": invariant.get("severity"),
        "title": redaction.redact_text(invariant.get("title") or ""),
        "matched_by": matched_by,
    }


def _md_value(value: Any) -> str:
    return redaction.redact_text(_clean_value(value))


def _bullets(values: List[str]) -> str:
    if not values:
        return "- (none)"
    return "\n".join(f"- {value}" for value in values)


def _section(title: str, values: List[str]) -> str:
    return f"## {title}\n{_bullets(values)}"


async def consolidated_plan(
    owner_id: str,
    project_id: str,
    conversation_id: str,
    include_policy_ids: Optional[List[str]] = None,
    include_invariant_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    await _require_project(owner_id, project_id)
    state = await get_state(owner_id, project_id, conversation_id)

    categories: set = set()
    for key in _GROUP_KEYS:
        for item in state.get(key) or []:
            category = item.get("category") if isinstance(item, dict) else None
            if category:
                categories.add(category)
    relevant_domains: set = set()
    for category in categories:
        domain = _CATEGORY_POLICY_DOMAINS.get(category)
        if domain:
            relevant_domains.add(domain)

    policies: List[Dict[str, Any]] = []
    try:
        if include_policy_ids:
            seen_policy_ids: set = set()
            for policy_id in include_policy_ids:
                policy = await prompt_assets.get_policy(owner_id, policy_id)
                if policy.get("project_id") != project_id:
                    raise PlanningStateError("Project policy belongs to a different project")
                if policy.get("id") in seen_policy_ids:
                    continue
                seen_policy_ids.add(policy.get("id"))
                policies.append(_link_policy(policy, "explicit"))
        else:
            for policy in await prompt_assets.list_policies(owner_id, project_id):
                if relevant_domains and policy.get("domain") not in relevant_domains:
                    continue
                policies.append(
                    _link_policy(policy, f"domain_match:{policy.get('domain')}")
                )
    except PromptAssetNotFound as exc:
        raise PlanningStateNotFound(str(exc)) from exc
    except PromptAssetError as exc:
        raise PlanningStateError(str(exc)) from exc

    invariants: List[Dict[str, Any]] = []
    try:
        if include_invariant_ids:
            seen_invariant_ids: set = set()
            for invariant_id in include_invariant_ids:
                invariant = await prompt_assets.get_invariant(owner_id, invariant_id)
                if invariant.get("project_id") != project_id:
                    raise PlanningStateError("Project invariant belongs to a different project")
                if invariant.get("id") in seen_invariant_ids:
                    continue
                seen_invariant_ids.add(invariant.get("id"))
                invariants.append(_link_invariant(invariant, "explicit"))
        else:
            for invariant in await prompt_assets.list_invariants(
                owner_id, project_id, status="active"
            ):
                if relevant_domains and invariant.get("domain") not in relevant_domains:
                    continue
                invariants.append(
                    _link_invariant(invariant, f"domain_match:{invariant.get('domain')}")
                )
    except PromptAssetNotFound as exc:
        raise PlanningStateNotFound(str(exc)) from exc
    except PromptAssetError as exc:
        raise PlanningStateError(str(exc)) from exc

    conversation = await repo.conversations.get(conversation_id, owner_id)
    title = ""
    if conversation:
        title = redaction.redact_text(conversation.get("title") or "")

    confirmed = [_md_value(i.get("value")) for i in state.get("confirmed_decisions") or []]
    constraints = [_md_value(i.get("value")) for i in state.get("constraints") or []]
    rejected = [_md_value(i.get("value")) for i in state.get("rejected_options") or []]
    open_questions = [_md_value(i.get("value")) for i in state.get("open_questions") or []]
    assumptions = [_md_value(i.get("value")) for i in state.get("assumptions") or []]
    verification = [_md_value(i.get("value")) for i in state.get("verification_items") or []]

    sections = {
        "objective": ["Authoritative plan derived from the decision ledger."],
        "confirmed_decisions": confirmed,
        "constraints": constraints,
        "rejected_options": rejected,
        "open_questions": open_questions,
        "assumptions": assumptions,
        "verification_items": verification,
        "policies": [p.get("name") or "" for p in policies],
        "invariants": [i.get("code") or "" for i in invariants],
    }

    header = "# Consolidated Plan"
    if title:
        header = f"{header}: {title}"
    body = [
        header,
        _section("Objective", sections["objective"]),
        _section("Confirmed Decisions", confirmed),
        _section("Constraints", constraints),
        _section("Rejected / Out of Scope", rejected),
        _section("Open Questions", open_questions),
        _section("Assumptions (not facts)", assumptions),
        _section("Needs Verification", verification),
        _section("Linked Policies", sections["policies"]),
        _section("Linked Invariants", sections["invariants"]),
    ]
    markdown = "\n\n".join(body)

    return {
        "markdown": markdown,
        "sections": sections,
        "policies": policies,
        "invariants": invariants,
        "state": state,
    }


# ------------------------------------------------------------------ catalog
def catalog() -> Dict[str, Any]:
    return {
        "statuses": list(ITEM_STATUSES),
        "kinds": list(ITEM_KINDS),
        "source_types": list(SOURCE_TYPES),
        "verification_statuses": list(VERIFICATION_STATUSES),
        "conflict_resolutions": list(CONFLICT_RESOLUTIONS),
        "topics": [
            {"key": rule["key"], "category": rule["category"], "label": rule["label"]}
            for rule in _TOPIC_RULES
        ],
    }
