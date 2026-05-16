import type { TopologyActor, ActorRuntime } from "@/store/types";

interface InspHeadProps {
  actor: TopologyActor;
  runtime: ActorRuntime;
}

export function InspHead({ actor, runtime }: InspHeadProps) {
  return (
    <div className="px-[14px] pt-[12px] pb-[10px] border-b border-line-soft">
      {/* Title row */}
      <div className="flex items-center gap-2">
        {/* Icon */}
        <div className="insp-title-icon flex-1 flex items-center gap-[9px] min-w-0">
          <span className="w-[30px] h-[30px] rounded-md bg-[#FAF8F2] border border-line text-ink inline-flex items-center justify-center flex-shrink-0">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
              <rect x="3" y="9" width="18" height="7" rx="1.5"/>
              <circle cx="8" cy="18" r="1.7"/>
              <circle cx="16" cy="18" r="1.7"/>
              <path d="M5 9V7h14v2"/>
            </svg>
          </span>
          <div className="min-w-0">
            <h3 className="m-0 text-14 font-semibold">{actor.id}</h3>
            <div className="font-mono text-11 text-ink-3">
              {actor.type} · {actor.label}
            </div>
          </div>
        </div>
        {/* Pin button */}
        <button className="w-[26px] h-[26px] rounded-sm inline-flex items-center justify-center text-ink-3 hover:bg-bg-hover hover:text-ink">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 17v5"/>
            <path d="M5 9l7-7 7 7-4 2v3l-3 3-3-3v-3z"/>
          </svg>
        </button>
        {/* More button */}
        <button className="w-[26px] h-[26px] rounded-sm inline-flex items-center justify-center text-ink-3 hover:bg-bg-hover hover:text-ink">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <circle cx="5" cy="12" r="1.6"/>
            <circle cx="12" cy="12" r="1.6"/>
            <circle cx="19" cy="12" r="1.6"/>
          </svg>
        </button>
      </div>

      {/* Status row */}
      <div className="mt-[10px] flex items-center gap-2 flex-wrap">
        {runtime.winning && (
          <span className="inline-flex items-center gap-[6px] h-[22px] px-2 rounded-[5px] text-11 font-medium bg-cta text-[#FAFAF7] border border-cta">
            <span className="w-[6px] h-[6px] rounded-full bg-[#FAFAF7]" />
            Winning bid · {runtime.bid?.toFixed(2) ?? "—"}
          </span>
        )}
        <span className="inline-flex items-center gap-[6px] h-[22px] px-2 rounded-[5px] text-11 font-medium bg-[#E7E6E2] text-ink-2 border border-line">
          <span className="w-[6px] h-[6px] rounded-full bg-[#8A8A90]" />
          Container healthy
        </span>
        <span className="ml-auto font-mono text-11 text-ink-3">
          up {runtime.uptime}
        </span>
      </div>
    </div>
  );
}
