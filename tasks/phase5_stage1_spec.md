# Phase 5 Stage 1 Committed Sub-Spec — Spatial Topology Model

Foundation for the canvas-as-truth shift. Backend gains a first-class
`Topology` dataclass with actor positions in pixels + reach radius. Single
track — no parallel. Strict backward-compat: every Phase 4 test must still
pass without modification.

## Decisions (approved)

- Coordinate units: **canvas px, backend coarse-grid snap (`cell_px=40`)**.
- `topology:` block overrides `warehouse:` block when both present.

## Required behaviour

- `Topology` dataclass is the single source of spatial truth.
- `pipeline.yaml` accepts a NEW optional `topology:` block alongside the
  existing `warehouse:` block. When both present, topology wins.
- `pipeline_config.get_topology() -> Topology | None` returns the parsed
  topology or `None` when only the legacy `warehouse:` block is present.
- A compatibility shim builds a synthetic Topology from the legacy
  `warehouse:` block when topology is absent, so downstream code can always
  ask for a Topology — but the shim is opt-in (call
  `get_topology_or_legacy_shim()`), so the default Phase 4 behaviour is
  untouched until Stage 3 wires it in.
- `CapabilityProfile` gains: `position_px: (float, float)`, `reach_px:
  float`, `capacity`, `speed` (already there).
- `derive_slots(...)` gains a NEW signature
  `derive_slots_by_actor(source_arm_id, dest_arm_id, topology)` returning 3
  slots with `target_pos`/`from_pos`/`to_pos` populated from the Topology.
  The current `derive_slots(source_zone, dest_zone)` keeps working
  unchanged — it remains the zone-name path for backward compat.

## Files

### 1. `app/models/topology.py` (new)

```python
from dataclasses import dataclass, field
from typing import Literal, Optional

@dataclass(frozen=True)
class ActorSpec:
    id: str                                 # e.g. "arm-1", "agv-1"
    kind: Literal["arm", "agv"]
    x: float                                # canvas px
    y: float                                # canvas px
    reach_px: float = 120.0                 # arms only meaningful for arms; AGVs ignore
    capacity: int = 1
    speed: float = 1.0
    label: Optional[str] = None
    color: Optional[str] = None

@dataclass(frozen=True)
class Topology:
    actors: tuple[ActorSpec, ...]
    canvas_width: int = 1600
    canvas_height: int = 1000
    cell_px: int = 40                       # backend coarse-grid snap unit

    def actors_by_id(self) -> dict[str, ActorSpec]: ...
    def arms(self) -> tuple[ActorSpec, ...]: ...
    def agvs(self) -> tuple[ActorSpec, ...]: ...
    def cell_for(self, x: float, y: float) -> tuple[int, int]:
        """Snap (x,y) px to (col,row) cell using cell_px."""
        return (int(x // self.cell_px), int(y // self.cell_px))
    def actor(self, actor_id: str) -> Optional[ActorSpec]: ...

def parse_topology(raw: dict) -> Topology:
    """Build a Topology from a parsed YAML topology block.

    Tolerant of missing fields:
      raw = {
        "canvas": {"width": ..., "height": ..., "cell_px": ...},
        "actors": [
          {"id": "arm-1", "kind": "arm", "x": 120, "y": 300, "reach_px": 140},
          {"id": "agv-1", "kind": "agv", "x": 800, "y": 600, "capacity": 1},
          ...
        ]
      }
    """
    ...
```

Pure dataclasses + parser. No I/O, no side effects.

### 2. `app/models/pipeline_config.py` (extend)

Add (do NOT remove the existing `get_stations`/`get_lane_cells`/`get_agvs`
functions — Stage 4/5 phase those out):

- `get_topology() -> Optional[Topology]` — return parsed `topology:` block or
  None.
- `get_topology_or_legacy_shim() -> Topology` — return real topology if
  present; otherwise synthesize one from the legacy `warehouse:` block
  (stations become arm actors at their dock cell × cell_px; AGVs use their
  bay cell × cell_px; arms get a default `reach_px=120`). Idempotent + pure.
- Reload (`pipeline_config.reload()`) must re-parse both blocks.

The legacy shim is essential: it lets Stage 3+ unconditionally call
`get_topology_or_legacy_shim()` without forcing every test fixture to add a
`topology:` block.

### 3. `app/agents/capability.py` (extend)

Extend `CapabilityProfile` (DON'T break the dataclass — add fields with
defaults so existing constructors still work):

```python
@dataclass
class CapabilityProfile:
    actor_id: str
    kind: Literal["arm", "agv"]
    reachable_zones: list[str] = field(default_factory=list)  # KEEP for Stage 1
    position: list[float] = field(default_factory=lambda: [0.0, 0.0])  # KEEP
    speed: float = 1.0
    capacity: int = 1
    # NEW Stage 1 fields:
    position_px: tuple[float, float] = (0.0, 0.0)
    reach_px: float = 120.0
```

Add `from_topology_spec(spec: ActorSpec) -> CapabilityProfile` constructor.

`load_profiles()` is unchanged in Stage 1 (Stage 3 wires it to topology).

### 4. `app/agents/negotiation.py` (extend)

Add new helper, keep existing `derive_slots`:

```python
@dataclass
class Slot:
    slot_id: str
    kind: Literal["arm", "agv"]
    # Existing zone-string fields (KEEP):
    zone: Optional[str] = None
    from_zone: Optional[str] = None
    to_zone: Optional[str] = None
    # NEW Phase 5 spatial fields (optional for back-compat):
    target_pos: Optional[tuple[float, float]] = None
    from_pos: Optional[tuple[float, float]] = None
    to_pos: Optional[tuple[float, float]] = None
    served_by: Optional[str] = None          # arm slots only — pins the actor
    constraint_tag: Optional[str] = None     # forwarded from announce constraint

def derive_slots(source_zone: str, dest_zone: str) -> list[Slot]:
    """UNCHANGED — Phase 1–4 zone-based decomposition."""
    ...

def derive_slots_by_actor(
    source_arm_id: str, dest_arm_id: str, topology: Topology
) -> list[Slot]:
    """Phase 5 spatial decomposition. Returns the same 3 slots
    (pickup/transport/place) but populates positions from the Topology."""
    src = topology.actor(source_arm_id)
    dst = topology.actor(dest_arm_id)
    if src is None or dst is None:
        raise ValueError(f"unknown arm in topology: {source_arm_id} / {dest_arm_id}")
    if src.kind != "arm" or dst.kind != "arm":
        raise ValueError("source and dest must be arm actors")
    src_pos = (src.x, src.y)
    dst_pos = (dst.x, dst.y)
    return [
        Slot(slot_id="pickup", kind="arm", served_by=src.id,
             target_pos=src_pos, zone=src.id),
        Slot(slot_id="transport", kind="agv",
             from_pos=src_pos, to_pos=dst_pos,
             from_zone=src.id, to_zone=dst.id),
        Slot(slot_id="place", kind="arm", served_by=dst.id,
             target_pos=dst_pos, zone=dst.id),
    ]
```

Why preserve `zone=src.id` on arm slots: Phase 4 `ArmAgent._bid_score` reads
`slot["zone"]` for the zone-match check. Setting `zone` to the actor's own ID
makes the pre-Phase-5 logic naturally pin a slot to its served-by arm (an arm
"reaches" its own zone-id). Stage 4 will replace this with the
position+reach check.

### 5. Tests

#### `tests/test_topology.py` (new)
- `test_actor_spec_round_trip` — dataclass construction.
- `test_parse_topology_full` — parse a typical block; assert canvas dims +
  actor list correctness; cell_for snap.
- `test_parse_topology_tolerates_missing_fields` — minimal actor dict
  (just id, kind, x, y) parses with defaults.
- `test_topology_arms_agvs_filters` — `arms()` and `agvs()` return only
  matching kinds.
- `test_actor_lookup` — `actor(id)` returns the right spec; missing returns
  None.

#### `tests/test_pipeline_config_topology.py` (new)
- `test_get_topology_returns_none_when_only_warehouse` — legacy fixture.
- `test_get_topology_returns_parsed_when_present` — synthetic
  `pipeline.yaml` with a `topology:` block.
- `test_legacy_shim_synthesizes_topology_from_warehouse` — call
  `get_topology_or_legacy_shim()` against a warehouse-only fixture, assert
  arms + AGVs become ActorSpecs at their dock/bay × cell_px positions.
- `test_topology_overrides_warehouse_when_both_present` — both blocks
  present, topology wins.
- `test_reload_picks_up_topology_change` — write a config, reload, observe
  new positions.

#### `tests/test_derive_slots_spatial.py` (new)
- `test_derive_slots_by_actor_populates_positions` — assert
  `target_pos`/`from_pos`/`to_pos` from a known Topology.
- `test_derive_slots_by_actor_pins_served_by` — pickup `served_by ==
  source_arm_id`.
- `test_derive_slots_by_actor_raises_on_unknown_actor` — missing arm ID.
- `test_derive_slots_by_actor_raises_on_non_arm_endpoints` — passing an AGV
  id as source.
- `test_derive_slots_zone_signature_unchanged` — call the existing
  `derive_slots(source_zone, dest_zone)`; assert Phase 4 shape preserved
  byte-for-byte.

## Hard invariants

- Every existing Phase 4 test passes UNCHANGED (520 baseline).
- `Slot` dataclass remains backward-compatible — new fields are optional with
  defaults; existing constructors `Slot(slot_id=..., kind=..., zone=...)`
  still work.
- `CapabilityProfile` remains backward-compatible — new fields optional with
  defaults.
- `pipeline_config.get_stations()` / `get_lane_cells()` / `get_agvs()` are
  UNCHANGED — Stage 5 phases them out.
- No I/O in `topology.py`. Pure dataclass + parser.
- No backend behaviour change yet. Stage 1 is plumbing only; Stage 4 makes
  it load-bearing.

## Verify

- `python -m pytest tests/ -q` baseline 520 pass / 5 pre-existing fail.
  After Stage 1: ≥ 520 + N new pass / 5 fail. Zero regressions.
- New topology + pipeline_config + derive_slots tests pass.
- `python -c "from app import create_app; create_app()"` constructs in both
  bus modes (inprocess + redis env).

## Opus review gate

- Topology dataclass is frozen + has no I/O — pure data.
- Legacy shim correctness: warehouse → topology round-trip preserves all
  actor identities; positions land at the right pixel coordinates given the
  warehouse `cell_px` (matches `pipeline_config.cell_to_px` math).
- `derive_slots_by_actor` populates positions consistently — `target_pos ==
  arm.(x,y)`; `from_pos == source.(x,y)`; `to_pos == dest.(x,y)`.
- `Slot` dataclass remains a tagged sum (zone-string path OR position path)
  with no field collisions.
- `CapabilityProfile` keeps every Phase 1–4 caller working.
