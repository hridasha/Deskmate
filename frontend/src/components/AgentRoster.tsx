"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export type RosterAgent = { id: string; name: string; color: string; role?: string; archetype?: string; grounded?: boolean };

function subtitle(a: RosterAgent) {
  return a.role === "head" ? "Head of the office" : a.archetype ?? "";
}

/** Every agent, searchable, with who is busy and who is a knowledge expert. Used inline and in the switcher. */
export default function AgentRoster({
  agents,
  selectedId,
  onSelect,
  busy = {},
}: {
  agents: RosterAgent[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  busy?: Record<string, boolean>;
}) {
  const [q, setQ] = useState("");
  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle ? agents.filter((a) => `${a.name} ${a.archetype ?? ""} ${a.role ?? ""}`.toLowerCase().includes(needle)) : agents;
  }, [agents, q]);
  return (
    <div className="flex min-h-0 flex-col gap-2">
      {agents.length > 6 && (
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find someone…" className="rounded-lg border border-black/15 px-3 py-1.5 text-sm outline-none focus:border-black" />
      )}
      <div className="min-h-0 space-y-1 overflow-y-auto">
        {list.length === 0 && <p className="px-2 py-1 text-xs text-black/50">Nobody matches.</p>}
        {list.map((a) => (
          <button
            key={a.id}
            onClick={() => onSelect(a.id)}
            className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-black/5 ${a.id === selectedId ? "bg-black/10" : ""}`}
          >
            <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: a.color }} />
            <span className="min-w-0 flex-1">
              <span className={`block truncate leading-tight ${a.id === selectedId ? "font-semibold" : "font-medium"}`}>{a.name}</span>
              <span className="block truncate text-[11px] text-black/50">{subtitle(a)}</span>
            </span>
            {a.grounded && <span title="Knowledge expert" className="shrink-0 text-xs">🎓</span>}
            {busy[a.id] && <span className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase text-amber-800">working</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

/** A compact "who am I talking to" button that opens the full roster. */
export function AgentSwitcher({ agents, selectedId, onSelect, busy }: { agents: RosterAgent[]; selectedId: string | null; onSelect: (id: string) => void; busy?: Record<string, boolean> }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const selected = agents.find((a) => a.id === selectedId);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div ref={box} className="relative">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 rounded-xl border border-black/10 px-3 py-2 text-left hover:bg-black/5" aria-haspopup="listbox" aria-expanded={open}>
        <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: selected?.color ?? "#ccc" }} />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-semibold leading-tight">{selected?.name ?? "Choose who to talk to"}</span>
          <span className="block truncate text-xs text-black/50">{selected ? subtitle(selected) : `${agents.length} people in the office`}</span>
        </span>
        <span className="shrink-0 text-xs text-black/50">Switch ▾</span>
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full z-40 mt-1 max-h-[60vh] overflow-hidden rounded-xl border border-black/10 bg-white p-2 shadow-xl">
          <AgentRoster
            agents={agents}
            selectedId={selectedId}
            busy={busy}
            onSelect={(id) => {
              onSelect(id);
              setOpen(false);
            }}
          />
        </div>
      )}
    </div>
  );
}
