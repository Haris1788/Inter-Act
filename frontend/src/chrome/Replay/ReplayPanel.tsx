// ReplayPanel.tsx — read-only playback of NegotiationBus history.
// Fetches GET /bus/replay (no filters → full history). No backend mutations.
//
// Phase 5 Stage 8 Track C additions:
//   - On cursor change (play tick or scrub): scans messages[0..cursor] in
//     reverse per actor for the most recent `position_update` message and
//     dispatches setReplayPositions to the runtime slice. replayMode is set to
//     true automatically.
//   - "Return to live" button: calls clearReplayMode() and returns the panel to
//     live mode.
//   - "REPLAY" banner rendered via a portal-free overlay on the canvas edge is
//     instead shown inside the panel controls bar to avoid touching the canvas
//     layer tree (Track B constraint).
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import type { BusMessage, ReplayPosition } from "@/store/types";
import { KIND_LABELS, kindChipClass } from "@/chrome/LogsPanel/busMessageChip";
import { Timeline } from "./Timeline";
import { useStore } from "@/store";

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

// Shape of payload for type === "position_update" messages.
interface PositionUpdatePayload {
  actor_id?: string;
  agv_id?: string;
  x?: number;
  y?: number;
  theta?: number;
}

/**
 * Scan messages[0..cursorIndex] in reverse and collect the most recent
 * position_update per actor. Returns a map suitable for setReplayPositions.
 * O(N) worst-case but stops per-actor as soon as the most-recent is found.
 */
function computeReplayPositions(
  raw: RawBusMessage[],
  cursorIndex: number,
): Record<string, ReplayPosition> {
  const result: Record<string, ReplayPosition> = {};
  // Walk backwards from cursor to 0.
  for (let i = cursorIndex; i >= 0; i--) {
    const msg = raw[i];
    if (msg.type !== "position_update") continue;
    const pay = msg.payload as PositionUpdatePayload | undefined;
    if (!pay) continue;
    const actorId = pay.actor_id ?? pay.agv_id;
    if (!actorId) continue;
    // Already have a more-recent entry for this actor — skip.
    if (actorId in result) continue;
    const x = pay.x ?? 0;
    const y = pay.y ?? 0;
    const theta = pay.theta ?? 0;
    result[actorId] = { x, y, theta };
  }
  return result;
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

/**
 * Dedup repeated AVAILABILITY_UPDATE heartbeats in a message list.
 * For each actor, consecutive heartbeats with the same payload.state are
 * collapsed — only the first sighting and state-change events are kept.
 * The raw backend log is NOT modified; this runs at render time only.
 */
function deduplicateHeartbeats(
  raw: RawBusMessage[],
  normalised: BusMessage[],
): BusMessage[] {
  const lastHeartbeatState = new Map<string, string>();
  const result: BusMessage[] = [];

  for (let i = 0; i < raw.length; i++) {
    const r = raw[i];
    const n = normalised[i];
    if (!n) continue;

    if (r.type !== "availability_update") {
      result.push(n);
      continue;
    }

    const pay = r.payload as Record<string, unknown> | undefined;
    const isHeartbeat = pay?.heartbeat === true;
    const newState = typeof pay?.state === "string" ? pay.state : undefined;

    if (!isHeartbeat || newState === undefined) {
      // Non-heartbeat availability_update — always show.
      result.push(n);
      continue;
    }

    const sender = r.sender ?? "system";
    const prevState = lastHeartbeatState.get(sender);

    if (prevState === undefined) {
      // First sighting.
      lastHeartbeatState.set(sender, newState);
      result.push({ ...n, text: `${sender} alive` });
    } else if (prevState !== newState) {
      // State transition.
      lastHeartbeatState.set(sender, newState);
      result.push({ ...n, text: `${sender}: ${prevState} → ${newState}` });
    }
    // else: same state — suppress.
  }

  return result;
}

export function ReplayPanel() {
  const [messages, setMessages] = useState<BusMessage[]>([]);
  // Keep the raw backend messages so computeReplayPositions can scan payloads.
  const [rawMessages, setRawMessages] = useState<RawBusMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);

  // Runtime slice actions — Phase 5 Stage 8 Track C.
  const setReplayPositions = useStore((s) => s.setReplayPositions);
  const clearReplayMode = useStore((s) => s.clearReplayMode);
  const replayMode = useStore((s) => s.replayMode);

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
      const deduped = deduplicateHeartbeats(data.messages, normalised);
      setMessages(deduped);
      setRawMessages(data.messages);
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

  // Phase 5 Stage 8 Track C — dispatch replayPositions on every cursor change
  // (play tick or user scrub). Reverse scan: O(N) but stops per-actor early.
  // Only dispatches when there are messages loaded.
  useEffect(() => {
    if (rawMessages.length === 0) return;
    const positions = computeReplayPositions(rawMessages, cursor);
    setReplayPositions(positions);
  }, [cursor, rawMessages, setReplayPositions]);

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

        {/* Phase 5 Stage 8 Track C — REPLAY badge + Return to live button.
            Shown whenever replayMode is active (i.e. any cursor is set). */}
        {replayMode && (
          <>
            <span className="shrink-0 rounded bg-amber-500/20 px-1.5 py-[2px] text-[9px] font-semibold uppercase tracking-widest text-amber-400 border border-amber-500/30">
              REPLAY
            </span>
            <button
              title="Return to live view"
              onClick={clearReplayMode}
              className="shrink-0 rounded border border-line px-2 py-[2px] text-[9.5px] text-ink-3 hover:bg-bg-hover hover:text-ink"
            >
              Return to live
            </button>
          </>
        )}
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
