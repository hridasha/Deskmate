"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";

type Modes = Record<string, Record<string, number>>;
type RetrievalReport = {
  embedding_model: string;
  docs: number;
  answerable: number;
  unanswerable: number;
  passages: number;
  modes: Modes;
  best_cutoff: number;
  cutoff_sweep: { cutoff: number; answerable_kept: number; unanswerable_wrongly_kept: number; balanced: number }[];
  chunking?: { method: string; passages: number; "hit@600": number; "hit@1500": number }[];
};
type RetrievalResponse = { report: RetrievalReport | null; running: boolean; error: string; corpus: boolean; cutoff_in_use: number; reranker: string | null };
type Feedback = { up: number; down: number; by_agent: { agent: string; up: number; down: number }[]; needs_attention: { id: string; agent: string; message: string; reply: string; comment: string; promoted: boolean }[] };

const pct = (v: number | undefined) => (v === undefined ? "" : `${Math.round(v * 100)}%`);

/** How well search finds the right passage (measured on a bundled benchmark, with the local embedding model: no API quota). */
export function RetrievalCard() {
  const [d, setD] = useState<RetrievalResponse | null>(null);
  const [err, setErr] = useState("");
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(() => apiFetch("/evals/retrieval").then((r) => r.json()).then(setD).catch(() => {}), []);
  useEffect(() => {
    load();
    return () => {
      if (poll.current) clearInterval(poll.current);
    };
  }, [load]);

  async function run() {
    setErr("");
    const r = await apiFetch("/evals/retrieval/run", { method: "POST" });
    if (!r.ok) return setErr((await r.json().catch(() => ({}))).detail ?? "Couldn't start");
    poll.current = setInterval(async () => {
      const s = await (await apiFetch("/evals/retrieval")).json();
      setD(s);
      if (!s.running && poll.current) clearInterval(poll.current);
    }, 2000);
    load();
  }

  const rep = d?.report;
  return (
    <div className="space-y-2 rounded-xl border border-black/10 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Search quality</h3>
        <button onClick={run} disabled={d?.running || !d?.corpus} className="ml-auto rounded-full bg-black/10 px-3 py-1 text-xs font-medium disabled:opacity-40">
          {d?.running ? "Measuring…" : "Measure now (free, about a minute)"}
        </button>
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
      {d?.error && <p className="text-xs text-red-600">{d.error}</p>}
      {!rep ? (
        <p className="text-xs text-black/50">Not measured yet.</p>
      ) : (
        <>
          <p className="text-xs text-black/60">
            {rep.answerable} questions over {rep.passages} passages from {rep.docs} documents, with {rep.embedding_model}. “Top-1” means the right passage came first; “within 600 characters” means the answer was inside the text an agent actually sees.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-black/50">
                <tr>
                  <th className="pr-3 font-medium">Search</th>
                  <th className="pr-3 font-medium">Top-1</th>
                  <th className="pr-3 font-medium">Top-3</th>
                  <th className="pr-3 font-medium">Top-5</th>
                  <th className="pr-3 font-medium">Within 600 chars</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(rep.modes).map(([m, v]) => (
                  <tr key={m} className={m === "hybrid+rerank" ? "font-semibold" : ""}>
                    <td className="pr-3">{m}</td>
                    <td className="pr-3">{pct(v["recall@1"])}</td>
                    <td className="pr-3">{pct(v["recall@3"])}</td>
                    <td className="pr-3">{pct(v["recall@5"])}</td>
                    <td className="pr-3">{pct(v["hit@600"])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-black/60">
            Relevance cutoff in use: <b>{d?.cutoff_in_use}</b>. Best on this benchmark: <b>{rep.best_cutoff}</b> (keeps {pct(rep.cutoff_sweep.find((c) => c.cutoff === rep.best_cutoff)?.answerable_kept)} of answerable questions, wrongly answers {pct(rep.cutoff_sweep.find((c) => c.cutoff === rep.best_cutoff)?.unanswerable_wrongly_kept)} of unanswerable ones). Reranker: {d?.reranker ? "on" : "off"}.
          </p>
          {rep.chunking && (
            <details className="text-xs">
              <summary className="cursor-pointer text-black/60">How each chunking method did</summary>
              <table className="mt-1 w-full text-left">
                <tbody>
                  {rep.chunking.map((c) => (
                    <tr key={c.method}>
                      <td className="pr-3">{c.method}</td>
                      <td className="pr-3">{c.passages} passages</td>
                      <td className="pr-3">{pct(c["hit@600"])} / {pct(c["hit@1500"])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}
    </div>
  );
}

/** Thumbs from chat, and a button to turn a bad answer into a permanent test. */
export function FeedbackCard() {
  const [f, setF] = useState<Feedback | null>(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => apiFetch("/feedback/summary").then((r) => r.json()).then(setF).catch(() => {}), []);
  useEffect(() => {
    load();
  }, [load]);

  async function promote(id: string) {
    const r = await apiFetch(`/feedback/promote/${id}`, { method: "POST" });
    setMsg(r.ok ? "Added. The next evaluation will ask this question again and check the answer." : (await r.json().catch(() => ({}))).detail ?? "Couldn't add it");
    load();
  }

  if (!f) return null;
  return (
    <div className="space-y-2 rounded-xl border border-black/10 p-3">
      <div className="flex items-center gap-3">
        <h3 className="text-sm font-semibold">Feedback from chat</h3>
        <span className="text-xs">
          👍 {f.up} · 👎 {f.down}
        </span>
      </div>
      {f.by_agent.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {f.by_agent.map((a) => (
            <span key={a.agent} className="rounded-full bg-black/5 px-2 py-0.5 text-[11px]">
              {a.agent} 👍{a.up} 👎{a.down}
            </span>
          ))}
        </div>
      )}
      {msg && <p className="text-xs text-green-700">{msg}</p>}
      {f.needs_attention.length === 0 ? (
        <p className="text-xs text-black/50">{f.up + f.down === 0 ? "Use 👍 / 👎 under an answer in chat to start collecting feedback." : "No thumbs-down answers."}</p>
      ) : (
        f.needs_attention.map((x) => (
          <div key={x.id} className="space-y-0.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs">
            <p>
              <b>{x.agent}</b> was asked: “{x.message}”
            </p>
            <p className="text-black/60">Answered: {x.reply}</p>
            {x.comment && <p>Should have: {x.comment}</p>}
            <button disabled={x.promoted} onClick={() => promote(x.id)} className="mt-1 rounded-full bg-black px-2 py-0.5 font-medium text-white disabled:opacity-40">
              {x.promoted ? "In the test suite" : "Add to tests"}
            </button>
          </div>
        ))
      )}
    </div>
  );
}
