// Sitcom-office floor plan. x runs left->right, z runs back wall (-) -> viewer (+).
// Yaw 0 faces +z (toward the viewer), PI faces -z (toward the back wall).

export type RoomId = "office" | "conf";

export type Spot = {
  pos: [number, number];
  yaw: number;
  clip: string;
  activities: string[];
  badge: string;
  /** Room this spot is inside; agents route through its door. */
  room?: RoomId;
  /** Extra waypoints inside the room, to walk around furniture. */
  via?: [number, number][];
};

export const DOOR_W = 1.2;
export const DOORS: Record<RoomId, { idx: number; x: number; z: number }> = {
  office: { idx: 0, x: 10.5, z: -5 },
  conf: { idx: 1, x: -10, z: -5 },
};

export const FLOOR = { w: 34, d: 21 };

/** The glass double door at the front of the office, where new hires walk in. */
export const MAIN_DOOR = { idx: 2, x: -10, z: 10.4 };

export function doorPoints(room: RoomId) {
  const d = DOORS[room];
  return {
    approach: [d.x, d.z + 1.0] as [number, number],
    doorway: [d.x, d.z] as [number, number],
    inside: [d.x, d.z - 1.0] as [number, number],
  };
}

// ---- Desks ----

export type DeskSlot = { x: number; z: number; yaw: number; reception?: boolean };
export const MAX_WORKERS = 19;

// Seating modelled on the show's floor plan: reception up front, Jim and Dwight facing each other in the
// sales pod, accounting in a corner cluster, and the warehouse (Darryl) off to the side.
export const SLOTS: DeskSlot[] = [
  { x: -6.5, z: 7.2, yaw: 0, reception: true }, // 0 reception
  { x: 2, z: 5.2, yaw: Math.PI }, // 1 sales (faces slot 2)
  { x: 2, z: 1.8, yaw: 0 }, // 2 sales (faces slot 1)
  { x: 6.2, z: 5.2, yaw: Math.PI }, // 3 sales
  { x: -9.5, z: 0.2, yaw: 0 }, // 4 accounting
  { x: -6.5, z: 0.2, yaw: 0 }, // 5 accounting
  { x: -8.0, z: 3.4, yaw: Math.PI }, // 6 accounting
  { x: 13.5, z: 0.2, yaw: 0 }, // 7 warehouse
  // Free desks for newly hired agents.
  { x: -2, z: -2.6, yaw: 0 },
  { x: 2, z: -2.6, yaw: 0 },
  { x: 6, z: -2.6, yaw: 0 },
  { x: -6, z: -2.6, yaw: 0 },
  { x: -3, z: 6.2, yaw: Math.PI },
  { x: 10, z: 3.6, yaw: Math.PI },
  { x: -12.5, z: 0.2, yaw: 0 },
  { x: -12.5, z: 3.4, yaw: Math.PI },
  { x: -1, z: 8.8, yaw: Math.PI },
  { x: 3, z: 8.8, yaw: Math.PI },
  { x: 7.2, z: 2.2, yaw: 0 },
];

const PREFERRED: Record<string, number> = {
  pam_beesly: 0,
  jim_halpert: 1,
  dwight_schrute: 2,
  stanley_hudson: 3,
  angela_martin: 4,
  oscar_martinez: 5,
  kevin_malone: 6,
  darryl_philbin: 7,
};

export function assignSlots(workerIds: string[]): Record<string, number> {
  const out: Record<string, number> = {};
  const used = new Set<number>();
  for (const id of workerIds) {
    const s = PREFERRED[id];
    if (s !== undefined && !used.has(s)) {
      out[id] = s;
      used.add(s);
    }
  }
  let next = 8; // custom hires go to the free desks
  for (const id of workerIds) {
    if (id in out) continue;
    while (used.has(next)) next++;
    out[id] = next % SLOTS.length;
    used.add(next);
  }
  return out;
}

export function deskSpot(slot: number): Spot {
  const s = SLOTS[slot % SLOTS.length];
  return { pos: [s.x, s.z], yaw: s.yaw, clip: "Yes", activities: ["Answering emails…", "Working on a report…", "On a call…", "Filing paperwork…"], badge: "💻" };
}

// ---- Conference room (the concentration room) ----

const SEAT_X = [-12.25, -10.75, -9.25, -7.75];
const SEATS: { pos: [number, number]; yaw: number; via?: [number, number][] }[] = [
  ...SEAT_X.map((x) => ({ pos: [x, -6.1] as [number, number], yaw: Math.PI })), // south side, facing the table
  ...SEAT_X.map((x) => ({ pos: [x, -8.9] as [number, number], yaw: 0, via: [[-6.2, -6.0], [-6.2, -8.9]] as [number, number][] })), // north side
];

export function confSpot(slot: number): Spot {
  const seat = SEATS[slot % SEATS.length];
  return {
    pos: seat.pos,
    yaw: seat.yaw,
    clip: "Yes",
    activities: ["Deep focus 🔒", "Heads-down working 🔒", "Do not disturb 🔒"],
    badge: "🔒",
    room: "conf",
    via: seat.via,
  };
}

// ---- Michael's office ----

export function officeSpot(): Spot {
  return {
    pos: [11.5, -8.5],
    yaw: 0,
    clip: "Yes",
    activities: ["Planning a big announcement…", "Reviewing the team's work…", "Doing important manager things…"],
    badge: "💼",
    room: "office",
    via: [[8.2, -6.0], [8.2, -8.5]],
  };
}

// ---- Common-area stations ----

export type StationId = "copier" | "kitchen" | "reception" | "mcp" | "archive";

export const STATIONS: Record<StationId, Spot & { label: string }> = {
  copier: { pos: [0, -8.4], yaw: Math.PI, clip: "Punch", label: "Copier & files", activities: ["Printing…", "Making copies…", "Filing records…"], badge: "🖨️" },
  kitchen: { pos: [12, 8.4], yaw: 0, clip: "Dance", label: "Kitchen", activities: ["Making coffee ☕", "Grabbing a snack…", "Microwaving lunch…"], badge: "☕" },
  reception: { pos: [-8.6, 7.4], yaw: Math.PI / 2, clip: "Wave", label: "Reception", activities: ["Chatting at reception…", "Picking up the mail…", "Catching up…"], badge: "💬" },
  archive: { pos: [-2.6, -8.6], yaw: Math.PI, clip: "ThumbsUp", label: "Document archive", activities: ["Searching the files…", "Reading documents…"], badge: "📚" },
  mcp: { pos: [-13.2, 0], yaw: -Math.PI / 2, clip: "Yes", label: "MCP server closet", activities: ["Calling an MCP tool…"], badge: "🔌" },
};

/** Where the MCP rack sits, so beams can point at it. */
export const MCP_RACK: [number, number, number] = [-15.3, 1.3, 0];

export const AMBIENT_STATIONS: StationId[] = ["copier", "kitchen", "reception"];

// ---- Camera views ----

export type Vec3Tuple = [number, number, number];
export type CameraView = { pos: Vec3Tuple; target: Vec3Tuple; nonce: number };

/** Named places you can fly the camera to. Some sit inside the glass rooms. */
export const VIEWS: { id: string; label: string; pos: Vec3Tuple; target: Vec3Tuple }[] = [
  { id: "overview", label: "Overview", pos: [0, 25, 19], target: [0, 0.5, 0] },
  { id: "michael", label: "Michael's office", pos: [7.4, 3.0, -5.7], target: [11.5, 1.0, -7.6] },
  { id: "conference", label: "Conference room", pos: [-6.2, 3.2, -5.8], target: [-10, 1.0, -7.5] },
  { id: "reception", label: "Reception", pos: [-6.5, 2.8, 11], target: [-6.5, 1.0, 7.4] },
  { id: "sales", label: "Sales pod", pos: [2, 5.5, 10.5], target: [2, 0.9, 3.5] },
  { id: "accounting", label: "Accounting", pos: [-8, 5.2, 7.5], target: [-8, 0.9, 1.5] },
  { id: "warehouse", label: "Warehouse", pos: [9, 5, 6.5], target: [14, 0.8, 0] },
  { id: "kitchen", label: "Kitchen", pos: [12, 5, 14], target: [12, 0.8, 8.5] },
  { id: "entrance", label: "Main door", pos: [-10, 5.5, 18], target: [-10, 1.0, 9.5] },
  { id: "mcp", label: "MCP closet", pos: [-9, 4, 5], target: [-15, 1.2, 0] },
];

/** A close camera angle on one desk (or on Michael's desk if slot is null). */
export function deskView(slot: number | null): { pos: Vec3Tuple; target: Vec3Tuple } {
  if (slot === null) return { pos: [11.5, 3.2, -4.4], target: [11.5, 1.0, -7.6] };
  const s = SLOTS[slot % SLOTS.length];
  const deskZ = s.z + Math.cos(s.yaw);
  // Stand on the viewer's side of the desk, looking at the occupant.
  return { pos: [s.x, 2.9, Math.max(s.z, deskZ) + 3.4], target: [s.x, 1.0, s.z] };
}

/** Which part of the office a floor position is in, for "where is everyone" questions. */
export function zoneOf(x: number, z: number): string {
  if (z > 10.2 || (x > -11.5 && x < -8.5 && z > 9.2)) return "Main entrance";
  if (x >= 6 && x <= 15 && z <= -5 && z >= -10.4) return "Michael's office";
  if (x >= -15 && x <= -5 && z <= -5 && z >= -10.4) return "Conference room";
  if (x < -12.2 && Math.abs(z) < 2.5) return "MCP server closet";
  if (x > 11 && Math.abs(z) < 4.6) return "Warehouse";
  if (x > 7.5 && z > 5.5) return "Kitchen";
  if (x < -4.2 && z > 5.8) return "Reception";
  if (Math.abs(x) < 3.6 && z < -7.4) return "Copier and document archive";
  if (x >= -12.5 && x <= -4.6 && z >= -1.5 && z <= 4.6) return "Accounting corner";
  if (x >= -0.8 && x <= 9 && z >= 0.2 && z <= 7) return "Sales pod";
  return "Open office floor";
}
