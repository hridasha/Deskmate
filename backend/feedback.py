"""Thumbs up/down on answers, and turning them into regression tests for the evaluation suite.

A thumbs-down plus a short note ("it should have mentioned the 25-day limit") can be promoted to an eval case: next time
the evals run, that same question is asked again and the answer is checked against what the person wanted.
"""
import json
import re
import time
import uuid

import db
import secrets_store

CASES_FILE = secrets_store.DATA_DIR / "feedback_cases.json"
MAX_CASES = 60


def add(user: str, person: str, agent: str, message: str, reply: str, rating: int, comment: str = "", sources: list | None = None) -> dict:
    fid = uuid.uuid4().hex[:10]
    db.run(
        "INSERT INTO feedback (id, user, person, agent, message, reply, rating, comment, sources, ts, promoted) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
        (fid, user, person, agent, message[:2000], reply[:4000], 1 if rating > 0 else -1, comment[:500], json.dumps(sources or [])[:4000], time.time()),
    )
    return {"id": fid}


def summary(user: str, limit: int = 20) -> dict:
    rows = db.q("SELECT * FROM feedback WHERE user=? ORDER BY ts DESC", (user,))
    by_agent: dict[str, dict] = {}
    for r in rows:
        a = by_agent.setdefault(r["agent"], {"agent": r["agent"], "up": 0, "down": 0})
        a["up" if r["rating"] > 0 else "down"] += 1
    recent = [
        {"id": r["id"], "agent": r["agent"], "rating": r["rating"], "message": r["message"][:200], "reply": r["reply"][:300], "comment": r["comment"], "ts": r["ts"], "promoted": bool(r["promoted"])}
        for r in rows
        if r["rating"] < 0
    ][:limit]
    return {"up": sum(1 for r in rows if r["rating"] > 0), "down": sum(1 for r in rows if r["rating"] < 0), "by_agent": sorted(by_agent.values(), key=lambda a: -(a["down"] + a["up"])), "needs_attention": recent}


def _load_cases() -> list[dict]:
    try:
        return json.loads(CASES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _terms(note: str, bad_reply: str) -> list[str]:
    """What a better answer should contain: figures and words from the note that the rejected answer did not already say."""
    seen = set(re.findall(r"[a-z0-9$%.]+", bad_reply.lower()))
    stop = {"the", "and", "that", "should", "have", "has", "was", "were", "with", "for", "its", "not", "but", "this", "from", "are", "had", "answer", "mention", "mentioned", "said", "say", "instead", "it"}
    numbers = re.findall(r"\d[\d,.]*", note)
    words = [w for w in re.findall(r"[a-z]{4,}", note.lower()) if w not in stop]
    out: list[str] = []
    for t in [n.rstrip(".,") for n in numbers] + words:
        if t and t not in seen and t not in out:
            out.append(t)
    return out[:6]


def promote(user: str, feedback_id: str) -> dict | None:
    """Turn a thumbs-down into an eval case. Returns the case, or None if there is no such feedback."""
    rows = db.q("SELECT * FROM feedback WHERE id=? AND user=?", (feedback_id, user))
    if not rows:
        return None
    r = rows[0]
    expect: dict = {"not_similar_to": r["reply"]}  # at minimum, don't give the same unwelcome answer again
    terms = _terms(r["comment"], r["reply"]) if r["comment"] else []
    if terms:
        expect["contains_all"] = terms[:3]
    case = {"id": f"fb-{feedback_id}", "agent": r["agent"], "message": r["message"], "expect": expect}
    cases = [c for c in _load_cases() if c["id"] != case["id"]][-(MAX_CASES - 1):] + [case]
    CASES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CASES_FILE.write_text(json.dumps(cases, indent=1), encoding="utf-8")
    db.run("UPDATE feedback SET promoted=1 WHERE id=?", (feedback_id,))
    return case


def eval_cases() -> list[dict]:
    return _load_cases()


def similarity(a: str, b: str) -> float:
    """Word overlap between two answers (Jaccard), 0 to 1."""
    x, y = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
    return len(x & y) / len(x | y) if x and y else 0.0
