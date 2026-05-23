// AgvTrailLayer — Stage 8 Track B.
//
// Renders a polyline trail of recent AGV positions for each AGV, derived from
// two sources (merged, deduped by timestamp):
//   1. Live: `actorRuntime[id].runtimePosition` — accumulated into a per-AGV
//      ring buffer in a local ref so updates at ~2 Hz don't trigger re-renders.
//   2. Historical: `/bus/replay?type=position_update&limit=500` — fetched on
//      mount and polled every 5 s to backfill positions older than the SocketIO
//      ring buffer window.
//
// Rendering:
//   - Konva `Line` with `tension=0.3` for slight smoothing (no per-frame alloc).
//   - Up to MAX_TRAIL_POINTS (50) per AGV; oldest points dropped from the front.
//   - Per-AGV color at 30% alpha; gradient fade applied via per-segment opacity
//     is approximated by rendering a single Line whose opacity ramps from old→new
//     using a gradient stroke (not supported by Konva natively), so we render
//     each AGV as a single Line at base alpha and let the trail sparsity do the
//     natural fade.
//   - Task filter: when a task_id is selected (Track C will inject
//     `selectedTaskId` prop), only positions for that task are shown; otherwise
//     shows the most-recent task's trail (or all if no task seen yet).
//
// Z-order: mounted BEFORE AgvTokenLayer in the Konva Stage so trails draw
// BEHIND the token bodies.
//
// Track C contract: this component accepts an optional `selectedTaskId` prop.
// Track C will pass it when the replay cursor is active. Default: undefined
// (auto-select most-recent task).
//
// DO NOT touch ReplayPanel.tsx or scrub mechanics — Track C owns those.

import { useEffect, useRef, useState, useCallback } from "react";
import { Layer, Line } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import { useActorRuntimeAll, useActors } from "@/store/selectors";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum trail positions kept per AGV. */
const MAX_TRAIL_POINTS = 50;

/** Background poll interval for /bus/replay (ms). */
const REPLAY_POLL_MS = 5_000;

/** Default AGV color if /config doesn't provide one. */
const DEFAULT_AGV_COLOR = "#16a085";

/** Trail stroke width (stage pixels). */
const TRAIL_STROKE_WIDTH = 2.5;

/** Minimum distance (px²) between successive points to avoid clustering. */
const MIN_DIST_SQ = 4; // 2 px

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TrailPoint {
  x: number;
  y: number;
  /** Unix epoch seconds (float). Used for dedup + task filter. */
  ts: number;
  task_id?: string;
}

// Shape of a raw POSITION_UPDATE message from /bus/replay.
interface ReplayPositionUpdate {
  type: string;
  sender: string;
  task_id?: string;
  negotiation_round?: number;
  payload?: {
    x?: number;
    y?: number;
    theta?: number;
    ts?: number;
  };
  ts?: string | number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hexToRgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16) ?? 22;
  const g = parseInt(hex.slice(3, 5), 16) ?? 160;
  const b = parseInt(hex.slice(5, 7), 16) ?? 133;
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

/** Extract epoch-seconds timestamp from a replay message. */
function extractTs(msg: ReplayPositionUpdate): number {
  if (msg.payload?.ts != null) return msg.payload.ts;
  if (msg.ts != null) {
    const n = typeof msg.ts === "number" ? msg.ts : Date.parse(msg.ts as string);
    return isNaN(n) ? Date.now() / 1000 : n / 1000;
  }
  return Date.now() / 1000;
}

/** Append a point to a trail ring buffer, enforcing MAX_TRAIL_POINTS cap
 *  and minimum distance filter to avoid clumping. Mutates `buf` in place. */
function appendPoint(buf: TrailPoint[], pt: TrailPoint): void {
  const last = buf[buf.length - 1];
  if (last != null) {
    const dx = pt.x - last.x;
    const dy = pt.y - last.y;
    if (dx * dx + dy * dy < MIN_DIST_SQ) return; // too close, skip
  }
  buf.push(pt);
  if (buf.length > MAX_TRAIL_POINTS) buf.splice(0, buf.length - MAX_TRAIL_POINTS);
}

/** Build a flat Konva `points` array [x0,y0,x1,y1,...] from trail points. */
function toKonvaPoints(pts: TrailPoint[]): number[] {
  const out: number[] = [];
  for (const p of pts) { out.push(p.x, p.y); }
  return out;
}

/** Select the most-recent task_id present in any trail. */
function mostRecentTaskId(trails: Map<string, TrailPoint[]>): string | null {
  let latestTs = -Infinity;
  let latestTask: string | null = null;
  for (const buf of trails.values()) {
    for (let i = buf.length - 1; i >= 0; i--) {
      const pt = buf[i];
      if (pt.task_id && pt.ts > latestTs) {
        latestTs = pt.ts;
        latestTask = pt.task_id;
      }
    }
  }
  return latestTask;
}

// ---------------------------------------------------------------------------
// AgvTrailLayer
// ---------------------------------------------------------------------------

export interface AgvTrailLayerProps {
  tx: ViewportTransform;
  /** Injected by Track C when replay cursor is active. When provided, only
   *  trails for this task are rendered. When undefined, auto-select the most
   *  recent task (or show all if no task_id seen). */
  selectedTaskId?: string;
}

export function AgvTrailLayer({ tx, selectedTaskId }: AgvTrailLayerProps) {
  // ── AGV color lookup (fetched once from /config) ──────────────────────────
  const [agvColors, setAgvColors] = useState<Record<string, string>>({});
  useEffect(() => {
    fetch("/config")
      .then((r) => r.json())
      .then((cfg) => {
        const m: Record<string, string> = {};
        (cfg.warehouse?.agvs ?? []).forEach(
          (a: { id: string; color?: string }) => {
            m[a.id] = a.color ?? DEFAULT_AGV_COLOR;
          },
        );
        setAgvColors(m);
      })
      .catch(() => {});
  }, []);

  // ── Topology actors (to know which AGVs exist) ────────────────────────────
  const actors = useActors();

  // ── Live runtimePosition subscription ────────────────────────────────────
  // We accumulate runtimePosition updates into a per-AGV ring buffer held in
  // a ref so that the RAF loop can read them without triggering re-renders
  // on every ~2 Hz position event.
  const trailsRef = useRef<Map<string, TrailPoint[]>>(new Map());

  const actorRuntime = useActorRuntimeAll();

  // Track which rtp timestamp we last consumed per AGV to avoid duplicates.
  const lastRtpTsRef = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    for (const [id, rt] of Object.entries(actorRuntime)) {
      const rtp = rt.runtimePosition;
      if (!rtp) continue;
      const prev = lastRtpTsRef.current.get(id);
      if (prev != null && rtp.ts <= prev) continue; // already consumed

      lastRtpTsRef.current.set(id, rtp.ts);

      if (!trailsRef.current.has(id)) {
        trailsRef.current.set(id, []);
      }
      const buf = trailsRef.current.get(id)!;
      appendPoint(buf, { x: rtp.x, y: rtp.y, ts: rtp.ts });
    }
  }); // no deps — run after every render to pick up fresh runtimePosition

  // ── Historical backfill from /bus/replay ─────────────────────────────────
  const fetchReplay = useCallback(async () => {
    try {
      const res = await fetch(
        "/bus/replay?type=position_update&limit=500",
      );
      if (!res.ok) return;
      const data: ReplayPositionUpdate[] = await res.json();
      for (const msg of data) {
        const sender = msg.sender;
        if (!sender) continue;
        const px = msg.payload?.x;
        const py = msg.payload?.y;
        if (px == null || py == null) continue;
        const ts = extractTs(msg);

        if (!trailsRef.current.has(sender)) {
          trailsRef.current.set(sender, []);
        }
        const buf = trailsRef.current.get(sender)!;
        appendPoint(buf, { x: px, y: py, ts, task_id: msg.task_id });
      }
      // Sort each trail by ts (replay may return unsorted).
      for (const buf of trailsRef.current.values()) {
        buf.sort((a, b) => a.ts - b.ts);
        // Re-enforce cap after sort+merge.
        if (buf.length > MAX_TRAIL_POINTS) {
          buf.splice(0, buf.length - MAX_TRAIL_POINTS);
        }
      }
    } catch {
      // Silently ignore — /bus/replay is best-effort.
    }
  }, []);

  useEffect(() => {
    fetchReplay();
    const timer = setInterval(fetchReplay, REPLAY_POLL_MS);
    return () => clearInterval(timer);
  }, [fetchReplay]);

  // ── RAF-driven render tick ────────────────────────────────────────────────
  // We snapshot the trails ref into React state at ~10 fps (100 ms) to avoid
  // per-frame re-renders while still updating smoothly.
  const [renderTick, setRenderTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setRenderTick((n) => n + 1), 100);
    return () => clearInterval(id);
  }, []);

  // Suppress unused-variable lint while using renderTick as a refresh trigger.
  void renderTick;

  // ── Determine task filter ─────────────────────────────────────────────────
  // If selectedTaskId is provided (Track C), use it.
  // Otherwise auto-select the most-recent task from trail history, or null
  // (show all trails) if no task_id has been seen yet.
  const effectiveTaskId =
    selectedTaskId !== undefined
      ? selectedTaskId
      : mostRecentTaskId(trailsRef.current);

  // ── Visibility guard ──────────────────────────────────────────────────────
  const hasAnyPoints = Array.from(trailsRef.current.values()).some(
    (buf) => buf.length >= 2,
  );
  if (!hasAnyPoints) return null;

  // ── Collect AGV ids to render ────────────────────────────────────────────
  // Render all AGVs that have trail data; topology actors used for color fallback.
  const agvIds = Array.from(trailsRef.current.keys());

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {agvIds.map((id) => {
        const fullBuf = trailsRef.current.get(id) ?? [];

        // Filter by task if effectiveTaskId is set.
        const filtered =
          effectiveTaskId != null
            ? fullBuf.filter(
                (p) => p.task_id == null || p.task_id === effectiveTaskId,
              )
            : fullBuf;

        if (filtered.length < 2) return null;

        const actor = actors[id];
        const color =
          agvColors[id] ??
          (actor as (typeof actor & { color?: string }) | undefined)?.color ??
          DEFAULT_AGV_COLOR;

        const pts = toKonvaPoints(filtered);

        // Gradient fade: render TWO lines:
        //   1. Full trail at low base alpha (oldest segments).
        //   2. Second half at slightly higher alpha (recent segment accent).
        const midIdx = Math.floor(filtered.length / 2);
        const recentPts = toKonvaPoints(filtered.slice(midIdx));

        return (
          <>
            {/* Base trail — full path, lower alpha (oldest points) */}
            <Line
              key={`trail-base-${id}`}
              points={pts}
              stroke={hexToRgba(color, 0.18)}
              strokeWidth={TRAIL_STROKE_WIDTH}
              tension={0.3}
              lineCap="round"
              lineJoin="round"
              listening={false}
            />
            {/* Recent accent — second half, higher alpha */}
            {recentPts.length >= 4 && (
              <Line
                key={`trail-accent-${id}`}
                points={recentPts}
                stroke={hexToRgba(color, 0.38)}
                strokeWidth={TRAIL_STROKE_WIDTH + 0.5}
                tension={0.3}
                lineCap="round"
                lineJoin="round"
                listening={false}
              />
            )}
          </>
        );
      })}
    </Layer>
  );
}
