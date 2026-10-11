from unittest.mock import AsyncMock
from app import repository as repo
from app.models import now_iso, new_id
from tests.conftest import call


def test_bulk_delete_conversations_success(app_client, as_user):
    user_id = as_user["id"]
    p_id = f"p-{new_id()}"
    # Create 3 conversations in p_id with dynamic IDs
    cid1, cid2, cid3 = f"c-{new_id()}", f"c-{new_id()}", f"c-{new_id()}"
    c1 = {"id": cid1, "project_id": p_id, "owner_id": user_id, "title": "Conv 1", "created_at": now_iso(), "updated_at": now_iso()}
    c2 = {"id": cid2, "project_id": p_id, "owner_id": user_id, "title": "Conv 2", "created_at": now_iso(), "updated_at": now_iso()}
    c3 = {"id": cid3, "project_id": p_id, "owner_id": user_id, "title": "Conv 3", "created_at": now_iso(), "updated_at": now_iso()}

    call(app_client, repo.conversations.insert, c1)
    call(app_client, repo.conversations.insert, c2)
    call(app_client, repo.conversations.insert, c3)

    # Add messages
    m1 = {"id": f"m-{new_id()}", "owner_id": user_id, "conversation_id": cid1, "role": "user", "content": "hi", "created_at": now_iso()}
    m2 = {"id": f"m-{new_id()}", "owner_id": user_id, "conversation_id": cid2, "role": "user", "content": "hello", "created_at": now_iso()}
    call(app_client, repo.messages.insert, m1)
    call(app_client, repo.messages.insert, m2)

    resp = app_client.post("/api/conversations/bulk-delete", json={
        "conversation_ids": [cid1, cid2],
        "project_id": p_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted_count"] == 2
    assert cid1 in data["deleted_ids"]
    assert cid2 in data["deleted_ids"]

    # Verify c3 still exists
    remaining = call(app_client, repo.conversations.get, cid3, user_id)
    assert remaining is not None
    # Verify c1 and c2 deleted
    assert call(app_client, repo.conversations.get, cid1, user_id) is None
    assert call(app_client, repo.conversations.get, cid2, user_id) is None

    # Verify messages deleted
    msgs_c1 = call(app_client, repo.messages.list, {"conversation_id": cid1})
    assert len(msgs_c1) == 0


def test_bulk_delete_cross_project_and_unauthorized(app_client, as_user):
    user_id = as_user["id"]
    p1 = f"p1-{new_id()}"
    p2 = f"p2-{new_id()}"
    cid_p1 = f"c-p1-{new_id()}"
    cid_p2 = f"c-p2-{new_id()}"
    cid_other = f"c-other-{new_id()}"
    cid_missing = f"c-missing-{new_id()}"

    # Conversation in project 1 owned by user
    c_p1 = {"id": cid_p1, "project_id": p1, "owner_id": user_id, "title": "Conv P1", "created_at": now_iso(), "updated_at": now_iso()}
    # Conversation in project 2 owned by user
    c_p2 = {"id": cid_p2, "project_id": p2, "owner_id": user_id, "title": "Conv P2", "created_at": now_iso(), "updated_at": now_iso()}
    # Conversation owned by another user
    c_other = {"id": cid_other, "project_id": p1, "owner_id": "other-user-999", "title": "Other", "created_at": now_iso(), "updated_at": now_iso()}

    call(app_client, repo.conversations.insert, c_p1)
    call(app_client, repo.conversations.insert, c_p2)
    call(app_client, repo.conversations.insert, c_other)

    # Try to bulk delete with project_id = p1, including c_p2, c_other, and a non-existent ID, plus duplicate cid_p1
    resp = app_client.post("/api/conversations/bulk-delete", json={
        "conversation_ids": [cid_p1, cid_p2, cid_other, cid_missing, cid_p1],
        "project_id": p1,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    # Only c-p1 should have been deleted
    assert data["deleted_count"] == 1
    assert data["deleted_ids"] == [cid_p1]
    assert cid_p2 in data["unauthorized_ids"]
    assert cid_other in data["unauthorized_ids"]
    assert cid_missing in data["missing_ids"]

    # Verify c_p2 and c_other are NOT deleted
    assert call(app_client, repo.conversations.get, cid_p2, user_id) is not None
    assert call(app_client, repo.conversations.get, cid_other, "other-user-999") is not None
    # Clean up c_other since as_user teardown only purges user_id
    call(app_client, repo.conversations.delete, cid_other, "other-user-999")


def test_continue_conversation_endpoint(app_client, as_user, monkeypatch):
    user_id = as_user["id"]
    p_id = f"p-{new_id()}"
    cid = f"c-{new_id()}"
    mid = f"m-cont-{new_id()}"
    prov_id = f"prov-{new_id()}"

    conv = {
        "id": cid,
        "project_id": p_id,
        "owner_id": user_id,
        "title": "Continue test",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    call(app_client, repo.conversations.insert, conv)

    # Model doc
    model = {
        "id": mid,
        "owner_id": user_id,
        "name": "Model Continue",
        "provider_id": prov_id,
        "enabled": True,
    }
    provider = {
        "id": prov_id,
        "owner_id": user_id,
        "name": "Provider 1",
        "enabled": True,
    }
    call(app_client, repo.models.insert, model)
    call(app_client, repo.providers.insert, provider)

    # Historical messages: user then partial assistant
    u_msg = {"id": f"m-u-{new_id()}", "owner_id": user_id, "conversation_id": cid, "project_id": p_id, "role": "user", "content": "Calculate 1 to 5", "created_at": now_iso()}
    a_id = f"m-a-{new_id()}"
    a_msg = {"id": a_id, "owner_id": user_id, "conversation_id": cid, "project_id": p_id, "role": "assistant", "content": "1, 2, 3,", "run_id": "r-prior-1", "created_at": now_iso()}
    call(app_client, repo.messages.insert, u_msg)
    call(app_client, repo.messages.insert, a_msg)

    mock_run = {
        "id": "r-new-continue",
        "model_id": mid,
        "model_name": "Model Continue",
        "status": "success",
        "normalized_response": " 4, 5.",
        "parent_run_id": "r-prior-1",
        "latency_ms": 100,
    }

    from app.routers import workspace as ws_router
    monkeypatch.setattr(ws_router, "execute_resumable_model", AsyncMock(return_value=mock_run))

    resp = app_client.post(f"/api/conversations/{cid}/continue", json={
        "model_id": mid,
        "continuation_prompt": "Continue counting",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Unifies into exactly one persistent logical assistant message
    assert data["assistant_message"]["id"] == a_id
    assert data["assistant_message"]["content"] == "1, 2, 3, 4, 5."
    assert data["assistant_message"]["continued"] is True
    assert data["assistant_message"]["continued_from_message_id"] == a_id
    assert data["assistant_message"]["segments"] == ["1, 2, 3,", " 4, 5."]
    assert data["assistant_message"]["run_chain"] == ["r-prior-1", "r-new-continue"]
    assert data["run"]["id"] == "r-new-continue"

    # Verify repo has exactly 2 messages (1 user, 1 assistant) - no duplicate assistant rows
    all_msgs = call(app_client, repo.messages.list, {"conversation_id": cid, "owner_id": user_id})
    assert len(all_msgs) == 2
