// Hook that sets the RF viewport to computeFit(paneW, paneH) on mount and
// whenever the container resizes. Also returns a fitToStage() callback for
// the toolbar "Fit" button.

import { useCallback, useEffect } from "react";
import { useReactFlow } from "@xyflow/react";
import { computeFit } from "@/lib/stageCoords";

export function useFitViewport(containerRef: React.RefObject<HTMLElement | null>) {
  const rf = useReactFlow();

  const applyFit = useCallback(
    (w: number, h: number) => {
      const vp = computeFit(w, h);
      rf.setViewport(vp, { duration: 0 });
    },
    [rf],
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

  // Fit-to-stage callback for toolbar button — re-applies the exact prototype
  // fit math (centers the 1320x720 stage in the current pane).
  const fitToStage = useCallback(() => {
    const el = containerRef.current;
    if (el) applyFit(el.offsetWidth, el.offsetHeight);
  }, [applyFit, containerRef]);

  return { fitToStage };
}
