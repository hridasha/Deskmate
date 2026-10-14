"use client";

import { useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { AgentInfo } from "./StudioScene";
import { Tag } from "./Tag";
import { DOORS, DOOR_W, FLOOR, MAIN_DOOR, MCP_RACK, SLOTS, type RoomId } from "./config";

/** State shared between agents (who write requests) and the doors/props (which animate). */
export type Shared = {
  doorOpen: number[];
  doorUntil: number[];
  deskWorking: boolean[];
  mcpUntil: number;
  groups: Record<string, THREE.Group | null>;
  info: Record<string, { label: string; moving: boolean }>;
};

type Vec3 = [number, number, number];

function Box({ pos, size, color, glass, emissive, rot }: { pos: Vec3; size: Vec3; color: string; glass?: boolean; emissive?: string; rot?: Vec3 }) {
  return (
    <mesh position={pos} rotation={rot} receiveShadow={!glass}>
      <boxGeometry args={size} />
      <meshStandardMaterial
        color={color}
        roughness={glass ? 0.1 : 0.75}
        transparent={glass}
        opacity={glass ? 0.2 : 1}
        depthWrite={!glass}
        emissive={emissive ?? "#000000"}
        emissiveIntensity={emissive ? 0.5 : 0}
      />
    </mesh>
  );
}

function Cyl({ pos, r, h, color, rr }: { pos: Vec3; r: number; h: number; color: string; rr?: number }) {
  return (
    <mesh position={pos}>
      <cylinderGeometry args={[r, rr ?? r, h, 18]} />
      <meshStandardMaterial color={color} roughness={0.7} />
    </mesh>
  );
}

function Plant({ pos, big }: { pos: [number, number]; big?: boolean }) {
  const k = big ? 1.4 : 1;
  return (
    <group position={[pos[0], 0, pos[1]]} scale={k}>
      <mesh position={[0, 0.3, 0]}>
        <cylinderGeometry args={[0.28, 0.22, 0.6, 14]} />
        <meshStandardMaterial color="#b5653a" />
      </mesh>
      <mesh position={[0, 0.95, 0]}>
        <sphereGeometry args={[0.45, 14, 14]} />
        <meshStandardMaterial color="#3f8f4f" />
      </mesh>
      <mesh position={[0.2, 1.25, 0.1]}>
        <sphereGeometry args={[0.3, 12, 12]} />
        <meshStandardMaterial color="#4aa35c" />
      </mesh>
    </group>
  );
}

/** A rolling office chair; `yaw` is the direction the sitter faces (0 = +z). */
function OfficeChair({ x, z, yaw, color = "#3a4252" }: { x: number; z: number; yaw: number; color?: string }) {
  return (
    <group position={[x, 0, z]} rotation={[0, yaw, 0]}>
      <Cyl pos={[0, 0.08, 0]} r={0.32} h={0.05} color="#2a2d33" />
      <Cyl pos={[0, 0.3, 0]} r={0.05} h={0.45} color="#555a63" />
      <Box pos={[0, 0.56, 0]} size={[0.62, 0.12, 0.6]} color={color} />
      <Box pos={[0, 0.98, -0.28]} size={[0.58, 0.7, 0.1]} color={color} />
      <Box pos={[-0.34, 0.72, 0]} size={[0.05, 0.05, 0.4]} color="#2a2d33" />
      <Box pos={[0.34, 0.72, 0]} size={[0.05, 0.05, 0.4]} color="#2a2d33" />
    </group>
  );
}

function Stool({ x, z }: { x: number; z: number }) {
  return (
    <group position={[x, 0, z]}>
      <Cyl pos={[0, 0.4, 0]} r={0.05} h={0.8} color="#555a63" />
      <Cyl pos={[0, 0.82, 0]} r={0.2} h={0.06} color="#c0392b" />
    </group>
  );
}

/** Low cubicle partition in gray fabric with a darker trim. */
function Partition({ pos, size }: { pos: Vec3; size: Vec3 }) {
  return (
    <group>
      <Box pos={pos} size={size} color="#8f9aa6" />
      <Box pos={[pos[0], pos[1] + size[1] / 2 + 0.03, pos[2]]} size={[size[0] + 0.02, 0.06, size[2] + 0.02]} color="#58626d" />
    </group>
  );
}

function Mug({ pos, color }: { pos: Vec3; color: string }) {
  return (
    <mesh position={pos}>
      <cylinderGeometry args={[0.06, 0.055, 0.12, 12]} />
      <meshStandardMaterial color={color} />
    </mesh>
  );
}

/** Personal desk props, a nod to each character's running gags. */
function DeskDecor({ id, x, z, top, color }: { id: string; x: number; z: number; top: number; color: string }) {
  const t = top;
  switch (id) {
    case "jim_halpert": // stapler suspended in gelatin
      return (
        <group>
          <mesh position={[x - 0.55, t + 0.13, z]}>
            <boxGeometry args={[0.46, 0.26, 0.32]} />
            <meshStandardMaterial color="#5ee08a" transparent opacity={0.55} />
          </mesh>
          <Box pos={[x - 0.55, t + 0.13, z]} size={[0.3, 0.07, 0.1]} color="#2b2b2b" />
          <Mug pos={[x + 0.2, t + 0.06, z - 0.15]} color="#f4f4f4" />
        </group>
      );
    case "dwight_schrute": // bobblehead and a beet
      return (
        <group>
          <Box pos={[x - 0.6, t + 0.09, z]} size={[0.12, 0.18, 0.08]} color="#c9a227" />
          <mesh position={[x - 0.6, t + 0.28, z]}>
            <sphereGeometry args={[0.1, 14, 14]} />
            <meshStandardMaterial color="#e6b894" />
          </mesh>
          <mesh position={[x + 0.1, t + 0.07, z + 0.1]}>
            <sphereGeometry args={[0.07, 12, 12]} />
            <meshStandardMaterial color="#7a1f4b" />
          </mesh>
          <Mug pos={[x + 0.4, t + 0.06, z - 0.1]} color="#c9a227" />
        </group>
      );
    case "pam_beesly": // pencil cup, framed art and a little vase
      return (
        <group>
          <Mug pos={[x - 0.9, t + 0.08, z]} color="#f2a6c0" />
          {["#e63946", "#2a9d8f", "#f4a261"].map((c, i) => (
            <Box key={c} pos={[x - 0.9 + (i - 1) * 0.03, t + 0.2, z]} size={[0.015, 0.14, 0.015]} color={c} />
          ))}
          <Box pos={[x + 0.5, t + 0.25, z - 0.35]} size={[0.5, 0.36, 0.03]} color="#8ecae6" />
          <mesh position={[x + 1.0, t + 0.1, z]}>
            <sphereGeometry args={[0.1, 12, 12]} />
            <meshStandardMaterial color="#f7d6e0" />
          </mesh>
        </group>
      );
    case "oscar_martinez": // tidy folders and a calculator
      return (
        <group>
          {[0, 1, 2].map((i) => (
            <Box key={i} pos={[x - 0.55, t + 0.03 + i * 0.06, z]} size={[0.42, 0.05, 0.3]} color={["#3a5ba0", "#f4f4f4", "#3a5ba0"][i]} />
          ))}
          <Box pos={[x + 0.2, t + 0.02, z + 0.1]} size={[0.14, 0.03, 0.2]} color="#222222" />
        </group>
      );
    case "angela_martin": // cat figurines and flowers
      return (
        <group>
          {[-0.75, -0.5, -0.25].map((dx, i) => (
            <group key={i}>
              <mesh position={[x + dx, t + 0.07, z]}>
                <sphereGeometry args={[0.07, 12, 12]} />
                <meshStandardMaterial color={["#f3e9d8", "#d9d4c8", "#f3e9d8"][i]} />
              </mesh>
              <mesh position={[x + dx, t + 0.16, z + 0.03]}>
                <sphereGeometry args={[0.045, 10, 10]} />
                <meshStandardMaterial color={["#f3e9d8", "#d9d4c8", "#f3e9d8"][i]} />
              </mesh>
            </group>
          ))}
          <Box pos={[x + 0.4, t + 0.1, z]} size={[0.02, 0.2, 0.02]} color="#3f8f4f" />
          <mesh position={[x + 0.4, t + 0.23, z]}>
            <sphereGeometry args={[0.06, 10, 10]} />
            <meshStandardMaterial color="#f28cb1" />
          </mesh>
        </group>
      );
    case "kevin_malone": // chili pot and a candy bowl
      return (
        <group>
          <mesh position={[x - 0.5, t + 0.08, z]}>
            <cylinderGeometry args={[0.15, 0.13, 0.16, 16]} />
            <meshStandardMaterial color="#b3261e" />
          </mesh>
          <mesh position={[x + 0.3, t + 0.05, z]}>
            <cylinderGeometry args={[0.12, 0.08, 0.1, 14]} />
            <meshStandardMaterial color="#f4f4f4" />
          </mesh>
          {["#e63946", "#2a9d8f", "#f4a261", "#457b9d"].map((c, i) => (
            <mesh key={c} position={[x + 0.25 + i * 0.04, t + 0.12, z + (i % 2) * 0.03]}>
              <sphereGeometry args={[0.025, 8, 8]} />
              <meshStandardMaterial color={c} />
            </mesh>
          ))}
        </group>
      );
    case "stanley_hudson": // crossword and coffee
      return (
        <group>
          <Box pos={[x - 0.4, t + 0.008, z]} size={[0.38, 0.012, 0.5]} color="#fbfbf6" />
          <Box pos={[x - 0.4, t + 0.016, z]} size={[0.3, 0.004, 0.4]} color="#9aa0a6" />
          <Mug pos={[x + 0.3, t + 0.06, z]} color="#8b5e34" />
        </group>
      );
    case "darryl_philbin": // radio and clipboard
      return (
        <group>
          <Box pos={[x - 0.5, t + 0.1, z]} size={[0.36, 0.2, 0.14]} color="#2f3b52" />
          <Box pos={[x - 0.38, t + 0.32, z]} size={[0.015, 0.25, 0.015]} color="#999999" />
          <Box pos={[x + 0.3, t + 0.01, z]} size={[0.3, 0.02, 0.4]} color="#c9a97b" />
        </group>
      );
    default:
      return (
        <group>
          <Mug pos={[x - 0.5, t + 0.06, z]} color={color} />
          <Box pos={[x + 0.4, t + 0.03, z]} size={[0.3, 0.06, 0.4]} color="#f4f4ee" />
        </group>
      );
  }
}

/** Keyboard, phone, tray and lamp that every desk has. */
function DeskBasics({ x, z, top, dz }: { x: number; z: number; top: number; dz: number }) {
  return (
    <group>
      <Box pos={[x + 0.05, top + 0.015, z - dz * 0.12]} size={[0.55, 0.03, 0.18]} color="#d9d9d4" />
      <Box pos={[x + 0.75, top + 0.05, z + dz * 0.05]} size={[0.2, 0.1, 0.22]} color="#2d2f36" />
      <Box pos={[x + 0.75, top + 0.11, z + dz * 0.05]} size={[0.14, 0.03, 0.08]} color="#2d2f36" />
      <Box pos={[x - 0.85, top + 0.05, z + dz * 0.2]} size={[0.34, 0.04, 0.26]} color="#6d747c" />
      <Box pos={[x - 0.85, top + 0.1, z + dz * 0.2]} size={[0.34, 0.04, 0.26]} color="#8a919a" />
    </group>
  );
}

function DeskMonitor({ x, y, z, slot, shared }: { x: number; y: number; z: number; slot: number; shared: MutableRefObject<Shared> }) {
  const mat = useRef<THREE.MeshStandardMaterial>(null);
  useFrame((_, dt) => {
    if (!mat.current) return;
    const goal = shared.current.deskWorking[slot] ? 1.4 : 0.08;
    mat.current.emissiveIntensity += (goal - mat.current.emissiveIntensity) * Math.min(1, dt * 4);
  });
  return (
    <group position={[x, y, z]}>
      <mesh>
        <boxGeometry args={[0.8, 0.5, 0.06]} />
        <meshStandardMaterial ref={mat} color="#10131a" emissive="#59c8ff" emissiveIntensity={0.08} />
      </mesh>
      <Box pos={[0, -0.32, 0]} size={[0.12, 0.14, 0.1]} color="#2d2f36" />
      <Box pos={[0, -0.4, 0]} size={[0.34, 0.03, 0.2]} color="#2d2f36" />
    </group>
  );
}

/** Hinged glass-office door that swings open when any agent is heading through it. */
function RoomDoor({ room, shared, color }: { room: RoomId; shared: MutableRefObject<Shared>; color: string }) {
  const d = DOORS[room];
  const ref = useRef<THREE.Group>(null);
  useFrame(({ clock }, dt) => {
    const s = shared.current;
    const target = clock.elapsedTime < s.doorUntil[d.idx] ? 1 : 0;
    s.doorOpen[d.idx] += (target - s.doorOpen[d.idx]) * Math.min(1, dt * 6);
    if (ref.current) ref.current.rotation.y = -s.doorOpen[d.idx] * (Math.PI / 2) * 0.95; // swings outward, toward the bullpen
  });
  return (
    <group ref={ref} position={[d.x - DOOR_W / 2, 0, d.z]}>
      <mesh position={[DOOR_W / 2, 1.05, 0]}>
        <boxGeometry args={[DOOR_W, 2.1, 0.06]} />
        <meshStandardMaterial color={color} roughness={0.35} transparent opacity={0.55} />
      </mesh>
      <Box pos={[DOOR_W / 2, 1.05, 0]} size={[DOOR_W + 0.04, 0.05, 0.08]} color="#59616b" />
      <mesh position={[DOOR_W - 0.15, 1.0, 0.08]}>
        <sphereGeometry args={[0.05, 12, 12]} />
        <meshStandardMaterial color="#e8d28a" metalness={0.8} roughness={0.3} />
      </mesh>
    </group>
  );
}

/** The main entrance: a glass double door that swings inward when anyone is heading through it. */
function MainDoor({ shared }: { shared: MutableRefObject<Shared> }) {
  const left = useRef<THREE.Group>(null);
  const right = useRef<THREE.Group>(null);
  const W = 1.5; // each leaf
  useFrame(({ clock }, dt) => {
    const s = shared.current;
    const target = clock.elapsedTime < s.doorUntil[MAIN_DOOR.idx] ? 1 : 0;
    s.doorOpen[MAIN_DOOR.idx] += (target - s.doorOpen[MAIN_DOOR.idx]) * Math.min(1, dt * 5);
    const a = s.doorOpen[MAIN_DOOR.idx] * (Math.PI / 2) * 0.95;
    if (left.current) left.current.rotation.y = a;
    if (right.current) right.current.rotation.y = -a;
  });
  const leaf = (
    <>
      <mesh position={[W / 2, 1.05, 0]}>
        <boxGeometry args={[W, 2.1, 0.05]} />
        <meshStandardMaterial color="#bcd4ff" transparent opacity={0.35} roughness={0.1} />
      </mesh>
      <Box pos={[W / 2, 2.07, 0]} size={[W, 0.08, 0.08]} color="#59616b" />
      <Box pos={[W / 2, 0.04, 0]} size={[W, 0.08, 0.08]} color="#59616b" />
      <Box pos={[0.03, 1.05, 0]} size={[0.06, 2.1, 0.08]} color="#59616b" />
      <Box pos={[W - 0.03, 1.05, 0]} size={[0.06, 2.1, 0.08]} color="#59616b" />
    </>
  );
  return (
    <group position={[MAIN_DOOR.x, 0, MAIN_DOOR.z]}>
      {/* frame: posts, header, threshold */}
      <Box pos={[-W - 0.08, 1.2, 0]} size={[0.16, 2.4, 0.2]} color="#59616b" />
      <Box pos={[W + 0.08, 1.2, 0]} size={[0.16, 2.4, 0.2]} color="#59616b" />
      <Box pos={[0, 2.45, 0]} size={[W * 2 + 0.32, 0.3, 0.22]} color="#59616b" />
      <Box pos={[0, 0.02, 0]} size={[W * 2, 0.04, 0.3]} color="#9aa3ad" />
      <group ref={left} position={[-W, 0, 0]}>
        {leaf}
      </group>
      <group ref={right} position={[W, 0, 0]} scale={[-1, 1, 1]}>
        {leaf}
      </group>
      <Tag position={[0, 2.95, 0]}>
        <span className="whitespace-nowrap rounded bg-black/60 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">Main entrance</span>
      </Tag>
    </group>
  );
}

function GlassRoomWalls({ x0, x1, doorX, wallZ, backZ }: { x0: number; x1: number; doorX: number; wallZ: number; backZ: number }) {
  const H = 2.4;
  const midZ = (wallZ + backZ) / 2;
  const depth = Math.abs(wallZ - backZ);
  const left = doorX - DOOR_W / 2 - x0;
  const right = x1 - (doorX + DOOR_W / 2);
  return (
    <group>
      <Box glass pos={[x0, H / 2, midZ]} size={[0.08, H, depth]} color="#bcd4ff" />
      <Box glass pos={[x1, H / 2, midZ]} size={[0.08, H, depth]} color="#bcd4ff" />
      <Box glass pos={[x0 + left / 2, H / 2, wallZ]} size={[left, H, 0.08]} color="#bcd4ff" />
      <Box glass pos={[x1 - right / 2, H / 2, wallZ]} size={[right, H, 0.08]} color="#bcd4ff" />
      {/* frame: base rail, mullions and header */}
      <Box pos={[x0 + left / 2, 0.06, wallZ]} size={[left, 0.12, 0.1]} color="#59616b" />
      <Box pos={[x1 - right / 2, 0.06, wallZ]} size={[right, 0.12, 0.1]} color="#59616b" />
      <Box pos={[(x0 + x1) / 2, H + 0.18, wallZ]} size={[x1 - x0, 0.36, 0.1]} color="#59616b" />
      <Box pos={[x0, H, midZ]} size={[0.1, 0.06, depth]} color="#59616b" />
      <Box pos={[x1, H, midZ]} size={[0.1, 0.06, depth]} color="#59616b" />
      {[0.33, 0.66].map((f) => (
        <Box key={f} pos={[x0 + left * f, H / 2, wallZ]} size={[0.05, H, 0.1]} color="#59616b" />
      ))}
      {[0.33, 0.66].map((f) => (
        <Box key={`r${f}`} pos={[x1 - right * f, H / 2, wallZ]} size={[0.05, H, 0.1]} color="#59616b" />
      ))}
    </group>
  );
}

function McpRack({ shared }: { shared: MutableRefObject<Shared> }) {
  const leds = useRef<(THREE.MeshStandardMaterial | null)[]>([]);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const active = t < shared.current.mcpUntil;
    leds.current.forEach((m, i) => {
      if (!m) return;
      m.emissiveIntensity = active ? 1.2 + Math.sin(t * 16 + i * 1.3) : 0.35 + 0.25 * Math.sin(t * 1.8 + i * 1.7);
      m.emissive.set(active ? "#59c8ff" : "#38f27a");
    });
  });
  return (
    <group position={[MCP_RACK[0], 0, MCP_RACK[2]]}>
      <Box pos={[0, 1.2, 0]} size={[1, 2.4, 1.5]} color="#1b1f27" />
      {Array.from({ length: 8 }, (_, i) => (
        <mesh key={i} position={[0.52, 0.5 + i * 0.25, 0]}>
          <boxGeometry args={[0.04, 0.1, 1.2]} />
          <meshStandardMaterial
            ref={(m) => {
              leds.current[i] = m;
            }}
            color="#0a0d12"
            emissive="#38f27a"
            emissiveIntensity={0.4}
          />
        </mesh>
      ))}
      {/* a little glass cage around it, like a real server closet */}
      <Box glass pos={[1.6, 1.2, 0]} size={[0.06, 2.4, 3]} color="#bcd4ff" />
      <Box glass pos={[0.7, 1.2, 1.5]} size={[1.9, 2.4, 0.06]} color="#bcd4ff" />
      <Box glass pos={[0.7, 1.2, -1.5]} size={[1.9, 2.4, 0.06]} color="#bcd4ff" />
      <Tag position={[0.2, 2.9, 0]}>
        <span className="whitespace-nowrap rounded bg-black/55 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-white">🔌 MCP server closet</span>
      </Tag>
    </group>
  );
}

/** Commercial carpet: speckled, with faint tile seams. */
function useCarpet(color: string, repeat: [number, number]) {
  return useMemo(() => {
    const c = document.createElement("canvas");
    c.width = c.height = 256;
    const g = c.getContext("2d");
    if (g) {
      g.fillStyle = color;
      g.fillRect(0, 0, 256, 256);
      let seed = 1234567; // a seeded generator: the same speckle on every render
      const rand = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
      for (let i = 0; i < 5000; i++) {
        g.fillStyle = rand() < 0.5 ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)";
        g.fillRect(rand() * 256, rand() * 256, 2, 2);
      }
      g.strokeStyle = "rgba(0,0,0,0.12)";
      g.lineWidth = 2;
      g.strokeRect(0, 0, 256, 256);
    }
    const t = new THREE.CanvasTexture(c);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(repeat[0], repeat[1]);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }, [color, repeat]);
}

function Window({ x, z, wallFace }: { x: number; z: number; wallFace: "back" }) {
  void wallFace;
  return (
    <group>
      <Box pos={[x, 1.9, z]} size={[2.5, 1.5, 0.05]} color="#a9d4f5" emissive="#6fb7f0" />
      <Box pos={[x, 1.9, z + 0.04]} size={[2.6, 0.06, 0.06]} color="#e9e4d6" />
      <Box pos={[x, 2.65, z + 0.04]} size={[2.6, 0.08, 0.08]} color="#e9e4d6" />
      <Box pos={[x, 1.15, z + 0.04]} size={[2.6, 0.08, 0.08]} color="#e9e4d6" />
      {Array.from({ length: 7 }, (_, i) => (
        <Box key={i} pos={[x, 2.5 - i * 0.1, z + 0.07]} size={[2.4, 0.06, 0.03]} color="#f4f1e8" />
      ))}
    </group>
  );
}

export function Office({ slots, agents, shared }: { slots: Record<string, number>; agents: AgentInfo[]; shared: MutableRefObject<Shared> }) {
  const occupied = useMemo(
    () => agents.filter((a) => a.role !== "head").map((a) => ({ a, i: slots[a.id] ?? 0, s: SLOTS[(slots[a.id] ?? 0) % SLOTS.length] })),
    [agents, slots],
  );
  const carpet = useCarpet("#8f98a8", [FLOOR.w / 2.4, FLOOR.d / 2.4]);
  const rug = useCarpet("#7a859a", [6, 5]);

  return (
    <group>
      {/* ---------- Floors ---------- */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[FLOOR.w, FLOOR.d]} />
        <meshStandardMaterial map={carpet} roughness={1} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[-2, 0.01, 2.6]}>
        <planeGeometry args={[24, 12]} />
        <meshStandardMaterial map={rug} roughness={1} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[12, 0.012, 8]}>
        <planeGeometry args={[9, 5]} />
        <meshStandardMaterial color="#e9e4d4" roughness={0.5} />
      </mesh>
      {/* kitchen tile grid */}
      {Array.from({ length: 8 }, (_, i) => (
        <Box key={`kx${i}`} pos={[7.7 + i * 1.1, 0.02, 8]} size={[0.02, 0.01, 5]} color="#cfc9b8" />
      ))}
      {Array.from({ length: 5 }, (_, i) => (
        <Box key={`kz${i}`} pos={[12, 0.02, 5.7 + i * 1.1]} size={[9, 0.01, 0.02]} color="#cfc9b8" />
      ))}
      {/* walkway outside the main door */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[-10, 0.005, 14.2]}>
        <planeGeometry args={[9, 7.4]} />
        <meshStandardMaterial color="#4b4f58" roughness={1} />
      </mesh>
      <Box pos={[-10, 0.015, 14.2]} size={[3.2, 0.02, 7.4]} color="#6a6f7a" />
      <MainDoor shared={shared} />
      {/* entrance mat */}
      <Box pos={[-9.8, 0.02, 9.6]} size={[3, 0.03, 1.6]} color="#3a3d44" />

      {/* ---------- Walls: wainscot, baseboard, chair rail ---------- */}
      <Box pos={[0, 0.55, -10.65]} size={[36, 1.1, 0.12]} color="#cdbf9f" />
      <Box pos={[0, 2.25, -10.7]} size={[36, 2.3, 0.2]} color="#ebe4d2" />
      <Box pos={[0, 1.12, -10.55]} size={[36, 0.08, 0.1]} color="#f4efe2" />
      <Box pos={[0, 0.07, -10.55]} size={[36, 0.14, 0.1]} color="#8a7f68" />
      <Box pos={[-17.05, 0.55, 0]} size={[0.12, 1.1, 21.4]} color="#c9bb9b" />
      <Box pos={[-17.1, 2.25, 0]} size={[0.2, 2.3, 21.4]} color="#e3dbc7" />
      <Box pos={[-16.95, 1.12, 0]} size={[0.1, 0.08, 21.4]} color="#f4efe2" />
      <Box pos={[-16.95, 0.07, 0]} size={[0.1, 0.14, 21.4]} color="#8a7f68" />
      {[-3.2, 0, 3.2].map((x) => (
        <Window key={x} x={x} z={-10.55} wallFace="back" />
      ))}
      <mesh position={[0, 3.0, -10.5]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.34, 0.34, 0.06, 24]} />
        <meshStandardMaterial color="#f6f6f2" />
      </mesh>
      <Box pos={[0, 3.0, -10.46]} size={[0.03, 0.26, 0.02]} color="#222222" />
      <Box pos={[0.08, 3.0, -10.46]} size={[0.18, 0.03, 0.02]} color="#222222" />
      {/* posters on the left wall */}
      {(
        [
          [-1, "#e6a23c"],
          [2.2, "#5b8def"],
          [5.4, "#e76f6f"],
          [8.6, "#6fcf97"],
        ] as [number, string][]
      ).map(([z, c]) => (
        <group key={z}>
          <Box pos={[-16.95, 2.0, z]} size={[0.05, 1.05, 0.8]} color="#f4f1e8" />
          <Box pos={[-16.92, 2.0, z]} size={[0.05, 0.85, 0.6]} color={c} />
        </group>
      ))}
      {/* corkboard of memos by the accounting corner */}
      <Box pos={[-16.95, 1.9, -5]} size={[0.06, 1.0, 2]} color="#b98a56" />
      {[-5.6, -5.1, -4.6, -4.2].map((z, i) => (
        <Box key={z} pos={[-16.9, 1.9 + (i % 2) * 0.2 - 0.1, z]} size={[0.04, 0.3, 0.3]} color={["#f4f4ee", "#f6e58d", "#f4f4ee", "#a9d4f5"][i]} />
      ))}

      {/* ---------- Michael's glass office ---------- */}
      <GlassRoomWalls x0={6} x1={15} doorX={DOORS.office.x} wallZ={-5} backZ={-10.4} />
      <RoomDoor room="office" shared={shared} color="#c9a227" />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[10.5, 0.015, -7.7]}>
        <planeGeometry args={[8.6, 5]} />
        <meshStandardMaterial color="#6b5b8a" roughness={1} />
      </mesh>
      <Box pos={[11.5, 0.4, -7.4]} size={[3.2, 0.8, 1]} color="#6b4a2f" />
      <Box pos={[11.5, 0.82, -7.4]} size={[3.3, 0.05, 1.1]} color="#7a5638" />
      <Box pos={[11.5, 1.1, -7.0]} size={[0.9, 0.5, 0.06]} color="#10131a" emissive="#59c8ff" />
      <Box pos={[10.2, 0.88, -7.7]} size={[0.8, 0.06, 0.2]} color="#f5c518" />
      <Cyl pos={[12.6, 0.98, -7.5]} r={0.1} h={0.36} rr={0.16} color="#f5c518" />
      <mesh position={[12.6, 1.25, -7.5]}>
        <sphereGeometry args={[0.13, 14, 14]} />
        <meshStandardMaterial color="#f5c518" metalness={0.8} roughness={0.3} />
      </mesh>
      <Box pos={[9.7, 0.95, -7.3]} size={[0.3, 0.24, 0.03]} color="#e8e0d0" />
      <Mug pos={[11.0, 0.92, -7.55]} color="#f5c518" />
      <OfficeChair x={11.5} z={-8.55} yaw={0} color="#5b3d2a" />
      <OfficeChair x={12.3} z={-6.1} yaw={Math.PI} color="#7a8aa5" />
      <OfficeChair x={13.5} z={-6.1} yaw={Math.PI} color="#7a8aa5" />
      {/* bookshelf with colorful binders along the east wall */}
      <Box pos={[14.7, 1, -9]} size={[0.6, 2, 2.6]} color="#5b4630" />
      {Array.from({ length: 10 }, (_, i) => (
        <Box key={i} pos={[14.45, 0.55 + (i % 3) * 0.6, -10.1 + Math.floor(i / 3) * 0.55]} size={[0.2, 0.4, 0.14]} color={["#b02a37", "#2f4d8f", "#2a9d8f", "#c9a227", "#6d5acd"][i % 5]} />
      ))}
      <Box pos={[7.2, 0.3, -9]} size={[1.2, 0.6, 2.2]} color="#7a6aa8" />
      <Box pos={[6.75, 0.75, -9]} size={[0.3, 0.6, 2.2]} color="#6a5a98" />
      <Box pos={[9.4, 0.18, -9.5]} size={[1.2, 0.36, 0.7]} color="#6b4a2f" />
      <Plant pos={[14.4, -5.6]} big />
      <Tag position={[11.5, 2.7, -5]}>
        <span className="whitespace-nowrap rounded bg-[#c9a227] px-2 py-0.5 text-[11px] font-semibold text-black shadow">Regional Manager</span>
      </Tag>

      {/* ---------- Conference room (the focus room) ---------- */}
      <GlassRoomWalls x0={-15} x1={-5} doorX={DOORS.conf.x} wallZ={-5} backZ={-10.4} />
      <RoomDoor room="conf" shared={shared} color="#7fa6d9" />
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <group key={i}>
          <Box pos={[-12.8, 1.0 + i * 0.22, -5.04]} size={[4.3, 0.05, 0.07]} color="#f1efe8" />
          <Box pos={[-7.2, 1.0 + i * 0.22, -5.04]} size={[4.3, 0.05, 0.07]} color="#f1efe8" />
        </group>
      ))}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[-10, 0.015, -7.7]}>
        <planeGeometry args={[9.6, 5]} />
        <meshStandardMaterial color="#586a86" roughness={1} />
      </mesh>
      <Box pos={[-10, 0.78, -7.5]} size={[6.4, 0.12, 1.6]} color="#7b5a3a" />
      <Box pos={[-12.6, 0.38, -7.5]} size={[0.2, 0.76, 1.2]} color="#4a3826" />
      <Box pos={[-7.4, 0.38, -7.5]} size={[0.2, 0.76, 1.2]} color="#4a3826" />
      <Box pos={[-10, 0.86, -7.5]} size={[0.3, 0.06, 0.3]} color="#2d2f36" />
      <Box pos={[-10, 2.0, -10.3]} size={[4.4, 1.6, 0.06]} color="#fafafa" />
      <Box pos={[-10, 1.18, -10.25]} size={[4.4, 0.06, 0.1]} color="#8f959c" />
      {[-11.5, -10.4, -8.8, -7.9].map((x, i) => (
        <Box key={x} pos={[x, 1.9 + (i % 2) * 0.3, -10.26]} size={[0.8, 0.05, 0.02]} color={["#2f4d8f", "#b02a37", "#2a9d8f", "#2f4d8f"][i]} />
      ))}
      {[-12.25, -10.75, -9.25, -7.75].map((x) => (
        <group key={x}>
          <OfficeChair x={x} z={-5.9} yaw={Math.PI} />
          <OfficeChair x={x} z={-9.1} yaw={0} />
        </group>
      ))}
      <Tag position={[-10, 2.7, -5]}>
        <span className="whitespace-nowrap rounded bg-[#7fa6d9] px-2 py-0.5 text-[11px] font-semibold text-black shadow">Conference · focus room</span>
      </Tag>

      {/* ---------- Cubicle partitions ---------- */}
      <Partition pos={[0.2, 0.6, 3.6]} size={[0.1, 1.2, 5.6]} />
      <Partition pos={[4.1, 0.6, 5.1]} size={[0.1, 1.2, 2.4]} />
      <Partition pos={[2, 0.6, 0.55]} size={[3.8, 1.2, 0.1]} />
      <Partition pos={[-11.2, 0.6, 1.6]} size={[0.1, 1.2, 5]} />
      <Partition pos={[-8, 0.6, -1.0]} size={[6.6, 1.2, 0.1]} />
      <Partition pos={[-4.9, 0.6, 2.2]} size={[0.1, 1.2, 2.2]} />

      {/* ---------- Desks, chairs and personal props ---------- */}
      {occupied.map(({ a, s, i }) => {
        const dz = Math.cos(s.yaw);
        const deskZ = s.z + dz * (s.reception ? 1.2 : 1.0);
        const top = s.reception ? 1.1 : 0.8;
        return (
          <group key={a.id}>
            <Box pos={[s.x, s.reception ? 0.55 : 0.4, deskZ]} size={s.reception ? [3.2, 1.1, 1.2] : [2, 0.8, 0.9]} color={s.reception ? "#b98d5c" : "#c9a97b"} />
            <Box pos={[s.x, top + 0.02, deskZ]} size={s.reception ? [3.3, 0.05, 1.3] : [2.1, 0.05, 1.0]} color={s.reception ? "#9d7647" : "#b8956a"} />
            <DeskMonitor x={s.x} y={s.reception ? 1.35 : 1.1} z={deskZ + dz * 0.2} slot={i} shared={shared} />
            <DeskBasics x={s.x} z={deskZ} top={top + 0.045} dz={dz} />
            <Box pos={[s.x - 0.7, top + 0.07, deskZ - dz * 0.2]} size={[0.5, 0.04, 0.35]} color="#f4f4ee" />
            <mesh position={[s.x + 0.45, top + 0.12, deskZ]}>
              <cylinderGeometry args={[0.07, 0.07, 0.16, 10]} />
              <meshStandardMaterial color={a.color} />
            </mesh>
            <DeskDecor id={a.id} x={s.x} z={deskZ} top={top + 0.05} color={a.color} />
            {!s.reception && <OfficeChair x={s.x} z={s.z - dz * 0.95} yaw={s.yaw} />}
            {s.reception && <OfficeChair x={s.x} z={s.z - 0.7} yaw={0} color="#7a3f4a" />}
          </group>
        );
      })}

      {/* ---------- Reception waiting area ---------- */}
      <Box pos={[-11.8, 0.2, 8.9]} size={[0.8, 0.4, 1.4]} color="#8a6a4a" />
      <Box pos={[-11.8, 0.43, 8.6]} size={[0.5, 0.04, 0.35]} color="#e63946" />
      <Box pos={[-11.8, 0.43, 9.1]} size={[0.5, 0.04, 0.35]} color="#2f4d8f" />
      <Box pos={[-13.4, 0.3, 8.6]} size={[0.9, 0.6, 0.9]} color="#3f6f9b" />
      <Box pos={[-13.9, 0.72, 8.6]} size={[0.2, 0.5, 0.9]} color="#355d83" />
      <Box pos={[-13.4, 0.3, 9.9]} size={[0.9, 0.6, 0.9]} color="#3f6f9b" />
      <Box pos={[-13.9, 0.72, 9.9]} size={[0.2, 0.5, 0.9]} color="#355d83" />
      <Plant pos={[-14.8, 7.2]} />

      {/* ---------- Copier and supply corner ---------- */}
      <Box pos={[0, 0.5, -9.9]} size={[1.5, 1, 0.8]} color="#cfd3d8" />
      <Box pos={[0, 1.05, -9.9]} size={[1.5, 0.1, 0.8]} color="#8f959c" />
      <Box pos={[0.1, 1.15, -9.9]} size={[0.5, 0.04, 0.4]} color="#f4f4ee" />
      <Box pos={[-1.3, 0.35, -9.8]} size={[0.5, 0.7, 0.5]} color="#2d2f36" />
      <Box pos={[1.4, 0.3, -9.8]} size={[0.5, 0.6, 0.5]} color="#3f6f9b" />
      {[-2.2, -3.0, 2.2, 3.0].map((x) => (
        <Box key={x} pos={[x, 0.7, -10]} size={[0.7, 1.4, 0.7]} color="#8d99a6" />
      ))}
      {[-2.2, 3.0].map((x) => (
        <Box key={`p${x}`} pos={[x, 1.55, -10]} size={[0.5, 0.3, 0.4]} color="#f4f4ee" />
      ))}

      {/* ---------- Kitchen / break room ---------- */}
      <Box pos={[12, 0.45, 9.9]} size={[5, 0.9, 0.9]} color="#d8d2c2" />
      <Box pos={[12, 0.92, 9.9]} size={[5.1, 0.05, 1]} color="#b9b2a0" />
      <Box pos={[12, 2.4, 10.25]} size={[4.6, 0.9, 0.5]} color="#8a6a4a" />
      {[10, 11.2, 12.4, 13.6].map((x) => (
        <Box key={x} pos={[x, 2.4, 9.99]} size={[1.1, 0.8, 0.03]} color="#9a7a58" />
      ))}
      <Box pos={[11.4, 0.96, 9.9]} size={[0.8, 0.04, 0.5]} color="#9aa3ad" />
      <Box pos={[10.2, 1.12, 9.9]} size={[0.6, 0.34, 0.45]} color="#d9d9d4" />
      <Box pos={[13.2, 1.15, 9.9]} size={[0.3, 0.4, 0.3]} color="#1b1b1f" />
      <Box pos={[13.2, 1.3, 9.76]} size={[0.06, 0.06, 0.02]} color="#e63946" emissive="#e63946" />
      <Box pos={[14.7, 1, 9.9]} size={[0.9, 2, 0.9]} color="#f2f2f2" />
      <Box pos={[14.7, 1.4, 9.43]} size={[0.06, 0.5, 0.04]} color="#b9b9b9" />
      <Box pos={[9, 1, 9.9]} size={[1, 2, 0.9]} color="#c0392b" emissive="#ff5a4a" />
      <Box pos={[9, 1.2, 9.42]} size={[0.7, 0.8, 0.04]} color="#f6e6c8" />
      <Cyl pos={[9.5, 0.7, 6.4]} r={0.9} h={0.08} color="#e9d9b7" />
      <Cyl pos={[9.5, 0.35, 6.4]} r={0.07} h={0.7} color="#555555" />
      <Stool x={8.5} z={6.4} />
      <Stool x={10.5} z={6.4} />
      <Stool x={9.5} z={5.4} />
      <Stool x={9.5} z={7.4} />
      <Cyl pos={[15.6, 0.4, 7.6]} r={0.3} h={0.8} rr={0.25} color="#5d6671" />
      <Plant pos={[16, 6]} />

      {/* ---------- Water cooler by the sales pod ---------- */}
      <Cyl pos={[8.6, 0.55, -1.4]} r={0.3} h={1.1} color="#f0f3f5" />
      <mesh position={[8.6, 1.3, -1.4]}>
        <cylinderGeometry args={[0.26, 0.26, 0.5, 20]} />
        <meshStandardMaterial color="#8fc7e8" transparent opacity={0.7} />
      </mesh>

      {/* ---------- Warehouse ---------- */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[14, 0.012, 0]}>
        <planeGeometry args={[5.5, 9]} />
        <meshStandardMaterial color="#b9b5ad" roughness={1} />
      </mesh>
      <Box pos={[11.3, 0.02, 0]} size={[0.12, 0.02, 9]} color="#f2c200" />
      {/* roll-up door on the east wall */}
      <Box pos={[17.05, 1.5, 0]} size={[0.15, 3, 5.4]} color="#9aa3ad" />
      {Array.from({ length: 10 }, (_, i) => (
        <Box key={i} pos={[16.95, 0.15 + i * 0.3, 0]} size={[0.08, 0.04, 5.2]} color="#7f8993" />
      ))}
      <Box pos={[16.9, 3.05, 0]} size={[0.2, 0.2, 5.6]} color="#f2c200" />
      {/* shelving racks with boxes */}
      {[-3.5, 3.5].map((z) => (
        <group key={z}>
          <Box pos={[15.4, 1.1, z]} size={[1.0, 0.06, 2.4]} color="#d9822b" />
          <Box pos={[15.4, 1.9, z]} size={[1.0, 0.06, 2.4]} color="#d9822b" />
          <Box pos={[14.9, 1.1, z - 1.1]} size={[0.08, 2.2, 0.08]} color="#3f6f9b" />
          <Box pos={[14.9, 1.1, z + 1.1]} size={[0.08, 2.2, 0.08]} color="#3f6f9b" />
          <Box pos={[15.9, 1.1, z - 1.1]} size={[0.08, 2.2, 0.08]} color="#3f6f9b" />
          <Box pos={[15.9, 1.1, z + 1.1]} size={[0.08, 2.2, 0.08]} color="#3f6f9b" />
          <Box pos={[15.4, 1.35, z - 0.5]} size={[0.8, 0.5, 0.7]} color="#b98a56" />
          <Box pos={[15.4, 1.35, z + 0.5]} size={[0.8, 0.5, 0.7]} color="#c89a63" />
          <Box pos={[15.4, 2.15, z]} size={[0.8, 0.5, 1.2]} color="#b98a56" />
        </group>
      ))}
      {([[12.4, 0.35, -2.8], [12.4, 0.35, -1.9], [12.2, 0.35, 3], [13.4, 0.35, 3.3], [12.8, 1.05, 3.1]] as Vec3[]).map(([x, y, z]) => (
        <Box key={`${x},${y},${z}`} pos={[x, y, z]} size={[0.9, 0.7, 0.9]} color="#b98a56" />
      ))}
      <Box pos={[13.8, 0.06, -3]} size={[1.3, 0.12, 1.3]} color="#8a6a3f" />
      <Box pos={[13.8, 0.4, -3]} size={[1.0, 0.55, 1.0]} color="#c89a63" />
      <Tag position={[14, 0.05, 4.2]}>
        <span className="whitespace-nowrap rounded bg-black/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-white">Warehouse</span>
      </Tag>

      {/* ---------- Labels ---------- */}
      <Tag position={[-6.5, 0.05, 9.6]}>
        <span className="whitespace-nowrap rounded bg-black/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-white">Reception</span>
      </Tag>
      <Tag position={[12, 0.05, 6.0]}>
        <span className="whitespace-nowrap rounded bg-black/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-white">Kitchen</span>
      </Tag>

      {/* ---------- MCP server closet ---------- */}
      <McpRack shared={shared} />

      {(
        [
          [-16, -4.2],
          [4.6, -9.8],
          [-4.6, -9.8],
          [-16, 9.8],
          [5, 9.8],
          [-3, 4.2],
          [8.2, 1.2],
        ] as [number, number][]
      ).map(([x, z]) => (
        <Plant key={`${x},${z}`} pos={[x, z]} />
      ))}
    </group>
  );
}
