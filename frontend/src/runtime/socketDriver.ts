import { io, type Socket } from "socket.io-client";
import type { useStore } from "@/store";
import type { RuntimeDriver, ActorOpResult } from "./driver";
import { normalizeConfig, type RawConfig } from "./configNormalizer";
import type { BusKind, BusMessage } from "@/store/types";
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

export function createSocketDriver(): RuntimeDriver {
  let socket: Socket | null = null;
  let raf = 0;
  let last = 0;
  // event_seq guard per object (mirror backend stale-drop). Capped - old
  // object ids are evicted FIFO so a long run can't grow these unbounded.
  const OBJ_CAP = 500;
  const objSeq = new Map<string, number>();
  // last emitted status per actor - dedup so observability records only real
  // transitions, never per-poll repeats.
  const lastStatus = new Map<string, string>();
  // Per-actor heartbeat state dedup map — tracks the last negotiation state
  // seen for each actor from AVAILABILITY_UPDATE heartbeats. When the state
  // is unchanged, the bus message is suppressed from the UI stream.
  // "seen" = actor appeared at least once (first-sighting gets one push).
  const lastHeartbeatState = new Map<string, string>();
  // actorId → downstream actorIds (from /config asset_graph).
  let adjacency: Record<string, string[]> = {};
  // object_id → last owner key, for detecting dock moments.
  const objOwner = new Map<string, string>();
  // Captured at start() so the Phase 13 operational controls can reconcile
  // store state from the backend without holding a separate store ref.
  let storeRef: typeof useStore | null = null;

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
          const { adjacency: adj } = normalizeConfig(cfg);
          adjacency = adj;
          // Phase 5.1: canvas is the source of truth for topology.
          // /config no longer auto-seeds the canvas. Runtime-only entities
          // (backend reconciler-spawned agv-1/2/3, arm-1/2) populate the
          // actorRuntime slice for Fleet Roster / Inspector observability,
          // NOT the authored topology slice. The authored topology is
          // restored from localStorage by useAutosave exclusively.
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
        lastStatus.clear();
        lastHeartbeatState.clear();
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
            // instead of cell-to-cell snapping. Speed is tuned ABOVE the
            // backend cell cadence (120px / 0.5s = 240px/s) so the visual
            // token tracks the AGV's logical cell instead of lagging behind
            // it; otherwise arm/load choreography (which keys off the logical
            // cell) fires while the token is still mid-path.
            s.setAgvTarget(p.id, { x: dest.x, y: dest.y }, 260);
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

      // Stage 5 Track B — live AGV pixel-space position at ~10 Hz.
      // Emitted by the backend spatial movement loop (_traverse_to).
      // 1. Stores runtimePosition in the runtime slice (observational, never
      //    sent back to the backend).
      // 2. Drives the existing spatial kinematics tween so tick() interpolates
      //    smoothly between updates (ANIM_DURATION ≈ 200 ms, well below the
      //    100 ms × 2 = 200 ms catch-up threshold).
      socket.on(
        "agv_position",
        (p: { actor_id: string; x: number; y: number; theta: number; ts: number }) => {
          const s = st();
          // Store raw observational position (Track C surfaces this for polish).
          s.setRuntimePosition(p.actor_id, { x: p.x, y: p.y, theta: p.theta, ts: p.ts });
          // Feed the spatial tween so the token moves smoothly.
          const existing = s.agvKinematics[p.actor_id];
          if (!existing || !existing.pos) {
            // First sighting: place the token at the reported position without
            // animation so it doesn't slide in from a stale location.
            s.setAgvKinematics(p.actor_id, {
              pos: { x: p.x, y: p.y },
              heading: p.theta,
            });
          } else {
            // Subsequent updates: set new position as the interpolation target.
            // tick() will ease there; speed=260 px/s gives ~200 ms over a 52 px
            // step (typical at 10 Hz × 5 px/step), satisfying ANIM_DURATION < 2×cadence.
            s.setAgvTarget(p.actor_id, { x: p.x, y: p.y }, 260);
          }
        },
      );

      // Phase 5.1 Stage 4 — AGV planned path overlay.
      // Emitted by backend after successful A* + reserve, before traversal begins.
      // Observational only: no consensus decisions depend on this event.
      socket.on(
        "agv_path",
        (p: {
          actor_id: string;
          task_id: string;
          epoch: number;
          path_cells: [number, number][];
          ts: number;
        }) => {
          st().setAgvPath(p.actor_id, {
            cells: p.path_cells,
            epoch: p.epoch,
            ts: p.ts,
          });
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
          st().pushLogLine(toLogLine(p));
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
          st().pushEvent({
            kind: "lifecycle",
            actorId: id,
            text: `${id} chose ${p.script}`,
            tone: "info",
            ts: p.timestamp,
          });
        },
      );

      // ── Real NegotiationBus events ──────────────────────────────────────────
      // The backend mirrors every Message from the NegotiationBus as a
      // `bus_message` SocketIO event. The `type` field IS the BusKind - no
      // regex inference, no fabrication.
      socket.on(
        "bus_message",
        (p: {
          type: string;
          task_id?: string;
          negotiation_round?: number;
          sender?: string;
          slot_id?: string;
          bid_version?: number;
          ts?: string;
          payload?: unknown;
        }) => {
          // Validate that the type is a known BusKind; drop silently if not.
          const KNOWN_KINDS = new Set<string>([
            "task_announce",
            "capability_broadcast",
            "availability_update",
            "bid",
            "counter_offer",
            "accept",
            "reject",
            "busy",
            "fault",
            "withdraw",
            "consensus_reached",
            "task_failed",
            // Stage 4 leg execution events
            "leg_started",
            "leg_completed",
            "leg_halted",
            // Stage 8 — bus-mirrored position update (drives canvas motion
            // in distributed mode where actor SocketIO is not browser-bound)
            "position_update",
          ]);
          if (!KNOWN_KINDS.has(p.type)) return;

          const kind = p.type as BusKind;
          // Backend Message.ts is Python time.time() — SECONDS since epoch.
          // JS Date constructor expects MILLISECONDS. Multiply by 1000 or
          // the bus pane renders 1970-era timestamps that look reasonable
          // ("14:19:21") but are actually 21 days into 1970.
          const ts =
            typeof p.ts === "number"
              ? new Date(p.ts * 1000).toISOString().slice(11, 19)
              : new Date().toISOString().slice(11, 19);
          const from = p.sender ?? "system";

          // Phase 5.1 Item 4 — heartbeat dedup for AVAILABILITY_UPDATE.
          // Always update negotiationState (NegotiationHaloLayer relies on it).
          // Only push to the bus message stream for first-sighting or state changes.
          if (kind === "availability_update" && p.payload) {
            const pay = p.payload as Record<string, unknown>;
            const isHeartbeat = pay.heartbeat === true;
            const newState = typeof pay.state === "string" ? pay.state : undefined;

            // Always update negotiation halo state regardless of dedup.
            if (newState !== undefined) {
              st().setActorRuntime(from, { negotiationState: newState });
            }

            if (isHeartbeat && newState !== undefined) {
              const prevState = lastHeartbeatState.get(from);

              if (prevState === undefined) {
                // First sighting — push a compact "alive" event.
                lastHeartbeatState.set(from, newState);
                st().pushBusMessage({
                  id: `bm-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                  ts,
                  kind,
                  from,
                  round: p.negotiation_round,
                  task_id: p.task_id,
                  text: `${from} alive`,
                });
              } else if (prevState !== newState) {
                // State changed — push a transition event.
                lastHeartbeatState.set(from, newState);
                st().pushBusMessage({
                  id: `bm-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                  ts,
                  kind,
                  from,
                  round: p.negotiation_round,
                  task_id: p.task_id,
                  text: `${from}: ${prevState} → ${newState}`,
                });
              }
              // else: same state, same sender — silently suppress (heartbeat spam).
              return;
            }
            // Non-heartbeat availability_update (manually published) — fall through
            // to normal pushBusMessage below, after building text.
          }

          // Build a human-readable text summary from payload fields.
          let text = "";
          if (p.task_id) text += p.task_id;
          if (p.slot_id) text += (text ? " · " : "") + `slot ${p.slot_id}`;
          // v0 is the default first-bid revision — show only when re-bid
          // (v >= 1) so the bus pane stops being flooded with "· v0" noise.
          if (typeof p.bid_version === "number" && p.bid_version > 0)
            text += (text ? " · " : "") + `v${p.bid_version}`;
          if (p.payload && typeof p.payload === "object") {
            const pay = p.payload as Record<string, unknown>;
            if (typeof pay.score === "number")
              text += (text ? " · " : "") + `score ${pay.score.toFixed(2)}`;
            if (typeof pay.reason === "string")
              text += (text ? " · " : "") + pay.reason;
            // Render transition label for collapsed heartbeat messages
            // (non-heartbeat availability_update that got a transition text set).
            if (typeof pay.transition === "string")
              text = pay.transition;
          }

          // Phase-6 rationale capture — only consensus_reached carries this.
          let rationale: BusMessage["rationale"];
          let constraint: string | undefined;
          if (kind === "consensus_reached" && p.payload && typeof p.payload === "object") {
            const pay = p.payload as Record<string, unknown>;
            if (Array.isArray(pay.rationale)) {
              rationale = pay.rationale as BusMessage["rationale"];
            }
            if (typeof pay.constraint === "string") constraint = pay.constraint;
          }

          st().pushBusMessage({
            id: `bm-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            ts,
            kind,
            from,
            round: p.negotiation_round,
            task_id: p.task_id,
            text,
            rationale,
            constraint,
          });

          // Surface fault/task_failed/leg_halted as operational events too.
          if (kind === "fault" || kind === "task_failed" || kind === "leg_halted") {
            st().pushEvent({
              kind: "lifecycle",
              text: `${kind} · ${text || from}`,
              tone: "err",
              ts,
            });
          }

          // Phase 5.1 Stage 4 — clear path preview after transport leg completes.
          // leg_completed from an agv actor (slot_id = "transport") means the AGV
          // has finished its planned path; the dashed overlay should be cleared.
          if (kind === "leg_completed" && p.slot_id === "transport" && p.sender) {
            st().clearAgvPath(p.sender);
          }

          // Phase 5.1 Fix — drive AGV canvas motion from POSITION_UPDATE on the
          // bus stream. Actor containers publish agv_position via their own
          // (disconnected) SocketIO instance, so the dedicated agv_position
          // handler above never fires in distributed mode. The bus mirror DOES
          // forward POSITION_UPDATE to the browser as bus_message, so translate
          // that into the same setRuntimePosition + setAgvTarget flow used by
          // the in-process agv_position event.
          // Phase-6 payload lifecycle — spawn on PAYLOAD_SPAWN, follow carrier
          // on PAYLOAD_TRANSFER. Pure derived rendering; no consensus impact.
          if (kind === "payload_spawn" && p.payload) {
            const pay = p.payload as Record<string, unknown>;
            const objId = typeof pay.obj_id === "string" ? pay.obj_id : null;
            const posArr = Array.isArray(pay.pos) ? pay.pos : null;
            const pos =
              posArr && posArr.length >= 2
                ? { x: Number(posArr[0]), y: Number(posArr[1]) }
                : null;
            if (objId) st().spawnPayload(objId, pos);
          }
          if (kind === "payload_transfer" && p.payload) {
            const pay = p.payload as Record<string, unknown>;
            const objId = typeof pay.obj_id === "string" ? pay.obj_id : null;
            const from = typeof pay.from === "string" ? pay.from : null;
            const to = typeof pay.to === "string" ? pay.to : null;
            if (objId) {
              const s = st();
              // If handing off TO a zone, snapshot the current carrier's
              // live pos into payload.pos so PayloadLayer has a coord to draw
              // at after the actor leaves the carrier slot.
              if (to && to.startsWith("zone:") && from) {
                const fromKin = s.agvKinematics[from];
                const fromActor = s.actors[from];
                const pos = fromKin?.pos ?? fromActor?.position ?? null;
                if (pos) s.spawnPayload(objId, pos);
              }
              s.transferPayload(objId, to);
            }
          }

          if (kind === "position_update" && p.sender && p.payload) {
            const pay = p.payload as Record<string, unknown>;
            const x = typeof pay.x === "number" ? pay.x : null;
            const y = typeof pay.y === "number" ? pay.y : null;
            const theta = typeof pay.theta === "number" ? pay.theta : 0;
            const posTs = typeof pay.ts === "number" ? pay.ts : 0;
            if (x !== null && y !== null) {
              const s = st();
              s.setRuntimePosition(p.sender, { x, y, theta, ts: posTs });
              const existing = s.agvKinematics[p.sender];
              if (!existing || !existing.pos) {
                s.setAgvKinematics(p.sender, { pos: { x, y }, heading: theta });
              } else {
                // POSITION_UPDATE cadence is ~500 ms; bump tween speed
                // accordingly so the token reaches the target before the
                // next update arrives (else it visibly stalls).
                s.setAgvTarget(p.sender, { x, y }, 120);
              }
            }
          }
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
