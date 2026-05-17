interface GaugeProps {
  label: string;
  value: string;
  fillPct: number;
  okColor?: boolean;
}

export function Gauge({ label, value, fillPct, okColor }: GaugeProps) {
  return (
    <div>
      <div className="text-11 text-ink-3 mb-1">{label}</div>
      <div className="font-mono text-12 text-ink mb-[5px]">{value}</div>
      <div className="relative h-[7px] rounded-[4px] overflow-hidden bg-bg-sunken">
        <div
          className={okColor ? "h-full bg-ok" : "h-full bg-ink"}
          style={{ width: `${fillPct}%` }}
        />
      </div>
    </div>
  );
}
