"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import AgentRoster from "@/components/AgentRoster";
import Markdown from "@/components/Markdown";
import MessageExtras, { type Msg, type Source } from "@/components/MessageExtras";
import type { Look } from "@/components/studio/looks";
import { apiFetch } from "@/lib/api";

const CharacterStage = dynamic(() => import("@/components/CharacterStage"), {
  ssr: false,
  loading: () => <div className="h-72 w-full animate-pulse rounded-xl bg-black/10" />,
});

type Character = { id: string; name: string; archetype: string; color: string; role?: string; look?: Look; starters?: string[]; grounded?: boolean };
type AgentState = "idle" | "thinking" | "tool_call" | "speaking" | "error";

const HEAD_STARTERS = ["Who is busy right now, what are they doing, and where is everyone?", "Ask Oscar to explain compound interest in one sentence, then summarize it for me."];
const DEFAULT_STARTERS = ["Introduce yourself in two sentences.", "Give me a 3-step plan for my day.", "What time is it in Tokyo?"];

/** Chat one-to-one with anyone in the office. */
export default function HomeClient() {
  const [characters, setCharacters] = useState<Character[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [chats, setChats] = useState<Record<string, Msg[]>>({});
  const [input, setInput] = useState("");
  const [state, setState] = useState<AgentState>("idle");
  const [stageState, setStageState] = useState<AgentState>("idle");
  const [emotion, setEmotion] = useState("neutral");
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const selected = characters.find((c) => c.id === selectedId) ?? null;
  const messages = selectedId ? chats[selectedId] ?? [] : [];
  const busy = state !== "idle";

  const load = useCallback(() => {
    apiFetch("/characters")
      .then((r) => r.json())
      .then((c: Character[]) => {
        setCharacters(c);
        setSelectedId((cur) => cur ?? c[0]?.id ?? null);
      })
      .catch(() => setError("Can't reach the backend. Is it running?"));
  }, []);

  useEffect(load, [load]);

  // The conversation is saved on the server, so reopen it when someone is picked.
  useEffect(() => {
    if (!selectedId || chats[selectedId]) return;
    let alive = true;
    apiFetch(`/agents/${selectedId}/history`)
      .then((r) => (r.ok ? r.json() : []))
      .then((h: Msg[]) => alive && h.length && setChats((c) => (c[selectedId] ? c : { ...c, [selectedId]: h })))
      .catch(() => {});
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, state, selectedId]);

  async function send(override?: string) {
    const text = (override ?? input).trim();
    if (!text || !selected || busy) return;
    const id = selected.id;
    setInput("");
    setError("");
    setBusyId(id);
    setChats((c) => ({ ...c, [id]: [...(c[id] ?? []), { role: "user", text }] }));

    const res = await apiFetch("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ character_id: id, message: text }) });
    if (!res.ok || !res.body) {
      const body = await res.json().catch(() => ({}));
      setError(typeof body.detail === "string" ? body.detail : "Something went wrong. Try again.");
      setBusyId(null);
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
        if (ev.state === "plan" || ev.state === "delegate" || ev.state === "delegate_result") continue; // visible in the studio
        if (ev.state === "sources") {
          turnSources.splice(0, turnSources.length, ...(ev.items as Source[]));
          continue;
        }
        setState(ev.state);
        // Let the character finish its reaction before returning to idle.
        if (holdTimer.current) clearTimeout(holdTimer.current);
        if (ev.state === "idle") {
          holdTimer.current = setTimeout(() => setStageState("idle"), 3000);
          setBusyId(null);
        } else setStageState(ev.state);
        if (ev.state === "speaking") {
          setEmotion(ev.emotion ?? "neutral");
          setChats((c) => ({ ...c, [id]: [...(c[id] ?? []), { role: "agent", text: ev.text, sources: turnSources.length ? turnSources : undefined }] }));
        }
        if (ev.state === "error") setError(ev.message);
      }
    }
  }

  const starters = selected ? (selected.role === "head" ? HEAD_STARTERS : selected.starters?.length ? selected.starters : DEFAULT_STARTERS) : [];

  return (
    <main className="mx-auto grid min-h-screen w-full max-w-6xl gap-4 p-4 md:grid-cols-[260px_300px_1fr] md:p-8">
      <aside className="flex max-h-[80vh] flex-col gap-2 rounded-2xl border border-black/10 bg-white p-3 shadow-sm">
        <h2 className="px-1 text-sm font-semibold">Talk to anyone</h2>
        <AgentRoster agents={characters} selectedId={selectedId} onSelect={(id) => !busy && setSelectedId(id)} busy={busyId ? { [busyId]: true } : {}} />
      </aside>

      <aside className="flex flex-col items-center gap-4 rounded-2xl border border-black/10 bg-white p-5 shadow-sm">
        {selected && <CharacterStage id={selected.id} state={stageState} emotion={emotion} color={selected.color} look={selected.look} />}
        <div className="text-center">
          <h1 className="text-xl font-bold">{selected?.name ?? "Loading…"}</h1>
          <p className="text-sm text-black/60">{selected?.role === "head" ? "Head of the office" : selected?.archetype}</p>
          {selected?.grounded && <span className="mt-1 inline-block rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800">🎓 Knowledge expert</span>}
        </div>
        <span className="rounded-full bg-black/5 px-3 py-1 text-xs font-medium uppercase tracking-wide">{state === "tool_call" ? "working" : state}</span>
        <Link href="/studio" className="mt-auto rounded-full bg-black px-4 py-2 text-xs font-medium text-white">
          Open the Studio →
        </Link>
      </aside>

      <section className="flex flex-col rounded-2xl border border-black/10 bg-white shadow-sm">
        <div className="flex-1 space-y-3 overflow-y-auto p-4" style={{ maxHeight: "68vh" }}>
          {messages.length === 0 && <p className="text-sm text-black/50">Say hello to {selected?.name ?? "your agent"}…</p>}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className="max-w-[80%]">
                <div className={`rounded-2xl px-4 py-2 text-sm ${m.role === "user" ? "whitespace-pre-wrap bg-black text-white" : "bg-black/5"}`}>{m.role === "agent" ? <Markdown text={m.text} /> : m.text}</div>
                {m.role === "agent" && selected && <MessageExtras msg={m} question={[...messages.slice(0, i)].reverse().find((x) => x.role === "user")?.text ?? ""} agentId={selected.id} />}
              </div>
            </div>
          ))}
          {(state === "thinking" || state === "tool_call") && busyId === selectedId && <p className="text-sm italic text-black/50">{selected?.name} is thinking…</p>}
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div ref={bottomRef} />
        </div>
        {starters.length > 0 && (
          <div className="flex flex-wrap gap-1.5 border-t border-black/10 px-3 pt-2">
            {starters.map((t) => (
              <button key={t} disabled={busy} onClick={() => send(t)} className="max-w-full truncate rounded-full bg-black/5 px-2.5 py-1 text-[11px] hover:bg-black/10 disabled:opacity-40" title={t}>
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
            placeholder={selected ? `Message ${selected.name}…` : "Pick someone to talk to"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <button className="rounded-xl bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40" disabled={busy || !input.trim()}>
            Send
          </button>
        </form>
      </section>
    </main>
  );
}
