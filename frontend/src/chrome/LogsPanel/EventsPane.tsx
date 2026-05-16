import { useState, useMemo } from "react";
import { useEvents, useActors } from "@/store/selectors";
import { cn } from "@/lib/cn";
import type { EventKind, OperationalEvent } from "@/store/types";

// ── Kind chip styling ──────────────────────────────────────────────────────────

const KIND_LABELS: Record<EventKind, string> = {
  health: "health",
  asset: "asset",
  dock: "dock",
  object: "object",
  deploy: "deploy",
  link: "link",
  lifecycle: "lifecycle",
};

const ALL_KINDS: EventKind[] = [
  "health",
  "asset",
  "dock",
  "object",
  "deploy",
  "link",
  "lifecycle",
];

/** Background + text for a kind chip based on tone. */
function toneChipClass(tone: OperationalEvent["tone"]): string {
  switch (tone) {
    case "ok":
      return "bg-ok-bg text-ok";
    case "warn":
      return "bg-warn-bg text-warn";
    case "err":
      return "bg-err-bg text-err";
    case "info":
      return "bg-info-bg text-info";
  }
}

/** Muted variant for the kind filter toggle chips (not based on tone). */
function kindFilterClass(kind: EventKind, active: boolean): string {
  if (!active) return "bg-bg-sunken text-ink-4 hover:text-ink-3";
  switch (kind) {
    case "health":
      return "bg-ok-bg text-ok border border-ok/30";
    case "asset":
      return "bg-info-bg text-info border border-info/30";
    case "dock":
      return "bg-ok-bg text-ok border border-ok/30";
    case "object":
      return "bg-info-bg text-info border border-info/30";
    case "deploy":
      return "bg-warn-bg text-warn border border-warn/30";
    case "link":
      return "bg-info-bg text-info border border-info/30";
    case "lifecycle":
      return "bg-err-bg text-err border border-err/30";
  }
}

// ── EventsPane ────────────────────────────────────────────────────────────────

const VISIBLE_CAP = 120;

export function EventsPane() {
  const allEvents = useEvents();
  const actors = useActors();

  // ── filter state (local; no store side-effects) ───────────────────────────
  const [activeKinds, setActiveKinds] = useState<Set<EventKind>>(
    new Set(ALL_KINDS),
  );
  const [actorFilter, setActorFilter] = useState<string>("all");

  const actorIds = useMemo(() => Object.keys(actors).sort(), [actors]);

  function toggleKind(kind: EventKind) {
    setActiveKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) {
        // keep at least one active
        if (next.size > 1) next.delete(kind);
      } else {
        next.add(kind);
      }
      return next;
    });
  }

  function toggleAllKinds() {
    if (activeKinds.size === ALL_KINDS.length) {
      // all on → no-op (can't deselect all; just leave as-is)
      return;
    }
    setActiveKinds(new Set(ALL_KINDS));
  }

  const allKindsActive = activeKinds.size === ALL_KINDS.length;

  // ── filtering + windowing ─────────────────────────────────────────────────
  const filtered = useMemo(() => {
    // allEvents is oldest→newest; reverse for newest-first display
    const reversed = [...allEvents].reverse();
    return reversed
      .filter((ev) => {
        if (!activeKinds.has(ev.kind)) return false;
        if (actorFilter !== "all" && ev.actorId !== actorFilter) return false;
        return true;
      })
      .slice(0, VISIBLE_CAP);
  }, [allEvents, activeKinds, actorFilter]);

  // ── render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Filter bar */}
      <div
        className={cn(
          "flex shrink-0 flex-wrap items-center gap-1.5 border-b border-line-soft",
          "px-3 py-1.5",
        )}
      >
        {/* "All" toggle */}
        <button
          onClick={toggleAllKinds}
          className={cn(
            "inline-flex h-[20px] items-center rounded px-[7px] font-mono text-[9.5px]",
            "tracking-[0.04em] transition-colors",
            allKindsActive
              ? "bg-bg-surface border border-line text-ink-2"
              : "bg-bg-sunken text-ink-4 hover:text-ink-3",
          )}
        >
          all
        </button>

        {/* Per-kind toggles */}
        {ALL_KINDS.map((kind) => {
          const on = activeKinds.has(kind);
          return (
            <button
              key={kind}
              onClick={() => toggleKind(kind)}
              className={cn(
                "inline-flex h-[20px] items-center rounded px-[7px] font-mono text-[9.5px]",
                "tracking-[0.04em] transition-colors",
                kindFilterClass(kind, on),
              )}
            >
              {KIND_LABELS[kind]}
            </button>
          );
        })}

        {/* Spacer */}
        <span className="flex-1" />

        {/* Actor dropdown */}
        <select
          value={actorFilter}
          onChange={(e) => setActorFilter(e.target.value)}
          className={cn(
            "h-[22px] rounded border border-line bg-bg-surface",
            "px-1.5 font-mono text-[9.5px] text-ink-3 outline-none",
            "hover:border-line cursor-pointer",
          )}
        >
          <option value="all">all actors</option>
          {actorIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>

      {/* Timeline */}
      <div className="min-h-0 flex-1 overflow-y-auto py-1.5 font-mono text-11.5">
        {filtered.length === 0 ? (
          <p className="px-4 py-3 text-ink-5 text-11.5">No events</p>
        ) : (
          filtered.map((ev) => <EventRow key={ev.id} event={ev} />)
        )}
      </div>
    </div>
  );
}

// ── EventRow ──────────────────────────────────────────────────────────────────

interface EventRowProps {
  event: OperationalEvent;
}

function EventRow({ event }: EventRowProps) {
  return (
    <div
      className={cn(
        "grid items-baseline gap-x-2 px-3.5 py-[2px] leading-[1.5]",
        "hover:bg-bg-hover",
      )}
      style={{ gridTemplateColumns: "72px 72px auto 1fr" }}
    >
      {/* Timestamp */}
      <span className="text-ink-5 shrink-0">{event.ts}</span>

      {/* Kind chip */}
      <span
        className={cn(
          "inline-flex items-center justify-center rounded px-[5px] py-[1px]",
          "text-[9.5px] tracking-[0.04em] shrink-0",
          toneChipClass(event.tone),
        )}
      >
        {KIND_LABELS[event.kind]}
      </span>

      {/* Actor id (optional) */}
      <span
        className={cn(
          "shrink-0 text-ink-4",
          event.actorId ? "opacity-100" : "opacity-0 select-none",
        )}
        aria-hidden={!event.actorId}
      >
        {event.actorId ?? "-"}
      </span>

      {/* Text */}
      <span
        className="break-words text-ink-2"
        style={{ whiteSpace: "pre-wrap" }}
      >
        {event.text}
      </span>
    </div>
  );
}
