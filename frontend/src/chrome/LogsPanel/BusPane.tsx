import { useMemo } from "react";
import { cn } from "@/lib/cn";
import type { BusMessage } from "@/store/types";
import { KIND_LABELS, kindChipClass } from "./busMessageChip";

interface BusPaneProps {
  messages: BusMessage[];
}

export function BusPane({ messages }: BusPaneProps) {
  // newest-first (memoized — bottom panel re-renders on runtime ticks)
  const reversed = useMemo(() => [...messages].reverse(), [messages]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-1.5 font-mono text-11.5">
      {reversed.length === 0 ? (
        <p className="px-4 py-3 text-ink-5 text-11.5">
          No bus traffic — negotiation events appear here
        </p>
      ) : (
        reversed.map((msg) => (
          <div
            key={msg.id}
            className={cn(
              "grid items-baseline gap-x-2 px-3.5 py-[2px] leading-[1.5]",
              "hover:bg-bg-hover",
            )}
            style={{ gridTemplateColumns: "72px 90px auto 1fr" }}
          >
            {/* Timestamp */}
            <span className="text-ink-5 shrink-0">{msg.ts}</span>

            {/* Kind chip */}
            <span
              className={cn(
                "inline-flex items-center justify-center rounded px-[5px] py-[1px]",
                "text-[9.5px] tracking-[0.04em] shrink-0",
                kindChipClass(msg.kind),
              )}
            >
              {KIND_LABELS[msg.kind]}
            </span>

            {/* From · round */}
            <span className="shrink-0 text-ink-4">
              {msg.from}
              {msg.round != null && msg.round > 0 ? (
                <span className="ml-1 text-ink-5">r{msg.round}</span>
              ) : null}
            </span>

            {/* Text */}
            <span
              className="break-words text-ink-2"
              style={{ whiteSpace: "pre-wrap" }}
            >
              {msg.text}
            </span>
          </div>
        ))
      )}
    </div>
  );
}
