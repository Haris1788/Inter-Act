// ActorInspector.tsx — read-only actor health + recent-activity view.
// Track C, Phase 4 Stage 6. No commands, no mutations. Observer only.
//
// Polls GET /bus/actors every 5 s (respectful — hard invariant).
// Expands to show recent activity derived from /bus/replay filtered by sender.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import type { BusMessage, BusKind } from "@/store/types";
import { KIND_LABELS, kindChipClass } from "@/chrome/LogsPanel/busMessageChip";

// ── /bus/actors response shape ────────────────────────────────────────────────

interface ActorEntry {
  actor_id: string;
  last_seen: string | null; // ISO timestamp or null if never seen
  state: string;
  presumed_dead: boolean;
}

interface ActorsResponse {
  actors: ActorEntry[];
  threshold_s: number;
}

// ── /bus/replay message (raw) ─────────────────────────────────────────────────

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

interface ReplayResponse {
  messages: RawMsg[];
  count: number;
}

function normaliseMsg(raw: RawMsg, idx: number): BusMessage {
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
    if (typeof pay.score === "number")
      text += (text ? " · " : "") + `score ${pay.score.toFixed(2)}`;
  }

  return {
    id: raw.id ?? `actor-msg-${idx}`,
    ts,
    kind,
    from,
    round: raw.negotiation_round,
    task_id: raw.task_id,
    text,
  };
}

// ── Relative time helper ──────────────────────────────────────────────────────

function relativeTime(isoTs: string | null): string {
  if (!isoTs) return "never";
  const diffMs = Date.now() - new Date(isoTs).getTime();
  const diffS = Math.floor(diffMs / 1000);
  if (diffS < 0) return "just now";
  if (diffS < 60) return `${diffS} s ago`;
  const diffM = Math.floor(diffS / 60);
  if (diffM < 60) return `${diffM} min ago`;
  const diffH = Math.floor(diffM / 60);
  return `${diffH} h ago`;
}

// ── Small message row ─────────────────────────────────────────────────────────

function MsgRow({ msg }: { msg: BusMessage }) {
  return (
    <div
      className="grid items-baseline gap-x-2 px-3 py-[2px] text-[10.5px] leading-[1.5]"
      style={{ gridTemplateColumns: "64px 78px 1fr" }}
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
      <span className="break-words text-ink-3 font-mono truncate" title={msg.text}>
        {msg.text}
      </span>
    </div>
  );
}

// ── Recent-activity loader (lazy, per expanded actor) ─────────────────────────

function RecentActivity({ actorId }: { actorId: string }) {
  const [messages, setMessages] = useState<BusMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const resp = await fetch("/bus/replay?limit=50");
        if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
        const data = (await resp.json()) as ReplayResponse;
        if (cancelled) return;
        const all = data.messages.map(normaliseMsg);
        // Filter to messages where sender === actorId, keep latest 10.
        const filtered = all
          .filter((m) => m.from === actorId)
          .slice(-10)
          .reverse();
        setMessages(filtered);
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Fetch failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [actorId]);

  if (loading) {
    return (
      <p className="px-3 py-1.5 text-[10px] text-ink-5 animate-pulse font-mono">
        loading…
      </p>
    );
  }
  if (error) {
    return (
      <p className="px-3 py-1.5 text-[10px] text-err font-mono">{error}</p>
    );
  }
  if (messages.length === 0) {
    return (
      <p className="px-3 py-1.5 text-[10px] text-ink-5 font-mono">
        No recent messages
      </p>
    );
  }
  return (
    <div className="py-0.5">
      <p className="px-3 pb-0.5 text-[9.5px] text-ink-5 font-mono tracking-wide uppercase">
        Recent activity
      </p>
      {messages.map((m) => (
        <MsgRow key={m.id} msg={m} />
      ))}
    </div>
  );
}

// ── State tone helper ─────────────────────────────────────────────────────────

function stateClass(state: string, presumedDead: boolean): string {
  if (presumedDead) return "text-err";
  switch (state) {
    case "idle":
      return "text-ok";
    case "bidding":
    case "negotiating":
    case "executing":
      return "text-info";
    case "faulted":
      return "text-err";
    default:
      return "text-ink-3";
  }
}

// ── Actor row card ────────────────────────────────────────────────────────────

function ActorRow({ actor }: { actor: ActorEntry }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border border-line rounded mx-2.5 mb-1.5 overflow-hidden">
      {/* Summary row */}
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

        {/* Actor id */}
        <span className="font-mono text-[11px] text-ink-2 w-[80px] shrink-0 truncate">
          {actor.actor_id}
        </span>

        {/* State */}
        <span
          className={cn(
            "font-mono text-[10px] shrink-0",
            stateClass(actor.state, actor.presumed_dead),
          )}
        >
          {actor.state}
        </span>

        {/* last_seen */}
        <span className="flex-1 text-right font-mono text-[10px] text-ink-5 truncate">
          {relativeTime(actor.last_seen)}
        </span>

        {/* presumed_dead badge */}
        {actor.presumed_dead && (
          <span className="shrink-0 rounded bg-err-bg px-1.5 py-[1px] font-mono text-[9px] text-err ml-1">
            dead
          </span>
        )}
      </button>

      {/* Expanded recent-activity */}
      {open && (
        <div className="border-t border-line-soft bg-bg-sunken py-0.5">
          <RecentActivity actorId={actor.actor_id} />
        </div>
      )}
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 5_000; // hard invariant: do NOT poll faster than 5 s

export function ActorInspector() {
  const [actors, setActors] = useState<ActorEntry[]>([]);
  const [threshold, setThreshold] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchActors = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch("/bus/actors");
      if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
      const data = (await resp.json()) as ActorsResponse;
      setActors(data.actors);
      setThreshold(data.threshold_s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fetch failed");
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch on mount, then poll every 5 s.
  useEffect(() => {
    void fetchActors();
    intervalRef.current = setInterval(() => void fetchActors(), POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current != null) clearInterval(intervalRef.current);
    };
  }, [fetchActors]);

  const deadCount = useMemo(
    () => actors.filter((a) => a.presumed_dead).length,
    [actors],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden font-mono text-11.5">
      {/* ── Toolbar ── */}
      <div className="flex shrink-0 items-center gap-2 border-b border-line-soft px-3 py-1.5">
        <span className="text-[11px] text-ink-3">
          Actor inspector
        </span>
        {threshold != null && (
          <span className="text-[10px] text-ink-5">
            dead threshold: {threshold}s
          </span>
        )}
        {deadCount > 0 && (
          <span className="rounded bg-err-bg px-1.5 py-[1px] text-[9px] text-err">
            {deadCount} dead
          </span>
        )}
        <span className="flex-1" />
        {loading && (
          <span className="text-[10px] text-ink-5 animate-pulse">loading…</span>
        )}
        <button
          title="Refresh now"
          onClick={() => void fetchActors()}
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

      {/* ── Actor list ── */}
      <div className="min-h-0 flex-1 overflow-y-auto py-1.5">
        {!loading && actors.length === 0 ? (
          <p className="px-4 py-3 text-ink-5 text-[11px]">
            No actors observed yet
          </p>
        ) : (
          actors.map((a) => <ActorRow key={a.actor_id} actor={a} />)
        )}
      </div>
    </div>
  );
}
