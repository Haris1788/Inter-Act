# INTER.act backend runtime

This file documents how backend state is created, updated, and broadcast.

## Startup flow

Entry point is `run.py`:

1. calls `create_app()` from `app/__init__.py`
2. initializes Flask app + Socket.IO (`threading` mode)
3. registers route blueprints
4. starts health monitor background thread

## Core state owners

Two in-memory state stores are central:

- `app/models/arm_state.py`  
  Arm angle/status and arm health map.

- object store inside `app/routes/arm_routes.py`  
  Object state, version, owner, and event sequence for ownership-driven rendering.

Both are lock-protected.

## Backend responsibilities

Backend does four things:

- validates and applies API writes (`/set_angle`, `/object_event`, arm/asset control routes)
- stores current runtime state
- emits Socket.IO events
- proxies demo lifecycle routes to demo-runner

Backend does not execute pipeline movement logic directly in this architecture.

## Socket.IO event contract

Backend emits:

- `state_update`
- `object_update`
- `objects_reset`
- `arm_health`
- `asset_health`
- `log_message`

Frontend treats these as live runtime signals and reconciles its local render state.

## Health monitor runtime

`app/services/health_monitor.py` runs as a background thread:

- pings each arm service `/health`
- pings each asset service `/health`
- updates arm/asset liveness state
- emits `arm_health`/`asset_health` and state log events
- synchronizes asset registry state with container-reported state (`idle`, `busy`, `draining`, `recovering`, `failed`, `stopped`)

## Object-event pipeline behavior

`/object_event` in `app/routes/arm_routes.py` now enforces:

- owner field presence (`owner_type`, `owner_id`)
- owner existence in current topology
- legal owner transitions
- stale-event rejection using event timestamps and monotonic sequence

This keeps backend object state consistent with ownership-driven frontend rendering.

## Route behavior notes

- `/reload_config` updates config cache only.
- `/restart_pipeline` resets runtime state from already-loaded config.
- `/demo/start|status|stop` proxy to demo-runner.
- `/arms/<id>/stop` is explicit stop control.
- `/arms/<id>/start` clears explicit stop state.
- `/assets/<asset_id>/fail` and `/assets/<asset_id>/recover` control asset lifecycle in backend + container.

## Operational caveats

- `/demo/stop` is best-effort in current implementation, not a guaranteed immediate hard interrupt.
- Health status is liveness-oriented, not a full end-to-end throughput guarantee.
- Runtime state is in-memory, so process restarts clear state.
