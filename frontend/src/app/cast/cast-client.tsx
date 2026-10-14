"use client";

import dynamic from "next/dynamic";
import Link from "next/link";

const CastLineup = dynamic(() => import("@/components/studio/CastLineup"), {
  ssr: false,
  loading: () => <div className="flex h-full items-center justify-center text-sm text-black/50">Loading the cast…</div>,
});

export default function CastClient() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-3 p-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">The Cast</h1>
          <p className="text-sm text-black/60">Everyone in the office plus everyone you can hire. Drag to rotate, scroll to zoom.</p>
        </div>
        <Link href="/studio" className="rounded-full bg-black px-3 py-1.5 text-xs font-medium text-white">
          Back to the office
        </Link>
      </header>
      <div className="h-[78vh] overflow-hidden rounded-2xl border border-black/10 shadow-sm">
        <CastLineup />
      </div>
    </main>
  );
}
