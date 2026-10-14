"""SQLite storage: notes, long-term memory, traces, task board, usage counters and API keys."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import secrets_store

DATA_DIR = Path(os.getenv("CASTCALL_DATA_DIR", Path(__file__).parent / "data"))
DB_PATH = DATA_DIR / "app.db"
CHECKPOINT_PATH = DATA_DIR / "checkpoints.db"

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, user TEXT, agent TEXT, text TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, user TEXT, agent TEXT, text TEXT, emb TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS traces (
  id INTEGER PRIMARY KEY, ts REAL, user TEXT, agent TEXT, kind TEXT, name TEXT, model TEXT,
  prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, latency_ms REAL DEFAULT 0,
  status TEXT DEFAULT 'ok', detail TEXT, cost REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS traces_ts ON traces (ts);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, parent TEXT, user TEXT, agent TEXT, task TEXT, status TEXT, result TEXT,
  created REAL, started REAL, ended REAL
);
CREATE TABLE IF NOT EXISTS usage (user TEXT, day TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (user, day));
CREATE TABLE IF NOT EXISTS kdocs (
  id TEXT PRIMARY KEY, user TEXT, name TEXT, kind TEXT, size INTEGER, chunks INTEGER, embedded INTEGER, scope TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS ksegments (doc_id TEXT, ord INTEGER, text TEXT, locator TEXT);
CREATE INDEX IF NOT EXISTS ksegments_doc ON ksegments (doc_id);
CREATE TABLE IF NOT EXISTS kchunks (id INTEGER PRIMARY KEY, doc_id TEXT, ord INTEGER, text TEXT, locator TEXT, emb TEXT);
CREATE INDEX IF NOT EXISTS kchunks_doc ON kchunks (doc_id);
CREATE TABLE IF NOT EXISTS ksources (
  id TEXT PRIMARY KEY, user TEXT, name TEXT, kind TEXT, url TEXT, schema_text TEXT, scope TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS presence (
  user TEXT, agent TEXT, zone TEXT, activity TEXT, at_desk INTEGER, ts REAL, PRIMARY KEY (user, agent)
);
CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, name TEXT, created REAL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT UNIQUE, name TEXT, pw_hash TEXT, workspace TEXT, role TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS invites (code TEXT PRIMARY KEY, workspace TEXT, role TEXT, created_by TEXT, expires REAL, used_by TEXT);
CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY, user TEXT, person TEXT, agent TEXT, message TEXT, reply TEXT, rating INTEGER, comment TEXT, sources TEXT, ts REAL, promoted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY, user TEXT, agent TEXT, message TEXT, spec TEXT, deliver TEXT, enabled INTEGER DEFAULT 1,
  next_run REAL, last_run REAL, last_status TEXT, last_result TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS triggers (
  id TEXT PRIMARY KEY, user TEXT, name TEXT, agent TEXT, token TEXT UNIQUE, deliver TEXT, created REAL, last_used REAL, calls INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS llm_daily (
  day TEXT, provider TEXT, model TEXT, kid TEXT, calls INTEGER DEFAULT 0, quota_hits INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
  PRIMARY KEY (day, provider, model, kid)
);
CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY, provider TEXT, label TEXT, secret TEXT, created REAL,
  status TEXT DEFAULT 'unknown', last_error TEXT, disabled INTEGER DEFAULT 0
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release."""
    for table in ("kchunks", "memories"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "emb_model" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN emb_model TEXT")
    have = {r[1] for r in conn.execute("PRAGMA table_info(kdocs)")}
    for col, kind in (("chunking", "TEXT"), ("hash", "TEXT"), ("origin", "TEXT"), ("synced", "REAL")):
        if col not in have:
            conn.execute(f"ALTER TABLE kdocs ADD COLUMN {col} {kind}")


def get() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _migrate(_conn)
            _conn.commit()
        return _conn


def reset_for_tests() -> None:
    """Close the cached connection so a test can point CASTCALL_DATA_DIR at a temp folder."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        rows = get().execute(sql, args).fetchall()
        return [dict(r) for r in rows]


def run(sql: str, args: tuple = ()) -> int:
    with _lock:
        cur = get().execute(sql, args)
        get().commit()
        return cur.lastrowid or 0


# ---------------- notes ----------------


def add_note(user: str, agent: str, text: str) -> int:
    run("INSERT INTO notes (user, agent, text, ts) VALUES (?,?,?,?)", (user, agent, text, time.time()))
    return q("SELECT COUNT(*) AS n FROM notes WHERE user=?", (user,))[0]["n"]


def list_notes(user: str, limit: int = 20) -> list[dict]:
    return q("SELECT agent, text, ts FROM notes WHERE user=? ORDER BY id DESC LIMIT ?", (user, limit))


# ---------------- memories ----------------


def add_memory(user: str, agent: str, text: str, emb: list[float] | None, emb_model: str | None = None) -> int:
    return run("INSERT INTO memories (user, agent, text, emb, emb_model, ts) VALUES (?,?,?,?,?,?)", (user, agent, text, json.dumps(emb) if emb else None, emb_model if emb else None, time.time()))


def list_memories(user: str, agent: str, limit: int = 200) -> list[dict]:
    rows = q("SELECT id, text, emb, emb_model, ts FROM memories WHERE user=? AND agent=? ORDER BY id DESC LIMIT ?", (user, agent, limit))
    for r in rows:
        r["emb"] = json.loads(r["emb"]) if r["emb"] else None
    return rows


# ---------------- traces ----------------


def log_trace(user: str, agent: str, kind: str, name: str = "", model: str = "", prompt_tokens: int = 0, completion_tokens: int = 0, latency_ms: float = 0, status: str = "ok", detail: str = "", cost: float = 0) -> None:
    run(
        "INSERT INTO traces (ts,user,agent,kind,name,model,prompt_tokens,completion_tokens,latency_ms,status,detail,cost) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), user, agent, kind, name, model, prompt_tokens, completion_tokens, latency_ms, status, detail[:500], cost),
    )


# ---------------- tasks (the team task board) ----------------


def upsert_task(task_id: str, **fields: Any) -> None:
    existing = q("SELECT id FROM tasks WHERE id=?", (task_id,))
    if not existing:
        run("INSERT INTO tasks (id, created) VALUES (?,?)", (task_id, time.time()))
    for k, v in fields.items():
        run(f"UPDATE tasks SET {k}=? WHERE id=?", (v, task_id))


def recent_tasks(user: str, limit: int = 30) -> list[dict]:
    return q("SELECT * FROM tasks WHERE user=? ORDER BY created DESC LIMIT ?", (user, limit))


# ---------------- usage / quotas ----------------


def bump_usage(user: str) -> int:
    day = time.strftime("%Y-%m-%d")
    run("INSERT INTO usage (user, day, count) VALUES (?,?,1) ON CONFLICT(user, day) DO UPDATE SET count = count + 1", (user, day))
    return q("SELECT count FROM usage WHERE user=? AND day=?", (user, day))[0]["count"]


def usage_today(user: str) -> int:
    rows = q("SELECT count FROM usage WHERE user=? AND day=?", (user, time.strftime("%Y-%m-%d")))
    return rows[0]["count"] if rows else 0


# ---------------- API keys (encrypted) ----------------


def add_api_key(provider: str, label: str, secret: str) -> int:
    return run("INSERT INTO api_keys (provider, label, secret, created) VALUES (?,?,?,?)", (provider, label, secrets_store.encrypt(secret), time.time()))


def list_api_keys() -> list[dict]:
    """Rows for display: the secret is masked, never returned."""
    out = []
    for r in q("SELECT * FROM api_keys ORDER BY id"):
        plain = secrets_store.decrypt(r["secret"]) or ""
        out.append({**{k: r[k] for k in ("id", "provider", "label", "created", "status", "last_error", "disabled")}, "masked": secrets_store.mask(plain) if plain else "(unreadable)", "source": "saved"})
    return out


def api_key_secrets(provider: str) -> list[tuple[int, str]]:
    """Decrypted keys for use by the LLM layer (server side only)."""
    out = []
    for r in q("SELECT id, secret FROM api_keys WHERE provider=? AND disabled=0 ORDER BY id", (provider,)):
        plain = secrets_store.decrypt(r["secret"])
        if plain:
            out.append((r["id"], plain))
    return out


def set_api_key_status(key_id: int, status: str, error: str = "") -> None:
    run("UPDATE api_keys SET status=?, last_error=? WHERE id=?", (status, error[:300], key_id))


def delete_api_key(key_id: int) -> bool:
    return run("DELETE FROM api_keys WHERE id=?", (key_id,)) >= 0 and True
