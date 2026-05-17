import { useGoal } from "@/store/selectors";
import { useStore } from "@/store";
import { cn } from "@/lib/cn";

interface GoalBarProps {
  className?: string;
}

export function GoalBar({ className }: GoalBarProps) {
  const goal = useGoal();
  const setGoalText = useStore((s) => s.setGoalText);
  const toggleConstraint = useStore((s) => s.toggleConstraint);

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

      {/* Constraints */}
      <div className="flex shrink-0 items-center gap-[3px] border-l border-line-soft px-2">
        {Object.entries(goal.constraints).map(([key, on]) => (
          <button
            key={key}
            onClick={() => toggleConstraint(key)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-[7px] py-[2px]",
              "font-mono text-10 tracking-[0.02em]",
              "border transition-colors",
              on
                ? "border-cta bg-cta text-[#FAFAF7]"
                : "border-line-soft bg-bg-sunken text-ink-2",
            )}
          >
            {key}
          </button>
        ))}
      </div>

      {/* Dispatch */}
      <button
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5",
          "h-8 rounded-md border border-cta bg-cta px-3",
          "text-12 font-semibold text-[#FAFAF7]",
          "hover:bg-cta-2",
        )}
      >
        <span>Dispatch</span>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="22" y1="2" x2="11" y2="13" />
          <polygon points="22 2 15 22 11 13 2 9 22 2" />
        </svg>
      </button>
    </div>
  );
}
