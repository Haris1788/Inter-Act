import { cn } from "@/lib/cn";
import type { ActorRuntime } from "@/store/types";

interface SectionTaskQueueProps {
  taskQueue: ActorRuntime["taskQueue"];
  live: boolean;
}

export function SectionTaskQueue({ taskQueue, live }: SectionTaskQueueProps) {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">
          Task queue
        </span>
        {live ? (
          <span className="text-10.5 text-ink-5">not connected</span>
        ) : (
          <span className="text-11 text-ink-4">{taskQueue.length} queued</span>
        )}
      </div>
      {live ? (
        <span className="text-11.5 text-ink-4">-</span>
      ) : (
        <div className="flex flex-col gap-[5px]">
          {taskQueue.map((task) => (
            <div
              key={task.id}
              className={cn(
                "grid items-center gap-2 px-2 py-[6px] rounded-[7px] text-11.5",
                "grid-cols-[16px_1fr_auto]",
                task.active
                  ? "bg-white border border-cta"
                  : "bg-bg-surface border border-line-soft",
              )}
            >
              <span
                className={cn(
                  "font-mono text-10.5",
                  task.active ? "text-cta font-bold" : "text-ink-4",
                )}
              >
                {task.active ? "▶" : task.ix}
              </span>
              <span className="text-ink">
                {task.label}
                <span className="text-ink-4 font-mono text-10.5 ml-[5px]">
                  {task.sub}
                </span>
              </span>
              <span className="font-mono text-10.5 text-ink-3">
                eta {task.etaS.toFixed(1)}s
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
