"""V6.15 PRD readiness / Living PRD compiler / version review tests.

Covers:
* 9  readiness with blockers (open conflict / open question) and no fake pct
* 10 readiness without blockers -> ready + "Ready to generate PRD"
* 11 PRD generation: sections reflect state, version 1 DRAFT persisted
* 12 assumptions stay assumptions (never promoted in PRD/markdown)
* 13 NEEDS_VERIFICATION stays visible (never promoted)
* 14 version creation + content_hash changes with planning state
* 15 section-level diff with line diff and affected_sections
* 16 approval lifecycle + supersession + foreign-version 404
* project + owner isolation, and old V6.14 project lazy-sync compatibility

Everything runs offline against the shared ``app_client`` / ``as_user`` fixtures.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

from app import repository as repo
from app.deps import get_current_user
from app.models import (
    PRD_STATUSES,
    READINESS_STATES,
    new_id,
    now_iso,
)

from tests.conftest import call
from _support import new_owner_id, purge_owner

API = "/api"


# ------------------------------------------------------------------ fixtures
def _seed_conversation(app_client, owner_id, project_id, turns):
    conv = {
        "id": new_id(),
        "owner_id": owner_id,
        "project_id": project_id,
        "title": "PRD source",
        "system_prompt": "",
        "workspace_mode": "plan",
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


def _project(app_client, name="pytest-v615prd"):
    r = app_client.post(
        f"{API}/projects", json={"name": f"{name}-{uuid.uuid4().hex[:8]}"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


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


# ------------------------------------------------------------------ PRD helpers
def _readiness(app_client, project_id, conversation_id=None):
    params = {"project_id": project_id}
    if conversation_id is not None:
        params["conversation_id"] = conversation_id
    return app_client.get(f"{API}/planning-state/readiness", params=params)


def _catalog(app_client):
    return app_client.get(f"{API}/planning-state/prd/catalog")


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


def _seed_all_dimensions(app_client, project_id, conversation_id):
    """One confirmed decision per readiness dimension (all 8 sections)."""
    mapping = (
        ("product", "product.goal", "Deliver attendance tracking for field staff"),
        ("actors", "actors.primary", "HR administrators and on-site employees"),
        ("workflows", "workflows.core",
         "Employee clocks in by fingerprint at a shared tablet"),
        ("business_rules", "attendance.rules", "Lateness after 09:15 is flagged"),
        ("security", "security.biometrics", "Map employee_id to biometric_id"),
        ("data", "data.retention", "Keep attendance records for 24 months"),
        ("integrations", "integration.hr_api", "Sync daily with the HR API"),
        ("platform", "platform.mobile", "Android only"),
    )
    created = []
    for category, topic_key, value in mapping:
        r = _create_item(
            app_client, project_id, conversation_id,
            topic_key=topic_key, category=category, kind="decision",
            value=value, status="CONFIRMED_BY_USER", source_type="manual",
        )
        assert r.status_code == 200, r.text
        created.append(r.json()["item"])
    return created


# ================================================================== catalog
def test_prd_catalog(app_client, as_user):
    r = _catalog(app_client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["statuses"]) == set(PRD_STATUSES)
    assert set(body["readiness_states"]) == set(READINESS_STATES)
    assert body["compiler_version"] == "v6.15.0"
    keys = {d["key"] for d in body["dimensions"]}
    assert {"product_goal", "core_workflows", "technical_constraints"} <= keys


# ================================================================== 9 readiness blockers
def test_readiness_with_blockers(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    _make_conflict(app_client, pid, conv["id"])
    question = _create_item(
        app_client, pid, conv["id"], topic_key="data.retention", category="data",
        kind="open_question", value="How long do we retain attendance data?",
        status="NEEDS_VERIFICATION", source_type="manual",
    )
    assert question.status_code == 200, question.text

    r = _readiness(app_client, pid, conv["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready"] is False
    assert body["blocker_count"] >= 2
    assert "percentage" not in body and "readiness_percentage" not in body

    states = {d["key"]: d["state"] for d in body["dimensions"]}
    assert states["technical_constraints"] == "BLOCKED"
    assert states["data_ownership"] == "BLOCKED"
    assert any(d["blockers"] for d in body["dimensions"])
    assert all(d["state"] in READINESS_STATES for d in body["dimensions"])
    assert body["summary"] != "Ready to generate PRD"


# ================================================================== 10 readiness clear
def test_readiness_without_blockers(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    _seed_all_dimensions(app_client, pid, conv["id"])

    r = _readiness(app_client, pid, conv["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["blocker_count"] == 0
    assert body["ready"] is True, body
    assert body["summary"] == "Ready to generate PRD"
    assert all(d["state"] in ("READY", "NEEDS_VERIFICATION") for d in body["dimensions"])
    assert "percentage" not in body


# ================================================================== 11 generation
def test_prd_generation_reflects_state_and_persists_draft(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    _seed_all_dimensions(app_client, pid, conv["id"])

    r = _generate(app_client, pid, conv["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert body["status"] == "DRAFT"
    assert body["compiler_version"] == "v6.15.0"
    assert body["content_hash"]
    assert body["approved_at"] is None

    sections = {s["key"]: s for s in body["content"]["sections"]}
    assert "product" in sections
    assert "planning_state" in sections  # always included
    assert any(
        i["evidence_class"] == "CONFIRMED_FACT" for i in sections["product"]["items"]
    )
    item = sections["product"]["items"][0]
    assert {"text", "status", "evidence_class", "decision_id",
            "topic_key", "provenance"} <= set(item)
    assert item["decision_id"]

    # Persisted as version 1 DRAFT (owner-scoped row), not just echoed back.
    stored = call(app_client, repo.planning_prds.get, body["id"], as_user["id"])
    assert stored is not None
    assert stored["version"] == 1
    assert stored["status"] == "DRAFT"


# ================================================================== 12 assumptions
def test_assumptions_remain_assumptions(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [
        ("assistant", "Assumption: we might use a shared tablet at the entrance"),
    ])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200
    state = _state(app_client, pid, conv["id"]).json()
    assert state["assumptions"], state
    assumption = state["assumptions"][0]
    assert assumption["status"] == "ASSUMED_BY_MODEL"

    body = _generate(app_client, pid, conv["id"]).json()
    items = [i for s in body["content"]["sections"] for i in s["items"]]
    matched = [i for i in items if i["decision_id"] == assumption["id"]]
    assert matched
    assert all(i["evidence_class"] == "ASSUMPTION" for i in matched)
    assert all(i["evidence_class"] != "CONFIRMED_FACT" for i in matched)
    assert "Assumption (not confirmed)" in body["markdown"]
    assert body["counts"]["assumed"] >= 1


# ================================================================== 13 verification
def test_needs_verification_stays_visible(app_client, as_user):
    claim = "store employee_id to biometric_id on Android"
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [
        ("assistant", claim),
    ])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200
    state = _state(app_client, pid, conv["id"]).json()
    assert state["verification_items"], state
    item = state["verification_items"][0]
    assert item["status"] == "NEEDS_VERIFICATION"

    body = _generate(app_client, pid, conv["id"]).json()
    items = [i for s in body["content"]["sections"] for i in s["items"]]
    matched = [i for i in items if i["decision_id"] == item["id"]]
    assert matched
    assert matched[0]["status"] == "NEEDS_VERIFICATION"
    assert matched[0]["evidence_class"] == "NEEDS_VERIFICATION"
    assert all(i["evidence_class"] != "CONFIRMED_FACT" for i in matched)
    assert "Needs verification:" in body["markdown"]

    # The authoritative ledger row is never promoted by compilation.
    ledger = _get_ledger_item(app_client, item["id"]).json()
    assert ledger["status"] == "NEEDS_VERIFICATION"


# ================================================================== 14 versions
def test_prd_versions_and_content_hash(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    _seed_all_dimensions(app_client, pid, conv["id"])

    v1 = _generate(app_client, pid, conv["id"]).json()
    assert v1["version"] == 1

    _create_item(
        app_client, pid, conv["id"], topic_key="ux.theme", category="ux",
        kind="decision", value="Dark theme by default",
        status="CONFIRMED_BY_USER", source_type="manual",
    )
    v2 = _generate(app_client, pid, conv["id"]).json()
    assert v2["version"] == 2
    assert v2["content_hash"] != v1["content_hash"]

    versions = _versions(app_client, pid).json()
    assert versions["latest_version"] == 2
    assert [v["version"] for v in versions["versions"]] == [2, 1]
    assert all(v.get("content_hash") for v in versions["versions"])
    entry = versions["versions"][0]
    assert "markdown" not in entry and "content" not in entry

    fetched = _get_version(app_client, v1["id"]).json()
    assert fetched["id"] == v1["id"]
    assert fetched["version"] == 1
    assert fetched["status"] == "DRAFT"
    assert "markdown" in fetched and "content" in fetched


# ================================================================== 15 diff
def test_prd_diff_reports_changed_and_added_sections(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    _create_item(
        app_client, pid, conv["id"], topic_key="product.goal", category="product",
        kind="decision", value="Initial product goal",
        status="CONFIRMED_BY_USER", source_type="manual",
    )
    v1 = _generate(app_client, pid, conv["id"]).json()

    _create_item(
        app_client, pid, conv["id"], topic_key="ux.flow", category="ux",
        kind="decision", value="Add a kiosk mode",
        status="CONFIRMED_BY_USER", source_type="manual",
    )
    _create_item(
        app_client, pid, conv["id"], topic_key="product.goal_secondary",
        category="product", kind="decision", value="Second product decision",
        status="CONFIRMED_BY_USER", source_type="manual",
    )
    v2 = _generate(app_client, pid, conv["id"]).json()

    r = _compare(app_client, pid, v1["id"], v2["id"])
    assert r.status_code == 200, r.text
    diff = r.json()
    assert diff["from"]["version"] == 1
    assert diff["to"]["version"] == 2
    assert diff["affected_sections"], diff

    by_key = {s["key"]: s for s in diff["sections"]}
    assert by_key["ux"]["change"] == "added"
    assert by_key["ux"]["line_diff"]
    assert by_key["product"]["change"] == "changed"
    assert any(d["op"] == "insert" for d in by_key["product"]["line_diff"])
    assert diff["summary"]["sections_added"] >= 1
    assert diff["summary"]["lines_added"] >= 1
    affected_keys = {s["key"] for s in diff["affected_sections"]}
    assert "ux" in affected_keys and "product" in affected_keys


# ================================================================== 16 approval
def test_prd_approval_supersedes_and_isolates(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    _seed_all_dimensions(app_client, pid, conv["id"])
    v1 = _generate(app_client, pid, conv["id"]).json()
    _create_item(
        app_client, pid, conv["id"], topic_key="ux.theme", category="ux",
        kind="decision", value="Dark theme by default",
        status="CONFIRMED_BY_USER", source_type="manual",
    )
    v2 = _generate(app_client, pid, conv["id"]).json()

    approved2 = _approve(app_client, v2["id"], pid)
    assert approved2.status_code == 200, approved2.text
    assert approved2.json()["status"] == "APPROVED"
    assert approved2.json()["approved_at"]

    approved1 = _approve(app_client, v1["id"], pid)
    assert approved1.status_code == 200, approved1.text
    assert approved1.json()["status"] == "APPROVED"

    # The previously approved version is superseded; history is retained.
    v2_after = _get_version(app_client, v2["id"]).json()
    assert v2_after["status"] == "SUPERSEDED"
    assert v2_after["superseded_at"]
    assert v2_after["markdown"]

    with _foreign_owner(app_client):
        assert _approve(app_client, v1["id"], pid).status_code == 404
        assert _get_version(app_client, v1["id"]).status_code == 404


# ================================================================== isolation
def test_prd_project_and_owner_isolation(app_client, as_user):
    pid_a = _project(app_client, "prd-a")
    pid_b = _project(app_client, "prd-b")
    conv, _ = _seed_conversation(app_client, as_user["id"], pid_a, [])
    _seed_all_dimensions(app_client, pid_a, conv["id"])
    v1 = _generate(app_client, pid_a, conv["id"]).json()

    versions_b = _versions(app_client, pid_b).json()
    assert versions_b["versions"] == []
    assert versions_b["latest_version"] is None

    # The owner can read its own version when scoped to the right project.
    assert _get_version(app_client, v1["id"], project_id=pid_a).status_code == 200

    # Same owner, SECOND project: the manual version id must not resolve.
    assert _get_version(app_client, v1["id"], project_id=pid_b).status_code == 404
    assert _approve(app_client, v1["id"], pid_b).status_code == 404
    assert _compare(app_client, pid_b, v1["id"], v1["id"]).status_code == 404
    assert _readiness(app_client, "does-not-exist", conv["id"]).status_code == 404

    with _foreign_owner(app_client):
        assert _versions(app_client, pid_a).status_code == 404
        assert _get_version(app_client, v1["id"]).status_code == 404
        assert _get_version(app_client, v1["id"], project_id=pid_a).status_code == 404
        assert _compare(app_client, pid_a, v1["id"], v1["id"]).status_code == 404
        assert _generate(app_client, pid_a, conv["id"]).status_code == 404


# ================================================================== compat
def test_old_v614_project_lazy_sync_compatibility(app_client, as_user):
    pid = _project(app_client, "prd-compat")
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    _seed_all_dimensions(app_client, pid, conv["id"])

    # The graph has never been synced for this project.
    nodes_before = call(
        app_client, repo.planning_nodes.list,
        {"owner_id": as_user["id"], "project_id": pid},
    )
    assert nodes_before == []

    assert _readiness(app_client, pid, conv["id"]).status_code == 200
    gen = _generate(app_client, pid, conv["id"])
    assert gen.status_code == 200, gen.text
    assert gen.json()["version"] == 1

    # Compatibility is additive: the ledger is intact, not reset or rewritten.
    state = _state(app_client, pid, conv["id"]).json()
    assert state["counts"]["confirmed_decisions"] >= 8

    nodes_after = call(
        app_client, repo.planning_nodes.list,
        {"owner_id": as_user["id"], "project_id": pid},
    )
    assert nodes_after


# ================================================================== plan prompt
def test_plan_mode_prompt_includes_readiness_block(app_client, as_user):
    import types

    from app.routers import workspace as workspace_router

    owner = as_user["id"]
    pid = _project(app_client, "prd-prompt")
    conv, _ = _seed_conversation(app_client, owner, pid, [])
    _seed_all_dimensions(app_client, pid, conv["id"])

    plan_prompt = call(
        app_client,
        workspace_router._effective_system_prompt,
        owner,
        conv,
        types.SimpleNamespace(workspace_mode="plan"),
        "base",
        conv["id"],
    )
    assert "PRD READINESS" in plan_prompt
    assert "technical_constraints" in plan_prompt

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
