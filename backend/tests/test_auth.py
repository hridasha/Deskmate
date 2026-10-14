"""Accounts, workspaces and roles (AUTH_MODE=on)."""
import pytest
from fastapi.testclient import TestClient

import agent
import main


@pytest.fixture()
def client(clean_db, monkeypatch):
    import auth

    auth._failures.clear()
    monkeypatch.setenv("AUTH_MODE", "on")
    monkeypatch.setattr(agent, "_mcp_tools", [])
    return TestClient(main.app)


def signup(client, email, **kw):
    r = client.post("/auth/signup", json={"email": email, "password": "correct horse", "name": email.split("@")[0], **kw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_everything_needs_sign_in_except_the_public_paths(client):
    assert client.get("/health").status_code == 200
    assert client.get("/auth/config").json()["auth"] is True
    assert client.get("/characters").status_code == 401
    assert client.get("/characters", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_signup_login_and_wrong_password(client):
    h = signup(client, "boss@example.com")
    assert client.get("/auth/me", headers=h).json()["role"] == "admin"
    assert client.post("/auth/login", json={"email": "boss@example.com", "password": "correct horse"}).status_code == 200
    assert client.post("/auth/login", json={"email": "boss@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/auth/signup", json={"email": "boss@example.com", "password": "correct horse"}).status_code == 400  # taken
    assert client.post("/auth/signup", json={"email": "nope", "password": "correct horse"}).status_code == 400
    assert client.post("/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code == 400


def test_repeated_wrong_passwords_are_throttled(client):
    signup(client, "boss@example.com")
    codes = [client.post("/auth/login", json={"email": "boss@example.com", "password": "bad"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_passwords_are_not_stored_in_plain_text(client):
    signup(client, "boss@example.com")
    import db

    stored = db.q("SELECT pw_hash FROM users")[0]["pw_hash"]
    assert stored.startswith("scrypt$") and "correct horse" not in stored


def test_workspaces_are_isolated_and_agents_are_private_to_their_workspace(client):
    a, b = signup(client, "a@example.com"), signup(client, "b@example.com")
    r = client.post("/agents", headers=a, json={"name": "Zed Zeta", "archetype": "a test agent", "color": "#112233", "personality": "Helpful and short."})
    assert r.status_code == 200
    names = lambda h: {c["name"] for c in client.get("/characters", headers=h).json()}  # noqa: E731
    assert "Zed Zeta" in names(a) and "Zed Zeta" not in names(b)
    assert client.delete(f"/agents/{r.json()['id']}", headers=b).status_code == 404  # can't remove someone else's hire
    assert client.delete(f"/agents/{r.json()['id']}", headers=a).status_code == 200


def test_invites_roles_and_the_viewer_cannot_change_things(client):
    admin = signup(client, "boss@example.com")
    code = client.post("/auth/invites", headers=admin, json={"role": "viewer"}).json()["code"]
    viewer = signup(client, "view@example.com", invite=code)
    assert client.get("/auth/me", headers=viewer).json()["role"] == "viewer"
    assert client.post("/auth/signup", json={"email": "again@example.com", "password": "correct horse", "invite": code}).status_code == 400  # single use
    assert client.get("/characters", headers=viewer).status_code == 200  # reads are fine, and it is the same workspace
    assert client.post("/agents", headers=viewer, json={"name": "Nope", "archetype": "x", "color": "#112233", "personality": "hi there"}).status_code == 403
    assert client.get("/settings/keys", headers=viewer).status_code == 403
    assert client.get("/auth/members", headers=viewer).status_code == 403


def test_member_can_build_but_not_reach_admin_areas(client):
    admin = signup(client, "boss@example.com")
    code = client.post("/auth/invites", headers=admin, json={"role": "member"}).json()["code"]
    member = signup(client, "mem@example.com", invite=code)
    assert client.post("/agents", headers=member, json={"name": "Mem Hire", "archetype": "helper", "color": "#112233", "personality": "Friendly and clear."}).status_code == 200
    assert client.post("/settings/keys", headers=member, json={"provider": "gemini", "key": "x" * 30}).status_code == 403
    assert client.post("/knowledge/databases", headers=member, json={"name": "d", "url": "sqlite:///x.db"}).status_code == 403
    assert client.post("/evals/retrieval/run", headers=member).status_code == 403  # evaluation runs are admin-only too
    assert client.post("/evals/run", headers=member).status_code == 403
    # the team shares agents
    assert "Mem Hire" in {c["name"] for c in client.get("/characters", headers=admin).json()}


def test_admin_manages_members_but_the_last_admin_stays(client):
    admin = signup(client, "boss@example.com")
    code = client.post("/auth/invites", headers=admin, json={"role": "member"}).json()["code"]
    signup(client, "mem@example.com", invite=code)
    members = client.get("/auth/members", headers=admin).json()["members"]
    boss = next(m for m in members if m["role"] == "admin")
    mem = next(m for m in members if m["role"] == "member")
    assert client.patch(f"/auth/members/{boss['id']}", headers=admin, json={"role": "viewer"}).status_code == 400
    assert client.patch(f"/auth/members/{mem['id']}", headers=admin, json={"role": "viewer"}).status_code == 200
    assert client.delete(f"/auth/members/{boss['id']}", headers=admin).status_code == 400
    assert client.delete(f"/auth/members/{mem['id']}", headers=admin).status_code == 200


def test_signup_can_be_restricted_to_invites(client, monkeypatch):
    signup(client, "boss@example.com")
    monkeypatch.setenv("OPEN_SIGNUP", "false")
    assert client.post("/auth/signup", json={"email": "x@example.com", "password": "correct horse"}).status_code == 403


def test_with_auth_off_nothing_changes_and_accounts_are_refused(clean_db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "off")
    c = TestClient(main.app)
    assert c.get("/characters").status_code == 200
    assert c.post("/auth/signup", json={"email": "a@b.co", "password": "correct horse"}).status_code == 400
