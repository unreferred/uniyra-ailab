"""V6.15 Structured Planning Graph API / integration tests.

Covers the derived graph view over the V6.14 decision ledger: sync/generation
and idempotency, decision provenance preservation, confirmed-vs-assumed
semantics, conflict and supersession handling, edge validity, impact analysis
(which must never mutate the ledger), clear-review, catalog, node detail and
owner/project isolation.

Everything runs offline against the shared ``app_client``/``as_user`` fixtures.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

from app import repository as repo
from app.deps import get_current_user
from app.models import (
    PLANNING_NODE_STATUSES,
    PLANNING_NODE_TYPES,
    PLANNING_RELATIONS,
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
        "title": "Graph source",
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


def _project(app_client, name="pytest-v615g"):
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


def _resolve_conflict(app_client, conflict_id, resolution, value=""):
    return app_client.post(
        f"{API}/planning-state/conflicts/{conflict_id}/resolve",
        json={"resolution": resolution, "value": value},
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


def _node(app_client, node_id):
    return app_client.get(f"{API}/planning-state/graph/nodes/{node_id}")


def _impact(app_client, project_id, **overrides):
    payload = {"project_id": project_id}
    payload.update(overrides)
    return app_client.post(f"{API}/planning-state/impact", json=payload)


def _clear_review(app_client, node_id, project_id):
    return app_client.post(
        f"{API}/planning-state/graph/nodes/{node_id}/clear-review",
        json={"project_id": project_id},
    )


def _by_decision(nodes, decision_id):
    return [n for n in nodes if n.get("decision_id") == decision_id]


def _section_of(body, key):
    return next(s for s in body["sections"] if s["key"] == key)


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
def test_graph_catalog(app_client, as_user):
    r = app_client.get(f"{API}/planning-state/graph/catalog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema_version"] == "v6.15.1"
    assert set(body["node_types"]) == set(PLANNING_NODE_TYPES)
    assert set(body["relations"]) == set(PLANNING_RELATIONS)
    assert set(body["statuses"]) == set(PLANNING_NODE_STATUSES)
    assert len(body["sections"]) == 15
    keys = {entry["key"] for entry in body["sections"]}
    assert {"product", "auth", "authorization", "security", "data"} <= keys


# ================================================================== isolation
def test_graph_project_isolation(app_client, as_user):
    pid_a = _project(app_client, "v615g-a")
    pid_b = _project(app_client, "v615g-b")
    conv, _ = _seed_conversation(app_client, as_user["id"], pid_a, [])
    item = _create_item(
        app_client, pid_a, conv["id"], topic_key="platform.mobile",
        category="platform", value="Android",
    ).json()["item"]

    assert _sync(app_client, pid_a).status_code == 200
    body_b = _graph(app_client, pid_b).json()
    assert _by_decision(body_b["nodes"], item["id"]) == []
    assert body_b["counts"]["decisions"] == 0

    node = _by_decision(_graph(app_client, pid_a).json()["nodes"], item["id"])[0]

    with _foreign_owner(app_client):
        assert _graph(app_client, pid_a).status_code == 404
        assert _node(app_client, node["id"]).status_code == 404
        assert _impact(app_client, pid_a, decision_id=item["id"]).status_code == 404
        assert _clear_review(app_client, node["id"], pid_a).status_code == 404
        assert _sync(app_client, pid_a).status_code == 404


# ================================================================== generation
def test_graph_sync_generates_and_is_idempotent(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    item = _create_item(
        app_client, pid, conv["id"], topic_key="platform.mobile",
        category="platform", value="Android",
    ).json()["item"]

    first = _sync(app_client, pid)
    assert first.status_code == 200, first.text
    body = first.json()

    types = {n["type"] for n in body["nodes"]}
    assert {"ROOT", "SECTION", "DECISION"} <= types
    node_keys = {n["node_key"] for n in body["nodes"]}
    assert "root" in node_keys
    assert "section:platform" in node_keys
    assert f"decision:{item['id']}" in node_keys
    assert body["counts"]["decisions"] == 1
    assert body["counts"]["edges"] > 0
    assert len(node_keys) == len(body["nodes"])  # no duplicate node keys

    first_ids = {n["id"] for n in body["nodes"]}
    second = _sync(app_client, pid).json()
    assert second["counts"] == body["counts"]
    assert {n["id"] for n in second["nodes"]} == first_ids  # ids stable, no dupes


# ================================================================== provenance
def test_decision_provenance_preserved(app_client, as_user):
    pid = _project(app_client)
    conv, msgs = _seed_conversation(
        app_client, as_user["id"], pid, [("user", "Android only")]
    )
    assert _ingest(app_client, pid, conv["id"]).status_code == 200
    state = _state(app_client, pid, conv["id"]).json()
    ledger_item = next(
        i for i in state["confirmed_decisions"] if i["topic_key"] == "platform.mobile"
    )
    assert ledger_item["source_message_id"] == msgs[0]["id"]

    body = _graph(app_client, pid).json()
    node = _by_decision(body["nodes"], ledger_item["id"])[0]
    assert node["decision_id"] == ledger_item["id"]
    assert node["source_message_id"] == ledger_item["source_message_id"]
    assert node["provenance"]["message_id"] == msgs[0]["id"]
    assert node["provenance"]["excerpt"]

    detail = _node(app_client, node["id"])
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["node"]["id"] == node["id"]
    assert payload["decision"]["id"] == ledger_item["id"]
    assert payload["decision"]["status"] == ledger_item["status"] == "CONFIRMED_BY_USER"
    assert payload["decision"]["value"] == ledger_item["value"] == "Android"
    assert payload["provenance"]["message_id"] == msgs[0]["id"]
    assert {"node", "decision", "provenance", "dependencies", "dependents",
            "affected", "history"} <= set(payload)
    assert any(d["node_key"].startswith("section:platform") for d in payload["dependents"])


# ================================================================== statuses
def test_confirmed_and_assumed_nodes_stay_distinct(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [
        ("user", "Android only"),
        ("assistant", "Assumption: we might use a shared tablet"),
    ])
    assert _ingest(app_client, pid, conv["id"]).status_code == 200
    state = _state(app_client, pid, conv["id"]).json()
    confirmed_item = next(
        i for i in state["confirmed_decisions"] if i["topic_key"] == "platform.mobile"
    )
    assumption_item = state["assumptions"][0]

    body = _graph(app_client, pid).json()
    confirmed_node = _by_decision(body["nodes"], confirmed_item["id"])[0]
    assumption_node = _by_decision(body["nodes"], assumption_item["id"])[0]

    assert confirmed_node["type"] == "DECISION"
    assert confirmed_node["status"] == "CONFIRMED_BY_USER"
    assert assumption_node["type"] == "ASSUMPTION"
    assert assumption_node["status"] == "ASSUMED_BY_MODEL"
    assert "shared tablet" in assumption_node["content"]
    assert assumption_node["type"] != confirmed_node["type"]
    assert assumption_node["status"] != "CONFIRMED_BY_USER"

    assert _section_of(body, "platform")["counts"]["confirmed"] == 1
    assert body["counts"]["assumptions"] == 1


# ================================================================== conflicts
def test_conflict_node_and_open_conflict_surfaced(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    previous, new, conflict = _make_conflict(app_client, pid, conv["id"])

    body = _sync(app_client, pid).json()
    conflicted_node = _by_decision(body["nodes"], new["id"])[0]
    previous_node = _by_decision(body["nodes"], previous["id"])[0]
    assert conflicted_node["status"] == "CONFLICTED"

    conflict_edges = [e for e in body["edges"] if e["relation"] == "CONFLICTS_WITH"]
    assert any(
        e["source_node_id"] == conflicted_node["id"]
        and e["target_node_id"] == previous_node["id"]
        for e in conflict_edges
    )
    assert body["counts"]["conflicts"] >= 1

    # The confirmed predecessor is unchanged in the ledger and the graph.
    detail = _node(app_client, previous_node["id"]).json()
    assert detail["decision"]["status"] == "CONFIRMED_BY_USER"
    assert detail["decision"]["value"] == "Android"


# ================================================================== supersession
def test_superseded_node_excluded_or_marked(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    previous, new, conflict = _make_conflict(app_client, pid, conv["id"])
    # Sync before resolving so the previous decision node exists.
    assert _sync(app_client, pid).status_code == 200
    assert _resolve_conflict(app_client, conflict["id"], "accept_new").status_code == 200

    body = _sync(app_client, pid).json()
    stale = _by_decision(body["nodes"], previous["id"])
    assert all(n["status"] == "SUPERSEDED" for n in stale)

    active = _by_decision(body["nodes"], new["id"])[0]
    assert active["status"] == "CONFIRMED_BY_USER"

    # Ledger history is retained and surfaced on the new node.
    history = _node(app_client, active["id"]).json()["history"]
    entry = next(h for h in history if h["decision_id"] == previous["id"])
    assert entry["status"] == "SUPERSEDED"
    assert entry["superseded_by_id"] == new["id"]
    assert _get_ledger_item(app_client, previous["id"]).json()["status"] == "SUPERSEDED"
    # The resolved conflict no longer produces an open CONFLICTS_WITH edge.
    assert body["counts"]["conflicts"] == 0


# ================================================================== edge validity
def test_graph_edges_reference_existing_nodes(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    _create_item(
        app_client, pid, conv["id"], topic_key="platform.mobile",
        category="platform", value="Android",
    )
    _create_item(
        app_client, pid, conv["id"], topic_key="data.retention",
        category="data", value="30 days",
    )
    body = _sync(app_client, pid).json()
    node_ids = {n["id"] for n in body["nodes"]}
    assert body["edges"]
    for edge in body["edges"]:
        assert edge["relation"] in PLANNING_RELATIONS
        assert edge["source_node_id"] in node_ids
        assert edge["target_node_id"] in node_ids
        assert edge["project_id"] == pid


# ================================================================== impact
def test_impact_analysis_flags_areas_without_touching_ledger(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    item = _create_item(
        app_client, pid, conv["id"], topic_key="auth.method", category="auth",
        kind="decision", value="fingerprint only", status="CONFIRMED_BY_USER",
        source_type="manual",
    ).json()["item"]

    assert _sync(app_client, pid).status_code == 200
    before = _get_ledger_item(app_client, item["id"]).json()

    r = _impact(app_client, pid, decision_id=item["id"])
    assert r.status_code == 200, r.text
    impact = r.json()
    assert impact["count"] >= 1
    assert impact["count"] == len(impact["affected_areas"])
    assert impact["message"] == (
        f"{impact['count']} planning areas may require revalidation."
    )
    assert impact["changed_section"]["key"] == "auth"
    assert impact["decision"]["id"] == item["id"]

    section_keys = {
        a["section_key"] for a in impact["affected_areas"]
        if a["reason"] == "section may require revalidation"
    }
    assert {"auth", "authorization", "security", "data"} <= section_keys
    assert all(
        a["review_required"] for a in impact["affected_areas"]
        if a["reason"] == "section may require revalidation"
    )

    # The authoritative ledger row is untouched.
    after = _get_ledger_item(app_client, item["id"]).json()
    assert after["status"] == before["status"] == "CONFIRMED_BY_USER"
    assert after["value"] == before["value"] == "fingerprint only"

    # The changed section node is sticky-flagged in the graph.
    graph = _graph(app_client, pid).json()
    assert _section_of(graph, "auth")["review_required"] is True

    # Clear-review resets the flag; sync must not re-add it.
    cleared = _clear_review(app_client, _section_of(graph, "auth")["node_id"], pid)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["review_required"] is False
    refreshed = _graph(app_client, pid).json()
    assert _section_of(refreshed, "auth")["review_required"] is False


def test_impact_by_node_and_missing_target(app_client, as_user):
    pid = _project(app_client)
    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    item = _create_item(
        app_client, pid, conv["id"], topic_key="auth.method", category="auth",
        value="fingerprint only",
    ).json()["item"]
    node = _by_decision(_sync(app_client, pid).json()["nodes"], item["id"])[0]

    by_node = _impact(app_client, pid, node_id=node["id"])
    assert by_node.status_code == 200, by_node.text
    assert by_node.json()["decision"]["id"] == item["id"]

    assert _impact(app_client, pid, decision_id="bogus").status_code == 404
    assert _impact(app_client, pid, node_id="bogus").status_code == 404
    assert _impact(app_client, pid).status_code == 404


# ================================================================== request errors
def test_graph_request_errors(app_client, as_user):
    pid = _project(app_client)
    assert _graph(app_client, "does-not-exist").status_code == 404
    assert _node(app_client, "does-not-exist").status_code == 404
    assert _clear_review(app_client, "does-not-exist", pid).status_code == 404

    conv, _ = _seed_conversation(app_client, as_user["id"], pid, [])
    item = _create_item(
        app_client, pid, conv["id"], topic_key="manual.graph", value="v",
    ).json()["item"]
    node = _by_decision(_sync(app_client, pid).json()["nodes"], item["id"])[0]
    other = _project(app_client, "v615g-other")
    assert _clear_review(app_client, node["id"], other).status_code == 404
