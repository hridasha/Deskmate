"""Scheduled tasks, inbound triggers (webhooks / Slack) and delivery."""
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import agent
import db
import main
import scheduler

U = {"X-User-Id": "sched-user-001"}
SLACK = "https://hooks.slack.com/services/T000/B000/secretsecret"


def ts(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


@pytest.fixture()
def client(clean_db, monkeypatch):
    monkeypatch.setattr(agent, "_mcp_tools", [])
    main._recent.clear()
    return TestClient(main.app)


@pytest.fixture()
def sent(monkeypatch):
    """Capture deliveries instead of calling Slack, a webhook or an SMTP server."""
    out = []

    async def fake_deliver(scope, title, text, cfg):
        out.append({"title": title, "text": text, "cfg": cfg})
        return []

    monkeypatch.setattr(scheduler, "deliver", fake_deliver)
    return out


# ---------- when ----------


def test_daily_and_weekly_next_run_respect_time_and_timezone():
    daily = {"kind": "daily", "at": "09:00", "tz_offset_min": 0}
    assert scheduler.next_run(daily, ts(2026, 10, 14, 8, 0)) == ts(2026, 10, 14, 9, 0)  # later today
    assert scheduler.next_run(daily, ts(2026, 10, 14, 9, 0)) == ts(2026, 10, 15, 9, 0)  # already passed: tomorrow
    ist = {"kind": "daily", "at": "09:00", "tz_offset_min": 330}  # 09:00 in UTC+5:30 is 03:30 UTC
    assert scheduler.next_run(ist, ts(2026, 10, 14, 0, 0)) == ts(2026, 10, 14, 3, 30)
    weekdays = {"kind": "weekly", "at": "09:00", "days": [0, 2], "tz_offset_min": 0}  # Monday and Wednesday
    assert datetime.fromtimestamp(scheduler.next_run(weekdays, ts(2026, 10, 14, 10, 0)), timezone.utc).weekday() == 0  # Wed 14th passed, so next Monday
    assert scheduler.next_run({"kind": "interval", "every_min": 15}, 1000.0) == 1900.0


@pytest.mark.parametrize(
    "spec",
    [{"kind": "interval", "every_min": 1}, {"kind": "interval", "every_min": "x"}, {"kind": "daily", "at": "25:00"}, {"kind": "weekly", "at": "09:00", "days": []}, {"kind": "daily", "at": "09:00", "tz_offset_min": 5000}, {"kind": "cron"}],
)
def test_bad_schedules_are_refused(spec):
    assert scheduler.valid_spec(spec)


def test_delivery_destinations_are_checked(monkeypatch):
    assert scheduler.check_delivery({"slack_url": "https://evil.example.com/hook"})
    assert scheduler.check_delivery({"slack_url": "http://hooks.slack.com/x"})
    assert scheduler.check_delivery({"slack_url": SLACK}) is None
    assert "internal" in scheduler.check_delivery({"webhook_url": "http://169.254.169.254/x"})
    assert "SMTP" in scheduler.check_delivery({"emails": ["a@b.co"]})  # email isn't configured
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    assert scheduler.check_delivery({"emails": ["a@b.co"]}) is None
    assert scheduler.check_delivery({"emails": ["not-an-email"]})
    assert scheduler.check_delivery({"emails": [f"a{i}@b.co" for i in range(6)]})


# ---------- schedules ----------


def make(client, **kw):
    body = {"agent_id": "pam_beesly", "message": "Summarise today's reception log", "kind": "daily", "at": "09:00", "deliver": {"slack_url": SLACK}, **kw}
    return client.post("/schedules", headers=U, json=body)


def test_create_list_toggle_delete_and_secrets_stay_hidden(client):
    r = make(client)
    assert r.status_code == 200 and r.json()["when"] == "every day at 09:00" and r.json()["deliver"] == {"slack": True, "webhook": None, "emails": []}
    assert "secretsecret" not in client.get("/schedules", headers=U).text  # the Slack link is never sent back
    assert "secretsecret" not in db.q("SELECT deliver FROM schedules")[0]["deliver"]  # and is encrypted at rest
    sid = r.json()["id"]
    assert client.patch(f"/schedules/{sid}?enabled=false", headers=U).json()["enabled"] is False
    assert client.delete(f"/schedules/{sid}", headers=U).status_code == 200
    assert client.get("/schedules", headers=U).json()["schedules"] == []


def test_validation_and_privacy(client):
    assert make(client, agent_id="nobody").status_code == 400
    assert make(client, kind="interval", every_min=2).status_code == 400
    assert make(client, deliver={"slack_url": "https://evil.example.com/x"}).status_code == 400
    sid = make(client).json()["id"]
    other = {"X-User-Id": "sched-user-002"}
    assert client.get("/schedules", headers=other).json()["schedules"] == []
    assert client.delete(f"/schedules/{sid}", headers=other).status_code == 404
    assert client.post(f"/schedules/{sid}/run", headers=other).status_code == 404


def test_run_now_asks_the_agent_delivers_and_records(client, fake_llm, sent):
    fake_llm([AIMessage(content="Three visitors signed in today. [emotion: happy]")])
    sid = make(client).json()["id"]
    res = client.post(f"/schedules/{sid}/run", headers=U).json()
    assert res["status"] == "ok" and "Three visitors" in res["reply"]
    assert sent[0]["text"].startswith("Three visitors") and sent[0]["cfg"]["slack_url"] == SLACK
    row = client.get("/schedules", headers=U).json()["schedules"][0]
    assert row["last_status"] == "ok" and row["last_run"] and "Three visitors" in row["last_result"]


def test_a_failing_run_is_recorded_not_raised(client, fake_llm, sent, monkeypatch):
    fake_llm([])
    sid = make(client).json()["id"]

    async def boom(*a, **k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(scheduler, "ask", boom)
    res = client.post(f"/schedules/{sid}/run", headers=U).json()
    assert res["status"].startswith("failed: model unavailable") and sent == []


@pytest.mark.asyncio
async def test_run_due_runs_only_due_enabled_schedules(client, fake_llm, sent):
    fake_llm([AIMessage(content=f"done {i} [emotion: neutral]") for i in range(3)])
    a, b = make(client).json()["id"], make(client).json()["id"]
    db.run("UPDATE schedules SET next_run=? WHERE id=?", (time.time() - 5, a))
    db.run("UPDATE schedules SET next_run=? WHERE id=?", (time.time() - 5, b))
    client.patch(f"/schedules/{b}?enabled=false", headers=U)
    db.run("UPDATE schedules SET next_run=? WHERE id=?", (time.time() - 5, b))  # disabling resets it; make it overdue again
    assert await scheduler.run_due() == 1
    assert len(sent) == 1
    assert db.q("SELECT next_run FROM schedules WHERE id=?", (a,))[0]["next_run"] > time.time()  # rescheduled for tomorrow


# ---------- inbound triggers ----------


def make_trigger(client, **kw):
    r = client.post("/triggers", headers=U, json={"name": "Ops bot", "agent_id": "pam_beesly", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_webhook_gets_an_answer_without_signing_in(client, fake_llm, sent):
    fake_llm([AIMessage(content="Dunder Mifflin, this is Pam. [emotion: happy]")])
    t = make_trigger(client, deliver={"slack_url": SLACK})
    r = client.post(f"/hooks/{t['token']}", json={"message": "Who is on reception?"})  # no X-User-Id, no token
    assert r.status_code == 200 and "this is Pam" in r.json()["reply"]
    assert sent and sent[0]["cfg"]["slack_url"] == SLACK  # the answer was also posted to Slack
    assert client.get("/triggers", headers=U).json()["triggers"][0]["calls"] == 1


def test_unknown_tokens_and_empty_messages_are_rejected(client):
    assert client.post("/hooks/not-a-real-token", json={"message": "hi"}).status_code == 404
    t = make_trigger(client)
    assert client.post(f"/hooks/{t['token']}", json={}).status_code == 400
    assert client.delete(f"/triggers/{t['id']}", headers=U).status_code == 200
    assert client.post(f"/hooks/{t['token']}", json={"message": "hi"}).status_code == 404  # deleting revokes it


def test_slack_slash_commands_are_acknowledged_then_answered_later(client, fake_llm, monkeypatch):
    fake_llm([AIMessage(content="Bears, beets, Battlestar Galactica. [emotion: happy]")])
    t = make_trigger(client)
    posted = []

    async def fake_post(url, text):
        posted.append((url, text))

    monkeypatch.setattr(scheduler, "post_to_response_url", fake_post)
    with TestClient(main.app) as c:  # a real event loop so the background answer runs
        r = c.post(f"/hooks/{t['token']}", data={"text": "favourite things?", "response_url": "https://hooks.slack.com/commands/1/2"})
        assert r.json()["response_type"] == "ephemeral"
        for _ in range(50):
            if posted:
                break
            time.sleep(0.1)
    assert posted and "Bears" in posted[0][1]


@pytest.mark.asyncio
async def test_slack_response_urls_must_be_on_slack(monkeypatch):
    called = []

    def no_client(**kw):
        called.append(1)
        raise AssertionError("must not call out")

    monkeypatch.setattr(scheduler.safe_url, "guarded_client", no_client)
    await scheduler.post_to_response_url("https://evil.example.com/steal", "secret")
    await scheduler.post_to_response_url("http://hooks.slack.com/x", "secret")
    assert called == []


def test_hook_traffic_is_rate_limited_per_trigger(client, fake_llm, sent):
    fake_llm([AIMessage(content=f"ok {i} [emotion: neutral]") for i in range(40)])
    t = make_trigger(client)
    resp = [client.post(f"/hooks/{t['token']}", json={"message": "ping"}) for _ in range(main.MINUTE_LIMIT + 2)]
    codes = [r.status_code for r in resp]
    print(resp[0].text)
    assert codes[0] == 200 and codes[-1] == 429, [r.text for r in resp[:3]]
