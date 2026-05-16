import type { TopologyActor } from "@/store/types";

/**
 * Canonical display name for an actor, used consistently across InspHead and
 * any other inspector surface.
 *
 * Rule: if `label` is non-empty and differs from the raw id, use `label`;
 * otherwise title-case the id segments ("arm-1" → "Arm 1", "veh-a" → "Veh A").
 */
export function displayName(actor: TopologyActor): string {
  if (actor.label && actor.label !== actor.id) return actor.label;
  // title-case id: split on "-", capitalise each part, join with space
  return actor.id
    .split("-")
    .map((seg) => seg.charAt(0).toUpperCase() + seg.slice(1))
    .join(" ");
}
