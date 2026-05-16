import { Field } from "./Field";
import { Slider } from "./Slider";
import { Seg } from "./Seg";

interface SectionMotionProps {
  live: boolean;
}

const EM = "-";

export function SectionMotion({ live }: SectionMotionProps) {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">
          Motion
        </span>
        {!live && <span className="text-11 text-ink-4">reset</span>}
        {live && <span className="text-10.5 text-ink-5">authored only</span>}
      </div>
      <Field label="Top speed">
        {live ? (
          <span className="font-mono text-11.5 text-ink-4">{EM}</span>
        ) : (
          <Slider fillPct={82} label="10.0 m/s" />
        )}
      </Field>
      <Field label="Acceleration">
        {live ? (
          <span className="font-mono text-11.5 text-ink-4">{EM}</span>
        ) : (
          <Slider fillPct={55} label="2.2 m/s²" />
        )}
      </Field>
      <Field label="Turning radius">
        {live ? (
          <span className="font-mono text-11.5 text-ink-4">{EM}</span>
        ) : (
          <div className="flex items-center h-[28px] px-2 bg-bg-surface border border-line rounded-sm gap-1">
            <input
              className="border-0 bg-transparent outline-none w-full text-12 text-ink font-mono"
              defaultValue="0.60"
              readOnly
            />
            <span className="text-11 text-ink-4 font-mono ml-[6px]">m</span>
          </div>
        )}
      </Field>
      <Field label="Precision">
        {live ? (
          <span className="font-mono text-11.5 text-ink-4">{EM}</span>
        ) : (
          <Seg options={["coarse", "standard", "fine"]} active="standard" />
        )}
      </Field>
    </div>
  );
}
