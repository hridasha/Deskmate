"""Live team status: delegation parsing, status-question routing, the status snapshot and the Head's tool."""
import json
import time

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import agent
import db
import main
import presence
import router

USER = {"X-User-Id": "presence-user-01"}
U = "presence-user-01"


def workers():
    return {k: v for k, v in agent.load_characters().items() if v.get("role") != "head"}


@pytest.fixture()
def client(clean_db, monkeypatch):
    monkeypatch.setattr(agent, "_mcp_tools", [])
    presence._runs.clear()
    main._recent.clear()
    with TestClient(main.app) as c:
        yield c


def events(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


# ---------- who is being handed work ----------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Ask Oscar to explain inflation", ["oscar_martinez"]),
        ("Ask Oscar and Pam to help", ["oscar_martinez", "pam_beesly"]),
        ("Ask Oscar to explain X and ask Pam to plan Y", ["oscar_martinez", "pam_beesly"]),
        ("have Jim, Dwight and Kevin check it", ["jim_halpert", "dwight_schrute", "kevin_malone"]),
        ("Tell me where Oscar is", []),  # 'tell' is followed by 'me', not a name
        ("Hi Oscar, how are you?", []),
        ("What is Pam doing right now?", []),
    ],
)
def test_delegated_targets_need_a_verb_followed_by_a_name(text, expected):
    assert router.delegated_targets(text, workers()) == expected


# ---------- status questions are answered by the Head, not delegated ----------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "msg",
    ["Who is busy right now?", "Which agents are active?", "What is everyone doing?", "Where is Oscar?", "Tell me where Oscar is", "Is Pam free?", "What is the progress so far?", "Give me a team status update"],
)
async def test_status_questions_get_no_plan(msg):
    tasks, via = await agent.make_plan(msg, workers(), U, "michael_scott", {})
    assert tasks == [] and via == "status"


@pytest.mark.asyncio
@pytest.mark.parametrize("msg", ["How far along is Oscar's task?", "Is Pam still working on the study plan?", "Who's in the conference room?"])
async def test_naming_someone_without_a_delegation_verb_never_uses_the_keyword_router(msg):
    """Regression: 'is Pam still working on the study plan' used to be sent straight to Pam by the router."""
    tasks, via = await agent.make_plan(msg, workers(), U, "michael_scott", {})
    assert tasks == [] and via != "router"


@pytest.mark.asyncio
async def test_real_delegation_still_works_when_the_word_progress_appears():
    tasks, via = await agent.make_plan("Ask Oscar to report progress on the audit", workers(), U, "michael_scott", {})
    assert via == "explicit" and tasks[0].agent_id == "oscar_martinez"


# ---------- the snapshot ----------


def seed():
    now = time.time()
    db.upsert_task("t1", parent="b", user=U, agent="oscar_martinez", task="Explain inflation", status="running", started=now - 7)
    db.upsert_task("t2", parent="b", user=U, agent="pam_beesly", task="Draft a study plan", status="done", result="Day 1 basics.", started=now - 20, ended=now - 5)
    db.upsert_task("t3", parent="b", user=U, agent="stanley_hudson", task="Old job", status="done", result="x", started=now - 900, ended=now - 800)
    presence.begin(U, "kevin_malone", "answering you: add these numbers")
    presence.step(U, "kevin_malone", "search_knowledge")
    presence.report(U, [{"id": "jim_halpert", "zone": "Kitchen", "activity": "Making coffee", "at_desk": False}, {"id": "oscar_martinez", "zone": "Accounting", "activity": "Working", "at_desk": True}])
    presence.report(U, [{"id": "dwight_schrute", "zone": "Warehouse", "activity": "x", "at_desk": False}], ts=time.time() - 300)  # a stale report


def entry(snap, cid):
    return next(e for e in snap["team"] if e["id"] == cid)


def test_snapshot_separates_real_work_from_wandering(clean_db):
    seed()
    snap = presence.snapshot(U, agent.load_characters())
    oscar, pam, kevin, jim = (entry(snap, c) for c in ("oscar_martinez", "pam_beesly", "kevin_malone", "jim_halpert"))
    assert oscar["state"] == "working" and oscar["progress"].startswith("running for") and oscar["zone"] == "Accounting" and oscar["at_desk"] is True
    assert kevin["state"] == "working" and kevin["steps"] == ["searched the documents"]
    assert pam["state"] == "idle" and pam["progress"] == "finished" and pam["last_result"] == "Day 1 basics."
    assert jim["state"] == "idle" and jim["zone"] == "Kitchen" and jim["activity"] == "Making coffee"
    assert snap["working"] == 2


def test_old_tasks_and_stale_locations_are_not_reported(clean_db):
    seed()
    snap = presence.snapshot(U, agent.load_characters())
    assert entry(snap, "stanley_hudson")["doing"] is None  # finished long ago
    dwight = entry(snap, "dwight_schrute")
    assert dwight["zone"] is None  # the report is stale: the office view isn't open
    assert dwight["last_seen_zone"] == "Warehouse" and 290 <= dwight["last_seen_s"] <= 320  # but we know where they were


def test_the_description_is_honest_about_who_is_working(clean_db):
    seed()
    text = presence.describe(U, agent.load_characters())
    assert "2 of" in text and "Oscar Martinez" in text
    assert any(line.startswith("- Oscar Martinez") and "WORKING" in line for line in text.splitlines())
    assert any(line.startswith("- Jim Halpert") and "IDLE" in line and "Kitchen" in line for line in text.splitlines())
    assert "last seen in Warehouse" in text and "location unknown" in text and "Only call someone 'working'" in text


def test_a_users_status_is_private(clean_db):
    seed()
    other = presence.snapshot("someone-else", agent.load_characters())
    assert other["working"] == 0 and all(e["zone"] is None for e in other["team"])


# ---------- API ----------


def test_presence_reports_show_up_in_team_status(client):
    r = client.post("/presence", headers=USER, json=[{"id": "jim_halpert", "zone": "Kitchen", "activity": "Making coffee", "at_desk": False}])
    assert r.status_code == 200
    jim = next(e for e in client.get("/team/status", headers=USER).json()["team"] if e["id"] == "jim_halpert")
    assert jim["zone"] == "Kitchen"
    other = next(e for e in client.get("/team/status", headers={"X-User-Id": "someone-else-1"}).json()["team"] if e["id"] == "jim_halpert")
    assert other["zone"] is None


def test_presence_input_is_validated(client):
    assert client.post("/presence", headers=USER, json=[{"id": "x", "zone": "z" * 200}]).status_code == 422


# ---------- the Head's tool ----------


def test_only_the_head_gets_the_status_tool(client, fake_llm):
    f = fake_llm([AIMessage(content="ok [emotion: neutral]")] * 3)
    client.post("/chat", headers=USER, json={"character_id": "michael_scott", "message": "Good morning!"})
    client.post("/chat", headers=USER, json={"character_id": "jim_halpert", "message": "hi"})
    heads_tools = f.tools_seen[-2] if "team_status" in f.tools_seen[-2] else f.tools_seen[0]
    assert any("team_status" in t for t in f.tools_seen) and "team_status" not in f.tools_seen[-1]
    assert "team_status" in heads_tools


def test_the_head_answers_a_status_question_from_the_live_snapshot(client, fake_llm):
    presence.report(U, [{"id": "jim_halpert", "zone": "Kitchen", "activity": "Making coffee", "at_desk": False}])
    db.upsert_task("t9", parent="b", user=U, agent="oscar_martinez", task="Explain inflation", status="running", started=time.time() - 4)
    f = fake_llm(
        [
            AIMessage(content="", tool_calls=[{"name": "team_status", "args": {}, "id": "c1"}]),
            AIMessage(content="Oscar is working on inflation; Jim is idle in the kitchen. [emotion: neutral]"),
        ]
    )
    evs = events(client.post("/chat", headers=USER, json={"character_id": "michael_scott", "message": "Who is busy right now and where is everyone?"}))
    assert not any(e["state"] in ("plan", "delegate") for e in evs)  # not delegated to anyone
    assert {"state": "tool_call", "tool": "team_status", "kind": "local"} in evs
    tool_msg = next(m for m in f.calls[1] if getattr(m, "name", "") == "team_status")
    assert "Oscar Martinez" in tool_msg.content and "WORKING" in tool_msg.content and "Kitchen" in tool_msg.content
    assert next(e for e in evs if e["state"] == "speaking")["text"].startswith("Oscar is working")


def test_worker_runs_are_visible_while_in_progress_and_cleared_after(client, fake_llm):
    seen = {}

    class Spy:
        async def ainvoke(self, messages, config=None):
            seen["during"] = presence._runs.get((U, "jim_halpert"))
            return AIMessage(content="ok [emotion: neutral]")

    import agent as a

    a.get_llm = lambda *x, **k: Spy()  # noqa: E731 - replaced for this test only
    try:
        client.post("/chat", headers=USER, json={"character_id": "jim_halpert", "message": "hello there"})
    finally:
        import llm

        a.get_llm = llm.get_llm
    assert seen["during"] and "hello there" in seen["during"]["what"]
    assert (U, "jim_halpert") not in presence._runs
