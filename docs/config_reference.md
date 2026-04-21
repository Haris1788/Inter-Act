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
- In UI-triggered runs, frontend currently sends fixed start payload (`products: 3`, `speed: "normal"`), so YAML values here may not drive button-start behavior unless UI is changed.

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

## `assets` (frontend-only extension layer)

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
- does not run backend lifecycle logic for non-arm assets

## Validation behavior

Dependency graph checks include:

- missing dependency references
- cycle detection

Topological order is exposed via backend status route.

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
