import { cn } from "@/lib/cn";
import { useDataSource, useLinkStatus } from "@/store/selectors";
import type { DataSource, LinkStatus } from "@/store/types";

type ChipConfig = {
  dotClass: string;
  label: string;
  pulse: boolean;
};

function resolveChip(source: DataSource, status: LinkStatus): ChipConfig {
  if (source === "mock") {
    return { dotClass: "bg-ink-5", label: "mock", pulse: false };
  }
  // live
  switch (status) {
    case "connected":
      return { dotClass: "bg-ok", label: "live", pulse: false };
    case "connecting":
      return { dotClass: "bg-warn", label: "connecting…", pulse: true };
    case "disconnected":
    default:
      return { dotClass: "bg-err", label: "offline", pulse: false };
  }
}

export function ConnectionChip() {
  const source = useDataSource();
  const status = useLinkStatus();
  const { dotClass, label, pulse } = resolveChip(source, status);

  return (
    <span
      className={cn(
        "font-mono text-10.5 tracking-wider",
        "inline-flex items-center gap-1.5",
        "rounded-full border border-line bg-bg-sunken px-[7px] py-px",
      )}
    >
      <span
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          dotClass,
          pulse && "animate-pulse-dot",
        )}
      />
      {label}
    </span>
  );
}
