"use client";

import { useMemo, useRef, type ReactNode } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import * as THREE from "three";

/** A DOM label whose size adapts to camera distance: compact in the overview, capped when zoomed in close. */
export function Tag({ position, children, front }: { position: [number, number, number]; children: ReactNode; front?: boolean }) {
  const g = useRef<THREE.Group>(null);
  const inner = useRef<HTMLDivElement>(null);
  const wp = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ camera }) => {
    if (!g.current || !inner.current) return;
    g.current.getWorldPosition(wp);
    const k = Math.min(1.1, Math.max(0.5, 17 / camera.position.distanceTo(wp)));
    inner.current.style.transform = `scale(${k.toFixed(3)})`;
  });
  return (
    <group ref={g} position={position}>
      <Html center zIndexRange={front ? [20, 0] : [10, 0]} style={{ pointerEvents: "none" }}>
        <div ref={inner} style={{ transformOrigin: "center bottom" }}>
          {children}
        </div>
      </Html>
    </group>
  );
}
