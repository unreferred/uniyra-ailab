"""V5.2 Dataset & Benchmark Registry: regression fixture materialization."""
from __future__ import annotations

from app import repository as repo
from app.deps import get_current_user
from app.services import datasets as datasets_service
from app.services import regression_fixtures as fixtures


def _project(client, name="v5-fixtures"):
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _materialize(client, project_id, name=None):
    payload = {"project_id": project_id}
    if name is not None:
        payload["name"] = name
    r = client.post("/api/datasets/regression-fixtures", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_regression_fixtures_creates_frozen_dataset_with_three_cases(app_client, as_user):
    project = _project(app_client)
    body = _materialize(app_client, project["id"])
    dataset, version, cases = body["dataset"], body["version"], body["cases"]
    assert dataset["project_id"] == project["id"]
    assert dataset["name"] == fixtures.FIXTURE_VERSION_NAME
    assert version["status"] == "frozen"
    assert version["content_hash"]
    assert version["case_count"] == 3
    assert len(cases) == 3
    assert [c["ordering"] for c in cases] == [1, 2, 3]


def test_regression_fixture_cases_carry_policies_and_assertions(app_client, as_user):
    project = _project(app_client, "v5-fixture-policies")
    cases = _materialize(app_client, project["id"])["cases"]
    by_fixture = {c["metadata"]["fixture"]: c for c in cases}

    on = by_fixture["knowledge_on"]
    assert on["knowledge_policy"]["knowledge_expected"] is True
    assert on["knowledge_policy"]["expect_retrieval"] is True
    assert on["knowledge_policy"]["expect_injection"] is True
    assert on["knowledge_policy"]["require_valid_markers"] is True
    assert on["grounding_expectations"]["require_citation"] is True
    assert on["grounding_expectations"]["forbid_fabricated_markers"] is True
    assert on["expected_output"] == fixtures.FIXTURE_NONCE
    assert on["reference_answer"] == fixtures.FIXTURE_NONCE
    assert {"type": "output_contains", "value": fixtures.FIXTURE_NONCE} in on["assertions"]
    assert {"type": "knowledge_marker_valid"} in on["assertions"]
    assert {"type": "grounded_citation"} in on["assertions"]

    off = by_fixture["knowledge_off"]
    assert off["knowledge_policy"]["knowledge_expected"] is False
    assert off["knowledge_policy"]["require_no_knowledge"] is True
    assert {"type": "output_not_contains", "value": fixtures.FIXTURE_NONCE} in off["assertions"]
    assert {"type": "knowledge_absent"} in off["assertions"]

    tools = by_fixture["no_tools"]
    assert tools["tool_policy"]["allow_tools"] is False
    assert tools["tool_policy"]["expected_tool_call_count"] == 0
    assert {"type": "tool_call_count_equals", "value": 0} in tools["assertions"]
    assert {"type": "no_tools"} in tools["assertions"]


def test_frozen_fixture_version_rejects_new_cases(app_client, as_user):
    project = _project(app_client, "v5-fixture-frozen")
    body = _materialize(app_client, project["id"])
    r = app_client.post(
        f"/api/datasets/{body['dataset']['id']}/versions/{body['version']['id']}/cases",
        json={"input_text": "extra"},
    )
    assert r.status_code == 400, r.text


def test_fixture_hash_pins_assertions(app_client, as_user):
    owner = as_user["id"]
    project = _project(app_client, "v5-fixture-hash")
    body = _materialize(app_client, project["id"])
    frozen_hash = body["version"]["content_hash"]

    async def _refreeze_with_changed_assertion():
        dataset = await repo.datasets.get(body["dataset"]["id"], owner)
        draft = await datasets_service.create_version_from(
            owner, dataset, source_version_id=body["version"]["id"])
        case = (await datasets_service.list_cases(owner, draft["id"], limit=100))[0]
        await repo.dataset_version_cases.update(case["id"], owner, {
            "assertions": [{"type": "output_contains", "value": "DIFFERENT-NONCE"}],
        })
        changed = await datasets_service.freeze_version(owner, draft)
        return changed["content_hash"]

    changed_hash = app_client.portal.call(_refreeze_with_changed_assertion)
    assert changed_hash
    assert changed_hash != frozen_hash


def test_get_version_case_is_owner_scoped(app_client, as_user):
    project = _project(app_client, "v5-fixture-get")
    case = _materialize(app_client, project["id"])["cases"][0]
    r = app_client.get(f"/api/datasets/version-cases/{case['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == case["id"]

    original = app_client.app.dependency_overrides[get_current_user]
    app_client.app.dependency_overrides[get_current_user] = lambda: {
        "id": "pytest-foreign-fixture-owner",
        "email": "foreign@example.com",
        "name": "Foreign",
        "role": "researcher",
    }
    try:
        foreign = app_client.get(f"/api/datasets/version-cases/{case['id']}")
        assert foreign.status_code == 404, foreign.text
    finally:
        app_client.app.dependency_overrides[get_current_user] = original


def test_fixture_dataset_is_project_scoped(app_client, as_user):
    project = _project(app_client, "v5-fixture-a")
    other = _project(app_client, "v5-fixture-b")
    body = _materialize(app_client, project["id"])
    listing = app_client.get(f"/api/datasets?project_id={other['id']}")
    assert listing.status_code == 200, listing.text
    assert body["dataset"]["id"] not in {d["id"] for d in listing.json()}


def test_knowledge_setup_nonce_matches_fixture_nonce():
    setup = fixtures.knowledge_setup()
    assert setup["nonce"] == fixtures.FIXTURE_NONCE
    assert fixtures.FIXTURE_NONCE in setup["document_text"]
    assert "knowledge" in setup["instructions"].lower()
