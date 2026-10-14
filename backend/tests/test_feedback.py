"""Thumbs up/down and promoting feedback into eval cases."""
import pytest
from fastapi.testclient import TestClient

import agent
import feedback
import main

U = {"X-User-Id": "fb-user-00001"}


@pytest.fixture()
def client(clean_db, monkeypatch):
    monkeypatch.setattr(agent, "_mcp_tools", [])
    return TestClient(main.app)


def post(client, rating, comment="", reply="Leave is 20 days.", headers=U):
    return client.post("/feedback", headers=headers, json={"agent_id": "pam_beesly", "message": "How much leave do I get?", "reply": reply, "rating": rating, "comment": comment})


def test_ratings_are_counted_per_agent_and_private_to_the_user(client):
    post(client, 1)
    post(client, -1, "should say 25 days")
    assert client.post("/feedback", headers=U, json={"agent_id": "pam_beesly", "message": "m", "reply": "r", "rating": 5}).status_code == 422
    s = client.get("/feedback/summary", headers=U).json()
    assert (s["up"], s["down"]) == (1, 1) and s["by_agent"][0] == {"agent": "pam_beesly", "up": 1, "down": 1}
    assert s["needs_attention"][0]["comment"] == "should say 25 days"
    other = client.get("/feedback/summary", headers={"X-User-Id": "fb-user-00002"}).json()
    assert (other["up"], other["down"]) == (0, 0)


def test_a_thumbs_down_becomes_an_eval_case_with_the_users_expectations(client):
    fid = post(client, -1, "It should say 25 days of leave").json()["id"]
    case = client.post(f"/feedback/promote/{fid}", headers=U).json()
    assert case["id"] == f"fb-{fid}" and case["agent"] == "pam_beesly" and case["message"] == "How much leave do I get?"
    assert "25" in case["expect"]["contains_all"] and case["expect"]["not_similar_to"] == "Leave is 20 days."
    assert [c["id"] for c in feedback.eval_cases()] == [case["id"]]
    assert client.post("/feedback/promote/nope", headers=U).status_code == 404
    assert client.get("/feedback/summary", headers=U).json()["needs_attention"][0]["promoted"] is True


def test_the_eval_check_rejects_the_same_bad_answer_and_requires_the_wanted_terms():
    from evals.run import check

    case = {"expect": {"not_similar_to": "Leave is 20 days.", "contains_all": ["25"]}}
    got = lambda reply: {"reply": reply, "error": "", "tools": [], "plan": None, "emotion": ""}  # noqa: E731
    assert not all(c["ok"] for c in check(case, got("Leave is 20 days."), "u"))
    assert all(c["ok"] for c in check(case, got("You get 25 days of leave per year, plus public holidays."), "u"))


def test_viewers_can_rate_answers(clean_db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "on")
    c = TestClient(main.app)
    admin = c.post("/auth/signup", json={"email": "a@example.com", "password": "correct horse"}).json()["token"]
    code = c.post("/auth/invites", headers={"Authorization": f"Bearer {admin}"}, json={"role": "viewer"}).json()["code"]
    viewer = {"Authorization": "Bearer " + c.post("/auth/signup", json={"email": "v@example.com", "password": "correct horse", "invite": code}).json()["token"]}
    fid = post(c, -1, headers=viewer).json()["id"]
    assert c.post(f"/feedback/promote/{fid}", headers=viewer).status_code == 403  # only admins change the test suite
