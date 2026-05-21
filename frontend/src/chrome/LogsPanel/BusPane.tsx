import { useMemo } from "react";
import { cn } from "@/lib/cn";
import type { BusKind, BusMessage } from "@/store/types";

const KIND_LABELS: Record<BusKind, string> = {
  announce: "announce",
  bid: "bid",
  accept: "accept",
  dock: "dock",
};

function kindChipClass(kind: BusKind): string {
  switch (kind) {
    case "announce":
      return "bg-info-bg text-info";
    case "bid":
      return "bg-warn-bg text-warn";
    case "accept":
      return "bg-ok-bg text-ok";
    case "dock":
      return "bg-ok-bg text-ok";
  }
}

interface BusPaneProps {
  messages: BusMessage[];
}

export function BusPane({ messages }: BusPaneProps) {
  // newest-first (memoized — bottom panel re-renders on runtime ticks)
  const reversed = useMemo(() => [...messages].reverse(), [messages]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-1.5 font-mono text-11.5">
      {reversed.length === 0 ? (
        <p className="px-4 py-3 text-ink-5 text-11.5">No bus traffic</p>
      ) : (
        reversed.map((msg) => (
          <div
            key={msg.id}
            className={cn(
              "grid items-baseline gap-x-2 px-3.5 py-[2px] leading-[1.5]",
              "hover:bg-bg-hover",
            )}
            style={{ gridTemplateColumns: "72px 80px auto 1fr" }}
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

            {/* From → To */}
            <span className="shrink-0 text-ink-4">
              {msg.from}
              {msg.to ? (
                <>
                  <span className="mx-1 text-ink-5">→</span>
                  {msg.to}
                </>
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
