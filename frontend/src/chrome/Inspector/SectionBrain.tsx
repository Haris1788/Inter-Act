import { Field } from "./Field";
import { Seg } from "./Seg";
import { Slider } from "./Slider";

export function SectionBrain() {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">Brain</span>
      </div>
      <Field label="Policy">
        <div className="flex items-center justify-between h-[28px] px-2 bg-bg-surface border border-line rounded-sm text-12 text-ink">
          Scheduler · FIFO
          <span className="text-ink-4">▾</span>
        </div>
      </Field>
      <Field label="Negotiation">
        <Seg options={["passive", "cooperative", "aggressive"]} active="cooperative" />
      </Field>
      <Field label="Priority">
        <Slider fillPct={68} label="0.68" />
      </Field>
    </div>
  );
}
