"use client";

import { Suspense, useEffect, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Environment } from "@react-three/drei";
import type * as THREE from "three";
import { Costume, useDressed } from "./Character";
import type { Look } from "./looks";

const S = 0.5;

function Model({ look, color }: { look: Look; color: string }) {
  const { scene, actions } = useDressed(color);
  const turn = useRef<THREE.Group>(null);
  useEffect(() => {
    const a = actions["Idle"];
    a?.reset().play();
    return () => {
      a?.stop();
    };
  }, [actions]);
  useFrame((_, dt) => {
    if (turn.current) turn.current.rotation.y += dt * 0.6;
  });
  return (
    <group ref={turn}>
      <primitive object={scene} scale={S} />
      <Costume scene={scene} look={look} s={S} />
    </group>
  );
}

/** A slowly turning robot wearing the given look, for the character creator. */
export default function LookPreview({ look, color }: { look: Look; color: string }) {
  return (
    <Canvas camera={{ position: [0, 1.5, 6.3], fov: 34 }} dpr={[1, 2]}>
      <color attach="background" args={["#e9e4d6"]} />
      <ambientLight intensity={1} />
      <directionalLight position={[3, 5, 5]} intensity={1.3} />
      <Suspense fallback={null}>
        <group position={[0, -0.1, 0]}>
          <Model look={look} color={color} />
        </group>
        <Environment preset="city" />
      </Suspense>
    </Canvas>
  );
}
