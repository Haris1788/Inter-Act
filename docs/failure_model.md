# INTER.act failure and recovery model

This document explains what happens when arms fail, stop, and recover.

## Quick model

There are two different stop paths:

1. Reported failure (`POST /arms/<id>/health` with `health=dead`)
2. Explicit stop (`POST /arms/<id>/stop`)

They do not behave the same way.

## Path A: reported failure (container/runtime failure)

When an arm reports `dead`, the backend marks that arm as failed.  
This path does not force an immediate downstream hard stop.

Downstream behavior is handled by pipeline flow:

- downstream workers can drain buffered work
- then move to stopped states through normal execution checks

This is why you may see a short period where upstream/downstream arms are still transitioning after one arm dies.

## Path B: explicit stop (operator/API action)

`POST /arms/<id>/stop` is an intentional control command.  
This path cascades to all transitive downstream dependents.

Use this when you want deterministic shutdown propagation through the dependency graph.

## Health monitor behavior

The backend health monitor (`app/services/health_monitor.py`) polls each arm service endpoint:

- first missed checks move arm to `unhealthy`
- repeated misses move arm to `dead`

Recovery from monitor polling is conservative:

- monitor can recover `dead` and `stopped` arms
- monitor requires upstream dependencies to be healthy
- monitor does not auto-recover `draining`

## Immediate failure signals from arm service

Arm microservices (`scripts/arm_server.py`) send a direct `dead` update on SIGTERM/SIGINT before exit when possible.  
This cuts detection delay compared with poll-only detection.

## Dependency validity

Dependency graph checks are derived from config and include:

- missing arm references
- cycle detection

If graph validation fails, pipeline behavior is not reliable until config is fixed.

## Operational guidance

- Use `/arms/<id>/stop` for intentional cascaded stop behavior.
- Use health monitor status for liveness, not for full pipeline run control.
- After config edits: call `/reload_config`, then `/restart_pipeline`.
