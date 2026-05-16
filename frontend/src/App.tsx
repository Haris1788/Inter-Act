import { useEffect } from "react";
import { useStore } from "@/store";
import { createRuntimeDriver, type RuntimeDriver } from "@/runtime";
import { Topbar } from "@/chrome/Topbar";
import { LeftRegistry } from "@/chrome/LeftRegistry";
import { Inspector } from "@/chrome/Inspector";
import { LogsPanel } from "@/chrome/LogsPanel";
import { CanvasViewport } from "@/canvas/CanvasViewport";

// App shell — mirrors styles-v2.css `.app` / `.main` grid exactly:
//   .app  : grid-rows 48px 1fr
//   .main : grid-cols 260px 1fr 320px / grid-rows 1fr 220px
//           areas "left canvas right" / "left logs right"
export function App() {
  useEffect(() => {
    let driver: RuntimeDriver | null = null;
    let cancelled = false;
    void createRuntimeDriver().then((d) => {
      if (cancelled) return;
      driver = d;
      d.start(useStore);
    });
    return () => {
      cancelled = true;
      driver?.stop();
    };
  }, []);

  return (
    <div className="grid h-screen w-screen grid-rows-[48px_1fr] overflow-hidden">
      <Topbar />
      <main
        className="grid min-h-0"
        style={{
          gridTemplateColumns: "260px 1fr 320px",
          gridTemplateRows: "1fr 220px",
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
    </div>
  );
}
