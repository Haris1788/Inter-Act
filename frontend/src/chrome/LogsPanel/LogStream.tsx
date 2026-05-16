import { useLogLines } from "@/store/selectors";
import { cn } from "@/lib/cn";

/** Map segment cls → Tailwind color class */
function segClass(cls: string | undefined): string {
  switch (cls) {
    case "id":
      return "text-cta";
    case "id2":
      return "text-ink";
    case "dim":
      return "text-ink-5";
    case "ok":
      return "text-ok";
    case "num":
      return "text-info";
    case "b":
      return "font-semibold text-ink";
    default:
      return "";
  }
}

/** Map log level → CSS class for the .lvl column */
function lvlClass(level: string): string {
  switch (level) {
    case "ok":
      return "text-ok";
    case "warn":
      return "text-warn";
    case "info":
      return "text-info";
    case "neg":
      return "text-ink font-semibold";
    case "dock":
      return "text-cta font-semibold";
    case "dbg":
      return "text-ink-4";
    default:
      return "text-ink-4";
  }
}

/** Map log level → display label */
function lvlLabel(level: string): string {
  switch (level) {
    case "ok":
      return "OK   ";
    case "warn":
      return "WARN ";
    case "info":
      return "INFO ";
    case "neg":
      return "NEG  ";
    case "dock":
      return "DOCK ";
    case "dbg":
      return "DBG  ";
    default:
      return level.toUpperCase();
  }
}

export function LogStream() {
  const lines = useLogLines();

  return (
    <div className="overflow-y-auto border-r border-line-soft py-2 font-mono text-11.5">
      {lines.map((line) => (
        <div
          key={line.id}
          className="grid gap-2 px-3.5 py-[1px] leading-[1.55]"
          style={{ gridTemplateColumns: "70px 56px 1fr" }}
        >
          {/* Timestamp */}
          <span className="text-ink-5">{line.ts}</span>

          {/* Level */}
          <span
            className={cn(
              "self-center py-[1px] text-center text-[9.5px] tracking-[0.05em]",
              lvlClass(line.level),
            )}
          >
            {lvlLabel(line.level)}
          </span>

          {/* Message */}
          <span className="break-words text-ink-2" style={{ whiteSpace: "pre-wrap" }}>
            {line.segments.map((seg, i) =>
              seg.cls ? (
                <span key={i} className={segClass(seg.cls)}>
                  {seg.text}
                </span>
              ) : (
                seg.text
              ),
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
