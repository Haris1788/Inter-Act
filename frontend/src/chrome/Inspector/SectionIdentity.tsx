import type { TopologyActor } from "@/store/types";
import { Field } from "./Field";

interface SectionIdentityProps {
  actor: TopologyActor;
}

export function SectionIdentity({ actor }: SectionIdentityProps) {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">Identity</span>
      </div>
      <Field label="Actor ID">
        <span className="font-mono text-11.5 text-ink-2">{actor.id}</span>
      </Field>
      <Field label="Type">
        <span className="text-12 text-ink">{actor.type}</span>
      </Field>
      <Field label="Container">
        <span className="font-mono text-11.5 text-ink-2">
          interact/veh:1.4 <span className="text-ink-5">·</span> :8010
        </span>
      </Field>
      <Field label="Label">
        <div className="flex items-center h-[28px] px-2 bg-bg-surface border border-line rounded-sm">
          <input
            className="border-0 bg-transparent outline-none w-full text-12 text-ink font-mono"
            defaultValue={actor.label}
            readOnly
          />
        </div>
      </Field>
    </div>
  );
}
