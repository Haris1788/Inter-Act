# INTER.act failure and recovery model

This document explains runtime behavior when modules fail, stop, drain, and recover.

## Runtime states

Arms and assets use these operational states:

- `idle`
- `busy`
- `draining`
- `failed`
- `recovering`
- `stopped`

`draining` means the module is alive but blocked by downstream flow constraints.

## Failure paths

### Explicit control path

- `POST /arms/<id>/stop` sets an explicit arm stop.
- `POST /assets/<asset_id>/fail` sets asset failure and updates container state.

This path is deterministic and operator-controlled.

### Health-monitor path

`app/services/health_monitor.py` polls arm and asset services:

- missed checks transition modules into failure states
- recovered container responses update backend status
- asset state is synced to container-reported lifecycle (`idle`/`busy`/`draining`/`recovering`/`failed`/`stopped`)

## Recovery path

- `POST /arms/<id>/start` clears explicit stop for arms.
- `POST /assets/<asset_id>/recover` starts the asset container and forwards `/recover` to the asset service when reachable.
- module controllers hold processing while `recovering`, then return to `idle`.

## Flow-control behavior under failure

Flow is backpressure-driven:

- transfer points are single-slot buffers
- modules do not process while `failed` or `recovering`
- blocked downstream places/emit conditions move modules to `draining`
- upstream modules can hold objects in passive buffers until downstream returns

## Ownership integrity during failures

`/object_event` enforces ownership transition validity against live topology, so even during failure/recovery windows, object ownership stays consistent for UI and debugging.

## Operational guidance

- Use explicit stop/fail routes when you need predictable operator control.
- Use health status as liveness signal, not as proof of full throughput.
- After topology edits, run `/reload_config` and `/restart_pipeline`.
