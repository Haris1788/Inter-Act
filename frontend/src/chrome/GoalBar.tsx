import { useState } from "react";
import { useGoal } from "@/store/selectors";
import { useStore } from "@/store";
import { cn } from "@/lib/cn";

interface GoalBarProps {
  className?: string;
}

export function GoalBar({ className }: GoalBarProps) {
  const goal = useGoal();
  const setGoalText = useStore((s) => s.setGoalText);
  const [dispatching, setDispatching] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  async function handleDispatch() {
    const text = goal.text.trim();
    if (!text || dispatching) return;
    setDispatching(true);
    setFeedback(null);
    try {
      const res = await fetch("/demo/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal: text }),
      });
      const data = await res.json();
      if (data.status === "dispatched") {
        const names = (data.assignments as { agv_id: string; package_id: string }[])
          .map((a) => `${a.agv_id} → ${a.package_id}`)
          .join(", ");
        setFeedback(`Dispatched (no AI): ${names}`);
      } else {
        setFeedback(data.error ?? "dispatch failed");
      }
    } catch {
      setFeedback("Could not reach backend");
    } finally {
      setDispatching(false);
      setTimeout(() => setFeedback(null), 5000);
    }
  }

  // POST /plan — fires the AI path: qwq:32b interprets + plans, plan_executor
  // delivers assignments to the AGV containers, each AGV consults qwen14b for
  // every step. Runs async (returns 202 immediately); progress streams into
  // the log panel via SocketIO `log_message` and `AI decision:` lines.
  async function handlePlan() {
    const text = goal.text.trim();
    if (!text || planning) return;
    setPlanning(true);
    setFeedback("AI planning… (first call cold-loads qwq:32b — minutes)");
    try {
      const res = await fetch("/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text }),
      });
      const data = await res.json();
      if (res.ok) {
        setFeedback(
          data.message ?? "AI plan accepted — watch the log panel for progress",
        );
      } else {
        setFeedback(data.error ?? "AI plan failed (is /ai/health connected?)");
      }
    } catch {
      setFeedback("Could not reach backend");
    } finally {
      setPlanning(false);
      setTimeout(() => setFeedback(null), 8000);
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
          placeholder="Describe a goal…"
          className={cn(
            "w-full border-0 bg-transparent outline-none",
            "text-13 text-ink placeholder:text-ink-4",
            "py-0.5",
          )}
        />
      </div>

      {/* AI Plan — runs the master AI (qwq:32b) + per-agent qwen14b */}
      <button
        onClick={handlePlan}
        disabled={planning}
        title="Send the goal to the master AI (qwq:32b on TUC). The planner assigns AGVs and each AGV's qwen14b decides every step from shared status. First call cold-loads the 32B model (minutes)."
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5",
          "h-8 rounded-md border border-cta bg-transparent px-3",
          "text-12 font-semibold text-cta",
          "hover:bg-cta hover:text-[#FAFAF7]",
          "disabled:opacity-50 disabled:cursor-not-allowed",
        )}
      >
        <span>{planning ? "Planning…" : "AI Plan"}</span>
      </button>

      {/* Dispatch — AI-free baseline path */}
      <button
        onClick={handleDispatch}
        disabled={dispatching}
        title="Run a pre-scripted dispatch (no AI). Useful as a visual baseline."
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5",
          "h-8 rounded-md border border-cta bg-cta px-3",
          "text-12 font-semibold text-[#FAFAF7]",
          "hover:bg-cta-2 disabled:opacity-50 disabled:cursor-not-allowed",
        )}
      >
        <span>{dispatching ? "Sending…" : "Dispatch"}</span>
        {!dispatching && (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
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
    </div>
  );
}
