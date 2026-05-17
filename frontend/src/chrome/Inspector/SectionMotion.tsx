import type { TopologyActor } from "@/store/types";
import { Field } from "./Field";
import { Slider } from "./Slider";
import { Seg } from "./Seg";

interface SectionMotionProps {
  actor: TopologyActor;
  live: boolean;
}

const EM = "-";

/** Derive nominal top-speed label from actor spec string, or fall back to EM. */
function speedLabel(spec: string): string {
  const m = spec.match(/(\d+(?:\.\d+)?)\s*m\/s/);
  return m ? `${m[1]} m/s` : EM;
}

/** Map speed string to a 0-100 fill percentage (clamped, max=20 m/s). */
function speedFillPct(spec: string): number {
  const m = spec.match(/(\d+(?:\.\d+)?)\s*m\/s/);
  if (!m) return 0;
  return Math.min(100, Math.round((parseFloat(m[1]) / 20) * 100));
}

export function SectionMotion({ actor, live }: SectionMotionProps) {
  const spd = speedLabel(actor.spec);
  const spdFill = speedFillPct(actor.spec);

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
        ) : spd === EM ? (
          <span className="font-mono text-11.5 text-ink-4">{EM}</span>
        ) : (
          <Slider fillPct={spdFill} label={spd} />
        )}
      </Field>
      <Field label="Acceleration">
        <span className="font-mono text-11.5 text-ink-4">{EM}</span>
      </Field>
      <Field label="Turning radius">
        <span className="font-mono text-11.5 text-ink-4">{EM}</span>
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
