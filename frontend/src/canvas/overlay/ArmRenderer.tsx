// ArmRenderer — Stage 6 Track A: arm articulation visualisation.
// Stage 6 Track C: gripper-close gesture + idle breathing pulse (additive).
//
// Renders one Konva group per arm actor (kind="arm") in the topology slice.
// Each group shows:
//   · Pivot circle (shoulder joint) at the actor's authored canvas position.
//   · Rotating stick (upper arm) at angle θ from backend `arm_angle_update`.
//   · Gripper rectangle at the stick tip.
//   · Actor-id label below the pivot.
//   · [Track C] Idle breathing ring on the pivot (subtle pulse, 2-s period).
//   · [Track C] Gripper-close animation during pickup/place.
//
// Angle convention (matches backend actuation.py sweep_arm):
//   θ=0   → RIGHT  (+x in screen space)
//   θ=90  → DOWN   (+y in screen space, Konva's +y axis)
//   θ=180 → LEFT   (-x in screen space)
//   θ=270 → UP     (-y in screen space)
//
// Konva y-axis: +y is DOWN on screen.  The backend emits standard math
// angles where 0=right and angles increase counter-clockwise in math coords.
// On screen (y-down), counter-clockwise in math = clockwise visually, so
// the rotation passed to Konva's `rotation` prop is used DIRECTLY (degrees)
// — no negation needed — because Konva's rotation goes clockwise (matching
// screen-y-down convention) and the backend emits angles in the same
// screen-down frame (PICK=180=left, PLACE=0=right, REST=90=down).
//
// Tween: angle changes are animated using a per-arm RAF interpolation (lerp)
// over ARM_TWEEN_MS. This avoids Konva's imperative Tween API and integrates
// cleanly with the existing RAF-driven `tick()` spatial slice. A local ref
// tracks displayed vs. target angle so the render is always frame-smooth.
//
// ── Track C: Gripper state heuristic ────────────────────────────────────────
// Chosen: angle-based (simpler than parsing LEG_* bus messages).
// When `arm_angle_update` arrives, record `angleChangedAt` (performance.now())
// per arm in a ref. Gripper width is computed each frame:
//   · If the settled target angle is near PICK (≈180°) or PLACE (≈0°)
//     AND the update was received within GRIPPER_CLOSE_WINDOW_MS → gripper
//     closes (animate width 8→3 px over GRIPPER_ANIM_MS).
//   · Otherwise gripper opens (animate width →8 px).
// "Near" = within ANGLE_SNAP_DEG of the landmark angle (accounts for float
// rounding in sweep_arm). No bus subscription needed; purely additive.
//
// ── Track C: Idle breathing pulse ──────────────────────────────────────────
// A concentric ring on the pivot oscillates radius 6→7 px and opacity 0.15→0
// with a 2-second sinusoidal period. Active when no arm_angle_update has
// arrived for > IDLE_THRESHOLD_MS (arm is presumed idle/resting).
// Breathing reuses the Track A RAF loop — no second requestAnimationFrame.
// The RAF is kept running at low cost (continuous sin eval) so the pulse
// stays smooth even when no tween is active. This is the same approach used
// by AgvTokenLayer's pulsePhase pattern.
//
// ── Track B insertion point ──────────────────────────────────────────────────
// TODO(Stage 6 Track B): add a dashed reach-radius circle per arm.
//   <Circle
//     x={0} y={0}
//     radius={arm.reach_px}
//     stroke={armColor} strokeWidth={1} dash={[4, 6]} opacity={0.25}
//     listening={false}
//   />
//   Mount it inside the arm Group, before the stick, so it renders below.

import { useEffect, useRef, useState } from "react";
import { Layer, Group, Circle, Rect, Line, Text } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import { useActors, useArmAngles } from "@/store/selectors";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Stick length as a fraction of arm.reach_px (40 % of radial work envelope). */
const STICK_FRACTION = 0.4;

/** Pivot circle radius in canvas pixels. */
const PIVOT_RADIUS = 6;

/** Pivot inner highlight radius. */
const PIVOT_INNER_RADIUS = 2.5;

/** Stroke width of the arm stick. */
const STICK_WIDTH = 3;

/** Gripper rectangle dimensions at the stick tip. */
const GRIPPER_W = 8;
const GRIPPER_H = 4;

/** [Track C] Closed gripper width in canvas pixels. */
const GRIPPER_W_CLOSED = 3;

/** Default color when topology actor has no color field. */
const DEFAULT_ARM_COLOR = "#c87941";

/** Animation duration for angle tween (ms). */
const ARM_TWEEN_MS = 300;

/** Default arm reach when not specified (px). */
const DEFAULT_REACH_PX = 120;

// ── Track C constants ──────────────────────────────────────────────────────

/** Window after an angle update arrives at a PICK/PLACE angle during which
 *  the gripper is considered to be closing/closed (ms). 1 000 ms matches the
 *  "< 1 s" spec and covers the full arm sweep to the station. */
const GRIPPER_CLOSE_WINDOW_MS = 1000;

/** Duration of the gripper open/close width animation (ms). */
const GRIPPER_ANIM_MS = 400;

/** Angle snap tolerance: angles within ±this many degrees of PICK/PLACE/REST
 *  landmarks are treated as being at that landmark. Float sweep output from
 *  actuation.py is exact but we add a small buffer for safety. */
const ANGLE_SNAP_DEG = 5;

/** Inactivity threshold before the idle breathing pulse activates (ms).
 *  1 500 ms — arms that have been at REST for 1.5 s are considered idle. */
const IDLE_THRESHOLD_MS = 1500;

/** Breathing pulse period (ms). */
const BREATH_PERIOD_MS = 2000;

/** Breathing pulse outer ring: max radius delta above PIVOT_RADIUS (px). */
const BREATH_RADIUS_DELTA = 1;

/** Breathing pulse max opacity. Intentionally very subtle (not distracting). */
const BREATH_OPACITY_MAX = 0.18;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Interpolate angle via the short arc (handles 0↔359 wrap correctly). */
function lerpAngle(from: number, to: number, t: number): number {
  let diff = ((to - from + 540) % 360) - 180; // shortest arc in [-180, 180]
  return from + diff * t;
}

/** Linear interpolation. */
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Stable color: prefer actor.color if it exists as a runtime extra, else default. */
function actorColor(
  actor: Record<string, unknown> | undefined,
  fallback = DEFAULT_ARM_COLOR,
): string {
  if (!actor) return fallback;
  const c = actor["color"];
  return typeof c === "string" ? c : fallback;
}

/** True if `angle` is within ANGLE_SNAP_DEG of `landmark` (mod 360). */
function nearAngle(angle: number, landmark: number): boolean {
  const diff = Math.abs(((angle - landmark + 540) % 360) - 180);
  return diff <= ANGLE_SNAP_DEG;
}

// ---------------------------------------------------------------------------
// Per-arm tween state (kept outside React so RAF closures read fresh values).
// ---------------------------------------------------------------------------

interface ArmTween {
  displayAngle: number;
  targetAngle: number;
  fromAngle: number;
  tweenStart: number | null; // performance.now() when tween started
}

// ---------------------------------------------------------------------------
// Per-arm Track C state (refs, no React state — mutated inside RAF).
// ---------------------------------------------------------------------------

interface ArmGripperState {
  /** Current displayed width (px, animated). */
  displayWidth: number;
  /** Target width (GRIPPER_W or GRIPPER_W_CLOSED). */
  targetWidth: number;
  /** Width animation start time. */
  animStart: number | null;
  /** Width at animation start. */
  fromWidth: number;
  /** performance.now() of the last angle update. */
  angleChangedAt: number;
}

// ---------------------------------------------------------------------------
// ArmRenderer
// ---------------------------------------------------------------------------

export function ArmRenderer({ tx }: { tx: ViewportTransform }) {
  const actors = useActors();
  const armAngles = useArmAngles();

  // Per-arm tween state. Keyed by actor id. Stored in a ref so the RAF
  // closure always sees the latest target without triggering re-renders.
  const tweenRef = useRef<Record<string, ArmTween>>({});

  // [Track C] Per-arm gripper animation state. Ref so RAF sees fresh values.
  const gripperRef = useRef<Record<string, ArmGripperState>>({});

  // Displayed angles — React state so changes trigger canvas repaints.
  const [displayAngles, setDisplayAngles] = useState<Record<string, number>>(
    {},
  );

  // [Track C] Render-triggering state for gripper widths + breathing phase.
  // `renderToken` is a monotonically incrementing counter bumped each RAF
  // frame when breathing or gripper animation is active. This avoids storing
  // the full gripper/breath map in React state (which would cause extra
  // renders for every field change).
  const [renderToken, setRenderToken] = useState(0);

  // [Track C] Breathing phase accumulator (ms since epoch mod BREATH_PERIOD).
  // Kept as a ref; read each frame inside the RAF without triggering state.
  const breathPhaseRef = useRef<number>(performance.now() % BREATH_PERIOD_MS);

  // Collect arm actors from the topology slice.
  const armActors = Object.values(actors).filter((a) => a.kind === "arm");

  // When armAngles slice changes, prime the tween for each arm AND record
  // the angle-change timestamp for the gripper heuristic (Track C).
  useEffect(() => {
    const now = performance.now();
    let changed = false;
    for (const actor of armActors) {
      const target = armAngles[actor.id] ?? 90; // REST=90 by default
      const existing = tweenRef.current[actor.id];
      if (!existing) {
        // First sighting: snap to target with no animation.
        tweenRef.current[actor.id] = {
          displayAngle: target,
          targetAngle: target,
          fromAngle: target,
          tweenStart: null,
        };
        changed = true;
      } else if (Math.abs(((target - existing.targetAngle + 540) % 360) - 180) > 0.1) {
        // New target: start tween from current displayed angle.
        tweenRef.current[actor.id] = {
          displayAngle: existing.displayAngle,
          targetAngle: target,
          fromAngle: existing.displayAngle,
          tweenStart: now,
        };
        changed = true;
      }

      // [Track C] Update the angle-change timestamp on ANY update (new or same).
      // Also update the gripper target based on the new angle.
      const gs = gripperRef.current[actor.id];
      const isPickPlace = nearAngle(target, 180) || nearAngle(target, 0);
      const newTargetWidth = isPickPlace ? GRIPPER_W_CLOSED : GRIPPER_W;
      if (!gs) {
        gripperRef.current[actor.id] = {
          displayWidth: GRIPPER_W,
          targetWidth: newTargetWidth,
          animStart: isPickPlace ? now : null,
          fromWidth: GRIPPER_W,
          angleChangedAt: now,
        };
      } else {
        const widthChanged = Math.abs(newTargetWidth - gs.targetWidth) > 0.1;
        gripperRef.current[actor.id] = {
          ...gs,
          angleChangedAt: now,
          targetWidth: newTargetWidth,
          animStart: widthChanged ? now : gs.animStart,
          fromWidth: widthChanged ? gs.displayWidth : gs.fromWidth,
        };
      }
    }
    if (changed) {
      // Kick off a render to reflect the new tween start.
      setDisplayAngles((prev) => ({ ...prev }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [armAngles]);

  // RAF loop: advance all active tweens + gripper animations + breathing.
  // Track C extends Track A's loop: the same requestAnimationFrame handles
  // angle tweens, gripper width, and breathing — no second RAF source.
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    function animate() {
      const now = performance.now();
      let anyActiveTween = false;
      const next: Record<string, number> = {};

      // ── Angle tweens (Track A) ──────────────────────────────────────────
      for (const [id, tw] of Object.entries(tweenRef.current)) {
        if (tw.tweenStart === null) {
          next[id] = tw.displayAngle;
          continue;
        }
        const elapsed = now - tw.tweenStart;
        const t = Math.min(elapsed / ARM_TWEEN_MS, 1);
        // easeOutCubic for a smooth deceleration at the end.
        const ease = 1 - Math.pow(1 - t, 3);
        const angle = lerpAngle(tw.fromAngle, tw.targetAngle, ease);
        tweenRef.current[id] = { ...tw, displayAngle: angle };
        next[id] = angle;
        if (t < 1) {
          anyActiveTween = true;
        } else {
          // Tween finished: snap and mark idle.
          tweenRef.current[id].tweenStart = null;
          tweenRef.current[id].displayAngle = tw.targetAngle;
          next[id] = tw.targetAngle;
        }
      }

      // ── Gripper width animation (Track C) ──────────────────────────────
      let anyActiveGripper = false;
      for (const [id, gs] of Object.entries(gripperRef.current)) {
        // If the gripper has been "closing" but the window has elapsed and
        // the arm is NOT at a pick/place angle, switch back to open.
        const tw = tweenRef.current[id];
        const settledAngle = tw?.targetAngle ?? 90;
        const isPickPlace = nearAngle(settledAngle, 180) || nearAngle(settledAngle, 0);
        const windowOk = (now - gs.angleChangedAt) < GRIPPER_CLOSE_WINDOW_MS;
        const wantClosed = isPickPlace && windowOk;
        const wantWidth = wantClosed ? GRIPPER_W_CLOSED : GRIPPER_W;

        // Target changed: restart animation.
        if (Math.abs(wantWidth - gs.targetWidth) > 0.1) {
          gripperRef.current[id] = {
            ...gs,
            targetWidth: wantWidth,
            fromWidth: gs.displayWidth,
            animStart: now,
          };
        }

        const animGs = gripperRef.current[id];
        if (animGs.animStart !== null) {
          const elapsed = now - animGs.animStart;
          const t = Math.min(elapsed / GRIPPER_ANIM_MS, 1);
          const ease = 1 - Math.pow(1 - t, 3); // easeOutCubic
          const w = lerp(animGs.fromWidth, animGs.targetWidth, ease);
          gripperRef.current[id] = { ...animGs, displayWidth: w };
          if (t < 1) {
            anyActiveGripper = true;
          } else {
            gripperRef.current[id].animStart = null;
            gripperRef.current[id].displayWidth = animGs.targetWidth;
          }
        }
      }

      // ── Breathing phase (Track C) ───────────────────────────────────────
      // Update once per frame; always runs so breathing is continuous.
      breathPhaseRef.current = now % BREATH_PERIOD_MS;

      // Is any arm in idle/breathing state?
      const anyBreathing = Object.values(gripperRef.current).some((gs) => {
        const age = now - gs.angleChangedAt;
        return age > IDLE_THRESHOLD_MS;
      });

      setDisplayAngles(next);

      // Continue RAF if: angle tween active, gripper animating, or any arm
      // is currently breathing (so the pulse keeps updating).
      if (anyActiveTween || anyActiveGripper || anyBreathing) {
        // Bump renderToken to force a Konva repaint for breathing/gripper.
        setRenderToken((t) => t + 1);
        rafRef.current = requestAnimationFrame(animate);
      } else {
        rafRef.current = null;
      }
    }

    // Start the RAF when a tween is pending OR any arm is in idle state
    // (breathing needs continuous frames).
    const hasPendingTween = Object.values(tweenRef.current).some(
      (tw) => tw.tweenStart !== null,
    );
    const hasPendingGripper = Object.values(gripperRef.current).some(
      (gs) => gs.animStart !== null,
    );
    const hasBreathing = Object.values(gripperRef.current).some((gs) => {
      const age = performance.now() - gs.angleChangedAt;
      return age > IDLE_THRESHOLD_MS;
    });

    if ((hasPendingTween || hasPendingGripper || hasBreathing) && rafRef.current === null) {
      rafRef.current = requestAnimationFrame(animate);
    }

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayAngles, renderToken]);

  if (armActors.length === 0) return null;

  // [Track C] Current breathing phase as a 0→1 value (sin-based, soft).
  const breathT = (Math.sin((breathPhaseRef.current / BREATH_PERIOD_MS) * 2 * Math.PI) + 1) / 2;
  const breathRadius = PIVOT_RADIUS + breathT * BREATH_RADIUS_DELTA;
  const breathOpacity = breathT * BREATH_OPACITY_MAX;

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {armActors.map((actor) => {
        const { x, y } = actor.position;
        const reach = actor.reach_px ?? DEFAULT_REACH_PX;
        const stickLen = reach * STICK_FRACTION;
        const color = actorColor(actor as unknown as Record<string, unknown>);

        // Current displayed angle in degrees (screen convention: 0=right, 90=down).
        const angleDeg = displayAngles[actor.id] ?? tweenRef.current[actor.id]?.displayAngle ?? 90;
        const angleRad = (angleDeg * Math.PI) / 180;

        // Stick tip coordinates in actor-local space (pivot at origin).
        const tipX = Math.cos(angleRad) * stickLen;
        const tipY = Math.sin(angleRad) * stickLen;

        // Gripper is oriented along the stick: we rotate the Group by angleDeg
        // so the gripper rect aligns with the stick direction automatically.
        // The gripper rect is placed at (stickLen, 0) in the rotated frame,
        // centred on the tip.

        // Stroke color: slightly darkened for contrast.
        const strokeColor = "rgba(0,0,0,0.45)";

        // [Track C] Gripper width: from animated gripper state, or default.
        const gripperW = gripperRef.current[actor.id]?.displayWidth ?? GRIPPER_W;

        // [Track C] Breathing: active when angle hasn't changed for > IDLE_THRESHOLD_MS.
        const gs = gripperRef.current[actor.id];
        const isIdle = !gs || (performance.now() - gs.angleChangedAt) > IDLE_THRESHOLD_MS;

        return (
          <Group key={actor.id} x={x} y={y}>
            {/* ── Track B insertion point ────────────────────────────────────
                TODO(Stage 6 Track B): reach radius circle goes here.
                See file-top comment for the exact Konva shape snippet.     */}

            {/* [Track C] Idle breathing ring — rendered below everything else.
                Oscillates radius 6→7 px, opacity 0.15→0, 2-s sinusoidal period.
                Only visible when the arm has been idle for > IDLE_THRESHOLD_MS.
                Intentionally subtle: a navigation cue, not a status alert.  */}
            {isIdle && (
              <Circle
                x={0}
                y={0}
                radius={breathRadius}
                stroke={color}
                strokeWidth={1.5}
                opacity={breathOpacity}
                listening={false}
              />
            )}

            {/* Arm stick: line from pivot to tip */}
            <Line
              points={[0, 0, tipX, tipY]}
              stroke={color}
              strokeWidth={STICK_WIDTH}
              lineCap="round"
            />

            {/* Gripper rectangle at the tip, oriented along the stick.
                We use a Group rotated to angleDeg so the rect stays aligned
                with the stick direction without trigonometry in props.
                [Track C] Width animates 8→3 px on pickup/place, 3→8 on open. */}
            <Group rotation={angleDeg}>
              <Rect
                x={stickLen - gripperW / 2}
                y={-GRIPPER_H / 2}
                width={gripperW}
                height={GRIPPER_H}
                fill={color}
                stroke={strokeColor}
                strokeWidth={0.8}
                cornerRadius={1}
              />
            </Group>

            {/* Shoulder pivot — rendered above the stick so it reads as
                a joint sitting on top of the arm structure. */}
            <Circle
              x={0}
              y={0}
              radius={PIVOT_RADIUS}
              fill={color}
              stroke={strokeColor}
              strokeWidth={1.2}
            />
            {/* Inner highlight to distinguish pivot from stick. */}
            <Circle
              x={0}
              y={0}
              radius={PIVOT_INNER_RADIUS}
              fill="rgba(255,255,255,0.55)"
            />

            {/* Actor-id label below the pivot. White/low-opacity to stay
                legible on the dark canvas without competing with the RF node
                label above (which is the primary identity affordance). */}
            <Text
              x={-24}
              y={PIVOT_RADIUS + 4}
              width={48}
              align="center"
              text={actor.id}
              fontSize={9}
              fill="rgba(255,255,255,0.65)"
              fontStyle="500"
            />
          </Group>
        );
      })}
    </Layer>
  );
}
