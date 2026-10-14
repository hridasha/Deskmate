"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentInfo, Interaction, Live } from "@/components/studio/StudioScene";
import { VIEWS, assignSlots, deskView, type CameraView } from "@/components/studio/config";
import LookEditor from "@/components/studio/LookEditor";
import { DEFAULT_LOOK, type Look } from "@/components/studio/looks";
import SettingsModal from "@/components/SettingsModal";
import { SignOut } from "@/components/AuthGate";
import KnowledgeModal from "@/components/KnowledgeModal";
import ExpertModal, { type ExpertForm } from "@/components/ExpertModal";
import { AgentSwitcher } from "@/components/AgentRoster";
import { apiFetch } from "@/lib/api";
import { plainText } from "@/lib/text";
import Markdown from "@/components/Markdown";
import MessageExtras, { type Msg, type Source } from "@/components/MessageExtras";

const StudioScene = dynamic(() => import("@/components/studio/StudioScene"), {
  ssr: false,
  loading: () => <div className="flex h-full items-center justify-center text-sm text-white/60">Building the office…</div>,
});

type Agent = AgentInfo & { archetype?: string; custom?: boolean; starters?: string[]; grounded?: boolean; knowledge?: { docs: string[]; sources: string[] } };
type LogEntry = { id: number; icon: string; text: string };
type QItem = { id: string; fromId: string; toId: string; task: string; reply?: string };
type BoardTask = { id: string; agent: string; task: string; status: string; result?: string };

const HEAD_TASKS = [
  "Who is busy right now, what are they doing, and where is everyone?",
  "Ask Oscar to explain compound interest in one sentence, then summarize it for me.",
  "Have Pam plan my study week for a big exam, then give me the short version.",
  "Ask Jim for a funny but honest status update on our project.",
];
const DEFAULT_TASKS = ["Introduce yourself in two sentences.", "Give me a 3-step plan for my day.", "What time is it in Tokyo?"];

const PALETTE = ["#6d5acd", "#d9482b", "#3f6f9b", "#4a8f5a", "#e75fa0", "#e0a800", "#00a6a6", "#8a6d3b"];

export default function StudioClient() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [live, setLive] = useState<Record<string, Live>>({});
  const [chats, setChats] = useState<Record<string, Msg[]>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [ambient, setAmbient] = useState(true);
  const [queue, setQueue] = useState<QItem[]>([]);
  const [board, setBoard] = useState<BoardTask[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [showKnowledge, setShowKnowledge] = useState(false);
  const [expert, setExpert] = useState<{ docs: string[]; sources: string[] } | null>(null);
  const [needsKey, setNeedsKey] = useState(false);
  const [quotaLevel, setQuotaLevel] = useState<"none" | "ok" | "low" | "exhausted">("ok");
  // The scene shows one handoff at a time; parallel tasks wait in line and each is shown once it has an answer.
  const interaction: Interaction = queue[0] ? { fromId: queue[0].fromId, toId: queue[0].toId, task: queue[0].task, reply: queue[0].reply } : null;
  const [log, setLog] = useState<LogEntry[]>([]);
  const [tab, setTab] = useState<"chat" | "team" | "tasks" | "activity">("chat");
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [freshIds, setFreshIds] = useState<string[]>([]);
  const [leavingIds, setLeavingIds] = useState<string[]>([]);
  const deleting = useRef<Set<string>>(new Set());
  const [showNew, setShowNew] = useState(false);
  const [showLook, setShowLook] = useState(false);
  const [view, setView] = useState<CameraView | null>(null);
  const [followId, setFollowId] = useState<string | null>(null);
  const viewNonce = useRef(0);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const logId = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  const addLog = useCallback((icon: string, text: string) => {
    setLog((l) => [{ id: logId.current++, icon, text }, ...l].slice(0, 60));
  }, []);

  const loadAgents = useCallback(async (selectId?: string) => {
    try {
      const c: Agent[] = await (await apiFetch(`/characters`)).json();
      setAgents(c);
      setSelectedId((cur) => selectId ?? cur ?? c[0]?.id ?? null);
    } catch {
      setError("Can't reach the backend. Is it running?");
    }
  }, []);

  useEffect(() => {
    loadAgents();
  }, [loadAgents]);

  const reportPresence = useCallback((items: { id: string; zone: string; activity: string; at_desk: boolean }[]) => {
    apiFetch("/presence", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(items) }).catch(() => {});
  }, []);

  const checkKeys = useCallback(() => {
    apiFetch("/health")
      .then((r) => r.json())
      .then((h) => setNeedsKey(Object.values(h.llm as Record<string, number>).reduce((a, b) => a + b, 0) === 0))
      .catch(() => {});
    apiFetch("/quota")
      .then((r) => r.json())
      .then((q) => setQuotaLevel(q.level))
      .catch(() => {});
  }, []);

  useEffect(() => {
    checkKeys();
  }, [checkKeys]);

  const selected = agents.find((a) => a.id === selectedId) ?? null;
  const selectedLive = selectedId ? live[selectedId] : undefined;
  const busy = !!selectedLive && selectedLive.state !== "idle";
  const nameOf = (id: string) => agents.find((a) => a.id === id)?.name ?? id;

  // Load the saved conversation the first time someone is selected (it lives on the server).
  useEffect(() => {
    if (!selectedId || chats[selectedId]) return;
    let live = true;
    apiFetch(`/agents/${selectedId}/history`)
      .then((r) => (r.ok ? r.json() : []))
      .then((h: Msg[]) => {
        if (live && h.length) setChats((c) => (c[selectedId] ? c : { ...c, [selectedId]: h }));
      })
      .catch(() => {});
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  // Once the handoff on screen has its answer, leave it up for a few seconds, then show the next one.
  const headId = queue[0]?.id;
  const headHasReply = !!queue[0]?.reply;
  useEffect(() => {
    if (!headId || !headHasReply) return;
    const t = setTimeout(() => setQueue((q) => q.slice(1)), 4500);
    return () => clearTimeout(t);
  }, [headId, headHasReply]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chats, selectedId, selectedLive?.state, tab]);

  function setAgentLive(id: string, patch: Partial<Live>) {
    setLive((l) => ({ ...l, [id]: { ...(l[id] ?? { state: "idle" as const, emotion: "neutral" }), ...patch } }));
  }

  async function send(override?: string) {
    const text = (override ?? input).trim();
    if (!text || !selected || busy) return;
    const id = selected.id;
    const who = selected.name;
    setInput("");
    setError("");
    setChats((c) => ({ ...c, [id]: [...(c[id] ?? []), { role: "user", text }] }));
    addLog("📨", `You asked ${who}: “${text.slice(0, 60)}”`);

    const res = await apiFetch(`/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: id, message: text }),
    });
    if (!res.ok || !res.body) {
      const body = await res.json().catch(() => ({}));
      setError(typeof body.detail === "string" ? body.detail : "Something went wrong. Try again.");
      return;
    }
    const reader = res.body.getReader();
    const turnSources: Source[] = [];
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        if (!part.startsWith("data: ")) continue;
        const ev = JSON.parse(part.slice(6));
        clearTimeout(timers.current[id]);
        if (ev.state === "idle") {
          // Let the agent finish its reaction before it returns to ambient work.
          timers.current[id] = setTimeout(() => setAgentLive(id, { state: "idle", tool: undefined, kind: undefined }), 3000);
          continue;
        }
        if (ev.state === "plan") {
          setBoard((b) => [...ev.tasks.map((t: BoardTask) => ({ ...t, status: "pending" })), ...b].slice(0, 12));
          addLog("🧭", `${who} planned ${ev.tasks.length} task${ev.tasks.length === 1 ? "" : "s"} (${ev.via === "llm" ? "with the planner" : `by ${ev.via}, no planner call`})`);
          continue;
        }
        if (ev.state === "delegate") {
          setQueue((q) => [...q, { id: ev.task_id, fromId: id, toId: ev.to, task: ev.task }]);
          setBoard((b) => b.map((t) => (t.id === ev.task_id ? { ...t, status: "running" } : t)));
          addLog("🤝", `${who} → ${nameOf(ev.to)}: ${ev.task}`);
          continue;
        }
        if (ev.state === "delegate_result") {
          setQueue((q) => q.map((x) => (x.id === ev.task_id ? { ...x, reply: ev.text } : x)));
          setBoard((b) => b.map((t) => (t.id === ev.task_id ? { ...t, status: ev.ok ? "done" : "failed", result: ev.text } : t)));
          addLog("💬", `${nameOf(ev.to)} replied: ${plainText(String(ev.text)).slice(0, 90)}`);
          continue;
        }
        setAgentLive(id, { state: ev.state, emotion: ev.emotion ?? "neutral", tool: ev.tool, kind: ev.kind });
        if (ev.state === "tool_call") addLog(ev.kind === "mcp" ? "🔌" : ev.kind === "knowledge" ? "📚" : "🛠️", `${who} ${ev.kind === "knowledge" ? (ev.tool === "search_knowledge" ? "searched the documents" : `used the database (${ev.tool})`) : `used ${ev.kind === "mcp" ? "MCP tool" : "tool"} ${ev.tool}`}`);
        if (ev.state === "sources") {
          turnSources.splice(0, turnSources.length, ...(ev.items as Source[]));
          addLog("📎", `${who} cited ${turnSources.length} passage${turnSources.length === 1 ? "" : "s"}`);
          continue;
        }
        if (ev.state === "speaking") {
          setChats((c) => ({ ...c, [id]: [...(c[id] ?? []), { role: "agent", text: ev.text, sources: turnSources.length ? turnSources : undefined }] }));
          addLog("🗣️", `${who} answered`);
        }
        if (ev.state === "error") setError(ev.message);
      }
    }
  }

  function flyTo(pos: CameraView["pos"], target: CameraView["target"]) {
    setFollowId(null);
    setView({ pos, target, nonce: viewNonce.current++ });
  }

  function zoomToDesk(id: string) {
    const a = agents.find((x) => x.id === id);
    if (!a) return;
    setSelectedId(id);
    if (a.role === "head") {
      const v = deskView(null);
      return flyTo(v.pos, v.target);
    }
    const slot = assignSlots(agents.filter((x) => x.role !== "head").map((x) => x.id))[id];
    const v = deskView(slot);
    flyTo(v.pos, v.target);
  }

  async function createAgent(form: NewAgentForm) {
    const res = await apiFetch(`/agents`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : "Couldn't create the agent");
    }
    const created: Agent = await res.json();
    setFreshIds((f) => [...f, created.id]);
    addLog("🆕", `${created.name} just joined the office`);
    const door = VIEWS.find((v) => v.id === "entrance");
    if (door) flyTo(door.pos, door.target);
    await loadAgents(created.id);
    setShowNew(false);
    setExpert(null);
  }

  async function saveLook(look: Look) {
    if (!selected?.custom) return;
    const res = await apiFetch(`/agents/${selected.id}/look`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(look) });
    if (!res.ok) throw new Error("Couldn't save the look");
    addLog("👔", `${selected.name} got a new look`);
    await loadAgents(selected.id);
    setShowLook(false);
  }

  function removeSelected() {
    if (!selected?.custom || leavingIds.includes(selected.id)) return;
    setLeavingIds((l) => [...l, selected.id]);
    addLog("🚪", `${selected.name} is heading out`);
    const door = VIEWS.find((v) => v.id === "entrance");
    if (door) flyTo(door.pos, door.target);
  }

  /** Called by the scene once a removed agent has walked out of the main door. */
  const finishRemoval = useCallback(
    async (id: string) => {
      if (deleting.current.has(id)) return;
      deleting.current.add(id);
      await apiFetch(`/agents/${id}`, { method: "DELETE" });
      setLeavingIds((l) => l.filter((x) => x !== id));
      deleting.current.delete(id);
      setSelectedId((cur) => (cur === id ? null : cur));
      addLog("👋", `${agents.find((a) => a.id === id)?.name ?? "Someone"} left the office`);
      await loadAgents(agents.find((a) => a.role === "head")?.id);
    },
    [agents, addLog, loadAgents],
  );

  const messages = selectedId ? chats[selectedId] ?? [] : [];

  return (
    <main className="mx-auto grid min-h-screen w-full max-w-[1500px] gap-4 p-4 lg:grid-cols-[1fr_400px]">
      <section className="flex flex-col gap-3">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h1 className="text-2xl font-bold">Deskmates Office</h1>
            <p className="text-sm text-black/60">Click anyone to chat. Ask the manager for a team task and watch the handoff.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => setShowKnowledge(true)} className="rounded-full bg-black/10 px-3 py-1.5 text-xs font-medium">
              📚 Knowledge
            </button>
            <button onClick={() => setShowSettings(true)} className="rounded-full bg-black/10 px-3 py-1.5 text-xs font-medium">
              ⚙ Settings
            </button>
            <button onClick={() => setShowNew(true)} className="rounded-full bg-black px-3 py-1.5 text-xs font-medium text-white">
              + New agent
            </button>
            <button onClick={() => setAmbient((a) => !a)} className={`rounded-full px-3 py-1.5 text-xs font-medium ${ambient ? "bg-black/80 text-white" : "bg-black/10"}`}>
              Office life: {ambient ? "ON" : "OFF"}
            </button>
            <Link href="/" className="rounded-full bg-black/10 px-3 py-1.5 text-xs font-medium">
              Single chat
            </Link>
            <SignOut />
          </div>
        </header>
        {needsKey && (
          <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm">
            <span>No API key is set up yet, so the agents can&apos;t answer. Add a free Gemini or Groq key to get started.</span>
            <button onClick={() => setShowSettings(true)} className="shrink-0 rounded-full bg-black px-3 py-1 text-xs font-medium text-white">
              Add a key
            </button>
          </div>
        )}
        {!needsKey && (quotaLevel === "low" || quotaLevel === "exhausted") && (
          <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm">
            <span>{quotaLevel === "exhausted" ? "Every AI model is out of free quota for now, so agents may not answer." : "Free AI quota is running low today."} Add a Groq, OpenRouter or Cerebras key from another account to keep going.</span>
            <button onClick={() => setShowSettings(true)} className="shrink-0 rounded-full bg-black px-3 py-1 text-xs font-medium text-white">
              Add a key
            </button>
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          {VIEWS.map((v) => (
            <button key={v.id} onClick={() => flyTo(v.pos, v.target)} className="rounded-full bg-black/5 px-2.5 py-1 text-[11px] font-medium hover:bg-black/10">
              {v.label}
            </button>
          ))}
        </div>
        <div className="h-[76vh] min-h-[440px] overflow-hidden rounded-2xl border border-black/10 shadow-sm">
          <StudioScene
            agents={agents}
            live={live}
            interaction={interaction}
            ambient={ambient}
            selectedId={selectedId}
            freshIds={freshIds}
            leavingIds={leavingIds}
            onAgentLeft={finishRemoval}
            onPresence={reportPresence}
            view={view}
            followId={followId}
            onSelect={setSelectedId}
            onFocusAgent={zoomToDesk}
          />
        </div>
      </section>

      <aside className="relative flex min-h-[460px] flex-col rounded-2xl border border-black/10 bg-white shadow-sm">
        <div className="flex flex-wrap items-center gap-2 border-b border-black/10 p-3">
          <div className="w-full">
            <AgentSwitcher
              agents={agents}
              selectedId={selectedId}
              busy={Object.fromEntries(Object.entries(live).map(([id, l]) => [id, l.state !== "idle"]))}
              onSelect={(id) => {
                setSelectedId(id);
                setTab("chat");
              }}
            />
          </div>
          {selected?.grounded && <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800">🎓 Knowledge expert</span>}
          <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-medium uppercase">{selectedLive?.state ?? "idle"}</span>
          {selected && (
            <>
              <button onClick={() => zoomToDesk(selected.id)} className="rounded-full bg-black/10 px-2 py-0.5 text-[10px] font-medium">
                Zoom to desk
              </button>
              <button
                onClick={() => setFollowId((f) => (f === selected.id ? null : selected.id))}
                className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${followId === selected.id ? "bg-black text-white" : "bg-black/10"}`}
              >
                Follow
              </button>
            </>
          )}
          {selected?.custom && (
            <button onClick={() => setShowLook(true)} className="rounded-full bg-black/10 px-2 py-0.5 text-[10px] font-medium">
              Edit look
            </button>
          )}
          {selected?.custom && (
            <button onClick={removeSelected} className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-700">
              Remove
            </button>
          )}
        </div>
        <div className="flex gap-1 border-b border-black/10 px-3 pt-2 text-xs">
          {(["chat", "team", "tasks", "activity"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`rounded-t-md px-3 py-1.5 font-medium capitalize ${tab === t ? "bg-black/5" : "text-black/50"}`}>
              {t}
              {t === "tasks" && board.length > 0 && <span className="ml-1 text-black/40">({board.length})</span>}
              {t === "activity" && log.length > 0 && <span className="ml-1 text-black/40">({log.length})</span>}
            </button>
          ))}
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto p-3" style={{ maxHeight: "56vh" }}>
          {tab === "chat" ? (
            <>
              {messages.length === 0 && <p className="text-sm text-black/50">Say hello to {selected?.name ?? "someone"}…</p>}
              {messages.map((m, i) => (
                <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className="max-w-[85%]">
                    <div className={`rounded-2xl px-3 py-2 text-sm ${m.role === "user" ? "whitespace-pre-wrap bg-black text-white" : "bg-black/5"}`}>{m.role === "agent" ? <Markdown text={m.text} /> : m.text}</div>
                    {m.role === "agent" && selected && <MessageExtras msg={m} question={[...messages.slice(0, i)].reverse().find((x) => x.role === "user")?.text ?? ""} agentId={selected.id} />}
                  </div>
                </div>
              ))}
              {error && <p className="text-xs text-red-600">{error}</p>}
            </>
          ) : tab === "team" ? (
            <TeamPanel
              agents={agents}
              onChat={(id) => {
                setSelectedId(id);
                setTab("chat");
              }}
            />
          ) : tab === "tasks" ? (
            <TaskBoard board={board} nameOf={nameOf} />
          ) : log.length === 0 ? (
            <p className="text-sm text-black/50">Handoffs, MCP tool calls and new hires show up here.</p>
          ) : (
            log.map((l) => (
              <div key={l.id} className="flex gap-2 text-sm">
                <span>{l.icon}</span>
                <span className="text-black/80">{l.text}</span>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        {tab === "chat" && selected && (
          <div className="flex flex-wrap gap-1.5 border-t border-black/10 px-3 pt-2">
            {(selected.role === "head" ? HEAD_TASKS : selected.starters?.length ? selected.starters : DEFAULT_TASKS).map((t) => (
              <button
                key={t}
                disabled={busy}
                onClick={() => send(t)}
                className="max-w-full truncate rounded-full bg-black/5 px-2.5 py-1 text-[11px] hover:bg-black/10 disabled:opacity-40"
                title={t}
              >
                {t}
              </button>
            ))}
          </div>
        )}
        <form
          className="flex gap-2 border-t border-black/10 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <input
            className="flex-1 rounded-xl border border-black/15 px-3 py-2 text-sm outline-none focus:border-black"
            placeholder={selected ? `Message ${selected.name}…` : "Pick someone"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <button className="rounded-xl bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40" disabled={busy || !input.trim()}>
            Send
          </button>
        </form>
      </aside>

      {showKnowledge && (
        <KnowledgeModal
          people={agents.filter((a) => a.role !== "head")}
          onClose={() => setShowKnowledge(false)}
          onCreateExpert={(docs, sources) => {
            setShowKnowledge(false);
            setExpert({ docs, sources });
          }}
        />
      )}
      {expert && <ExpertModal initialDocs={expert.docs} initialSources={expert.sources} onClose={() => setExpert(null)} onCreate={createAgent} />}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} onKeysChanged={checkKeys} />}
      {showNew && (
        <NewAgentModal
          onClose={() => setShowNew(false)}
          onCreate={createAgent}
          onExpert={() => {
            setShowNew(false);
            setExpert({ docs: [], sources: [] });
          }}
        />
      )}
      {showLook && selected?.custom && <EditLookModal agent={selected} onClose={() => setShowLook(false)} onSave={saveLook} />}
    </main>
  );
}

type Preset = { id: string; name: string; archetype: string; color: string; personality: string; tools?: string[] };
type NewAgentForm = { name: string; archetype: string; personality: string; color: string; look?: Look; tools?: string[]; knowledge?: ExpertForm["knowledge"] };

function NewAgentModal({ onClose, onCreate, onExpert }: { onClose: () => void; onCreate: (f: NewAgentForm) => Promise<void>; onExpert: () => void }) {
  const [mode, setMode] = useState<"gallery" | "custom">("gallery");
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [name, setName] = useState("");
  const [archetype, setArchetype] = useState("");
  const [personality, setPersonality] = useState("");
  const [color, setColor] = useState(PALETTE[0]);
  const [look, setLook] = useState<Look>(DEFAULT_LOOK);
  const [web, setWeb] = useState(false);
  const [dbAccess, setDbAccess] = useState(false);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const ok = name.trim() && archetype.trim() && personality.trim();

  useEffect(() => {
    apiFetch(`/presets`)
      .then((r) => r.json())
      .then(setPresets)
      .catch(() => setPresets([]));
  }, []);

  async function create(f: NewAgentForm) {
    if (saving) return;
    setSaving(true);
    setErr("");
    try {
      await onCreate(f);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Something went wrong");
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-2xl bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-bold">Hire a new agent</h2>
            <button onClick={onExpert} className="rounded-full bg-emerald-600 px-3 py-1 text-xs font-medium text-white">
              🎓 From my knowledge
            </button>
          </div>
          <div className="flex gap-1 rounded-full bg-black/5 p-1 text-xs font-medium">
            {(["gallery", "custom"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)} className={`rounded-full px-3 py-1 ${mode === m ? "bg-black text-white" : ""}`}>
                {m === "gallery" ? "Pick from the team" : "Create your own"}
              </button>
            ))}
          </div>
        </div>

        {mode === "gallery" ? (
          <div className="grid flex-1 gap-2 overflow-y-auto sm:grid-cols-2">
            {presets === null && <p className="text-sm text-black/50">Loading…</p>}
            {presets?.length === 0 && <p className="text-sm text-black/50">Everyone available is already in the office. Create your own instead.</p>}
            {presets?.map((p) => (
              <div key={p.id} className="flex items-start gap-3 rounded-xl border border-black/10 p-3">
                <span className="mt-1 h-3 w-3 shrink-0 rounded-full" style={{ background: p.color }} />
                <div className="min-w-0 flex-1">
                  <p className="font-semibold leading-tight">{p.name}</p>
                  <p className="text-xs text-black/60">{p.archetype}</p>
                </div>
                <button
                  disabled={saving}
                  onClick={() => create({ name: p.name, archetype: p.archetype, personality: p.personality, color: p.color, tools: p.tools })}
                  className="rounded-full bg-black px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
                >
                  Hire
                </button>
              </div>
            ))}
          </div>
        ) : (
          <form
            className="flex-1 space-y-3 overflow-y-auto"
            onSubmit={(e) => {
              e.preventDefault();
              if (ok) create({ name, archetype, personality, color, look, tools: [...(web ? ["fetch"] : []), ...(dbAccess ? ["database"] : [])] });
            }}
          >
            <label className="block text-sm">
              <span className="font-medium">Name</span>
              <input maxLength={40} value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm outline-none focus:border-black" placeholder="e.g. Jordan" />
            </label>
            <label className="block text-sm">
              <span className="font-medium">Role</span>
              <input maxLength={80} value={archetype} onChange={(e) => setArchetype(e.target.value)} className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm outline-none focus:border-black" placeholder="e.g. Meeting notes taker" />
            </label>
            <label className="block text-sm">
              <span className="font-medium">Personality and instructions</span>
              <textarea maxLength={1200} rows={4} value={personality} onChange={(e) => setPersonality(e.target.value)} className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm outline-none focus:border-black" placeholder="How do they talk? What are they good at?" />
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={web} onChange={(e) => setWeb(e.target.checked)} className="mt-1" />
              <span>
                <span className="font-medium">Can browse the web</span>
                <span className="block text-xs text-black/60">Lets this agent open and read web pages. Page content is treated as untrusted data.</span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={dbAccess} onChange={(e) => setDbAccess(e.target.checked)} className="mt-1" />
              <span>
                <span className="font-medium">Can query connected databases</span>
                <span className="block text-xs text-black/60">Read-only SQL on databases you connect in Knowledge. Documents you upload are searchable by every agent.</span>
              </span>
            </label>
            <div className="text-sm">
              <span className="font-medium">Robot body color</span>
              <div className="mt-1 flex flex-wrap gap-2">
                {PALETTE.map((c) => (
                  <button type="button" key={c} onClick={() => setColor(c)} className={`h-7 w-7 rounded-full border-2 ${color === c ? "border-black" : "border-transparent"}`} style={{ background: c }} aria-label={c} />
                ))}
              </div>
            </div>
            <div className="text-sm">
              <span className="font-medium">Outfit and look</span>
              <div className="mt-2">
                <LookEditor look={look} color={color} onChange={setLook} />
              </div>
            </div>
            <div className="flex justify-end pt-1">
              <button disabled={!ok || saving} className="rounded-xl bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
                {saving ? "Hiring…" : "Hire"}
              </button>
            </div>
          </form>
        )}
        {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
        <div className="mt-3 flex justify-end">
          <button onClick={onClose} className="rounded-xl bg-black/10 px-4 py-2 text-sm font-medium">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function EditLookModal({ agent, onClose, onSave }: { agent: AgentInfo; onClose: () => void; onSave: (l: Look) => Promise<void> }) {
  const [look, setLook] = useState<Look>(agent.look ?? DEFAULT_LOOK);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  async function save() {
    setSaving(true);
    setErr("");
    try {
      await onSave(look);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Something went wrong");
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-y-auto rounded-2xl bg-white p-5 shadow-xl">
        <h2 className="mb-3 text-lg font-bold">Dress {agent.name}</h2>
        <LookEditor look={look} color={agent.color} onChange={setLook} />
        {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-xl bg-black/10 px-4 py-2 text-sm font-medium">
            Cancel
          </button>
          <button onClick={save} disabled={saving} className="rounded-xl bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
            {saving ? "Saving…" : "Save look"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TaskBoard({ board, nameOf }: { board: BoardTask[]; nameOf: (id: string) => string }) {
  if (board.length === 0) return <p className="text-sm text-black/50">When the manager hands out work, the tasks show up here as they run.</p>;
  const chip: Record<string, string> = { pending: "bg-black/5 text-black/60", running: "bg-amber-100 text-amber-800", done: "bg-green-100 text-green-800", failed: "bg-red-100 text-red-800" };
  return (
    <div className="space-y-2">
      {board.map((t) => (
        <div key={t.id} className="rounded-xl border border-black/10 p-2.5 text-sm">
          <div className="flex items-center gap-2">
            <span className="font-medium">{nameOf(t.agent)}</span>
            <span className={`ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ${chip[t.status] ?? chip.pending}`}>{t.status}</span>
          </div>
          <p className="mt-1 text-xs text-black/70">{t.task}</p>
          {t.result && <p className="mt-1.5 rounded-lg bg-black/5 px-2 py-1.5 text-xs">{plainText(t.result).slice(0, 260)}</p>}
        </div>
      ))}
    </div>
  );
}

type TeamEntry = {
  id: string;
  name: string;
  role: string;
  state: "working" | "idle";
  doing: string | null;
  progress: string | null;
  steps: string[];
  last_result: string | null;
  zone: string | null;
  activity: string | null;
  at_desk: boolean | null;
};

/** The same live status the manager reads: who is working on what, how far along, and where everyone is. */
function TeamPanel({ agents, onChat }: { agents: Agent[]; onChat: (id: string) => void }) {
  const [snap, setSnap] = useState<{ working: number; total: number; team: TeamEntry[] } | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      apiFetch("/team/status")
        .then((r) => r.json())
        .then((d) => alive && setSnap(d))
        .catch(() => {});
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  if (!snap) return <p className="text-sm text-black/50">Loading…</p>;
  const sorted = [...snap.team].sort((a, b) => (a.state === b.state ? 0 : a.state === "working" ? -1 : 1));
  return (
    <div className="space-y-2">
      <p className="text-xs text-black/60">
        <b>{snap.working}</b> of {snap.total} working on a real task. Everyone else is idle, even if you see them walking around.
      </p>
      {sorted.map((e) => (
        <div key={e.id} className="rounded-xl border border-black/10 p-2.5 text-sm">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: agents.find((a) => a.id === e.id)?.color ?? "#ccc" }} />
            <span className="font-medium">{e.name}</span>
            <span className="truncate text-xs text-black/50">{e.role === "head" ? "Head" : e.role}</span>
            <span className={`ml-auto shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ${e.state === "working" ? "bg-amber-100 text-amber-800" : "bg-black/5 text-black/60"}`}>{e.state}</span>
            <button onClick={() => onChat(e.id)} className="shrink-0 rounded-full bg-black px-2 py-0.5 text-[10px] font-medium text-white">
              Chat
            </button>
          </div>
          {e.doing && (
            <p className="mt-1 text-xs text-black/80">
              {e.doing} {e.progress && <span className="text-black/50">· {e.progress}</span>}
            </p>
          )}
          {e.steps.length > 0 && <p className="text-xs text-black/50">So far: {e.steps.join(", ")}</p>}
          {e.last_result && <p className="mt-1 rounded-lg bg-black/5 px-2 py-1 text-xs">{plainText(e.last_result)}</p>}
          <p className="mt-1 text-[11px] text-black/50">
            📍 {e.zone ? `${e.zone}${e.at_desk === null ? "" : e.at_desk ? " · at their desk" : ""}${e.activity ? ` · ${e.activity}` : ""}` : "location unknown (open the office view)"}
          </p>
        </div>
      ))}
    </div>
  );
}
