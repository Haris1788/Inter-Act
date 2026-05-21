import { useEffect } from "react";
import { useStore } from "@/store";
import { createRuntimeDriver, type RuntimeDriver } from "@/runtime";
import { setActiveDriver } from "@/runtime/ops";
import { useAutosave } from "@/runtime/useAutosave";
import { Topbar } from "@/chrome/Topbar";
import { LeftRegistry } from "@/chrome/LeftRegistry";
import { Inspector } from "@/chrome/Inspector";
import { LogsPanel } from "@/chrome/LogsPanel";
import { CanvasViewport } from "@/canvas/CanvasViewport";
import { OnboardingOverlay } from "@/chrome/OnboardingOverlay";

// Application-level density scale. ~0.9 makes the workspace read like
// Chrome at 90% while the browser stays at native 100%. This is a shell
// layout transform (the inverse-sized box keeps it filling the viewport),
// NOT a browser-zoom hack: it scales typography, spacing, panels, toolbar,
// inspector, the operational dock AND the canvas uniformly. ReactFlow +
// Konva sit under the SAME transform, so screen↔flow math and overlay
// alignment stay exact (RF reads the scaled bounding rect).
const UI_SCALE = 0.9;
const INV = `${100 / UI_SCALE}`; // 111.111… → after scale == 100vw/vh

export function App() {
  useAutosave();

  useEffect(() => {
    let driver: RuntimeDriver | null = null;
    let cancelled = false;
    void createRuntimeDriver().then((d) => {
      if (cancelled) return;
      driver = d;
      d.start(useStore);
      setActiveDriver(d);
    });
    return () => {
      cancelled = true;
      setActiveDriver(null);
      driver?.stop();
    };
  }, []);

  return (
    <div
      className="fixed left-0 top-0 overflow-hidden"
      style={{
        width: `${INV}vw`,
        height: `${INV}vh`,
        transform: `scale(${UI_SCALE})`,
        transformOrigin: "top left",
      }}
    >
      <div className="grid h-full w-full grid-rows-[48px_1fr] overflow-hidden">
        <Topbar />
      <main
        className="grid min-h-0"
        style={{
          gridTemplateColumns: "260px 1fr 320px",
          // Dock sizes itself (auto); canvas (1fr) reclaims the space when the
          // operational dock collapses. The dock animates its own height.
          gridTemplateRows: "1fr auto",
          gridTemplateAreas: '"left canvas right" "left logs right"',
        }}
      >
        <div style={{ gridArea: "left" }} className="min-h-0">
          <LeftRegistry />
        </div>
        <div style={{ gridArea: "canvas" }} className="min-h-0">
          <CanvasViewport />
        </div>
        <div style={{ gridArea: "right" }} className="min-h-0">
          <Inspector />
        </div>
        <div style={{ gridArea: "logs" }} className="min-h-0">
          <LogsPanel />
        </div>
      </main>
        <OnboardingOverlay />
      </div>
    </div>
  );
}
