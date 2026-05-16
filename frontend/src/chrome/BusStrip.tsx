import { useBusMessages, useBusRate } from "@/store/selectors";
import { cn } from "@/lib/cn";
import type { BusKind } from "@/store/types";

function kindClasses(kind: BusKind): string {
  switch (kind) {
    case "announce":
      return "bg-bg-surface border-line";
    case "bid":
      return "bg-bg-sunken border-line";
    case "accept":
      return "bg-cta border-cta text-[#FAFAF7]";
    case "dock":
      return "bg-bg-surface border-cta text-ink";
    default:
      return "bg-bg-surface border-line";
  }
}

function LvlBadge({ kind }: { kind: BusKind }) {
  const isAccept = kind === "accept";
  return (
    <span
      className={cn(
        "rounded-[3px] border px-[5px] py-[1px] text-[9px] uppercase tracking-[0.08em]",
        isAccept
          ? "border-white/20 bg-white/18 text-white/90"
          : "border-line-soft bg-bg-sunken text-ink-4",
      )}
    >
      {kind}
    </span>
  );
}

export function BusStrip() {
  const messages = useBusMessages();
  const rate = useBusRate();

  return (
    <div
      className={cn(
        "absolute bottom-0 left-0 right-0 z-[5]",
        "flex h-[46px] items-center gap-0 overflow-hidden",
        "border-t border-line px-3.5",
      )}
      style={{
        background:
          "linear-gradient(to top, rgba(238,235,227,0.92) 0%, rgba(250,248,242,0.7) 100%)",
        backdropFilter: "blur(6px)",
      }}
    >
      {/* Label */}
      <div className="mr-3 flex shrink-0 items-center gap-[7px] border-r border-line-soft pr-3">
        <span
          className="animate-pulse-dot inline-block h-[7px] w-[7px] rounded-full bg-ok"
          style={{ boxShadow: "0 0 0 3px #E6EBE0" }}
        />
        <span className="font-mono text-10.5 font-bold uppercase tracking-[0.1em] text-ink">
          Message bus
        </span>
        <span className="font-mono text-10.5 text-ink-4">· {rate} msg/s</span>
      </div>

      {/* Feed */}
      <div className="flex min-w-0 flex-1 items-center gap-2.5 overflow-hidden">
        {messages.slice(-12).map((msg) => (
          <span
            key={msg.id}
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5",
              "h-6 rounded-full border px-[9px]",
              "font-mono text-11 text-ink-2",
              kindClasses(msg.kind),
            )}
          >
            <span
              className={cn(
                "text-ink-5",
                msg.kind === "accept" && "text-white/60",
              )}
            >
              {msg.ts}
            </span>
            <LvlBadge kind={msg.kind} />
            <strong
              className={cn(
                "font-semibold",
                msg.kind === "accept" ? "text-[#FAFAF7]" : "text-ink",
              )}
            >
              {msg.from}
            </strong>
            {msg.to && (
              <>
                <span
                  className={cn(
                    "mx-px",
                    msg.kind === "accept" ? "text-white/60" : "text-ink-4",
                  )}
                >
                  →
                </span>
                <strong
                  className={cn(
                    "font-semibold",
                    msg.kind === "accept" ? "text-[#FAFAF7]" : "text-ink",
                  )}
                >
                  {msg.to}
                </strong>
              </>
            )}
            {msg.text && (
              <span
                className={cn(
                  msg.kind === "accept" ? "text-white/70" : "text-ink-4",
                )}
              >
                {msg.text}
              </span>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}
