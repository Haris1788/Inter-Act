import { io, type Socket } from "socket.io-client";
import type { useStore } from "@/store";
import type { RuntimeDriver, ActorOpResult } from "./driver";
import { normalizeConfig, type RawConfig } from "./configNormalizer";
import { loadLocal } from "@/lib/persistence";
import type { BusKind } from "@/store/types";
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
          const { actors, zones, adjacency: adj } = normalizeConfig(cfg);
          adjacency = adj;
          // The authored topology (positions/labels) is the USER's document
          // and is restored from local storage by useAutosave. Backend
          // /config must NOT overwrite it on every (re)connect, or saved
          // layout reverts on refresh. Only seed from /config when there is
          // no saved workspace yet. Runtime (health/asset) is a separate
          // slice and keeps flowing regardless.
          const hasSaved = (loadLocal()?.actors?.length ?? 0) > 0;
          if (!hasSaved && Object.keys(actors).length)
            st().hydrateTopology({ actors, zones });
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
          ]);
          if (!KNOWN_KINDS.has(p.type)) return;

          const kind = p.type as BusKind;
          const ts =
            p.ts
              ? new Date(p.ts).toISOString().slice(11, 19)
              : new Date().toISOString().slice(11, 19);
          const from = p.sender ?? "system";

          // Build a human-readable text summary from payload fields.
          let text = "";
          if (p.task_id) text += p.task_id;
          if (p.slot_id) text += (text ? " · " : "") + `slot ${p.slot_id}`;
          if (typeof p.bid_version === "number")
            text += (text ? " · " : "") + `v${p.bid_version}`;
          if (p.payload && typeof p.payload === "object") {
            const pay = p.payload as Record<string, unknown>;
            if (typeof pay.score === "number")
              text += (text ? " · " : "") + `score ${pay.score.toFixed(2)}`;
            if (typeof pay.reason === "string")
              text += (text ? " · " : "") + pay.reason;
          }

          st().pushBusMessage({
            id: `bm-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            ts,
            kind,
            from,
            round: p.negotiation_round,
            task_id: p.task_id,
            text,
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

          // Phase 5.1 Stage 5 Track A — negotiation halo state.
          // Extract state from availability_update heartbeats so the canvas
          // can render a pulsing halo around actors currently in play.
          if (kind === "availability_update" && p.sender && p.payload) {
            const pay = p.payload as Record<string, unknown>;
            if (typeof pay.state === "string") {
              st().setActorRuntime(from, { negotiationState: pay.state });
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
