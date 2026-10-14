"use client";

import dynamic from "next/dynamic";
import { HAIR_STYLES, type Look } from "./looks";

const LookPreview = dynamic(() => import("./LookPreview"), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse bg-black/10" />,
});

const HAIR_COLORS = ["#1f1a17", "#4a3220", "#7a5230", "#b5762b", "#d9c07a", "#c0643a", "#7d7d7d", "#e8e8e8"];
const CLOTH_COLORS = ["#f2f2f2", "#9ec3e8", "#8793d1", "#c9a227", "#e63946", "#2a9d8f", "#e48fa8", "#c3b1e1", "#4a8f5a", "#2f3a52", "#222222", "#8a8f99"];
const TIE_COLORS = ["#b02a37", "#2f4d8f", "#5a3b22", "#3d3d6b", "#c9b79c", "#2a9d8f", "#f4a261", "#222222"];
const PANTS_COLORS = ["#2f3a52", "#33363f", "#4b3b2c", "#3a3f4b", "#5b2333", "#27402f", "#6b5b3a", "#202225"];

function Swatches({
  value,
  colors,
  onPick,
  allowNone,
}: {
  value?: string;
  colors: string[];
  onPick: (c: string | undefined) => void;
  allowNone?: boolean;
}) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5">
      {allowNone && (
        <button
          type="button"
          onClick={() => onPick(undefined)}
          className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${!value ? "border-black bg-black text-white" : "border-black/20"}`}
        >
          None
        </button>
      )}
      {colors.map((c) => (
        <button
          type="button"
          key={c}
          onClick={() => onPick(c)}
          aria-label={c}
          className={`h-6 w-6 rounded-full border-2 ${value === c ? "border-black" : "border-black/10"}`}
          style={{ background: c }}
        />
      ))}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="text-sm">
      <span className="text-xs font-medium text-black/70">{label}</span>
      {children}
    </div>
  );
}

/** Live 3D preview plus controls for hair, clothes and accessories. */
export default function LookEditor({ look, color, onChange }: { look: Look; color: string; onChange: (l: Look) => void }) {
  const set = (patch: Partial<Look>) => onChange({ ...look, ...patch });
  return (
    <div className="grid gap-3 sm:grid-cols-[170px_1fr]">
      <div className="h-56 overflow-hidden rounded-xl border border-black/10 sm:h-auto sm:min-h-[300px]">
        <LookPreview look={look} color={color} />
      </div>
      <div className="space-y-2.5">
        <Row label="Hair style">
          <select value={look.hair} onChange={(e) => set({ hair: e.target.value as Look["hair"] })} className="mt-1 w-full rounded-lg border border-black/15 px-2 py-1.5 text-sm capitalize">
            {HAIR_STYLES.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
        </Row>
        <Row label="Hair color">
          <Swatches value={look.hairColor} colors={HAIR_COLORS} onPick={(c) => c && set({ hairColor: c })} />
        </Row>
        <Row label="Shirt">
          <Swatches value={look.shirt} colors={CLOTH_COLORS} onPick={(c) => c && set({ shirt: c })} />
        </Row>
        <Row label="Jacket or cardigan">
          <Swatches value={look.jacket} colors={CLOTH_COLORS} allowNone onPick={(c) => set({ jacket: c })} />
        </Row>
        <Row label="Tie">
          <Swatches value={look.tie} colors={TIE_COLORS} allowNone onPick={(c) => set({ tie: c })} />
        </Row>
        <Row label="Pants">
          <Swatches value={look.pants} colors={PANTS_COLORS} onPick={(c) => c && set({ pants: c })} />
        </Row>
        <div className="flex flex-wrap items-center gap-4 pt-1 text-sm">
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={!!look.glasses} onChange={(e) => set({ glasses: e.target.checked })} /> Glasses
          </label>
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={!!look.mustache} onChange={(e) => set({ mustache: e.target.checked })} /> Mustache
          </label>
          <label className="flex items-center gap-1.5">
            Build
            <input type="range" min={0.85} max={1.4} step={0.05} value={look.bulk ?? 1} onChange={(e) => set({ bulk: Number(e.target.value) })} />
          </label>
        </div>
      </div>
    </div>
  );
}
