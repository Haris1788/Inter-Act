# Phase 5 Stage 3 Committed Sub-Spec — Dynamic Actor Placement Runtime

Backend reads the canvas-published `topology:` block. Reconciler builds the
desired actor set from topology. Actor runner loads its CapabilityProfile
from topology by ACTOR_ID. Single track — consistency surface.

## Required behaviour

- `POST /deploy/apply` accepts a new `topology` field (Stage 2D's serializer)
  and writes it into `pipeline.yaml` as the `topology:` block.
- `pipeline_config.reload()` re-parses both `warehouse:` and `topology:`.
- `container_manager.build_desired_from_config()` returns the actor set from
  the topology block when present, else falls back to the existing
  arms+AGVs source. Each desired entry carries `(actor_id, kind, x, y)` so
  the reconciler can pass position via env.
- `scripts/actor_runner.py` reads its profile from
  `get_topology_or_legacy_shim().actor(ACTOR_ID)` when `INTERACT_BUS=redis`
  (it does today, so this is just the lookup path). Backward-compat: if no
  topology, falls back to current `capability.load_profiles()` filter.
- `capability.load_profiles()` is extended to ALSO build profiles from
  topology when present (uses `CapabilityProfile.from_topology_spec`); falls
  back to existing arms+AGVs path when only `warehouse:` present.

## Files

### 1. `app/services/deploy_bridge.py` (extend)
- Existing `build_pipeline_yaml(doc, canvas_meta=None)` writes the yaml.
- Read `doc.get("topology")` and serialize it as a `topology:` block in the
  output yaml when present. Shape matches Stage 1 parser:
  ```yaml
  topology:
    canvas: {width, height, cell_px}
    actors:
      - {id, kind, x, y, reach_px, capacity, speed, label?, color?}
  ```
- Existing `warehouse:` / `arms:` / `assets:` blocks remain unchanged in the
  output — coexistence is intentional for the Stage 1-3 transition.

### 2. `app/services/container_manager.py` (extend)
- `build_desired_from_config()` checks `pipeline_config.get_topology()`.
  - If present: `desired = [{actor_id, kind, x, y, reach_px, capacity,
    speed} for actor in topology.actors]`.
  - Else: existing path (`get_arms()` + `get_agvs()`).
- `create_actor(actor_id, kind, ...)` passes new env to actor containers:
  `ACTOR_X`, `ACTOR_Y`, `ACTOR_REACH_PX`, `ACTOR_CAPACITY`, `ACTOR_SPEED`
  (all optional — actor_runner falls back to topology lookup when env
  absent).

### 3. `scripts/actor_runner.py` (extend)
- `_load_profile(actor_id, logger)` priority:
  1. If env has `ACTOR_X`/`ACTOR_Y`, build a `CapabilityProfile` directly
     from env (fastest, no yaml read).
  2. Else call `get_topology_or_legacy_shim()` and look up by `actor_id` —
     returns a profile via `from_topology_spec(...)`.
  3. Else fall back to `capability.load_profiles()` + filter (legacy).

### 4. `app/agents/capability.py` (extend)
- `load_profiles()` checks `pipeline_config.get_topology()`.
  - If present: build one `CapabilityProfile` per topology actor via
    `from_topology_spec(...)`. `reachable_zones` for arms = `[actor_id]` so
    Phase 4's zone-match `_bid_score` still pins by served-by.
  - Else: existing path (arms with served-by zones + AGVs with all-station
    zones).

### 5. Tests

#### `tests/test_deploy_topology_payload.py` (new)
- `test_deploy_writes_topology_block` — POST a deploy with `topology` field;
  read written `pipeline.yaml`; assert the topology block round-trips.
- `test_deploy_omits_topology_when_absent` — POST without topology; yaml has
  no `topology:` key (warehouse-only path).
- `test_reload_picks_up_new_topology` — write yaml, reload, observe
  `get_topology()` reflects it.

#### `tests/test_build_desired_topology.py` (new)
- `test_build_desired_uses_topology_when_present` — fixture with topology;
  desired list = topology actors with kind/id/position.
- `test_build_desired_falls_back_to_warehouse_when_no_topology` — legacy
  fixture; desired list = arms + AGVs (current behaviour).

#### `tests/test_actor_runner_topology.py` (new)
- `test_load_profile_from_env_x_y` — `_load_profile` honours `ACTOR_X` +
  `ACTOR_Y` env.
- `test_load_profile_from_topology` — env absent, topology present; profile
  built from topology.
- `test_load_profile_legacy_fallback` — no env, no topology; current path
  works.

#### `tests/test_capability_topology.py` (new)
- `test_load_profiles_uses_topology_when_present`.
- `test_load_profiles_falls_back_to_warehouse`.

## Hard invariants

- Pure plumbing. No spatial bid logic change yet (Stage 4).
- Backward-compat: every Phase 4 test + the 535 Phase 5 Stage 1 baseline
  pass unchanged.
- Topology overrides warehouse when both present (Stage 1 decision).
- `pipeline.yaml` shape allows `topology:` AND `warehouse:` to coexist
  during Stage 1-3 transition. Phase 5 final may deprecate warehouse, not
  Stage 3.
- No backend HTTP change beyond `/deploy/apply` accepting one more field.

## Verify

- `python -m pytest tests/ -q` baseline 535 pass / 5 pre-existing fail.
  After: ≥ 535 + ~10 new pass / 5 fail. Zero regressions.
- New tests pass 3× consecutively.
- `python -c "from app import create_app; create_app()"` constructs.
- Manual: POST a deploy with topology via curl; `cat pipeline.yaml` shows
  the new block.

## Opus review gate

- Topology block is correctly serialized in `pipeline.yaml`.
- Reconciler `desired` set is identical when topology vs warehouse define the
  same actors (legacy shim parity).
- Actor runner profile resolution priority is correct (env → topology →
  legacy) — no silent fall-through bug that ignores newer config.
- `load_profiles()` topology path preserves `reachable_zones=[actor_id]` so
  Phase 4 zone-match still works on arm slots whose `zone == arm.id`.
- No spatial bidding change leaks into Stage 3 (Stage 4's territory).
