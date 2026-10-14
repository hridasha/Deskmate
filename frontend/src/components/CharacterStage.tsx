"use client";

import { Suspense, useEffect, useMemo } from "react";
import { Canvas } from "@react-three/fiber";
import { ContactShadows, Environment } from "@react-three/drei";
import * as THREE from "three";
import { Costume, useDressed } from "@/components/studio/Character";
import { lookFor, type Look } from "@/components/studio/looks";

export type AgentState = "idle" | "thinking" | "tool_call" | "speaking" | "error";

const S = 0.45;

// Which animation plays for a given agent state / emotion.
function pickClip(state: AgentState, emotion: string): string {
  if (state === "thinking") return "Walking";
  if (state === "tool_call") return "Running";
  if (state === "error") return "Death";
  if (state === "speaking") {
    return { happy: "ThumbsUp", annoyed: "No", surprised: "Jump", thinking: "Standing" }[emotion] ?? "Wave";
  }
  return "Idle";
}

// Face morph targets exist for these emotions on the model's head.
const FACE: Record<string, string> = { annoyed: "Angry", surprised: "Surprised" };

function Figure({ id, color, look, state, emotion }: { id: string; color: string; look?: Look; state: AgentState; emotion: string }) {
  const dressed = useMemo(() => lookFor(id, color, look), [id, color, look]);
  const { scene, actions } = useDressed(color);

  useEffect(() => {
    const next = actions[pickClip(state, emotion)] ?? actions["Idle"];
    next?.reset().fadeIn(0.3).play();
    return () => {
      next?.fadeOut(0.3);
    };
  }, [state, emotion, actions]);

  useEffect(() => {
    const target = state === "speaking" ? FACE[emotion] : undefined;
    scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.morphTargetDictionary || !mesh.morphTargetInfluences) return;
      for (const [key, idx] of Object.entries(mesh.morphTargetDictionary)) mesh.morphTargetInfluences[idx] = key === target ? 1 : 0;
    });
  }, [state, emotion, scene]);

  return (
    <group position={[0, -1.05, 0]}>
      <primitive object={scene} scale={S} />
      <Costume scene={scene} look={dressed} s={S} />
    </group>
  );
}

/** The selected agent, dressed in their look, reacting to what they are doing. */
export default function CharacterStage({
  id,
  state,
  emotion,
  color,
  look,
}: {
  id: string;
  state: AgentState;
  emotion: string;
  color: string;
  look?: Look;
}) {
  return (
    <div className="h-72 w-full overflow-hidden rounded-xl" style={{ background: `radial-gradient(circle at 50% 30%, ${color}, #1a1530)` }}>
      <Canvas camera={{ position: [0, 0.7, 4.6], fov: 40 }} dpr={[1, 2]}>
        <ambientLight intensity={0.8} />
        <directionalLight position={[3, 5, 2]} intensity={1.6} />
        <Suspense fallback={null}>
          <Figure key={id} id={id} color={color} look={look} state={state} emotion={emotion} />
          <Environment preset="city" />
          <ContactShadows position={[0, -1.05, 0]} opacity={0.5} scale={6} blur={2.5} />
        </Suspense>
      </Canvas>
    </div>
  );
}
