"use client";

import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { useAnimations, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import * as SkeletonUtils from "three/examples/jsm/utils/SkeletonUtils.js";
import type { Look } from "./looks";

export const ROBOT = "/models/RobotExpressive.glb";

/** A clone of the robot with its own materials; the body shell is tinted with the agent's color. */
export function useDressed(main: string) {
  const gltf = useGLTF(ROBOT);
  const scene = useMemo(() => {
    const s = SkeletonUtils.clone(gltf.scene);
    s.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh) return;
      const cloned = (Array.isArray(m.material) ? m.material : [m.material]).map((mat) => {
        const c = mat.clone();
        if (c.name === "Main" && "color" in c) (c as THREE.MeshStandardMaterial).color.set(main);
        return c;
      });
      m.material = Array.isArray(m.material) ? cloned : cloned[0];
    });
    return s;
  }, [gltf.scene, main]);
  const { actions } = useAnimations(gltf.animations, scene);
  return { scene, actions };
}

// Measurements are in the robot's own units (the model is ~4.8 units tall; +z is the face).
type Anchor = { key: "head" | "torso" | "hips"; bone: string; point: [number, number, number] };
const ANCHORS: Anchor[] = [
  { key: "head", bone: "Head", point: [0, 3.72, 0] },
  { key: "torso", bone: "Abdomen", point: [0, 2.08, 0] },
  { key: "hips", bone: "Hips", point: [0, 1.38, 0] },
];

type Limb = { key: string; a: string; b: string; r: number; color: string };

function Mat({ color, rough = 0.85 }: { color: string; rough?: number }) {
  return <meshStandardMaterial color={color} roughness={rough} />;
}

function Hair({ look }: { look: Look }) {
  const c = look.hairColor;
  // Head centre is the origin; the head is a ~1.6-radius sphere with the visor facing +z.
  const cap = (theta: number) => (
    <mesh scale={[1.58, 1.58, 1.6]} position={[0, -0.02, -0.04]}>
      <sphereGeometry args={[1, 28, 18, 0, Math.PI * 2, 0, theta]} />
      <Mat color={c} rough={0.95} />
    </mesh>
  );
  switch (look.hair) {
    case "bowl":
      return (
        <group>
          {cap(Math.PI * 0.42)}
          <mesh position={[0, 0.95, 1.2]} rotation={[-0.35, 0, 0]}>
            <boxGeometry args={[2.3, 0.4, 0.55]} />
            <Mat color={c} rough={0.95} />
          </mesh>
        </group>
      );
    case "messy":
      return (
        <group>
          {cap(Math.PI * 0.38)}
          {(
            [
              [-0.7, 1.55, 0.3],
              [0.2, 1.7, 0.5],
              [0.9, 1.5, 0.0],
              [-0.2, 1.65, -0.5],
            ] as [number, number, number][]
          ).map(([x, y, z], i) => (
            <mesh key={i} position={[x, y, z]} rotation={[0.3 * (i - 1.5), 0, 0.25 * (i - 1.5)]}>
              <coneGeometry args={[0.28, 0.7, 8]} />
              <Mat color={c} rough={0.95} />
            </mesh>
          ))}
        </group>
      );
    case "long":
      return (
        <group>
          {cap(Math.PI * 0.44)}
          <mesh position={[0, -0.5, -1.25]}>
            <boxGeometry args={[2.7, 2.6, 0.6]} />
            <Mat color={c} rough={0.95} />
          </mesh>
          {[-1, 1].map((sx) => (
            <mesh key={sx} position={[sx * 1.5, -0.3, -0.3]}>
              <boxGeometry args={[0.35, 2.0, 1.2]} />
              <Mat color={c} rough={0.95} />
            </mesh>
          ))}
        </group>
      );
    case "bun":
      return (
        <group>
          {cap(Math.PI * 0.4)}
          <mesh position={[0, 1.72, -0.7]}>
            <sphereGeometry args={[0.55, 16, 16]} />
            <Mat color={c} rough={0.95} />
          </mesh>
        </group>
      );
    case "bob":
      return (
        <group>
          {cap(Math.PI * 0.44)}
          {[-1, 1].map((sx) => (
            <mesh key={sx} position={[sx * 1.52, -0.45, -0.2]}>
              <boxGeometry args={[0.4, 1.7, 1.9]} />
              <Mat color={c} rough={0.95} />
            </mesh>
          ))}
          <mesh position={[0, -0.35, -1.3]}>
            <boxGeometry args={[2.9, 1.9, 0.5]} />
            <Mat color={c} rough={0.95} />
          </mesh>
        </group>
      );
    case "slick":
      return (
        <group>
          {cap(Math.PI * 0.36)}
          <mesh position={[0.35, 1.15, 0.9]} rotation={[-0.5, 0, -0.18]}>
            <boxGeometry args={[1.9, 0.3, 0.7]} />
            <Mat color={c} rough={0.7} />
          </mesh>
        </group>
      );
    case "curly":
      return (
        <group>
          {cap(Math.PI * 0.4)}
          {Array.from({ length: 14 }, (_, i) => {
            const a = (i / 14) * Math.PI * 2;
            return (
              <mesh key={i} position={[Math.cos(a) * 1.45, 0.75 + (i % 3) * 0.45, Math.sin(a) * 1.4 - 0.15]}>
                <sphereGeometry args={[0.4, 10, 10]} />
                <Mat color={c} rough={0.95} />
              </mesh>
            );
          })}
        </group>
      );
    case "bald":
      return (
        <group>
          {[-1, 1].map((sx) => (
            <mesh key={sx} position={[sx * 1.55, 0.1, -0.35]} scale={[0.55, 1.2, 1]}>
              <sphereGeometry args={[0.45, 12, 12]} />
              <Mat color={c} rough={0.95} />
            </mesh>
          ))}
        </group>
      );
    default:
      return cap(Math.PI * 0.4);
  }
}

function HeadWear({ look }: { look: Look }) {
  return (
    <group>
      <Hair look={look} />
      {look.glasses && (
        <group position={[0, 0.05, 1.62]}>
          {[-1, 1].map((sx) => (
            <mesh key={sx} position={[sx * 0.62, 0, 0]}>
              <torusGeometry args={[0.5, 0.075, 10, 28]} />
              <meshStandardMaterial color="#141414" />
            </mesh>
          ))}
          <mesh>
            <boxGeometry args={[0.3, 0.07, 0.07]} />
            <meshStandardMaterial color="#141414" />
          </mesh>
        </group>
      )}
      {look.mustache && (
        <mesh position={[0, -0.55, 1.62]}>
          <boxGeometry args={[1.1, 0.2, 0.3]} />
          <Mat color="#4a4a4a" />
        </mesh>
      )}
    </group>
  );
}

function TorsoWear({ look }: { look: Look }) {
  const bulk = look.bulk ?? 1;
  const body = look.jacket ?? look.shirt;
  const zf = (look.jacket ? 0.8 : 0.77) * bulk;
  return (
    <group>
      {/* shirt or jacket */}
      <mesh scale={[(look.jacket ? 0.84 : 0.8) * bulk, 1.2, (look.jacket ? 0.78 : 0.74) * bulk]}>
        <cylinderGeometry args={[1, 0.9, 1, 28]} />
        <Mat color={body} />
      </mesh>
      {/* shirt front visible between jacket lapels */}
      {look.jacket && (
        <mesh position={[0, 0.02, zf - 0.02]}>
          <boxGeometry args={[0.55, 1.05, 0.05]} />
          <Mat color={look.shirt} />
        </mesh>
      )}
      {look.tie && (
        <group position={[0, 0, zf + 0.02]}>
          <mesh position={[0, -0.12, 0]}>
            <boxGeometry args={[0.26, 0.8, 0.06]} />
            <Mat color={look.tie} />
          </mesh>
          <mesh position={[0, 0.34, 0.01]}>
            <boxGeometry args={[0.3, 0.2, 0.09]} />
            <Mat color={look.tie} />
          </mesh>
        </group>
      )}
    </group>
  );
}

/** Clothes, hair and glasses that follow the robot's animated skeleton. Pass the same scale as the model. */
export function Costume({ scene, look, s }: { scene: THREE.Object3D; look: Look; s: number }) {
  const root = useRef<THREE.Group>(null);
  const find = (n: string) => scene.getObjectByName(n) ?? undefined;

  // Rest pose, captured before any animation plays: bone positions/orientations and the offset to each anchor point.
  const rest = useMemo(() => {
    scene.updateMatrixWorld(true);
    return ANCHORS.map((a) => {
      const b = find(a.bone);
      const p = new THREE.Vector3();
      const q = new THREE.Quaternion();
      b?.getWorldPosition(p);
      b?.getWorldQuaternion(q);
      return { ...a, off: new THREE.Vector3(...a.point).sub(p), qInv: q.clone().invert() };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene]);

  const sleeve = look.jacket ?? look.shirt;
  const limbs: Limb[] = useMemo(
    () => [
      { key: "uaL", a: "UpperArmL", b: "LowerArmL", r: 0.36, color: sleeve },
      { key: "uaR", a: "UpperArmR", b: "LowerArmR", r: 0.36, color: sleeve },
      { key: "ulL", a: "UpperLegL", b: "LowerLegL", r: 0.22, color: look.pants },
      { key: "ulR", a: "UpperLegR", b: "LowerLegR", r: 0.22, color: look.pants },
    ],
    [look.pants, sleeve],
  );

  const limbRefs = useRef<Record<string, THREE.Mesh | null>>({});
  const anchorRefs = useRef<Record<string, THREE.Group | null>>({});
  const t = useMemo(
    () => ({
      a: new THREE.Vector3(),
      b: new THREE.Vector3(),
      d: new THREE.Vector3(),
      o: new THREE.Vector3(),
      up: new THREE.Vector3(0, 1, 0),
      q: new THREE.Quaternion(),
      gq: new THREE.Quaternion(),
    }),
    [],
  );

  useFrame(() => {
    const g = root.current?.parent;
    if (!g) return;
    g.getWorldQuaternion(t.gq).invert();

    for (const l of limbs) {
      const m = limbRefs.current[l.key];
      const ba = find(l.a);
      const bb = find(l.b);
      if (!m || !ba || !bb) continue;
      ba.getWorldPosition(t.a);
      g.worldToLocal(t.a);
      bb.getWorldPosition(t.b);
      g.worldToLocal(t.b);
      t.d.subVectors(t.b, t.a);
      const len = t.d.length();
      if (len < 1e-4) continue;
      m.position.addVectors(t.a, t.b).multiplyScalar(0.5);
      m.quaternion.setFromUnitVectors(t.up, t.d.normalize());
      m.scale.set(l.r * s, len, l.r * s);
    }

    for (const a of rest) {
      const grp = anchorRefs.current[a.key];
      const b = find(a.bone);
      if (!grp || !b) continue;
      b.getWorldPosition(t.a);
      g.worldToLocal(t.a);
      b.getWorldQuaternion(t.q);
      t.q.premultiply(t.gq).multiply(a.qInv); // how far the bone has turned from its rest pose
      t.o.copy(a.off).multiplyScalar(s).applyQuaternion(t.q);
      grp.position.copy(t.a).add(t.o);
      grp.quaternion.copy(t.q);
    }
  });

  return (
    <group ref={root}>
      {limbs.map((l) => (
        <mesh
          key={l.key}
          ref={(m) => {
            limbRefs.current[l.key] = m;
          }}
        >
          <cylinderGeometry args={[1, 1, 1, 18]} />
          <Mat color={l.color} />
        </mesh>
      ))}
      {ANCHORS.map((a) => (
        <group
          key={a.key}
          ref={(g) => {
            anchorRefs.current[a.key] = g;
          }}
          scale={s}
        >
          {a.key === "head" && <HeadWear look={look} />}
          {a.key === "torso" && <TorsoWear look={look} />}
          {a.key === "hips" && (
            <mesh scale={[0.88 * (look.bulk ?? 1), 0.3, 0.8 * (look.bulk ?? 1)]}>
              <cylinderGeometry args={[1, 0.95, 1, 24]} />
              <Mat color={look.pants} />
            </mesh>
          )}
        </group>
      ))}
    </group>
  );
}

useGLTF.preload(ROBOT);
