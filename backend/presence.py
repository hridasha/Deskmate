"""Live office awareness: who is doing what, how far along it is, and where everyone is right now.

Two sources are combined:
- the server knows about REAL work: delegated tasks, tool runs and the steps taken so far;
- the office view reports where each agent is standing and what they appear to be doing, since movement is simulated there.
Wandering around is reported as idle, so the Head never presents the decorative office life as real work.
Locations are saved, so when the office view is closed the Head can still say where someone was last seen.
"""
import time

import db

STALE_S = 20  # a report older than this means the office view isn't open right now
FORGET_S = 6 * 3600  # ...and after this long a "last seen" is no longer worth mentioning

_runs: dict[tuple[str, str], dict] = {}  # (user, agent) -> {what, started, steps}


def report(user: str, items: list[dict], ts: float | None = None) -> None:
    """Save where each agent is (called every few seconds by the office view)."""
    now = ts if ts is not None else time.time()
    rows = [(user, str(it["id"]), str(it.get("zone", ""))[:60], str(it.get("activity", ""))[:80], int(bool(it.get("at_desk"))), now) for it in items[:40]]
    with db._lock:
        conn = db.get()
        conn.executemany("INSERT INTO presence (user, agent, zone, activity, at_desk, ts) VALUES (?,?,?,?,?,?) ON CONFLICT(user, agent) DO UPDATE SET zone=excluded.zone, activity=excluded.activity, at_desk=excluded.at_desk, ts=excluded.ts", rows)
        conn.commit()


def begin(user: str, agent: str, what: str) -> None:
    _runs[(user, agent)] = {"what": what[:100], "started": time.time(), "steps": []}


def step(user: str, agent: str, tool: str) -> None:
    run = _runs.get((user, agent))
    if run:
        run["steps"] = [*run["steps"], tool][-6:]


def end(user: str, agent: str) -> None:
    _runs.pop((user, agent), None)


def _locations(user: str) -> dict[str, dict]:
    return {r["agent"]: r for r in db.q("SELECT agent, zone, activity, at_desk, ts FROM presence WHERE user=?", (user,))}


STEP_WORDS = {
    "search_knowledge": "searched the documents",
    "query_database": "ran a database query",
    "describe_data_sources": "looked at the database tables",
    "fetch": "read a web page",
    "take_note": "saved a note",
    "remember": "stored a memory",
    "recall": "looked up a memory",
    "get_current_time": "checked the time",
}


def snapshot(user: str, characters: dict[str, dict]) -> dict:
    """A structured view of the whole team, used by both the Head's tool and the Team tab."""
    now = time.time()
    latest: dict[str, dict] = {}
    for t in db.recent_tasks(user, 60):  # newest first
        latest.setdefault(t["agent"], t)
    where = _locations(user)
    team = []
    for cid, c in characters.items():
        run = _runs.get((user, cid))
        task = latest.get(cid)
        live_task = task if task and task["status"] in ("pending", "running") else None
        recent = task if task and task["status"] in ("done", "failed") and now - (task["ended"] or 0) < 180 else None
        loc = where.get(cid)
        age = now - loc["ts"] if loc else None
        fresh = loc is not None and age <= STALE_S
        entry = {
            "id": cid,
            "name": c["name"],
            "role": "head" if c.get("role") == "head" else c.get("archetype", ""),
            "state": "working" if (run or live_task) else "idle",
            "doing": None,
            "progress": None,
            "steps": [STEP_WORDS.get(s, f"used {s}") for s in (run["steps"] if run else [])],
            "last_result": None,
            "zone": loc["zone"] if fresh else None,
            "activity": loc["activity"] if fresh else None,
            "at_desk": bool(loc["at_desk"]) if fresh else None,
            # when the office view is closed we can still say where they were last seen
            "last_seen_zone": loc["zone"] if loc and not fresh and age <= FORGET_S else None,
            "last_seen_s": int(age) if loc and not fresh and age <= FORGET_S else None,
        }
        if live_task:
            started = live_task["started"] or live_task["created"]
            entry["doing"] = live_task["task"]
            entry["progress"] = "queued" if live_task["status"] == "pending" else f"running for {int(now - started)}s"
        elif run:
            entry["doing"] = run["what"]
            entry["progress"] = f"running for {int(now - run['started'])}s"
        elif recent:
            entry["doing"] = recent["task"]
            entry["progress"] = "finished" if recent["status"] == "done" else "failed"
            entry["last_result"] = (recent["result"] or "")[:160]
        team.append(entry)
    return {"working": sum(1 for e in team if e["state"] == "working"), "total": len(team), "team": team}


def _ago(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} seconds ago"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    return f"{round(seconds / 3600)} hours ago"


def _where_text(e: dict) -> str:
    if e["zone"]:
        desk = "" if e["at_desk"] is None else (", at their desk" if e["at_desk"] else ", away from their desk")
        return f"{e['zone']}{desk}"
    if e["last_seen_zone"]:
        return f"not live (the office view is closed); last seen in {e['last_seen_zone']} {_ago(e['last_seen_s'])}"
    return "location unknown (the office view isn't open)"


def describe(user: str, characters: dict[str, dict]) -> str:
    """The text the Head reads when asked about the team."""
    snap = snapshot(user, characters)
    lines = [f"Live team status: {snap['working']} of {snap['total']} are working on a real task right now."]
    for e in snap["team"]:
        where = _where_text(e)
        if e["state"] == "working":
            steps = f" Steps so far: {', '.join(e['steps'])}." if e["steps"] else ""
            lines.append(f"- {e['name']} ({e['role']}): WORKING on \"{e['doing']}\" ({e['progress']}).{steps} Location: {where}.")
        else:
            extra = f" Just {e['progress']} \"{e['doing']}\"." if e["doing"] else ""
            hang = f" Currently: {e['activity']}" if e["activity"] else ""
            lines.append(f"- {e['name']} ({e['role']}): IDLE.{extra}{hang} Location: {where}.")
    lines.append("Only call someone 'working' if marked WORKING. Anyone else is idle, even if they are walking around or at a station.")
    return "\n".join(lines)
