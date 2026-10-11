"""V6.15.8 regression coverage: attendance planning end-to-end + isolation.

This closes the V6.15 loop by exercising the whole chain on one real product
scenario -- ledger (V6.14) -> structured graph (V6.15.1) -> readiness /
Living PRD (V6.15.x) -- and then adversarially probes the security boundaries
that phase introduced:

* an end-to-end multi-company attendance plan: populated sections, decision
  provenance, technical uncertainty that is NEVER promoted to confirmed, PRD
  labelling (rejected / assumption / needs-verification / confirmed), explicit
  per-dimension readiness (no fake percentage), versioning, diff and approval;
* the question policy: cosmetic questions become assumptions and never block
  readiness, high-value questions stay allowed but capped at 3 per turn;
* owner + project isolation across graph / node detail / impact / readiness /
  PRD versions / compare, including manual-id guessing;
* superseded decisions are never resurrected into the active graph or PRD while
  history stays in the ledger;
* universal secret redaction across every derived surface;
* legacy V6.14 projects lazily and idempotently syncing into the graph with no
  destructive reset of the ledger.

Everything runs offline against the shared ``app_client``/``as_user`` fixtures.
Foreign owners are created with ``new_owner_id()`` and purged with
``purge_owner``; no new collections are introduced.
"""
from __future__ import annotations

import json
import types
import uuid
from contextlib import contextmanager

from app import repository as repo
from app.deps import get_current_user
from app.models import READINESS_STATES, new_id, now_iso
from app.routers import workspace as workspace_router
from app.services import planning_state
from app.services import redaction

from tests.conftest import call
from _support import new_owner_id, purge_owner

API = "/api"

# The exact V6.14 mandatory regression sequence.
MANDATORY_SEQUENCE = (
    "I want attendance using phone fingerprint",
    "Android only",
    "one shared company phone",
    "fingerprint only",
    "no GPS",
    "no geofence",
    "no selfie",
    "unlimited employees",
    "HR-assisted enrollment",
    "SaaS limits later",
    "email lateness notification",
    "HR integration API",
    "Google Drive daily backup",
)

MANDATORY_CONFIRMED_TOPICS = (
    "platform.mobile",
    "device.model",
    "auth.method",
    "enrollment.method",
    "scale.employee_limit",
    "notification.email",
    "integration.hr_api",
    "backup.drive",
)

MANDATORY_REJECTED_TOPICS = (
    "location.geofence",
    "biometrics.selfie",
    "platform.ios",
)

# The exact technical-uncertainty claim from the mandatory regression.
TECHNICAL_CLAIM = (
    "We will store employee_id to biometric_id for multiple fingerprints "
    "on one Android phone"
)
ASSUMPTION_LINE = "Assumption: we might use a shared tablet at the entrance"

# Additional user turns (multi-company / HR scope / super admin / device
# whitelist / per-company employee ids / REST / backend). Turns that the
# deterministic extractor cannot map are still part of the captured transcript;
# the corresponding decisions are recorded explicitly below.
EXTRA_TURNS = (
    ("user", "attendance rules: lateness after 09:15 is flagged"),
    ("user", "data retention: keep attendance records for 24 months"),
    ("user", "super admin manages all companies"),
    ("user", "multi-company deployment for several client organizations"),
    ("user", "restrict clock-in to whitelisted hardware identifiers"),
    ("user", "employee IDs are scoped per company"),
    ("user", "expose a REST interface for payroll"),
    ("user", "backend: NestJS + PostgreSQL"),
    ("assistant", ASSUMPTION_LINE),
    ("assistant", TECHNICAL_CLAIM),
)

# Additional confirmed decisions that guarantee every readiness section is
# populated (multi-company, company-scoped HR, super admin, device whitelist,
# per-company employee ids, REST integration, NestJS + PostgreSQL). The final
# row also supersedes the "SaaS limits later" open question so readiness is not
# blocked by an unresolved question.
ADDITIONAL_DECISIONS = (
    ("product.goal", "product",
     "Deliver multi-company attendance tracking for field staff"),
    ("workflows.core", "workflow",
     "Employees clock in by fingerprint at a whitelisted company phone"),
    ("architecture.backend", "architecture", "NestJS + PostgreSQL"),
    ("data.tenant_scope", "data", "Employee IDs are scoped per company"),
    ("admin.hr_scope", "admin",
     "Company-scoped HR administrators for each company"),
    ("admin.super", "admin", "Super admin manages all companies"),
    ("device.whitelist", "device",
     "Only whitelisted company hardware may clock in"),
    ("integration.rest", "integration",
     "Expose a REST integration API for the HR system"),
    ("platform.multi_company", "platform",
     "Multi-company deployment with company-scoped HR data"),
    ("scale.saas_limits", "scale", "SaaS tenant limits are out of scope for v1"),
)

COSMETIC_QUESTIONS = (
    "What favicon should we use?",
    "Which animation style?",
    "Which decorative icons?",
    "What minor spacing and styling?",
    "Which micro-interactions?",
)

HIGH_VALUE_QUESTIONS = (
    "Which mobile platform should we target?",
    "What authentication method should we use?",
    "What data retention policy applies?",
    "What are the attendance business rules?",
    "Which HR integration API should we use?",
)

POPULATED_SECTIONS = (
    "platform", "security", "data", "integrations",
    "authorization", "business_rules",
)

READINESS_DIMENSION_KEYS = {
    "product_goal", "primary_users", "core_workflows",
    "critical_business_rules", "security_boundaries", "data_ownership",
    "integration_requirements", "technical_constraints",
}


# ------------------------------------------------------------------ fixtures
def _seed_conversation(app_client, owner_id, project_id, turns, *,
                       title="Regression source", workspace_mode="plan"):
    conv = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "title": title,
        "system_prompt": "",
        "workspace_mode": workspace_mode,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    call(app_client, repo.conversations.insert, conv)
    msgs = []
    for role, content in turns:
        msg = {
            "id": new_id(),
            "owner_id": owner_id,
            "conversation_id": conv["id"],
            "project_id": project_id,
            "role": role,
            "content": content,
            "created_at": now_iso(),
        }
        call(app_client, repo.messages.insert, msg)
        msgs.append(msg)
    return conv, msgs


@contextmanager
def _foreign_owner(app_client):
    user = {
        "id": new_owner_id(),
        "email": f"pytest-foreign-{uuid.uuid4().hex}@example.com",
        "name": "Foreign",
        "role": "researcher",
        "created_at": now_iso(),
    }
    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield user
    finally:
        app_client.app.dependency_overrides[get_current_user] = original
        call(app_client, purge_owner, user["id"])


def _project(app_client, name="pytest-v615reg"):
    r = app_client.post(
        f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ------------------------------------------------------------------ http helpers
def _ingest(app_client, project_id, conversation_id, message_ids=None):
    return app_client.post(
        f"{API}/planning-state/conversations/{conversation_id}/ingest",
        json={"project_id": project_id, "message_ids": list(message_ids or [])},
    )


def _state(app_client, project_id, conversation_id):
    return app_client.get(
        f"{API}/planning-state",
        params={"project_id": project_id, "conversation_id": conversation_id},
    )


def _create_item(app_client, project_id, conversation_id, **overrides):
    payload = {"project_id": project_id, "conversation_id": conversation_id}
    payload.update(overrides)
    return app_client.post(f"{API}/planning-state/items", json=payload)


def _get_ledger_item(app_client, item_id):
    return app_client.get(f"{API}/planning-state/items/{item_id}")


def _resolve_conflict(app_client, conflict_id, resolution, value=""):
    return app_client.post(
        f"{API}/planning-state/conflicts/{conflict_id}/resolve",
        json={"resolution": resolution, "value": value},
    )


def _check_questions(app_client, project_id, conversation_id, questions):
    return app_client.post(
        f"{API}/planning-state/check-questions",
        json={
            "project_id": project_id,
            "conversation_id": conversation_id,
            "questions": list(questions),
        },
    )


def _sync(app_client, project_id, conversation_id=None):
    payload = {"project_id": project_id}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    return app_client.post(f"{API}/planning-state/graph/sync", json=payload)


def _graph(app_client, project_id, **params):
    query = {"project_id": project_id}
    query.update(params)
    return app_client.get(f"{API}/planning-state/graph", params=query)


def _node(app_client, node_id, project_id=None):
    params = {}
    if project_id is not None:
        params["project_id"] = project_id
    return app_client.get(f"{API}/planning-state/graph/nodes/{node_id}", params=params)


def _impact(app_client, project_id, **overrides):
    payload = {"project_id": project_id}
    payload.update(overrides)
    return app_client.post(f"{API}/planning-state/impact", json=payload)


def _clear_review(app_client, node_id, project_id):
    return app_client.post(
        f"{API}/planning-state/graph/nodes/{node_id}/clear-review",
        json={"project_id": project_id},
    )


def _readiness(app_client, project_id, conversation_id=None):
    params = {"project_id": project_id}
    if conversation_id is not None:
        params["conversation_id"] = conversation_id
    return app_client.get(f"{API}/planning-state/readiness", params=params)


def _generate(app_client, project_id, conversation_id=None):
    payload = {"project_id": project_id}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    return app_client.post(f"{API}/planning-state/prd/generate", json=payload)


def _versions(app_client, project_id):
    return app_client.get(
        f"{API}/planning-state/prd/versions", params={"project_id": project_id}
    )


def _get_version(app_client, version_id, project_id=None):
    params = {"project_id": project_id} if project_id is not None else None
    return app_client.get(
        f"{API}/planning-state/prd/versions/{version_id}", params=params
    )


def _compare(app_client, project_id, from_version_id, to_version_id):
    return app_client.get(
        f"{API}/planning-state/prd/compare",
        params={
            "project_id": project_id,
            "from_version_id": from_version_id,
            "to_version_id": to_version_id,
        },
    )


def _approve(app_client, version_id, project_id):
    return app_client.post(
        f"{API}/planning-state/prd/versions/{version_id}/approve",
        json={"project_id": project_id},
    )


# ------------------------------------------------------------------ data helpers
def _by_decision(nodes, decision_id):
    return [n for n in nodes if n.get("decision_id") == decision_id]


def _by_topic(items, topic_key):
    return [i for i in items if i.get("topic_key") == topic_key]


def _by_value(items, needle):
    return [i for i in items if needle in (i.get("value") or "")]


def _section_of(body, key):
    return next(s for s in body["sections"] if s["key"] == key)


def _section_item_count(section):
    counts = section.get("counts") or {}
    return sum(int(v) for v in counts.values())


def _items(body):
    return [i for s in body["content"]["sections"] for i in s["items"]]


def _assert_no_percentage(payload):
    percent_keys = []

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                if "percent" in str(key).lower():
                    percent_keys.append(f"{path}.{key}")
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(payload)
    assert percent_keys == [], f"percentage fields found: {percent_keys}"


def _seed_attendance_scenario(app_client, owner):
    """Seed the mandatory + extended attendance planning session."""
    pid = _project(app_client, "v615reg-attendance")
    turns = [("user", text) for text in MANDATORY_SEQUENCE] + list(EXTRA_TURNS)
    conv, msgs = _seed_conversation(app_client, owner, pid, turns)
    ingest = _ingest(app_client, pid, conv["id"])
    assert ingest.status_code == 200, ingest.text

    # The mandatory sequence itself contains one genuine contradiction
    # (auth.method: "attendance ... fingerprint" vs "fingerprint only"). A real
    # session settles it before the plan can be considered PRD-ready.
    state = _state(app_client, pid, conv["id"]).json()
    for conflict in state["conflicts"]:
        resolved = _resolve_conflict(app_client, conflict["id"], "accept_new")
        assert resolved.status_code == 200, resolved.text

    created = []
    for topic_key, category, value in ADDITIONAL_DECISIONS:
        r = _create_item(
            app_client, pid, conv["id"],
            topic_key=topic_key, category=category, kind="decision",
            value=value, status="CONFIRMED_BY_USER", source_type="manual",
        )
        assert r.status_code == 200, r.text
        created.append(r.json()["item"])

    return {
        "project_id": pid,
        "conversation": conv,
        "messages": msgs,
        "created": created,
    }


def _make_conflict(app_client, project_id, conversation_id):
    first = _create_item(
        app_client, project_id, conversation_id,
        topic_key="platform.mobile", category="platform", kind="decision",
        value="Android", status="CONFIRMED_BY_USER", source_type="manual",
    )
    assert first.status_code == 200, first.text
    second = _create_item(
        app_client, project_id, conversation_id,
        topic_key="platform.mobile", category="platform", kind="decision",
        value="iOS", status="CONFIRMED_BY_USER", source_type="manual",
    )
    assert second.status_code == 200, second.text
    assert second.json()["conflict"] is not None
    return first.json()["item"], second.json()["item"], second.json()["conflict"]


def _insert_legacy_decision(app_client, owner, pid, conv_id, *,
                            topic_key, category, value):
    """Insert a raw ``planning_decisions`` row, as a pre-V6.15 project would."""
    now = now_iso()
    doc = {
        "id": new_id(),
        "owner_id": owner,
        "project_id": pid,
        "conversation_id": conv_id,
        "topic_key": topic_key,
        "category": category,
        "kind": "decision",
        "value": value,
        "status": "CONFIRMED_BY_USER",
        "source_type": "user",
        "confidence": 0.9,
        "verification_status": "UNVERIFIED",
        "source_message_id": None,
        "provenance": {
            "conversation_id": conv_id,
            "message_id": None,
            "excerpt": value,
            "source_type": "user",
            "recorded_at": now,
        },
        "supersedes_id": None,
        "superseded_by_id": None,
        "conflict_id": None,
        "created_at": now,
        "updated_at": now,
    }
    call(app_client, repo.planning_decisions.insert, doc)
    return doc


def _decision_snapshot(app_client, owner, pid):
    async def _load():
        docs = await repo.planning_decisions.list(
            {"owner_id": owner, "project_id": pid},
            sort_field="created_at", descending=False, limit=2000,
        )
        return sorted(json.dumps(d, sort_keys=True, default=str) for d in docs)
    return call(app_client, _load)


# ================================================================== 1. end-to-end
def test_attendance_planning_ledger_graph_and_verification(app_client, as_user):
    owner = as_user["id"]
    scenario = _seed_attendance_scenario(app_client, owner)
    pid, conv = scenario["project_id"], scenario["conversation"]

    state = _state(app_client, pid, conv["id"]).json()
    confirmed_topics = {i["topic_key"] for i in state["confirmed_decisions"]}
    assert set(MANDATORY_CONFIRMED_TOPICS) <= confirmed_topics
    rejected_topics = {i["topic_key"] for i in state["rejected_options"]}
    assert set(MANDATORY_REJECTED_TOPICS) <= rejected_topics

    # Technical uncertainty stays NEEDS_VERIFICATION in the ledger.
    claims = _by_value(state["verification_items"], "employee_id")
    assert claims, state["verification_items"]
    claim = claims[0]
    assert claim["status"] == "NEEDS_VERIFICATION"
    assert claim["kind"] == "verification_item"
    assert claim["source_type"] == "assistant"
    assert claim["status"] != "CONFIRMED_BY_USER"
    assert not _by_value(state["confirmed_decisions"], "employee_id")

    # The model assumption stays an assumption.
    assumptions = _by_value(state["assumptions"], "shared tablet")
    assert assumptions
    assert assumptions[0]["status"] == "ASSUMED_BY_MODEL"

    # Graph generation: populated sections and decision provenance.
    body = _sync(app_client, pid).json()
    assert body["counts"]["decisions"] >= 1
    for section_key in POPULATED_SECTIONS:
        assert _section_item_count(_section_of(body, section_key)) > 0, section_key

    for node in body["nodes"]:
        if node["type"] in ("ROOT", "SECTION"):
            continue
        assert node["decision_id"], node

    # The technical claim node is NEEDS_VERIFICATION, never confirmed.
    claim_nodes = _by_decision(body["nodes"], claim["id"])
    assert claim_nodes, claim["id"]
    assert claim_nodes[0]["status"] == "NEEDS_VERIFICATION"
    assert claim_nodes[0]["type"] == "REQUIREMENT"
    assert all(
        n["status"] != "CONFIRMED_BY_USER"
        for n in _by_value_nodes(body["nodes"], "employee_id")
    )


def _by_value_nodes(nodes, needle):
    return [n for n in nodes if needle in (n.get("content") or "")]


# ================================================================== 2. readiness
def test_readiness_is_explicit_and_ready_without_blocks(app_client, as_user):
    owner = as_user["id"]
    scenario = _seed_attendance_scenario(app_client, owner)
    pid, conv = scenario["project_id"], scenario["conversation"]

    r = _readiness(app_client, pid, conv["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_no_percentage(body)

    assert {d["key"] for d in body["dimensions"]} == READINESS_DIMENSION_KEYS
    for dimension in body["dimensions"]:
        assert set(dimension) >= {
            "key", "label", "state", "detail", "blockers", "section_key",
            "section_keys", "item_count",
        }
        assert dimension["state"] in READINESS_STATES
        assert isinstance(dimension["blockers"], list)

    assert body["blocker_count"] == 0, body["blockers"]
    assert body["ready"] is True, body
    assert body["summary"] == "Ready to generate PRD"
    assert all(
        d["state"] in ("READY", "NEEDS_VERIFICATION") for d in body["dimensions"]
    )


# ================================================================== 3. cosmetics
def test_cosmetic_questions_do_not_block_readiness(app_client, as_user):
    owner = as_user["id"]
    scenario = _seed_attendance_scenario(app_client, owner)
    pid, conv = scenario["project_id"], scenario["conversation"]

    for question in COSMETIC_QUESTIONS:
        classified = planning_state.classify_question(question)
        assert classified["value"] == "LOW_VALUE", classified
        assert classified["category"] == "cosmetic"

    result = _check_questions(app_client, pid, conv["id"], COSMETIC_QUESTIONS)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["allowed"] == []
    assert body["blocked"] == []
    assert body["deferred"] == []
    assert {e["question"] for e in body["assumptions"]} == set(COSMETIC_QUESTIONS)

    # A cosmetic open question recorded in the ledger must never block readiness.
    cosmetic_topic = planning_state.topic_key_for_question(COSMETIC_QUESTIONS[0])
    created = _create_item(
        app_client, pid, conv["id"],
        topic_key=cosmetic_topic, category="other", kind="open_question",
        value=COSMETIC_QUESTIONS[0], status="NEEDS_VERIFICATION",
        source_type="manual",
    )
    assert created.status_code == 200, created.text

    readiness = _readiness(app_client, pid, conv["id"]).json()
    assert readiness["blocker_count"] == 0, readiness["blockers"]
    assert readiness["ready"] is True, readiness
    assert all(
        "favicon" not in json.dumps(b).lower() for b in readiness["blockers"]
    )


# ================================================================== 4. cap
def test_high_value_questions_capped_with_overflow_deferred(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client, "v615reg-cap")
    conv, _ = _seed_conversation(app_client, owner, pid, [])

    cap = planning_state.MAX_HIGH_VALUE_QUESTIONS_PER_TURN
    assert cap == 3
    result = _check_questions(app_client, pid, conv["id"], HIGH_VALUE_QUESTIONS)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["allowed"] == list(HIGH_VALUE_QUESTIONS[:cap])
    assert [e["question"] for e in body["deferred"]] == list(HIGH_VALUE_QUESTIONS[cap:])
    assert body["blocked"] == []
    assert body["assumptions"] == []
    assert body["policy"]["max_high_value_questions_per_turn"] == cap


# ================================================================== 5. PRD labels
def test_prd_labels_never_promote_rejected_assumed_or_unverified(app_client, as_user):
    owner = as_user["id"]
    scenario = _seed_attendance_scenario(app_client, owner)
    pid, conv = scenario["project_id"], scenario["conversation"]

    state = _state(app_client, pid, conv["id"]).json()
    body = _generate(app_client, pid, conv["id"]).json()
    items = _items(body)
    by_id = {}
    for item in items:
        by_id.setdefault(item["decision_id"], []).append(item)

    # Rejected options are labelled, never presented as confirmed requirements.
    rejected = state["rejected_options"]
    assert rejected
    for item in rejected:
        matched = by_id.get(item["id"]) or []
        assert matched, item
        assert all(m["evidence_class"] == "REJECTED" for m in matched)
        assert all(m["status"] == "REJECTED" for m in matched)
        for confirmed_item in items:
            if confirmed_item["evidence_class"] == "CONFIRMED_FACT":
                assert item["value"] not in confirmed_item["text"]
    assert "Out of scope / rejected:" in body["markdown"]

    # Assumptions stay assumptions.
    assumption = _by_value(state["assumptions"], "shared tablet")[0]
    matched = by_id[assumption["id"]]
    assert all(m["evidence_class"] == "ASSUMPTION" for m in matched)
    assert all(m["evidence_class"] != "CONFIRMED_FACT" for m in matched)
    assert "Assumption (not confirmed)" in body["markdown"]

    # Needs-verification stays visible and un-promoted.
    claim = _by_value(state["verification_items"], "employee_id")[0]
    matched = by_id[claim["id"]]
    assert matched
    assert all(m["evidence_class"] == "NEEDS_VERIFICATION" for m in matched)
    assert all(m["status"] == "NEEDS_VERIFICATION" for m in matched)
    assert all(m["evidence_class"] != "CONFIRMED_FACT" for m in matched)
    assert "Needs verification:" in body["markdown"]

    # Confirmed decisions appear as confirmed facts.
    confirmed = _by_topic(state["confirmed_decisions"], "platform.mobile")[0]
    matched = by_id[confirmed["id"]]
    assert matched
    assert all(m["evidence_class"] == "CONFIRMED_FACT" for m in matched)


# ================================================================== 6. versioning
def test_prd_versioning_compare_and_approval(app_client, as_user):
    owner = as_user["id"]
    scenario = _seed_attendance_scenario(app_client, owner)
    pid, conv = scenario["project_id"], scenario["conversation"]

    v1 = _generate(app_client, pid, conv["id"]).json()
    assert v1["version"] == 1
    assert v1["status"] == "DRAFT"

    added = _create_item(
        app_client, pid, conv["id"], topic_key="ux.theme", category="ux",
        kind="decision", value="Dark theme by default for night shifts",
        status="CONFIRMED_BY_USER", source_type="manual",
    )
    assert added.status_code == 200, added.text
    v2 = _generate(app_client, pid, conv["id"]).json()
    assert v2["version"] == 2
    assert v2["content_hash"] != v1["content_hash"]

    versions = _versions(app_client, pid).json()
    assert versions["latest_version"] == 2
    assert [v["version"] for v in versions["versions"]] == [2, 1]

    diff = _compare(app_client, pid, v1["id"], v2["id"]).json()
    by_key = {s["key"]: s for s in diff["sections"]}
    assert by_key["ux"]["change"] == "added"
    assert diff["affected_sections"], diff
    affected_keys = {s["key"] for s in diff["affected_sections"]}
    assert "ux" in affected_keys
    assert diff["summary"]["sections_added"] >= 1

    approved = _approve(app_client, v2["id"], pid)
    assert approved.status_code == 200, approved.text
    approved_body = approved.json()
    assert approved_body["status"] == "APPROVED"
    assert approved_body["approved_at"]
    # APPROVED PRD is a document review, explicitly NOT a certification.
    assert "certified" not in approved_body
    assert "certification_id" not in approved_body
    candidates = call(
        app_client, repo.certification_candidates.count, {"owner_id": owner}
    )
    assert candidates == 0


# ================================================================== 7. plan prompt
def test_plan_mode_prompt_reflects_readiness_and_stop_interviewing(app_client, as_user):
    owner = as_user["id"]
    scenario = _seed_attendance_scenario(app_client, owner)
    conv = scenario["conversation"]

    plan_prompt = call(
        app_client,
        workspace_router._effective_system_prompt,
        owner,
        conv,
        types.SimpleNamespace(workspace_mode="plan"),
        "base",
        conv["id"],
    )
    assert isinstance(plan_prompt, str)
    assert "WORKSPACE MODE: PLAN" in plan_prompt
    assert "Ask at most 3 high-value questions" in plan_prompt
    assert "STOP interviewing" in plan_prompt
    assert "PRD READINESS" in plan_prompt
    assert "Ready to generate" in plan_prompt
    assert "Never ask about a topic that is already decided" in plan_prompt
    assert "ASSUMED_BY_MODEL" in plan_prompt

    chat_prompt = call(
        app_client,
        workspace_router._effective_system_prompt,
        owner,
        conv,
        types.SimpleNamespace(workspace_mode="chat"),
        "base",
        conv["id"],
    )
    assert chat_prompt == "base"
    assert "Ask at most 3 high-value questions" not in chat_prompt


# ================================================================== 8. isolation
def test_project_isolation_same_owner(app_client, as_user):
    owner = as_user["id"]
    pid_a = _project(app_client, "v615reg-a")
    pid_b = _project(app_client, "v615reg-b")

    conv_a, _ = _seed_conversation(app_client, owner, pid_a, [])
    item_a = _create_item(
        app_client, pid_a, conv_a["id"], topic_key="platform.mobile",
        category="platform", kind="decision", value="Android",
        status="CONFIRMED_BY_USER", source_type="manual",
    ).json()["item"]
    node_a = _by_decision(_sync(app_client, pid_a).json()["nodes"], item_a["id"])[0]
    v_a = _generate(app_client, pid_a, conv_a["id"]).json()

    conv_b, _ = _seed_conversation(app_client, owner, pid_b, [])
    v_b = _generate(app_client, pid_b, conv_b["id"]).json()

    # Project B's graph never contains project A's decisions.
    graph_b = _graph(app_client, pid_b).json()
    assert _by_decision(graph_b["nodes"], item_a["id"]) == []
    assert graph_b["counts"]["decisions"] == 0

    # Project B's readiness never reports project A's items.
    readiness_b = _readiness(app_client, pid_b, conv_b["id"]).json()
    assert all(d["item_count"] == 0 for d in readiness_b["dimensions"])
    assert readiness_b["blocker_count"] == 0

    # PRD versions and compare stay inside one project.
    assert _versions(app_client, pid_b).json()["versions"][0]["id"] == v_b["id"]
    assert _compare(app_client, pid_b, v_a["id"], v_a["id"]).status_code == 404
    assert _compare(app_client, pid_a, v_a["id"], v_b["id"]).status_code == 404

    # Version detail must not resolve across same-owner projects.
    assert _get_version(app_client, v_a["id"], project_id=pid_a).status_code == 200
    assert _get_version(app_client, v_a["id"], project_id=pid_b).status_code == 404
    assert _approve(app_client, v_a["id"], pid_b).status_code == 404

    # Node detail is project-scoped when a project is supplied.
    assert _node(app_client, node_a["id"], project_id=pid_a).status_code == 200
    assert _node(app_client, node_a["id"], project_id=pid_b).status_code == 404


def test_foreign_owner_isolation(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client, "v615reg-owner")
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    item = _create_item(
        app_client, pid, conv["id"], topic_key="platform.mobile",
        category="platform", kind="decision", value="Android",
        status="CONFIRMED_BY_USER", source_type="manual",
    ).json()["item"]
    node = _by_decision(_sync(app_client, pid).json()["nodes"], item["id"])[0]
    v1 = _generate(app_client, pid, conv["id"]).json()

    with _foreign_owner(app_client):
        assert _graph(app_client, pid).status_code == 404
        assert _node(app_client, node["id"]).status_code == 404
        assert _impact(app_client, pid, decision_id=item["id"]).status_code == 404
        assert _impact(app_client, pid, node_id=node["id"]).status_code == 404
        assert _readiness(app_client, pid, conv["id"]).status_code == 404
        assert _versions(app_client, pid).status_code == 404
        assert _get_version(app_client, v1["id"]).status_code == 404
        assert _compare(app_client, pid, v1["id"], v1["id"]).status_code == 404
        assert _approve(app_client, v1["id"], pid).status_code == 404
        assert _clear_review(app_client, node["id"], pid).status_code == 404


def test_impact_cannot_cross_projects_or_owners(app_client, as_user):
    owner = as_user["id"]
    pid_a = _project(app_client, "v615reg-imp-a")
    pid_b = _project(app_client, "v615reg-imp-b")
    conv_a, _ = _seed_conversation(app_client, owner, pid_a, [])
    item_a = _create_item(
        app_client, pid_a, conv_a["id"], topic_key="auth.method", category="auth",
        kind="decision", value="fingerprint only",
        status="CONFIRMED_BY_USER", source_type="manual",
    ).json()["item"]
    node_a = _by_decision(_sync(app_client, pid_a).json()["nodes"], item_a["id"])[0]
    conv_b, _ = _seed_conversation(app_client, owner, pid_b, [])
    _sync(app_client, pid_b)

    before = _graph(app_client, pid_a).json()["counts"]["review_required"]

    # Same owner, wrong project: the decision/node ids must not resolve.
    assert _impact(app_client, pid_b, decision_id=item_a["id"]).status_code == 404
    assert _impact(app_client, pid_b, node_id=node_a["id"]).status_code == 404

    # Foreign owner: the whole project is invisible.
    with _foreign_owner(app_client):
        assert _impact(app_client, pid_a, decision_id=item_a["id"]).status_code == 404
        assert _impact(app_client, pid_a, node_id=node_a["id"]).status_code == 404

    after = _graph(app_client, pid_a).json()["counts"]["review_required"]
    assert after == before
    assert _section_of(_graph(app_client, pid_a).json(), "auth")["review_required"] is False
    assert _get_ledger_item(app_client, item_a["id"]).json()["status"] == "CONFIRMED_BY_USER"


def test_manual_ids_cannot_bypass_ownership(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client, "v615reg-ids")
    other = _project(app_client, "v615reg-ids-other")
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    item = _create_item(
        app_client, pid, conv["id"], topic_key="platform.mobile",
        category="platform", kind="decision", value="Android",
        status="CONFIRMED_BY_USER", source_type="manual",
    ).json()["item"]
    node = _by_decision(_sync(app_client, pid).json()["nodes"], item["id"])[0]
    v1 = _generate(app_client, pid, conv["id"]).json()

    # The owner can still read its own node detail.
    assert _node(app_client, node["id"]).status_code == 200

    with _foreign_owner(app_client):
        assert _node(app_client, node["id"]).status_code == 404
        assert _get_version(app_client, v1["id"]).status_code == 404
        assert _clear_review(app_client, node["id"], pid).status_code == 404
        assert _impact(app_client, pid, node_id=node["id"]).status_code == 404
        assert _impact(app_client, pid, decision_id=item["id"]).status_code == 404
        assert _approve(app_client, v1["id"], pid).status_code == 404

    # Same owner, wrong project still cannot act on the id.
    assert _get_version(app_client, v1["id"], project_id=pid).status_code == 200
    assert _get_version(app_client, v1["id"], project_id=other).status_code == 404
    assert _clear_review(app_client, node["id"], other).status_code == 404
    assert _approve(app_client, v1["id"], other).status_code == 404
    assert _compare(app_client, other, v1["id"], v1["id"]).status_code == 404


# ================================================================== 9. supersession
def test_superseded_decision_not_resurrected(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client, "v615reg-super")
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    previous, new, conflict = _make_conflict(app_client, pid, conv["id"])

    assert _sync(app_client, pid).status_code == 200
    assert _resolve_conflict(app_client, conflict["id"], "accept_new").status_code == 200
    body = _sync(app_client, pid).json()

    stale = _by_decision(body["nodes"], previous["id"])
    assert stale and all(n["status"] == "SUPERSEDED" for n in stale)
    active = _by_decision(body["nodes"], new["id"])
    assert active and active[0]["status"] == "CONFIRMED_BY_USER"

    # Ledger history is retained, not deleted.
    old = _get_ledger_item(app_client, previous["id"]).json()
    assert old["status"] == "SUPERSEDED"
    assert old["superseded_by_id"] == new["id"]
    state = _state(app_client, pid, conv["id"]).json()
    assert any(i["id"] == previous["id"] for i in state["superseded"])
    assert all(i["value"] != "Android" for i in state["confirmed_decisions"])

    # The compiled PRD never presents the superseded value as authoritative.
    prd = _generate(app_client, pid, conv["id"]).json()
    items = _items(prd)
    assert all(
        i["evidence_class"] != "CONFIRMED_FACT"
        for i in items if i["decision_id"] == previous["id"]
    )
    assert any(
        i["decision_id"] == new["id"] and i["evidence_class"] == "CONFIRMED_FACT"
        for i in items
    )
    assert "Android" not in [
        i["text"] for i in items if i["evidence_class"] == "CONFIRMED_FACT"
    ]

    history = _node(app_client, active[0]["id"]).json()["history"]
    entry = next(h for h in history if h["decision_id"] == previous["id"])
    assert entry["status"] == "SUPERSEDED"


# ================================================================== 10. redaction
def test_secret_redaction_across_graph_readiness_and_prd(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client, "v615reg-secret")
    secret = "sk-" + "A" * 24
    conv, _ = _seed_conversation(app_client, owner, pid, [
        ("assistant", f"{TECHNICAL_CLAIM} using token {secret}"),
    ])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    created = _create_item(
        app_client, pid, conv["id"], topic_key="integration.credentials",
        category="integration", kind="decision",
        value=f"Provider credential api_key={secret} for the HR backend",
        status="CONFIRMED_BY_USER", source_type="manual",
    )
    assert created.status_code == 200, created.text

    graph = _sync(app_client, pid).json()
    node = _by_decision(
        _graph(app_client, pid).json()["nodes"], created.json()["item"]["id"]
    )[0]
    detail = _node(app_client, node["id"]).json()
    readiness = _readiness(app_client, pid, conv["id"]).json()
    prd = _generate(app_client, pid, conv["id"]).json()

    blobs = {
        "graph_nodes": json.dumps(graph["nodes"]),
        "graph_edges": json.dumps(graph["edges"]),
        "node_detail": json.dumps(detail),
        "readiness": json.dumps(readiness),
        "prd_markdown": prd["markdown"],
        "prd_content": json.dumps(prd["content"]),
    }
    for name, blob in blobs.items():
        assert secret not in blob, f"secret leaked in {name}"
        assert redaction.contains_secret(blob) is False, f"secret-shaped value in {name}"

    assert "[REDACTED]" in json.dumps(graph["nodes"])
    assert "[REDACTED]" in prd["markdown"]


# ================================================================== 11. legacy compat
def test_old_v614_project_lazy_sync_compatibility(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client, "v615reg-legacy")
    conv, _ = _seed_conversation(app_client, owner, pid, [])

    dimensions = (
        ("product", "product.goal", "Deliver attendance tracking"),
        ("actors", "actors.primary", "HR administrators and employees"),
        ("workflows", "workflows.core", "Fingerprint clock-in at a shared tablet"),
        ("business_rules", "attendance.rules", "Lateness after 09:15 is flagged"),
        ("security", "security.biometrics", "Map employee_id to biometric_id"),
        ("data", "data.retention", "Keep attendance records for 24 months"),
        ("integrations", "integration.hr_api", "Sync daily with the HR API"),
        ("platform", "platform.mobile", "Android only"),
    )
    for category, topic_key, value in dimensions:
        _insert_legacy_decision(
            app_client, owner, pid, conv["id"],
            topic_key=topic_key, category=category, value=value,
        )

    before = _decision_snapshot(app_client, owner, pid)
    assert before
    # A legacy project has never had a graph built.
    nodes_before = call(
        app_client, repo.planning_nodes.list,
        {"owner_id": owner, "project_id": pid},
    )
    assert nodes_before == []

    # Readiness / graph / node detail lazily and idempotently sync.
    readiness = _readiness(app_client, pid, conv["id"])
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["ready"] is True, readiness.json()

    first = _sync(app_client, pid)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["counts"]["decisions"] == len(dimensions)
    decision_node = next(n for n in body["nodes"] if n["decision_id"])

    detail = _node(app_client, decision_node["id"])
    assert detail.status_code == 200, detail.text

    v1 = _generate(app_client, pid, conv["id"])
    assert v1.status_code == 200, v1.text
    assert v1.json()["version"] == 1
    assert _versions(app_client, pid).json()["latest_version"] == 1

    # The lazy sync is additive: the ledger rows are untouched.
    assert _decision_snapshot(app_client, owner, pid) == before

    # Adding a decision produces a comparable next version.
    extra = _insert_legacy_decision(
        app_client, owner, pid, conv["id"],
        topic_key="ux.theme", category="ux", value="Dark theme by default",
    )
    v2 = _generate(app_client, pid, conv["id"]).json()
    assert v2["version"] == 2
    diff = _compare(app_client, pid, v1.json()["id"], v2["id"])
    assert diff.status_code == 200, diff.text
    assert diff.json()["affected_sections"]

    after = _decision_snapshot(app_client, owner, pid)
    assert len(after) == len(before) + 1
    assert any(extra["id"] in row for row in after)

    # Repeated sync is idempotent: same counts, same node ids, ledger unchanged.
    baseline = _sync(app_client, pid).json()
    second = _sync(app_client, pid).json()
    assert second["counts"] == baseline["counts"]
    assert {n["id"] for n in second["nodes"]} == {n["id"] for n in baseline["nodes"]}
    assert _decision_snapshot(app_client, owner, pid) == after
