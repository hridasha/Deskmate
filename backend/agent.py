"""Agent runtime.

- Worker agents: a LangGraph loop (model <-> tools) with persisted state, long-term memory, a guard that
  catches claims of actions that never happened, and fencing of untrusted tool output.
- The Head: a planner that routes work to specialists in parallel, tracks it on a task board, then writes the answer.
"""
import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from typing import Literal

from pydantic import BaseModel, Field

import db
import dbsource
import knowledge
import memory
import policy
import presence
import router
import safety
from llm import ainvoke_retry, get_llm
from telemetry import Telemetry

CHAR_DIR = Path(__file__).parent / "characters"
CUSTOM_FILE = db.DATA_DIR / "custom_agents.json"
EMOTIONS = ["happy", "thinking", "annoyed", "surprised", "neutral"]
EMOTION_RE = re.compile(r"\[emotion:\s*(\w+)\]", re.IGNORECASE)
MAX_WORKERS = 19  # number of desks in the office
MAX_SUBTASKS = 3
INTERNAL = "[internal check] "
KNOWLEDGE_TOOLS = {"search_knowledge", "query_database", "describe_data_sources"}

# ---------------- persistence ----------------

_checkpointer = None


def set_checkpointer(saver) -> None:
    global _checkpointer
    _checkpointer = saver


def get_checkpointer():
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = MemorySaver()
    return _checkpointer


# ---------------- MCP (Model Context Protocol) tools ----------------

MCP_SERVERS = {
    "time": {"command": "python", "args": ["-m", "mcp_server_time"], "transport": "stdio"},
}
MCP_TOOL_NAMES: set[str] = set()
_mcp_tools: list | None = None


async def get_mcp_tools() -> list:
    """Load tools from each MCP server once. A server that fails to start is skipped; the rest still work."""
    global _mcp_tools
    if _mcp_tools is None:
        loaded: list = []
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            for name, cfg in MCP_SERVERS.items():
                try:
                    tools = await asyncio.wait_for(MultiServerMCPClient({name: cfg}).get_tools(), timeout=40)
                    loaded.extend(tools)
                except Exception as e:  # noqa: BLE001
                    print(f"MCP server '{name}' unavailable: {e!r}")
        except ImportError:
            pass
        import webfetch

        loaded.append(webfetch.make_tool())  # in-process and SSRF-guarded at connect time (the old MCP fetch server could be redirected inward)
        MCP_TOOL_NAMES.update(t.name for t in loaded)
        _mcp_tools = loaded
    return _mcp_tools


# ---------------- characters ----------------


def load_custom() -> list[dict]:
    try:
        return json.loads(CUSTOM_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_custom(items: list[dict]) -> None:
    CUSTOM_FILE.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")


def load_characters(owner: str | None = None) -> dict[str, dict]:
    """Built-in cast plus custom hires. With `owner`, custom agents made by someone else are left out (agents without an owner are shared)."""
    chars = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in CHAR_DIR.glob("*.json")}
    for c in load_custom():
        if owner is None or c.get("owner") in (None, owner):
            chars[c["id"]] = {**c, "custom": True}
    return chars


# ---------------- run context and tools ----------------


@dataclass
class Ctx:
    user: str
    agent_id: str
    tier: str = "fast"
    callbacks: list = field(default_factory=list)
    sources: list = field(default_factory=list)  # passages cited this turn, numbered [1], [2]... (a Head's team shares one list)


def content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)


def parse_reply(text: str) -> tuple[str, str]:
    """Split a reply into (clean text, emotion). Tolerant of missing or malformed tags; falls back to a classifier."""
    tags = EMOTION_RE.findall(text)
    clean = EMOTION_RE.sub("", text).strip()
    emo = tags[-1].lower() if tags else ""
    return clean, emo if emo in EMOTIONS else router.guess_emotion(clean)


def kb_limits(character: dict) -> tuple[set[str] | None, set[str] | None]:
    """The documents and databases a knowledge-expert agent is limited to (None = no limit)."""
    if not character.get("grounded"):
        return None, None
    kb = character.get("knowledge") or {}
    return set(kb.get("docs", [])), set(kb.get("sources", []))


def make_tools(ctx: Ctx, character: dict, mcp_tools: list) -> list:
    """Per-request tools bound to this user and agent, filtered by what the agent is allowed to use."""
    user, aid = ctx.user, ctx.agent_id
    only_docs, only_sources = kb_limits(character)

    @tool
    def take_note(text: str) -> str:
        """Save a short note that the user can read later. Use it when the user asks you to note, save or write something down."""
        return f"Saved note #{db.add_note(user, aid, text[:500])}."

    @tool
    def recall_notes() -> str:
        """List the user's most recent saved notes."""
        rows = db.list_notes(user, 10)
        return "\n".join(f"- {r['text']}" for r in rows) or "No notes yet."

    @tool
    async def remember(fact: str) -> str:
        """Remember a lasting fact about the user or their preferences, so you can use it in future conversations."""
        return await memory.remember(user, aid, fact)

    @tool
    async def recall(query: str) -> str:
        """Look up things you previously remembered about the user."""
        found = await memory.recall(user, aid, query)
        return "\n".join(f"- {t}" for t in found) or "Nothing relevant remembered."

    @tool(response_format="content_and_artifact")
    async def search_knowledge(query: str) -> tuple[str, list]:
        """Search the user's uploaded documents (PDF, Word, Excel, CSV, notes) for passages relevant to a question. Each passage is numbered like [1]; cite the numbers in your answer, e.g. "Leave is 25 days [1]"."""
        hits = await knowledge.search(user, aid, query, only=only_docs)
        if not hits:
            return "No relevant passages found in the uploaded documents.", []
        items = []
        for h in hits:
            known = next((s for s in ctx.sources if s["doc_id"] == h["doc_id"] and s["text"] == h["text"]), None)
            if known is None:
                known = {"n": len(ctx.sources) + 1, "doc_id": h["doc_id"], "doc": h["doc"], "locator": h["locator"], "text": h["text"][:900]}
                ctx.sources.append(known)
            items.append(known)
        return "\n\n".join(f"[{s['n']}] {s['doc']} \u00b7 {s['locator']}\n{s['text']}" for s in items), items

    @tool
    def describe_data_sources() -> str:
        """List the connected databases with their tables and columns. Call this before writing a query."""
        return dbsource.describe(user, aid, only_sources)

    @tool
    async def query_database(source: str, sql: str) -> str:
        """Run one read-only SELECT query against a connected database (source is the database name). Returns at most 50 rows."""
        return await dbsource.query(user, aid, source, sql, only_sources)

    @tool
    def team_status() -> str:
        """Get a live snapshot of the whole team: who is working on what, how far along it is, the steps taken so far, and where everyone is in the office. Use it whenever the user asks about the team, who is busy or free, progress, or where someone is."""
        return presence.describe(user, load_characters(user))

    all_tools = [take_note, recall_notes, remember, recall, search_knowledge, describe_data_sources, query_database, team_status, *mcp_tools]
    return policy.filter_tools(all_tools, policy.allowed_tools(character))


def system_prompt(character: dict, goal: str, extra: str = "", memories: list[str] | None = None, summary: str = "", docs: list[str] | None = None, sources: list[str] | None = None) -> str:
    parts = [
        character["persona"],
        f"The user's goal for you: {goal or 'Be a helpful companion.'}",
        "You may use your tools when they genuinely help. Only say you saved or remembered something if you actually called the tool.",
        safety.UNTRUSTED_RULE,
        "Stay in character. Be concise (under 120 words unless asked for more). "
        f"End every reply with an emotion tag on its own line, exactly like [emotion: happy], choosing one of: {', '.join(EMOTIONS)}.",
    ]
    if docs:
        parts.append(f"The user has uploaded documents: {', '.join(docs)}. For questions about them, call search_knowledge, answer only from what it returns, and cite sources like [name \u00b7 location]. If nothing relevant is found, say so instead of guessing.")
    if sources:
        parts.append(f"Connected databases: {', '.join(sources)}. Call describe_data_sources to see the tables, then query_database with a read-only SELECT. Base numbers on the query results.")
    if character.get("grounded"):
        parts.append("You are a knowledge-base expert. For any question about your topic, call search_knowledge first (and query_database for questions about data), answer only from what the results say, cite the sources, and if they don't contain the answer, say so plainly instead of guessing. For greetings or thanks, just reply normally.")
    if memories:
        parts.append("What you remember about this user from earlier conversations:\n" + "\n".join(f"- {m}" for m in memories))
    if summary:
        parts.append("Summary of the earlier part of this conversation:\n" + summary)
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


# ---------------- claim detection (the guard) ----------------

_CLAIM = re.compile(
    r"\b(?:i(?:['’]ve| have|['’]ll| will)?|we(?:['’]ve| have)?|it(?:['’]s| is| has been)?|that(?:['’]s| is| has been)?|all)\s+(?:just |already |now |safely )?"
    r"(?:saved|noted|jotted(?: down)?|stored|filed|written down|remembered)\b|\bmade a note\b|\bconsider (?:it|that) (?:noted|saved|done)\b",
    re.IGNORECASE,
)


def claims_saved(text: str) -> bool:
    return bool(_CLAIM.search(text))


def tools_used_this_turn(messages: list) -> set[str]:
    used: set[str] = set()
    for m in reversed(messages):
        if isinstance(m, HumanMessage) and not str(m.content).startswith(INTERNAL):
            break
        if isinstance(m, AIMessage):
            used |= {tc["name"] for tc in (m.tool_calls or [])}
    return used


# ---------------- the worker graph ----------------


class AgentState(MessagesState):
    retries: int


_summaries: dict[tuple[str, int], str] = {}
HISTORY_LIMIT, HISTORY_KEEP = 24, 12


async def _prepare_history(thread: str, messages: list, config: RunnableConfig) -> tuple[list, str]:
    """Keep the prompt small: summarize the older part of a long conversation instead of resending it all."""
    if len(messages) <= HISTORY_LIMIT:
        return messages, ""
    cut = len(messages) - HISTORY_KEEP
    while cut < len(messages) and not isinstance(messages[cut], HumanMessage):
        cut += 1  # never start mid tool-call
    older, recent = messages[:cut], messages[cut:]
    key = (thread, len(older) // 4)
    if key not in _summaries:
        transcript = "\n".join(f"{'User' if isinstance(m, HumanMessage) else 'Assistant'}: {content_to_text(m.content)[:300]}" for m in older if isinstance(m, (HumanMessage, AIMessage)) and content_to_text(m.content))
        try:
            resp = await ainvoke_retry(get_llm(tier="fast"), [SystemMessage("Summarize this conversation in under 120 words. Keep facts about the user, decisions and open tasks."), HumanMessage(transcript[-6000:])], config)
            _summaries[key] = content_to_text(resp.content)
        except Exception:  # noqa: BLE001 - fall back to just trimming
            _summaries[key] = ""
    return recent, _summaries[key]


def build_graph(character: dict, goal: str, tools: list, ctx: Ctx, extra: str = ""):
    async def call_model(state: AgentState, config: RunnableConfig):
        msgs = state["messages"]
        thread = (config.get("configurable") or {}).get("thread_id", "")
        recent, summary = await _prepare_history(thread, msgs, config)
        mems: list[str] = []
        last_user = next((content_to_text(m.content) for m in reversed(msgs) if isinstance(m, HumanMessage) and not str(m.content).startswith(INTERNAL)), "")
        if last_user and memory.has_memories(ctx.user, ctx.agent_id):
            mems = await memory.recall(ctx.user, ctx.agent_id, last_user)
        names = {t.name for t in tools}
        only_docs, only_sources = kb_limits(character)
        docs = knowledge.doc_names(ctx.user, ctx.agent_id, only=only_docs) if "search_knowledge" in names else []
        sources = dbsource.source_names(ctx.user, ctx.agent_id, only_sources) if "query_database" in names else []
        prompt_extra = extra
        if "team_status" in names:
            prompt_extra = (extra + "\n\n" if extra else "") + "When asked about the team (who is active or busy, what someone is doing, progress, or where people are), call team_status and report exactly what it says. Do not invent progress. Only people marked WORKING are working; others are idle even if they are walking around."
        sys = SystemMessage(content=system_prompt(character, goal, prompt_extra, mems, summary, docs, sources))
        resp = await ainvoke_retry(get_llm(tools, tier=ctx.tier), [sys] + recent, config)
        return {"messages": [resp]}

    def route(state: AgentState):
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else "guard"

    async def sanitize(state: AgentState):
        """Fence output from untrusted tools (web pages) so the model treats it as data, not instructions."""
        out = []
        for m in reversed(state["messages"]):
            if not isinstance(m, ToolMessage):
                break
            text = content_to_text(m.content)
            if m.name in safety.UNTRUSTED_TOOLS and not text.startswith("<untrusted"):
                if safety.scan(text):
                    db.log_trace(ctx.user, ctx.agent_id, "guard", name="injection_in_tool_output", detail=f"{m.name}: {text[:120]}")
                out.append(ToolMessage(content=safety.wrap_untrusted(text, m.name), tool_call_id=m.tool_call_id, name=m.name, id=m.id))
        return {"messages": out}

    async def guard(state: AgentState):
        """If the reply claims to have saved something but no tool ran, make the model fix it (once)."""
        if state.get("retries", 0) >= 1:
            return {}
        text = content_to_text(state["messages"][-1].content)
        if claims_saved(text) and not (tools_used_this_turn(state["messages"]) & {"take_note", "remember"}):
            db.log_trace(ctx.user, ctx.agent_id, "guard", name="unperformed_action", detail=text[:200])
            nudge = INTERNAL + "You said you saved or remembered something, but you did not call a tool. Call take_note (a note the user can read later) or remember (a lasting fact) now, or correct your reply."
            return {"retries": 1, "messages": [HumanMessage(content=nudge)]}
        return {}

    def after_guard(state: AgentState):
        last = state["messages"][-1]
        return "model" if isinstance(last, HumanMessage) and str(last.content).startswith(INTERNAL) else END

    g = StateGraph(AgentState)
    g.add_node("model", call_model)
    g.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    g.add_node("sanitize", sanitize)
    g.add_node("guard", guard)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", route, ["tools", "guard"])
    g.add_edge("tools", "sanitize")
    g.add_edge("sanitize", "model")
    g.add_conditional_edges("guard", after_guard, ["model", END])
    return g.compile(checkpointer=get_checkpointer())


def cited(text: str, sources: list) -> list:
    """Only the sources the answer actually refers to, e.g. [2]; if it cites none, everything that was looked at."""
    used = {int(n) for n in re.findall(r"\[(\d{1,3})\]", text)}
    picked = [s for s in sources if s["n"] in used]
    return picked or list(sources)


async def stream_agent(graph, message: str, config: RunnableConfig, ctx: "Ctx | None" = None) -> AsyncIterator[dict]:
    """Run a worker graph and turn its progress into UI events (thinking, tool calls, sources, the final reply)."""
    pending: AIMessage | None = None
    async for update in graph.astream({"messages": [HumanMessage(message)], "retries": 0}, config, stream_mode="updates"):
        for node, data in update.items():
            if node == "model":
                msg = data["messages"][-1]
                if getattr(msg, "tool_calls", None):
                    pending = None
                    for tc in msg.tool_calls:
                        yield {"state": "tool_call", "tool": tc["name"], "kind": "mcp" if tc["name"] in MCP_TOOL_NAMES else "knowledge" if tc["name"] in KNOWLEDGE_TOOLS else "local"}
                else:
                    pending = msg
            elif node == "guard":
                if data and data.get("messages"):
                    pending = None  # the guard asked for a corrected reply
                elif pending is not None:
                    text, emotion = parse_reply(content_to_text(pending.content))
                    if ctx and ctx.sources:
                        yield {"state": "sources", "items": cited(text, ctx.sources)}
                    yield {"state": "speaking", "text": text, "emotion": emotion}
                    pending = None
    if pending is not None:
        text, emotion = parse_reply(content_to_text(pending.content))
        if ctx and ctx.sources:
            yield {"state": "sources", "items": cited(text, ctx.sources)}
        yield {"state": "speaking", "text": text, "emotion": emotion}


def pick_tier(text: str) -> str:
    """Route cheap small talk to the fast model and heavier requests to the stronger one."""
    heavy = len(text) > 400 or re.search(r"\b(plan|analy[sz]e|compare|step[- ]by[- ]step|in detail|strategy|research|itinerary)\b", text, re.I)
    return "smart" if heavy else "fast"


# ---------------- the Head: plan, delegate in parallel, answer ----------------


class SubTask(BaseModel):
    agent_id: str = Field(description="id of the teammate who should do this")
    task: str = Field(description="one clear sentence describing what they should do")


class Plan(BaseModel):
    intent: Literal["delegate", "status", "answer"] = Field(
        default="delegate",
        description="'status' if the user asks about the team's activity, availability, progress or whereabouts; 'answer' if you can answer alone; 'delegate' if teammates should do work",
    )
    subtasks: list[SubTask] = Field(default_factory=list, description="up to 3 subtasks; empty if the Head can answer alone")


_STATUS_Q = re.compile(
    r"\b(?:who(?:'s|\s+is|\s+are)?\s+(?:all\s+)?(?:active|busy|free|available|working|idle|around|here)|which\s+(?:agents?|people|team\s*members?)|"
    r"what(?:'s|\s+is|\s+are)\s+(?:everyone|everybody|the\s+team|each\s+(?:agent|person)|all\s+(?:agents|of\s+them)|\w+)\s+(?:doing|up\s+to|working\s+on)|"
    r"(?:team|office|everyone'?s?)\s+status|status\s+(?:of|update)|where(?:'s|\s+is|\s+are)\s+\w+|where\s+\w+\s+(?:is|are)\b|is\s+\w+\s+(?:busy|free|available|working|idle|around)|"
    r"what\s+(?:are|is)\s+(?:\w+\s+(?:and|&)\s+)+\w+\s+(?:doing|working\s+on|up\s+to)|how(?:'s|\s+is)\s+\w+\s+getting\s+(?:on|along)|"
    r"(?:any(?:one|body)|who)\s+(?:is\s+)?(?:available|free|idle)|tell\s+me\s+what\s+(?:the\s+team|everyone|everybody)\s+(?:is|are)\s+(?:doing|up\s+to|working)|"
    r"(?:any\s+)?updates?\s+(?:from|on)\s+(?:the\s+)?(?:team|everyone|everybody)|"
    r"how(?:'s|\s+is)\s+.{0,40}(?:going|progressing)|how\s+far\s+along|still\s+(?:working|busy)|who(?:'s|\s+is)\s+in\s+the\s+\w+|current\s+workload|progress\s+(?:of|on|so far)|what\s+is\s+the\s+progress)\b",
    re.I,
)


async def make_plan(message: str, workers: dict[str, dict], user: str, head_id: str, config: RunnableConfig) -> tuple[list[SubTask], str]:
    """Decide who does what. Cheap router first; only fall back to an LLM planner when it isn't sure."""
    rt = router.Router(workers)
    scores = rt.scores(message)
    named = router.delegated_targets(message, workers)
    if len(named) == 1:  # a single, clearly named teammate: no need to spend a planning call
        return [SubTask(agent_id=named[0], task=router.clean_task(message, [workers[named[0]]["name"]]))], "explicit"
    if not named and _STATUS_Q.search(message):  # a question about the team: the Head answers it from live status
        return [], "status"
    mentioned = router.explicit_targets(message, workers)  # names that appear, with or without a delegation verb
    if not named and not mentioned and rt.confident(scores):  # a keyword guess must not override anyone the user mentioned
        return [SubTask(agent_id=scores[0][0], task=message.strip())], "router"
    try:
        roster = "\n".join(f"- {aid}: {a['name']} ({a['archetype']})" for aid, a in workers.items())
        hints = ", ".join(f"{aid} ({s:.2f})" for aid, s in scores[:3])
        if named:
            hints += f". The user explicitly named: {', '.join(named)}"
        sys = SystemMessage(f"You are the Head of a small office. Decide which teammates, if any, should work on the user's request. Use at most {MAX_SUBTASKS} subtasks, each for a different teammate, each one clear sentence. Return no subtasks if the request is small talk, a question about the team itself (who is busy or free, what someone is doing, progress, where people are), or something you can answer yourself.\nTeam:\n{roster}\nA quick keyword router suggests: {hints}.")
        plan = await ainvoke_retry(get_llm(tier="smart", schema=Plan), [sys, HumanMessage(message)], config)
        if plan.intent in ("status", "answer"):
            return [], "status" if plan.intent == "status" else "llm"
        seen, tasks = set(), []
        for st in plan.subtasks:
            if st.agent_id in workers and st.agent_id not in seen:
                seen.add(st.agent_id)
                tasks.append(st)
        return tasks[:MAX_SUBTASKS], "llm"
    except Exception as e:  # noqa: BLE001
        db.log_trace(user, head_id, "plan", name="planner_error", status="error", detail=str(e))
        if named:  # the LLM is unavailable: give each named person the whole request
            return [SubTask(agent_id=a, task=message.strip()) for a in named[:MAX_SUBTASKS]], "fallback"
        return [], "fallback"


async def run_worker(worker: dict, task: str, relay_from: str, user: str, mcp_tools: list, sources: list | None = None) -> str:
    ctx = Ctx(user=user, agent_id=worker["id"], tier=pick_tier(task), callbacks=[Telemetry(user, worker["id"])], sources=sources if sources is not None else [])
    graph = build_graph(worker, "", make_tools(ctx, worker, mcp_tools), ctx)
    cfg: RunnableConfig = {"configurable": {"thread_id": f"{user}:{worker['id']}:team"}, "callbacks": ctx.callbacks}
    presence.begin(user, worker["id"], f"{relay_from} asked: {task}")
    try:
        out = await graph.ainvoke({"messages": [HumanMessage(f"{relay_from} asks you: {task}")], "retries": 0}, cfg)
    finally:
        presence.end(user, worker["id"])
    return parse_reply(content_to_text(out["messages"][-1].content))[0] or "(no answer)"


async def orchestrate(head: dict, workers: dict[str, dict], message: str, user: str, goal: str = "") -> AsyncIterator[dict]:
    """Stream UI events while the Head plans, hands work to specialists in parallel, then answers."""
    mcp_tools = await get_mcp_tools()
    presence.begin(user, head["id"], f"coordinating: {message}")
    try:
        async for ev in _orchestrate(head, workers, message, user, goal, mcp_tools):
            yield ev
    finally:
        presence.end(user, head["id"])


async def _orchestrate(head: dict, workers: dict[str, dict], message: str, user: str, goal: str, mcp_tools: list) -> AsyncIterator[dict]:
    head_ctx = Ctx(user=user, agent_id=head["id"], tier=pick_tier(message), callbacks=[Telemetry(user, head["id"])])
    cfg: RunnableConfig = {"configurable": {"thread_id": f"{user}:{head['id']}"}, "callbacks": head_ctx.callbacks}

    tasks, via = await make_plan(message, workers, user, head["id"], cfg)
    db.log_trace(user, head["id"], "plan", name="plan", detail=via)
    batch = uuid.uuid4().hex[:8]
    rows = [{"id": f"{batch}-{i}", "agent": t.agent_id, "task": t.task} for i, t in enumerate(tasks)]
    for r in rows:
        db.upsert_task(r["id"], parent=batch, user=user, agent=r["agent"], task=r["task"], status="pending")
    if rows:
        yield {"state": "plan", "via": via, "tasks": [{**r, "status": "pending"} for r in rows]}

    results: dict[str, str] = {}
    if rows:
        events: asyncio.Queue = asyncio.Queue()
        sem = asyncio.Semaphore(3)

        async def job(r: dict):
            async with sem:
                await events.put({"state": "delegate", "to": r["agent"], "task": r["task"], "task_id": r["id"]})
                db.upsert_task(r["id"], status="running", started=time.time())
                try:
                    text = await asyncio.wait_for(run_worker(workers[r["agent"]], r["task"], head["name"], user, mcp_tools, head_ctx.sources), timeout=90)
                    ok = True
                except Exception as e:  # noqa: BLE001
                    text, ok = f"(couldn't finish: {type(e).__name__})", False
                results[r["agent"]] = text
                db.upsert_task(r["id"], status="done" if ok else "failed", result=text[:1000], ended=time.time())
                await events.put({"state": "delegate_result", "to": r["agent"], "text": text, "task_id": r["id"], "ok": ok})

        runner = asyncio.gather(*(job(r) for r in rows))
        while not (runner.done() and events.empty()):
            try:
                yield await asyncio.wait_for(events.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
        await runner

    extra = ""
    if results:
        listing = "\n".join(f"- {workers[a]['name']}: {t}" for a, t in results.items())
        extra = f"Your team just worked on the user's request. Combine their results into one short answer in your own voice and credit them by name. Keep any source numbers like [1] exactly as they appear.\nTeam results:\n{listing}"
    graph = build_graph(head, goal, make_tools(head_ctx, head, mcp_tools), head_ctx, extra)
    async for ev in stream_agent(graph, message, cfg, head_ctx):
        yield ev


async def agent_events(character: dict, message: str, user: str, goal: str = "") -> AsyncIterator[dict]:
    """Events for chatting directly with one agent (the Head goes through the planner)."""
    if character.get("role") == "head":
        workers = {k: v for k, v in load_characters(user).items() if v.get("role") != "head"}
        async for ev in orchestrate(character, workers, message, user, goal):
            yield ev
        return
    ctx = Ctx(user=user, agent_id=character["id"], tier=pick_tier(message), callbacks=[Telemetry(user, character["id"])])
    graph = build_graph(character, goal, make_tools(ctx, character, await get_mcp_tools()), ctx)
    cfg: RunnableConfig = {"configurable": {"thread_id": f"{user}:{character['id']}"}, "callbacks": ctx.callbacks}
    presence.begin(user, character["id"], f"answering you: {message}")
    try:
        async for ev in stream_agent(graph, message, cfg, ctx):
            yield ev
    finally:
        presence.end(user, character["id"])


async def history(character: dict, user: str) -> list[dict]:
    """Saved conversation for display, read from the checkpointer."""
    graph = build_graph(character, "", [], Ctx(user=user, agent_id=character["id"]))
    state = await graph.aget_state({"configurable": {"thread_id": f"{user}:{character['id']}"}})
    out = []
    for m in (state.values or {}).get("messages", []):
        text = content_to_text(m.content)
        if isinstance(m, HumanMessage) and text and not text.startswith(INTERNAL):
            out.append({"role": "user", "text": text})
        elif isinstance(m, AIMessage) and text and not m.tool_calls:
            out.append({"role": "agent", "text": parse_reply(text)[0]})
    return out
