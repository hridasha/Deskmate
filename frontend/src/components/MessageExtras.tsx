"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";

export type Source = { n: number; doc_id: string; doc: string; locator: string; text: string };
export type Msg = { role: "user" | "agent"; text: string; sources?: Source[] };

/** Opens the original uploaded file in a new tab (it needs the sign-in headers, so it is fetched as a blob). */
async function openSource(s: Source) {
  const r = await apiFetch(`/knowledge/${s.doc_id}/file`);
  if (!r.ok) return alert("The original file isn't available any more.");
  const blob = await r.blob();
  const page = /^p\.(\d+)/.exec(s.locator)?.[1];
  window.open(`${URL.createObjectURL(blob)}${page && blob.type === "application/pdf" ? `#page=${page}` : ""}`, "_blank", "noopener");
}

/** Under an agent's answer: the passages it was based on, and thumbs up/down. */
export default function MessageExtras({ msg, question, agentId }: { msg: Msg; question: string; agentId: string }) {
  const [open, setOpen] = useState<number | null>(null);
  const [rating, setRating] = useState<0 | 1 | -1>(0);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");

  async function send(value: 1 | -1, comment = "") {
    setRating(value);
    setAsking(false);
    await apiFetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_id: agentId, message: question, reply: msg.text, rating: value, comment, sources: (msg.sources ?? []).map(({ n, doc, locator }) => ({ n, doc, locator })) }),
    }).catch(() => {});
  }

  return (
    <div className="mt-1 space-y-1 text-[11px] text-black/60">
      {msg.sources && msg.sources.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span>Sources:</span>
          {msg.sources.map((s) => (
            <button key={s.n} onClick={() => setOpen(open === s.n ? null : s.n)} className={`rounded-full px-2 py-0.5 ${open === s.n ? "bg-black text-white" : "bg-black/10 hover:bg-black/20"}`}>
              [{s.n}] {s.doc} · {s.locator}
            </button>
          ))}
        </div>
      )}
      {msg.sources?.filter((s) => s.n === open).map((s) => (
        <div key={s.n} className="rounded-lg border border-black/10 bg-white p-2 text-xs text-black/80">
          <p className="whitespace-pre-wrap">{s.text}</p>
          <button onClick={() => openSource(s)} className="mt-1 underline">
            Open {s.doc}
          </button>
        </div>
      ))}
      <div className="flex items-center gap-1">
        <button aria-label="Good answer" disabled={rating !== 0} onClick={() => send(1)} className={`rounded px-1 hover:bg-black/10 ${rating === 1 ? "bg-green-100" : ""} disabled:opacity-60`}>
          👍
        </button>
        <button aria-label="Bad answer" disabled={rating !== 0} onClick={() => setAsking(true)} className={`rounded px-1 hover:bg-black/10 ${rating === -1 ? "bg-red-100" : ""} disabled:opacity-60`}>
          👎
        </button>
        {rating !== 0 && <span>Thanks, noted.</span>}
      </div>
      {asking && (
        <div className="flex gap-1">
          <input autoFocus value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="What should it have said? (optional)" className="min-w-0 flex-1 rounded border border-black/15 px-2 py-1 text-xs" />
          <button onClick={() => send(-1, note)} className="rounded-full bg-black px-2 py-1 text-white">
            Send
          </button>
        </div>
      )}
    </div>
  );
}
