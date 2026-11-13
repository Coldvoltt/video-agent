"""Accounts: sign up / sign in with a session cookie, claiming anonymous work, settings, dashboard."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from editor import auth, config, store, worker
from editor.router import router

ANON = {"user_id": "user_anon123"}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def fresh_limits():
    auth._failures.clear()
    yield
    auth._failures.clear()


def sign_up(client, email="maya@example.com", password="correct horse", **extra):
    return client.post("/v2/auth/signup", json={"email": email, "password": password, "name": "Maya", **extra})


def test_sign_up_sign_in_and_out(client):
    assert client.get("/v2/auth/me").json()["user"] is None
    r = sign_up(client, email="  Maya@Example.com ")
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["email"] == "maya@example.com" and user["id"].startswith("acct_")
    cookie = r.cookies.get(auth.SESSION_COOKIE)
    assert cookie and "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert client.get("/v2/auth/me").json()["user"]["id"] == user["id"]

    with store.connect() as c:
        row = c.execute("SELECT password_hash FROM editor_users").fetchone()
        sess = c.execute("SELECT token_hash FROM editor_sessions").fetchone()
    assert row["password_hash"].startswith("scrypt$") and "correct horse" not in row["password_hash"]
    assert sess["token_hash"] != cookie                                   # only a hash of the token is stored

    assert client.post("/v2/auth/logout").status_code == 200
    assert client.get("/v2/auth/me").json()["user"] is None
    assert client.post("/v2/auth/login", json={"email": "maya@example.com", "password": "wrong one"}).status_code == 401
    r = client.post("/v2/auth/login", json={"email": "MAYA@example.com", "password": "correct horse"})
    assert r.status_code == 200 and client.get("/v2/auth/me").json()["user"]["id"] == user["id"]


def test_sign_up_problems(client):
    assert "valid email" in sign_up(client, email="not-an-email").json()["detail"]
    assert "at least 8" in sign_up(client, password="short").json()["detail"]
    assert sign_up(client).status_code == 200
    client.post("/v2/auth/logout")
    assert "already an account" in sign_up(client).json()["detail"]


def test_account_projects_need_the_session(client):
    user = sign_up(client).json()["user"]
    pid = client.post("/v2/projects", json={"name": "Mine"}).json()["id"]
    assert [p["id"] for p in client.get("/v2/projects").json()["projects"]] == [pid]
    stranger = TestClient(client.app)
    r = stranger.get("/v2/projects", params={"user_id": user["id"]})     # knowing the id isn't enough
    assert r.status_code == 401
    assert stranger.get(f"/v2/projects/{pid}", params={"user_id": user["id"]}).status_code == 401
    assert stranger.get("/v2/projects", params=ANON).json()["projects"] == []
    assert stranger.get("/v2/projects").status_code == 401                # no session, no id


def test_require_account_refuses_anonymous(client, monkeypatch):
    monkeypatch.setenv("EDITOR_REQUIRE_ACCOUNT", "1")
    assert client.get("/v2/projects", params=ANON).status_code == 401
    assert client.get("/v2/auth/me").json()["require_account"] is True
    sign_up(client)
    assert client.get("/v2/projects").status_code == 200


def test_signing_up_claims_anonymous_work(client, media_dir):
    pid = client.post("/v2/projects", params=ANON, json={"name": "Before signing up"}).json()["id"]
    with open(media_dir / "landscape.mp4", "rb") as f:
        aid = client.post(f"/v2/projects/{pid}/assets/upload", params={**ANON, "transcribe": False},
                          files={"file": ("clip.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    old_dir = config.settings.storage_root / "users" / ANON["user_id"]
    assert old_dir.exists()

    r = sign_up(client, claim_user_id=ANON["user_id"])
    assert r.json()["projects_moved"] == 1
    uid = r.json()["user"]["id"]
    assert [p["id"] for p in client.get("/v2/projects").json()["projects"]] == [pid]
    asset = store.get_asset(aid)
    assert asset["user_id"] == uid and asset["storage_key"].startswith(f"users/{uid}/")
    assert asset["proxy_key"] is None or asset["proxy_key"].startswith(f"users/{uid}/")
    assert not old_dir.exists()
    file = client.get(f"/v2/projects/{pid}/assets/{aid}/file")
    assert file.status_code == 200 and len(file.content) > 1000          # the moved file is still served
    assert TestClient(client.app).get("/v2/projects", params=ANON).json()["projects"] == []   # the old id is empty now

    # claiming can't take another account's work, nor an account id
    other = TestClient(client.app)
    r2 = sign_up(other, email="sam@example.com", claim_user_id=uid)
    assert r2.json()["projects_moved"] == 0
    assert other.get("/v2/projects").json()["projects"] == []


def test_settings_and_profile(client):
    sign_up(client)
    me = client.get("/v2/me").json()
    assert me["settings"]["integrations"]["telegram"]["notify"] is False
    r = client.patch("/v2/me", json={"name": "Maya O.", "settings": {
        "integrations": {"telegram": {"notify": True, "bot_token": "x"}, "slack": {"on": True}}, "admin": True}})
    assert r.status_code == 200
    s = r.json()["settings"]
    assert s == {"integrations": {"telegram": {"notify": True}}}           # unknown keys are dropped
    assert r.json()["user"]["name"] == "Maya O."
    bad_type = client.patch("/v2/me", json={"settings": {"integrations": {"telegram": {"notify": "yes"}}}})
    assert bad_type.json()["settings"]["integrations"]["telegram"]["notify"] is True
    assert client.patch("/v2/me", json={"current_password": "nope", "new_password": "a new password"}).status_code == 400
    assert client.patch("/v2/me", json={"current_password": "correct horse", "new_password": "a new password"}).status_code == 200
    client.post("/v2/auth/logout")
    assert client.post("/v2/auth/login", json={"email": "maya@example.com", "password": "a new password"}).status_code == 200
    assert TestClient(client.app).get("/v2/me").status_code == 401


def test_dashboard(client, media_dir):
    sign_up(client)
    pid = client.post("/v2/projects", json={"name": "Vlog"}).json()["id"]
    with open(media_dir / "landscape.mp4", "rb") as f:
        aid = client.post(f"/v2/projects/{pid}/assets/upload", params={"transcribe": False},
                          files={"file": ("clip.mp4", f)}).json()["asset"]["id"]
    worker.run_until_empty()
    client.put(f"/v2/projects/{pid}/timeline", json={"timeline": {"clips": [{"id": "c1", "asset_id": aid, "in": 0, "out": 4}]},
                                                     "note": "First cut"})
    (card,) = client.get("/v2/dashboard").json()["projects"]
    assert card["name"] == "Vlog" and card["videos"] == 1 and card["version"] == 1
    assert card["last_change"] == "First cut" and card["duration"] == pytest.approx(4.0)
    assert card["thumb"]["id"] == aid


def test_brute_force_guard(client):
    sign_up(client)
    client.post("/v2/auth/logout")
    for _ in range(auth.LIMIT):
        client.post("/v2/auth/login", json={"email": "maya@example.com", "password": "guess"})
    r = client.post("/v2/auth/login", json={"email": "maya@example.com", "password": "correct horse"})
    assert r.status_code == 429
