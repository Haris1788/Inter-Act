import type { TopologyActor } from "@/store/types";
import { Field } from "./Field";

const EM = "-";

interface SectionBrainProps {
  actor: TopologyActor;
  live: boolean;
}

export function SectionBrain({ actor }: SectionBrainProps) {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">
          Brain
        </span>
      </div>
      <Field label="Model">
        {/* actor.brain is topology data - always available */}
        <span className="font-mono text-11.5 text-ink-2">
          {actor.brain || EM}
        </span>
      </Field>
      <Field label="Policy">
        {/* Policy maps to actor.brain; Negotiation/Priority are not in topology. */}
        <span className="font-mono text-11.5 text-ink-2">
          {actor.brain || EM}
        </span>
      </Field>
      <Field label="Negotiation">
        <span className="font-mono text-11.5 text-ink-4">{EM}</span>
      </Field>
      <Field label="Priority">
        <span className="font-mono text-11.5 text-ink-4">{EM}</span>
      </Field>
    </div>
  );
}
