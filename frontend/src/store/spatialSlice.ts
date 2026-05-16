import type { StateCreator } from "zustand";
import type { RootStore, SpatialSlice } from "./types";

// Ephemeral, frame-rate state. Never persisted, never written to YAML.
// `tick` is the seam the mock driver (now) and socket driver (later) drive.
export const createSpatialSlice: StateCreator<RootStore, [], [], SpatialSlice> = (set) => ({
  agvKinematics: {},
  trajectories: [],
  dockGlows: [],
  reachability: [],

  // Mock kinematics integration. Real motion will be backend-authoritative;
  // this only keeps the overlay alive during the UI-only phase.
  tick: (dtMs) =>
    set((s) => {
      const next: typeof s.agvKinematics = {};
      let changed = false;
      for (const [id, k] of Object.entries(s.agvKinematics)) {
        if (!k.target) {
          next[id] = k;
          continue;
        }
        const dx = k.target.x - k.pos.x;
        const dy = k.target.y - k.pos.y;
        const dist = Math.hypot(dx, dy);
        if (dist < 0.5) {
          next[id] = { ...k, target: null };
          changed = true;
          continue;
        }
        const step = (k.speed * dtMs) / 1000;
        const t = Math.min(1, step / dist);
        next[id] = {
          ...k,
          pos: { x: k.pos.x + dx * t, y: k.pos.y + dy * t },
          heading: Math.atan2(dy, dx),
        };
        changed = true;
      }
      return changed ? { agvKinematics: next } : {};
    }),

  hydrateSpatial: (patch) => set(() => ({ ...patch })),
});
