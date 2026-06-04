// Pure adapters between the existing Flask/Socket.IO backend contract and the
// frontend store. Backend is source of truth; we never invent events.
// (See research: state_update, arm_health, asset_health, object_update,
//  objects_reset, log_message, script_chosen, task_status/complete.)

import type {
  ActorId,
  ActorStatus,
  BusKind,
  LogLine,
  TopologyActor,
  Vec2,
} from "@/store/types";
import type { RootStore } from "@/store";

// ---- id mapping ----------------------------------------------------------
// Backend arms are numeric (1..N); frontend actor ids are "arm-<n>".
// Assets keep their backend id verbatim ("veh-a", ...).
export const armActorId = (armId: number | string): ActorId => `arm-${armId}`;
export const assetActorId = (assetId: string): ActorId => assetId;

// ---- status mapping ------------------------------------------------------
// Arm health enum (arm_state): idle|alive|unhealthy|dead|draining|stopped
export function armHealthToStatus(h: string): ActorStatus {
  switch (h) {
    case "alive":
      return "ok";
    case "idle":
      return "idle";
    case "unhealthy":
    case "draining":
    case "recovering":
      return "warn";
    case "dead":
    case "stopped":
      return "down";
    default:
      return "idle";
  }
}

// Asset status enum (asset_state): idle|busy|draining|failed|recovering|stopped
export function assetStatusToStatus(s: string): ActorStatus {
  switch (s) {
    case "busy":
      return "busy";
    case "idle":
      return "idle";
    case "draining":
    case "recovering":
      return "warn";
    case "failed":
    case "stopped":
      return "down";
    default:
      return "idle";
  }
}

// ---- log_message → LogLine ----------------------------------------------
// Payload: { timestamp:"HH:MM:SS", source, message, level:"info|warning|error|success"|null }
const LEVEL_MAP: Record<string, LogLine["level"]> = {
  info: "info",
  success: "ok",
  warning: "warn",
  error: "warn",
};

export function toLogLine(p: {
  timestamp?: string;
  source?: string;
  message?: string;
  level?: string | null;
}): LogLine {
  const level = LEVEL_MAP[p.level ?? "info"] ?? "info";
  const segs: LogLine["segments"] = [];
  if (p.source) segs.push({ text: `[${p.source}] `, cls: "dim" });
  segs.push({ text: p.message ?? "" });
  return {
    id: `${p.timestamp ?? ""}-${Math.random().toString(36).slice(2, 8)}`,
    ts: p.timestamp ?? new Date().toISOString().slice(11, 19),
    level,
    segments: segs,
  };
}

// ---- coordination/bus classification ------------------------------------
// The backend has no bid/contract events. The decentralised "message bus" is
// derived from log_message + script_chosen: only lines that read as actor
// coordination surface as bus pills (direct-docking model - no handover).
const BUS_RULES: { re: RegExp; kind: BusKind }[] = [
  { re: /\bdock|docking|approach|undock/i, kind: "dock" },
  { re: /\bpick|grab|announce|seeking|request/i, kind: "announce" },
  {
    re: /\bplace|deliver|complete|done|hand(ed)?\s?off|transfer/i,
    kind: "accept",
  },
  { re: /\bbid|select|assign|chose|chosen/i, kind: "bid" },
];

export function classifyBus(
  source: string,
  message: string,
): { kind: BusKind; from: string; text: string } | null {
  for (const r of BUS_RULES) {
    if (r.re.test(message)) {
      return { kind: r.kind, from: source || "system", text: message };
    }
  }
  return null;
}

// ---- owner → stage position ---------------------------------------------
// arm/asset → that actor's node center; zone → zone chip; transfer_point /
// handover_N → NULL (no transfer node exists in the decentralised model - the
// driver treats it as a transient dock between the two carriers instead).
export function resolveOwnerPosition(
  store: RootStore,
  ownerType: string,
  ownerId: string,
): Vec2 | null {
  if (ownerType === "arm" || ownerType === "asset") {
    const a = store.actors[ownerId];
    if (a)
      return { x: a.position.x + a.size.w / 2, y: a.position.y + a.size.h / 2 };
    return null;
  }
  if (ownerType === "zone") {
    // Phase 7 Stage F: zones are kind:"zone" actors. Resolve by id, else by
    // source/sink subtype. Position is the box centre (store holds top-left).
    const center = (a: TopologyActor) => ({
      x: a.position.x + a.size.w / 2,
      y: a.position.y + a.size.h / 2,
    });
    const z = store.actors[ownerId] ?? store.actors[ownerId.replace(/-zone$/, "")];
    if (z && z.kind === "zone") return center(z);
    const want = /in|feed|intake/i.test(ownerId) ? "source" : "sink";
    const byRole = Object.values(store.actors).find(
      (a) => a.kind === "zone" && a.subtype === want,
    );
    return byRole ? center(byRole) : null;
  }
  // transfer_point / handover_N - no node in the new model.
  return null;
}

export const midpoint = (a: Vec2, b: Vec2): Vec2 => ({
  x: (a.x + b.x) / 2,
  y: (a.y + b.y) / 2,
});
