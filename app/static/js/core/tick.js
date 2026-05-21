/* Phase 3: frontend no longer simulates pipeline progress.
 *
 * All asset state - vehicle position, asset status -
 * is driven exclusively by backend SocketIO events (`object_update`,
 * `state_update`, `arm_health`, `asset_health`). This file used to host
 * `tickVehicle` / `driveTransits` / `checkProximityHandovers`;
 * those have been removed because they introduced frontend-side guesses about
 * pipeline progress, violating the "frontend is a pure reflection" rule.
 *
 * The exported `tick(now)` is kept as a no-op so main.js's loop signature
 * stays unchanged. Future visual-only side effects (e.g. flash GC, debug
 * overlay refresh) can hook in here without re-introducing simulation.
 */

export function tick(_now) {
  // No-op: backend is the sole driver of pipeline progress.
}
