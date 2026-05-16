import { useActorEvents } from "@/store/derived";
import type { OperationalEvent } from "@/store/types";
import { cn } from "@/lib/cn";

interface SectionHistoryProps {
  actorId: string | null;
}

const KIND_LABELS: Record<OperationalEvent["kind"], string> = {
  health: "health",
  asset: "asset",
  dock: "dock",
  object: "object",
  deploy: "deploy",
  link: "link",
  lifecycle: "lifecycle",
};

const TONE_CLASSES: Record<OperationalEvent["tone"], string> = {
  ok: "text-ok bg-ok-bg",
  warn: "text-warn bg-warn-bg",
  err: "text-err bg-err-bg",
  info: "text-info bg-info-bg",
};

function EventRow({ event }: { event: OperationalEvent }) {
  const timeStr = (() => {
    try {
      return new Date(event.ts).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return event.ts;
    }
  })();

  return (
    <div className="flex items-start gap-[8px] py-[6px] border-b border-line-soft last:border-0">
      {/* Timestamp */}
      <span className="font-mono text-10.5 text-ink-4 whitespace-nowrap mt-[1px] shrink-0 min-w-[62px]">
        {timeStr}
      </span>

      {/* Kind chip */}
      <span
        className={cn(
          "inline-flex items-center px-[5px] py-[1px] rounded text-10 font-semibold uppercase tracking-[0.06em] shrink-0 mt-[1px]",
          TONE_CLASSES[event.tone],
        )}
      >
        {KIND_LABELS[event.kind]}
      </span>

      {/* Event text */}
      <span className="text-11 text-ink-2 leading-[1.45] break-words min-w-0">
        {event.text}
      </span>
    </div>
  );
}

export function SectionHistory({ actorId }: SectionHistoryProps) {
  // useActorEvents returns newest-last; we reverse for reverse-chronological display.
  const events = useActorEvents(actorId, 40);
  const reversed = [...events].reverse();

  return (
    <div className="px-[14px] pt-[12px] pb-2">
      <div className="flex items-center justify-between mb-[10px]">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">
          Operational History
        </span>
        {events.length > 0 && (
          <span className="font-mono text-10.5 text-ink-5">{events.length} events</span>
        )}
      </div>

      {reversed.length === 0 ? (
        <p className="text-12 text-ink-5 italic">No recorded activity for this actor.</p>
      ) : (
        <div>
          {reversed.map((ev) => (
            <EventRow key={ev.id} event={ev} />
          ))}
        </div>
      )}
    </div>
  );
}
