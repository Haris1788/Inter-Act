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
