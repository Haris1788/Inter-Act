import { useEffect, useState } from "react";
import { useGoal, useBusMessages } from "@/store/selectors";
import { useStore } from "@/store";
import { cn } from "@/lib/cn";

interface GoalBarProps {
  className?: string;
}

export function GoalBar({ className }: GoalBarProps) {
  const goal = useGoal();
  const setGoalText = useStore((s) => s.setGoalText);
  const busMessages = useBusMessages();
  const [negotiating, setNegotiating] = useState(false);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  // Watch bus for completion of the active task. The /negotiate POST returns
  // immediately (202) — actual end is when all 3 legs complete OR a fault /
  // task_failed lands. Subscribe to bus messages, count leg_completed events
  // for the active task_id, flip the loader off when done.
  useEffect(() => {
    if (!activeTaskId) return;
    const taskMsgs = busMessages.filter(
      (m) => m.task_id === activeTaskId,
    );
    const legsDone = taskMsgs.filter((m) => m.kind === "leg_completed").length;
    const failed = taskMsgs.some(
      (m) => m.kind === "task_failed" || m.kind === "fault",
    );
    if (legsDone >= 3 || failed) {
      setNegotiating(false);
      setActiveTaskId(null);
      setFeedback(failed ? "Negotiation failed — see bus log" : "Done");
      const t = setTimeout(() => setFeedback(null), 4000);
      return () => clearTimeout(t);
    }
  }, [busMessages, activeTaskId]);

  // POST /negotiate — sync mode so we capture the task_id; runtime
  // continues asynchronously and the useEffect above watches for
  // completion via the bus stream.
  async function handleNegotiate() {
    const text = goal.text.trim();
    if (!text || negotiating) return;
    setNegotiating(true);
    setFeedback(null);
    setActiveTaskId(null);
    try {
      const res = await fetch("/negotiate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text, sync: true }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        task_id?: string;
        error?: string;
      };
      if (res.ok && data.task_id) {
        setActiveTaskId(data.task_id);
        setFeedback("Negotiating — watching bus for completion…");
      } else {
        setFeedback(data.error ?? `Unexpected status ${res.status}`);
        setNegotiating(false);
      }
    } catch {
      setFeedback("Could not reach backend");
      setNegotiating(false);
    }
  }

  return (
    <div
      className={cn(
        "absolute left-1/2 top-16 z-[6] -translate-x-1/2",
        "flex items-center gap-2.5",
        "w-[min(780px,calc(100%-60px))]",
        "rounded-lg border border-line bg-bg-surface py-2 pl-3 pr-2",
        "shadow-e2",
        className,
      )}
    >
      {/* Label */}
      <div className="flex shrink-0 items-center gap-2 border-r border-line-soft pr-2.5">
        <span
          className={cn(
            "inline-flex h-[26px] w-[26px] items-center justify-center rounded-md",
            "bg-cta text-[#FAFAF7]",
          )}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="12" r="4" />
          </svg>
        </span>
        <span className="text-10 font-bold uppercase tracking-[0.1em] text-ink">Goal</span>
      </div>

      {/* Input */}
      <div className="flex min-w-0 flex-1 items-center">
        <input
          type="text"
          value={goal.text}
          onChange={(e) => setGoalText(e.target.value)}
          placeholder="Describe a negotiation goal…"
          className={cn(
            "w-full border-0 bg-transparent outline-none",
            "text-13 text-ink placeholder:text-ink-4",
            "py-0.5",
          )}
        />
      </div>

      {/* Negotiate — sync POST captures task_id; bus stream signals done */}
      <button
        onClick={handleNegotiate}
        disabled={negotiating}
        title="Submit goal to the negotiation runtime. Agents bid autonomously; observe progress in the Message Bus panel."
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5",
          "h-8 rounded-md border border-cta bg-cta px-3",
          "text-12 font-semibold text-[#FAFAF7]",
          "hover:bg-cta-2 disabled:opacity-70 disabled:cursor-not-allowed",
        )}
      >
        {negotiating ? (
          <>
            {/* Spinning loader */}
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              className="animate-spin"
              style={{ animation: "spin 0.8s linear infinite" }}
            >
              <path d="M21 12a9 9 0 1 1-6.219-8.56" />
            </svg>
            <span>Negotiating…</span>
          </>
        ) : (
          <>
            <span>Negotiate</span>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </>
        )}
      </button>

      {/* Feedback toast */}
      {feedback && (
        <div className={cn(
          "absolute left-1/2 top-full mt-2 -translate-x-1/2",
          "rounded-md border border-line bg-bg-surface px-3 py-1.5 shadow-e2",
          "text-12 text-ink whitespace-nowrap",
        )}>
          {feedback}
        </div>
      )}

      {/* CSS keyframes for the spinner */}
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
