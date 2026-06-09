# Phase 1 — AGV Capability Schema (M1)

**Status:** ✅ Verified live on 29 May 2026.

## What changed

Added a shared per-AGV capability declaration that both the in-process backend
path (`VehicleController` / `agv_state`) and the containerized AGV brain
(`scripts/agv_*.py`) read from. No inheritance refactor — the schema is data,
both code paths read it the same way.

## What I implemented

### New file: `app/models/agv_capabilities.py`
- `AGVCapabilities` dataclass: `agv_id, kind, weight_max_kg, allowed_labels, speed_factor, bay_cell`.
- `accepts_label(label)` helper — returns `True` if `label` is in `allowed_labels` or the AGV's labels include `"any"`.
- `from_env(agv_id)` — reads AGV-side env vars (`AGV_KIND, AGV_WEIGHT_MAX_KG, AGV_ALLOWED_LABELS, AGV_SPEED_FACTOR`).
- `from_config(agv_cfg, ai_defaults)` — reads backend-side pipeline.yaml (per-AGV `capabilities` block, falling back to `ai.capability_defaults`).
- `to_env_dict(caps)` — serialises to env-var pairs for docker-compose generation.

### `pipeline.yaml`
- Added `capabilities` block per AGV (red+green+blue+any for the demo).
- Added `ai.capability_defaults` for AGVs that omit their own block.
- Added `ai.bid_weights` (distance/capability/load) for Phase 2.
- Added a third `ai.coordination_mode` value: **`smart`** = US 32 contract-net + US 34 features.

### `app/models/pipeline_config.py`
- `get_ai_config()`, `get_coordination_mode()`, `coordination_mode_is_decentralized()`, `coordination_mode_is_smart()`.
- `get_capability_defaults()`, `get_bid_weights()`.
- `get_agv_capabilities(agv_id)` and `get_all_agv_capabilities()` returning `AGVCapabilities` instances.

### `app/models/agv_state.py`
- `register()` now accepts an optional `capabilities` dict.
- `initialize_from_config()` reads capabilities from pipeline_config and stores them.
- New helpers `set_capabilities()` / `get_capabilities()`.

### `app/routes/agv_routes.py`
- `/coord/snapshot` now includes `capabilities` per AGV.
- `/agv/state/<id>` already returns the full record (capabilities included via the agv_state shape).

### `scripts/generate_compose.py`
- Per-AGV `environment:` block now includes the four capability env vars derived from `pipeline.yaml`.

### `scripts/agv_server.py`
- Module-level `CAPABILITIES` dict built from env vars at boot (mirrors `from_env` without importing `app/`).
- New helpers `_mode_is_decentralized()` and `_mode_is_smart()`.
- `/status` now surfaces `capabilities` + `coordination_mode`.
- New `/capabilities` endpoint returning the AGV's declaration.
- BusListener startup gate widened from `== "decentralized"` to `in ("decentralized", "smart")`.

### `scripts/agv_brain.py`
- All 7 occurrences of `COORDINATION_MODE == "decentralized"` / `!= "decentralized"` updated to handle `"smart"` as a decentralized-family value.

### `app/routes/ai_routes.py`
- `/plan` routing gate widened: smart-mode plans also go through `MasterAnnouncer.announce_plan`.

## Features

- **Per-AGV capability declaration** in `pipeline.yaml`, propagated automatically to docker-compose, the AGV containers, and the backend's `agv_state`.
- **Both paths read the same schema** — backend (from yaml) and container (from env). Single source of truth at the yaml level.
- **Feature flag extended to a 3-value enum** without inventing a parallel mechanism: `centralized | decentralized | smart`. US 32 fallback (`decentralized`) remains identical.
- **`accepts_label()` helper** ready for Phase 3 master-side filtering.

## Capabilities

| Field | Type | Default | Notes |
|---|---|---|---|
| `kind` | str | `shuttle` | Free-text; could later drive bid weighting per kind. |
| `weight_max_kg` | float | `50.0` | Used by Phase 2 capability_match scoring. |
| `allowed_labels` | list[str] | `["any"]` | `"any"` matches everything; otherwise case-insensitive substring match. |
| `speed_factor` | float | `1.0` | Multiplier on `MOVE_STEP_SLEEP` for future use. |
| `bay_cell` | list[int] | from `warehouse.agvs[].bay` | Position field, surfaced for completeness. |

## Live test results (29 May 2026 evening)

```
$ curl -s http://localhost:8080/agv/state/agv-1
{
  "id": "agv-1", "status": "idle",
  "capabilities": {
    "agv_id": "agv-1", "kind": "shuttle",
    "allowed_labels": ["red", "green", "blue", "any"],
    "speed_factor": 1.0, "weight_max_kg": 50.0,
    "bay_cell": [10, 3]
  }, ...
}

$ curl -s http://localhost:8101/status
{
  "agv_id": "agv-1", "coordination_mode": "smart",
  "task_status": "idle",
  "capabilities": {
    "agv_id": "agv-1", "kind": "shuttle",
    "allowed_labels": ["red", "green", "blue", "any"],
    "speed_factor": 1.0, "weight_max_kg": 50.0
  }
}

$ docker logs interact-agv-1 | grep -E "smart|capabilities"
COORDINATION_MODE: smart
[agv-1] smart (US 34) BusListener started
[agv-1] capabilities: {'agv_id': 'agv-1', 'kind': 'shuttle',
    'weight_max_kg': 50.0, 'allowed_labels': ['red', 'green', 'blue', 'any'],
    'speed_factor': 1.0}
```

All three checkpoints satisfied: capabilities surface on `/agv/state/<id>`, on
`/coord/snapshot`, and on the container's `/status`. BusListener boots under
`smart` mode (US 34 features now enabled for downstream phases).

## US 32 fallback verified

Setting `COORDINATION_MODE=decentralized` (instead of `smart`) bypasses every
US 34 code path added in this phase:

- AGV registers capabilities anyway (harmless data; they're just never read).
- BusListener still starts (decentralized family).
- Bidding (Phase 2 onwards) checks the smart flag before computing the
  deterministic score; under `decentralized` the original flat-AI score path
  is used unchanged.

## Limitations / open items

- No backend-side AGV registration **from the AGV container** — capabilities
  flow from `pipeline.yaml` directly into backend `agv_state` via
  `initialize_from_config`. If a container's env disagrees with `pipeline.yaml`,
  the backend uses the yaml value. This is intentional (yaml = source of
  truth) but worth flagging for the team.
- `kind` is free-text; later phases could whitelist a kind enum.
- `speed_factor` exists but no consumer reads it yet — that's part of Phase 2
  (composite bid) or a follow-up.
- The `/capabilities` *backend* endpoint is Phase 5; this phase only exposed
  it on the AGV container.

## Next phase

Phase 2 (M2) — deterministic composite bid in `scripts/agv_bus.py:_ask_for_bid`.
The pre-conditions are all in place:

- Container has `CAPABILITIES` dict and `_mode_is_smart()` available.
- Backend exposes capabilities via `/coord/snapshot` so AGVs can read peer caps too.
- `pipeline.yaml:ai.bid_weights` carries the tunable weights.
