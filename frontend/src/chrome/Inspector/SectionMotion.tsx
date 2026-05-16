import { Field } from "./Field";
import { Slider } from "./Slider";
import { Seg } from "./Seg";

export function SectionMotion() {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">Motion</span>
        <span className="text-11 text-ink-4">reset</span>
      </div>
      <Field label="Top speed">
        <Slider fillPct={82} label="10.0 m/s" />
      </Field>
      <Field label="Acceleration">
        <Slider fillPct={55} label="2.2 m/s²" />
      </Field>
      <Field label="Turning radius">
        <div className="flex items-center h-[28px] px-2 bg-bg-surface border border-line rounded-sm gap-1">
          <input
            className="border-0 bg-transparent outline-none w-full text-12 text-ink font-mono"
            defaultValue="0.60"
            readOnly
          />
          <span className="text-11 text-ink-4 font-mono ml-[6px]">m</span>
        </div>
      </Field>
      <Field label="Precision">
        <Seg options={["coarse", "standard", "fine"]} active="standard" />
      </Field>
    </div>
  );
}
