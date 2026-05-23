// ReplayPanel.tsx — read-only playback of NegotiationBus history.
// Fetches GET /bus/replay (no filters → full history). No backend mutations.
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import type { BusMessage } from "@/store/types";
import { KIND_LABELS, kindChipClass } from "@/chrome/LogsPanel/busMessageChip";
import { Timeline } from "./Timeline";

type Speed = 1 | 2 | 4;

// Shape of the /bus/replay JSON response.
interface ReplayResponse {
  messages: RawBusMessage[];
  count: number;
}

// Raw shape from backend — may have slightly different field names.
interface RawBusMessage {
  id?: string;
  type: string;
  sender?: string;
  ts?: string;
  task_id?: string;
  negotiation_round?: number;
  slot_id?: string;
  bid_version?: number;
  payload?: unknown;
}

/** Normalise a raw backend message into BusMessage (mirrors socketDriver logic). */
function normalise(raw: RawBusMessage, idx: number): BusMessage {
  const kind = raw.type as BusMessage["kind"];
  const ts = raw.ts
    ? new Date(raw.ts).toISOString().slice(11, 19)
    : "??:??:??";
  const from = raw.sender ?? "system";

  let text = "";
  if (raw.task_id) text += raw.task_id;
  if (raw.slot_id) text += (text ? " · " : "") + `slot ${raw.slot_id}`;
  if (typeof raw.bid_version === "number")
    text += (text ? " · " : "") + `v${raw.bid_version}`;
  if (raw.payload && typeof raw.payload === "object") {
    const pay = raw.payload as Record<string, unknown>;
    if (typeof pay.score === "number")
      text += (text ? " · " : "") + `score ${pay.score.toFixed(2)}`;
    if (typeof pay.reason === "string")
      text += (text ? " · " : "") + pay.reason;
  }

  return {
    id: raw.id ?? `replay-${idx}`,
    ts,
    kind,
    from,
    round: raw.negotiation_round,
    task_id: raw.task_id,
    text,
  };
}

export function ReplayPanel() {
  const [messages, setMessages] = useState<BusMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);

  // Auto-play interval ref.
  const playRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch /bus/replay (no filters → full history).
  const fetchHistory = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch("/bus/replay");
      if (!resp.ok) {
        setError(`Server returned ${resp.status}`);
        return;
      }
      const data = (await resp.json()) as ReplayResponse;
      const normalised = data.messages.map(normalise);
      setMessages(normalised);
      setCursor(0);
      setPlaying(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fetch failed");
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch on mount.
  useEffect(() => {
    void fetchHistory();
  }, [fetchHistory]);

  // Auto-play tick.
  useEffect(() => {
    if (!playing || messages.length === 0) {
      if (playRef.current != null) clearInterval(playRef.current);
      return;
    }
    const ms = 800 / speed;
    playRef.current = setInterval(() => {
      setCursor((c) => {
        if (c >= messages.length - 1) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, ms);
    return () => {
      if (playRef.current != null) clearInterval(playRef.current);
    };
  }, [playing, speed, messages.length]);

  const total = messages.length;
  const atEnd = cursor >= total - 1;

  function togglePlay() {
    if (atEnd && !playing) {
      // Restart from beginning.
      setCursor(0);
      setPlaying(true);
    } else {
      setPlaying((p) => !p);
    }
  }

  function stepBack() {
    setPlaying(false);
    setCursor((c) => Math.max(0, c - 1));
  }

  function stepForward() {
    setPlaying(false);
    setCursor((c) => Math.min(total - 1, c + 1));
  }

  // Messages visible up to and including the cursor.
  const visible = messages.slice(0, cursor + 1);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden font-mono text-11.5">
      {/* ── Controls bar ── */}
      <div className="flex shrink-0 items-center gap-2 border-b border-line-soft px-3 py-1.5">
        {/* Step back */}
        <button
          title="Step back"
          disabled={cursor <= 0 || total === 0}
          onClick={stepBack}
          className="inline-flex h-[26px] w-[26px] items-center justify-center rounded hover:bg-bg-hover disabled:opacity-30"
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>

        {/* Play / Pause */}
        <button
          title={playing ? "Pause" : "Play"}
          disabled={total === 0}
          onClick={togglePlay}
          className="inline-flex h-[26px] w-[26px] items-center justify-center rounded hover:bg-bg-hover disabled:opacity-30"
        >
          {playing ? (
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="6" y="5" width="4" height="14" />
              <rect x="14" y="5" width="4" height="14" />
            </svg>
          ) : (
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
          )}
        </button>

        {/* Step forward */}
        <button
          title="Step forward"
          disabled={atEnd || total === 0}
          onClick={stepForward}
          className="inline-flex h-[26px] w-[26px] items-center justify-center rounded hover:bg-bg-hover disabled:opacity-30"
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>

        {/* Scrub bar */}
        <input
          type="range"
          min={0}
          max={Math.max(0, total - 1)}
          value={cursor}
          disabled={total === 0}
          onChange={(e) => {
            setPlaying(false);
            setCursor(Number(e.target.value));
          }}
          className="flex-1"
          style={{ minWidth: 60 }}
          aria-label="Replay position"
        />

        {/* Position label */}
        <span className="shrink-0 text-ink-4 text-[10px] tabular-nums">
          {total === 0 ? "0/0" : `${cursor + 1}/${total}`}
        </span>

        {/* Speed selector */}
        <div className="flex items-center gap-0.5 shrink-0">
          {([1, 2, 4] as Speed[]).map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className={cn(
                "rounded px-1.5 py-[2px] text-[9.5px]",
                speed === s
                  ? "bg-bg-surface border border-line text-ink"
                  : "text-ink-3 hover:text-ink-2",
              )}
            >
              {s}×
            </button>
          ))}
        </div>

        {/* Refresh */}
        <button
          title="Refresh from backend"
          onClick={() => void fetchHistory()}
          disabled={loading}
          className="ml-1 inline-flex h-[26px] w-[26px] items-center justify-center rounded hover:bg-bg-hover disabled:opacity-40"
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

      {/* ── Timeline ── */}
      {total > 0 && (
        <div className="shrink-0 border-b border-line-soft">
          <Timeline
            messages={messages}
            cursorIndex={cursor}
            onSelect={(idx) => {
              setPlaying(false);
              setCursor(idx);
            }}
          />
        </div>
      )}

      {/* ── Message list (up to cursor) ── */}
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {total === 0 && !loading ? (
          <p className="px-4 py-3 text-ink-5">
            No bus messages yet — start a negotiation.
          </p>
        ) : (
          visible
            .slice()
            .reverse()
            .map((msg, revIdx) => {
              // The cursor message is the most recent (index 0 in reversed list).
              const isCursor = revIdx === 0;
              return (
                <div
                  key={msg.id}
                  className={cn(
                    "grid items-baseline gap-x-2 px-3.5 py-[2px] leading-[1.5]",
                    isCursor ? "bg-bg-hover" : "hover:bg-bg-hover",
                  )}
                  style={{ gridTemplateColumns: "72px 90px auto 1fr" }}
                >
                  <span className="text-ink-5 shrink-0">{msg.ts}</span>
                  <span
                    className={cn(
                      "inline-flex items-center justify-center rounded px-[5px] py-[1px]",
                      "text-[9.5px] tracking-[0.04em] shrink-0",
                      kindChipClass(msg.kind),
                    )}
                  >
                    {KIND_LABELS[msg.kind]}
                  </span>
                  <span className="shrink-0 text-ink-4">
                    {msg.from}
                    {msg.round != null && msg.round > 0 ? (
                      <span className="ml-1 text-ink-5">r{msg.round}</span>
                    ) : null}
                  </span>
                  <span
                    className="break-words text-ink-2"
                    style={{ whiteSpace: "pre-wrap" }}
                  >
                    {msg.text}
                  </span>
                </div>
              );
            })
        )}
      </div>
    </div>
  );
}
