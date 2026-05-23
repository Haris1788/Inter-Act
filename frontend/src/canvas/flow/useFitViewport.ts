// useFitViewport - Phase 5.1 Stage 1.
//
// Derives the initial viewport from topology actor bounds (NOT from the
// hardcoded 1320×720 warehouse stage). Camera bounds are NEVER read from
// /config warehouse dims.
//
// Returns fitToStage() for toolbar "Fit" button — re-applies the same
// topology-bounds fit math on demand.

import { useCallback, useEffect, useRef } from "react";
import { useReactFlow } from "@xyflow/react";
import { useActors } from "@/store/selectors";
import { topologyBounds } from "@/canvas/topologyBounds";

export function useFitViewport(
  containerRef: React.RefObject<HTMLElement | null>,
) {
  const rf = useReactFlow();
  const actors = useActors();

  // Latest applyFit closure stored in a ref so the mount/resize effect
  // below stays STABLE (no actor-change dep). Without this, every actor
  // mutation (move, add, delete, constraint toggle) would re-run the
  // mount effect and re-fit the viewport — clobbering the user's manual
  // zoom. User-driven fit happens via the toolbar "Fit" button only.
  const applyFitRef = useRef<(w: number, h: number) => void>(() => {});

  applyFitRef.current = (w: number, h: number) => {
    const actorList = Object.values(actors);
    const bounds = topologyBounds(actorList);

    // Scale to fit bounds in pane with modest padding.
    const scaleX = w / bounds.width;
    const scaleY = h / bounds.height;
    const zoom = Math.max(0.1, Math.min(1.2, Math.min(scaleX, scaleY) * 0.85));

    // Center the bounds in the pane.
    const x = w / 2 - (bounds.x + bounds.width / 2) * zoom;
    const y = h / 2 - (bounds.y + bounds.height / 2) * zoom;

    rf.setViewport({ x, y, zoom }, { duration: 0 });
  };

  // Fit on FIRST MOUNT + container resize ONLY. Effect deps are stable
  // (no actors / no applyFit closure) so the user's manual zoom survives
  // any subsequent state change.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    // Initial fit after one microtask so RF is fully mounted.
    const timer = setTimeout(() => {
      applyFitRef.current(el.offsetWidth, el.offsetHeight);
    }, 0);

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        applyFitRef.current(width, height);
      }
    });
    ro.observe(el);

    return () => {
      clearTimeout(timer);
      ro.disconnect();
    };
  }, [containerRef]);

  // Fit-to-topology callback for toolbar button (uses latest actors).
  const fitToStage = useCallback(() => {
    const el = containerRef.current;
    if (el) applyFitRef.current(el.offsetWidth, el.offsetHeight);
  }, [containerRef]);

  return { fitToStage };
}
