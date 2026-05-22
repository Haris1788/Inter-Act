import { Field } from "./Field";

const EM = "-";

interface SectionDockingProps {
  live: boolean;
}

export function SectionDocking({ live }: SectionDockingProps) {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">
          Docking
        </span>
        <span className="text-10.5 text-ink-5">
          {live ? "not connected" : "no runtime data"}
        </span>
      </div>

      {/* Docking graph data lives in runtime; uniform placeholder for all actors. */}
      <span className="text-11.5 text-ink-4 block mb-2">{EM}</span>

      <Field label="Dock precision">
        <span className="font-mono text-11.5 text-ink-4">{EM}</span>
      </Field>
      <Field label="Approach speed">
        <span className="font-mono text-11.5 text-ink-4">{EM}</span>
      </Field>
    </div>
  );
}
