export function LeftFoot() {
  return (
    <div className="flex items-center gap-[10px] border-t border-line-soft px-3 py-[10px]">
      {/*
        .left-foot .ring
        conic-gradient: ok color 0→70%, bg-sunken 70%→100%
        After pseudo-element: 14×14 circle in bg-panel color (donut hole)
        Reproduced as two stacked absolutely-positioned elements.
      */}
      <span
        className="relative inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full"
        style={{
          background: "conic-gradient(#5F8F58 0 70%, #EEEBE3 70% 100%)",
        }}
      >
        {/* donut hole */}
        <span className="absolute h-[14px] w-[14px] rounded-full bg-bg-panel" />
      </span>

      <div>
        <div className="text-11.5 text-ink-3">
          <strong className="font-semibold text-ink">Ollama</strong>
          {" · qwen2.5:0.5b"}
        </div>
        <div className="font-mono text-10.5 text-ink-4">coordinator · 70% mem</div>
      </div>
    </div>
  );
}
