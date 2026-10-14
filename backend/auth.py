"""Sign-in, team workspaces and roles.

AUTH_MODE=off (default): the old anonymous per-browser identity, fine for one person on localhost.
AUTH_MODE=on: every request needs a signed-in user. People belong to a workspace and share its agents, documents, tasks and chats.

Roles:  admin  everything, including API keys, database connections, members and evaluations
        member chat, create and remove agents, upload knowledge, manage tasks
        viewer read-only, plus chatting with agents
"""
import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Header, HTTPException, Request

import db
import secrets_store

ROLES = ("admin", "member", "viewer")
TOKEN_TTL_S = 7 * 24 * 3600
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_UID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_failures: dict[str, deque] = defaultdict(deque)

# Viewers may call these; every other write needs member, and the paths below need admin.
VIEWER_WRITES = ("/chat", "/presence", "/feedback")
ADMIN_PREFIXES = ("/settings", "/evals/run", "/evals/retrieval/run", "/feedback/promote", "/knowledge/databases", "/auth/members", "/auth/invites")
PUBLIC_PATHS = ("/health", "/auth/config", "/auth/login", "/auth/signup")
PUBLIC_PREFIXES = ("/hooks/",)  # inbound triggers: the secret token in the address is the credential


def enabled() -> bool:
    return os.getenv("AUTH_MODE", "off").strip().lower() in ("on", "1", "true", "yes")


@dataclass
class Principal:
    id: str  # who: a user id (or the anonymous browser id)
    scope: str  # whose data: the workspace (or the anonymous browser id)
    role: str = "admin"
    email: str = ""
    name: str = ""
    workspace: str = ""


# ---------------- passwords and tokens ----------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        check = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(check.hex(), digest)
    except (ValueError, TypeError):
        return False


def _signing_key() -> str:
    env = os.getenv("AUTH_SECRET", "").strip()
    if env:
        return env
    path = secrets_store.DATA_DIR / ".auth_secret"
    secrets_store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


def issue_token(user: dict) -> str:
    return jwt.encode({"sub": user["id"], "iat": int(time.time()), "exp": int(time.time()) + TOKEN_TTL_S}, _signing_key(), algorithm="HS256")


def public_user(u: dict) -> dict:
    ws = db.q("SELECT name FROM workspaces WHERE id=?", (u["workspace"],))
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"], "workspace": u["workspace"], "workspace_name": ws[0]["name"] if ws else ""}


# ---------------- accounts ----------------


def _throttle(key: str) -> None:
    window = _failures[key]
    now = time.time()
    while window and now - window[0] > 900:
        window.popleft()
    if len(window) >= 10:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")


def signup(email: str, password: str, name: str, invite: str = "", workspace_name: str = "") -> dict:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    if len(password) < 8:
        raise HTTPException(400, "Use a password of at least 8 characters.")
    if db.q("SELECT 1 FROM users WHERE email=?", (email,)):
        raise HTTPException(400, "That email already has an account. Sign in instead.")
    now = time.time()
    if invite.strip():
        inv = db.q("SELECT * FROM invites WHERE code=?", (invite.strip(),))
        if not inv or inv[0]["used_by"] or inv[0]["expires"] < now:
            raise HTTPException(400, "That invite code isn't valid (it may have been used or expired).")
        workspace, role = inv[0]["workspace"], inv[0]["role"]
    else:
        if os.getenv("OPEN_SIGNUP", "true").lower() in ("0", "false", "no") and db.q("SELECT 1 FROM users LIMIT 1"):
            raise HTTPException(403, "Sign-up is by invitation only. Ask an admin for an invite code.")
        workspace, role = uuid.uuid4().hex[:10], "admin"
        label = workspace_name.strip() or f"{name.strip() or email.split('@')[0]}'s office"
        db.run("INSERT INTO workspaces (id, name, created) VALUES (?,?,?)", (workspace, label[:60], now))
    uid = uuid.uuid4().hex[:12]
    db.run("INSERT INTO users (id, email, name, pw_hash, workspace, role, created) VALUES (?,?,?,?,?,?,?)", (uid, email, name.strip()[:60] or email.split("@")[0], hash_password(password), workspace, role, now))
    if invite.strip():
        db.run("UPDATE invites SET used_by=? WHERE code=?", (uid, invite.strip()))
    user = db.q("SELECT * FROM users WHERE id=?", (uid,))[0]
    return {"token": issue_token(user), "user": public_user(user)}


def login(email: str, password: str, ip: str = "") -> dict:
    key = f"{ip}|{email.strip().lower()}"
    _throttle(key)
    rows = db.q("SELECT * FROM users WHERE email=?", (email.strip().lower(),))
    if not rows or not verify_password(password, rows[0]["pw_hash"]):
        _failures[key].append(time.time())
        raise HTTPException(401, "Wrong email or password.")
    return {"token": issue_token(rows[0]), "user": public_user(rows[0])}


def user_from_token(token: str) -> Optional[dict]:
    try:
        claims = jwt.decode(token, _signing_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    rows = db.q("SELECT * FROM users WHERE id=?", (claims.get("sub", ""),))
    return rows[0] if rows else None


def members(workspace: str) -> list[dict]:
    return [{k: r[k] for k in ("id", "email", "name", "role", "created")} for r in db.q("SELECT * FROM users WHERE workspace=? ORDER BY created", (workspace,))]


def _admins(workspace: str) -> int:
    return db.q("SELECT COUNT(*) AS n FROM users WHERE workspace=? AND role='admin'", (workspace,))[0]["n"]


def set_role(workspace: str, user_id: str, role: str) -> None:
    rows = db.q("SELECT * FROM users WHERE id=? AND workspace=?", (user_id, workspace))
    if role not in ROLES or not rows:
        raise HTTPException(404, "No such member or role.")
    if rows[0]["role"] == "admin" and role != "admin" and _admins(workspace) <= 1:
        raise HTTPException(400, "A workspace needs at least one admin.")
    db.run("UPDATE users SET role=? WHERE id=?", (role, user_id))


def remove_member(workspace: str, user_id: str) -> None:
    rows = db.q("SELECT * FROM users WHERE id=? AND workspace=?", (user_id, workspace))
    if not rows:
        raise HTTPException(404, "No such member.")
    if rows[0]["role"] == "admin" and _admins(workspace) <= 1:
        raise HTTPException(400, "A workspace needs at least one admin.")
    db.run("DELETE FROM users WHERE id=?", (user_id,))


def create_invite(workspace: str, role: str, created_by: str, days: int = 7) -> dict:
    if role not in ROLES:
        raise HTTPException(400, "Unknown role.")
    code = secrets.token_urlsafe(9)
    expires = time.time() + days * 86400
    db.run("INSERT INTO invites (code, workspace, role, created_by, expires, used_by) VALUES (?,?,?,?,?,NULL)", (code, workspace, role, created_by, expires))
    return {"code": code, "role": role, "expires": expires}


def list_invites(workspace: str) -> list[dict]:
    rows = db.q("SELECT * FROM invites WHERE workspace=? AND used_by IS NULL AND expires>? ORDER BY expires", (workspace, time.time()))
    return [{"code": r["code"], "role": r["role"], "expires": r["expires"]} for r in rows]


def revoke_invite(workspace: str, code: str) -> None:
    db.run("DELETE FROM invites WHERE workspace=? AND code=?", (workspace, code))


# ---------------- request identity ----------------


def principal_for(authorization: Optional[str], x_user_id: Optional[str]) -> Principal:
    if not enabled():
        uid = x_user_id if x_user_id and _UID.match(x_user_id) else "anon"
        return Principal(id=uid, scope=uid)
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = user_from_token(token) if token else None
    if not user:
        raise HTTPException(401, "Please sign in.")
    return Principal(id=user["id"], scope=f"ws:{user['workspace']}", role=user["role"], email=user["email"], name=user["name"], workspace=user["workspace"])


def current(request: Request, authorization: Optional[str] = Header(default=None), x_user_id: Optional[str] = Header(default=None)) -> Principal:
    """The caller's identity (cached on the request so the role gate and the endpoint share one lookup)."""
    cached = getattr(request.state, "principal", None)
    if cached is None:
        cached = principal_for(authorization, x_user_id)
        request.state.principal = cached
    return cached


def enforce_role(request: Request, authorization: Optional[str] = Header(default=None), x_user_id: Optional[str] = Header(default=None)) -> None:
    """App-wide gate: sign-in required, viewers read-only, admin-only areas. No-op when AUTH_MODE is off."""
    if not enabled():
        return
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return
    p = current(request, authorization, x_user_id)
    if p.role == "admin":
        return
    if any(path.startswith(x) for x in ADMIN_PREFIXES):
        raise HTTPException(403, "That needs an admin.")
    if request.method not in ("GET", "HEAD") and p.role == "viewer" and not path.startswith(VIEWER_WRITES):
        raise HTTPException(403, "Viewers can look but not change things.")
