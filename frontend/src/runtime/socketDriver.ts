import { io, type Socket } from "socket.io-client";
import type { useStore } from "@/store";
import type { RuntimeDriver, ActorOpResult } from "./driver";
import { normalizeConfig, type RawConfig } from "./configNormalizer";
import { loadLocal } from "@/lib/persistence";
import {
  armActorId,
  assetActorId,
  armHealthToStatus,
  assetStatusToStatus,
  toLogLine,
  resolveOwnerPosition,
} from "@/lib/contract";

// Live runtime. Adapts to the EXISTING backend contract (no new events).
// Same-origin: nginx proxies /socket.io and the REST API to Flask; the Vite
// dev server proxies them too (vite.config.ts).
async function getJSON<T>(
  url: string,
  signal?: AbortSignal,
): Promise<T | null> {
  try {
    const r = await fetch(url, { signal });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

async function postOk(url: string): Promise<{ ok: boolean; note: string }> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 6000);
    const r = await fetch(url, { method: "POST", signal: ctrl.signal });
    clearTimeout(t);
    if (!r.ok) return { ok: false, note: `HTTP ${r.status}` };
    const body = (await r.json().catch(() => ({}))) as {
      container_control?: string;
    };
    const cc = body.container_control;
    // Honest: the backend soft-fails container control when docker is not
    // wired up. Surface that instead of pretending the action landed.
    if (cc && cc !== "ok" && cc !== "not needed" && cc !== "already recovering")
      return { ok: false, note: cc };
    return { ok: true, note: cc ?? "ok" };
  } catch {
    return { ok: false, note: "request failed" };
  }
}

/** `arm-7` → 7; anything else → null (treated as a non-arm asset id). */
function armNumber(actorId: string): number | null {
  const m = /^arm-(\d+)$/.exec(actorId);
  return m ? Number(m[1]) : null;
}

// US 34.1: render a one-line summary of a bus event for the BusPane. Keeps
// the panel readable instead of dumping the whole JSON payload.
function busEventToText(
  type: string,
  payload: Record<string, unknown>,
  taskId?: string | null,
): string {
  const pkg = (payload.package_id as string) ?? taskId ?? "?";
  switch (type) {
    case "task_announce":
      return `announce ${pkg} (${payload.label ?? "no-label"}${
        payload.weight_kg != null ? ` · ${payload.weight_kg}kg` : ""
      })`;
    case "task_orphan":
      return `orphan ${pkg} — ${payload.reason ?? "fault"}`;
    case "bid": {
      const score = payload.score as number | undefined;
      const why = payload.rationale as string | undefined;
      return `bid ${pkg} score=${score?.toFixed?.(1) ?? score ?? "?"} ${why ?? ""}`;
    }
    case "claim":
      return `claim ${pkg} → ${payload.winner ?? "?"} (score=${
        payload.winning_score ?? "?"
      })`;
    case "agent_status":
      return `status=${payload.status ?? "?"}${
        payload.current_task ? ` task=${payload.current_task}` : ""
      }`;
    case "intent": {
      const path = Array.isArray(payload.path_cells)
        ? ` (${(payload.path_cells as unknown[]).length} cells)`
        : "";
      return `intent ${payload.action ?? "?"}${path}${
        payload.detail ? ` — ${payload.detail}` : ""
      }`;
    }
    case "arm_approach":
      return `approach ${payload.arm_station ?? "?"} eta=${payload.eta_s ?? "?"}s`;
    case "arm_ready":
      return `arm ${payload.action ?? "?"} for ${payload.for_agv ?? "?"}`;
    case "arm_busy":
      return `arm busy ${payload.arm_station ?? "?"}`;
    case "yield":
      return `yield: ${payload.reason ?? "?"}${
        payload.waiting_for ? ` (waiting ${payload.waiting_for})` : ""
      }`;
    case "help_request":
      return `help: ${payload.reason ?? "?"}`;
    default:
      // Unknown type — show the JSON so nothing is silently swallowed.
      try {
        return `${type} ${JSON.stringify(payload).slice(0, 140)}`;
      } catch {
        return type;
      }
  }
}

// US 34.1: map a bus event to which AI tier is currently "speaking". Returns
// null when the event is not associated with an AI action (e.g. yield is
// pure script). Mirrors §4 of Work flow(12 June 2026).md.
function inferActiveAi(
  type: string,
  source: string,
): { model: string; role: string; source: string } | null {
  switch (type) {
    case "task_spec":
    case "plan":
    case "task_announce":
      return { model: "qwq:32b", role: "planner", source: "master" };
    case "bid":
      return {
        model: "qwen2.5:14b",
        role: "judge",
        source: source || "agv",
      };
    case "intent":
      return {
        model: "qwen2.5:14b",
        role: "tactical",
        source: source || "agv",
      };
    case "arm_ready":
    case "arm_approach":
      return {
        model: "qwen2.5:14b",
        role: "arm-gate",
        source: source || "arm",
      };
    case "claim":
      return { model: "qwq:32b", role: "planner", source: "master" };
    default:
      return null;
  }
}

export function createSocketDriver(): RuntimeDriver {
  let socket: Socket | null = null;
  let raf = 0;
  let last = 0;
  let busWindow: number[] = [];
  let lastBusSec = 0;
  // event_seq guard per object (mirror backend stale-drop). Capped - old
  // object ids are evicted FIFO so a long run can't grow these unbounded.
  const OBJ_CAP = 500;
  const objSeq = new Map<string, number>();
  // last emitted status per actor - dedup so observability records only real
  // transitions, never per-poll repeats.
  const lastStatus = new Map<string, string>();
  // actorId → downstream actorIds (from /config asset_graph).
  let adjacency: Record<string, string[]> = {};
  // object_id → last owner key, for detecting dock moments.
  const objOwner = new Map<string, string>();
  // Captured at start() so the Phase 13 operational controls can reconcile
  // store state from the backend without holding a separate store ref.
  let storeRef: typeof useStore | null = null;
  // US 34.1: per-AGV color lookup (from /config.warehouse.agvs[]), populated
  // at bootstrap. Used to colour route polylines and bus messages by AGV.
  // Empty until /config returns — polylines fall back to a neutral colour
  // while the lookup is cold.
  let agvColors: Record<string, string> = {};
  // US 34.1: bus dedup by (seq, type, task_id). The backend rebroadcasts the
  // same envelope twice (room + default broadcast); without this the
  // BusPane shows every event twice.
  const seenBus = new Set<string>();

  // Re-derive truthful actor runtime from the backend container snapshot.
  // Used on reconnect and after every operational action so the UI never
  // advances ahead of the backend (the backend stays the authority).
  async function reconcileRuntime(): Promise<void> {
    const store = storeRef;
    if (!store) return;
    const snap = await getJSON<{
      arms?: Record<string, { health: string; error: string | null }>;
      assets?: Record<string, { status: string }>;
    }>("/runtime/status");
    if (!snap) return;
    const st = store.getState();
    if (snap.arms)
      for (const [id, h] of Object.entries(snap.arms))
        st.setActorRuntime(armActorId(id), {
          status: armHealthToStatus(h.health),
          phase: h.error ? `${h.health} · ${h.error}` : h.health,
        });
    if (snap.assets)
      for (const [id, a] of Object.entries(snap.assets))
        st.setActorRuntime(assetActorId(id), {
          status: assetStatusToStatus(a.status),
          phase: a.status,
        });
  }

  return {
    start(store) {
      storeRef = store;
      const st = () => store.getState();
      st().setConnection("live", "connecting");

      const bootstrap = async () => {
        const cfg = await getJSON<RawConfig>("/config");
        if (cfg) {
          const { actors, adjacency: adj } = normalizeConfig(cfg);
          adjacency = adj;
          // US 34.1: capture per-AGV color so the bus handler can paint route
          // polylines + bus rows in each AGV's signature colour.
          const whAgvs = (cfg as unknown as {
            warehouse?: { agvs?: { id: string; color?: string }[] };
          }).warehouse?.agvs;
          if (Array.isArray(whAgvs)) {
            const next: Record<string, string> = {};
            for (const a of whAgvs)
              if (a?.id) next[a.id] = a.color ?? "#888";
            agvColors = next;
          }
          // The authored topology (positions/labels) is the USER's document
          // and is restored from local storage by useAutosave. Backend
          // /config must NOT overwrite it on every (re)connect, or saved
          // layout reverts on refresh. Only seed from /config when there is
          // no saved workspace yet. Runtime (health/asset) is a separate
          // slice and keeps flowing regardless.
          const hasSaved = (loadLocal()?.actors?.length ?? 0) > 0;
          if (!hasSaved && Object.keys(actors).length)
            st().hydrateTopology({ actors });
        }
        const [state, objects, arms, assets] = await Promise.all([
          getJSON<{
            arms: { arm_id: number; angle: number; status: string }[];
          }>("/state"),
          getJSON<{
            objects: {
              object_id: string;
              owner_type: string;
              owner_id: string;
              event_seq: number;
            }[];
          }>("/objects"),
          getJSON<{
            arms: Record<string, { health: string; error: string | null }>;
          }>("/arms/health"),
          getJSON<{
            assets: Record<
              string,
              { status: string; carried_object: string | null }
            >;
          }>("/assets/state"),
        ]);
        state?.arms?.forEach((a) =>
          st().setActorRuntime(armActorId(a.arm_id), {
            status: armHealthToStatus(a.status),
            phase: a.status,
          }),
        );
        if (arms?.arms)
          for (const [id, h] of Object.entries(arms.arms))
            st().setActorRuntime(armActorId(id), {
              status: armHealthToStatus(h.health),
              phase: h.health,
            });
        if (assets?.assets)
          for (const [id, a] of Object.entries(assets.assets))
            st().setActorRuntime(assetActorId(id), {
              status: assetStatusToStatus(a.status),
              phase: a.status,
            });
        // Never regress the dedup guard below an event_seq already seen from
        // an in-flight live event that arrived before this bootstrap resolved.
        objects?.objects?.forEach((o) =>
          objSeq.set(
            o.object_id,
            Math.max(o.event_seq, objSeq.get(o.object_id) ?? -1),
          ),
        );
      };

      socket = io({
        path: "/socket.io",
        transports: ["websocket", "polling"],
        reconnection: true,
      });

      socket.on("connect", () => {
        st().setConnection("live", "connected");
        st().pushEvent({
          kind: "link",
          text: "Runtime link established",
          tone: "ok",
        });
        // Resilience: a backend restart resets its event_seq counter. Clearing
        // the per-object dedup maps on every (re)connect prevents post-restart
        // object events being wrongly dropped as stale. Bootstrap reseeds them.
        objSeq.clear();
        objOwner.clear();
        busWindow = [];
        lastStatus.clear();
        void bootstrap();
      });
      socket.on("disconnect", () => {
        st().setConnection("live", "disconnected");
        st().pushEvent({
          kind: "link",
          text: "Runtime link lost",
          tone: "warn",
        });
      });
      socket.io.on("reconnect_attempt", () =>
        st().setConnection("live", "connecting"),
      );

      socket.on(
        "state_update",
        (p: { arms?: { arm_id: number; status: string }[] }) => {
          p.arms?.forEach((a) =>
            st().setActorRuntime(armActorId(a.arm_id), {
              status: armHealthToStatus(a.status),
              phase: a.status,
            }),
          );
        },
      );

      // ────────────────────────────────────────────────────────────────────
      // US 34.1: live bus mirror — backend's publish_local() emits to the
      // SocketIO event name "bus" (also into the "coord-bus" room). Every
      // task_announce / bid / claim / agent_status / intent / arm_ready /
      // task_orphan / yield etc. flows through here. Three things happen
      // per event:
      //   (1) Pushed verbatim into busMessages so BusPane shows the full
      //       coordination conversation (was log-derived heuristic before).
      //   (2) intent events carrying `path_cells` populate agvRoutes for the
      //       Konva polyline overlay (item 4).
      //   (3) The activeAi label is derived from event type/source so the
      //       left-foot badge tells the operator which model tier is doing
      //       work right now (item 5).
      // ────────────────────────────────────────────────────────────────────
      socket.on(
        "bus",
        (ev: {
          type: string;
          from?: string;
          to?: string;
          task_id?: string | null;
          payload?: Record<string, unknown>;
          seq?: number;
          ts?: string;
        }) => {
          // Dedup: the backend emits each event twice (room + default
          // broadcast). Identical seq+type+task_id collapses to one row.
          const dedupKey = `${ev.seq ?? "x"}|${ev.type}|${ev.task_id ?? ""}`;
          if (seenBus.has(dedupKey)) return;
          seenBus.add(dedupKey);
          if (seenBus.size > 2000) {
            // Trim oldest 500 to keep the set bounded. Iteration order is
            // insertion order in Set, so .keys().next() is the oldest.
            let n = 500;
            for (const k of seenBus) {
              seenBus.delete(k);
              if (--n <= 0) break;
            }
          }

          const s = st();
          const kind = (ev.type ?? "system") as
            | "task_announce" | "task_orphan" | "bid" | "claim"
            | "agent_status" | "intent" | "arm_approach" | "arm_ready"
            | "arm_busy" | "yield" | "help_request" | "system";
          const text = busEventToText(ev.type, ev.payload ?? {}, ev.task_id);
          const ts = ev.ts && /^\d{2}:\d{2}:\d{2}$/.test(ev.ts)
            ? ev.ts
            : new Date().toISOString().slice(11, 19);
          s.pushBusMessage({
            id: `bus-${ev.seq ?? Date.now()}-${ev.type}`,
            ts,
            kind,
            from: ev.from ?? "?",
            to: ev.to,
            text,
            taskId: ev.task_id ?? undefined,
            seq: ev.seq,
          });

          // (2) intent.path_cells → polyline
          if (ev.type === "intent") {
            const payload = ev.payload ?? {};
            const cells = Array.isArray(payload.path_cells)
              ? (payload.path_cells as [number, number][]).filter(
                  (c) => Array.isArray(c) && c.length >= 2,
                )
              : null;
            const agvId = (payload.agv_id as string) || ev.from || "";
            // Fallback colour matches AGV id when /config hasn't returned yet.
            // Without this the route was invisible against the grey bay fill.
            const FALLBACK_COLORS: Record<string, string> = {
              "agv-1": "#2563eb",   // blue
              "agv-2": "#16a34a",   // green
              "agv-3": "#eab308",   // yellow
            };
            const routeColor =
              agvColors[agvId] ??
              FALLBACK_COLORS[agvId] ??
              "#ff00aa";  // hot pink so a missed mapping is unmissable, not invisible
            if (agvId && cells && cells.length > 0) {
              // Build helps debugging: log every published route so DevTools
              // shows exactly what the canvas should draw.
              console.log("[route]", agvId, "→", cells.length, "cells:", cells);
              s.setAgvRoute({
                agvId,
                color: routeColor,
                cells,
                action: String(payload.action ?? ""),
                ts: performance.now(),
              });
            }
            // The return_to_bay intent ends the leg — clear the polyline a
            // few seconds after the AGV reports it (timer fires below in the
            // same handler so we don't need a new rAF). Keeps the canvas
            // from accumulating dead routes after a run.
            if ((payload.action as string) === "return_to_bay" && agvId) {
              window.setTimeout(() => st().clearAgvRoute(agvId), 8000);
            }
          }

          // (3) Derive active-AI from the event. The mapping mirrors
          // PROJECT_FLOWCHART: task_announce / claim / plan → master qwq:32b;
          // bid / intent → tactical qwen14b on the AGV; arm_ready → arm
          // qwen14b. agent_status alone doesn't move the badge.
          const ai = inferActiveAi(ev.type, ev.from ?? "system");
          if (ai) s.setActiveAi({ ...ai, tsMs: performance.now() });
        },
      );

      socket.on(
        "arm_health",
        (p: { arm_id: number; health: string; error?: string | null }) => {
          const id = armActorId(p.arm_id);
          const status = armHealthToStatus(p.health);
          st().setActorRuntime(id, {
            status,
            phase: p.error ? `${p.health} · ${p.error}` : p.health,
          });
          // Observability: only record actual transitions (no per-poll spam).
          if (lastStatus.get(id) !== status) {
            lastStatus.set(id, status);
            st().pushEvent({
              kind: "health",
              actorId: id,
              text: `${id} → ${p.health}${p.error ? ` (${p.error})` : ""}`,
              tone:
                status === "down" ? "err" : status === "warn" ? "warn" : "ok",
            });
          }
        },
      );

      socket.on(
        "asset_health",
        (p: {
          asset_id: string;
          status: string;
          carried_object?: string | null;
          error?: string | null;
        }) => {
          const id = assetActorId(p.asset_id);
          const status = assetStatusToStatus(p.status);
          st().setActorRuntime(id, { status, phase: p.status });
          if (lastStatus.get(id) !== status) {
            lastStatus.set(id, status);
            st().pushEvent({
              kind: "asset",
              actorId: id,
              text: `${id} → ${p.status}${p.carried_object ? ` · carrying ${p.carried_object}` : ""}`,
              tone:
                status === "down" ? "err" : status === "warn" ? "warn" : "ok",
            });
          }
        },
      );

      socket.on(
        "object_update",
        (p: {
          object_id: string;
          owner_type: string;
          owner_id: string;
          carrier_asset?: string | null;
          carrier_arm?: number | null;
          color?: string | null;
          event_seq?: number;
        }) => {
          // stale-drop (mirror backend ledger semantics)
          const seq = p.event_seq ?? 0;
          if ((objSeq.get(p.object_id) ?? -1) > seq) return;
          objSeq.set(p.object_id, seq);

          const s = st();
          const prevKey = objOwner.get(p.object_id);
          const ownerKey = `${p.owner_type}:${p.owner_id}`;
          objOwner.set(p.object_id, ownerKey);

          // Arm-cargo tracking (legacy, kept for ArmLayer fallback).
          if (p.owner_type === "arm") {
            s.setArmCargo(p.owner_id, p.object_id);
          } else if (prevKey && prevKey.startsWith("arm:")) {
            const prevArm = prevKey.slice(4);
            s.setArmCargo(prevArm, null);
          }

          // Sneh-port: unified payload model. ONE entry per package, the
          // carrier string fully determines the render position. This is
          // what kills the "package on table AND on AGV" duplicate render.
          //   arm   → carrier = "arm-N"          (PayloadLayer reads arm tip)
          //   asset → carrier = "agv-N"          (PayloadLayer reads AGV pos)
          //   zone  → carrier = "zone:<station>" (PayloadLayer reads table pos)
          let carrier: string | null = null;
          if (p.owner_type === "arm") carrier = p.owner_id;
          else if (p.owner_type === "asset") carrier = p.owner_id;
          else if (p.owner_type === "zone") carrier = `zone:${p.owner_id}`;
          else if (p.owner_type === "transfer_point") carrier = `zone:${p.owner_id}`;
          s.setPayloadCarrier(p.object_id, p.color ?? "#888", carrier);

          // FIFO evict oldest tracked objects (Map keeps insertion order).
          while (objSeq.size > OBJ_CAP) {
            const k = objSeq.keys().next().value as string;
            objSeq.delete(k);
            objOwner.delete(k);
          }

          // AGV in transit: move the carrying AGV toward its downstream actor.
          if (p.carrier_asset) {
            const agvId = assetActorId(p.carrier_asset);
            const home = resolveOwnerPosition(s, "asset", agvId);
            const downstream = (adjacency[agvId] ?? [])[0];
            const target =
              (downstream &&
                resolveOwnerPosition(
                  s,
                  s.actors[downstream]?.kind === "agv" ? "asset" : "arm",
                  downstream,
                )) ||
              resolveOwnerPosition(s, p.owner_type, p.owner_id);
            if (home && target) {
              if (!s.agvKinematics[agvId])
                s.setAgvKinematics(agvId, { pos: home });
              s.setAgvTarget(agvId, target, 240);
              s.emitTrajectory({
                id: `tr-${p.object_id}`,
                path: `M ${home.x} ${home.y} Q ${(home.x + target.x) / 2} ${Math.min(home.y, target.y) - 60}, ${target.x} ${target.y}`,
                phantom: true,
                opacity: 0.5,
                fadeMsLeft: 2600,
              });
            }
          }

          // Dock moment: ownership crossed an arm↔asset boundary (direct
          // docking - there is no transfer node in this model).
          const crossedDock =
            prevKey &&
            ((prevKey.startsWith("arm:") && p.owner_type === "asset") ||
              (prevKey.startsWith("asset:") && p.owner_type === "arm") ||
              p.owner_type === "transfer_point");
          if (crossedDock) {
            const at =
              resolveOwnerPosition(s, p.owner_type, p.owner_id) ??
              (p.carrier_asset
                ? resolveOwnerPosition(
                    s,
                    "asset",
                    assetActorId(p.carrier_asset),
                  )
                : null);
            if (at)
              s.emitDockGlow({
                id: `dg-${p.object_id}`,
                at,
                intensity: 0.8,
                fadeMsLeft: 1400,
              });
            s.pushEvent({
              kind: "dock",
              actorId: p.carrier_asset
                ? assetActorId(p.carrier_asset)
                : undefined,
              text: `dock · ${p.object_id} → ${p.owner_id}`,
              tone: "info",
            });
          }
        },
      );

      socket.on(
        "agv_update",
        (p: {
          id: string;
          status?: string;
          color?: string;
          px?: { x: number; y: number } | null;
          target_px?: { x: number; y: number } | null;
          carried_object?: string | null;
          error?: string | null;
        }) => {
          const s = st();
          const existing = s.agvKinematics[p.id];
          const dest = p.target_px ?? p.px;
          if (!existing || !existing.pos) {
            // First sighting: drop the AGV at its real position (its bay) so
            // it doesn't animate in from the origin.
            if (p.px) s.setAgvKinematics(p.id, { pos: { x: p.px.x, y: p.px.y } });
          } else if (dest) {
            // Subsequent updates: set the new cell as a TARGET and let the
            // spatial tick() ease the body there — continuous vehicle motion
            // instead of cell-to-cell snapping. Speed MUST exceed the backend
            // cell cadence: AGV-1 at speed_factor 1.6 steps cells every 310ms
            // (cell_px 120 / 0.31s = 387 px/s minimum), so 260 px/s caused
            // the canvas token to lag and cut corners — looking like "AGV
            // ignores its own path". 600 px/s keeps the visual token snapped
            // to the AGV's logical cell even for the fastest AGV.
            s.setAgvTarget(p.id, { x: dest.x, y: dest.y }, 600);
          }
          const status =
            p.status === "faulted" || p.status === "stopped" ? "down"
            : p.status === "moving" || p.status === "loading" || p.status === "unloading" || p.status === "returning" ? "warn"
            : "ok";
          s.setActorRuntime(p.id, { status, phase: p.status ?? "" });
          if (p.carried_object !== undefined) {
            s.setAgvCargo(p.id, p.carried_object ?? null);
          }
        },
      );

      socket.on("arm_angle_update", (p: { arm_id: number; angle: number }) => {
        st().setArmAngle(`arm-${p.arm_id}`, p.angle);
      });

      socket.on(
        "table_update",
        (p: { station: string; packages: Array<{ id: string; color: string }> }) => {
          st().setTablePkgs(p.station, p.packages);
        },
      );

      socket.on("objects_reset", () => {
        objSeq.clear();
        objOwner.clear();
        st().clearSpatialRuntime();
        st().pushEvent({ kind: "object", text: "Objects reset", tone: "info" });
      });

      socket.on(
        "log_message",
        (p: {
          timestamp?: string;
          source?: string;
          message?: string;
          level?: string | null;
        }) => {
          // US 34.1: log lines go to the log panel only. The bus panel is now
          // fed by the canonical `bus` SocketIO event (see the handler above)
          // — the old heuristic classifyBus pass over log messages caused
          // duplicates AND missed events that never made it to the log stream.
          const s = st();
          s.pushLogLine(toLogLine(p));
          const now = performance.now();
          busWindow = busWindow.filter((t) => now - t < 1000);
          busWindow.push(now);
          // Throttle busRate to ~1 Hz so a high-frequency log stream doesn't
          // re-render bus-rate subscribers on every message.
          const sec = Math.floor(now / 1000);
          if (sec !== lastBusSec) {
            lastBusSec = sec;
            if (busWindow.length !== s.busRate)
              s.hydrateRuntime({ busRate: busWindow.length });
          }
        },
      );

      socket.on(
        "script_chosen",
        (p: {
          actor_id: number | string;
          arm_id?: number | null;
          script: string;
          timestamp?: string;
        }) => {
          // arm → numeric arm_id wins ("arm-N"); asset → actor_id is its
          // verbatim string id. One derivation, used for both writes.
          const id =
            p.arm_id != null
              ? armActorId(p.arm_id)
              : assetActorId(String(p.actor_id));
          st().setActorRuntime(id, { phase: p.script });
          st().pushBusMessage({
            id: `sc-${p.timestamp ?? ""}-${Math.random().toString(36).slice(2, 6)}`,
            ts: p.timestamp ?? "",
            kind: "bid",
            from: id,
            text: `→ ${p.script}`,
          });
          st().pushEvent({
            kind: "lifecycle",
            actorId: id,
            text: `${id} chose ${p.script}`,
            tone: "info",
            ts: p.timestamp,
          });
        },
      );

      const onTask = (p: {
        arm_id?: number;
        action?: string;
        status?: string;
      }) => {
        if (p.arm_id == null) return;
        st().setActorRuntime(armActorId(p.arm_id), {
          phase: p.action ?? p.status ?? "",
        });
      };
      socket.on("task_status", onTask);
      socket.on("task_complete", onTask);

      // Spatial rAF - same loop the mock driver uses; drives interpolation +
      // trace/glow fade. Spatial-only; never re-renders RF topology nodes.
      const loop = (t: number) => {
        if (last) store.getState().tick(t - last);
        last = t;
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
    },

    stop() {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      last = 0;
      socket?.removeAllListeners();
      socket?.disconnect();
      socket = null;
      storeRef = null;
    },

    // ── Phase 13 operational controls ──
    // Real backend container actions. After the call we reconcile runtime
    // from the backend (never optimistic). Health/asset socket events also
    // arrive independently; reconcile is the immediate confirmation.
    async stopActor(actorId: string): Promise<ActorOpResult> {
      const armN = armNumber(actorId);
      const url =
        armN != null
          ? `/arms/${armN}/stop`
          : `/assets/${encodeURIComponent(actorId)}/fail`;
      const r = await postOk(url);
      const st = storeRef?.getState();
      st?.pushEvent({
        kind: "lifecycle",
        actorId,
        text: r.ok
          ? `${actorId} stop requested · ${r.note}`
          : `${actorId} stop failed · ${r.note}`,
        tone: r.ok ? "warn" : "err",
      });
      await reconcileRuntime();
      return r;
    },

    async restartActor(actorId: string): Promise<ActorOpResult> {
      const armN = armNumber(actorId);
      const url =
        armN != null
          ? `/arms/${armN}/restart`
          : `/assets/${encodeURIComponent(actorId)}/recover`;
      const r = await postOk(url);
      const st = storeRef?.getState();
      st?.pushEvent({
        kind: "lifecycle",
        actorId,
        text: r.ok
          ? `${actorId} restart requested · ${r.note}`
          : `${actorId} restart failed · ${r.note}`,
        tone: r.ok ? "ok" : "err",
      });
      await reconcileRuntime();
      return r;
    },

    refreshRuntime(): Promise<void> {
      return reconcileRuntime();
    },
  };
}
