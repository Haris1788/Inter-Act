interface SegProps {
  options: string[];
  active: string;
}

export function Seg({ options, active }: SegProps) {
  return (
    <span className="inline-flex p-[2px] bg-bg-sunken border border-line-soft rounded-sm">
      {options.map((opt) => (
        <span
          key={opt}
          className={
            opt === active
              ? "px-2 py-[3px] text-11 text-ink bg-bg-elevated rounded-[4px] shadow-e1"
              : "px-2 py-[3px] text-11 text-ink-3 rounded-[4px]"
          }
        >
          {opt}
        </span>
      ))}
    </span>
  );
}
