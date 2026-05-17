interface SliderProps {
  fillPct: number;
  label: string;
}

export function Slider({ fillPct, label }: SliderProps) {
  const pct = `${fillPct}%`;
  return (
    <div className="grid gap-[10px] grid-cols-[1fr_60px] items-center">
      <div className="relative h-[6px] rounded-[3px] bg-bg-sunken">
        <span
          className="absolute inset-y-0 left-0 bg-ink rounded-[3px]"
          style={{ width: pct }}
        />
        <span
          className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 w-[14px] h-[14px] rounded-full bg-white border-[1.5px] border-ink shadow-e1"
          style={{ left: pct }}
        />
      </div>
      <span className="font-mono text-11 text-ink bg-bg-surface border border-line rounded-[5px] h-[24px] inline-flex items-center justify-center px-1">
        {label}
      </span>
    </div>
  );
}
