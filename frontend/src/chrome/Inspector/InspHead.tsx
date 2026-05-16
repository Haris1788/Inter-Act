import { cn } from "@/lib/cn";
import { useCoherence } from "@/store/derived";
import type { TopologyActor, ActorRuntime } from "@/store/types";

interface InspHeadProps {
  actor: TopologyActor;
  runtime: ActorRuntime;
  live: boolean;
}

// Map ActorStatus → badge appearance
const STATUS_BADGE: Record<
  string,
  { label: string; dotCls: string; bgCls: string; textCls: string; borderCls: string }
> = {
  ok:        { label: "Healthy",    dotCls: "bg-ok",           bgCls: "bg-[#E7F4EC]", textCls: "text-[#1D6B38]", borderCls: "border-[#B8DEC5]" },
  idle:      { label: "Idle",       dotCls: "bg-[#8A8A90]",    bgCls: "bg-[#E7E6E2]", textCls: "text-ink-2",    borderCls: "border-line" },
  busy:      { label: "Busy",       dotCls: "bg-cta",          bgCls: "bg-[#EFF4FF]", textCls: "text-[#1B47A5]", borderCls: "border-[#C3D3F7]" },
  warn:      { label: "Warning",    dotCls: "bg-[#D08A00]",    bgCls: "bg-[#FDF6E3]", textCls: "text-[#7A5200]", borderCls: "border-[#EDD68A]" },
  down:      { label: "Down",       dotCls: "bg-[#C93535]",    bgCls: "bg-[#FBEAEA]", textCls: "text-[#7A1A1A]", borderCls: "border-[#EDB8B8]" },
};

const COHERENCE_CHIP: Record<string, { label: string; dot: string; bg: string; text: string; border: string }> = {
  draft:    { label: "draft",    dot: "bg-ink-4",  bg: "bg-bg-sunken",   text: "text-ink-3",        border: "border-line-soft" },
  modified: { label: "modified", dot: "bg-warn",   bg: "bg-[#FDF6E3]",   text: "text-[#7A5200]",   border: "border-[#EDD68A]" },
  deployed: { label: "deployed", dot: "bg-ok",     bg: "bg-[#E7F4EC]",   text: "text-[#1D6B38]",   border: "border-[#B8DEC5]" },
  stale:    { label: "stale",    dot: "bg-err",    bg: "bg-[#FBEAEA]",   text: "text-[#7A1A1A]",   border: "border-[#EDB8B8]" },
};

export function InspHead({ actor, runtime, live }: InspHeadProps) {
  const statusKey = runtime.status in STATUS_BADGE ? runtime.status : "idle";
  const badge = STATUS_BADGE[statusKey];

  const { byId: coherenceById, runtimeOnly } = useCoherence();
  const coherenceState = coherenceById[actor.id];
  const isRuntimeOnly = runtimeOnly.includes(actor.id);
  const chip = coherenceState ? COHERENCE_CHIP[coherenceState] : null;

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
        {/* Bid/winning badge — mock only (live has no bid data) */}
        {!live && runtime.winning && (
          <span className="inline-flex items-center gap-[6px] h-[22px] px-2 rounded-[5px] text-11 font-medium bg-cta text-[#FAFAF7] border border-cta">
            <span className="w-[6px] h-[6px] rounded-full bg-[#FAFAF7]" />
            Winning bid · {runtime.bid?.toFixed(2) ?? "—"}
          </span>
        )}

        {/* Health status badge — always shown; driven by live status in live mode */}
        <span
          className={cn(
            "inline-flex items-center gap-[6px] h-[22px] px-2 rounded-[5px] text-11 font-medium border",
            badge.bgCls, badge.textCls, badge.borderCls,
          )}
        >
          <span className={cn("w-[6px] h-[6px] rounded-full", badge.dotCls)} />
          {live ? badge.label : "Container healthy"}
        </span>

        {/* Phase label — live only; shows the current script/phase from backend */}
        {live && runtime.phase && (
          <span className="font-mono text-10.5 text-ink-3 truncate max-w-[120px]" title={runtime.phase}>
            {runtime.phase}
          </span>
        )}

        {/* Coherence chip */}
        {isRuntimeOnly ? (
          <span
            className={cn(
              "inline-flex items-center gap-[5px] h-[22px] px-2 rounded-[5px] text-11 font-medium border",
              COHERENCE_CHIP.stale.bg, COHERENCE_CHIP.stale.text, COHERENCE_CHIP.stale.border,
            )}
            title="Present in live runtime but not in authored topology"
          >
            <span className={cn("w-[5px] h-[5px] rounded-full", COHERENCE_CHIP.stale.dot)} />
            runtime-only
          </span>
        ) : chip && (
          <span
            className={cn(
              "inline-flex items-center gap-[5px] h-[22px] px-2 rounded-[5px] text-11 font-medium border",
              chip.bg, chip.text, chip.border,
            )}
            title={
              coherenceState === "draft"    ? "Authored but never deployed" :
              coherenceState === "modified" ? "Changed since last deploy" :
              coherenceState === "deployed" ? "Matches last deploy snapshot" :
              coherenceState === "stale"    ? "In deploy snapshot but not running" : ""
            }
          >
            <span className={cn("w-[5px] h-[5px] rounded-full", chip.dot)} />
            {chip.label}
          </span>
        )}

        {/* Uptime — show only in mock (live has empty string default) */}
        {!live && (
          <span className="ml-auto font-mono text-11 text-ink-3">
            up {runtime.uptime}
          </span>
        )}
      </div>
    </div>
  );
}
