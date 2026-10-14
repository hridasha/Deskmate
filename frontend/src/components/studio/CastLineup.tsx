"use client";

import { Suspense, useEffect, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { ContactShadows, Environment, Html, OrbitControls } from "@react-three/drei";
import { Costume, useDressed } from "./Character";
import { lookFor } from "./looks";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
const S = 0.5;
const COLS = 8;

type Person = { id: string; name: string; color: string };

function Figure({ p, x, z }: { p: Person; x: number; z: number }) {
  const look = lookFor(p.id, p.color);
  const { scene, actions } = useDressed(p.color);
  const [labelled, setLabelled] = useState(false);
  useEffect(() => setLabelled(true), []); // add the HTML label after the figure has committed, not during a suspended render
  useEffect(() => {
    const a = actions["Idle"];
    a?.reset().play();
    return () => {
      a?.stop();
    };
  }, [actions]);
  return (
    <group position={[x, 0, z]}>
      <primitive object={scene} scale={S} />
      <Costume scene={scene} look={look} s={S} />
      {labelled && (
        <Html position={[0, 2.7, 0]} center style={{ pointerEvents: "none" }}>
          <span className="whitespace-nowrap rounded-full bg-black/75 px-2 py-0.5 text-[11px] font-semibold text-white">{p.name}</span>
        </Html>
      )}
    </group>
  );
}

/** Every character: the ones in the office plus everyone you can hire. */
export default function CastLineup() {
  const [people, setPeople] = useState<Person[]>([]);

  useEffect(() => {
    Promise.all([fetch(`${API}/characters`).then((r) => r.json()), fetch(`${API}/presets`).then((r) => r.json())])
      .then(([chars, presets]: [Person[], Person[]]) => {
        const seen = new Set<string>();
        setPeople([...chars, ...presets].filter((p) => !seen.has(p.id) && seen.add(p.id)));
      })
      .catch(() => setPeople([]));
  }, []);

  if (people.length === 0) return <div className="flex h-full items-center justify-center text-sm text-black/50">Loading the cast…</div>; // mount the canvas once, with everyone, instead of remounting it when the list arrives

  return (
    <Canvas camera={{ position: [0, 17, 20], fov: 38 }} dpr={[1, 2]}>
      <color attach="background" args={["#d9d4c7"]} />
      <ambientLight intensity={1} />
      <directionalLight position={[3, 8, 8]} intensity={1.4} />
      <Suspense fallback={null}>
        {people.map((p, i) => (
          <Suspense key={p.id} fallback={null}>
            {/* each figure suspends on its own, so one still loading never hides (and unmounts the labels of) the others */}
            <Figure p={p} x={((i % COLS) - (COLS - 1) / 2) * 2} z={-Math.floor(i / COLS) * 3.2} />
          </Suspense>
        ))}
        <Environment preset="city" />
        <ContactShadows position={[0, 0, -3]} opacity={0.3} scale={40} blur={2.5} />
      </Suspense>
      <OrbitControls target={[0, 0.5, -3.2]} />
    </Canvas>
  );
}
