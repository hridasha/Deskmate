"use client";

import { Suspense, useEffect, useMemo, useRef, useState, type ElementRef, type MutableRefObject } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { ContactShadows, Environment, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { AgentState } from "../CharacterStage";
import { Costume, useDressed } from "./Character";
import { Office, type Shared } from "./OfficeSet";
import { Tag } from "./Tag";
import { lookFor, type Look } from "./looks";
import { plainText } from "@/lib/text";
import {
  AMBIENT_STATIONS,
  DOORS,
  MAIN_DOOR,
  MCP_RACK,
  SLOTS,
  STATIONS,
  assignSlots,
  zoneOf,
  type CameraView,
  confSpot,
  deskSpot,
  doorPoints,
  officeSpot,
  type RoomId,
  type Spot,
} from "./config";

const WALK_SPEED = 1.6;
const RUN_SPEED = 2.8;

export type AgentInfo = { id: string; name: string; color: string; role?: string; look?: Look };
export type Live = { state: AgentState; emotion: string; tool?: string; kind?: string };
export type Interaction = { fromId: string; toId: string; task: string; reply?: string } | null;

const REACTION: Record<string, string> = { happy: "ThumbsUp", annoyed: "No", surprised: "Jump", thinking: "Standing" };
const pick = <T,>(xs: T[]) => xs[Math.floor(Math.random() * xs.length)];
const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

type Location = "outside" | RoomId;
type Waypoint = { v: THREE.Vector3; tag?: Location };

function Agent({
  info,
  all,
  slots,
  live,
  interaction,
  ambient,
  selected,
  fresh,
  leaving,
  onLeft,
  onSelect,
  onFocus,
  shared,
}: {
  info: AgentInfo;
  all: AgentInfo[];
  slots: Record<string, number>;
  live: Live;
  interaction: Interaction;
  ambient: boolean;
  selected: boolean;
  fresh: boolean;
  leaving: boolean;
  onLeft: (id: string) => void;
  onSelect: () => void;
  onFocus: () => void;
  shared: MutableRefObject<Shared>;
}) {
  const isHead = info.role === "head";
  const s = isHead ? 0.5 : 0.4;
  const look = useMemo(() => lookFor(info.id, info.color, info.look), [info.id, info.color, info.look]);
  const { scene, actions } = useDressed(info.color);

  const slot = slots[info.id] ?? 0;
  const workers = useMemo(() => all.filter((a) => a.role !== "head"), [all]);
  const role: "from" | "to" | null = interaction ? (interaction.fromId === info.id ? "from" : interaction.toId === info.id ? "to" : null) : null;

  const group = useRef<THREE.Group>(null);
  const plan = useRef({
    dest: null as Spot | null,
    path: [] as Waypoint[],
    phase: "moving" as "moving" | "working",
    location: "outside" as Location,
    until: 0,
    activity: "",
    clip: "Idle",
    uiKey: "",
  });
  const latest = useRef({ live, interaction, role, ambient, leaving });
  latest.current = { live, interaction, role, ambient, leaving };
  const leftRef = useRef(false);
  const onLeftRef = useRef(onLeft);
  onLeftRef.current = onLeft;
  const [clip, setClip] = useState("Idle");
  const [ui, setUi] = useState({ label: "", badge: "", speech: "" });
  const [hovered, setHovered] = useState(false);

  const home: Spot = isHead ? officeSpot() : deskSpot(slot);

  /** A spot beside another agent's desk, facing them. */
  function besideDesk(workerId: string, label: string): Spot {
    const s = SLOTS[(slots[workerId] ?? 0) % SLOTS.length];
    const right = s.x < 5;
    return {
      pos: [s.x + (right ? 1.6 : -1.6), s.z],
      yaw: right ? -Math.PI / 2 : Math.PI / 2, // face the worker
      clip: "Yes",
      activities: [label],
      badge: "💬",
    };
  }

  /** Where this agent should be right now, given real state, interactions and ambient behavior. */
  function decide(): Spot {
    const { live: lv, interaction: it, role: r } = latest.current;
    if (latest.current.leaving) return { pos: [MAIN_DOOR.x, 8.8], yaw: Math.PI, clip: "Wave", activities: ["Heading out…"], badge: "👋" };
    if (it && r === "from") return besideDesk(it.toId, "Briefing…");
    if (it && r === "to") {
      const right = SLOTS[slot % SLOTS.length].x < 5;
      return { ...deskSpot(slot), yaw: right ? Math.PI / 2 : -Math.PI / 2, activities: ["Listening…"], badge: "💬" };
    }
    if (lv.state !== "idle") {
      if (lv.kind === "mcp") return STATIONS.mcp;
      if (lv.kind === "knowledge") return STATIONS.archive;
      return isHead ? officeSpot() : confSpot(slot);
    }
    // Ambient "everyone is busy" behavior.
    const cur = plan.current.dest;
    for (let i = 0; i < 4; i++) {
      const r2 = Math.random();
      let next: Spot;
      if (isHead) {
        const target = workers.length ? pick(workers) : null;
        next = r2 < 0.5 || !target ? officeSpot() : { ...besideDesk(target.id, `Checking in on ${target.name}…`), clip: "Wave", badge: "👋" };
      } else if (r2 < 0.55) next = home;
      else if (r2 < 0.72) next = confSpot(slot);
      else {
        const ids = AMBIENT_STATIONS.filter((s) => !(info.id === "pam_beesly" && s === "reception"));
        next = STATIONS[pick(ids)];
      }
      if (!cur || cur.pos[0] !== next.pos[0] || cur.pos[1] !== next.pos[1]) return next;
    }
    return home;
  }

  function buildPath(dest: Spot): Waypoint[] {
    const p = plan.current;
    const v = (x: number, z: number) => new THREE.Vector3(x, 0, z);
    const path: Waypoint[] = [];
    const destLoc: Location = dest.room ?? "outside";
    if (p.location !== destLoc) {
      if (p.location !== "outside") {
        const d = doorPoints(p.location);
        path.push({ v: v(...d.doorway) }, { v: v(...d.approach), tag: "outside" });
      }
      if (dest.room) {
        const d = doorPoints(dest.room);
        path.push({ v: v(...d.approach) }, { v: v(...d.doorway) }, { v: v(...d.inside), tag: dest.room });
      }
    }
    dest.via?.forEach(([x, z]) => path.push({ v: v(x, z) }));
    path.push({ v: v(...dest.pos) });
    return path;
  }

  function goTo(dest: Spot) {
    const p = plan.current;
    p.dest = dest;
    p.path = buildPath(dest);
    p.phase = "moving";
    p.activity = pick(dest.activities);
  }

  // Place the agent: at its desk (or office), or at the entrance if freshly created.
  useEffect(() => {
    const g = group.current;
    if (!g) return;
    const start = fresh ? ([MAIN_DOOR.x, 14.5] as [number, number]) : home.pos;
    g.position.set(start[0], 0, start[1]);
    shared.current.groups[info.id] = g;
    plan.current.location = !fresh && isHead ? "office" : "outside";
    goTo(fresh ? home : decide());
    if (fresh) {
      // New hires walk in through the main door, over the mat, then to their desk.
      const v = (x: number, z: number) => new THREE.Vector3(x, 0, z);
      plan.current.path = [{ v: v(MAIN_DOOR.x, 12.2) }, { v: v(MAIN_DOOR.x, MAIN_DOOR.z) }, { v: v(MAIN_DOOR.x, 8.8) }, ...plan.current.path];
    }
    return () => {
      shared.current.groups[info.id] = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Someone being removed walks to the main door and out, then asks to be deleted.
  useEffect(() => {
    if (!leaving) return;
    const p = plan.current;
    const v = (x: number, z: number) => new THREE.Vector3(x, 0, z);
    goTo(decide());
    p.path = [...p.path, { v: v(MAIN_DOOR.x, MAIN_DOOR.z) }, { v: v(MAIN_DOOR.x, 12.4) }, { v: v(MAIN_DOOR.x, 15.5) }];
    p.until = Infinity;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leaving]);

  // Real work, interactions and MCP calls override the ambient loop.
  useEffect(() => {
    if (latest.current.leaving) return;
    const p = plan.current;
    const busy = live.state !== "idle" || role !== null;
    goTo(decide());
    p.until = busy ? Infinity : 0; // when done: finish the action, then resume ambient work
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.state, live.kind, role, interaction?.toId]);

  useFrame(({ clock }, dt) => {
    const g = group.current;
    if (!g) return;
    const p = plan.current;
    const { live: lv, interaction: it, role: r, ambient: amb } = latest.current;
    const now = clock.elapsedTime;
    const busy = lv.state !== "idle" || r !== null;

    if (p.phase === "working" && !busy && now > p.until) {
      if (amb) goTo(decide());
      else p.until = now + 3;
    }

    let targetYaw = p.dest?.yaw ?? 0;
    if (p.phase === "moving") {
      const wp = p.path[0];
      if (!wp) {
        p.phase = "working";
        p.until = busy || latest.current.leaving ? Infinity : now + 5 + Math.random() * 7;
        if (latest.current.leaving && !leftRef.current) {
          leftRef.current = true;
          onLeftRef.current(info.id);
        }
      } else {
        const dx = wp.v.x - g.position.x;
        const dz = wp.v.z - g.position.z;
        const dist = Math.hypot(dx, dz);
        if (dist < 0.12) {
          if (wp.tag) p.location = wp.tag;
          p.path.shift();
        } else {
          const step = Math.min(dist, (lv.state === "tool_call" ? RUN_SPEED : WALK_SPEED) * dt);
          g.position.x += (dx / dist) * step;
          g.position.z += (dz / dist) * step;
          targetYaw = Math.atan2(dx, dz);
        }
      }
    }
    let diff = targetYaw - g.rotation.y;
    diff = Math.atan2(Math.sin(diff), Math.cos(diff));
    g.rotation.y += diff * Math.min(1, dt * 8);

    // Agents open doors for themselves (and anyone else nearby) as they pass.
    (Object.keys(DOORS) as RoomId[]).forEach((room) => {
      const d = DOORS[room];
      if (p.phase === "moving" && Math.hypot(g.position.x - d.x, g.position.z - d.z) < 2.2) {
        shared.current.doorUntil[d.idx] = Math.max(shared.current.doorUntil[d.idx], now + 0.5);
      }
    });
    if (p.phase === "moving" && Math.hypot(g.position.x - MAIN_DOOR.x, g.position.z - MAIN_DOOR.z) < 2.6) {
      shared.current.doorUntil[MAIN_DOOR.idx] = Math.max(shared.current.doorUntil[MAIN_DOOR.idx], now + 0.5);
    }
    const atDesk = p.phase === "working" && p.location === "outside" && p.dest?.badge === "💻";
    if (!isHead) shared.current.deskWorking[slot] = atDesk;
    if (p.phase === "working" && p.dest === STATIONS.mcp) shared.current.mcpUntil = now + 0.5;

    // Animation clip.
    let nextClip: string;
    const replied = !!it?.reply;
    if (p.phase === "moving") nextClip = lv.state === "tool_call" ? "Running" : "Walking";
    else if (r === "to") nextClip = replied ? "Wave" : "Yes";
    else if (r === "from") nextClip = replied ? "ThumbsUp" : "Yes";
    else if (lv.state === "speaking") nextClip = REACTION[lv.emotion] ?? "Wave";
    else if (lv.state === "error") nextClip = "Death";
    else if (lv.state === "thinking" || lv.state === "tool_call") nextClip = "Yes";
    else nextClip = amb ? p.dest?.clip ?? "Idle" : "Idle";
    if (nextClip !== p.clip) {
      p.clip = nextClip;
      setClip(nextClip);
    }

    // Label, badge and speech bubble.
    const nameOf = (id: string) => all.find((a) => a.id === id)?.name ?? id;
    let label: string;
    if (r === "from" && it) label = `Briefing ${nameOf(it.toId)}…`;
    else if (r === "to" && it) label = `Listening to ${nameOf(it.fromId)}…`;
    else if (lv.state === "thinking") label = "Thinking…";
    else if (lv.state === "tool_call") label = lv.kind === "mcp" ? `MCP tool: ${lv.tool}` : lv.kind === "knowledge" ? (lv.tool === "search_knowledge" ? "Searching the documents…" : "Querying the database…") : `Using ${lv.tool}…`;
    else if (lv.state === "speaking") label = "Talking…";
    else if (lv.state === "error") label = "Something broke";
    else label = p.phase === "moving" ? "On my way…" : amb ? p.activity : "Idle";
    const badge = r ? "💬" : lv.kind === "mcp" && lv.state !== "idle" ? "🔌" : lv.kind === "knowledge" && lv.state !== "idle" ? "📚" : lv.state !== "idle" && !isHead ? "🔒" : p.dest?.badge ?? "";
    let speech = "";
    if (r === "from" && it) speech = `“${trunc(it.task, 70)}”`;
    if (r === "to" && it) speech = it.reply ? trunc(plainText(it.reply), 110) : "…";
    shared.current.info[info.id] = { label, moving: p.phase === "moving" };
    const key = `${label}|${badge}|${speech}`;
    if (key !== p.uiKey) {
      p.uiKey = key;
      setUi({ label, badge, speech });
    }
  });

  useEffect(() => {
    const a = actions[clip] ?? actions["Idle"];
    a?.reset().fadeIn(0.25).play();
    return () => {
      a?.fadeOut(0.25);
    };
  }, [clip, actions]);

  const detailed = selected || hovered || live.state !== "idle" || role !== null;

  return (
    <group
      ref={group}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      onDoubleClick={(e) => {
        e.stopPropagation();
        onFocus();
      }}
      onPointerOver={() => {
        setHovered(true);
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={() => {
        setHovered(false);
        document.body.style.cursor = "";
      }}
    >
      <primitive object={scene} scale={s} />
      <Costume scene={scene} look={look} s={s} />
      {selected && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
          <ringGeometry args={[0.55, 0.7, 40]} />
          <meshBasicMaterial color={info.color} />
        </mesh>
      )}
      {isHead && <HeadStar />}
      <Tag position={[0, isHead ? 3.5 : 2.4, 0]} front>
        <div className="flex flex-col items-center gap-1 whitespace-nowrap">
          {ui.speech && (
            <div className="max-w-[190px] whitespace-normal rounded-xl bg-white px-2.5 py-1.5 text-center text-[11px] leading-snug text-black shadow-lg">{ui.speech}</div>
          )}
          <span className="flex items-center gap-1 rounded-full bg-black/75 px-2 py-0.5 text-[11px] font-semibold text-white">
            {ui.badge && <span>{ui.badge}</span>}
            {info.name}
          </span>
          {detailed && ui.label && <span className="rounded-full bg-white/90 px-2 py-0.5 text-[10px] text-black shadow">{ui.label}</span>}
        </div>
      </Tag>
    </group>
  );
}

/** Floating gold marker that sets the Head apart. */
function HeadStar() {
  const ref = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    ref.current.rotation.y = clock.elapsedTime * 1.5;
    ref.current.position.y = 2.95 + Math.sin(clock.elapsedTime * 2) * 0.08;
  });
  return (
    <mesh ref={ref} position={[0, 2.95, 0]}>
      <octahedronGeometry args={[0.2]} />
      <meshStandardMaterial color="#f5c518" emissive="#f5a300" emissiveIntensity={0.7} metalness={0.8} roughness={0.25} />
    </mesh>
  );
}

/** Glowing line between two points (interaction, MCP call). */
function Beam({ getA, getB, color }: { getA: () => THREE.Vector3 | null; getB: () => THREE.Vector3 | null; color: string }) {
  const mesh = useRef<THREE.Mesh>(null);
  const t = useMemo(() => ({ a: new THREE.Vector3(), b: new THREE.Vector3(), d: new THREE.Vector3(), up: new THREE.Vector3(0, 1, 0) }), []);
  useFrame(({ clock }) => {
    const m = mesh.current;
    if (!m) return;
    const a = getA();
    const b = getB();
    if (!a || !b) {
      m.visible = false;
      return;
    }
    t.a.copy(a).setY(1.4);
    t.b.copy(b).setY(1.4);
    t.d.subVectors(t.b, t.a);
    const len = t.d.length();
    if (len < 0.01) {
      m.visible = false;
      return;
    }
    m.visible = true;
    m.position.addVectors(t.a, t.b).multiplyScalar(0.5);
    m.scale.set(1, len, 1);
    m.quaternion.setFromUnitVectors(t.up, t.d.normalize());
    (m.material as THREE.MeshBasicMaterial).opacity = 0.45 + 0.3 * Math.sin(clock.elapsedTime * 6);
  });
  return (
    <mesh ref={mesh}>
      <cylinderGeometry args={[0.04, 0.04, 1, 8]} />
      <meshBasicMaterial color={color} transparent opacity={0.6} />
    </mesh>
  );
}

/** Flies the camera to a requested view, or follows an agent. Any manual drag cancels the fly-to. */
function CameraRig({
  view,
  followId,
  shared,
  controls,
}: {
  view: CameraView | null;
  followId: string | null;
  shared: MutableRefObject<Shared>;
  controls: MutableRefObject<ElementRef<typeof OrbitControls> | null>;
}) {
  const { camera } = useThree();
  const goal = useRef<{ pos: THREE.Vector3; target: THREE.Vector3 } | null>(null);
  const tmp = useMemo(() => ({ off: new THREE.Vector3(), t: new THREE.Vector3() }), []);

  useEffect(() => {
    if (view) goal.current = { pos: new THREE.Vector3(...view.pos), target: new THREE.Vector3(...view.target) };
  }, [view]);

  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    const cancel = () => {
      goal.current = null;
    };
    c.addEventListener("start", cancel);
    return () => c.removeEventListener("start", cancel);
  }, [controls]);

  useFrame((_, dt) => {
    const c = controls.current;
    if (!c) return;
    const k = 1 - Math.exp(-dt * 4);
    if (followId) {
      const g = shared.current.groups[followId];
      if (g) {
        tmp.off.subVectors(camera.position, c.target);
        tmp.t.set(g.position.x, 1.0, g.position.z);
        c.target.lerp(tmp.t, k);
        camera.position.copy(c.target).add(tmp.off);
        c.update();
      }
      return;
    }
    const gl = goal.current;
    if (!gl) return;
    camera.position.lerp(gl.pos, k);
    c.target.lerp(gl.target, k);
    c.update();
    if (camera.position.distanceTo(gl.pos) < 0.05 && c.target.distanceTo(gl.target) < 0.05) goal.current = null;
  });
  return null;
}

export default function StudioScene({
  agents,
  live,
  interaction,
  ambient,
  selectedId,
  freshIds,
  leavingIds,
  view,
  followId,
  onSelect,
  onFocusAgent,
  onAgentLeft,
  onPresence,
}: {
  agents: AgentInfo[];
  live: Record<string, Live>;
  interaction: Interaction;
  ambient: boolean;
  selectedId: string | null;
  freshIds: string[];
  leavingIds: string[];
  view: CameraView | null;
  followId: string | null;
  onSelect: (id: string) => void;
  onFocusAgent: (id: string) => void;
  onAgentLeft: (id: string) => void;
  onPresence?: (items: { id: string; zone: string; activity: string; at_desk: boolean }[]) => void;
}) {
  const controls = useRef<ElementRef<typeof OrbitControls> | null>(null);
  const shared = useRef<Shared>({ doorOpen: [0, 0, 0], doorUntil: [0, 0, 0], deskWorking: Array(SLOTS.length).fill(false), mcpUntil: 0, groups: {}, info: {} });
  const slots = useMemo(() => assignSlots(agents.filter((a) => a.role !== "head").map((a) => a.id)), [agents]);
  // Tell the server where everyone is and what they appear to be doing, so the Head can answer "where is Oscar?".
  useEffect(() => {
    if (!onPresence) return;
    const t = setInterval(() => {
      if (document.hidden) return;
      const items = [];
      for (const a of agents) {
        const g = shared.current.groups[a.id];
        if (!g) continue;
        const own = a.role === "head" ? officeSpot().pos : deskSpot(slots[a.id] ?? 0).pos;
        items.push({ id: a.id, zone: zoneOf(g.position.x, g.position.z), activity: shared.current.info[a.id]?.label ?? "", at_desk: Math.hypot(g.position.x - own[0], g.position.z - own[1]) < 1.1 });
      }
      if (items.length) onPresence(items);
    }, 2500);
    return () => clearInterval(t);
  }, [agents, slots, onPresence]);

  const mcpUser = Object.entries(live).find(([, l]) => l.kind === "mcp" && l.state !== "idle")?.[0];

  return (
    <Canvas camera={{ position: [0, 25, 19], fov: 42 }} dpr={[1, 2]}>
      <color attach="background" args={["#1d1a2b"]} />
      <ambientLight intensity={0.95} />
      <directionalLight position={[8, 14, 10]} intensity={1.3} />
      <Suspense fallback={null}>
        <Office slots={slots} agents={agents} shared={shared} />
        {agents.map((a) => (
          <Agent
            key={a.id}
            info={a}
            all={agents}
            slots={slots}
            live={live[a.id] ?? { state: "idle", emotion: "neutral" }}
            interaction={interaction}
            ambient={ambient}
            selected={selectedId === a.id}
            fresh={freshIds.includes(a.id)}
            leaving={leavingIds.includes(a.id)}
            onLeft={onAgentLeft}
            onSelect={() => onSelect(a.id)}
            onFocus={() => onFocusAgent(a.id)}
            shared={shared}
          />
        ))}
        {interaction && (
          <Beam
            color="#f5c518"
            getA={() => shared.current.groups[interaction.fromId]?.position ?? null}
            getB={() => shared.current.groups[interaction.toId]?.position ?? null}
          />
        )}
        {mcpUser && (
          <Beam color="#59c8ff" getA={() => shared.current.groups[mcpUser]?.position ?? null} getB={() => new THREE.Vector3(MCP_RACK[0], 0, MCP_RACK[2])} />
        )}
        <Environment preset="city" />
        <ContactShadows position={[0, 0.02, 0]} opacity={0.3} scale={40} blur={2.5} />
      </Suspense>
      <OrbitControls ref={controls} makeDefault enablePan minDistance={1.5} maxDistance={55} maxPolarAngle={Math.PI / 2.05} target={[0, 0.5, 0]} />
      <CameraRig view={view} followId={followId} shared={shared} controls={controls} />
    </Canvas>
  );
}

