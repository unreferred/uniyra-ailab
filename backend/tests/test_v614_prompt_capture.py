"""V6.14 Workspace Prompt Capture / Composer API tests.

Covers the Workspace prompt-capture composer end to end: the static catalog,
deterministic response/conversation analysis, save semantics through the
EXISTING prompt store (draft by default, frozen only on explicit request),
planning-template block mapping with duplicate prevention, explicit-only
policy/invariant linking, universal secret redaction, owner/project isolation,
the Create-Agent frozen-version binding contract, and Workspace mode
persistence/behavior.

Everything runs offline against the shared ``app_client``/``as_user`` fixtures:
conversations and messages are seeded directly through ``repo`` for throwaway
``pytest-`` owners that are purged after each test, and every foreign owner used
for isolation is created with ``new_owner_id()`` and purged again.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

from app import repository as repo
from app.deps import get_current_user
from app.models import new_id, now_iso
from app.services import redaction
from app.services import workspace_modes

from tests.conftest import call
from _support import create_model, create_provider, new_owner_id, purge_owner

API = "/api"

# The canonical extraction keys the catalog must publish.
SECTION_KEYS = (
    "role",
    "objective",
    "scope",
    "constraints",
    "architecture_rules",
    "design_rules",
    "security_rules",
    "phases",
    "validation",
    "expected_output",
)

# ------------------------------------------------------------------ fixtures
_ASSISTANT_SECTIONS = """## Role
Senior backend engineer

## Objective
Promote a Workspace conversation into a reusable prompt draft

## Constraints
- Tests are required
- Keep the change bounded

## Architecture Rules
- Reuse the existing service layer

## Security Rules
- Ownership isolation must never regress

## Execution Phases
1. Inspect the existing capture service
2. Compose the structured prompt

## Validation
- Run the focused pytest module

## Expected Output
- A change report
"""

_CONSTRAINT_SECTIONS = """## Constraints
- Tests are required
- Keep the change bounded
- Reuse existing services
"""

_ARCH_SECURITY_SECTIONS = """## Architecture Rules
- Reuse the existing service layer

## Security Rules
- Never expose secrets to other owners
"""


def _seed_conversation(app_client, owner_id, project_id, turns, *, title="Capture source", workspace_mode="chat"):
    """Insert a conversation plus its messages directly through ``repo``."""
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


def _project(app_client, name="pytest-v614"):
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


def _analyze(app_client, project_id, conversation_id, **overrides):
    payload = {"project_id": project_id, "conversation_id": conversation_id}
    payload.update(overrides)
    return app_client.post(f"{API}/prompt-capture/analyze", json=payload)


def _save(app_client, project_id, conversation_id, **overrides):
    payload = {"project_id": project_id, "conversation_id": conversation_id}
    payload.update(overrides)
    return app_client.post(f"{API}/prompt-capture/save", json=payload)


def _block_ids(app_client, project_id):
    r = app_client.get(f"{API}/prompt-blocks", params={"project_id": project_id})
    assert r.status_code == 200, r.text
    return [item["id"] for item in r.json()]


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


# ================================================================== 1 catalog
def test_capture_catalog(app_client, as_user):
    r = app_client.get(f"{API}/prompt-capture/catalog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["modes"]) == {"response", "conversation"}
    assert set(body["kinds"]) == {"prompt", "planning_template"}
    assert len(body["sections"]) == 10
    assert set(body["sections"]) == set(SECTION_KEYS)
    assert body["targets"]
    assert body["compiler_modes"]


# ================================================================== 2 single response
def test_capture_single_assistant_response(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Please implement the capture feature now."),
        ("assistant", _ASSISTANT_SECTIONS),
    ])
    user_msg, assistant_msg = msgs

    r = _analyze(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_msg["id"]],
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["mode"] == "response"
    assert body["source"]["message_ids"] == [assistant_msg["id"]]
    assert body["message_ids"] == [assistant_msg["id"]]
    assert user_msg["id"] not in body["message_ids"]

    structure = body["structure"]
    assert structure["objective"].strip()
    assert structure["security_rules"]
    assert structure["phases"]
    assert structure["validation"]
    assert "## Objective" in body["content"]

    if body["preview"] is None:
        # Preview is best-effort; the deterministic content must still exist.
        assert body["preview_error"]
        assert body["content"]
    else:
        assert isinstance(body["preview"], dict)


# ================================================================== 3 conversation
def test_capture_full_conversation(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Objective: capture the workspace conversation into a prompt."),
        ("assistant", "Validation: run the full regression suite before reporting."),
    ])
    user_msg, assistant_msg = msgs

    r = _analyze(app_client, pid, conv["id"], mode="conversation")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["mode"] == "conversation"
    assert set(body["message_ids"]) == {user_msg["id"], assistant_msg["id"]}
    assert body["source"]["message_count"] >= 2

    # Both the user and the assistant turn influenced the extraction.
    assert "capture the workspace conversation" in body["structure"]["objective"]
    validation_text = " ".join(body["structure"]["validation"])
    assert "regression suite" in validation_text


# ================================================================== 4 save prompt
def test_save_prompt_creates_spec_and_draft_version(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Please implement the capture feature now."),
        ("assistant", _ASSISTANT_SECTIONS),
    ])
    assistant_id = msgs[1]["id"]

    first = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
    )
    assert first.status_code == 200, first.text
    saved = first.json()

    prompt = saved["prompt"]
    version = saved["version"]
    assert prompt["project_id"] == pid
    assert version["prompt_id"] == prompt["id"]
    assert version["version"] == 1
    assert version["status"] == "draft"
    assert version.get("frozen_at") is None

    listed = app_client.get(f"{API}/prompts/{prompt['id']}/versions")
    assert listed.status_code == 200, listed.text
    versions = listed.json()
    assert len(versions) == 1
    assert versions[0]["id"] == version["id"]
    assert versions[0]["status"] == "draft"

    # Re-saving against the same prompt promotes into a new draft version.
    second = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
        prompt_id=prompt["id"],
    )
    assert second.status_code == 200, second.text
    v2 = second.json()["version"]
    assert v2["prompt_id"] == prompt["id"]
    assert v2["version"] == 2
    assert v2["status"] == "draft"
    assert v2["id"] != version["id"]

    captured = app_client.get(f"{API}/prompt-capture", params={"project_id": pid})
    assert captured.status_code == 200, captured.text
    items = captured.json()
    assert len(items) >= 2
    for item in items:
        assert item["prompt_id"] == prompt["id"]
        assert item["conversation_id"] == conv["id"]
        assert item["structured_spec"]["source"]["conversation_id"] == conv["id"]
    assert {item["prompt_version_id"] for item in items} >= {version["id"], v2["id"]}


# ================================================================== 5 planning template
def test_save_planning_template_maps_blocks(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)

    starters = app_client.post(
        f"{API}/prompt-blocks/starters", json={"project_id": pid}
    )
    assert starters.status_code == 200, starters.text
    starter_blocks = app_client.get(
        f"{API}/prompt-blocks", params={"project_id": pid}
    ).json()
    assert len(starter_blocks) == 9
    mapped_categories = {"tests_required", "bounded_scope", "reuse_existing_services"}
    mapped_starter_ids = {
        block["id"] for block in starter_blocks if block["category"] in mapped_categories
    }
    assert len(mapped_starter_ids) == 3

    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Tighten the implementation."),
        ("assistant", _CONSTRAINT_SECTIONS),
    ])
    assistant_id = msgs[1]["id"]

    first = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id],
        kind="planning_template", create_missing_blocks=False,
    )
    assert first.status_code == 200, first.text
    version = first.json()["version"]
    assert version["blocks"]
    assert set(version["blocks"]) & mapped_starter_ids
    linked = version["structured_spec"]["linked"]["blocks"]
    assert linked
    assert set(linked) & mapped_starter_ids
    assert set(version["blocks"]) == set(linked)

    before_ids = _block_ids(app_client, pid)
    before_version_blocks = list(version["blocks"])

    second = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id],
        kind="planning_template", create_missing_blocks=True,
    )
    assert second.status_code == 200, second.text
    after_ids = _block_ids(app_client, pid)
    assert after_ids == before_ids
    assert second.json()["version"]["blocks"] == before_version_blocks


# ================================================================== 6 duplicate blocks
def test_duplicate_block_prevention(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    constraint = "Reuse the existing helper service"
    existing = _block(
        app_client, pid,
        name="Reuse helper",
        category="reuse_existing_services",
        content=constraint,
    )

    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Keep this bounded."),
        ("assistant", f"## Constraints\n- {constraint}\n"),
    ])
    assistant_id = msgs[1]["id"]
    before = _block_ids(app_client, pid)
    assert before == [existing["id"]]

    first = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id],
        kind="planning_template", create_missing_blocks=False,
    )
    second = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id],
        kind="planning_template", create_missing_blocks=False,
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    after = _block_ids(app_client, pid)
    assert after == before
    assert first.json()["version"]["blocks"] == [existing["id"]]
    assert second.json()["version"]["blocks"] == [existing["id"]]
    assert [b["id"] for b in first.json()["blocks"]] == [existing["id"]]
    assert [b["id"] for b in second.json()["blocks"]] == [existing["id"]]


# ================================================================== 7 explicit linking
def test_policy_invariant_linking_requires_explicit_confirmation(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    policy = _policy(
        app_client, pid, domain="ARCHITECTURE",
        content="Reuse the existing service layer",
    )
    invariant = _invariant(
        app_client, pid, domain="SECURITY",
        title="No secret leakage", severity="high",
    )
    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Review the change."),
        ("assistant", _ARCH_SECURITY_SECTIONS),
    ])
    assistant_id = msgs[1]["id"]

    analysis = _analyze(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id],
    )
    assert analysis.status_code == 200, analysis.text
    body = analysis.json()
    assert policy["id"] in [item["id"] for item in body["policies"]["candidates"]]
    assert invariant["id"] in [item["id"] for item in body["invariants"]["candidates"]]

    no_link = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
        include_policy_ids=[], include_invariant_ids=[],
    )
    assert no_link.status_code == 200, no_link.text
    linked = no_link.json()["version"]["structured_spec"]["linked"]
    assert linked["policies"] == []
    assert linked["invariants"] == []

    explicit = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
        include_policy_ids=[policy["id"]],
        include_invariant_ids=[invariant["id"]],
    )
    assert explicit.status_code == 200, explicit.text
    linked = explicit.json()["version"]["structured_spec"]["linked"]
    assert linked["policies"] == [policy["id"]]
    assert linked["invariants"] == [invariant["id"]]

    # Same owner, foreign project asset -> domain error, never linked.
    other_pid = _project(app_client, "v614-other")
    other_invariant = _invariant(app_client, other_pid, title="Other project invariant")
    cross = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
        include_invariant_ids=[other_invariant["id"]],
    )
    assert cross.status_code == 400, cross.text

    # Foreign owner asset -> owner-scoped not-found, never linked.
    before_count = len(
        app_client.get(f"{API}/prompt-capture", params={"project_id": pid}).json()
    )
    with _foreign_owner(app_client):
        foreign_pid = _project(app_client, "v614-foreign")
        foreign_policy = _policy(app_client, foreign_pid, domain="ARCHITECTURE")
    foreign_attempt = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
        include_policy_ids=[foreign_policy["id"]],
    )
    assert foreign_attempt.status_code in (400, 404), foreign_attempt.text
    after_count = len(
        app_client.get(f"{API}/prompt-capture", params={"project_id": pid}).json()
    )
    assert after_count == before_count


# ================================================================== 8 redaction
def test_secret_redaction(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)

    openai_secret = "sk-" + "A" * 24
    github_secret = "ghp_" + "B" * 24
    bearer_token = "abcdef1234567890"
    raw_secrets = (openai_secret, github_secret, "hunter2", bearer_token)

    assistant = (
        "## Role\n"
        f"You are a security reviewer; never print {openai_secret}.\n\n"
        "## Security Rules\n"
        f"- Use the key {openai_secret} only in tests.\n"
        f"- The token {github_secret} is rotated daily.\n"
        "- password=hunter2 must never be logged.\n"
        f"- Authorization: Bearer {bearer_token} is the request header.\n"
    )
    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Check the secret handling."),
        ("assistant", assistant),
    ])
    assistant_id = msgs[1]["id"]

    analysis = _analyze(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id],
    )
    assert analysis.status_code == 200, analysis.text
    body = analysis.json()
    assert body["redaction"]["redacted"] is True
    assert body["redaction"]["matches"] >= 1

    for text in (
        body["content"],
        body["system_prompt"],
        json.dumps(body["structured_spec"]),
    ):
        assert "[REDACTED]" in text
        for raw in raw_secrets:
            assert raw not in text

    saved = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
    )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    version = saved_body["version"]
    capture = saved_body["capture"]
    assert capture["redaction"]["redacted"] is True

    persisted = app_client.get(f"{API}/prompt-capture/{capture['id']}")
    assert persisted.status_code == 200, persisted.text
    persisted_body = persisted.json()

    for text in (
        persisted_body["content"],
        persisted_body["system_prompt"],
        json.dumps(persisted_body["structured_spec"]),
        json.dumps(persisted_body),
        version["content"],
        json.dumps(version["structured_spec"]),
    ):
        assert "[REDACTED]" in text
        for raw in raw_secrets:
            assert raw not in text

    assert redaction.contains_secret(persisted_body["content"]) is False
    assert redaction.contains_secret(json.dumps(persisted_body["structured_spec"])) is False
    assert redaction.contains_secret(version["content"]) is False


# ================================================================== 9 foreign project
def test_foreign_project_rejection(app_client, as_user):
    owner = as_user["id"]
    pid_a = _project(app_client, "v614-a")
    pid_b = _project(app_client, "v614-b")
    conv_a, msgs = _seed_conversation(app_client, owner, pid_a, [
        ("user", "Please implement the capture feature now."),
        ("assistant", _ASSISTANT_SECTIONS),
    ])
    assistant_id = msgs[1]["id"]

    # Same owner, a conversation in project A cannot be captured as project B.
    cross_project = _analyze(
        app_client, pid_b, conv_a["id"],
        mode="response", message_ids=[assistant_id],
    )
    assert cross_project.status_code in (400, 404), cross_project.text

    # A foreign owner cannot analyze or save against A's conversation -> 404.
    with _foreign_owner(app_client):
        foreign_analyze = _analyze(
            app_client, pid_a, conv_a["id"],
            mode="response", message_ids=[assistant_id],
        )
        assert foreign_analyze.status_code == 404, foreign_analyze.text
        foreign_save = _save(
            app_client, pid_a, conv_a["id"],
            mode="response", message_ids=[assistant_id], kind="prompt",
        )
        assert foreign_save.status_code == 404, foreign_save.text

    # Same-owner assets from another project are rejected, never linked.
    block_b = _block(app_client, pid_b)
    policy_b = _policy(app_client, pid_b)
    invariant_b = _invariant(app_client, pid_b)
    for key, asset_id in (
        ("include_block_ids", block_b["id"]),
        ("include_policy_ids", policy_b["id"]),
        ("include_invariant_ids", invariant_b["id"]),
    ):
        rejected = _save(
            app_client, pid_a, conv_a["id"],
            mode="response", message_ids=[assistant_id], kind="prompt",
            **{key: [asset_id]},
        )
        assert rejected.status_code == 400, (key, rejected.text)

    # A foreign-owner asset is owner-scoped not-found, never linked.
    with _foreign_owner(app_client):
        foreign_pid = _project(app_client, "v614-foreign")
        foreign_block = _block(app_client, foreign_pid)
    rejected = _save(
        app_client, pid_a, conv_a["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
        include_block_ids=[foreign_block["id"]],
    )
    assert rejected.status_code in (400, 404), rejected.text


# ================================================================== 10 agent binding
def test_create_agent_prefill_binding(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Please implement the capture feature now."),
        ("assistant", _ASSISTANT_SECTIONS),
    ])
    assistant_id = msgs[1]["id"]

    frozen_save = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
        freeze_version=True,
    )
    assert frozen_save.status_code == 200, frozen_save.text
    frozen_version = frozen_save.json()["version"]
    assert frozen_version["status"] == "frozen"

    draft_save = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[assistant_id], kind="prompt",
        freeze_version=False,
    )
    assert draft_save.status_code == 200, draft_save.text
    draft_version = draft_save.json()["version"]
    assert draft_version["status"] == "draft"

    model_id = _make_model(app_client, owner)
    agent_resp = app_client.post(f"{API}/agents", json={
        "project_id": pid, "name": f"agent-{uuid.uuid4().hex[:6]}",
    })
    assert agent_resp.status_code == 200, agent_resp.text
    agent_id = agent_resp.json()["id"]

    bound = app_client.post(f"{API}/agents/{agent_id}/versions", json={
        "model_id": model_id, "prompt_version_id": frozen_version["id"],
    })
    assert bound.status_code == 200, bound.text
    assert bound.json()["prompt_version_id"] == frozen_version["id"]

    rejected = app_client.post(f"{API}/agents/{agent_id}/versions", json={
        "model_id": model_id, "prompt_version_id": draft_version["id"],
    })
    assert rejected.status_code == 400, rejected.text
    assert "frozen" in rejected.json()["detail"].lower()


# ================================================================== 11 workspace modes
def test_workspace_modes_persistence_and_behavior(app_client, as_user):
    pid = _project(app_client)
    created = app_client.post(f"{API}/conversations", json={
        "project_id": pid, "title": "Modes",
    })
    assert created.status_code == 200, created.text
    conv_id = created.json()["id"]

    patched = app_client.patch(
        f"{API}/conversations/{conv_id}", json={"workspace_mode": "plan"}
    )
    assert patched.status_code == 200, patched.text

    got = app_client.get(f"{API}/conversations/{conv_id}")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["workspace_mode"] == "plan"
    assert body["defaults"]["workspace_mode"] == "plan"

    invalid = app_client.patch(
        f"{API}/conversations/{conv_id}", json={"workspace_mode": "bogus"}
    )
    assert invalid.status_code == 400, invalid.text

    modes = app_client.get(f"{API}/workspace-modes")
    assert modes.status_code == 200, modes.text
    catalog = modes.json()
    assert catalog["default"] == "chat"
    assert {entry["key"] for entry in catalog["modes"]} == {
        "chat", "plan", "research", "audit",
    }

    plan = workspace_modes.compose_system_prompt("plan", "base")
    assert plan.startswith("base")
    assert "WORKSPACE MODE: PLAN" in plan
    assert workspace_modes.compose_system_prompt("chat", "base") == "base"


# ================================================================== 12 isolation
def test_capture_owner_isolation(app_client, as_user):
    owner = as_user["id"]
    pid = _project(app_client)
    conv, msgs = _seed_conversation(app_client, owner, pid, [
        ("user", "Please implement the capture feature now."),
        ("assistant", _ASSISTANT_SECTIONS),
    ])
    saved = _save(
        app_client, pid, conv["id"],
        mode="response", message_ids=[msgs[1]["id"]], kind="prompt",
    )
    assert saved.status_code == 200, saved.text
    capture_id = saved.json()["capture"]["id"]

    with _foreign_owner(app_client):
        hidden = app_client.get(f"{API}/prompt-capture/{capture_id}")
        assert hidden.status_code == 404, hidden.text
        leaked = app_client.get(f"{API}/prompt-capture", params={"project_id": pid})
        assert leaked.status_code == 200, leaked.text
        assert leaked.json() == []

    visible = app_client.get(f"{API}/prompt-capture/{capture_id}")
    assert visible.status_code == 200, visible.text
    assert visible.json()["id"] == capture_id
