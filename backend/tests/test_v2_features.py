"""Smoke tests for V2 features: Council, Research, Findings, Observability."""
import os
import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/") or os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
# Fall back to reading frontend/.env (same as test_omnilab_api.py)
if not BASE_URL:
    for _env_path in ("/app/frontend/.env", "../frontend/.env", "../../frontend/.env"):
        try:
            with open(_env_path) as f:
                for line in f:
                    if line.startswith("REACT_APP_BACKEND_URL="):
                        BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                        break
            if BASE_URL:
                break
        except Exception:
            continue


def _attach_bearer_auth(session, response):
    """Mirror Secure SameSite=None login cookies as Authorization: Bearer.

    See tests/test_omnilab_api.py: ``requests`` stores Secure cookies but
    never replays them over plain http, so attach the access_token from the
    login Set-Cookie header / session jar as a Bearer header while keeping
    cookie behavior unchanged.
    """
    token = None
    try:
        data = response.json()
        if isinstance(data, dict) and data.get("access_token"):
            token = data["access_token"]
    except Exception:
        pass
    if not token:
        try:
            token = session.cookies.get("access_token")
        except Exception:
            pass
    if not token:
        try:
            raw = getattr(response.raw, "headers", None)
            if raw is not None and hasattr(raw, "getlist"):
                set_cookies = raw.getlist("Set-Cookie")
            else:
                single = response.headers.get("Set-Cookie", "")
                set_cookies = [single] if single else []
            for entry in set_cookies:
                for part in entry.split(";"):
                    part = part.strip()
                    if part.startswith("access_token="):
                        token = part.split("=", 1)[1]
                        break
                if token:
                    break
        except Exception:
            pass
    if token:
        session.headers.update({"Authorization": f"Bearer {token}"})
    return token


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "researcher@omnilab.ai", "password": "omnilab123"})
    assert r.status_code == 200, r.text
    _attach_bearer_auth(s, r)
    return s


@pytest.fixture(scope="module")
def project_id(session):
    r = session.get(f"{BASE_URL}/api/projects")
    assert r.status_code == 200
    projs = r.json()
    if projs:
        return projs[0]["id"]
    r = session.post(f"{BASE_URL}/api/projects", json={"name": "TEST_v2", "description": "test"})
    return r.json()["id"]


@pytest.fixture(scope="module")
def models(session):
    r = session.get(f"{BASE_URL}/api/models")
    assert r.status_code == 200
    return [m for m in r.json() if m.get("enabled")]


# --- Research ---
class TestResearch:
    def test_create_get_update_delete(self, session, project_id):
        r = session.post(f"{BASE_URL}/api/research", json={"project_id": project_id, "title": "TEST_res", "research_question": "q?", "notes": ""})
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        # get
        r = session.get(f"{BASE_URL}/api/research/{rid}")
        assert r.status_code == 200
        assert r.json()["title"] == "TEST_res"
        assert "linked_experiments" in r.json()
        # patch
        r = session.patch(f"{BASE_URL}/api/research/{rid}", json={"notes": "updated notes", "status": "paused"})
        assert r.status_code == 200
        r = session.get(f"{BASE_URL}/api/research/{rid}")
        assert r.json()["notes"] == "updated notes"
        assert r.json()["status"] == "paused"
        # list
        r = session.get(f"{BASE_URL}/api/research?project_id={project_id}")
        assert any(x["id"] == rid for x in r.json())
        # delete
        r = session.delete(f"{BASE_URL}/api/research/{rid}")
        assert r.status_code == 200


# --- Findings ---
class TestFindings:
    def test_create_and_resolve_evidence(self, session, project_id):
        r = session.post(f"{BASE_URL}/api/findings", json={
            "project_id": project_id, "title": "TEST_finding", "statement": "s",
            "confidence": "medium", "status": "provisional",
            "evidence": [{"type": "experiment", "ref_id": "does-not-exist", "label": "ghost"}],
        })
        assert r.status_code == 200, r.text
        fid = r.json()["id"]
        r = session.get(f"{BASE_URL}/api/findings/{fid}")
        assert r.status_code == 200
        assert "resolved_evidence" in r.json()
        # ghost evidence should have exists=false
        assert r.json()["resolved_evidence"][0]["exists"] is False
        # patch status
        r = session.patch(f"{BASE_URL}/api/findings/{fid}", json={"status": "validated"})
        assert r.status_code == 200
        r = session.get(f"{BASE_URL}/api/findings/{fid}")
        assert r.json()["status"] == "validated"
        # cleanup
        session.delete(f"{BASE_URL}/api/findings/{fid}")


# --- Observability ---
class TestObservability:
    def test_shape(self, session):
        r = session.get(f"{BASE_URL}/api/observability")
        assert r.status_code == 200
        d = r.json()
        for k in ("series", "models", "totals"):
            assert k in d
        assert "requests" in d["totals"]
        assert "cost_available" in d["totals"]


# --- Council listing ---
class TestCouncilList:
    def test_list_endpoint(self, session, project_id):
        r = session.get(f"{BASE_URL}/api/councils?project_id={project_id}")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
