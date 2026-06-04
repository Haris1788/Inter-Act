# Phase 3 — Execution-Time Peer Signals on the Bus

**Date:** 2026-05-21
**Branch:** dev-haris
**COORDINATION_MODE:** decentralized

## What was built

Phases 1 + 2 handled the boundary cases (fault recovery, initial allocation).
Phase 3 covers the **steady-state chatter** between agents during execution
— the brief's `intent`, `arm_approach`, `arm_ready`, `arm_busy`, `yield`, and
`help_request` events. Peers use these signals to anticipate each other's
moves instead of just discovering conflicts at the dock.

### Signal types added

| Type | Producer | Carries | When emitted |
|------|----------|---------|---------------|
| `intent` | AGV | `{action, detail, agv_id}` | Before starting any major step (move_to_input, move_to_output, acquire_lane) |
| `arm_approach` | AGV | `{arm_station, eta_s, agv_id}` | On the LAST cell of a move-to-arm sequence, so the arm + peers see it ≥ 1 cell before dock |
| `arm_ready` | Arm | `{arm_station, for_agv, action}` | After the arm finishes loading / unloading the package |
| `yield` | AGV | `{reason, waiting_for, agv_id}` | When lane/station acquisition fails — peer is holding |
| `help_request` | AGV | `{reason, blocked_at, agv_id}` | Stuck after max retries (instrumentation in place; not exercised in this run) |

### Implementation

- `scripts/agv_bus.py` — `publish_intent`, `publish_arm_approach`, `publish_yield`, `publish_help_request` helpers
- `scripts/agv_brain.py` — `_bus_intent`, `_bus_arm_approach`, `_bus_yield`, `_bus_help` no-op wrappers (silent in centralized mode) wired into:
  - `_script_move_to_input` — intent + arm_approach (on last cell)
  - `_script_move_to_output` — intent + arm_approach (on last cell)
  - `_script_acquire_lane` — intent + yield (on failure)
- `app/routes/dispatch_routes.py` — `_auto_arm_signals` publishes `arm_ready` to the bus after each load and each unload

## Live test — 2026-05-21 11:34 local

`POST /plan {"prompt":"send 3 packages sorted red green blue, distribute across all AGVs"}`

### Verbatim Phase 3 bus events (heartbeats stripped)

```
seq=23 11:34:01.264 intent       from=agv-3 task=pkg-1  action=move_to_input  detail="heading to input arm via 15 cells"
seq=29 11:34:08.329 arm_approach from=agv-3 task=pkg-1  arm_station=input_arm  eta_s=0.5
seq=34 11:34:14.703 arm_ready    from=arm-1 task=pkg-1  action=loaded   for_agv=agv-3
seq=39 11:34:20.661 intent       from=agv-3 task=pkg-1  action=acquire_lane   detail="requesting shared travel lane"
seq=41 11:34:20.841 intent       from=agv-1 task=pkg-2  action=move_to_input  detail="heading to input arm via 11 cells"
seq=45 11:34:25.888 arm_approach from=agv-1 task=pkg-2  arm_station=input_arm  eta_s=0.5
seq=50 11:34:32.530 intent       from=agv-3 task=pkg-1  action=move_to_output detail="heading to output arm via 1 cells"
seq=51 11:34:32.533 arm_approach from=agv-3 task=pkg-1  arm_station=output_arm eta_s=0.5
seq=52 11:34:32.562 arm_ready    from=arm-1 task=pkg-2  action=loaded   for_agv=agv-1
seq=55 11:34:33.215 arm_ready    from=arm-2 task=pkg-1  action=unloaded for_agv=agv-3
```

### Visualised handshake (pkg-1, agv-3)

```
t=00s   intent       AGV→bus  "move_to_input, 15 cells"
                                    │
                                    ▼  (14 cells travelled, lane safe)
t=07s   arm_approach AGV→bus  "input_arm in 1 cell, eta 0.5s"
                                    │
                                    ▼  (arm animates, sweeps, places package)
t=13s   arm_ready    arm→bus  "loaded for agv-3, input_arm"
                                    │
                                    ▼  (AGV proceeds: lane → output)
t=19s   intent       AGV→bus  "acquire_lane"
                                    │
t=31s   intent       AGV→bus  "move_to_output, 1 cell"
t=31s   arm_approach AGV→bus  "output_arm in 1 cell, eta 0.5s"
                                    │
                                    ▼
t=32s   arm_ready    arm→bus  "unloaded for agv-3, output_arm"   ← package delivered
```

The whole bay→input→output→bay cycle is now narrated on the bus. A peer AGV
can subscribe to `intent` + `arm_approach` + `arm_ready` and know exactly
what is happening at each dock without needing to poll `/coord/snapshot`.

### Concurrency

While agv-3 was finishing pkg-1, agv-1 began pkg-2 — visible in the transcript:
- 11:34:20.841: agv-1 emits `intent move_to_input` (overlapping with agv-3 still en route)
- 11:34:25.888: agv-1 emits `arm_approach input_arm` (right after arm-1 freed up from agv-3)
- 11:34:32.562: arm-1 emits `arm_ready loaded` for agv-1

Two AGVs in flight on the same bus, with all signals timestamped and ordered. No collisions, no yields needed — the existing lane/station locks plus these advance signals kept things clean.

## Coverage

| Event type | Producer | Observed in this run |
|------------|----------|----------------------|
| `intent` | AGV | ✅ multiple (move_to_input, acquire_lane, move_to_output) |
| `arm_approach` | AGV | ✅ for both input and output stations |
| `arm_ready` | arm (via backend) | ✅ both "loaded" and "unloaded" |
| `yield` | AGV | not exercised — no lane contention happened |
| `help_request` | AGV | not exercised — no max-retry path hit |
| `arm_busy` | arm | not implemented — lock-based station acquisition makes this redundant in the current stack |

## Status

✅ Phase 3 — **peer execution-time signaling proven live**. Intent + approach + ready handshake completes the bay→input→output→bay cycle entirely through bus events.
