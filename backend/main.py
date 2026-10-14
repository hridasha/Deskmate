"""FastAPI app: agents, streaming chat, saved history, task board, metrics, quotas and API-key settings."""
import asyncio
import json
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field, StringConstraints

import agent as agents
import auth
import bundle
import db
import chunking
import dbsource
import docsync
import embeddings
import feedback
import knowledge
import llm
import memory
import policy
import rerank
import presence
import safety
import scheduler
import telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.get()
    async def prepare_embeddings() -> None:
        await embeddings.warm_up()
        await rerank.rerank("warm up", ["warm up", "warm up"])  # download and load once, not on the first question (no-op when off)
        try:
            n = await knowledge.reindex() + await memory.reindex()
            if n:
                print(f"Re-embedded {n} items with {embeddings.model_id()}")
        except Exception as e:  # noqa: BLE001
            print(f"Re-indexing skipped: {e!r}")

    warm = asyncio.create_task(prepare_embeddings())
    ticker = asyncio.create_task(scheduler.loop()) if os.getenv("SCHEDULER", "on").lower() not in ("off", "0", "false") else None
    async with AsyncSqliteSaver.from_conn_string(str(db.CHECKPOINT_PATH)) as saver:
        agents.set_checkpointer(saver)
        yield
        agents.set_checkpointer(None)  # the connection closes with this block; don't leave a dead one behind
    warm.cancel()
    if ticker:
        ticker.cancel()


app = FastAPI(title="Deskmates API", lifespan=lifespan, dependencies=[Depends(auth.enforce_role)])
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- identity, quotas, admin ----------------

_UID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "200"))
MINUTE_LIMIT = int(os.getenv("MINUTE_LIMIT", "12"))
IP_MINUTE_LIMIT = int(os.getenv("IP_MINUTE_LIMIT", "60"))  # a client can invent user ids, but not IP addresses
_recent: dict[str, deque] = defaultdict(deque)


def get_user(p: auth.Principal = Depends(auth.current)) -> str:
    """Whose data this is: an anonymous per-browser id, or (with AUTH_MODE=on) the signed-in user's workspace."""
    return p.scope


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    """Admin actions (API keys, database connections) need ADMIN_TOKEN. With none set, only dev mode leaves them open.
    With AUTH_MODE=on the admin role (checked for every request in auth.enforce_role) takes its place."""
    if auth.enabled():
        return
    token = os.getenv("ADMIN_TOKEN", "")
    if token:
        if x_admin_token != token:
            raise HTTPException(401, "Admin token required")
    elif os.getenv("CASTCALL_ENV", "prod").lower() != "dev":
        raise HTTPException(401, "Admin actions are locked. Set ADMIN_TOKEN on the server (or CASTCALL_ENV=dev for local use).")


def client_ip(request: Request) -> str:
    """The caller's address. Behind a trusted proxy, set TRUST_PROXY=true to use X-Forwarded-For."""
    if os.getenv("TRUST_PROXY", "").lower() in ("1", "true", "yes"):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_quota_ip(ip: str) -> None:
    window = _recent[f"ip:{ip}"]
    now = time.time()
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= IP_MINUTE_LIMIT:
        raise HTTPException(429, "Too many requests from this network. Try again in a minute.")
    window.append(now)


def check_quota(user: str, ip: str = "") -> None:
    now = time.time()
    if ip:
        ip_window = _recent[f"ip:{ip}"]
        while ip_window and now - ip_window[0] > 60:
            ip_window.popleft()
        if len(ip_window) >= IP_MINUTE_LIMIT:
            raise HTTPException(429, "Too many requests from this network. Try again in a minute.")
        ip_window.append(now)
    window = _recent[user]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= MINUTE_LIMIT:
        raise HTTPException(429, f"Slow down: at most {MINUTE_LIMIT} messages per minute.")
    if db.usage_today(user) >= DAILY_LIMIT:
        raise HTTPException(429, f"Daily limit of {DAILY_LIMIT} messages reached. It resets tomorrow.")
    window.append(now)
    db.bump_usage(user)


# ---------------- models ----------------

Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class LookIn(BaseModel):
    """Costume for an agent's robot: clothes, hair and accessories."""

    hair: Literal["short", "bowl", "messy", "long", "bun", "bald", "bob", "slick", "curly"] = "short"
    hairColor: Hex = "#4a3220"
    skin: Hex = "#e6b894"
    shirt: Hex = "#8793d1"
    pants: Hex = "#33363f"
    jacket: Optional[Hex] = None
    tie: Optional[Hex] = None
    glasses: bool = False
    mustache: bool = False
    bulk: float = Field(default=1.0, ge=0.8, le=1.4)


def look_dict(look: Optional[LookIn]) -> Optional[dict]:
    return {k: v for k, v in look.model_dump().items() if v is not None} if look else None


class KnowledgeIn(BaseModel):
    """Build an expert agent from some of the user's documents and databases."""

    doc_ids: list[str] = Field(default_factory=list, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    exclusive: bool = True  # hide these sources from every other agent
    starters: list[str] = Field(default_factory=list, max_length=5)


class NewAgent(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    archetype: str = Field(min_length=1, max_length=80)
    personality: str = Field(min_length=1, max_length=1200)
    color: str = Field(default="#6d5acd", pattern=r"^#[0-9a-fA-F]{6}$")
    look: Optional[LookIn] = None
    tools: list[str] = Field(default_factory=list, max_length=5)
    knowledge: Optional[KnowledgeIn] = None


class ChatRequest(BaseModel):
    character_id: str
    message: str = Field(min_length=1, max_length=4000)
    goal: str = Field(default="", max_length=500)


class NewKey(BaseModel):
    provider: Literal["gemini", "groq", "openrouter", "cerebras"]
    key: str = Field(min_length=20, max_length=300)
    label: str = Field(default="", max_length=40)


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "agent"


# ---------------- health, agents, presets ----------------

PRESETS_FILE = Path(__file__).parent / "presets.json"


@app.get("/health")
def health():
    return {"ok": True, "llm": {p: len(llm.keys_for(p)) for p in llm.PROVIDERS}, "embeddings": embeddings.model_id()}


# ---------------- accounts ----------------


class SignupIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=60)
    invite: str = Field(default="", max_length=60)
    workspace_name: str = Field(default="", max_length=60)


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class RoleIn(BaseModel):
    role: Literal["admin", "member", "viewer"]


@app.get("/auth/config")
def auth_config():
    return {"auth": auth.enabled(), "open_signup": os.getenv("OPEN_SIGNUP", "true").lower() not in ("0", "false", "no")}


@app.post("/auth/signup")
def auth_signup(body: SignupIn, request: Request):
    if not auth.enabled():
        raise HTTPException(400, "Accounts are switched off on this server (AUTH_MODE=off).")
    check_quota_ip(client_ip(request))
    return auth.signup(body.email, body.password, body.name, body.invite, body.workspace_name)


@app.post("/auth/login")
def auth_login(body: LoginIn, request: Request):
    if not auth.enabled():
        raise HTTPException(400, "Accounts are switched off on this server (AUTH_MODE=off).")
    return auth.login(body.email, body.password, client_ip(request))


@app.get("/auth/me")
def auth_me(p: auth.Principal = Depends(auth.current)):
    return {"auth": auth.enabled(), "id": p.id, "email": p.email, "name": p.name, "role": p.role, "workspace": p.workspace}


@app.get("/auth/members")
def auth_members(p: auth.Principal = Depends(auth.current)):
    return {"members": auth.members(p.workspace), "invites": auth.list_invites(p.workspace)}


@app.patch("/auth/members/{user_id}")
def auth_set_role(user_id: str, body: RoleIn, p: auth.Principal = Depends(auth.current)):
    auth.set_role(p.workspace, user_id, body.role)
    return {"ok": True}


@app.delete("/auth/members/{user_id}")
def auth_remove(user_id: str, p: auth.Principal = Depends(auth.current)):
    auth.remove_member(p.workspace, user_id)
    return {"ok": True}


@app.post("/auth/invites")
def auth_invite(body: RoleIn, p: auth.Principal = Depends(auth.current)):
    return auth.create_invite(p.workspace, body.role, p.id)


@app.delete("/auth/invites/{code}")
def auth_revoke(code: str, p: auth.Principal = Depends(auth.current)):
    auth.revoke_invite(p.workspace, code)
    return {"ok": True}


class FeedbackIn(BaseModel):
    agent_id: str = Field(max_length=60)
    message: str = Field(max_length=4000)
    reply: str = Field(max_length=4000)
    rating: Literal[1, -1]
    comment: str = Field(default="", max_length=500)
    sources: list[dict] = Field(default_factory=list, max_length=12)


@app.post("/feedback")
def feedback_add(body: FeedbackIn, p: auth.Principal = Depends(auth.current)):
    return feedback.add(p.scope, p.id, body.agent_id, body.message, body.reply, body.rating, body.comment, body.sources)


@app.get("/feedback/summary")
def feedback_summary(user: str = Depends(get_user)):
    return feedback.summary(user)


@app.post("/feedback/promote/{feedback_id}")
def feedback_promote(feedback_id: str, user: str = Depends(get_user)):
    """Make a thumbs-down into a permanent test: the evals will ask the same question again."""
    case = feedback.promote(user, feedback_id)
    if not case:
        raise HTTPException(404, "No such feedback")
    return case


@app.get("/export")
def export_all(user: str = Depends(get_user)):
    """Download your agents and documents as one .zip (no keys, database connections or chats)."""
    from fastapi.responses import Response

    return Response(bundle.export_bundle(user), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="deskmates-export.zip"'})


@app.post("/import")
async def import_all(file: UploadFile = File(...), user: str = Depends(get_user)):
    data = await file.read(bundle.MAX_ZIP + 1)
    try:
        return await bundle.import_bundle(user, data)
    except bundle.ImportProblem as e:
        raise HTTPException(400, str(e)) from e


# ---------------- schedules and triggers ----------------


class DeliverIn(BaseModel):
    slack_url: str = Field(default="", max_length=400)
    webhook_url: str = Field(default="", max_length=400)
    emails: list[str] = Field(default_factory=list, max_length=5)


class ScheduleIn(BaseModel):
    agent_id: str = Field(max_length=60)
    message: str = Field(min_length=1, max_length=2000)
    kind: Literal["interval", "daily", "weekly"]
    every_min: int = Field(default=60, ge=1, le=10080)
    at: str = Field(default="09:00", max_length=5)
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], max_length=7)
    tz_offset_min: int = Field(default=0, ge=-840, le=840)
    deliver: DeliverIn = Field(default_factory=DeliverIn)


class TriggerIn(BaseModel):
    name: str = Field(default="", max_length=60)
    agent_id: str = Field(max_length=60)
    deliver: DeliverIn = Field(default_factory=DeliverIn)


@app.get("/schedules")
def schedules_list(user: str = Depends(get_user)):
    return {"schedules": scheduler.list_schedules(user), "email_ready": bool(os.getenv("SMTP_HOST"))}


@app.post("/schedules")
def schedules_add(body: ScheduleIn, user: str = Depends(get_user)):
    spec = {"kind": body.kind, "every_min": body.every_min, "at": body.at, "days": body.days, "tz_offset_min": body.tz_offset_min}
    try:
        return scheduler.create_schedule(user, body.agent_id, body.message, spec, body.deliver.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/schedules/{sid}")
def schedules_toggle(sid: str, enabled: bool, user: str = Depends(get_user)):
    row = scheduler.set_enabled(user, sid, enabled)
    if not row:
        raise HTTPException(404, "No such schedule")
    return row


@app.post("/schedules/{sid}/run")
async def schedules_run_now(sid: str, user: str = Depends(get_user)):
    if not any(s["id"] == sid for s in scheduler.list_schedules(user)):
        raise HTTPException(404, "No such schedule")
    return await scheduler.run_schedule(sid)


@app.delete("/schedules/{sid}")
def schedules_delete(sid: str, user: str = Depends(get_user)):
    if not scheduler.delete_schedule(user, sid):
        raise HTTPException(404, "No such schedule")
    return {"ok": True}


@app.get("/triggers")
def triggers_list(user: str = Depends(get_user)):
    return {"triggers": scheduler.list_triggers(user), "email_ready": bool(os.getenv("SMTP_HOST"))}


@app.post("/triggers")
def triggers_add(body: TriggerIn, user: str = Depends(get_user)):
    try:
        return scheduler.create_trigger(user, body.name, body.agent_id, body.deliver.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/triggers/{tid}")
def triggers_delete(tid: str, user: str = Depends(get_user)):
    if not scheduler.delete_trigger(user, tid):
        raise HTTPException(404, "No such trigger")
    return {"ok": True}


@app.post("/hooks/{token}")
async def hook_fire(token: str, request: Request):
    """Inbound trigger. JSON {"message": "..."} (or {"text": "..."}), or a Slack slash command (form with text and response_url)."""
    trigger = scheduler.trigger_by_token(token)
    if not trigger:
        raise HTTPException(404, "Unknown trigger")
    check_quota_ip(client_ip(request))
    check_quota(f"hook:{trigger['id']}")  # a leaked link can't burn unlimited quota
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        body = await request.json()
        message = str(body.get("message") or body.get("text") or "") if isinstance(body, dict) else ""
        response_url = ""
    else:
        form = await request.form()
        message, response_url = str(form.get("text", "")), str(form.get("response_url", ""))
    message = message.strip()[:4000]
    if not message:
        raise HTTPException(400, 'Send {"message": "..."}')
    if response_url:  # Slack wants an answer within 3 seconds: acknowledge now, answer afterwards
        async def later():
            try:
                await scheduler.post_to_response_url(response_url, await scheduler.fire(trigger, message))
            except Exception as e:  # noqa: BLE001
                await scheduler.post_to_response_url(response_url, f"Sorry, that failed: {str(e)[:120]}")

        asyncio.create_task(later())
        return {"response_type": "ephemeral", "text": "On it, one moment…"}
    try:
        return {"reply": await scheduler.fire(trigger, message)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"The agent couldn't answer: {str(e)[:160]}") from e


@app.get("/quota")
def quota():
    """How much free-tier AI quota is left today, per provider and model (an estimate; see llm.quota_status)."""
    return llm.quota_status()


@app.get("/characters")
def characters(user: str = Depends(get_user)):
    chars = list(agents.load_characters(user).values())
    # Head first, then built-ins alphabetically, then custom agents in creation order.
    chars.sort(key=lambda c: (c.get("role") != "head", bool(c.get("custom")), c["name"] if not c.get("custom") else ""))
    return chars


@app.get("/presets")
def presets(user: str = Depends(get_user)):
    """Ready-made agents you can hire with one click (those already in the office are left out)."""
    existing = set(agents.load_characters(user))
    items = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    return [{**p, "id": slugify(p["name"])} for p in items if slugify(p["name"]) not in existing]


@app.get("/tools/catalog")
def tools_catalog():
    return [{"id": k, "description": v} for k, v in policy.OPTIONAL_TOOLS.items()]


@app.post("/agents")
def create_agent(body: NewAgent, user: str = Depends(get_user)):
    chars = agents.load_characters(user)
    if len([c for c in chars.values() if c.get("role") != "head"]) >= agents.MAX_WORKERS:
        raise HTTPException(400, f"The office is full ({agents.MAX_WORKERS} agents). Remove a custom agent first.")
    try:
        personality = safety.sanitize_persona(body.personality)
    except safety.PersonaRejected as e:
        raise HTTPException(400, str(e)) from e
    base = slugify(body.name)
    cid, n = base, 2
    while cid in chars:
        cid, n = f"{base}_{n}", n + 1
    agent = {
        "id": cid,
        "owner": user,
        "name": body.name.strip(),
        "archetype": body.archetype.strip(),
        "color": body.color,
        "persona": f"You are {body.name.strip()}, {body.archetype.strip()}. {personality}",
    }
    tools = [t for t in body.tools if t in policy.OPTIONAL_TOOLS]
    docs, sources = [], []
    if body.knowledge:
        k = body.knowledge
        docs = [d for d in knowledge.list_docs(user) if d["id"] in k.doc_ids]
        sources = [s for s in dbsource.list_sources(user) if s["id"] in k.source_ids]
        if not docs and not sources:
            raise HTTPException(400, "Pick at least one of your documents or databases.")
        agent["grounded"] = True
        agent["knowledge"] = {"docs": [d["id"] for d in docs], "sources": [s["id"] for s in sources]}
        agent["starters"] = [s.strip()[:120] for s in k.starters if s.strip()][:4]
        if sources and "database" not in tools:
            tools.append("database")
    if tools:
        agent["tools"] = tools
    if body.look:
        agent["look"] = look_dict(body.look)
    agents.save_custom([*agents.load_custom(), agent])
    if body.knowledge and body.knowledge.exclusive:  # keep these sources private to the new expert
        for d in docs:
            knowledge.set_scope(user, d["id"], [*(d["scope"] or []), cid] if d["scope"] else [cid])
        for s in sources:
            dbsource.set_scope(user, s["id"], [*(s["scope"] or []), cid] if s["scope"] else [cid])
    return {**agent, "custom": True}


@app.patch("/agents/{agent_id}/look")
def update_look(agent_id: str, look: LookIn, user: str = Depends(get_user)):
    items = agents.load_custom()
    for c in items:
        if c["id"] == agent_id and c.get("owner") in (None, user):
            c["look"] = look_dict(look)
            agents.save_custom(items)
            return {**c, "custom": True}
    raise HTTPException(404, "No such custom agent")


@app.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, user: str = Depends(get_user)):
    items = agents.load_custom()
    if not any(c["id"] == agent_id and c.get("owner") in (None, user) for c in items):
        raise HTTPException(404, "No such custom agent")
    agents.save_custom([c for c in items if c["id"] != agent_id])
    return {"ok": True}


@app.get("/agents/{agent_id}/history")
async def agent_history(agent_id: str, user: str = Depends(get_user)):
    character = agents.load_characters(user).get(agent_id)
    if not character:
        raise HTTPException(404, "Unknown character")
    return await agents.history(character, user)


# ---------------- chat ----------------


def friendly_error(e: Exception) -> str:
    text = str(e)
    if "No LLM configured" in text:
        return "No API key is set up yet. Open Settings and add a Gemini or Groq key."
    if isinstance(e, llm.QuotaExhausted):
        return f"{text} Adding another API key (from a different Google project) or a Groq key in Settings gives you more room."
    return text[:300]


@app.post("/chat")
async def chat(req: ChatRequest, request: Request, user: str = Depends(get_user)):
    character = agents.load_characters(user).get(req.character_id)
    if not character:
        raise HTTPException(404, "Unknown character")
    check_quota(user, client_ip(request))

    async def stream():
        yield sse({"state": "thinking"})
        try:
            async for ev in agents.agent_events(character, req.message, user, req.goal):
                yield sse(ev)
        except Exception as e:  # surface provider errors to the UI instead of dropping the stream
            db.log_trace(user, character["id"], "error", name="chat", status="error", detail=f"{type(e).__name__}: {e}")
            yield sse({"state": "error", "message": friendly_error(e)})
        yield sse({"state": "idle"})

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------------- knowledge: documents and databases ----------------


class ScopeIn(BaseModel):
    agents: Optional[list[str]] = None


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    agent_id: str = ""


class NewSource(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=8, max_length=500)
    scope: Optional[list[str]] = None


def _scope(raw: str) -> Optional[list[str]]:
    return [s.strip() for s in raw.split(",") if s.strip()] or None


@app.get("/knowledge")
def knowledge_list(user: str = Depends(get_user)):
    return {
        "docs": knowledge.list_docs(user),
        "sources": dbsource.list_sources(user),
        "limits": {"max_docs": knowledge.MAX_DOCS, "max_mb": knowledge.MAX_BYTES // (1024 * 1024), "formats": knowledge.SUPPORTED_LABEL},
    }


@app.get("/knowledge/strategies")
def knowledge_strategies():
    return {"strategies": [{"id": k, "description": v} for k, v in chunking.STRATEGIES.items()], "default": {"strategy": "auto", "size": chunking.Config().size, "overlap": chunking.Config().overlap}, "limits": {"min_size": chunking.MIN_SIZE, "max_size": chunking.MAX_SIZE}}


class UrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    scope: Optional[list[str]] = None


@app.post("/knowledge/url")
async def knowledge_from_url(body: UrlIn, user: str = Depends(get_user)):
    """Add a web page or online file. It is re-checked for changes automatically (and on demand with /sync)."""
    try:
        return await docsync.ingest_url(user, body.url, body.scope)
    except knowledge.IngestError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/knowledge/{doc_id}/sync")
async def knowledge_sync(doc_id: str, user: str = Depends(get_user)):
    res = await docsync.sync_doc(user, doc_id)
    if res["status"] == "failed":
        raise HTTPException(400, res.get("detail", "Couldn't sync"))
    return res


@app.get("/knowledge/{doc_id}/file")
def knowledge_file(doc_id: str, user: str = Depends(get_user)):
    """The original uploaded file (for 'open the source' links on cited answers)."""
    found = knowledge.original_file(user, doc_id)
    if not found:
        raise HTTPException(404, "The original file isn't available.")
    path, name = found
    from fastapi.responses import FileResponse
    import mimetypes

    return FileResponse(path, filename=name, media_type=mimetypes.guess_type(name)[0] or "application/octet-stream", content_disposition_type="inline")


@app.post("/knowledge/upload")
async def knowledge_upload(
    request: Request,
    file: UploadFile = File(...),
    scope: str = Form(default=""),
    chunking_method: str = Form(default="auto", alias="chunking"),
    chunk_size: int = Form(default=900),
    overlap: int = Form(default=120),
    user: str = Depends(get_user),
):
    check_quota(user, client_ip(request))  # embedding a document uses model quota too
    data = await file.read(knowledge.MAX_BYTES + 1)
    try:
        cfg = chunking.Config(chunking_method, chunk_size, overlap).validate()
        return await knowledge.ingest(user, file.filename or "upload", data, _scope(scope), cfg)
    except (knowledge.IngestError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


class RechunkIn(BaseModel):
    strategy: str = "auto"
    size: int = 900
    overlap: int = 120


@app.post("/knowledge/{doc_id}/rechunk")
async def knowledge_rechunk(doc_id: str, body: RechunkIn, user: str = Depends(get_user)):
    """Cut an existing document with a different method, without uploading it again."""
    try:
        return await knowledge.rechunk(user, doc_id, chunking.Config(body.strategy, body.size, body.overlap).validate())
    except (knowledge.IngestError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@app.get("/knowledge/{doc_id}/chunks")
def knowledge_chunks(doc_id: str, limit: int = 8, user: str = Depends(get_user)):
    rows = knowledge.preview(user, doc_id, min(max(limit, 1), 40))
    if not rows and not knowledge.get_doc(user, doc_id):
        raise HTTPException(404, "No such document")
    return [{**r, "text": r["text"][:700]} for r in rows]


@app.post("/knowledge/reindex")
async def knowledge_reindex(user: str = Depends(get_user)):
    """Re-embed this user's documents (after switching the embedding model)."""
    return {"reindexed": await knowledge.reindex(user), "model": embeddings.model_id()}


@app.post("/knowledge/search")
async def knowledge_search(body: SearchIn, user: str = Depends(get_user)):
    """Try a search the way an agent would, to see which passages come back."""
    hits = await knowledge.search(user, body.agent_id, body.query)
    return [{**h, "text": h["text"][:500]} for h in hits]


@app.patch("/knowledge/{doc_id}/scope")
def knowledge_scope(doc_id: str, body: ScopeIn, user: str = Depends(get_user)):
    if not knowledge.set_scope(user, doc_id, body.agents):
        raise HTTPException(404, "No such document")
    return knowledge.get_doc(user, doc_id)


@app.delete("/knowledge/{doc_id}")
def knowledge_delete(doc_id: str, user: str = Depends(get_user)):
    if not knowledge.delete_doc(user, doc_id):
        raise HTTPException(404, "No such document")
    return {"ok": True}


@app.post("/knowledge/databases")
async def database_add(body: NewSource, user: str = Depends(get_user), _: None = Depends(require_admin)):
    try:
        return await asyncio.to_thread(dbsource.add_url_source, user, body.name, body.url, body.scope)
    except dbsource.UnsafeQuery as e:
        raise HTTPException(400, str(e)) from e


@app.post("/knowledge/databases/upload")
async def database_upload(file: UploadFile = File(...), name: str = Form(...), scope: str = Form(default=""), user: str = Depends(get_user), _: None = Depends(require_admin)):
    data = await file.read(dbsource.MAX_UPLOAD + 1)
    try:
        return await asyncio.to_thread(dbsource.add_sqlite_upload, user, name, data, _scope(scope))
    except dbsource.UnsafeQuery as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/knowledge/databases/{source_id}/scope")
def database_scope(source_id: str, body: ScopeIn, user: str = Depends(get_user), _: None = Depends(require_admin)):
    updated = dbsource.set_scope(user, source_id, body.agents)
    if not updated:
        raise HTTPException(404, "No such database")
    return updated


@app.delete("/knowledge/databases/{source_id}")
def database_delete(source_id: str, user: str = Depends(get_user), _: None = Depends(require_admin)):
    if not dbsource.delete_source(user, source_id):
        raise HTTPException(404, "No such database")
    return {"ok": True}


# ---------------- live team status ----------------


class PresenceItem(BaseModel):
    id: str = Field(max_length=60)
    zone: str = Field(default="", max_length=60)
    activity: str = Field(default="", max_length=80)
    at_desk: bool = False


@app.post("/presence")
def report_presence(items: list[PresenceItem], user: str = Depends(get_user)):
    """The office view reports where each agent is and what they appear to be doing."""
    presence.report(user, [i.model_dump() for i in items[:40]])
    return {"ok": True}


@app.get("/team/status")
def team_status(user: str = Depends(get_user)):
    return presence.snapshot(user, agents.load_characters(user))


# ---------------- observability ----------------


@app.get("/tasks")
def tasks(user: str = Depends(get_user)):
    return db.recent_tasks(user)


@app.get("/notes")
def notes(user: str = Depends(get_user)):
    return db.list_notes(user)


@app.get("/usage")
def usage(user: str = Depends(get_user)):
    return {"today": db.usage_today(user), "daily_limit": DAILY_LIMIT, "per_minute": MINUTE_LIMIT}


@app.get("/metrics")
def metrics(hours: float = 24):
    return {**telemetry.summary(min(max(hours, 0.1), 24 * 30)), "availability": llm.cooldown_status()}


# ---------------- settings: API keys ----------------


@app.get("/settings/keys")
def list_keys(_: None = Depends(require_admin)):
    return {"protected": bool(os.getenv("ADMIN_TOKEN")), "keys": [*llm.env_key_rows(), *db.list_api_keys()]}


@app.post("/settings/keys")
async def add_key(body: NewKey, _: None = Depends(require_admin)):
    """Validate the key with a free metadata call, then store it encrypted. It is never returned to the browser."""
    ok, message = await llm.validate_key(body.provider, body.key.strip())
    if not ok:
        raise HTTPException(400, message)
    key_id = db.add_api_key(body.provider, body.label.strip() or body.provider, body.key.strip())
    db.set_api_key_status(key_id, "ok")
    llm.clear_key_cache()
    return {"id": key_id, "status": "ok", "message": message}


@app.post("/settings/keys/{key_id}/test")
async def test_key(key_id: int, _: None = Depends(require_admin)):
    row = next((r for r in db.q("SELECT id, provider, secret FROM api_keys WHERE id=?", (key_id,))), None)
    if not row:
        raise HTTPException(404, "No such key")
    import secrets_store

    plain = secrets_store.decrypt(row["secret"])
    if not plain:
        db.set_api_key_status(key_id, "error", "Could not decrypt (SECRETS_KEY changed?)")
        raise HTTPException(400, "This key can't be decrypted. Remove it and add it again.")
    ok, message = await llm.validate_key(row["provider"], plain)
    db.set_api_key_status(key_id, "ok" if ok else "error", "" if ok else message)
    return {"ok": ok, "message": message}


@app.delete("/settings/keys/{key_id}")
def remove_key(key_id: int, _: None = Depends(require_admin)):
    db.delete_api_key(key_id)
    llm.clear_key_cache()
    return {"ok": True}


# ---------------- evaluation ----------------

import asyncio  # noqa: E402

from evals import run as evals_run  # noqa: E402

_eval_state: dict = {"running": False, "done": 0, "total": 0, "error": ""}
_eval_task: Optional[asyncio.Task] = None


@app.get("/evals/latest")
def evals_latest():
    return evals_run.latest() or {}


@app.get("/evals/status")
def evals_status():
    return _eval_state


@app.post("/evals/run")
async def evals_start(routing_only: bool = False, judge: bool = False, _: None = Depends(require_admin)):
    """Start an evaluation run in the background. Live cases use real model calls (and quota)."""
    global _eval_task
    if _eval_state["running"]:
        raise HTTPException(409, "An evaluation is already running")

    def progress(done: int, total: int) -> None:
        _eval_state.update(done=done, total=total)

    async def job() -> None:
        _eval_state.update(running=True, done=0, total=0, error="")
        try:
            await evals_run.run_suite(routing_only, None, judge, progress)
        except Exception as e:  # noqa: BLE001
            _eval_state["error"] = f"{type(e).__name__}: {e}"[:200]
        finally:
            _eval_state["running"] = False

    _eval_task = asyncio.create_task(job())
    return {"started": True}


_ret_state: dict = {"running": False, "error": ""}


@app.get("/evals/retrieval")
def evals_retrieval():
    """The latest retrieval benchmark: how well search finds the right passage, and the similarity cutoff it recommends."""
    from evals import retrieval

    f = retrieval.REPORT
    return {"report": json.loads(f.read_text(encoding="utf-8")) if f.exists() else None, "running": _ret_state["running"], "error": _ret_state["error"], "corpus": retrieval.CORPUS.exists(), "cutoff_in_use": embeddings.threshold("chunk"), "reranker": rerank.model_name()}


@app.post("/evals/retrieval/run")
async def evals_retrieval_run(_: None = Depends(require_admin)):
    """Re-measure retrieval on the bundled benchmark. Runs locally with the embedding model, so it costs no API quota."""
    from evals import retrieval

    if _ret_state["running"]:
        raise HTTPException(409, "The benchmark is already running")
    if not retrieval.CORPUS.exists():
        raise HTTPException(400, "The benchmark corpus is missing (python -m evals.retrieval --build).")
    if embeddings.provider() == "none":
        raise HTTPException(400, "Embeddings are turned off, so there is nothing to measure.")

    async def job() -> None:
        _ret_state.update(running=True, error="")
        try:
            await retrieval.evaluate(sweep_chunking=False)
        except Exception as e:  # noqa: BLE001
            _ret_state["error"] = f"{type(e).__name__}: {e}"[:200]
        finally:
            _ret_state["running"] = False

    asyncio.create_task(job())
    return {"started": True}


# ---------------- drafting an expert from knowledge ----------------


class SuggestIn(BaseModel):
    doc_ids: list[str] = Field(default_factory=list, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=10)


class ExpertDraft(BaseModel):
    name: str = Field(description="a short, friendly first name, different from the existing team")
    archetype: str = Field(description="their role in under 8 words, e.g. 'Employee handbook expert'")
    personality: str = Field(description="2-3 sentences on how they talk and help; they answer only from their sources")
    starters: list[str] = Field(description="4 realistic questions a user could ask that the sources can answer")


def _fallback_draft(doc_names: list[str], source_names: list[str]) -> dict:
    topic = (doc_names + source_names)[0].rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()
    return {
        "name": "Sage",
        "archetype": f"{topic} expert",
        "personality": "Clear, calm and precise. Always checks the sources before answering and says plainly when something isn't covered.",
        "starters": ["What are the main topics covered?", f"Give me a short summary of {topic}.", "What should I know first?"],
    }


@app.post("/agents/suggest-from-knowledge")
async def suggest_expert(body: SuggestIn, user: str = Depends(get_user)):
    """Draft an expert agent (name, role, personality, starter questions) from the content of the chosen sources."""
    samples = knowledge.samples(user, body.doc_ids)
    sources = [s for s in dbsource.list_sources(user) if s["id"] in body.source_ids]
    if not samples and not sources:
        raise HTTPException(400, "Pick at least one of your documents or databases.")
    doc_names = [s["name"] for s in samples]
    source_names = [s["name"] for s in sources]
    material = "\n\n".join(f"Document {s['name']}:\n" + "\n---\n".join(s["passages"]) for s in samples)
    material += "\n\n".join(f"\n\nDatabase {s['name']} tables:\n{s['schema'][:800]}" for s in sources)
    taken = ", ".join(c["name"] for c in agents.load_characters(user).values())
    try:
        sys = f"Design a knowledge-base expert agent for an office team, based on the material below. Existing names to avoid: {taken}. Keep it practical and truthful to the material."
        draft = await llm.ainvoke_retry(llm.get_llm(tier="smart", schema=ExpertDraft), [("system", sys), ("human", material[:7000])])
        out = draft.model_dump()
        out["personality"] = safety.sanitize_persona(out["personality"])
    except Exception as e:  # noqa: BLE001 - a draft is a convenience; fall back to a sensible default
        db.log_trace(user, "expert", "error", name="suggest_expert", status="error", detail=f"{type(e).__name__}: {e}")
        out = _fallback_draft(doc_names, source_names)
    out["starters"] = [s.strip()[:120] for s in out.get("starters", []) if s.strip()][:4]
    return {**out, "doc_names": doc_names, "source_names": source_names}
