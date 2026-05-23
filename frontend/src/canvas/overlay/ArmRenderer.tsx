// ArmRenderer — Stage 6 Track A: arm articulation visualisation.
// Stage 6 Track C: gripper-close gesture + idle breathing pulse (additive).
// Phase 5.1 Fix #1: reach radius visibility (radial gradient + stronger outline + negotiation pulse).
// Phase 5.1 Fix #2: arm geometry proportions (stick == reach, pivot/gripper scale with reach).
//
// Renders one Konva group per arm actor (kind="arm") in the topology slice.
// Each group shows:
//   · Pivot circle (shoulder joint) at the actor's authored canvas position.
//   · Rotating stick (upper arm) at angle θ from backend `arm_angle_update`.
//   · Gripper rectangle at the stick tip (ON the reach boundary).
//   · Actor-id label below the pivot.
//   · [Track C] Idle breathing ring on the pivot (subtle pulse, 2-s period).
//   · [Track C] Gripper-close animation during pickup/place.
//   · [Fix #1] Reach radius: radial gradient fill + solid outline, pulse when negotiating.
//
// Invariant (Fix #2): pivot_center + stick_length == reach_radius.
// Gripper tip terminates ON the reach boundary. All geometry scales with reach_px.
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
//     closes (animate width →closed over GRIPPER_ANIM_MS).
//   · Otherwise gripper opens (animate width →open).
// "Near" = within ANGLE_SNAP_DEG of the landmark angle (accounts for float
// rounding in sweep_arm). No bus subscription needed; purely additive.
//
// ── Track C: Idle breathing pulse ──────────────────────────────────────────
// A concentric ring on the pivot oscillates radius and opacity with a 2-second
// sinusoidal period. Active when no arm_angle_update has arrived for >
// IDLE_THRESHOLD_MS (arm is presumed idle/resting).
// Breathing reuses the Track A RAF loop — no second requestAnimationFrame.
// The RAF is kept running at low cost (continuous sin eval) so the pulse
// stays smooth even when no tween is active. This is the same approach used
// by AgvTokenLayer's pulsePhase pattern.
//
// ── Fix #1: Reach radius visibility ─────────────────────────────────────────
// Replaces old dashed 12%-alpha circle with:
//   · Radial gradient fill: actor.color@12% at center → transparent at boundary.
//   · Solid outline at 35%–60% alpha (35% idle, 50% hover, 60% selected).
//   · NEGOTIATING state: outline alpha pulses 40%→80% on 1.5 s sine (same RAF).
//   Visible at 40% zoom because gradient fill survives downscale.

import { useEffect, useRef, useState } from "react";
import { Layer, Group, Circle, Rect, Line, Text } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import { useActors, useArmAngles, useActorRuntimeAll } from "@/store/selectors";
import { useStore } from "@/store";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Default color when topology actor has no color field. */
const DEFAULT_ARM_COLOR = "#c87941";

/** Animation duration for angle tween (ms). */
const ARM_TWEEN_MS = 300;

/** Default arm reach when not specified (px). */
const DEFAULT_REACH_PX = 120;

// ── Fix #2 — Per-reach dimension functions ────────────────────────────────
// All arm geometry scales with reach_px so arms feel physically proportional.
// Invariant: stick_length == reach_px → gripper tip ON the radius boundary.

/** Pivot circle radius: scales with arm size. Floor = 8 px so tiny arms stay visible. */
function pivotRadius(reach: number): number { return Math.max(8, reach * 0.04); }

/** Pivot inner highlight radius (proportional, min 2.5). */
function pivotInnerRadius(reach: number): number { return Math.max(2.5, reach * 0.015); }

/** Stick width: substantial enough to read as physical. Floor = 4 px. */
function stickWidth(reach: number): number { return Math.max(4, reach * 0.025); }

/** Open gripper width: scales with reach. Floor = 10 px. */
function gripperWOpen(reach: number): number { return Math.max(10, reach * 0.06); }

/** Gripper height: scales with reach. Floor = 5 px. */
function gripperHVal(reach: number): number { return Math.max(5, reach * 0.03); }

/** Closed gripper width: fraction of open width, at least 3 px. */
function gripperWClosed(reach: number): number { return Math.max(3, gripperWOpen(reach) * 0.35); }

// ── Fix #1 — Reach radius visibility constants ────────────────────────────

/** Reach outline stroke width (px canvas). 1.5 px → ~0.6 px at 40% zoom, still legible. */
const REACH_STROKE_WIDTH = 1.5;
const REACH_STROKE_WIDTH_SELECTED = 2;

/** Outline alpha (stroke at radius edge). */
const REACH_STROKE_ALPHA_DEFAULT = 0.35;
const REACH_STROKE_ALPHA_HOVER = 0.50;
const REACH_STROKE_ALPHA_SELECTED = 0.60;
const REACH_STROKE_ALPHA_SELECTED_HOVER = 0.75;

/** Gradient fill inner alpha (at pivot center). Fades to 0 at radius boundary. */
const REACH_GRAD_INNER_ALPHA = 0.12;

/** NEGOTIATING pulse animation: outline alpha oscillates on a 1.5 s sine. */
const PULSE_PERIOD_MS = 1500;
const PULSE_ALPHA_MIN = 0.40;
const PULSE_ALPHA_MAX = 0.80;

/** Negotiation states that trigger the animated pulse ring on the reach circle. */
const NEGOTIATING_STATES = new Set(["bidding", "won", "executing"]);

// ── Chip constants ──────────────────────────────────────────────────────────
/** Chip background padding (px). */
const CHIP_PAD_X = 5;
const CHIP_PAD_Y = 3;

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

/** Breathing pulse outer ring: max radius delta above pivot radius (px). */
const BREATH_RADIUS_DELTA = 1;

/** Breathing pulse max opacity. Intentionally very subtle (not distracting). */
const BREATH_OPACITY_MAX = 0.18;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Interpolate angle via the short arc (handles 0↔359 wrap correctly). */
function lerpAngle(from: number, to: number, t: number): number {
  const diff = ((to - from + 540) % 360) - 180; // shortest arc in [-180, 180]
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

/** Convert 0..1 alpha to a two-digit hex byte appended to a hex color string.
 *  e.g. alphaHex(0.12) → "1f"  (used for gradient color stops). */
function alphaHex(a: number): string {
  return Math.round(Math.min(1, Math.max(0, a)) * 255)
    .toString(16)
    .padStart(2, "0");
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
  /** Target width (openWidth or closedWidth). */
  targetWidth: number;
  /** Width animation start time. */
  animStart: number | null;
  /** Width at animation start. */
  fromWidth: number;
  /** performance.now() of the last angle update. */
  angleChangedAt: number;
  /** Per-arm open gripper width (derived from reach_px at init). */
  openWidth: number;
  /** Per-arm closed gripper width (derived from reach_px at init). */
  closedWidth: number;
}

// ---------------------------------------------------------------------------
// ArmRenderer
// ---------------------------------------------------------------------------

export function ArmRenderer({ tx }: { tx: ViewportTransform }) {
  const actors = useActors();
  const armAngles = useArmAngles();
  const selectedActorId = useStore((s) => s.selectedActorId);
  // Fix #1: read negotiationState per actor for pulse ring.
  const actorRuntime = useActorRuntimeAll();

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

  // [Phase 5.1 Stage 5 Track A] Hover state: which arm id is hovered.
  // Stored as a ref for fast RAF reads + a state for React re-render.
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  // [Track C] Breathing phase accumulator (ms since epoch mod BREATH_PERIOD).
  // Kept as a ref; read each frame inside the RAF without triggering state.
  // Also drives the Fix #1 negotiation pulse (different period constant).
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
      const reach = actor.reach_px ?? DEFAULT_REACH_PX;
      const gs = gripperRef.current[actor.id];
      const isPickPlace = nearAngle(target, 180) || nearAngle(target, 0);
      const wOpen = gripperWOpen(reach);
      const wClosed = gripperWClosed(reach);
      const newTargetWidth = isPickPlace ? wClosed : wOpen;
      if (!gs) {
        gripperRef.current[actor.id] = {
          displayWidth: wOpen,
          targetWidth: newTargetWidth,
          animStart: isPickPlace ? now : null,
          fromWidth: wOpen,
          angleChangedAt: now,
          openWidth: wOpen,
          closedWidth: wClosed,
        };
      } else {
        const widthChanged = Math.abs(newTargetWidth - gs.targetWidth) > 0.1;
        gripperRef.current[actor.id] = {
          ...gs,
          angleChangedAt: now,
          targetWidth: newTargetWidth,
          animStart: widthChanged ? now : gs.animStart,
          fromWidth: widthChanged ? gs.displayWidth : gs.fromWidth,
          openWidth: wOpen,
          closedWidth: wClosed,
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
  // Fix #1: breathPhaseRef.current also drives the negotiation pulse (different period).
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
        // openWidth/closedWidth stored per-arm in the gripper state from
        // the useEffect (which has reach_px context). Use them directly.
        const wantWidth = wantClosed ? gs.closedWidth : gs.openWidth;

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
      // breathPhaseRef.current is also read for the Fix #1 negotiation pulse
      // (using PULSE_PERIOD_MS instead of BREATH_PERIOD_MS).
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
  const breathOpacity = breathT * BREATH_OPACITY_MAX;

  // [Fix #1] Negotiation pulse phase: 0→1 on PULSE_PERIOD_MS sine.
  // Reuses breathPhaseRef (same RAF loop) — offset by π/2 so pulse and breath
  // are not perfectly in sync, giving a slightly richer feel.
  const pulseT = (Math.sin((breathPhaseRef.current / PULSE_PERIOD_MS) * 2 * Math.PI + Math.PI / 2) + 1) / 2;

  return (
    // Phase 5.1 Stage 5 Track A — hover compromise:
    // Layer has listening={true} so the hit-area Circle on the pivot can
    // receive pointer events. All visual shapes inside have listening={false}
    // so only the explicit hit area captures events. This avoids unintended
    // hits on the stick/gripper/reach-circle while keeping Konva-layer-wide
    // listening off for every other layer.
    <Layer
      listening={true}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {armActors.map((actor) => {
        const { x, y } = actor.position;
        const reach = actor.reach_px ?? DEFAULT_REACH_PX;

        // Fix #2: All geometry scales with reach_px.
        // Invariant: stickLen == reach → gripper tip terminates ON radius boundary.
        const stickLen = reach;
        const pRadius = pivotRadius(reach);
        const pInnerRadius = pivotInnerRadius(reach);
        const sWidth = stickWidth(reach);
        const gH = gripperHVal(reach);

        const color = actorColor(actor as unknown as Record<string, unknown>);
        const isSelected = actor.id === selectedActorId;
        const isHovered = actor.id === hoveredId;

        // Fix #1: Negotiation state for pulse ring on reach circle.
        const ns = actorRuntime[actor.id]?.negotiationState ?? "";
        const isNegotiating = NEGOTIATING_STATES.has(ns);

        // Reach outline stroke alpha: much stronger than old 12%, reads at 40% zoom.
        const reachStrokeAlpha = isNegotiating
          ? lerp(PULSE_ALPHA_MIN, PULSE_ALPHA_MAX, pulseT) // animated pulse
          : isSelected
            ? (isHovered ? REACH_STROKE_ALPHA_SELECTED_HOVER : REACH_STROKE_ALPHA_SELECTED)
            : (isHovered ? REACH_STROKE_ALPHA_HOVER : REACH_STROKE_ALPHA_DEFAULT);

        const reachSW = isSelected ? REACH_STROKE_WIDTH_SELECTED : REACH_STROKE_WIDTH;

        // Current displayed angle in degrees (screen convention: 0=right, 90=down).
        const angleDeg = displayAngles[actor.id] ?? tweenRef.current[actor.id]?.displayAngle ?? 90;
        const angleRad = (angleDeg * Math.PI) / 180;

        // Stick tip coordinates in actor-local space (pivot at origin).
        // stickLen == reach → tip IS on the reach boundary (invariant).
        const tipX = Math.cos(angleRad) * stickLen;
        const tipY = Math.sin(angleRad) * stickLen;

        // Chip placement: just beyond the gripper tip.
        const chipX = Math.cos(angleRad) * (stickLen + 12);
        const chipY = Math.sin(angleRad) * (stickLen + 12) - 10;

        // Stroke color: slightly darkened for contrast.
        const strokeColor = "rgba(0,0,0,0.45)";

        // [Track C] Gripper width: from animated gripper state, or default open width.
        const gState = gripperRef.current[actor.id];
        const gripperDisplayW = gState?.displayWidth ?? gripperWOpen(reach);

        // [Track C] Breathing: active when angle hasn't changed for > IDLE_THRESHOLD_MS.
        const isIdle = !gState || (performance.now() - gState.angleChangedAt) > IDLE_THRESHOLD_MS;

        // Breathing ring radius: expands slightly above pivot radius.
        const breathRadius = pRadius + breathT * BREATH_RADIUS_DELTA;

        // Chip label text (e.g. "120px").
        const chipLabel = `${reach}px`;
        // Estimate chip width: ~6px per char + padding.
        const chipW = chipLabel.length * 6 + CHIP_PAD_X * 2;
        const chipH = 10 + CHIP_PAD_Y * 2;

        // Fix #1: Radial gradient color stops for the reach area fill.
        // Konva fillRadialGradientColorStops: [position, color, position, color, ...]
        // Inner: actor.color at REACH_GRAD_INNER_ALPHA; outer: fully transparent.
        const innerColorStop = `${color}${alphaHex(REACH_GRAD_INNER_ALPHA)}`;
        const outerColorStop = `${color}00`;

        return (
          <Group key={actor.id} x={x} y={y}>
            {/* ── Fix #1: Reach radius — radial gradient fill + solid outline ──
                Gradient: soft "operational halo" from actor.color@12% at center
                to transparent at boundary. Visible at any zoom level.
                Outline: solid (not dashed) at 35–60% alpha, 1.5–2 px stroke.
                NEGOTIATING: outline alpha pulses 40→80% on 1.5 s sine (same RAF).
                SELECTED: outline at 60% alpha, 2 px stroke.
                Rendered first (below stick/gripper/pivot). */}
            <Circle
              x={0}
              y={0}
              radius={reach}
              fillRadialGradientStartPoint={{ x: 0, y: 0 }}
              fillRadialGradientStartRadius={0}
              fillRadialGradientEndPoint={{ x: 0, y: 0 }}
              fillRadialGradientEndRadius={reach}
              fillRadialGradientColorStops={[0, innerColorStop, 1, outerColorStop]}
              stroke={color}
              strokeWidth={reachSW}
              opacity={reachStrokeAlpha}
              listening={false}
            />

            {/* [Track C] Idle breathing ring on the pivot — subtle pulse.
                Radius oscillates pRadius → pRadius+1, opacity 0→0.18.
                Only visible when the arm has been idle for > IDLE_THRESHOLD_MS. */}
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

            {/* Fix #2: Arm stick — length == reach (tip ON radius boundary).
                stickWidth scales with reach (floor 4 px). lineCap="round". */}
            <Line
              points={[0, 0, tipX, tipY]}
              stroke={color}
              strokeWidth={sWidth}
              lineCap="round"
              listening={false}
            />

            {/* Fix #2: Gripper rectangle at the stick TIP (at distance reach from pivot).
                Width and height scale with reach. Track C animates width open↔closed.
                Rotated group so gripper aligns with stick direction. */}
            <Group rotation={angleDeg} listening={false}>
              <Rect
                x={stickLen - gripperDisplayW / 2}
                y={-gH / 2}
                width={gripperDisplayW}
                height={gH}
                fill={color}
                stroke={strokeColor}
                strokeWidth={0.8}
                cornerRadius={1}
                listening={false}
              />
            </Group>

            {/* Fix #2: Shoulder pivot — radius scales with arm (floor 8 px).
                Rendered above the stick so it reads as a joint on the arm structure. */}
            <Circle
              x={0}
              y={0}
              radius={pRadius}
              fill={color}
              stroke={strokeColor}
              strokeWidth={1.2}
              listening={false}
            />
            {/* Inner highlight to distinguish pivot from stick. */}
            <Circle
              x={0}
              y={0}
              radius={pInnerRadius}
              fill="rgba(255,255,255,0.55)"
              listening={false}
            />

            {/* Actor-id label below the pivot. Black for legibility against
                the light canvas background. */}
            <Text
              x={-24}
              y={pRadius + 4}
              width={48}
              align="center"
              text={actor.id}
              fontSize={9}
              fill="rgba(0,0,0,0.85)"
              fontStyle="600"
              listening={false}
            />

            {/* ── Phase 5.1 Stage 5 Track A: Hover hit area + reach chip ──────
                Transparent circle centred on pivot — sole listening target.
                onMouseEnter/Leave set hoveredId state which triggers a React
                re-render to update the reach circle alpha and show the chip.
                All other shapes above have listening={false}, so this is the
                only hit zone in the layer. */}
            <Circle
              x={0}
              y={0}
              radius={pRadius + 6}
              fill="transparent"
              listening={true}
              onMouseEnter={(e) => {
                const stage = e.target.getStage();
                if (stage) stage.container().style.cursor = "crosshair";
                setHoveredId(actor.id);
              }}
              onMouseLeave={(e) => {
                const stage = e.target.getStage();
                if (stage) stage.container().style.cursor = "";
                setHoveredId(null);
              }}
            />

            {/* Reach chip — shown only on hover. Positioned near gripper tip.
                Background rect + label text. Intentionally tiny: informational,
                not decorative. */}
            {isHovered && (
              <Group x={chipX} y={chipY} listening={false}>
                <Rect
                  x={-chipW / 2}
                  y={-chipH / 2}
                  width={chipW}
                  height={chipH}
                  fill="rgba(20,20,20,0.75)"
                  cornerRadius={3}
                />
                <Text
                  x={-chipW / 2 + CHIP_PAD_X}
                  y={-chipH / 2 + CHIP_PAD_Y}
                  text={chipLabel}
                  fontSize={10}
                  fill="rgba(255,255,255,0.92)"
                  fontStyle="500"
                />
              </Group>
            )}
          </Group>
        );
      })}
    </Layer>
  );
}
