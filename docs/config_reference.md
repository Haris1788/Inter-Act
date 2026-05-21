# INTER.act config reference

Primary config file is `pipeline.yaml`.

This document lists what each section controls in the current code.

## Top-level keys

- `pipeline`
- `failure_policy`
- `health`
- `arms`
- `assets`

## `pipeline`

Fields:

- `mode`: `sequential | parallel | dependency`
- `products`: integer
- `speed`: `fast | normal | slow`

Notes:

- `mode` is used by pipeline execution logic.
- UI-triggered runs currently send their own `products` and `speed` payload. YAML values still matter for non-UI and scripted runs.

## `failure_policy`

Fields:

- `default`: `stop | skip | retry | isolate`
- `retry_max`: integer
- `retry_delay`: seconds

Behavior:

- Used as fallback policy source.
- Per-arm override can be provided via `arms[].failure_policy`.

## `health`

Fields:

- `check_interval`: poll interval seconds
- `timeout`: per-health-request timeout
- `max_missed`: missed checks before dead transition

Used by backend health monitor loop.

## `arms`

Required fields per arm:

- `id` (int)

Common fields:

- `label` (string)
- `color` (hex string)
- `depends_on` (list of arm IDs)
- `critical` (bool)
- `pivot_x` (int, optional)
- `pivot_y` (int, optional)
- `length` (int, optional)
- `failure_policy` (optional per-arm override)

Behavior:

- defines arm count and arm identities
- drives dependency graph validation and ordering
- drives scene generation for arm geometry
- drives compose generation for `arm-*` services

## `assets`

Supported `type` values currently:

- `vehicle`
- `conveyor`

Shared fields:

- `id`, `type`, `label`, `color`, `upstream`, `downstream`

### Vehicle fields

- `waypoints`: list of `{x, y}`
- `speed`: number (px/sec)
- `loop`: bool

### Conveyor fields

- `from`: `{x, y}`
- `to`: `{x, y}`
- `slot_count`: int
- `shift_interval`: seconds

Backend role:

- passes normalized asset geometry in `/config`
- stores asset state in `asset_state` registry
- health monitor polls asset containers and syncs lifecycle status
- asset services use this topology for runtime flow

## Validation behavior

Validation checks include:

- missing references in arm dependencies
- cycle detection
- asset graph validity checks exposed via `/config` and `/pipeline/status`

Derived order:

- `module_flow_order` is generated and exposed in `/config`
- demo-runner uses this order to wire transfer points and HTTP bridges

## Change application workflow

After editing `pipeline.yaml`:

```bash
curl -X POST http://localhost:5000/reload_config
curl -X POST http://localhost:5000/restart_pipeline
```

If arm count changed in Docker mode:

```bash
python scripts/generate_compose.py
docker compose up --build
```
