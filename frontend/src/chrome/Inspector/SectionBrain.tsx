import type { TopologyActor } from "@/store/types";
import { Field } from "./Field";
import { Seg } from "./Seg";
import { Slider } from "./Slider";

const EM = "—";

interface SectionBrainProps {
  actor: TopologyActor;
  live: boolean;
}

export function SectionBrain({ actor, live }: SectionBrainProps) {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">Brain</span>
      </div>
      <Field label="Model">
        {/* actor.brain is topology data — always available */}
        <span className="font-mono text-11.5 text-ink-2">{actor.brain || EM}</span>
      </Field>
      <Field label="Policy">
        {live ? (
          <span className="font-mono text-11.5 text-ink-4">{EM}</span>
        ) : (
          <div className="flex items-center justify-between h-[28px] px-2 bg-bg-surface border border-line rounded-sm text-12 text-ink">
            Scheduler · FIFO
            <span className="text-ink-4">▾</span>
          </div>
        )}
      </Field>
      <Field label="Negotiation">
        {live ? (
          <span className="font-mono text-11.5 text-ink-4">{EM}</span>
        ) : (
          <Seg options={["passive", "cooperative", "aggressive"]} active="cooperative" />
        )}
      </Field>
      <Field label="Priority">
        {live ? (
          <span className="font-mono text-11.5 text-ink-4">{EM}</span>
        ) : (
          <Slider fillPct={68} label="0.68" />
        )}
      </Field>
    </div>
  );
}
