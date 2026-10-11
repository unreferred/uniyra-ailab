"""V6.15 Planning Question Policy tests.

Covers the deterministic, LLM-free question policy layered on top of the V6.14
Planning Decision Memory ledger: HIGH_VALUE / LOW_VALUE / DEFERRED
classification, the per-turn high-value question cap, cosmetic questions being
auto-assumed rather than asked, architecture/security blockers being allowed,
the "stop interviewing / PRD ready" signal, and the PLAN-mode system prompt
guidance. Pure functions are exercised directly and through the existing
``POST /planning-state/check-questions`` surface, with the V6.14 behavior kept
intact.

Everything runs offline against the shared ``app_client``/``as_user`` fixtures:
conversations and messages are seeded directly through ``repo`` for throwaway
``pytest-`` owners that are purged after each test.
"""
from __future__ import annotations

import types
import uuid

from app import repository as repo
from app.models import new_id, now_iso
from app.routers import workspace as workspace_router
from app.services import planning_state
from app.services import workspace_modes

from tests.conftest import call

API = "/api"

CORE_TOPICS = list(planning_state.CORE_PLANNING_TOPICS)


# ------------------------------------------------------------------ fixtures
def _seed_conversation(app_client, owner_id, project_id, turns, *,
                       title="Question policy", workspace_mode="plan"):
    """Insert a Plan-mode conversation plus its messages directly via ``repo``."""
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


def _project(app_client, name="pytest-v615qp"):
    r = app_client.post(
        f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ------------------------------------------------------------------ planning helpers
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


def _check_questions(app_client, project_id, conversation_id, questions):
    return app_client.post(
        f"{API}/planning-state/check-questions",
        json={
            "project_id": project_id,
            "conversation_id": conversation_id,
            "questions": list(questions),
        },
    )


def _create_item(app_client, project_id, conversation_id, **overrides):
    payload = {"project_id": project_id, "conversation_id": conversation_id}
    payload.update(overrides)
    return app_client.post(f"{API}/planning-state/items", json=payload)


def _cover_all_core_topics(app_client, project_id, conversation_id):
    """Confirm a decision for every core topic so no topic is unresolved."""
    for index, topic_key in enumerate(CORE_TOPICS):
        created = _create_item(
            app_client, project_id, conversation_id,
            topic_key=topic_key, category="other", kind="decision",
            value=f"decided-{index}", status="CONFIRMED_BY_USER",
            source_type="manual",
        )
        assert created.status_code == 200, created.text


# ================================================================== A. resolved topic
def test_confirmed_decision_not_asked_again(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [("user", "Android only")])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    result = _check_questions(app_client, pid, conv["id"], ["Android or iOS?"])
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["allowed"] == []
    assert len(body["blocked"]) == 1
    assert body["blocked"][0]["topic_key"] == "platform.mobile"
    assert body["blocked"][0]["reason"]

    # The pure function mirrors the HTTP behavior.
    state = _state(app_client, pid, conv["id"]).json()
    filtered = planning_state.filter_repeated_questions(["Android or iOS?"], state)
    assert filtered["allowed"] == []
    assert filtered["blocked"][0]["topic_key"] == "platform.mobile"


# ================================================================== B. cosmetic
def test_cosmetic_questions_are_assumed_not_asked(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])

    questions = ["What favicon should we use?", "Which animation style?"]
    result = _check_questions(app_client, pid, conv["id"], questions)
    assert result.status_code == 200, result.text
    body = result.json()

    assert body["allowed"] == []
    assert body["blocked"] == []
    assert body["deferred"] == []
    assumed = {entry["question"] for entry in body["assumptions"]}
    assert assumed == set(questions)
    assert all(entry["reason"] for entry in body["assumptions"])

    for question in questions:
        classified = planning_state.classify_question(question)
        assert classified["value"] == "LOW_VALUE"
        assert classified["category"] == "cosmetic"


# ================================================================== C. architecture
def test_architecture_blocker_is_allowed(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])

    question = "Which backend and database should we use?"
    assert planning_state.classify_question(question)["value"] == "HIGH_VALUE"

    result = _check_questions(app_client, pid, conv["id"], [question])
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["allowed"] == [question]
    assert body["blocked"] == []
    assert body["deferred"] == []


# ================================================================== D. security
def test_security_blocker_is_allowed(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])

    question = "Do we need GPS geofencing?"
    classified = planning_state.classify_question(question)
    assert classified["value"] == "HIGH_VALUE"
    assert classified["topic_key"] == "location.geofence"

    result = _check_questions(app_client, pid, conv["id"], [question])
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["allowed"] == [question]
    assert body["blocked"] == []


# ================================================================== E. stop signal
def test_no_blockers_stops_interview_and_prd_ready(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    _cover_all_core_topics(app_client, pid, conv["id"])

    state = _state(app_client, pid, conv["id"]).json()
    status = planning_state.assess_interview_status(state)
    assert status["unresolved_core_topics"] == []
    assert status["blocking"] == []
    assert status["stopped"] is True
    assert status["prd_ready"] is True
    assert status["reason"]

    result = _check_questions(app_client, pid, conv["id"], [])
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["stopped"] is True
    assert body["prd_ready"] is True
    assert body["unresolved_core_topics"] == []
    assert body["blocking"] == []


def test_open_question_on_core_topic_blocks(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    _cover_all_core_topics(app_client, pid, conv["id"])
    # Reopen one core topic: it becomes an open question but is still covered.
    state = _state(app_client, pid, conv["id"]).json()
    item_id = next(
        item["id"] for item in state["confirmed_decisions"]
        if item["topic_key"] == "attendance.rules"
    )
    reopened = app_client.post(f"{API}/planning-state/items/{item_id}/reopen")
    assert reopened.status_code == 200, reopened.text

    state = _state(app_client, pid, conv["id"]).json()
    status = planning_state.assess_interview_status(state)
    assert status["unresolved_core_topics"] == []
    assert any(entry["topic_key"] == "attendance.rules"
               for entry in status["blocking"])
    assert status["stopped"] is False
    assert status["prd_ready"] is False


# ================================================================== F. cap
def test_high_value_questions_capped_per_turn(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])

    questions = [
        "Which mobile platform should we target?",
        "What authentication method should we use?",
        "What data retention policy applies?",
        "What are the attendance business rules?",
        "Which HR integration API should we use?",
    ]
    result = _check_questions(app_client, pid, conv["id"], questions)
    assert result.status_code == 200, result.text
    body = result.json()

    assert body["allowed"] == questions[:3]
    assert len(body["allowed"]) == planning_state.MAX_HIGH_VALUE_QUESTIONS_PER_TURN
    assert [entry["question"] for entry in body["deferred"]] == questions[3:]
    assert all(entry["topic_key"] for entry in body["deferred"])
    assert body["blocked"] == []
    assert body["assumptions"] == []


# ================================================================== G. pure functions
def test_classify_question_and_assess_interview_status_pure():
    # classify_question never raises on odd input and returns the full shape.
    for raw in (None, "", 12345, ["x"], {"a": 1}, object()):
        classified = planning_state.classify_question(raw)
        assert set(classified) == {
            "question", "topic_key", "value", "category", "reason",
        }
        assert classified["value"] == "DEFERRED"
        assert classified["reason"]

    assert planning_state.classify_question(
        "What favicon should we use?")["value"] == "LOW_VALUE"
    assert planning_state.classify_question(
        "Which animation style?")["value"] == "LOW_VALUE"
    assert planning_state.classify_question(
        "Do we need GPS geofencing?") == {
        "question": "Do we need GPS geofencing?",
        "topic_key": "location.geofence",
        "value": "HIGH_VALUE",
        "category": "location",
        "reason": "materially affects the plan (location)",
    }

    # assess_interview_status tolerates missing/odd state and never raises.
    for raw in (None, {}, "nonsense", [], {"conflicts": None},
                {"resolved_topics": None}, {"open_questions": [None, 7]}):
        status = planning_state.assess_interview_status(raw)
        assert set(status) == {
            "covered_topics", "unresolved_core_topics", "blocking",
            "stopped", "prd_ready", "reason",
        }
        assert isinstance(status["blocking"], list)

    empty = planning_state.assess_interview_status({})
    assert empty["stopped"] is False
    assert empty["unresolved_core_topics"] == CORE_TOPICS

    # An ASSUMED_BY_MODEL assumption and a generic verification item are
    # covered/ignored, never treated as blockers.
    state = {
        "assumptions": [{
            "topic_key": "auth.method", "kind": "assumption",
            "status": "ASSUMED_BY_MODEL", "value": "assume biometric",
        }],
        "verification_items": [{
            "topic_key": "generic.some.thing", "kind": "verification_item",
            "status": "NEEDS_VERIFICATION", "value": "generic claim",
        }],
        "open_questions": [], "conflicts": [], "resolved_topics": [],
    }
    status = planning_state.assess_interview_status(state)
    assert "auth.method" in status["covered_topics"]
    assert "auth.method" not in status["unresolved_core_topics"]
    assert status["blocking"] == []

    # An open conflict is a blocker.
    conflicted = planning_state.assess_interview_status({
        "conflicts": [{"status": "open", "topic_key": "platform.mobile"}],
    })
    assert conflicted["stopped"] is False
    assert any(entry["kind"] == "conflict" for entry in conflicted["blocking"])


# ================================================================== H. V6.14 intact
def test_v614_resolved_topic_behavior_intact(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [("user", "Android only")])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    body = _check_questions(app_client, pid, conv["id"], ["Android or iOS?"]).json()
    # Existing keys are unchanged and newly added keys do not alter semantics.
    assert body["allowed"] == []
    assert body["blocked"][0]["topic_key"] == "platform.mobile"
    assert "platform.mobile" in body["resolved_topics"]
    assert "assumptions" in body and "deferred" in body
    assert body["policy"]["max_high_value_questions_per_turn"] == (
        planning_state.MAX_HIGH_VALUE_QUESTIONS_PER_TURN
    )
    assert "auth" in body["policy"]["high_value_categories"]
    assert "favicon" in body["policy"]["low_value_topics"]
    assert len(body["policy"]["low_value_topics"]) <= 20


# ================================================================== plan prompt
def test_plan_prompt_contains_question_policy_chat_unchanged(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [("user", "Android only")])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

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
    assert "ASSUMED_BY_MODEL" in plan_prompt
    assert "STOP interviewing" in plan_prompt
    assert "WORKSPACE DECISION STATE" in plan_prompt
    assert "INTERVIEW STATUS" in plan_prompt

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
    assert "WORKSPACE MODE: PLAN" not in chat_prompt

    # compose_system_prompt: chat unchanged; plan carries the policy scaffold.
    assert workspace_modes.compose_system_prompt("chat", "base", "BLOCK") == "base"
    composed = workspace_modes.compose_system_prompt("plan", "base", "BLOCK")
    assert composed.startswith("base")
    assert "Ask at most 3 high-value questions" in composed
    assert "STOP interviewing" in composed
    assert composed.rstrip().endswith("BLOCK")
