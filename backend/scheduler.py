"""Scheduled tasks and triggers.

* Schedules: "every day at 09:00, ask Dwight for the sales summary" -> the answer is delivered to Slack, email or a webhook.
* Triggers: a secret URL (POST /hooks/<token>) that asks an agent a question. Works as a Slack slash command or from any
  automation (Zapier, GitHub, a mail-forwarding rule): send {"message": "..."} and get {"reply": "..."} back.
* A background loop (every 30 s) runs what is due, and re-syncs documents (docsync) every SYNC_INTERVAL_MIN minutes.

Outbound calls go through the SSRF-guarded client. Slack links must be hooks.slack.com. Email needs SMTP_HOST (and friends).
"""
import asyncio
import json
import os
import re
import secrets
import smtplib
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlparse

import agent as agents
import db
import docsync
import safe_url
import secrets_store

MIN_INTERVAL = 5  # minutes
MAX_SCHEDULES = 20
MAX_TRIGGERS = 10
EMAILS_PER_DAY = 20
_sent: dict[str, list[float]] = {}


# ---------------- when is the next run ----------------


def valid_spec(spec: dict) -> str | None:
    """Return a problem description, or None if the schedule is fine."""
    kind = spec.get("kind")
    if kind == "interval":
        if not isinstance(spec.get("every_min"), int) or not MIN_INTERVAL <= spec["every_min"] <= 7 * 24 * 60:
            return f"Run at most every {MIN_INTERVAL} minutes and at least once a week."
        return None
    if kind in ("daily", "weekly"):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(spec.get("at", ""))):
            return "Give the time as HH:MM, for example 09:00."
        if kind == "weekly" and not (spec.get("days") and all(isinstance(d, int) and 0 <= d <= 6 for d in spec["days"])):
            return "Pick at least one weekday."
        if not -14 * 60 <= int(spec.get("tz_offset_min", 0)) <= 14 * 60:
            return "That time zone offset isn't valid."
        return None
    return "Choose interval, daily or weekly."


def next_run(spec: dict, after: float) -> float:
    if spec["kind"] == "interval":
        return after + spec["every_min"] * 60
    tz = timezone(timedelta(minutes=int(spec.get("tz_offset_min", 0))))
    hh, mm = (int(x) for x in spec["at"].split(":"))
    local = datetime.fromtimestamp(after, tz)
    for add in range(0, 9):
        day = (local + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if day.timestamp() > after and (spec["kind"] == "daily" or day.weekday() in spec["days"]):
            return day.timestamp()
    return after + 86400  # unreachable for valid specs


def describe(spec: dict) -> str:
    if spec["kind"] == "interval":
        return f"every {spec['every_min']} minutes"
    days = "every day" if spec["kind"] == "daily" else "on " + ", ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] for d in sorted(spec["days"]))
    return f"{days} at {spec['at']}"


# ---------------- delivery ----------------


def check_delivery(deliver: dict) -> str | None:
    slack = deliver.get("slack_url", "")
    if slack:
        p = urlparse(slack)
        if p.scheme != "https" or p.hostname != "hooks.slack.com":
            return "A Slack link must look like https://hooks.slack.com/services/..."
    hook = deliver.get("webhook_url", "")
    if hook:
        try:
            safe_url.check_url(hook)
        except safe_url.BlockedHost as e:
            return str(e)
    emails = deliver.get("emails", [])
    if emails:
        if not os.getenv("SMTP_HOST"):
            return "Email isn't set up on this server (SMTP_HOST is missing)."
        if len(emails) > 5 or not all(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e) for e in emails):
            return "Use up to 5 valid email addresses."
    return None


def _send_email(to: list[str], subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "deskmates@localhost"))
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject[:150]
    msg.set_content(body)
    host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=20) as s:
        if os.getenv("SMTP_STARTTLS", "true").lower() != "false":
            s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
        s.send_message(msg)


async def deliver(scope: str, title: str, text: str, cfg: dict) -> list[str]:
    """Send a result to every configured place. Returns a note for each failure (empty = all delivered)."""
    problems: list[str] = []
    if cfg.get("slack_url"):
        try:
            async with safe_url.guarded_client(timeout=15) as c:
                r = await c.post(cfg["slack_url"], json={"text": f"*{title}*\n{text}"[:3500]})
            if r.status_code >= 300:
                problems.append(f"Slack answered HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"Slack failed ({type(e).__name__})")
    if cfg.get("webhook_url"):
        try:
            async with safe_url.guarded_client(timeout=15) as c:
                r = await c.post(cfg["webhook_url"], json={"title": title, "text": text})
            if r.status_code >= 300:
                problems.append(f"Webhook answered HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"Webhook failed ({type(e).__name__})")
    if cfg.get("emails"):
        today = [t for t in _sent.get(scope, []) if time.time() - t < 86400]
        if len(today) >= EMAILS_PER_DAY:
            problems.append("Daily email limit reached")
        else:
            try:
                await asyncio.to_thread(_send_email, cfg["emails"], title, text)
                today.append(time.time())
            except Exception as e:  # noqa: BLE001
                problems.append(f"Email failed ({type(e).__name__})")
            _sent[scope] = today
    return problems


def _seal(cfg: dict) -> str:
    """Delivery settings contain secret links, so they are stored encrypted."""
    return secrets_store.encrypt(json.dumps(cfg))


def _open(blob: str | None) -> dict:
    try:
        return json.loads(secrets_store.decrypt(blob) or "{}") if blob else {}
    except json.JSONDecodeError:
        return {}


def _mask(cfg: dict) -> dict:
    """What the UI may see: where results go, without the secret parts of the links."""
    return {"slack": bool(cfg.get("slack_url")), "webhook": urlparse(cfg["webhook_url"]).hostname if cfg.get("webhook_url") else None, "emails": cfg.get("emails", [])}


# ---------------- running an agent without a browser ----------------


async def ask(scope: str, agent_id: str, message: str) -> str:
    character = agents.load_characters(scope).get(agent_id)
    if not character:
        raise LookupError("That agent no longer exists.")
    if db.usage_today(scope) >= int(os.getenv("DAILY_LIMIT", "200")):
        raise RuntimeError("The daily message limit is used up.")
    db.bump_usage(scope)
    reply = ""
    async for ev in agents.agent_events(character, message, scope):
        if ev["state"] == "speaking":
            reply = ev["text"]
        elif ev["state"] == "error":
            raise RuntimeError(ev.get("message", "The agent failed."))
    return reply or "(no answer)"


# ---------------- schedules ----------------


def _public(r: dict) -> dict:
    spec = json.loads(r["spec"])
    return {"id": r["id"], "agent": r["agent"], "message": r["message"], "spec": spec, "when": describe(spec), "deliver": _mask(_open(r["deliver"])), "enabled": bool(r["enabled"]), "next_run": r["next_run"], "last_run": r["last_run"], "last_status": r["last_status"], "last_result": r["last_result"]}


def list_schedules(scope: str) -> list[dict]:
    return [_public(r) for r in db.q("SELECT * FROM schedules WHERE user=? ORDER BY created", (scope,))]


def create_schedule(scope: str, agent_id: str, message: str, spec: dict, deliver_cfg: dict) -> dict:
    if agent_id not in agents.load_characters(scope):
        raise ValueError("Pick an agent that exists.")
    if db.q("SELECT COUNT(*) AS n FROM schedules WHERE user=?", (scope,))[0]["n"] >= MAX_SCHEDULES:
        raise ValueError(f"You can keep up to {MAX_SCHEDULES} schedules.")
    problem = valid_spec(spec) or check_delivery(deliver_cfg)
    if problem:
        raise ValueError(problem)
    sid = uuid.uuid4().hex[:10]
    now = time.time()
    db.run("INSERT INTO schedules (id, user, agent, message, spec, deliver, enabled, next_run, created) VALUES (?,?,?,?,?,?,1,?,?)", (sid, scope, agent_id, message[:2000], json.dumps(spec), _seal(deliver_cfg), next_run(spec, now), now))
    return _public(db.q("SELECT * FROM schedules WHERE id=?", (sid,))[0])


def set_enabled(scope: str, sid: str, enabled: bool) -> dict | None:
    rows = db.q("SELECT * FROM schedules WHERE id=? AND user=?", (sid, scope))
    if not rows:
        return None
    db.run("UPDATE schedules SET enabled=?, next_run=? WHERE id=?", (int(enabled), next_run(json.loads(rows[0]["spec"]), time.time()), sid))
    return _public(db.q("SELECT * FROM schedules WHERE id=?", (sid,))[0])


def delete_schedule(scope: str, sid: str) -> bool:
    if not db.q("SELECT 1 FROM schedules WHERE id=? AND user=?", (sid, scope)):
        return False
    db.run("DELETE FROM schedules WHERE id=?", (sid,))
    return True


async def run_schedule(sid: str) -> dict:
    """Run one schedule now: ask the agent, deliver the answer, record what happened. Never raises."""
    rows = db.q("SELECT * FROM schedules WHERE id=?", (sid,))
    if not rows:
        return {"status": "missing"}
    r = rows[0]
    spec = json.loads(r["spec"])
    now = time.time()
    db.run("UPDATE schedules SET next_run=? WHERE id=?", (next_run(spec, now), sid))  # move on first, so a slow run isn't started twice
    try:
        reply = await asyncio.wait_for(ask(r["user"], r["agent"], r["message"]), timeout=180)
        problems = await deliver(r["user"], f"Deskmates: {r['message'][:60]}", reply, _open(r["deliver"]))
        status = "ok" if not problems else "delivered with problems: " + "; ".join(problems)
    except Exception as e:  # noqa: BLE001
        reply, status = "", f"failed: {str(e)[:160] or type(e).__name__}"
    db.run("UPDATE schedules SET last_run=?, last_status=?, last_result=? WHERE id=?", (now, status, reply[:1000], sid))
    return {"status": status, "reply": reply}


async def run_due() -> int:
    due = db.q("SELECT id FROM schedules WHERE enabled=1 AND next_run<=?", (time.time(),))
    for d in due:
        await run_schedule(d["id"])
    return len(due)


# ---------------- inbound triggers ----------------


def _trigger_public(r: dict) -> dict:
    return {"id": r["id"], "name": r["name"], "agent": r["agent"], "token": r["token"], "deliver": _mask(_open(r["deliver"])), "created": r["created"], "last_used": r["last_used"], "calls": r["calls"]}


def list_triggers(scope: str) -> list[dict]:
    return [_trigger_public(r) for r in db.q("SELECT * FROM triggers WHERE user=? ORDER BY created", (scope,))]


def create_trigger(scope: str, name: str, agent_id: str, deliver_cfg: dict) -> dict:
    if agent_id not in agents.load_characters(scope):
        raise ValueError("Pick an agent that exists.")
    if db.q("SELECT COUNT(*) AS n FROM triggers WHERE user=?", (scope,))[0]["n"] >= MAX_TRIGGERS:
        raise ValueError(f"You can keep up to {MAX_TRIGGERS} triggers.")
    problem = check_delivery(deliver_cfg)
    if problem:
        raise ValueError(problem)
    tid = uuid.uuid4().hex[:10]
    db.run("INSERT INTO triggers (id, user, name, agent, token, deliver, created, calls) VALUES (?,?,?,?,?,?,?,0)", (tid, scope, name.strip()[:60] or "Trigger", agent_id, secrets.token_urlsafe(24), _seal(deliver_cfg), time.time()))
    return _trigger_public(db.q("SELECT * FROM triggers WHERE id=?", (tid,))[0])


def delete_trigger(scope: str, tid: str) -> bool:
    if not db.q("SELECT 1 FROM triggers WHERE id=? AND user=?", (tid, scope)):
        return False
    db.run("DELETE FROM triggers WHERE id=?", (tid,))
    return True


def trigger_by_token(token: str) -> dict | None:
    rows = db.q("SELECT * FROM triggers WHERE token=?", (token,))
    return rows[0] if rows else None


async def fire(trigger: dict, message: str) -> str:
    """Ask the trigger's agent and deliver the answer to the trigger's destinations too. Returns the reply."""
    db.run("UPDATE triggers SET last_used=?, calls=calls+1 WHERE id=?", (time.time(), trigger["id"]))
    reply = await asyncio.wait_for(ask(trigger["user"], trigger["agent"], message), timeout=120)
    cfg = _open(trigger["deliver"])
    if cfg:
        await deliver(trigger["user"], f"{trigger['name']}: {message[:60]}", reply, cfg)
    return reply


async def post_to_response_url(url: str, text: str) -> None:
    """Slack slash commands give a one-time URL to answer later (it must be on slack.com)."""
    host = urlparse(url).hostname or ""
    if not (urlparse(url).scheme == "https" and (host == "slack.com" or host.endswith(".slack.com"))):
        return
    async with safe_url.guarded_client(timeout=15) as c:
        await c.post(url, json={"response_type": "in_channel", "text": text[:3500]})


# ---------------- the loop ----------------

_last_sync = 0.0


async def loop() -> None:
    global _last_sync
    interval = int(os.getenv("SYNC_INTERVAL_MIN", "30"))
    while True:
        try:
            await run_due()
            if interval > 0 and time.time() - _last_sync >= interval * 60:
                _last_sync = time.time()
                await docsync.sync_all()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  one bad pass must not stop the loop
            print(f"Scheduler pass failed: {e!r}")
        await asyncio.sleep(30)
