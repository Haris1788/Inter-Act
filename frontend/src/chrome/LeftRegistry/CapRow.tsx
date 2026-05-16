interface CapRowProps {
  name: string;
  count: string;
  filled: boolean;
}

export function CapRow({ name, count, filled }: CapRowProps) {
  return (
    <div
      draggable
      className="grid cursor-grab items-center rounded-[7px] px-2 py-[6px] text-12 hover:bg-bg-hover"
      style={{ gridTemplateColumns: "22px 1fr auto", gap: "9px" }}
    >
      {/* .dot / .dot.filled */}
      <span
        className="mx-auto block h-2 w-2 rounded-[2px] border border-ink"
        style={{ background: filled ? "#18181B" : "#fbfbfd" }}
      />
      <span className="text-ink">{name}</span>
      <span className="font-mono text-10.5 text-ink-4">{count}</span>
    </div>
  );
}
