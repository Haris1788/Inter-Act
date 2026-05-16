// Topology overview — faithful port of the prototype `.minimap`
// (INTER.act.html / styles.css): a framed "Topology · overview" card with
// a label, an inset frame, a node SVG and a static viewport rectangle.
// Data-driven: nodes are the real authored actors/zones (so it reflects
// the topology and the current selection), styled like the reference.

import { useActors, useZones, useSelectedActorId } from "@/store/selectors";
import { STAGE_W, STAGE_H } from "@/lib/stageCoords";

const ARM = { w: 210, h: 150 };
const AGV = { w: 184, h: 118 };
const ZONE = { w: 90, h: 60 };

export function TopologyMinimap() {
  const actors = useActors();
  const zones = useZones();
  const selectedId = useSelectedActorId();

  return (
    <div
      className="absolute bottom-[14px] right-[14px] z-[5] h-[110px] w-[180px] rounded-md border border-line bg-bg-surface p-2 shadow-e2"
      aria-label="Topology overview"
    >
      <div className="text-[10px] uppercase tracking-[0.08em] text-ink-4">
        Topology · overview
      </div>

      {/* mm-frame: inset 22 8 8 8 */}
      <div className="absolute inset-x-2 bottom-2 top-[22px] overflow-hidden rounded-[5px] bg-bg-canvas">
        <svg
          className="absolute inset-0 h-full w-full"
          viewBox={`0 0 ${STAGE_W} ${STAGE_H}`}
          preserveAspectRatio="xMidYMid meet"
        >
          {/* zones — whisper chips */}
          {Object.values(zones).map((z) => (
            <rect
              key={z.id}
              x={z.position.x - ZONE.w / 2}
              y={z.position.y - ZONE.h / 2}
              width={ZONE.w}
              height={ZONE.h}
              rx={10}
              fill="#EFEDE7"
              stroke="#C9C6BD"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ))}

          {/* actors — arms / AGVs; selected pops, rest muted warm-gray */}
          {Object.values(actors).map((a) => {
            const sel = a.id === selectedId;
            const size = a.kind === "arm" ? ARM : AGV;
            return (
              <rect
                key={a.id}
                x={a.position.x}
                y={a.position.y}
                width={size.w}
                height={size.h}
                rx={14}
                fill={
                  sel
                    ? "rgba(24,24,27,0.30)"
                    : a.kind === "agv"
                      ? "rgba(122,132,110,0.22)"
                      : "rgba(96,94,88,0.24)"
                }
                stroke={sel ? "#18181B" : "rgba(96,94,88,0.55)"}
                strokeWidth={sel ? 2.5 : 1}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
        </svg>

        {/* mm-view: static viewport rectangle (matches the prototype) */}
        <div
          className="pointer-events-none absolute rounded-[4px] border-[1.5px] border-cta"
          style={{
            top: "8%",
            left: "22%",
            width: "52%",
            height: "60%",
            background: "rgba(24,24,27,0.05)",
          }}
        />
      </div>
    </div>
  );
}
