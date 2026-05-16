// LabelsOverlay - Domain 4 DOM labels.
//
// A pointer-events-none absolute wrapper containing an inner div driven by
// the RF viewport transform so cap-tags, dock-state and annotation stay locked
// to stage-space coordinates during pan/zoom.
//
// Labels authored in 1320×720 stage space (left/top in px, centred with
// translate(-50%,-50%) matching the prototype .cap-tag / .dock-state rules).
// NO CSS transition - that would cause lag on pan.
//
// Domain 3 owns the pool-label React Flow node; we render only:
//   - .cap-tag ×2  ("capability · pick" and "capability · place")
//   - .dock-state   ("docking · veh-1 approaching")
//   - .annot        ("Decentralised negotiation")

import { useViewportTransform } from "@/canvas/overlay/useViewportTransform";
import { cn } from "@/lib/cn";

export function LabelsOverlay() {
  const { x, y, zoom } = useViewportTransform();

  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden="true">
      {/*
       * Inner div carries the viewport transform.
       * All child elements use left/top in stage-px; they are then scaled +
       * translated by this wrapper so they match the RF world transform exactly.
       */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          transformOrigin: "0 0",
          transform: `translate(${x}px,${y}px) scale(${zoom})`,
          // No CSS transition - prevents lag during pan.
        }}
      >
        {/* ── capability · pick (arm-1 at stage 285, 90) ── */}
        <CapTag label="pick" style={{ left: 285, top: 90 }} />

        {/* ── capability · place (arm-2 at stage 1035, 90) ── */}
        <CapTag label="place" style={{ left: 1035, top: 90 }} />

        {/* ── dock-state (arm-1 docking ring at stage 310, 355) ── */}
        <DockState
          text="docking · veh-1 approaching"
          style={{ left: 310, top: 355 }}
        />

        {/* ── annotation (stage 495, 138) ── */}
        <Annotation
          title="Decentralised negotiation"
          body={
            <>
              arm-1 announced <em>pick @ feed-I</em>. All AGVs in the pool bid;
              veh-1 wins on speed and approaches arm-1&rsquo;s docking ring
              directly &mdash; no handover table.
            </>
          }
          style={{ left: 495, top: 138, maxWidth: 280 }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components - kept in this file (small, co-located with their parent).
// ---------------------------------------------------------------------------

interface CapTagProps {
  label: string;
  style: { left: number; top: number };
}

function CapTag({ label, style }: CapTagProps) {
  return (
    <div
      className={cn(
        "absolute z-[3] inline-flex items-center gap-[6px]",
        "h-[22px] px-[9px]",
        "font-mono text-[10.5px] text-ink",
        "bg-bg-canvas border border-line rounded-[999px]",
        "tracking-[0.03em]",
        "-translate-x-1/2 -translate-y-1/2",
      )}
      style={{ left: style.left, top: style.top }}
    >
      <span className="text-ink-4">capability ·</span>
      {label}
    </div>
  );
}

interface DockStateProps {
  text: string;
  style: { left: number; top: number };
}

function DockState({ text, style }: DockStateProps) {
  return (
    <div
      className={cn(
        "absolute z-[3]",
        "-translate-x-1/2 -translate-y-1/2",
        "font-mono text-[10px] tracking-[0.08em] uppercase text-cta font-semibold",
        "bg-white/85 border border-cta rounded-[999px]",
        "px-[9px] py-[2px]",
        "inline-flex items-center gap-[6px]",
      )}
      style={{ left: style.left, top: style.top }}
    >
      {/* pulsing dot - matches .dock-state::before */}
      <span
        className="inline-block w-[6px] h-[6px] rounded-full bg-cta animate-pulse-dot"
        aria-hidden="true"
      />
      {text}
    </div>
  );
}

interface AnnotationProps {
  title: string;
  body: React.ReactNode;
  style: { left: number; top: number; maxWidth: number };
}

function Annotation({ title, body, style }: AnnotationProps) {
  return (
    <div
      className={cn(
        "absolute z-[4]",
        "bg-white border border-dashed border-line-strong rounded-[8px]",
        "px-[10px] py-[8px]",
        "text-[11.5px] text-ink-2",
        "shadow-[0_1px_0_rgba(24,24,27,0.03),0_1px_2px_rgba(24,24,27,0.04)]",
      )}
      style={{ left: style.left, top: style.top, maxWidth: style.maxWidth }}
    >
      <div
        className={cn(
          "font-semibold text-ink mb-[3px]",
          "text-[10.5px] tracking-[0.06em] uppercase",
        )}
      >
        {title}
      </div>
      {body}
    </div>
  );
}
