"""V6.14 Planning Decision Memory API / integration tests.

Covers the deterministic, LLM-free per-conversation decision ledger behind
Workspace Plan mode: extraction into CONFIRMED_BY_USER / REJECTED /
ASSUMED_BY_MODEL / NEEDS_VERIFICATION rows, conflict creation and resolution,
supersession, question de-duplication, the plan-mode system-prompt injection,
the prompt-capture bridge, universal secret redaction, and owner/project
isolation.

Everything runs offline against the shared ``app_client``/``as_user`` fixtures:
conversations and messages are seeded directly through ``repo`` for throwaway
``pytest-`` owners that are purged after each test, and every foreign owner used
for isolation is created with ``new_owner_id()`` and purged again.
"""
from __future__ import annotations

import json
import types
import uuid
from contextlib import contextmanager

from app import providers as providers_module
from app import repository as repo
from app import services
from app.deps import get_current_user
from app.models import (
    PLANNING_CONFLICT_RESOLUTIONS,
    PLANNING_CONFLICT_STATUSES,
    PLANNING_ITEM_KINDS,
    PLANNING_ITEM_STATUSES,
    PLANNING_SOURCE_TYPES,
    PLANNING_VERIFICATION_STATUSES,
    new_id,
    now_iso,
)
from app.routers import workspace as workspace_router
from app.services import execution as execution_module
from app.services import planning_state
from app.services import prompt_capture
from app.services import workspace_modes

from tests.conftest import call
from _support import (
    CapableAdapter,
    create_model,
    create_provider,
    new_owner_id,
    purge_owner,
    result_success,
)

API = "/api"

# The exact user-turn sequence from the mandatory regression: a compact product
# planning session that resolves eleven distinct topics.
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


# ------------------------------------------------------------------ fixtures
def _seed_conversation(
    app_client, owner_id, project_id, turns, *,
    title="Decision source", workspace_mode="plan",
):
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


@contextmanager
def _foreign_owner(app_client):
    """Authenticate as a throwaway second owner, purging it afterwards."""
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


def _project(app_client, name="pytest-v614dm"):
    r = app_client.post(
        f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _block(app_client, project_id, **overrides):
    payload = {
        "project_id": project_id,
        "name": f"block-{uuid.uuid4().hex[:8]}",
        "category": "other",
        "content": "body",
    }
    payload.update(overrides)
    r = app_client.post(f"{API}/prompt-blocks", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _policy(app_client, project_id, **overrides):
    payload = {
        "project_id": project_id,
        "name": f"policy-{uuid.uuid4().hex[:8]}",
        "domain": "SECURITY",
        "content": "rule",
    }
    payload.update(overrides)
    r = app_client.post(f"{API}/project-policies", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _invariant(app_client, project_id, **overrides):
    payload = {
        "project_id": project_id,
        "code": f"CODE-{uuid.uuid4().hex[:6]}",
        "domain": "SECURITY",
        "title": "Invariant",
    }
    payload.update(overrides)
    r = app_client.post(f"{API}/project-invariants", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _make_model(app_client, owner):
    async def _setup():
        provider_id = await create_provider(owner)
        return await create_model(
            owner,
            provider_id=provider_id,
            display_name=f"pytest-model-{uuid.uuid4().hex[:6]}",
            model_identifier=f"pytest/{uuid.uuid4().hex}",
        )

    return call(app_client, _setup)


def _block_ids(app_client, project_id):
    r = app_client.get(f"{API}/prompt-blocks", params={"project_id": project_id})
    assert r.status_code == 200, r.text
    return [item["id"] for item in r.json()]


def _policy_ids(app_client, project_id):
    r = app_client.get(f"{API}/project-policies", params={"project_id": project_id})
    assert r.status_code == 200, r.text
    return [item["id"] for item in r.json()]


def _invariant_ids(app_client, project_id):
    r = app_client.get(f"{API}/project-invariants", params={"project_id": project_id})
    assert r.status_code == 200, r.text
    return [item["id"] for item in r.json()]


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


def _consolidated(app_client, project_id, conversation_id, **overrides):
    payload = {"project_id": project_id}
    payload.update(overrides)
    return app_client.post(
        f"{API}/planning-state/conversations/{conversation_id}/consolidated-plan",
        json=payload,
    )


def _create_item(app_client, project_id, conversation_id, **overrides):
    payload = {"project_id": project_id, "conversation_id": conversation_id}
    payload.update(overrides)
    return app_client.post(f"{API}/planning-state/items", json=payload)


def _get_item(app_client, item_id):
    return app_client.get(f"{API}/planning-state/items/{item_id}")


def _patch_item(app_client, item_id, **overrides):
    return app_client.patch(f"{API}/planning-state/items/{item_id}", json=overrides)


def _reopen_item(app_client, item_id):
    return app_client.post(f"{API}/planning-state/items/{item_id}/reopen")


def _verify_item(app_client, item_id, verification_status, evidence=""):
    return app_client.post(
        f"{API}/planning-state/items/{item_id}/verify",
        json={"verification_status": verification_status, "evidence": evidence},
    )


def _list_conflicts(app_client, project_id, conversation_id, status=None):
    params = {"project_id": project_id, "conversation_id": conversation_id}
    if status is not None:
        params["status"] = status
    return app_client.get(f"{API}/planning-state/conflicts", params=params)


def _resolve_conflict(app_client, conflict_id, resolution, value=""):
    return app_client.post(
        f"{API}/planning-state/conflicts/{conflict_id}/resolve",
        json={"resolution": resolution, "value": value},
    )


def _analyze(app_client, project_id, conversation_id, **overrides):
    payload = {"project_id": project_id, "conversation_id": conversation_id}
    payload.update(overrides)
    return app_client.post(f"{API}/prompt-capture/analyze", json=payload)


def _save(app_client, project_id, conversation_id, **overrides):
    payload = {"project_id": project_id, "conversation_id": conversation_id}
    payload.update(overrides)
    return app_client.post(f"{API}/prompt-capture/save", json=payload)


def _install(monkeypatch, executor, *, tools=False, vision=False, streamer=None):
    factory = lambda provider: CapableAdapter(  # noqa: E731
        executor, streamer, tools=tools, vision=vision
    )
    monkeypatch.setattr(services, "get_adapter", factory)
    monkeypatch.setattr(execution_module, "get_adapter", factory)
    monkeypatch.setattr(providers_module, "get_adapter", factory)


def _topic_keys(items):
    return {item["topic_key"] for item in items}


def _values(items):
    return {item["value"] for item in items}


def _by_topic(items, topic_key):
    return [item for item in items if item["topic_key"] == topic_key]


def _make_conflict(app_client, project_id, conversation_id):
    """Confirm Android, then contradict with iOS -> one open conflict."""
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


# ================================================================== catalog
def test_catalog_exposes_vocabularies_and_topics(app_client, as_user):
    r = app_client.get(f"{API}/planning-state/catalog")
    assert r.status_code == 200, r.text
    body = r.json()

    assert set(body["statuses"]) == set(PLANNING_ITEM_STATUSES)
    assert set(body["kinds"]) == set(PLANNING_ITEM_KINDS)
    assert set(body["source_types"]) == set(PLANNING_SOURCE_TYPES)
    assert set(body["verification_statuses"]) == set(PLANNING_VERIFICATION_STATUSES)
    assert set(body["conflict_resolutions"]) == set(PLANNING_CONFLICT_RESOLUTIONS)

    assert body["topics"]
    topics = {entry["key"]: entry for entry in body["topics"]}
    assert set(MANDATORY_CONFIRMED_TOPICS) <= set(topics)
    assert set(MANDATORY_REJECTED_TOPICS) <= set(topics)
    for entry in body["topics"]:
        assert set(entry) == {"key", "category", "label"}


# ================================================================== explicit statements
def test_explicit_user_statements_store_confirmed_and_rejected(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [
        ("user", "Android only"),
        ("user", "no GPS"),
    ])
    r = _ingest(app_client, pid, conv["id"])
    assert r.status_code == 200, r.text

    state = _state(app_client, pid, conv["id"]).json()
    confirmed = _by_topic(state["confirmed_decisions"], "platform.mobile")
    assert confirmed
    assert confirmed[0]["status"] == "CONFIRMED_BY_USER"
    assert confirmed[0]["source_type"] == "user"
    assert confirmed[0]["value"] == "Android"

    rejected = _by_topic(state["rejected_options"], "location.geofence")
    assert rejected
    assert rejected[0]["status"] == "REJECTED"
    assert rejected[0]["source_type"] == "user"


# ================================================================== assumptions
def test_model_assumption_stays_assumption(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [
        ("assistant", "Assumption: we might use a shared tablet"),
    ])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    state = _state(app_client, pid, conv["id"]).json()
    assumptions = state["assumptions"]
    assert assumptions
    assert assumptions[0]["status"] == "ASSUMED_BY_MODEL"
    assert assumptions[0]["kind"] == "assumption"
    assert assumptions[0]["source_type"] == "assistant"
    assert "shared tablet" in assumptions[0]["value"]
    assert all(
        "shared tablet" not in item["value"]
        for item in state["confirmed_decisions"]
    )


# ================================================================== technical claims
def test_technical_claim_needs_verification_and_does_not_override_confirmed(
    app_client, as_user,
):
    owner = as_user["id"]
    pid = _project(app_client)
    claim = (
        "We will store employee_id to biometric_id for multiple fingerprints "
        "on one Android phone"
    )
    conv, _ = _seed_conversation(app_client, owner, pid, [
        ("user", "Android only"),
        ("assistant", claim),
    ])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    state = _state(app_client, pid, conv["id"]).json()

    verification = [item for item in state["verification_items"]
                    if "employee_id" in item["value"]]
    assert verification
    assert verification[0]["status"] == "NEEDS_VERIFICATION"
    assert verification[0]["kind"] == "verification_item"
    assert verification[0]["source_type"] == "assistant"

    confirmed = _by_topic(state["confirmed_decisions"], "platform.mobile")
    assert confirmed
    assert confirmed[0]["status"] == "CONFIRMED_BY_USER"
    assert confirmed[0]["value"] == "Android"
    assert all(claim != item["value"] for item in state["confirmed_decisions"])


# ================================================================== resolved questions
def test_resolved_topic_not_asked_again(app_client, as_user):
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
    assert "platform.mobile" in body["resolved_topics"]

    # The pure function mirrors the HTTP behavior.
    state = _state(app_client, pid, conv["id"]).json()
    filtered = planning_state.filter_repeated_questions(["Android or iOS?"], state)
    assert filtered["allowed"] == []
    assert filtered["blocked"][0]["topic_key"] == "platform.mobile"


def test_equivalent_questions_map_to_same_topic(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [("user", "Android only")])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    questions = [
        "Android or iOS?",
        "What mobile platform?",
        "Will this run only on Android?",
    ]
    for question in questions:
        assert planning_state.topic_key_for_question(question) == "platform.mobile"

    result = _check_questions(app_client, pid, conv["id"], questions)
    assert result.status_code == 200, result.text
    blocked = result.json()["blocked"]
    assert len(blocked) == len(questions)
    assert {entry["topic_key"] for entry in blocked} == {"platform.mobile"}


# ================================================================== conflicts
def test_contradicting_statement_creates_conflict(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Android only"),
        ("user", "also support iOS"),
    ])
    assert _ingest(app_client, pid, conv["id"], [msgs[0]["id"]]).status_code == 200
    assert _ingest(app_client, pid, conv["id"], [msgs[1]["id"]]).status_code == 200

    state = _state(app_client, pid, conv["id"]).json()
    assert len(state["conflicts"]) == 1
    conflict = state["conflicts"][0]
    assert conflict["topic_key"] == "platform.mobile"
    assert conflict["status"] == "open"
    assert "Android" in conflict["previous_value"]
    assert "iOS" in conflict["new_value"]

    previous = _get_item(app_client, conflict["previous_item_id"]).json()
    new = _get_item(app_client, conflict["new_item_id"]).json()
    assert previous["status"] == "CONFIRMED_BY_USER"
    assert previous["value"] == "Android"
    assert new["status"] == "CONFLICTED"
    assert new["value"] == "iOS"

    # The original confirmed decision is still authoritative.
    confirmed = _by_topic(state["confirmed_decisions"], "platform.mobile")
    assert confirmed and confirmed[0]["value"] == "Android"


def test_conflict_resolution_accept_new(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    previous, new, conflict = _make_conflict(app_client, pid, conv["id"])

    r = _resolve_conflict(app_client, conflict["id"], "accept_new")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conflict"]["status"] == "resolved"
    assert body["conflict"]["resolution"] == "accept_new"
    assert body["conflict"]["resolved_value"] == "iOS"
    # Provenance is preserved on the conflict record.
    assert body["conflict"]["previous_item_id"] == previous["id"]
    assert body["conflict"]["new_item_id"] == new["id"]

    prev_doc = _get_item(app_client, previous["id"]).json()
    new_doc = _get_item(app_client, new["id"]).json()
    assert prev_doc["status"] == "SUPERSEDED"
    assert prev_doc["superseded_by_id"] == new["id"]
    assert new_doc["status"] == "CONFIRMED_BY_USER"
    assert new_doc["conflict_id"] is None

    state = body["state"]
    confirmed = _by_topic(state["confirmed_decisions"], "platform.mobile")
    assert confirmed and confirmed[0]["value"] == "iOS"


def test_conflict_resolution_keep_previous(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    previous, new, conflict = _make_conflict(app_client, pid, conv["id"])

    r = _resolve_conflict(app_client, conflict["id"], "keep_previous")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conflict"]["status"] == "resolved"
    assert body["conflict"]["resolution"] == "keep_previous"
    assert body["conflict"]["resolved_value"] == "Android"

    prev_doc = _get_item(app_client, previous["id"]).json()
    new_doc = _get_item(app_client, new["id"]).json()
    assert prev_doc["status"] == "CONFIRMED_BY_USER"
    assert new_doc["status"] == "SUPERSEDED"
    assert new_doc["superseded_by_id"] == previous["id"]


def test_conflict_resolution_edit_manual(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    previous, new, conflict = _make_conflict(app_client, pid, conv["id"])

    manual = "Android with an iOS fallback"
    r = _resolve_conflict(app_client, conflict["id"], "edit_manual", value=manual)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conflict"]["status"] == "resolved"
    assert body["conflict"]["resolution"] == "edit_manual"
    assert body["conflict"]["resolved_value"] == manual
    resolved_item_id = body["conflict"]["resolved_item_id"]
    assert resolved_item_id

    prev_doc = _get_item(app_client, previous["id"]).json()
    new_doc = _get_item(app_client, new["id"]).json()
    assert prev_doc["status"] == "SUPERSEDED"
    assert new_doc["status"] == "SUPERSEDED"

    resolved = _get_item(app_client, resolved_item_id).json()
    assert resolved["value"] == manual
    assert resolved["status"] == "CONFIRMED_BY_USER"

    # edit_manual requires a value.
    conv2, _ = _seed_conversation(app_client, owner, pid, [])
    _, _, conflict2 = _make_conflict(app_client, pid, conv2["id"])
    missing = _resolve_conflict(app_client, conflict2["id"], "edit_manual")
    assert missing.status_code == 400, missing.text


# ================================================================== supersession
def test_superseded_decisions_excluded_but_retained(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    previous, new, conflict = _make_conflict(app_client, pid, conv["id"])
    assert _resolve_conflict(app_client, conflict["id"], "accept_new").status_code == 200

    state = _state(app_client, pid, conv["id"]).json()
    assert all(item["value"] != "Android" for item in state["confirmed_decisions"])
    assert any(item["value"] == "iOS" for item in state["confirmed_decisions"])
    superseded = state["superseded"]
    assert any(
        item["id"] == previous["id"] and item["status"] == "SUPERSEDED"
        for item in superseded
    )

    plan = _consolidated(app_client, pid, conv["id"])
    assert plan.status_code == 200, plan.text
    sections = plan.json()["sections"]
    assert "iOS" in sections["confirmed_decisions"]
    assert "Android" not in sections["confirmed_decisions"]
    assert "Android" not in plan.json()["markdown"]
    assert "iOS" in plan.json()["markdown"]


# ================================================================== rejected options
def test_rejected_option_never_active_requirement(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [("user", "no GPS")])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    state = _state(app_client, pid, conv["id"]).json()
    rejected = _by_topic(state["rejected_options"], "location.geofence")
    assert rejected and rejected[0]["value"] == "GPS / geofence"
    assert not _by_topic(state["confirmed_decisions"], "location.geofence")
    assert all("GPS" not in item["value"] for item in state["confirmed_decisions"])

    plan = _consolidated(app_client, pid, conv["id"]).json()
    sections = plan["sections"]
    assert "GPS / geofence" in sections["rejected_options"]
    assert all("GPS" not in value for value in sections["confirmed_decisions"])
    assert all("GPS" not in value for value in sections["constraints"])
    assert "Rejected / Out of Scope" in plan["markdown"]
    assert "GPS / geofence" in plan["markdown"]


# ================================================================== open questions
def test_open_question_remains_visible(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [("user", "SaaS limits later")])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    state = _state(app_client, pid, conv["id"]).json()
    open_questions = _by_topic(state["open_questions"], "scale.saas_limits")
    assert open_questions
    assert open_questions[0]["status"] == "NEEDS_VERIFICATION"
    assert open_questions[0]["kind"] == "open_question"
    assert "saas limits" in open_questions[0]["value"].lower()
    assert not _by_topic(state["confirmed_decisions"], "scale.saas_limits")


# ================================================================== manual ops
def test_manual_item_ops_create_patch_reopen_verify(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])

    created = _create_item(
        app_client, pid, conv["id"],
        topic_key="manual.demo", category="other", kind="decision",
        value="Manual v1", status="CONFIRMED_BY_USER", source_type="manual",
        confidence=0.9,
    )
    assert created.status_code == 200, created.text
    item = created.json()["item"]
    assert item["value"] == "Manual v1"
    assert item["status"] == "CONFIRMED_BY_USER"
    assert item["source_type"] == "manual"
    item_id = item["id"]

    assert _get_item(app_client, item_id).json()["value"] == "Manual v1"

    patched = _patch_item(app_client, item_id, value="Manual v2")
    assert patched.status_code == 200, patched.text
    assert patched.json()["value"] == "Manual v2"

    reopened = _reopen_item(app_client, item_id)
    assert reopened.status_code == 200, reopened.text
    reopened_body = reopened.json()
    assert reopened_body["status"] == "NEEDS_VERIFICATION"
    assert reopened_body["kind"] == "open_question"
    assert reopened_body["verification_status"] == "UNVERIFIED"

    verified = _verify_item(
        app_client, item_id, "TECHNICALLY_VERIFIED", evidence="checked in code",
    )
    assert verified.status_code == 200, verified.text
    verified_body = verified.json()
    assert verified_body["status"] == "TECHNICALLY_VERIFIED"
    assert verified_body["verification_status"] == "VERIFIED"
    assert verified_body["provenance"]["evidence"] == "checked in code"

    needs = _verify_item(
        app_client, item_id, "NEEDS_VERIFICATION", evidence="reopened by reviewer",
    )
    assert needs.status_code == 200, needs.text
    assert needs.json()["status"] == "NEEDS_VERIFICATION"
    assert needs.json()["verification_status"] == "UNVERIFIED"

    invalid = _create_item(
        app_client, pid, conv["id"], kind="not-a-kind", value="x",
    )
    assert invalid.status_code == 400, invalid.text


# ================================================================== plan prompt
def test_plan_mode_system_prompt_injects_ledger(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [
        ("user", "Android only"),
        ("user", "no GPS"),
    ])
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
    assert "WORKSPACE DECISION STATE" in plan_prompt
    assert "WORKSPACE MODE: PLAN" in plan_prompt
    assert "platform.mobile" in plan_prompt
    assert "location.geofence" in plan_prompt
    assert "do NOT ask" in plan_prompt

    chat_prompt = call(
        app_client,
        workspace_router._effective_system_prompt,
        owner,
        conv,
        types.SimpleNamespace(workspace_mode="chat"),
        "base",
        conv["id"],
    )
    assert "WORKSPACE DECISION STATE" not in chat_prompt
    assert chat_prompt == "base"

    plan_composed = workspace_modes.compose_system_prompt("plan", "base", "BLOCK")
    assert plan_composed.startswith("base")
    assert "WORKSPACE MODE: PLAN" in plan_composed
    assert plan_composed.rstrip().endswith("BLOCK")
    assert workspace_modes.compose_system_prompt("chat", "base", "BLOCK") == "base"


# ================================================================== capture bridge
def test_prompt_capture_includes_confirmed_decisions(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [
        ("user", "Android only"),
        ("user", "unlimited employees"),
    ])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    analysis = _analyze(app_client, pid, conv["id"], mode="conversation")
    assert analysis.status_code == 200, analysis.text
    body = analysis.json()

    decision_values = _values(body["decision_state"]["confirmed_decisions"])
    assert {"Android", "unlimited employees"} <= decision_values
    assert {"Android", "unlimited employees"} <= set(
        body["capture_fields"]["confirmed_decisions"]
    )
    assert {"Android", "unlimited employees"} <= set(
        body["structured_spec"]["confirmed_decisions"]
    )

    saved = _save(app_client, pid, conv["id"], mode="conversation", kind="prompt")
    assert saved.status_code == 200, saved.text
    saved_spec = saved.json()["version"]["structured_spec"]
    assert {"Android", "unlimited employees"} <= set(saved_spec["confirmed_decisions"])


def test_prompt_capture_does_not_promote_assumptions(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    claim = (
        "We will store employee_id to biometric_id for multiple fingerprints "
        "on one Android phone"
    )
    conv, _ = _seed_conversation(app_client, owner, pid, [
        ("assistant", "Assumption: we might use a shared tablet"),
        ("assistant", claim),
    ])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    body = _analyze(app_client, pid, conv["id"], mode="conversation").json()
    spec = body["structured_spec"]

    assert any("shared tablet" in value for value in spec["assumptions"])
    assert any("employee_id" in value for value in spec["verification_items"])
    assert any(
        item["status"] == "ASSUMED_BY_MODEL"
        for item in body["decision_state"]["assumptions"]
    )
    assert any(
        item["status"] == "NEEDS_VERIFICATION"
        for item in body["decision_state"]["verification_items"]
    )

    for field in ("confirmed_decisions", "constraints"):
        joined = " ".join(spec[field])
        assert "shared tablet" not in joined
        assert "employee_id" not in joined


def test_prompt_capture_excludes_chatter(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [
        ("user", "Thanks!"),
        ("user", "Thanks!"),
        ("assistant", "Sure, happy to help."),
        ("user", "Android only"),
        ("user", "Objective: build attendance tracking"),
    ])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    body = _analyze(app_client, pid, conv["id"], mode="conversation").json()
    assert "Thanks" not in body["content"]
    assert "happy to help" not in body["content"]
    assert "Android" in body["structured_spec"]["confirmed_decisions"]

    assert prompt_capture._is_chatter("Thanks!") is True
    assert prompt_capture._is_chatter("happy to help") is True
    assert prompt_capture._is_chatter("Android only") is False


# ================================================================== asset reuse
def test_capture_reuses_existing_policy_and_invariant_links(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    policy = _policy(
        app_client, pid, domain="ARCHITECTURE",
        content="Reuse the existing service layer",
    )
    invariant = _invariant(
        app_client, pid, domain="SECURITY", title="No secret leakage",
    )
    conv, _ = _seed_conversation(app_client, owner, pid, [
        ("user", "Review the change."),
        ("assistant", "## Architecture Rules\n- Reuse the existing service layer\n"),
    ])
    before_policies = _policy_ids(app_client, pid)
    before_invariants = _invariant_ids(app_client, pid)

    saved = _save(
        app_client, pid, conv["id"],
        mode="conversation", kind="planning_template",
        include_policy_ids=[policy["id"]],
        include_invariant_ids=[invariant["id"]],
    )
    assert saved.status_code == 200, saved.text
    linked = saved.json()["version"]["structured_spec"]["linked"]
    assert linked["policies"] == [policy["id"]]
    assert linked["invariants"] == [invariant["id"]]

    # No new policy/invariant assets were created; the existing ids are reused.
    assert _policy_ids(app_client, pid) == before_policies
    assert _invariant_ids(app_client, pid) == before_invariants


def test_capture_does_not_create_duplicate_blocks(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    constraint = "Tests are required"
    existing = _block(
        app_client, pid,
        name="Tests required", category="tests_required", content=constraint,
    )
    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Keep this bounded."),
        ("assistant", f"## Constraints\n- {constraint}\n"),
    ])
    assistant_id = msgs[1]["id"]
    # A decision-state constraint must not spawn a second block either.
    manual = _create_item(
        app_client, pid, conv["id"],
        topic_key="quality.tests", category="other", kind="constraint",
        value=constraint, status="CONFIRMED_BY_USER", source_type="manual",
    )
    assert manual.status_code == 200, manual.text

    before = _block_ids(app_client, pid)
    assert before == [existing["id"]]

    first = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id],
        kind="planning_template", create_missing_blocks=True,
    )
    second = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id],
        kind="planning_template", create_missing_blocks=True,
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    assert _block_ids(app_client, pid) == before
    assert first.json()["version"]["blocks"] == [existing["id"]]
    assert second.json()["version"]["blocks"] == [existing["id"]]


# ================================================================== redaction
def test_secret_redaction_in_ledger_and_capture(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    openai_secret = "sk-" + "A" * 24
    github_secret = "ghp_" + "B" * 24
    raw_secrets = (openai_secret, github_secret, "hunter2")

    message = (
        f"Use the key {openai_secret} and token {github_secret} "
        "and password=hunter2"
    )
    conv, _ = _seed_conversation(app_client, owner, pid, [("user", message)])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200

    state = _state(app_client, pid, conv["id"]).json()
    ledger_texts = []
    for group in (
        "confirmed_decisions", "constraints", "rejected_options", "assumptions",
        "open_questions", "verification_items", "superseded",
    ):
        for item in state[group]:
            ledger_texts.append(item["value"])
            ledger_texts.append(json.dumps(item["provenance"]))
    ledger_joined = "\n".join(ledger_texts)
    assert "[REDACTED]" in ledger_joined
    for raw in raw_secrets:
        assert raw not in ledger_joined

    plan = _consolidated(app_client, pid, conv["id"]).json()
    assert "[REDACTED]" in plan["markdown"]
    for raw in raw_secrets:
        assert raw not in plan["markdown"]
        assert raw not in json.dumps(plan["sections"])

    capture = _analyze(app_client, pid, conv["id"], mode="conversation").json()
    assert "[REDACTED]" in capture["content"]
    for raw in raw_secrets:
        assert raw not in capture["content"]
        assert raw not in json.dumps(capture["structured_spec"])
        assert raw not in json.dumps(capture["decision_state"])


# ================================================================== isolation
def test_cross_project_conversation_writes_rejected(app_client, as_user):
    owner = as_user["id"]
    pid_a = _project(app_client, "v614dm-a")
    pid_b = _project(app_client, "v614dm-b")
    conv, _ = _seed_conversation(app_client, owner, pid_a, [("user", "Android only")])

    ingest = _ingest(app_client, pid_b, conv["id"])
    assert ingest.status_code == 400, ingest.text

    created = _create_item(
        app_client, pid_b, conv["id"], topic_key="x", value="v",
    )
    assert created.status_code == 400, created.text


def test_cross_project_conversation_reads_are_not_found(app_client, as_user):
    owner = as_user["id"]
    pid_a = _project(app_client, "v614dm-a")
    pid_b = _project(app_client, "v614dm-b")
    conv, _ = _seed_conversation(app_client, owner, pid_a, [("user", "Android only")])
    assert _ingest(app_client, pid_a, conv["id"]).status_code == 200

    # A conversation from another project must not be readable as project B.
    state = _state(app_client, pid_b, conv["id"])
    assert state.status_code == 404, state.text

    plan = _consolidated(app_client, pid_b, conv["id"])
    assert plan.status_code == 404, plan.text


def test_foreign_owner_isolation(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [("user", "Android only")])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200
    item = _create_item(
        app_client, pid, conv["id"], topic_key="manual.owner", value="owned",
    ).json()["item"]
    _, _, conflict = _make_conflict(app_client, pid, conv["id"])

    with _foreign_owner(app_client):
        # Reads are owner-scoped: either not-found or empty, never owner A's data.
        foreign_state = _state(app_client, pid, conv["id"])
        assert foreign_state.status_code in (200, 404)
        if foreign_state.status_code == 200:
            assert foreign_state.json()["confirmed_decisions"] == []

        assert _get_item(app_client, item["id"]).status_code == 404

        foreign_conflicts = _list_conflicts(app_client, pid, conv["id"])
        assert foreign_conflicts.status_code in (200, 404)
        if foreign_conflicts.status_code == 200:
            assert foreign_conflicts.json() == []

        # A foreign owner cannot resolve or consume the conflict/project.
        resolve = _resolve_conflict(app_client, conflict["id"], "accept_new")
        assert resolve.status_code == 404, resolve.text
        foreign_plan = _consolidated(app_client, pid, conv["id"])
        assert foreign_plan.status_code == 404, foreign_plan.text


def test_conflicts_listing_filters_by_status(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    _, _, conflict = _make_conflict(app_client, pid, conv["id"])

    all_conflicts = _list_conflicts(app_client, pid, conv["id"])
    assert all_conflicts.status_code == 200, all_conflicts.text
    assert len(all_conflicts.json()) == 1
    assert all_conflicts.json()[0]["status"] == "open"

    open_only = _list_conflicts(app_client, pid, conv["id"], status="open")
    assert len(open_only.json()) == 1
    resolved_only = _list_conflicts(app_client, pid, conv["id"], status="resolved")
    assert resolved_only.json() == []

    assert _resolve_conflict(app_client, conflict["id"], "accept_new").status_code == 200
    assert _list_conflicts(app_client, pid, conv["id"], status="open").json() == []
    assert len(_list_conflicts(
        app_client, pid, conv["id"], status="resolved").json()) == 1

    invalid = _list_conflicts(app_client, pid, conv["id"], status="bogus")
    assert invalid.status_code == 400, invalid.text


# ================================================================== canonical runtime
def test_workspace_modes_reuse_canonical_runtime(app_client, as_user, monkeypatch):
    catalog = app_client.get(f"{API}/workspace-modes")
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    assert body["default"] == "chat"
    assert {entry["key"] for entry in body["modes"]} == {
        "chat", "plan", "research", "audit",
    }

    # Modes are composition presets, not a second runtime.
    assert workspace_router.execute_model is services.execute_model
    assert workspace_router.stream_execution is services.stream_execution
    assert not hasattr(workspace_modes, "execute")
    assert not hasattr(workspace_modes, "run")

    owner = as_user["id"]
    pid = _project(app_client)
    model_id = _make_model(app_client, owner)
    created = app_client.post(f"{API}/conversations", json={
        "project_id": pid, "title": "Plan run", "workspace_mode": "plan",
    })
    assert created.status_code == 200, created.text
    cid = created.json()["id"]

    recorded = []

    def executor(model, req):
        recorded.append(req)
        return result_success("Scripted plan reply")

    _install(monkeypatch, executor)

    sent = app_client.post(f"{API}/conversations/{cid}/messages", json={
        "model_id": model_id, "content": "Android only", "workspace_mode": "plan",
    })
    assert sent.status_code == 200, sent.text
    sent_body = sent.json()
    assert sent_body["run"]["status"] == "success"
    assert "Scripted plan reply" in sent_body["assistant_message"]["content"]
    assert len(recorded) == 1

    async def _runs():
        return await repo.runs.list({"owner_id": owner, "conversation_id": cid})

    runs = call(app_client, _runs)
    assert len(runs) == 1

    async def _msgs():
        return await repo.messages.list(
            {"owner_id": owner, "conversation_id": cid},
            sort_field="created_at", descending=False, limit=1000,
        )

    msgs = call(app_client, _msgs)
    roles = [msg["role"] for msg in msgs]
    assert roles.count("assistant") == 1
    assert roles.count("user") == 1

    # The canonical plan-mode path auto-ingested the user turn into the ledger.
    state = _state(app_client, pid, cid).json()
    assert any(
        item["topic_key"] == "platform.mobile"
        for item in state["confirmed_decisions"]
    )


# ================================================================== mandatory regression
def test_mandatory_example_retains_decisions_and_blocks_repeats(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, _ = _seed_conversation(
        app_client, owner, pid,
        [("user", text) for text in MANDATORY_SEQUENCE],
    )
    ingest = _ingest(app_client, pid, conv["id"])
    assert ingest.status_code == 200, ingest.text

    state = _state(app_client, pid, conv["id"]).json()

    confirmed_topics = _topic_keys(state["confirmed_decisions"])
    assert set(MANDATORY_CONFIRMED_TOPICS) <= confirmed_topics

    rejected_topics = _topic_keys(state["rejected_options"])
    assert set(MANDATORY_REJECTED_TOPICS) <= rejected_topics

    resolved = set(state["resolved_topics"])
    assert set(MANDATORY_CONFIRMED_TOPICS) <= resolved
    assert set(MANDATORY_REJECTED_TOPICS) <= resolved

    questions = [
        "Android or iOS?",
        "Do you need GPS?",
        "Do you need a selfie?",
        "Who performs enrollment?",
        "What is the employee source of truth?",
        "What data retention policy applies?",
        "What are the attendance business rules?",
    ]
    result = _check_questions(app_client, pid, conv["id"], questions)
    assert result.status_code == 200, result.text
    body = result.json()

    blocked_questions = {entry["question"] for entry in body["blocked"]}
    assert set(questions[:4]) <= blocked_questions
    assert all(entry["topic_key"] for entry in body["blocked"])
    assert set(body["allowed"]) == set(questions[4:])
