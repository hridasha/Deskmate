"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";

type TuneRow = { strategy: string; size: number; chunks: number; "hit@600": number; "hit@1500": number; score: number };
type ChunkInfo = {
  strategy: string;
  requested: string;
  size: number;
  overlap: number;
  reason: string;
  tuning?: { probes: number; tested?: number; results?: TuneRow[]; note?: string };
};
type Doc = { id: string; name: string; kind: string; size: number; chunks: number; embedded: number; created: number; scope: string[] | null; chunking?: ChunkInfo | null; origin?: { type: string; url?: string; path?: string }; synced?: number | null };
type Strategy = { id: string; description: string };
type Piece = { ord: number; locator: string; text: string };

const LABEL: Record<string, string> = {
  auto: "Auto (recommended)",
  tuned: "Tune on my data",
  fixed: "Fixed size",
  sentence: "Whole sentences",
  paragraph: "Paragraphs",
  structure: "By headings",
  page: "By page / rows",
  semantic: "By topic change",
};
type Source = { id: string; name: string; kind: string; schema: string; scope: string[] | null };
type Hit = { doc: string; locator: string; text: string; score: number; cosine: number; bm25: number };
type Person = { id: string; name: string; role?: string };
type Upload = { name: string; status: "uploading" | "done" | "error"; message?: string };

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.docx,.xlsx,.xlsm,.csv,.tsv,.txt,.md,.json,.html,.htm,.log,.yaml,.yml,.xml,.py,.js,.ts,.sql";

const ICON: Record<string, string> = { png: "🖼️", jpg: "🖼️", jpeg: "🖼️", webp: "🖼️", gif: "🖼️", bmp: "🖼️", pdf: "📕", docx: "📘", xlsx: "📗", xlsm: "📗", csv: "📊", tsv: "📊", html: "🌐", htm: "🌐" };

function fmtSize(n: number) {
  return n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

function scopeLabel(scope: string[] | null, people: Person[]) {
  if (!scope) return "All agents";
  return scope.map((id) => people.find((p) => p.id === id)?.name.split(" ")[0] ?? id).join(", ");
}

/** Checklist for choosing which agents may use a document or database. */
function ScopePicker({ people, value, onSave }: { people: Person[]; value: string[] | null; onSave: (v: string[] | null) => void }) {
  const [all, setAll] = useState(value === null);
  const [picked, setPicked] = useState<string[]>(value ?? []);
  return (
    <div className="mt-2 rounded-lg bg-black/5 p-2 text-xs">
      <label className="flex items-center gap-1.5 font-medium">
        <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> All agents
      </label>
      {!all && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
          {people.map((p) => (
            <label key={p.id} className="flex items-center gap-1">
              <input type="checkbox" checked={picked.includes(p.id)} onChange={(e) => setPicked((s) => (e.target.checked ? [...s, p.id] : s.filter((x) => x !== p.id)))} />
              {p.name.split(" ")[0]}
            </label>
          ))}
        </div>
      )}
      <button onClick={() => onSave(all || picked.length === 0 ? null : picked)} className="mt-2 rounded-full bg-black px-3 py-1 text-[11px] font-medium text-white">
        Save
      </button>
    </div>
  );
}

export default function KnowledgeModal({ people, onClose, onCreateExpert }: { people: Person[]; onClose: () => void; onCreateExpert: (docs: string[], sources: string[]) => void }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [limits, setLimits] = useState<{ max_docs: number; max_mb: number; formats: string } | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [drag, setDrag] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [pickedDocs, setPickedDocs] = useState<string[]>([]);
  const [pickedSources, setPickedSources] = useState<string[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [cfg, setCfg] = useState({ strategy: "auto", size: 900, overlap: 120 });
  const [advanced, setAdvanced] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const [pieces, setPieces] = useState<Record<string, Piece[]>>({});
  const [rechunkWith, setRechunkWith] = useState("auto");
  const [rechunking, setRechunking] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [dbName, setDbName] = useState("");
  const [dbUrl, setDbUrl] = useState("");
  const [dbMode, setDbMode] = useState<"file" | "url">("file");
  const [dbFile, setDbFile] = useState<File | null>(null);
  const [dbMsg, setDbMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [dbBusy, setDbBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const [webUrl, setWebUrl] = useState("");
  const [webBusy, setWebBusy] = useState(false);
  const [webMsg, setWebMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await apiFetch("/knowledge");
    if (!r.ok) return;
    const d = await r.json();
    setDocs(d.docs);
    setSources(d.sources);
    setLimits(d.limits);
  }, []);

  useEffect(() => {
    load().catch(() => {});
    apiFetch("/knowledge/strategies")
      .then((r) => r.json())
      .then((d) => setStrategies(d.strategies))
      .catch(() => {});
  }, [load]);

  async function addWebPage(e: React.FormEvent) {
    e.preventDefault();
    setWebBusy(true);
    setWebMsg(null);
    const r = await apiFetch("/knowledge/url", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: webUrl.trim() }) });
    const body = await r.json().catch(() => ({}));
    setWebBusy(false);
    if (!r.ok) return setWebMsg({ ok: false, text: typeof body.detail === "string" ? body.detail : "Couldn't add that address" });
    setWebUrl("");
    setWebMsg({ ok: true, text: `${body.name}: ${body.chunks} passages. It is re-checked for changes automatically.` });
    await load();
  }

  async function syncNow(id: string) {
    setSyncing(id);
    const r = await apiFetch(`/knowledge/${id}/sync`, { method: "POST" });
    const body = await r.json().catch(() => ({}));
    setSyncing(null);
    setWebMsg(r.ok ? { ok: true, text: body.status === "updated" ? "The page changed, so it was re-indexed." : "No changes since the last check." } : { ok: false, text: body.detail ?? "Couldn't re-check" });
    await load();
  }

  async function openOriginal(d: Doc) {
    const r = await apiFetch(`/knowledge/${d.id}/file`);
    if (!r.ok) return;
    window.open(URL.createObjectURL(await r.blob()), "_blank", "noopener");
  }

  async function togglePieces(id: string) {
    if (pieces[id]) return setPieces(({ [id]: _drop, ...rest }) => rest);
    const r = await apiFetch(`/knowledge/${id}/chunks?limit=6`);
    if (r.ok) setPieces((p) => ({ ...p, [id]: [] as Piece[] }));
    if (r.ok) {
      const rows: Piece[] = await r.json();
      setPieces((p) => ({ ...p, [id]: rows }));
    }
  }

  async function rechunk(id: string) {
    setRechunking(id);
    const r = await apiFetch(`/knowledge/${id}/rechunk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strategy: rechunkWith, size: cfg.size, overlap: cfg.overlap }),
    });
    setRechunking(null);
    if (r.ok) {
      setPieces(({ [id]: _drop, ...rest }) => rest);
      await load();
    }
  }

  async function uploadFiles(files: FileList | File[]) {
    for (const f of Array.from(files)) {
      setUploads((u) => [{ name: f.name, status: "uploading" }, ...u]);
      const form = new FormData();
      form.append("file", f);
      form.append("chunking", cfg.strategy);
      form.append("chunk_size", String(cfg.size));
      form.append("overlap", String(cfg.overlap));
      const r = await apiFetch("/knowledge/upload", { method: "POST", body: form });
      const body = await r.json().catch(() => ({}));
      setUploads((u) => u.map((x) => (x.name === f.name && x.status === "uploading" ? { name: f.name, status: r.ok ? "done" : "error", message: r.ok ? `${body.chunks} passages (${body.chunking?.strategy ?? "auto"}${body.chunking?.requested === "auto" || body.chunking?.requested === "tuned" ? `: ${body.chunking.reason}` : ""})${body.embedded < body.chunks ? ", keyword search only" : ""}` : (typeof body.detail === "string" ? body.detail : "Upload failed") } : x)));
      await load();
    }
  }

  async function remove(id: string) {
    await apiFetch(`/knowledge/${id}`, { method: "DELETE" });
    await load();
  }

  async function saveScope(path: string, agents: string[] | null) {
    await apiFetch(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ agents }) });
    setEditing(null);
    await load();
  }

  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    const r = await apiFetch("/knowledge/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query }) });
    setHits(r.ok ? await r.json() : []);
    setSearching(false);
  }

  async function addDatabase(e: React.FormEvent) {
    e.preventDefault();
    setDbBusy(true);
    setDbMsg(null);
    let r: Response;
    if (dbMode === "file" && dbFile) {
      const form = new FormData();
      form.append("name", dbName);
      form.append("file", dbFile);
      r = await apiFetch("/knowledge/databases/upload", { method: "POST", body: form });
    } else {
      r = await apiFetch("/knowledge/databases", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: dbName, url: dbUrl }) });
    }
    const body = await r.json().catch(() => ({}));
    setDbBusy(false);
    if (r.status === 401) return setDbMsg({ ok: false, text: "This server needs the admin token to connect databases. Enter it in Settings → API keys first." });
    if (!r.ok) return setDbMsg({ ok: false, text: typeof body.detail === "string" ? body.detail : "Couldn't connect" });
    setDbMsg({ ok: true, text: "Connected (read-only)." });
    setDbName("");
    setDbUrl("");
    setDbFile(null);
    await load();
  }

  async function removeDb(id: string) {
    await apiFetch(`/knowledge/databases/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[92vh] w-full max-w-3xl flex-col rounded-2xl bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-bold">Knowledge</h2>
          <button onClick={onClose} className="rounded-full bg-black/10 px-3 py-1 text-xs font-medium">
            Close
          </button>
        </div>
        <div className="flex-1 space-y-6 overflow-y-auto pr-1">
          {/* ---------- documents ---------- */}
          <section className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">Documents</h3>
              <button
                onClick={() => onCreateExpert(pickedDocs, pickedSources)}
                className={`rounded-full px-3 py-1 text-xs font-medium ${pickedDocs.length + pickedSources.length > 0 ? "bg-emerald-600 text-white" : "bg-black/10 text-black/70"}`}
              >
                🎓 {pickedDocs.length + pickedSources.length > 0 ? `Create an expert from ${pickedDocs.length + pickedSources.length} selected` : "Create an expert agent"}
              </button>
            </div>
            <p className="text-xs text-black/60">
              Upload files and the agents will search them when you ask about them, quoting the source. {limits ? `Up to ${limits.max_docs} files, ${limits.max_mb} MB each: ${limits.formats}.` : ""}
            </p>
            <div className="rounded-xl border border-black/10 p-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">How to cut documents into passages</span>
                <select value={cfg.strategy} onChange={(e) => setCfg((c) => ({ ...c, strategy: e.target.value }))} className="rounded-lg border border-black/15 px-2 py-1 text-sm">
                  {(strategies.length ? strategies : [{ id: "auto", description: "" }]).map((s) => (
                    <option key={s.id} value={s.id}>
                      {LABEL[s.id] ?? s.id}
                    </option>
                  ))}
                </select>
                <button onClick={() => setAdvanced((a) => !a)} className="text-xs underline">
                  {advanced ? "Hide" : "Size and overlap"}
                </button>
              </div>
              <p className="mt-1 text-xs text-black/60">{strategies.find((s) => s.id === cfg.strategy)?.description}</p>
              {advanced && (
                <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
                  <label className="flex items-center gap-2">
                    Size {cfg.size}
                    <input type="range" min={200} max={3000} step={50} value={cfg.size} onChange={(e) => setCfg((c) => ({ ...c, size: Number(e.target.value), overlap: Math.min(c.overlap, Math.floor(Number(e.target.value) / 2)) }))} />
                  </label>
                  <label className="flex items-center gap-2">
                    Overlap {cfg.overlap}
                    <input type="range" min={0} max={Math.min(500, Math.floor(cfg.size / 2))} step={10} value={cfg.overlap} onChange={(e) => setCfg((c) => ({ ...c, overlap: Number(e.target.value) }))} />
                  </label>
                </div>
              )}
            </div>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
              }}
              onClick={() => input.current?.click()}
              className={`cursor-pointer rounded-xl border-2 border-dashed px-4 py-6 text-center text-sm ${drag ? "border-black bg-black/5" : "border-black/20"}`}
            >
              Drop files here or <span className="font-medium underline">browse</span>
              <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
            </div>
            <form onSubmit={addWebPage} className="flex flex-wrap items-center gap-2 text-sm">
              <input value={webUrl} onChange={(e) => setWebUrl(e.target.value)} placeholder="…or add a web page or online file (https://)" className="min-w-0 flex-1 rounded-lg border border-black/15 px-3 py-2 text-sm" />
              <button disabled={webBusy || webUrl.trim().length < 8} className="rounded-lg bg-black/10 px-3 py-2 text-xs font-medium disabled:opacity-40">
                {webBusy ? "Fetching…" : "Add page"}
              </button>
            </form>
            {webMsg && <p className={`text-xs ${webMsg.ok ? "text-green-700" : "text-red-600"}`}>{webMsg.text}</p>}
            {uploads.slice(0, 4).map((u, i) => (
              <p key={i} className={`text-xs ${u.status === "error" ? "text-red-600" : u.status === "done" ? "text-green-700" : "text-black/60"}`}>
                {u.status === "uploading" ? "⏳" : u.status === "done" ? "✓" : "✕"} {u.name}: {u.status === "uploading" ? "reading and indexing…" : u.message}
              </p>
            ))}
            <div className="space-y-1.5">
              {docs.length === 0 && <p className="text-sm text-black/50">No documents yet.</p>}
              {docs.map((d) => (
                <div key={d.id} className="rounded-lg border border-black/10 px-3 py-2 text-sm">
                  <div className="flex items-center gap-2">
                    <input type="checkbox" aria-label={`Select ${d.name}`} checked={pickedDocs.includes(d.id)} onChange={() => setPickedDocs((l) => (l.includes(d.id) ? l.filter((x) => x !== d.id) : [...l, d.id]))} />
                    <span>{ICON[d.kind] ?? "📄"}</span>
                    <span className="truncate font-medium">{d.name}</span>
                    <span className="shrink-0 text-xs text-black/50">
                      {fmtSize(d.size)} · {d.chunks} passages
                    </span>
                    {d.embedded < d.chunks && <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] text-amber-800">keyword search only</span>}
                    {d.origin?.type === "url" && (
                      <button onClick={() => syncNow(d.id)} disabled={syncing === d.id} title={`Re-check ${d.origin.url}${d.synced ? `. Last checked ${new Date(d.synced * 1000).toLocaleString()}` : ""}`} className="shrink-0 rounded-full bg-sky-50 px-2 py-0.5 text-[10px] font-medium text-sky-800 disabled:opacity-50">
                        {syncing === d.id ? "checking…" : "↻ web · sync"}
                      </button>
                    )}
                    {d.origin?.type === "folder" && <span className="shrink-0 rounded-full bg-sky-50 px-2 py-0.5 text-[10px] font-medium text-sky-800">📁 synced folder</span>}
                    <button onClick={() => openOriginal(d)} className="shrink-0 text-[10px] underline">
                      open
                    </button>
                    <button onClick={() => setDetail(detail === d.id ? null : d.id)} title={d.chunking?.reason} className="shrink-0 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-800">
                      ✂ {d.chunking ? LABEL[d.chunking.strategy] ?? d.chunking.strategy : "chunking"}
                    </button>
                    <button onClick={() => setEditing(editing === d.id ? null : d.id)} className="ml-auto shrink-0 rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-medium">
                      {scopeLabel(d.scope, people)}
                    </button>
                    <button onClick={() => remove(d.id)} className="shrink-0 rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-700">
                      Delete
                    </button>
                  </div>
                  {editing === d.id && <ScopePicker people={people} value={d.scope} onSave={(v) => saveScope(`/knowledge/${d.id}/scope`, v)} />}
                  {detail === d.id && (
                    <div className="mt-2 space-y-2 rounded-lg bg-black/5 p-2 text-xs">
                      {d.chunking ? (
                        <p>
                          <b>{LABEL[d.chunking.strategy] ?? d.chunking.strategy}</b>, up to {d.chunking.size} characters, {d.chunking.overlap} overlap. <span className="text-black/60">Why: {d.chunking.reason}.</span>
                        </p>
                      ) : (
                        <p className="text-black/60">Added before chunking choices existed. Upload it again to change how it is cut.</p>
                      )}
                      {d.chunking?.tuning?.results && (
                        <div className="overflow-x-auto">
                          <p className="mb-1 text-black/60">
                            Scoreboard from {d.chunking.tuning.probes} questions made from this document. Each method got the same amount of text to find the answer in (hit@600 / hit@1500 characters).
                          </p>
                          <table className="w-full text-left">
                            <thead className="text-black/50">
                              <tr>
                                <th className="pr-3 font-medium">Method</th>
                                <th className="pr-3 font-medium">Size</th>
                                <th className="pr-3 font-medium">Passages</th>
                                <th className="pr-3 font-medium">hit@600</th>
                                <th className="pr-3 font-medium">hit@1500</th>
                              </tr>
                            </thead>
                            <tbody>
                              {d.chunking.tuning.results.map((r, i) => (
                                <tr key={i} className={i === 0 ? "font-semibold" : ""}>
                                  <td className="pr-3">{LABEL[r.strategy] ?? r.strategy}{i === 0 ? " ✓" : ""}</td>
                                  <td className="pr-3">{r.size}</td>
                                  <td className="pr-3">{r.chunks}</td>
                                  <td className="pr-3">{Math.round(r["hit@600"] * 100)}%</td>
                                  <td className="pr-3">{Math.round(r["hit@1500"] * 100)}%</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                      <div className="flex flex-wrap items-center gap-2">
                        <select value={rechunkWith} onChange={(e) => setRechunkWith(e.target.value)} className="rounded-lg border border-black/15 px-2 py-1">
                          {strategies.map((s) => (
                            <option key={s.id} value={s.id}>
                              {LABEL[s.id] ?? s.id}
                            </option>
                          ))}
                        </select>
                        <button disabled={rechunking === d.id} onClick={() => rechunk(d.id)} className="rounded-full bg-black px-3 py-1 font-medium text-white disabled:opacity-40">
                          {rechunking === d.id ? "Re-cutting…" : "Re-chunk"}
                        </button>
                        <button onClick={() => togglePieces(d.id)} className="rounded-full bg-black/10 px-3 py-1 font-medium">
                          {pieces[d.id] ? "Hide passages" : "Preview passages"}
                        </button>
                      </div>
                      {pieces[d.id]?.map((pc) => (
                        <div key={pc.ord} className="rounded bg-white p-2">
                          <p className="font-medium text-black/60">
                            #{pc.ord + 1} · {pc.locator}
                          </p>
                          <p className="mt-0.5 whitespace-pre-wrap">{pc.text.slice(0, 260)}{pc.text.length > 260 ? "…" : ""}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
            {docs.length > 0 && (
              <form onSubmit={search} className="space-y-2 rounded-xl border border-black/10 p-3">
                <p className="text-xs font-medium">Test a search</p>
                <div className="flex gap-2">
                  <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Ask something your documents answer…" className="min-w-0 flex-1 rounded-lg border border-black/15 px-3 py-2 text-sm" />
                  <button disabled={searching} className="rounded-lg bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
                    {searching ? "…" : "Search"}
                  </button>
                </div>
                {hits && hits.length === 0 && <p className="text-xs text-black/50">No relevant passages. An agent would say it couldn&apos;t find anything.</p>}
                {hits?.map((h, i) => (
                  <div key={i} className="rounded-lg bg-black/5 p-2 text-xs">
                    <p className="font-medium">
                      {h.doc} · {h.locator} <span className="font-normal text-black/50">(match {h.score.toFixed(3)})</span>
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-black/80">{h.text.slice(0, 280)}</p>
                  </div>
                ))}
              </form>
            )}
          </section>

          {/* ---------- databases ---------- */}
          <section className="space-y-3">
            <h3 className="text-sm font-semibold">Databases</h3>
            <p className="text-xs text-black/60">
              Connect a database and agents with database access (Oscar, or anyone you enable when hiring) can answer questions by running read-only SQL. Writes are blocked at several layers, results are capped at 50 rows,
              and queries are time-limited. Use a read-only database user.
            </p>
            <form onSubmit={addDatabase} className="space-y-2 rounded-xl border border-black/10 p-3">
              <div className="flex gap-1 rounded-full bg-black/5 p-1 text-xs font-medium">
                {(["file", "url"] as const).map((m) => (
                  <button type="button" key={m} onClick={() => setDbMode(m)} className={`flex-1 rounded-full px-3 py-1 ${dbMode === m ? "bg-black text-white" : ""}`}>
                    {m === "file" ? "Upload a SQLite file" : "Connect to PostgreSQL / MySQL"}
                  </button>
                ))}
              </div>
              <div className="flex gap-2">
                <input value={dbName} maxLength={40} onChange={(e) => setDbName(e.target.value)} placeholder="Name (e.g. shop)" className="w-40 rounded-lg border border-black/15 px-3 py-2 text-sm" />
                {dbMode === "file" ? (
                  <input key="db-file" type="file" accept=".db,.sqlite,.sqlite3" onChange={(e) => setDbFile(e.target.files?.[0] ?? null)} className="min-w-0 flex-1 text-xs" />
                ) : (
                  <input key="db-url" type="password" autoComplete="off" value={dbUrl} onChange={(e) => setDbUrl(e.target.value)} placeholder="postgresql://user:password@host:5432/dbname" className="min-w-0 flex-1 rounded-lg border border-black/15 px-3 py-2 text-sm" />
                )}
                <button disabled={dbBusy || !dbName.trim() || (dbMode === "file" ? !dbFile : dbUrl.length < 8)} className="rounded-lg bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
                  {dbBusy ? "Connecting…" : "Connect"}
                </button>
              </div>
              {dbMsg && <p className={`text-xs ${dbMsg.ok ? "text-green-700" : "text-red-600"}`}>{dbMsg.text}</p>}
            </form>
            <div className="space-y-1.5">
              {sources.length === 0 && <p className="text-sm text-black/50">No databases connected.</p>}
              {sources.map((s) => (
                <div key={s.id} className="rounded-lg border border-black/10 px-3 py-2 text-sm">
                  <div className="flex items-center gap-2">
                    <input type="checkbox" aria-label={`Select ${s.name}`} checked={pickedSources.includes(s.id)} onChange={() => setPickedSources((l) => (l.includes(s.id) ? l.filter((x) => x !== s.id) : [...l, s.id]))} />
                    <span>🗄️</span>
                    <span className="font-medium">{s.name}</span>
                    <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-semibold uppercase">{s.kind}</span>
                    <button onClick={() => setEditing(editing === s.id ? null : s.id)} className="ml-auto rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-medium">
                      {scopeLabel(s.scope, people)}
                    </button>
                    <button onClick={() => removeDb(s.id)} className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-700">
                      Remove
                    </button>
                  </div>
                  <details className="mt-1 text-xs text-black/60">
                    <summary className="cursor-pointer">Tables and columns</summary>
                    <pre className="mt-1 whitespace-pre-wrap rounded bg-black/5 p-2 font-mono text-[11px]">{s.schema}</pre>
                  </details>
                  {editing === s.id && <ScopePicker people={people} value={s.scope} onSave={(v) => saveScope(`/knowledge/databases/${s.id}/scope`, v)} />}
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
