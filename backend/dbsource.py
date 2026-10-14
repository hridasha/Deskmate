"""Read-only access to the user's own databases (SQLite uploads, PostgreSQL, MySQL) for agents allowed to use them.

Defense in depth: the connection itself is read-only, only a single SELECT/WITH statement is accepted, write keywords and
risky functions are refused, results are capped, and queries are time-limited. SQLite sources must be uploaded files, so an
agent can never open the app's own database.
"""
import asyncio
import json
import re
import time
import uuid
from pathlib import Path

import sqlparse
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url

import db
import safe_url
import secrets_store

UPLOAD_DIR = db.DATA_DIR / "uploads"
MAX_ROWS = 50
TIMEOUT_S = 8
MAX_UPLOAD = 100 * 1024 * 1024

_WRITE = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|truncate|grant|revoke|copy|call|exec|execute|vacuum|reindex|merge|into|load_extension)\b", re.I)
_RISKY_FUNCS = re.compile(r"\b(pg_[a-z_]+|lo_import|lo_export|dblink|readfile|writefile|sleep|benchmark|load_file|sys_exec)\s*\(", re.I)
_STRINGS = re.compile(r"'(?:[^']|'')*'")


class UnsafeQuery(ValueError):
    """The SQL, or the connection, isn't allowed."""


def validate_select(sql: str) -> str:
    """Return the cleaned SQL if it is a single read-only SELECT/WITH query; otherwise raise UnsafeQuery."""
    cleaned = sqlparse.format(sql, strip_comments=True).strip().rstrip(";").strip()
    if not cleaned:
        raise UnsafeQuery("Empty query.")
    if len(sqlparse.parse(cleaned)) != 1 or ";" in _STRINGS.sub("''", cleaned):
        raise UnsafeQuery("Only a single statement is allowed.")
    if cleaned.split(None, 1)[0].upper() not in ("SELECT", "WITH"):
        raise UnsafeQuery("Only SELECT queries are allowed.")
    bare = _STRINGS.sub("''", cleaned)
    m = _WRITE.search(bare)
    if m:
        raise UnsafeQuery(f"'{m.group(1).upper()}' isn't allowed: this connection is read-only.")
    if _RISKY_FUNCS.search(bare):
        raise UnsafeQuery("That function isn't allowed.")
    return cleaned


def _normalize(url: str) -> str:
    url = url.strip()
    url = re.sub(r"^postgres(ql)?://", "postgresql+psycopg2://", url)  # name the driver: newer SQLAlchemy defaults to one we don't ship
    url = re.sub(r"^mysql://", "mysql+pymysql://", url)
    return url


_engines: dict[str, Engine] = {}


def make_engine(url: str) -> Engine:
    url = _normalize(url)
    backend = make_url(url).get_backend_name()
    if backend == "sqlite":
        path = Path(make_url(url).database or "").resolve()
        if UPLOAD_DIR.resolve() not in path.parents:
            raise UnsafeQuery("SQLite databases must be uploaded as files.")
        return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    if backend in ("postgresql", "mysql"):
        parsed = make_url(url)
        try:
            ip = safe_url.pin_host(parsed.host)  # connect to the address we checked, so DNS can't change its answer afterwards
        except safe_url.BlockedHost as e:
            raise UnsafeQuery(str(e)) from e
    if backend == "postgresql":
        return create_engine(url, connect_args={"options": f"-c default_transaction_read_only=on -c statement_timeout={TIMEOUT_S * 1000}", "connect_timeout": 8, "hostaddr": ip}, pool_pre_ping=True)
    if backend == "mysql":
        engine = create_engine(parsed.set(host=ip), connect_args={"connect_timeout": 8, "read_timeout": TIMEOUT_S}, pool_pre_ping=True)

        @event.listens_for(engine, "connect")
        def _read_only(dbapi_conn, _record):  # the server refuses writes too, not just our SQL checks
            cur = dbapi_conn.cursor()
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.close()

        return engine
    raise UnsafeQuery("Supported databases: PostgreSQL, MySQL, or an uploaded SQLite file.")


def schema_text(engine: Engine, max_tables: int = 40) -> str:
    insp = inspect(engine)
    lines = []
    for table in insp.get_table_names()[:max_tables]:
        cols = ", ".join(f"{c['name']} {str(c['type']).split('(')[0]}" for c in insp.get_columns(table)[:30])
        lines.append(f"{table}({cols})")
    return "\n".join(lines)[:6000] or "(no tables)"


def _engine_for(row: dict) -> Engine:
    if row["id"] not in _engines:
        url = secrets_store.decrypt(row["url"])
        if not url:
            raise UnsafeQuery("Stored connection can't be read. Remove and add this database again.")
        _engines[row["id"]] = make_engine(url)
    return _engines[row["id"]]


# ---------------- registry ----------------


def _public(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "schema": row["schema_text"], "scope": json.loads(row["scope"]) if row["scope"] else None, "created": row["created"]}


def list_sources(user: str) -> list[dict]:
    return [_public(r) for r in db.q("SELECT * FROM ksources WHERE user=? ORDER BY created DESC", (user,))]


def _register(user: str, name: str, url: str, scope: list[str] | None) -> dict:
    name = re.sub(r"[^\w .-]", "", name).strip()[:40]
    if not name:
        raise UnsafeQuery("Give the database a name.")
    if db.q("SELECT 1 FROM ksources WHERE user=? AND lower(name)=lower(?)", (user, name)):
        raise UnsafeQuery(f"You already have a database called '{name}'.")
    engine = make_engine(url)
    try:  # prove the connection works (and is readable) before saving anything
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        schema = schema_text(engine)
    except UnsafeQuery:
        raise
    except Exception as e:  # noqa: BLE001
        engine.dispose()
        raise UnsafeQuery(f"Couldn't connect: {type(e).__name__}") from e
    sid = uuid.uuid4().hex[:10]
    kind = make_url(_normalize(url)).get_backend_name()
    db.run("INSERT INTO ksources (id, user, name, kind, url, schema_text, scope, created) VALUES (?,?,?,?,?,?,?,?)", (sid, user, name, kind, secrets_store.encrypt(_normalize(url)), schema, json.dumps(scope) if scope else None, time.time()))
    _engines[sid] = engine
    return _public(db.q("SELECT * FROM ksources WHERE id=?", (sid,))[0])


def add_url_source(user: str, name: str, url: str, scope: list[str] | None = None) -> dict:
    return _register(user, name, url, scope)


def add_sqlite_upload(user: str, name: str, data: bytes, scope: list[str] | None = None) -> dict:
    if len(data) > MAX_UPLOAD:
        raise UnsafeQuery("That database file is larger than 100 MB.")
    if not data.startswith(b"SQLite format 3\x00"):
        raise UnsafeQuery("That doesn't look like a SQLite database file.")
    folder = UPLOAD_DIR / re.sub(r"[^\w-]", "_", user)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex[:10]}.db"
    path.write_bytes(data)
    try:
        return _register(user, name, f"sqlite:///{path}", scope)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def set_scope(user: str, source_id: str, agents: list[str] | None) -> dict | None:
    if not db.q("SELECT 1 FROM ksources WHERE user=? AND id=?", (user, source_id)):
        return None
    db.run("UPDATE ksources SET scope=? WHERE id=?", (json.dumps(agents) if agents else None, source_id))
    return _public(db.q("SELECT * FROM ksources WHERE id=?", (source_id,))[0])


def delete_source(user: str, source_id: str) -> bool:
    rows = db.q("SELECT * FROM ksources WHERE user=? AND id=?", (user, source_id))
    if not rows:
        return False
    eng = _engines.pop(source_id, None)
    if eng:
        eng.dispose()
    url = secrets_store.decrypt(rows[0]["url"]) or ""
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///"):]).unlink(missing_ok=True)
    db.run("DELETE FROM ksources WHERE id=?", (source_id,))
    return True


def _visible(user: str, agent_id: str, only: set[str] | None = None) -> list[dict]:
    rows = db.q("SELECT * FROM ksources WHERE user=?", (user,))
    if only is not None:  # an expert agent only sees the databases it was built from
        return [r for r in rows if r["id"] in only]
    return [r for r in rows if not r["scope"] or agent_id in json.loads(r["scope"])]


def source_names(user: str, agent_id: str, only: set[str] | None = None) -> list[str]:
    return [r["name"] for r in _visible(user, agent_id, only)]


def describe(user: str, agent_id: str, only: set[str] | None = None) -> str:
    rows = _visible(user, agent_id, only)
    if not rows:
        return "No databases are connected."
    return "\n\n".join(f"Database '{r['name']}' ({r['kind']}):\n{r['schema_text']}" for r in rows)


# ---------------- querying ----------------


def _run_query(row: dict, sql: str) -> str:
    engine = _engine_for(row)
    wrapped = f"SELECT * FROM ({sql}) AS _q LIMIT {MAX_ROWS + 1}"
    with engine.connect() as conn:
        if row["kind"] == "sqlite":
            deadline = time.time() + TIMEOUT_S
            conn.connection.dbapi_connection.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 100000)
        elif row["kind"] == "mysql":
            conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME={TIMEOUT_S * 1000}"))
        result = conn.execute(text(wrapped))
        cols = list(result.keys())
        rows = result.fetchmany(MAX_ROWS + 1)
    more = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    if not rows:
        return "Query ran successfully but returned no rows."
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v).replace("\n", " ")[:80] for v in r) + " |")
    text_out = "\n".join(out)
    if len(text_out) > 4000:
        text_out = text_out[:4000] + "\n[...truncated]"
    return text_out + (f"\n(showing the first {MAX_ROWS} rows)" if more else "")


async def query(user: str, agent_id: str, source: str, sql: str, only: set[str] | None = None) -> str:
    """Run a validated read-only query. Errors come back as text so the agent can fix its SQL and retry."""
    rows = _visible(user, agent_id, only)
    row = next((r for r in rows if r["name"].lower() == source.strip().lower()), None)
    if not row:
        return f"Unknown database '{source}'. Available: {', '.join(r['name'] for r in rows) or 'none'}."
    try:
        clean = validate_select(sql)
        return await asyncio.wait_for(asyncio.to_thread(_run_query, row, clean), timeout=TIMEOUT_S + 4)
    except UnsafeQuery as e:
        return f"Query refused: {e}"
    except asyncio.TimeoutError:
        return "The query took too long and was stopped. Try a narrower query."
    except Exception as e:  # noqa: BLE001
        return f"Query failed: {str(e).splitlines()[0][:200]}"
