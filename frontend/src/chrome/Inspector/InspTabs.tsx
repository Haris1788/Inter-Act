import { cn } from "@/lib/cn";

const TABS = ["Properties", "Runtime", "Logs", "YAML"] as const;
type Tab = (typeof TABS)[number];

interface InspTabsProps {
  active: Tab;
  onChange: (tab: Tab) => void;
}

export function InspTabs({ active, onChange }: InspTabsProps) {
  return (
    <div className="flex gap-[2px] px-[10px] pt-[6px] pb-0 border-b border-line-soft">
      {TABS.map((tab) => (
        <span
          key={tab}
          onClick={() => onChange(tab)}
          className={cn(
            "px-[10px] py-2 text-12 cursor-pointer -mb-px border-b-2",
            tab === active
              ? "text-ink border-cta"
              : "text-ink-3 border-transparent hover:text-ink-2",
          )}
        >
          {tab}
        </span>
      ))}
    </div>
  );
}

export type { Tab };
