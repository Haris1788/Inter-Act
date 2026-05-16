import { io, type Socket } from "socket.io-client";
import type { RuntimeDriver } from "./driver";
import { normalizeConfig, type RawConfig } from "./configNormalizer";
import {
  armActorId,
  assetActorId,
  armHealthToStatus,
  assetStatusToStatus,
  toLogLine,
  classifyBus,
  resolveOwnerPosition,
} from "@/lib/contract";

// Live runtime. Adapts to the EXISTING backend contract (no new events).
// Same-origin: nginx proxies /socket.io and the REST API to Flask; the Vite
// dev server proxies them too (vite.config.ts).
async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T | null> {
  try {
    const r = await fetch(url, { signal });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export function createSocketDriver(): RuntimeDriver {
  let socket: Socket | null = null;
  let raf = 0;
  let last = 0;
  let busWindow: number[] = [];
  let lastBusSec = 0;
  // event_seq guard per object (mirror backend stale-drop). Capped — old
  // object ids are evicted FIFO so a long run can't grow these unbounded.
  const OBJ_CAP = 500;
  const objSeq = new Map<string, number>();
  // actorId → downstream actorIds (from /config asset_graph).
  let adjacency: Record<string, string[]> = {};
  // object_id → last owner key, for detecting dock moments.
  const objOwner = new Map<string, string>();

  return {
    start(store) {
      const st = () => store.getState();
      st().setConnection("live", "connecting");

      const bootstrap = async () => {
        const cfg = await getJSON<RawConfig>("/config");
        if (cfg) {
          const { actors, zones, adjacency: adj } = normalizeConfig(cfg);
          adjacency = adj;
          if (Object.keys(actors).length) st().hydrateTopology({ actors, zones });
        }
        const [state, objects, arms, assets] = await Promise.all([
          getJSON<{ arms: { arm_id: number; angle: number; status: string }[] }>("/state"),
          getJSON<{ objects: { object_id: string; owner_type: string; owner_id: string; event_seq: number }[] }>("/objects"),
          getJSON<{ arms: Record<string, { health: string; error: string | null }> }>("/arms/health"),
          getJSON<{ assets: Record<string, { status: string; carried_object: string | null }> }>("/assets/state"),
        ]);
        state?.arms?.forEach((a) =>
          st().setActorRuntime(armActorId(a.arm_id), { status: armHealthToStatus(a.status), phase: a.status }),
        );
        if (arms?.arms)
          for (const [id, h] of Object.entries(arms.arms))
            st().setActorRuntime(armActorId(id), { status: armHealthToStatus(h.health), phase: h.health });
        if (assets?.assets)
          for (const [id, a] of Object.entries(assets.assets))
            st().setActorRuntime(assetActorId(id), { status: assetStatusToStatus(a.status), phase: a.status });
        // Never regress the dedup guard below an event_seq already seen from
        // an in-flight live event that arrived before this bootstrap resolved.
        objects?.objects?.forEach((o) =>
          objSeq.set(o.object_id, Math.max(o.event_seq, objSeq.get(o.object_id) ?? -1)),
        );
      };

      socket = io({ path: "/socket.io", transports: ["websocket", "polling"], reconnection: true });

      socket.on("connect", () => {
        st().setConnection("live", "connected");
        // Resilience: a backend restart resets its event_seq counter. Clearing
        // the per-object dedup maps on every (re)connect prevents post-restart
        // object events being wrongly dropped as stale. Bootstrap reseeds them.
        objSeq.clear();
        objOwner.clear();
        busWindow = [];
        void bootstrap();
      });
      socket.on("disconnect", () => st().setConnection("live", "disconnected"));
      socket.io.on("reconnect_attempt", () => st().setConnection("live", "connecting"));

      socket.on("state_update", (p: { arms?: { arm_id: number; status: string }[] }) => {
        p.arms?.forEach((a) =>
          st().setActorRuntime(armActorId(a.arm_id), { status: armHealthToStatus(a.status), phase: a.status }),
        );
      });

      socket.on("arm_health", (p: { arm_id: number; health: string; error?: string | null }) => {
        st().setActorRuntime(armActorId(p.arm_id), {
          status: armHealthToStatus(p.health),
          phase: p.error ? `${p.health} · ${p.error}` : p.health,
        });
      });

      socket.on(
        "asset_health",
        (p: { asset_id: string; status: string; carried_object?: string | null; error?: string | null }) => {
          st().setActorRuntime(assetActorId(p.asset_id), {
            status: assetStatusToStatus(p.status),
            phase: p.status,
          });
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
              (downstream && resolveOwnerPosition(s, s.actors[downstream]?.kind === "agv" ? "asset" : "arm", downstream)) ||
              resolveOwnerPosition(s, p.owner_type, p.owner_id);
            if (home && target) {
              if (!s.agvKinematics[agvId]) s.setAgvKinematics(agvId, { pos: home });
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
          // docking — there is no transfer node in this model).
          const crossedDock =
            prevKey &&
            ((prevKey.startsWith("arm:") && p.owner_type === "asset") ||
              (prevKey.startsWith("asset:") && p.owner_type === "arm") ||
              p.owner_type === "transfer_point");
          if (crossedDock) {
            const at =
              resolveOwnerPosition(s, p.owner_type, p.owner_id) ??
              (p.carrier_asset ? resolveOwnerPosition(s, "asset", assetActorId(p.carrier_asset)) : null);
            if (at) s.emitDockGlow({ id: `dg-${p.object_id}`, at, intensity: 0.8, fadeMsLeft: 1400 });
          }
        },
      );

      socket.on("objects_reset", () => {
        objSeq.clear();
        objOwner.clear();
        st().clearSpatialRuntime();
      });

      socket.on(
        "log_message",
        (p: { timestamp?: string; source?: string; message?: string; level?: string | null }) => {
          const s = st();
          s.pushLogLine(toLogLine(p));
          const now = performance.now();
          busWindow = busWindow.filter((t) => now - t < 1000);
          busWindow.push(now);
          const bus = classifyBus(p.source ?? "system", p.message ?? "");
          if (bus) {
            s.pushBusMessage({
              id: `b-${now.toFixed(0)}-${Math.random().toString(36).slice(2, 6)}`,
              ts: p.timestamp ?? "",
              kind: bus.kind,
              from: bus.from,
              text: bus.text,
            });
          }
          // Throttle busRate to ~1 Hz so a high-frequency log stream doesn't
          // re-render bus-rate subscribers on every message.
          const sec = Math.floor(now / 1000);
          if (sec !== lastBusSec) {
            lastBusSec = sec;
            if (busWindow.length !== s.busRate) s.hydrateRuntime({ busRate: busWindow.length });
          }
        },
      );

      socket.on(
        "script_chosen",
        (p: { actor_id: number | string; arm_id?: number | null; script: string; timestamp?: string }) => {
          // arm → numeric arm_id wins ("arm-N"); asset → actor_id is its
          // verbatim string id. One derivation, used for both writes.
          const id = p.arm_id != null ? armActorId(p.arm_id) : assetActorId(String(p.actor_id));
          st().setActorRuntime(id, { phase: p.script });
          st().pushBusMessage({
            id: `sc-${p.timestamp ?? ""}-${Math.random().toString(36).slice(2, 6)}`,
            ts: p.timestamp ?? "",
            kind: "bid",
            from: id,
            text: `→ ${p.script}`,
          });
        },
      );

      const onTask = (p: { arm_id?: number; action?: string; status?: string }) => {
        if (p.arm_id == null) return;
        st().setActorRuntime(armActorId(p.arm_id), { phase: p.action ?? p.status ?? "" });
      };
      socket.on("task_status", onTask);
      socket.on("task_complete", onTask);

      // Spatial rAF — same loop the mock driver uses; drives interpolation +
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
    },
  };
}
