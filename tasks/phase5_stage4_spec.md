# Phase 5 Stage 4 Committed Sub-Spec — Spatial Negotiation + Distance-Aware Bidding

**THE acceptance gate.** Moving an actor on the canvas MUST change bid
scores + winners + timings. If this stage's tests do not pass, Phase 5 is
failed by definition. Single track — consensus invariant.

## Decisions (approved)

- Bid metric: Euclidean distance for time-based constraints, Manhattan for
  distance-based.
- `public_winner` extends with `shortest time` / `longest time` /
  `shortest distance` / `longest distance` / `heaviest load`.

## Required behaviour

- `ArmAgent._bid_score(slot)` becomes spatial:
  - return `None` if slot is not an arm slot.
  - return `None` if `slot.target_pos` is outside `self.profile.reach_px` of
    `self.profile.position_px` (arm physically can't reach).
  - else: return Euclidean distance from arm to target. Closer arm wins
    ties via the existing `public_winner` rule.
- `AGVAgent._bid_score(slot)` becomes spatial:
  - return `None` if not transport.
  - else: cost = euclid(self.position_px → slot.from_pos) + euclid(
    slot.from_pos → slot.to_pos). The first term = pickup approach cost;
    the second term = trip cost. Total = time-proportional total work.
- `public_winner(bids, constraint)` extends:
  - `""` or unrecognised: lowest-sender-id tiebreak (current default).
  - `"shortest time"` / `"shortest distance"`: min `payload.score` (current).
  - `"longest time"` / `"longest distance"`: MAX `payload.score` (new).
  - `"heaviest load"`: requires `payload.capacity` (new field); winner =
    max capacity, ties by min score.
- The constraint string comes from `task.constraint` (already in announce
  payload).
- Legacy `load_profiles()` warehouse path must populate `position_px` from
  `bay_cell × cell_px` so spatial scoring works without topology.

## Files

### 1. `app/agents/arm_agent.py` (rewrite `_bid_score`)
```python
import math
from typing import Optional

class ArmAgent(ActorAgent):
    def _bid_score(self, slot: dict) -> Optional[float]:
        if slot.get("kind") != "arm":
            return None
        target = slot.get("target_pos")
        if target is None:
            # Legacy zone-string slot (Phase 1-4 path) — fall back.
            zone = slot.get("zone")
            if zone is None or zone not in self.profile.reachable_zones:
                return None
            return 0.0
        ax, ay = self.profile.position_px
        tx, ty = float(target[0]), float(target[1])
        dist = math.hypot(ax - tx, ay - ty)
        if dist > self.profile.reach_px:
            return None  # physically out of reach
        return dist
```

### 2. `app/agents/agv_agent.py` (rewrite `_bid_score`)
```python
import math
from typing import Optional

class AGVAgent(ActorAgent):
    def _bid_score(self, slot: dict) -> Optional[float]:
        if slot.get("kind") != "agv":
            return None
        from_pos = slot.get("from_pos")
        to_pos = slot.get("to_pos")
        if from_pos is None or to_pos is None:
            # Legacy zone-string slot — fall back to current Manhattan via dock cell.
            return self._legacy_bid_score(slot)
        ax, ay = self.profile.position_px
        fx, fy = float(from_pos[0]), float(from_pos[1])
        tx, ty = float(to_pos[0]), float(to_pos[1])
        approach = math.hypot(ax - fx, ay - fy)
        trip = math.hypot(fx - tx, fy - ty)
        return approach + trip

    def _legacy_bid_score(self, slot: dict) -> Optional[float]:
        # KEEP the existing dock-cell Manhattan path unchanged for Phase 4
        # zone-string tests that don't populate target_pos.
        ...  # existing _bid_score body moved here
```

### 3. `app/agents/negotiation.py` (extend `public_winner`)
- Add `"longest time"`, `"longest distance"`, `"heaviest load"` to the
  scored-constraint set. Negate score for longest variants (or use max
  directly — pick the clearer implementation).
- Heaviest load: rank by `payload.get("capacity", 1)` desc, then by score
  asc.
- Bid payload extension: AGVAgent + ArmAgent include
  `"capacity": self.profile.capacity` and `"reach_px":
  self.profile.reach_px` in the BID payload so peers re-derive winners
  identically from history (pure-function invariant).

### 4. `app/agents/capability.py` (legacy path fills position_px)
- In `load_profiles()` legacy warehouse path: when constructing
  `CapabilityProfile`, populate `position_px = (bay_col * cell_px, bay_row
  * cell_px)` where `cell_px` comes from
  `pipeline_config.get_warehouse_cell_px()` (or falls back to 40). Same for
  arm positions — derive from station dock cell.
- Topology path (Stage 3) already populates position_px via
  `from_topology_spec`. No change.

### 5. `app/routes/negotiate_routes.py` (extend slot derivation)
- When the request has a `topology:` block effectively active
  (`get_topology()` returns something), call
  `derive_slots_by_actor(source_arm_id, dest_arm_id, topology)` instead of
  `derive_slots(source_zone, dest_zone)`. Default arms: first arm in
  topology = source, second arm = dest (or carry hints from the interpreter
  later — for Stage 4 use that default).
- Otherwise keep existing `derive_slots(source_zone, dest_zone)` path.

## Tests

### `tests/test_spatial_bidding.py` (new — THE acceptance gate)
- `test_placement_changes_arm_bid_score` — two arms at different positions;
  same pickup slot at fixed `target_pos`; assert closer arm has lower bid
  (or higher, depending on convention) and farther arm has higher.
- `test_arm_out_of_reach_returns_none` — arm with `reach_px=100`, target at
  distance 200 → bid returns None (no bid placed).
- `test_placement_changes_agv_bid_score` — two AGVs at different positions;
  agv closer to pickup wins shortest-time, farther wins longest-time.
- `test_acceptance_gate_position_dictates_winner` — THE load-bearing test:
  - Build a topology with arm-1 at (200, 200), arm-2 at (1400, 200), agv-1
    at (300, 200), agv-2 at (1300, 200).
  - Run full negotiation; under `shortest time` agv-1 wins.
  - Run again with `longest time`; agv-2 wins.
  - Move agv-1 to (1500, 1500); run `shortest time`; agv-2 now wins.
  - Assert every winner change is reflected in `accepted_actors(history,
    task_id, round)`.
- `test_heaviest_load_constraint_picks_max_capacity` — heterogeneous AGVs
  (cap 1 vs cap 4); constraint "heaviest load"; AGV with cap=4 wins.
- `test_legacy_zone_path_unchanged` — call `_bid_score` with a slot that has
  only `zone`/`from_zone`/`to_zone` (no `target_pos`/`from_pos`); ensure the
  Phase 4 logic still applies (backward-compat).

### `tests/test_public_winner_constraints.py` (new)
- `test_shortest_time_picks_min` — 3 bids, min score wins.
- `test_longest_time_picks_max` — 3 bids, max score wins.
- `test_longest_distance_picks_max` — same with distance metric.
- `test_heaviest_load_breaks_capacity_first` — two bids one cap=4 one
  cap=1; cap=4 wins regardless of score.
- `test_default_constraint_unchanged` — empty constraint → lowest sender id
  tiebreak (Phase 4 behaviour).

### Update existing integration tests
- The Phase 4 `test_negotiation_integration.py` uses zone-string `_announce()`
  — works unchanged via the legacy `_bid_score` path. Verify they still pass.
- Add a NEW integration test
  `tests/test_spatial_negotiation_integration.py` that runs the full ArmAgent
  + AGVAgent loop against a topology-based announce and confirms end-to-end
  spatial selection.

## Hard invariants

- Pure-functional consensus over `bus.history()` unchanged.
- Leaderless `public_winner` rule preserved — every actor computes the same
  winner from the same history.
- BID payload includes `capacity` + `reach_px` so peer re-derivation matches
  the publisher's view.
- Legacy zone-string slot path (Phase 4) keeps working — `_bid_score`
  detects absence of position fields and falls back.
- No new MessageType. No new coordinator. No central scheduler.
- 548 baseline tests pass unchanged.

## Verify

- `python -m pytest tests/ -q` baseline 548 / 5. After: ≥ 548 + ~12 new / 5.
  Zero regressions.
- New tests pass 3× consecutively (especially the acceptance gate).
- `python -c "from app import create_app; create_app()"` constructs.

## Opus review gate

- The acceptance gate test passes — placement provably dictates winners.
- Spatial bid is a pure function of (`profile.position_px`, `slot` positions,
  `reach_px`, `capacity`). No hidden state.
- `public_winner` constraint extensions are deterministic + tie-broken by
  sender id (leaderless invariant).
- Legacy zone-string path is exercised by an explicit test and still works.
- BID payload carries enough info for peer re-derivation.
