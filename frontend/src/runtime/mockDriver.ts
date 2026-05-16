import type { RuntimeDriver } from "./driver";
import {
  mockActors,
  mockZones,
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

  return {
    start(store) {
      const s = store.getState();
      s.setConnection("mock", "connected");
      s.hydrateTopology({ actors: mockActors, zones: mockZones, pool: mockPool, goal: mockGoal });
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
    },
  };
}
