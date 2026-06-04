import { useStore } from "@/store";

// US 34.1: dynamic active-AI badge. Previously hardcoded to
// "Ollama · qwen2.5:0.5b coordinator · 70% mem" which lied about what was
// running. Now reads `activeAi` from the runtime slice — that field is
// driven by bus events (task_announce → planner, bid/intent → tactical,
// arm_ready → arm-gate) and falls back to the qwen2.5:0.5b coordinator
// when nothing has fired yet (genuine idle state).
function roleLabel(role: string): string {
  switch (role) {
    case "planner":
      return "planner";
    case "tactical":
      return "tactical";
    case "judge":
      return "bid judge";
    case "arm-gate":
      return "arm gate";
    case "idle":
    default:
      return "coordinator";
  }
}

// Stale = no AI event in the last 30s. Drops the ring back to neutral so the
// badge doesn't claim "tactical busy" hours after the last action.
const STALE_AFTER_MS = 30_000;

export function LeftFoot() {
  const activeAi = useStore((s) => s.activeAi);
  const linkStatus = useStore((s) => s.linkStatus);

  const now = typeof performance !== "undefined" ? performance.now() : 0;
  const isStale = activeAi.tsMs > 0 && now - activeAi.tsMs > STALE_AFTER_MS;
  const role = isStale ? "idle" : activeAi.role;
  const model = activeAi.model;
  const source = isStale ? "" : activeAi.source;

  // Ring colour: green when AI is actively driving, neutral when idle/stale,
  // red when the runtime link is down (no events can arrive).
  const ringColor =
    linkStatus === "disconnected"
      ? "#C04E3A"
      : role !== "idle"
      ? "#5F8F58"
      : "#9C9685";

  return (
    <div className="flex items-center gap-[10px] border-t border-line-soft px-3 py-[10px]">
      <span
        className="relative inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full"
        style={{
          background: `conic-gradient(${ringColor} 0 70%, #EEEBE3 70% 100%)`,
        }}
      >
        <span className="absolute h-[14px] w-[14px] rounded-full bg-bg-panel" />
      </span>

      <div>
        <div className="text-11.5 text-ink-3">
          <strong className="font-semibold text-ink">Ollama</strong>
          {" · "}
          {model}
        </div>
        <div className="font-mono text-10.5 text-ink-4">
          {roleLabel(role)}
          {source ? ` · ${source}` : ""}
        </div>
      </div>
    </div>
  );
}
