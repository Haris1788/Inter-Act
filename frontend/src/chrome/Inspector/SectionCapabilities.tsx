import { cn } from "@/lib/cn";

interface Cap {
  label: string;
  on: boolean;
}

const VEH1_CAPS: Cap[] = [
  { label: "transit", on: true },
  { label: "dock", on: true },
  { label: "carry · 1", on: true },
  { label: "weigh", on: false },
  { label: "inspect", on: false },
  { label: "charge-self", on: false },
];

export function SectionCapabilities() {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">Capabilities</span>
        <span className="text-11 text-ink-4">advertise</span>
      </div>
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        {VEH1_CAPS.map((cap) => (
          <div
            key={cap.label}
            className={cn(
              "grid gap-[6px] items-center py-[2px]",
              "grid-cols-[14px_1fr]",
              cap.on ? "text-11.5 text-ink-2" : "text-11.5 text-ink-4",
            )}
          >
            <span
              className={cn(
                "w-[10px] h-[10px] rounded-[2px] border border-ink ml-[2px]",
                cap.on ? "bg-ink" : "bg-bg-surface",
              )}
            />
            {cap.label}
          </div>
        ))}
      </div>
    </div>
  );
}
