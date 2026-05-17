import type { StateCreator } from "zustand";
import type { AgvKinematics, RootStore, SpatialSlice } from "./types";

const DEFAULT_KIN: AgvKinematics = {
  pos: { x: 0, y: 0 },
  heading: 0,
  speed: 220,
  target: null,
};

// Ephemeral, frame-rate state. Never persisted, never written to YAML.
// `tick` interpolates AGV motion and fades runtime traces/glows.
// Stays slice-pure: position resolution lives in the driver, not here.
export const createSpatialSlice: StateCreator<
  RootStore,
  [],
  [],
  SpatialSlice
> = (set) => ({
  agvKinematics: {},
  trajectories: [],
  dockGlows: [],
  reachability: [],

  tick: (dtMs) =>
    set((s) => {
      let changed = false;

      // AGV interpolation toward target.
      const nextK: typeof s.agvKinematics = {};
      for (const [id, k] of Object.entries(s.agvKinematics)) {
        if (!k.target) {
          nextK[id] = k;
          continue;
        }
        const dx = k.target.x - k.pos.x;
        const dy = k.target.y - k.pos.y;
        const dist = Math.hypot(dx, dy);
        if (dist < 0.5) {
          nextK[id] = { ...k, pos: k.target, target: null, origin: null };
          changed = true;
          continue;
        }
        // Accel/decel easing - operational, not gamey. Velocity ramps up over
        // the first ~18% of the leg and eases down over the last ~22%, with a
        // floor so the AGV never stalls. Falls back to constant speed if the
        // leg origin is unknown.
        const origin = k.origin ?? null;
        let vf = 1;
        if (origin) {
          const legLen =
            Math.hypot(k.target.x - origin.x, k.target.y - origin.y) || dist;
          const traveled = Math.max(0, legLen - dist);
          const p = Math.min(1, Math.max(0, traveled / legLen));
          const accel = Math.min(1, p / 0.18);
          const decel = Math.min(1, (1 - p) / 0.22);
          vf = Math.max(0.22, Math.min(accel, decel, 1));
        }
        const step = (k.speed * vf * dtMs) / 1000;
        const t = Math.min(1, step / dist);
        nextK[id] = {
          ...k,
          pos: { x: k.pos.x + dx * t, y: k.pos.y + dy * t },
          heading: Math.atan2(dy, dx),
        };
        changed = true;
      }

      // Fade runtime traces (fadeMsLeft != null). Persistent (null) untouched.
      let traj = s.trajectories;
      if (traj.some((t) => t.fadeMsLeft != null)) {
        traj = traj
          .map((t) =>
            t.fadeMsLeft == null
              ? t
              : {
                  ...t,
                  fadeMsLeft: t.fadeMsLeft - dtMs,
                  opacity: Math.max(0, (t.fadeMsLeft - dtMs) / 1800) * 0.6,
                },
          )
          .filter((t) => t.fadeMsLeft == null || t.fadeMsLeft > 0);
        changed = true;
      }

      let glows = s.dockGlows;
      if (glows.some((g) => g.fadeMsLeft != null)) {
        glows = glows
          .map((g) =>
            g.fadeMsLeft == null
              ? g
              : {
                  ...g,
                  fadeMsLeft: g.fadeMsLeft - dtMs,
                  intensity: Math.max(0, (g.fadeMsLeft - dtMs) / 1400),
                },
          )
          .filter((g) => g.fadeMsLeft == null || g.fadeMsLeft > 0);
        changed = true;
      }

      return changed
        ? { agvKinematics: nextK, trajectories: traj, dockGlows: glows }
        : {};
    }),

  setAgvKinematics: (id, patch) =>
    set((s) => ({
      agvKinematics: {
        ...s.agvKinematics,
        [id]: { ...DEFAULT_KIN, ...s.agvKinematics[id], ...patch },
      },
    })),

  setAgvTarget: (id, target, speed) =>
    set((s) => {
      const cur = s.agvKinematics[id] ?? {
        pos: target ?? { x: 0, y: 0 },
        heading: 0,
        speed: 220,
        target: null,
      };
      return {
        agvKinematics: {
          ...s.agvKinematics,
          // record the leg origin so tick() can ease accel/decel
          [id]: {
            ...cur,
            target,
            origin: { ...cur.pos },
            speed: speed ?? cur.speed,
          },
        },
      };
    }),

  emitTrajectory: (t) =>
    set((s) => ({
      trajectories: [...s.trajectories.filter((x) => x.id !== t.id), t].slice(
        -12,
      ),
    })),

  emitDockGlow: (g) =>
    set((s) => ({
      dockGlows: [...s.dockGlows.filter((x) => x.id !== g.id), g].slice(-8),
    })),

  clearSpatialRuntime: () =>
    set((s) => ({
      // keep persistent (fadeMsLeft == null) items, drop runtime ones
      trajectories: s.trajectories.filter((t) => t.fadeMsLeft == null),
      dockGlows: s.dockGlows.filter((g) => g.fadeMsLeft == null),
      agvKinematics: {},
    })),

  hydrateSpatial: (patch) => set(() => ({ ...patch })),
});
