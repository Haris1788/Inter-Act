// useFitViewport - Phase 5.1 Stage 1.
//
// Derives the initial viewport from topology actor bounds (NOT from the
// hardcoded 1320×720 warehouse stage). Camera bounds are NEVER read from
// /config warehouse dims.
//
// Returns fitToStage() for toolbar "Fit" button — re-applies the same
// topology-bounds fit math on demand.

import { useCallback, useEffect } from "react";
import { useReactFlow } from "@xyflow/react";
import { useActors } from "@/store/selectors";
import { topologyBounds } from "@/canvas/topologyBounds";

export function useFitViewport(
  containerRef: React.RefObject<HTMLElement | null>,
) {
  const rf = useReactFlow();
  const actors = useActors();

  const applyFit = useCallback(
    (w: number, h: number) => {
      const actorList = Object.values(actors);
      const bounds = topologyBounds(actorList);

      // Scale to fit bounds in pane with modest padding.
      const scaleX = w / bounds.width;
      const scaleY = h / bounds.height;
      const zoom = Math.max(0.4, Math.min(1.2, Math.min(scaleX, scaleY) * 0.85));

      // Center the bounds in the pane.
      const x = w / 2 - (bounds.x + bounds.width / 2) * zoom;
      const y = h / 2 - (bounds.y + bounds.height / 2) * zoom;

      rf.setViewport({ x, y, zoom }, { duration: 0 });
    },
    [rf, actors],
  );

  // Fit on mount + container resize.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    // Initial fit after one microtask so RF is fully mounted.
    const timer = setTimeout(() => {
      applyFit(el.offsetWidth, el.offsetHeight);
    }, 0);

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        applyFit(width, height);
      }
    });
    ro.observe(el);

    return () => {
      clearTimeout(timer);
      ro.disconnect();
    };
  }, [applyFit, containerRef]);

  // Fit-to-topology callback for toolbar button.
  const fitToStage = useCallback(() => {
    const el = containerRef.current;
    if (el) applyFit(el.offsetWidth, el.offsetHeight);
  }, [applyFit, containerRef]);

  return { fitToStage };
}
