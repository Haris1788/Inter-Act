# INTER.act backend runtime

This file documents how backend state is created, updated, and broadcast.

## Startup flow

Entry point is `run.py`:

1. calls `create_app()` from `app/__init__.py`
2. initializes Flask app + Socket.IO (`threading` async mode)
3. registers route blueprints
4. imports socket handlers (`connect`, `disconnect`)
5. starts health monitor background thread

## Core state owners

Two in-memory state stores are central:

- `app/models/arm_state.py`  
  Arm angle/status and arm health map.

- object state map inside `app/routes/arm_routes.py`  
  Object position/state snapshots used by frontend sync.

Both are lock-protected.

## Backend responsibilities

Backend does four things:

- validates and applies API writes (`/set_angle`, `/object_event`, etc.)
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
- `log_message`

Frontend treats these as live runtime signals and reconciles its local render state.

## Health monitor runtime

`app/services/health_monitor.py` runs as a background thread:

- pings each arm service `/health`
- marks `unhealthy` on misses
- marks `dead` on configured miss threshold
- recovers `dead`/`stopped` to `alive` when arm responds and dependencies permit

It does not auto-recover `draining`.

## Route behavior notes that matter

- `/reload_config` only updates config cache in memory.
- `/restart_pipeline` resets runtime state using already-loaded config.
- `/demo/start|status|stop` are proxy routes to demo-runner.
- `/arms/<id>/stop` is explicit downstream cascade control.
- `/arms/<id>/health` is status reporting path from arm services and demo flow.

## Operational caveats

- `/demo/stop` is best-effort in current implementation, not a guaranteed immediate hard interrupt.
- Health status says liveness, not full production-flow readiness.
- Runtime state is in-memory, so process restarts clear state.
