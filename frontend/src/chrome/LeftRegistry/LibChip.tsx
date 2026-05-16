interface LibChipProps {
  icon: React.ReactNode;
  name: string;
  sub: string;
}

export function LibChip({ icon, name, sub }: LibChipProps) {
  return (
    <div
      draggable
      className="flex cursor-grab flex-col rounded-md border border-line bg-bg-surface p-[9px_9px_8px] transition-[box-shadow,border-color,transform] duration-[120ms] hover:border-line-strong hover:shadow-e1 active:translate-y-px active:cursor-grabbing"
    >
      {/* .top */}
      <div className="flex items-center gap-[7px]">
        {/* .icon */}
        <span className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-[5px] bg-bg-sunken text-ink-2">
          {icon}
        </span>
        <span className="text-12 font-medium text-ink">{name}</span>
      </div>
      {/* .sub */}
      <div className="mt-[6px] font-mono text-10.5 text-ink-4">{sub}</div>
    </div>
  );
}
