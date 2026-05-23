// Shared chip/label helpers for NegotiationBus message types.
// Both BusPane (live stream) and ReplayPanel (history) import from here so
// there is exactly ONE renderer for kind → chip/label. Do not duplicate.
import type { BusKind } from "@/store/types";

// Human-readable label for each real NegotiationBus message type.
export const KIND_LABELS: Record<BusKind, string> = {
  task_announce: "announce",
  capability_broadcast: "capability",
  availability_update: "available",
  bid: "bid",
  counter_offer: "counter",
  accept: "accept",
  reject: "reject",
  busy: "busy",
  fault: "fault",
  withdraw: "withdraw",
  consensus_reached: "consensus",
  task_failed: "failed",
  // Stage 4 leg execution events
  leg_started: "leg·start",
  leg_completed: "leg·done",
  leg_halted: "leg·halt",
};

// Tailwind class string for the chip background/foreground.
export function kindChipClass(kind: BusKind): string {
  switch (kind) {
    case "task_announce":
      return "bg-info-bg text-info";
    case "capability_broadcast":
    case "availability_update":
      return "bg-bg-sunken text-ink-3";
    case "bid":
    case "counter_offer":
      return "bg-warn-bg text-warn";
    case "accept":
    case "consensus_reached":
      return "bg-ok-bg text-ok";
    case "reject":
    case "busy":
    case "withdraw":
      return "bg-bg-sunken text-ink-3";
    case "fault":
    case "task_failed":
    case "leg_halted":
      return "bg-err-bg text-err";
    case "leg_started":
      return "bg-info-bg text-info";
    case "leg_completed":
      return "bg-ok-bg text-ok";
  }
}

// Hex color for SVG/Canvas rendering (used in Timeline).
// Maps to the same visual tones as kindChipClass but as raw hex.
export const KIND_TONE_HEX: Record<BusKind, { fill: string; stroke: string }> =
  {
    task_announce: { fill: "#E4E8EC", stroke: "#5C6B7A" },
    capability_broadcast: { fill: "#EEEBE3", stroke: "#6B6B72" },
    availability_update: { fill: "#EEEBE3", stroke: "#6B6B72" },
    bid: { fill: "#F0E6D0", stroke: "#B08742" },
    counter_offer: { fill: "#F0E6D0", stroke: "#B08742" },
    accept: { fill: "#E6EBE0", stroke: "#5F8F58" },
    reject: { fill: "#EEEBE3", stroke: "#6B6B72" },
    busy: { fill: "#EEEBE3", stroke: "#6B6B72" },
    fault: { fill: "#EEDAD1", stroke: "#A4503B" },
    withdraw: { fill: "#EEEBE3", stroke: "#6B6B72" },
    consensus_reached: { fill: "#E6EBE0", stroke: "#5F8F58" },
    task_failed: { fill: "#EEDAD1", stroke: "#A4503B" },
    leg_started: { fill: "#E4E8EC", stroke: "#5C6B7A" },
    leg_completed: { fill: "#E6EBE0", stroke: "#5F8F58" },
    leg_halted: { fill: "#EEDAD1", stroke: "#A4503B" },
  };
