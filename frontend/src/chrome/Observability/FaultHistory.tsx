// FaultHistory.tsx — read-only view of fault-class bus messages grouped by task.
// Track C, Phase 4 Stage 6. No mutations, no actions. Observer only.
//
// Chip mapping: imports from busMessageChip.ts (Track B extracted it).
// If Track B had NOT extracted it, we'd duplicate a minimal inline map with a
// TODO to consolidate — but the file now exists, so we import cleanly.

import { useCallback, useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import type { BusMessage, BusKind } from "@/store/types";
import { KIND_LABELS, kindChipClass } from "@/chrome/LogsPanel/busMessageChip";

// Shape returned by /bus/replay.
interface ReplayResponse {
  messages: RawMsg[];
  count: number;
}

interface RawMsg {
  id?: string;
  type: string;
  sender?: string;
  ts?: string;
  task_id?: string;
  negotiation_round?: number;
  slot_id?: string;
  payload?: unknown;
}

// Fault-class kinds we care about.
const FAULT_KINDS = new Set<BusKind>([
  "fault",
  "withdraw",
  "task_failed",
  "leg_halted",
]);

/** Normalise raw backend message into BusMessage (mirrors ReplayPanel logic). */
function normalise(raw: RawMsg, idx: number): BusMessage {
  const kind = raw.type as BusKind;
  const ts = raw.ts
    ? new Date(raw.ts).toISOString().slice(11, 19)
    : "??:??:??";
  const from = raw.sender ?? "system";

  let text = "";
  if (raw.task_id) text += raw.task_id;
  if (raw.slot_id) text += (text ? " · " : "") + `slot ${raw.slot_id}`;
  if (raw.payload && typeof raw.payload === "object") {
    const pay = raw.payload as Record<string, unknown>;
    if (typeof pay.reason === "string")
      text += (text ? " · " : "") + pay.reason;
  }

  return {
    id: raw.id ?? `fault-${idx}`,
    ts,
    kind,
    from,
    round: raw.negotiation_round,
    task_id: raw.task_id,
    text,
  };
}

/** Fetch all fault-class messages. Uses a single /bus/replay call and filters
 *  client-side — simpler than 4 parallel fetches and avoids 4× the requests. */
async function fetchFaultMessages(): Promise<BusMessage[]> {
  const resp = await fetch("/bus/replay");
  if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
  const data = (await resp.json()) as ReplayResponse;
  const all = data.messages.map(normalise);
  return all.filter((m) => FAULT_KINDS.has(m.kind));
}

// ── Per-task group ────────────────────────────────────────────────────────────

interface TaskGroup {
  task_id: string;
  messages: BusMessage[];
  latestKind: BusKind;
  latestTs: string;
}

function groupByTask(messages: BusMessage[]): TaskGroup[] {
  const map = new Map<string, BusMessage[]>();
  for (const m of messages) {
    const key = m.task_id ?? "(no task)";
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(m);
  }

  // Sort groups newest-first by the latest message in each group.
  return Array.from(map.entries())
    .map(([task_id, msgs]) => {
      const sorted = [...msgs].sort((a, b) => b.ts.localeCompare(a.ts));
      return {
        task_id,
        messages: sorted,
        latestKind: sorted[0].kind,
        latestTs: sorted[0].ts,
      };
    })
    .sort((a, b) => b.latestTs.localeCompare(a.latestTs));
}

// ── Small message row (shared within expanded card) ───────────────────────────

function MsgRow({ msg }: { msg: BusMessage }) {
  return (
    <div
      className="grid items-baseline gap-x-2 px-3 py-[2px] text-[11px] leading-[1.5]"
      style={{ gridTemplateColumns: "64px 78px auto 1fr" }}
    >
      <span className="text-ink-5 shrink-0 font-mono">{msg.ts}</span>
      <span
        className={cn(
          "inline-flex items-center justify-center rounded px-[5px] py-[1px]",
          "text-[9px] tracking-[0.04em] shrink-0 font-mono",
          kindChipClass(msg.kind),
        )}
      >
        {KIND_LABELS[msg.kind]}
      </span>
      <span className="shrink-0 text-ink-4 font-mono">{msg.from}</span>
      <span className="break-words text-ink-3 font-mono" style={{ whiteSpace: "pre-wrap" }}>
        {msg.text}
      </span>
    </div>
  );
}

// ── Collapsible task card ─────────────────────────────────────────────────────

function TaskCard({ group }: { group: TaskGroup }) {
  const [open, setOpen] = useState(false);

  // Short task_id — last 8 chars if it looks like a UUID.
  const shortId =
    group.task_id.length > 12
      ? "…" + group.task_id.slice(-8)
      : group.task_id;

  return (
    <div className="border border-line rounded mx-2.5 mb-1.5 overflow-hidden">
      {/* Card header (always visible) */}
      <button
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-1.5 text-left",
          "hover:bg-bg-hover transition-colors",
          open ? "bg-bg-hover" : "",
        )}
      >
        {/* Expand chevron */}
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="shrink-0 text-ink-4"
          style={{ transform: open ? "rotate(90deg)" : undefined }}
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>

        {/* Latest fault kind chip */}
        <span
          className={cn(
            "inline-flex items-center justify-center rounded px-[5px] py-[1px]",
            "text-[9px] tracking-[0.04em] shrink-0 font-mono",
            kindChipClass(group.latestKind),
          )}
        >
          {KIND_LABELS[group.latestKind]}
        </span>

        {/* Task id */}
        <span className="font-mono text-[11px] text-ink-2 flex-1 truncate" title={group.task_id}>
          {shortId}
        </span>

        {/* Count badge */}
        <span className="ml-auto shrink-0 rounded bg-err-bg px-1.5 py-[1px] font-mono text-[9px] text-err">
          {group.messages.length}
        </span>

        {/* Latest ts */}
        <span className="shrink-0 font-mono text-[10px] text-ink-5 ml-1">
          {group.latestTs}
        </span>
      </button>

      {/* Expanded message list */}
      {open && (
        <div className="border-t border-line-soft bg-bg-sunken py-0.5">
          {group.messages.map((m) => (
            <MsgRow key={m.id} msg={m} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export function FaultHistory() {
  const [messages, setMessages] = useState<BusMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const msgs = await fetchFaultMessages();
      setMessages(msgs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fetch failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const groups = useMemo(() => groupByTask(messages), [messages]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden font-mono text-11.5">
      {/* ── Toolbar ── */}
      <div className="flex shrink-0 items-center gap-2 border-b border-line-soft px-3 py-1.5">
        <span className="text-[11px] text-ink-3">Fault history</span>
        <span className="flex-1" />
        {loading && (
          <span className="text-[10px] text-ink-5 animate-pulse">loading…</span>
        )}
        <button
          title="Refresh"
          onClick={() => void refresh()}
          disabled={loading}
          className="inline-flex h-[26px] w-[26px] items-center justify-center rounded hover:bg-bg-hover disabled:opacity-40"
        >
          <svg
            width="11"
            height="11"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            style={{ transform: loading ? "rotate(30deg)" : undefined }}
          >
            <polyline points="1 4 1 10 7 10" />
            <path d="M3.51 15a9 9 0 1 0 .49-3" />
          </svg>
        </button>
      </div>

      {/* ── Error banner ── */}
      {error && (
        <div className="shrink-0 px-3 py-1.5 text-err text-[10px] bg-err-bg">
          {error}
        </div>
      )}

      {/* ── Content ── */}
      <div className="min-h-0 flex-1 overflow-y-auto py-1.5">
        {!loading && groups.length === 0 ? (
          <p className="px-4 py-3 text-ink-5 text-[11px]">
            No faults observed
          </p>
        ) : (
          groups.map((g) => <TaskCard key={g.task_id} group={g} />)
        )}
      </div>
    </div>
  );
}
