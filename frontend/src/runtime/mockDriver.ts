import type { RuntimeDriver, ActorOpResult } from "./driver";
import type { useStore } from "@/store";
import {
  mockActors,
  mockPool,
  mockGoal,
  mockActorRuntime,
  mockBus,
  mockBusRate,
  mockLogs,
  mockValidation,
  mockContracts,
  mockTrajectories,
  mockReachability,
  mockDockGlows,
} from "./mockData";

// Seeds all three slices from the prototype fixtures and runs a light rAF loop
// so the spatial overlay animates. No backend contact. Swapping this for
// socketDriver (same interface) is the only change needed next phase.
export function createMockDriver(): RuntimeDriver {
  let raf = 0;
  let last = 0;
  let storeRef: typeof useStore | null = null;

  return {
    start(store) {
      storeRef = store;
      const s = store.getState();
      s.setConnection("mock", "connected");
      s.hydrateTopology({ actors: mockActors, pool: mockPool, goal: mockGoal });
      s.hydrateRuntime({
        actorRuntime: mockActorRuntime,
        busMessages: mockBus,
        busRate: mockBusRate,
        logLines: mockLogs,
        validation: mockValidation,
        contracts: mockContracts,
      });
      s.hydrateSpatial({
        trajectories: mockTrajectories,
        reachability: mockReachability,
        dockGlows: mockDockGlows,
        agvKinematics: {},
      });
      // Seed a small operational timeline so the Events view is populated in
      // the standalone (mock) demo. Representative, not fabricated analytics.
      s.pushEvent({ kind: "link", text: "Mock runtime started", tone: "info" });
      s.pushEvent({ kind: "health", actorId: "arm-1", text: "arm-1 → alive", tone: "ok" });
      s.pushEvent({ kind: "asset", actorId: "veh-1", text: "veh-1 → busy · carrying obj-1", tone: "ok" });
      s.pushEvent({ kind: "dock", actorId: "veh-1", text: "dock · obj-1 → arm-2", tone: "info" });

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
      storeRef = null;
    },

    // Honest mock control: there is no container, so we mutate the mock
    // runtime directly. The topbar already labels the data source "mock",
    // so this is a truthful local effect, not a faked backend.
    stopActor(actorId: string): Promise<ActorOpResult> {
      const s = storeRef?.getState();
      if (!s) return Promise.resolve({ ok: false, note: "mock not started" });
      s.setActorRuntime(actorId, { status: "down", phase: "stopped" });
      s.pushEvent({
        kind: "lifecycle",
        actorId,
        text: `${actorId} stopped (mock runtime)`,
        tone: "warn",
      });
      return Promise.resolve({ ok: true, note: "mock actor stopped" });
    },

    restartActor(actorId: string): Promise<ActorOpResult> {
      const s = storeRef?.getState();
      if (!s) return Promise.resolve({ ok: false, note: "mock not started" });
      s.setActorRuntime(actorId, { status: "idle", phase: "idle" });
      s.pushEvent({
        kind: "lifecycle",
        actorId,
        text: `${actorId} restarted (mock runtime)`,
        tone: "ok",
      });
      return Promise.resolve({ ok: true, note: "mock actor restarted" });
    },

    refreshRuntime(): Promise<void> {
      // Mock state is authoritative locally; nothing to reconcile.
      return Promise.resolve();
    },
  };
}
