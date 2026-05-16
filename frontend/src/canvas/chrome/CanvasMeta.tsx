// Canvas meta pill — top-right: environment indicator.
// Mirrors .canvas-meta / .pill from styles-v2.css.

export function CanvasMeta() {
  return (
    <div className="absolute right-[14px] top-[14px] z-[5] flex items-center gap-2">
      <div className="inline-flex h-[28px] items-center gap-[7px] rounded-[7px] border border-line bg-white px-[10px] text-12 text-ink-2 shadow-e1">
        <span className="h-[7px] w-[7px] rounded-full bg-ok shadow-[0_0_0_2px_rgba(95,143,88,0.20)]" />
        environment
        <span className="font-mono text-11 text-ink-3">staging</span>
      </div>
    </div>
  );
}
