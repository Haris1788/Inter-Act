// Actor node rendered inside React Flow — covers both arm and agv/dense variants.
// Width: arm=210px, agv=184px. Visuals pixel-faithful to .actor.arm / .actor.agv.dense
// in styles-v2.css. RF default node styling is disabled via nodeDefaults in flowConfig.

import { memo } from "react";
import { type NodeProps } from "@xyflow/react";
import { cn } from "@/lib/cn";
import { useActorRuntime } from "@/store/selectors";
import { useSelectedActorId } from "@/store/selectors";
import type { TopologyActor } from "@/store/types";

/** The `data` payload stored on each RF ActorNode. */
export type ActorNodeData = TopologyActor;

// Glyphs from prototype SVGs (arm-6DoF, arm-scara, agv-shuttle, agv-forklift, agv-tow)
function GlyphSvg({ kind, type }: { kind: string; type: string }) {
  if (kind === "arm") {
    if (type.includes("SCARA")) {
      return (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <rect x="4" y="14" width="16" height="3" rx="1" />
          <path d="M9 14V8h6v6" />
          <path d="M12 8V4" />
        </svg>
      );
    }
    if (type.includes("Delta")) {
      return (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M4 6h16" />
          <path d="M6 6l6 12 6-12" />
          <circle cx="12" cy="18" r="1.5" />
        </svg>
      );
    }
    // 6DoF default
    return (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <circle cx="6" cy="18" r="2" />
        <path d="M6 16V9l6-3v5l5 1v6" />
        <circle cx="17" cy="17" r="2" />
      </svg>
    );
  }

  // AGV variants
  if (type.includes("Forklift")) {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d="M3 17h11V7h4l3 4v6h-2" />
        <circle cx="7" cy="19" r="1.5" />
        <circle cx="17" cy="19" r="1.5" />
      </svg>
    );
  }
  if (type.includes("Tow")) {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
        <rect x="3" y="11" width="11" height="5" rx="1" />
        <path d="M14 13h4l3 3" />
        <circle cx="7" cy="18" r="1.4" />
        <circle cx="18" cy="18" r="1.4" />
      </svg>
    );
  }
  // Shuttle default
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="3" y="9" width="18" height="7" rx="1.5" />
      <circle cx="8" cy="18" r="1.7" />
      <circle cx="16" cy="18" r="1.7" />
      <path d="M5 9V7h14v2" />
    </svg>
  );
}

export const ActorNode = memo(function ActorNode({ data }: NodeProps) {
  const actor = data as unknown as ActorNodeData;
  const runtime = useActorRuntime(actor.id);
  const selectedId = useSelectedActorId();

  const status = runtime?.status ?? "idle";
  const isWinning = runtime?.winning ?? false;
  const isSelected = selectedId === actor.id;
  const isAgv = actor.kind === "agv";

  const phase = runtime?.phase ?? "";
  const bid = runtime?.bid;

  return (
    <div
      data-status={status}
      style={{ width: isAgv ? 184 : 210 }}
      className={cn(
        // base card
        "relative select-none rounded-lg border bg-white text-ink",
        "shadow-e2 transition-shadow duration-[140ms]",
        // hover
        "hover:shadow-e3",
        // selected ring
        isSelected && "border-cta shadow-[0_0_0_1px_#18181B,0_6px_22px_rgba(24,24,27,0.08),0_2px_4px_rgba(24,24,27,0.04)]",
        // winning ring (agv)
        isWinning && !isSelected && "border-cta shadow-[0_0_0_1px_#18181B,0_1px_0_rgba(24,24,27,0.03),0_3px_10px_rgba(24,24,27,0.05)]",
        // normal border when neither
        !isSelected && !isWinning && "border-line",
      )}
    >
      {/* winning dashed ring overlay */}
      {isWinning && (
        <div
          className="pointer-events-none absolute"
          style={{
            inset: -10,
            border: "1.5px dashed #18181B",
            borderRadius: 18,
            opacity: 0.35,
          }}
        />
      )}

      {/* HEAD */}
      <div
        className={cn(
          "flex items-center gap-2",
          isAgv ? "px-[10px] pb-[5px] pt-[7px]" : "px-[11px] pb-[7px] pt-[9px]",
        )}
      >
        <span
          className={cn(
            "rounded-[4px] px-[6px] py-[2px]",
            "font-mono text-10 uppercase tracking-[0.08em] text-ink",
            isAgv ? "bg-[#EEE8DC]" : "bg-bg-sunken",
          )}
        >
          {actor.type}
        </span>
        <span className="ml-auto font-mono text-11 text-ink-3">{actor.id}</span>
        {/* bid pill — only on agv */}
        {isAgv && bid !== null && bid !== undefined && (
          <span
            className={cn(
              "ml-1 rounded-[4px] border px-[5px] py-[1px]",
              "font-mono text-10",
              isWinning
                ? "border-cta bg-cta text-white"
                : "border-line-soft bg-bg-sunken text-ink-2",
            )}
          >
            bid {bid.toFixed(2)}
          </span>
        )}
      </div>

      {/* BODY */}
      <div
        className={cn(
          "flex items-center",
          isAgv ? "gap-[9px] px-[10px] pb-[7px] pt-0" : "gap-[10px] px-[11px] pb-[8px] pt-[2px]",
        )}
      >
        <span
          className={cn(
            "inline-flex items-center justify-center rounded-[8px] border border-line-soft text-ink",
            "bg-[#FAF8F2]",
            isAgv ? "h-[30px] w-[30px] rounded-[7px]" : "h-[38px] w-[38px]",
          )}
        >
          <GlyphSvg kind={actor.kind} type={actor.type} />
        </span>
        <div>
          <div
            className={cn(
              "font-semibold text-ink",
              isAgv ? "text-12.5" : "text-13",
            )}
          >
            {actor.label}
          </div>
          <div className="mt-[2px] font-mono text-10.5 text-ink-3">{actor.spec}</div>
        </div>
      </div>

      {/* CAP STRIP — arms only */}
      {!isAgv && (
        <div className="flex flex-wrap gap-1 px-[11px] pb-[9px]">
          {actor.capabilities.map((cap) => (
            <span
              key={cap}
              className={cn(
                "rounded-[4px] border px-[6px] py-[1px]",
                "font-mono text-[9.5px] lowercase tracking-[0.04em]",
                cap === actor.liveCapability
                  ? "border-cta bg-cta text-white"
                  : "border-line-soft bg-bg-sunken text-ink-2",
              )}
            >
              {cap}
            </span>
          ))}
        </div>
      )}

      {/* FOOT */}
      <div
        className={cn(
          "flex items-center justify-between border-t border-dashed border-line-soft",
          "text-11 text-ink-3",
          isAgv ? "px-[10px] py-[6px]" : "px-[11px] py-[7px]",
        )}
      >
        {/* pip with status dot */}
        <span className="inline-flex items-center gap-[6px] font-mono text-10.5">
          <PipDot status={status} />
          {phase}
          {(status === "ok" || status === "busy") && (
            <span
              className="inline-block h-[5px] w-[5px] rounded-full bg-current animate-pulse-dot"
              style={{ opacity: 0.7 }}
            />
          )}
        </span>
        {/* metric */}
        {isAgv && runtime?.metrics?.battery != null ? (
          <span className="font-mono text-10.5 text-ink-3">{runtime.metrics.battery}% bat</span>
        ) : (
          <span className="font-mono text-10.5 text-ink-3">
            {actor.spec.split("·")[0]?.trim()}
          </span>
        )}
      </div>
    </div>
  );
});

function PipDot({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    ok: "bg-ok shadow-[0_0_0_2px_#E6EBE0]",
    busy: "bg-cta shadow-[0_0_0_2px_rgba(24,24,27,0.10)]",
    warn: "bg-warn shadow-[0_0_0_2px_#F0E6D0]",
    idle: "bg-idle shadow-[0_0_0_2px_#E7E6E2]",
  };
  return (
    <span
      className={cn(
        "inline-block h-[7px] w-[7px] rounded-full",
        colorMap[status] ?? colorMap.idle,
      )}
    />
  );
}
