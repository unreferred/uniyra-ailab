"""OmniLab backend API tests — auth, seeding, projects, registry, workspace, arena, judge, experiments, ownership."""
import os
import time
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or os.environ.get("BASE_URL", "").rstrip("/")
# Fall back to reading frontend/.env
if not BASE_URL:
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass

API = f"{BASE_URL}/api"
ADMIN_EMAIL = "researcher@omnilab.ai"
ADMIN_PASSWORD = "omnilab123"


def _attach_bearer_auth(session, response):
    """Mirror Secure SameSite=None login cookies as Authorization: Bearer.

    The backend sets ``Secure`` cookies which ``requests`` stores but never
    replays over plain http (e.g. BASE_URL=http://localhost:8001), so every
    authenticated request would 401. ``app/deps.py:get_current_user`` also
    accepts ``Authorization: Bearer <access_token>``, so extract the token
    from the login response (Set-Cookie header / session jar / login JSON)
    and attach it as a default session header. Cookie behavior is unchanged
    (jar still holds the cookies for https runs).
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


# ---------------- fixtures
@pytest.fixture(scope="session")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    assert "access_token" in s.cookies, "access_token cookie not set"
    assert "refresh_token" in s.cookies, "refresh_token cookie not set"
    _attach_bearer_auth(s, r)
    return s


@pytest.fixture(scope="session")
def new_user_session():
    s = requests.Session()
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "testpass123", "name": "Test User"},
               timeout=30)
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"
    _attach_bearer_auth(s, r)
    s._email = email
    return s


@pytest.fixture(scope="session")
def project_id(admin_session):
    r = admin_session.post(f"{API}/projects",
                            json={"name": f"TEST_proj_{uuid.uuid4().hex[:6]}", "description": "pytest"})
    assert r.status_code == 200
    return r.json()["id"]


# ---------------- AUTH & seeding
class TestAuth:
    def test_admin_login_and_me(self, admin_session):
        r = admin_session.get(f"{API}/auth/me")
        assert r.status_code == 200
        me = r.json()
        assert me["email"] == ADMIN_EMAIL
        assert "id" in me

    def test_register_new_user_seeds(self, new_user_session):
        r = new_user_session.get(f"{API}/auth/me")
        assert r.status_code == 200
        # no providers are seeded by default (BYOK only)
        providers = new_user_session.get(f"{API}/providers").json()
        assert providers == []
        models = new_user_session.get(f"{API}/models").json()
        assert models == []
        # rubrics seeded
        rubrics = new_user_session.get(f"{API}/rubrics").json()
        assert len(rubrics) >= 1

    def test_invalid_login(self):
        r = requests.post(f"{API}/auth/login",
                          json={"email": "nonexistent@x.com", "password": "wrong"}, timeout=15)
        assert r.status_code == 401

    def test_me_requires_auth(self):
        r = requests.get(f"{API}/auth/me", timeout=15)
        assert r.status_code == 401


# ---------------- REGISTRY
class TestRegistry:
    def test_list_seeded_models_admin(self, admin_session):
        r = admin_session.get(f"{API}/models")
        assert r.status_code == 200
        models = r.json()
        assert len(models) >= 3
        assert all("owner_id" in m and "provider_id" in m for m in models)

    def test_openai_compatible_requires_base_url(self, admin_session):
        r = admin_session.post(f"{API}/providers", json={
            "name": "TEST_bad_openai", "provider_type": "openai_compatible"
        })
        assert r.status_code == 400
        assert "base_url" in r.text.lower()

    def test_create_openai_compatible_provider(self, admin_session):
        r = admin_session.post(f"{API}/providers", json={
            "name": f"TEST_openai_{uuid.uuid4().hex[:6]}",
            "provider_type": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-fake",
        })
        assert r.status_code == 200
        p = r.json()
        assert p["provider_type"] == "openai_compatible"
        assert p["has_api_key"] is True

    def test_model_toggle_enable(self, admin_session):
        models = admin_session.get(f"{API}/models").json()
        m = models[0]
        r = admin_session.patch(f"{API}/models/{m['id']}", json={"enabled": not m["enabled"]})
        assert r.status_code == 200
        assert r.json()["enabled"] == (not m["enabled"])
        # revert
        admin_session.patch(f"{API}/models/{m['id']}", json={"enabled": m["enabled"]})

    def test_model_connection_test_universal(self, admin_session):
        models = admin_session.get(f"{API}/models").json()
        target = models[0]
        r = admin_session.post(f"{API}/models/{target['id']}/test", timeout=90)
        assert r.status_code == 200
        body = r.json()
        # ok may be True/False depending on real API; the endpoint should return structured payload
        assert "ok" in body and "latency_ms" in body
        if not body["ok"]:
            pytest.skip(f"model test not ok (real LLM): {body.get('error')}")


# ---------------- PROJECTS
class TestProjects:
    def test_create_and_get(self, admin_session, project_id):
        r = admin_session.get(f"{API}/projects/{project_id}")
        assert r.status_code == 200
        data = r.json()
        assert "stats" in data
        assert data["id"] == project_id

    def test_rename(self, admin_session, project_id):
        r = admin_session.patch(f"{API}/projects/{project_id}", json={"name": "TEST_renamed"})
        assert r.status_code == 200
        assert r.json()["name"] == "TEST_renamed"

    def test_delete_project(self, admin_session):
        r = admin_session.post(f"{API}/projects", json={"name": "TEST_todelete"})
        pid = r.json()["id"]
        r = admin_session.delete(f"{API}/projects/{pid}")
        assert r.status_code == 200
        r = admin_session.get(f"{API}/projects/{pid}")
        assert r.status_code == 404


# ---------------- WORKSPACE
class TestWorkspace:
    def test_workspace_send_and_artifact(self, admin_session, project_id):
        models = admin_session.get(f"{API}/models").json()
        model = next((m for m in models if m.get("enabled")), models[0])
        # create conversation
        r = admin_session.post(f"{API}/conversations", json={
            "project_id": project_id, "title": "TEST_conv"})
        assert r.status_code == 200
        conv_id = r.json()["id"]
        r = admin_session.post(f"{API}/conversations/{conv_id}/messages",
                                json={"model_id": model["id"], "content": "Say hi in 3 words.",
                                      "max_tokens": 20})
        assert r.status_code == 200, r.text
        data = r.json()
        run = data["run"]
        if run["status"] != "success":
            pytest.skip(f"real LLM error: {run.get('error')}")
        assert data["assistant_message"]["content"]
        assert run.get("latency_ms") is not None
        # save as artifact
        r = admin_session.post(f"{API}/artifacts", json={
            "project_id": project_id, "title": "TEST_art",
            "content": data["assistant_message"]["content"],
            "source": "workspace", "source_ref": run["id"]})
        assert r.status_code == 200
        aid = r.json()["id"]
        # verify listed
        lst = admin_session.get(f"{API}/artifacts", params={"project_id": project_id}).json()
        assert any(a["id"] == aid for a in lst)


# ---------------- ARENA + JUDGE + EXPERIMENTS
class TestArena:
    @pytest.fixture(scope="class")
    def arena_run(self, admin_session, project_id):
        models = admin_session.get(f"{API}/models").json()
        picks = [m["id"] for m in models if m.get("enabled")][:2]
        assert len(picks) >= 2, "need at least 2 enabled models"
        r = admin_session.post(f"{API}/arena/run", json={
            "project_id": project_id, "task": "In one short sentence, what is 2+2?",
            "model_ids": picks, "blind": True,
            "params": {"max_tokens": 30}}, timeout=180)
        assert r.status_code == 200, r.text
        return r.json()

    def test_arena_run_produces_runs(self, arena_run):
        assert "experiment" in arena_run and "runs" in arena_run
        assert len(arena_run["runs"]) == 2
        assert arena_run["experiment"]["blind"] is True

    def test_experiment_listed(self, admin_session, arena_run):
        exp_id = arena_run["experiment"]["id"]
        r = admin_session.get(f"{API}/experiments")
        assert r.status_code == 200
        assert any(e["id"] == exp_id for e in r.json())

    def test_experiment_blind_and_reveal(self, admin_session, arena_run):
        exp_id = arena_run["experiment"]["id"]
        r = admin_session.get(f"{API}/experiments/{exp_id}", params={"reveal": False})
        assert r.status_code == 200
        blind = r.json()
        assert blind["revealed"] is False
        # runs should have label "Response A/B", no model_id
        for run in blind["runs"]:
            assert run.get("label", "").startswith("Response ")
            assert not run.get("model_name")
        # reveal
        r2 = admin_session.get(f"{API}/experiments/{exp_id}", params={"reveal": True})
        rev = r2.json()
        assert rev["revealed"] is True
        assert any(run.get("model_name") for run in rev["runs"])

    def test_ai_judge(self, admin_session, arena_run):
        exp_id = arena_run["experiment"]["id"]
        models = admin_session.get(f"{API}/models").json()
        rubrics = admin_session.get(f"{API}/rubrics").json()
        judge_model = models[0]["id"]
        rubric_id = rubrics[0]["id"]
        r = admin_session.post(f"{API}/experiments/{exp_id}/judge",
                                json={"judge_model_id": judge_model, "rubric_id": rubric_id},
                                timeout=180)
        assert r.status_code == 200, r.text
        ev = r.json()
        assert isinstance(ev.get("eval_type"), str) and ev["eval_type"]
        # Verify structured shape
        assert "id" in ev

    def test_rerun_creates_new_experiment(self, admin_session, arena_run):
        exp_id = arena_run["experiment"]["id"]
        r = admin_session.post(f"{API}/experiments/{exp_id}/rerun", timeout=180)
        assert r.status_code == 200
        new_exp = r.json()["experiment"]
        assert new_exp["id"] != exp_id
        # parent link is set after run_arena; verify via GET
        r_new = admin_session.get(f"{API}/experiments/{new_exp['id']}")
        assert r_new.status_code == 200
        assert r_new.json().get("parent_experiment_id") == exp_id
        # original still exists
        r2 = admin_session.get(f"{API}/experiments/{exp_id}")
        assert r2.status_code == 200

    def test_clone(self, admin_session, arena_run):
        exp_id = arena_run["experiment"]["id"]
        r = admin_session.post(f"{API}/experiments/{exp_id}/clone")
        assert r.status_code == 200
        clone = r.json()
        assert clone["status"] == "draft"
        assert clone["cloned_from"] == exp_id


# ---------------- OVERVIEW
class TestOverview:
    def test_overview(self, admin_session):
        r = admin_session.get(f"{API}/overview")
        assert r.status_code == 200
        data = r.json()
        assert "counts" in data and "recent_experiments" in data and "model_usage" in data
        assert data["counts"]["models"] >= 3


# ---------------- OWNERSHIP
class TestOwnership:
    def test_cross_user_isolation(self, admin_session, new_user_session, project_id):
        # new user should NOT be able to fetch admin's project
        r = new_user_session.get(f"{API}/projects/{project_id}")
        assert r.status_code == 404, f"expected 404, got {r.status_code}"

    def test_cross_user_experiment_isolation(self, admin_session, new_user_session):
        exps = admin_session.get(f"{API}/experiments").json()
        if not exps:
            pytest.skip("no experiments to test")
        eid = exps[0]["id"]
        r = new_user_session.get(f"{API}/experiments/{eid}")
        assert r.status_code == 404
