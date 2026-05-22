import type { TopologyActor } from "@/store/types";
import { canonicalLabel } from "@/lib/actorNormalizer";

/**
 * Canonical display name for an actor, used consistently across InspHead,
 * ActorNode, FleetRoster and any other actor surface.
 *
 * Rule: if `label` is non-empty and differs from the raw id, use `label`;
 * otherwise title-case the id segments ("arm-1" → "Arm 1", "veh-a" → "Veh A").
 * The title-case rule is shared with `normalizeActor` via `canonicalLabel`.
 */
export function displayName(actor: TopologyActor): string {
  if (actor.label && actor.label !== actor.id) return actor.label;
  return canonicalLabel(actor.id);
}
