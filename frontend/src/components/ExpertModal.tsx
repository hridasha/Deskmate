"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import LookEditor from "@/components/studio/LookEditor";
import { DEFAULT_LOOK, type Look } from "@/components/studio/looks";

type Doc = { id: string; name: string; kind: string; chunks: number };
type Source = { id: string; name: string; kind: string };
type Draft = { name: string; archetype: string; personality: string; starters: string[]; doc_names: string[]; source_names: string[] };

export type ExpertForm = {
  name: string;
  archetype: string;
  personality: string;
  color: string;
  look: Look;
  tools: string[];
  knowledge: { doc_ids: string[]; source_ids: string[]; exclusive: boolean; starters: string[] };
};

const PALETTE = ["#6d5acd", "#d9482b", "#3f6f9b", "#4a8f5a", "#e75fa0", "#e0a800", "#00a6a6", "#8a6d3b"];
const ICON: Record<string, string> = { pdf: "📕", docx: "📘", xlsx: "📗", xlsm: "📗", csv: "📊", tsv: "📊", html: "🌐", htm: "🌐" };

/** Build an expert agent from chosen documents and databases: pick sources, review the drafted profile, hire. */
export default function ExpertModal({
  initialDocs = [],
  initialSources = [],
  onClose,
  onCreate,
}: {
  initialDocs?: string[];
  initialSources?: string[];
  onClose: () => void;
  onCreate: (f: ExpertForm) => Promise<void>;
}) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [pickedDocs, setPickedDocs] = useState<string[]>(initialDocs);
  const [pickedSources, setPickedSources] = useState<string[]>(initialSources);
  const [step, setStep] = useState<"pick" | "review">("pick");
  const [drafting, setDrafting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [name, setName] = useState("");
  const [archetype, setArchetype] = useState("");
  const [personality, setPersonality] = useState("");
  const [starters, setStarters] = useState<string[]>([]);
  const [color, setColor] = useState(PALETTE[2]);
  const [look, setLook] = useState<Look>({ ...DEFAULT_LOOK, glasses: true, hair: "bun", shirt: "#e6efe8", jacket: "#2d6a4f" });
  const [exclusive, setExclusive] = useState(true);

  useEffect(() => {
    apiFetch("/knowledge")
      .then((r) => r.json())
      .then((d) => {
        setDocs(d.docs);
        setSources(d.sources);
      })
      .catch(() => setErr("Couldn't load your knowledge."));
  }, []);

  const toggle = (list: string[], set: (v: string[]) => void, id: string) => set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  const nothing = pickedDocs.length === 0 && pickedSources.length === 0;

  async function draft() {
    setDrafting(true);
    setErr("");
    const r = await apiFetch("/agents/suggest-from-knowledge", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ doc_ids: pickedDocs, source_ids: pickedSources }) });
    const body = await r.json().catch(() => ({}));
    setDrafting(false);
    if (!r.ok) return setErr(typeof body.detail === "string" ? body.detail : "Couldn't draft the expert.");
    const d = body as Draft;
    setName(d.name);
    setArchetype(d.archetype);
    setPersonality(d.personality);
    setStarters(d.starters);
    setStep("review");
  }

  async function hire(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErr("");
    try {
      await onCreate({
        name,
        archetype,
        personality,
        color,
        look,
        tools: [],
        knowledge: { doc_ids: pickedDocs, source_ids: pickedSources, exclusive, starters: starters.filter((s) => s.trim()) },
      });
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Something went wrong");
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[92vh] w-full max-w-3xl flex-col rounded-2xl bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold">🎓 Create an expert from your knowledge</h2>
            <p className="text-xs text-black/60">An agent that answers only from the sources you choose, always checks them first, and cites where each answer came from.</p>
          </div>
          <button onClick={onClose} className="rounded-full bg-black/10 px-3 py-1 text-xs font-medium">
            Close
          </button>
        </div>

        <div className="flex-1 overflow-y-auto pr-1">
          {step === "pick" ? (
            <div className="space-y-4">
              <section className="space-y-1.5">
                <h3 className="text-sm font-semibold">Documents</h3>
                {docs.length === 0 && <p className="text-sm text-black/50">No documents yet. Upload some in 📚 Knowledge first.</p>}
                {docs.map((d) => (
                  <label key={d.id} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${pickedDocs.includes(d.id) ? "border-black bg-black/5" : "border-black/10"}`}>
                    <input type="checkbox" checked={pickedDocs.includes(d.id)} onChange={() => toggle(pickedDocs, setPickedDocs, d.id)} />
                    <span>{ICON[d.kind] ?? "📄"}</span>
                    <span className="truncate font-medium">{d.name}</span>
                    <span className="ml-auto shrink-0 text-xs text-black/50">{d.chunks} passages</span>
                  </label>
                ))}
              </section>
              <section className="space-y-1.5">
                <h3 className="text-sm font-semibold">Databases</h3>
                {sources.length === 0 && <p className="text-sm text-black/50">No databases connected.</p>}
                {sources.map((s) => (
                  <label key={s.id} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${pickedSources.includes(s.id) ? "border-black bg-black/5" : "border-black/10"}`}>
                    <input type="checkbox" checked={pickedSources.includes(s.id)} onChange={() => toggle(pickedSources, setPickedSources, s.id)} />
                    <span>🗄️</span>
                    <span className="font-medium">{s.name}</span>
                    <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-semibold uppercase">{s.kind}</span>
                  </label>
                ))}
              </section>
            </div>
          ) : (
            <form id="expert-form" onSubmit={hire} className="space-y-3">
              <p className="rounded-lg bg-black/5 px-3 py-2 text-xs text-black/70">
                Drafted from your sources. Change anything you like before hiring. Sources: {[...pickedDocs.map((id) => docs.find((d) => d.id === id)?.name), ...pickedSources.map((id) => sources.find((s) => s.id === id)?.name)].filter(Boolean).join(", ")}.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-sm">
                  <span className="font-medium">Name</span>
                  <input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm" />
                </label>
                <label className="block text-sm">
                  <span className="font-medium">Role</span>
                  <input value={archetype} maxLength={80} onChange={(e) => setArchetype(e.target.value)} className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm" />
                </label>
              </div>
              <label className="block text-sm">
                <span className="font-medium">Personality</span>
                <textarea value={personality} maxLength={1200} rows={3} onChange={(e) => setPersonality(e.target.value)} className="mt-1 w-full rounded-lg border border-black/15 px-3 py-2 text-sm" />
              </label>
              <div className="text-sm">
                <span className="font-medium">Suggested questions (shown as quick buttons)</span>
                <div className="mt-1 space-y-1.5">
                  {starters.map((s, i) => (
                    <input key={i} value={s} maxLength={120} onChange={(e) => setStarters((l) => l.map((x, j) => (j === i ? e.target.value : x)))} className="w-full rounded-lg border border-black/15 px-3 py-1.5 text-sm" />
                  ))}
                </div>
              </div>
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" checked={exclusive} onChange={(e) => setExclusive(e.target.checked)} className="mt-1" />
                <span>
                  <span className="font-medium">Keep these sources private to this expert</span>
                  <span className="block text-xs text-black/60">Other agents won&apos;t be able to search them. You can change this later in Knowledge.</span>
                </span>
              </label>
              <div className="text-sm">
                <span className="font-medium">Robot body color</span>
                <div className="mt-1 flex flex-wrap gap-2">
                  {PALETTE.map((c) => (
                    <button type="button" key={c} onClick={() => setColor(c)} aria-label={c} className={`h-7 w-7 rounded-full border-2 ${color === c ? "border-black" : "border-transparent"}`} style={{ background: c }} />
                  ))}
                </div>
              </div>
              <details className="text-sm">
                <summary className="cursor-pointer font-medium">Outfit and look</summary>
                <div className="mt-2">
                  <LookEditor look={look} color={color} onChange={setLook} />
                </div>
              </details>
            </form>
          )}
        </div>

        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
        <div className="mt-4 flex justify-end gap-2">
          {step === "review" && (
            <button type="button" onClick={() => setStep("pick")} className="rounded-xl bg-black/10 px-4 py-2 text-sm font-medium">
              Back
            </button>
          )}
          {step === "pick" ? (
            <button onClick={draft} disabled={nothing || drafting} className="rounded-xl bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
              {drafting ? "Reading your sources…" : `Draft an expert (${pickedDocs.length + pickedSources.length} selected)`}
            </button>
          ) : (
            <button form="expert-form" disabled={saving || !name.trim() || !archetype.trim() || !personality.trim()} className="rounded-xl bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
              {saving ? "Hiring…" : "Hire this expert"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
