import { useCallback } from "react";
import type { TopologyActor } from "@/store/types";
import { useStore } from "@/store";
import { Field } from "./Field";

interface SectionSpatialProps {
  actor: TopologyActor;
}

// ── Stepper sub-component ──────────────────────────────────────────────────

interface StepperProps {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
}

function Stepper({ value, min, max, step, onChange, format }: StepperProps) {
  const label = format ? format(value) : String(value);
  const dec = () => onChange(Math.max(min, parseFloat((value - step).toFixed(10))));
  const inc = () => onChange(Math.min(max, parseFloat((value + step).toFixed(10))));

  return (
    <div className="flex items-center gap-[4px]">
      <button
        onClick={dec}
        disabled={value <= min}
        className="w-[22px] h-[22px] rounded-[4px] border border-line bg-bg-surface text-ink-3 text-13 leading-none flex items-center justify-center hover:bg-bg-raised active:bg-line disabled:opacity-30 disabled:cursor-not-allowed select-none"
      >
        −
      </button>
      <span className="font-mono text-11.5 text-ink w-[42px] text-center bg-bg-surface border border-line rounded-[4px] h-[22px] inline-flex items-center justify-center px-1">
        {label}
      </span>
      <button
        onClick={inc}
        disabled={value >= max}
        className="w-[22px] h-[22px] rounded-[4px] border border-line bg-bg-surface text-ink-3 text-13 leading-none flex items-center justify-center hover:bg-bg-raised active:bg-line disabled:opacity-30 disabled:cursor-not-allowed select-none"
      >
        +
      </button>
    </div>
  );
}

// ── Reach slider sub-component ─────────────────────────────────────────────

const REACH_MIN = 40;
const REACH_MAX = 400;
const REACH_STEP = 10;

interface ReachSliderProps {
  value: number;
  onChange: (v: number) => void;
}

function ReachSlider({ value, onChange }: ReachSliderProps) {
  const fillPct = ((value - REACH_MIN) / (REACH_MAX - REACH_MIN)) * 100;

  const handleSlider = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      onChange(Number(e.target.value));
    },
    [onChange],
  );

  const handleInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = Number(e.target.value);
      if (!isNaN(v)) {
        onChange(Math.max(REACH_MIN, Math.min(REACH_MAX, Math.round(v / REACH_STEP) * REACH_STEP)));
      }
    },
    [onChange],
  );

  return (
    <div className="flex items-center gap-[6px]">
      {/* Track */}
      <div className="relative flex-1 h-[6px] rounded-[3px] bg-bg-sunken">
        <span
          className="absolute inset-y-0 left-0 bg-ink rounded-[3px]"
          style={{ width: `${fillPct}%` }}
        />
        <span
          className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 w-[14px] h-[14px] rounded-full bg-white border-[1.5px] border-ink shadow-e1 pointer-events-none"
          style={{ left: `${fillPct}%` }}
        />
        {/* Invisible native range on top */}
        <input
          type="range"
          min={REACH_MIN}
          max={REACH_MAX}
          step={REACH_STEP}
          value={value}
          onChange={handleSlider}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
        />
      </div>
      {/* Numeric input */}
      <input
        type="number"
        min={REACH_MIN}
        max={REACH_MAX}
        step={REACH_STEP}
        value={value}
        onChange={handleInput}
        className="w-[46px] h-[22px] font-mono text-11 text-ink bg-bg-surface border border-line rounded-[4px] text-center outline-none focus:border-cta px-1"
      />
    </div>
  );
}

// ── Section ────────────────────────────────────────────────────────────────

export function SectionSpatial({ actor }: SectionSpatialProps) {
  const updateActor = useStore((s) => s.updateActor);
  const markDirty = useStore((s) => s.markDirty);

  const patch = useCallback(
    <K extends "reach_px" | "capacity" | "speed">(key: K, value: TopologyActor[K]) => {
      updateActor(actor.id, { [key]: value });
      markDirty();
    },
    [actor.id, updateActor, markDirty],
  );

  const x = actor.position.x.toFixed(1);
  const y = actor.position.y.toFixed(1);

  return (
    <div className="px-[14px] pt-[14px] pb-1">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">
          Spatial
        </span>
      </div>

      {/* Position — read-only */}
      <Field label="Position">
        <div className="flex items-center gap-[6px]">
          <span className="font-mono text-11.5 text-ink-2">
            ({x}, {y})
          </span>
          <span className="text-10.5 text-ink-5 whitespace-nowrap">move on canvas</span>
        </div>
      </Field>

      {/* Reach radius — arms only */}
      {actor.kind === "arm" && (
        <Field label="Reach radius">
          <ReachSlider
            value={actor.reach_px}
            onChange={(v) => patch("reach_px", v)}
          />
        </Field>
      )}

      {/* Capacity stepper */}
      <Field label="Capacity">
        <Stepper
          value={actor.capacity}
          min={1}
          max={4}
          step={1}
          onChange={(v) => patch("capacity", v)}
        />
      </Field>

      {/* Speed stepper */}
      <Field label="Speed mult.">
        <Stepper
          value={actor.speed}
          min={0.5}
          max={3.0}
          step={0.5}
          onChange={(v) => patch("speed", v)}
          format={(v) => `×${v.toFixed(1)}`}
        />
      </Field>
    </div>
  );
}
