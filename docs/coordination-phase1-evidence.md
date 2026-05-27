# Phase 1 — Decentralized Fault Recovery via Contract-Net

**Date:** 2026-05-21
**Branch:** dev-haris
**COORDINATION_MODE:** decentralized
**TUC GPU tunnel:** active (qwq:32b + qwen2.5:14b-instruct via http://host.docker.internal:11500)

## What was built

A peer-to-peer fault recovery protocol on top of the existing INTER.act stack.
No central decision-maker — when an AGV faults, the surviving AGVs handle the
takeover entirely among themselves through a bus they all share.

### Components

| File | Role |
|------|------|
| `app/routes/bus_routes.py` | HTTP bus (`POST /bus/publish`, `GET /bus/events`, `GET /bus/recent`) + ring buffer + lifecycle-aware idempotency on `task_orphan` |
| `scripts/agv_bus.py` | Per-AGV `BusListener` — three threads: bus polling, peer heartbeat watch, self-heartbeat publish |
| `scripts/agv_server.py` | Wires `BusListener` into each AGV container; exposes `_is_busy`, `_take_task`, `_current_state` hooks |
| `scripts/agv_brain.py` | Publishes `agent_status` at task start + on completion; calls `publish_task_orphan` on self-detected fault |
| `app/agents/central_brain.py` | `_on_agv_fault` only bound when `COORDINATION_MODE=centralized` — silent in decentralized runs |
| `app/__init__.py`, `scripts/generate_compose.py`, `pipeline.yaml` | Plumbing for the env-var feature flag |

### Bus envelope (per the brief)
```
{seq, ts, from, type, task_id, payload}
```

### Event types in this phase
- `agent_status` — heartbeat-with-task (every 3 s while running + on transition)
- `task_orphan` — published by the first AGV to detect a peer gone silent ≥ 8 s
- `bid` — `{score, rationale, eta_s}` — qwen14b tactical brain decides
- `claim` — announcer picks max-score winner after 20 s bid window

### Tunables (`scripts/agv_bus.py`)
- `BUS_POLL_INTERVAL = 1.0 s`
- `SNAPSHOT_POLL_INTERVAL = 2.0 s`
- `HEARTBEAT_INTERVAL_S = 3.0 s`
- `PEER_STALE_S = 8.0 s` (brief said > 3 s; widened to absorb poll jitter)
- `BID_WINDOW_S = 20.0 s` (brief said 2.5 s; widened because qwen14b on the shared TUC GPU answers in 8-15 s)

## Live test — 2026-05-21 11:13 local

1. `docker compose up -d --build` — all 7 containers healthy
2. `POST /plan {"prompt":"send 3 packages sorted red green blue"}` — planner returned 1 assignment (agv-1 → pkg-1)
3. agv-1 began running pkg-1 at 11:13:23 and emitted periodic heartbeat (seq 25-28)
4. `docker stop -t 0 interact-agv-1` at **11:13:32**
5. Waited ~30 s for the protocol to fire — no manual intervention

### Verbatim bus transcript
```
seq= 25 11:13:23.337 agent_status   from=agv-1  task=pkg-1  status=running  (heartbeat)
seq= 26 11:13:23.691 agent_status   from=agv-1  task=pkg-1  status=running
seq= 27 11:13:26.693 agent_status   from=agv-1  task=pkg-1  status=running
seq= 28 11:13:29.697 agent_status   from=agv-1  task=pkg-1  status=running  ← last heartbeat before kill

                                    ─── KILL: docker stop interact-agv-1 ───

seq= 29 11:13:38.550 task_orphan    from=agv-3  task=pkg-1  reason="agv-1 silent 8.3s (heartbeat timeout)"
seq= 30 11:13:48.323 bid            from=agv-2  task=pkg-1  score=85.0  eta_s=2.5  rationale="Close to pkg-1 and idle"
seq= 31 11:13:59.339 claim          from=agv-3  task=pkg-1  winner=agv-2  rationale="Close to pkg-1 and idle"
seq= 32 11:13:59.385 agent_status   from=agv-2  task=pkg-1  status=running  (takeover begins)
seq= 33+                                                                    (agv-2 heartbeats while completing pkg-1)
```

### Per-AGV bus log

agv-3 (announcer):
```
[11:13:38] fault-detected: agv-1 silent 8.3s → orphan pkg-1 (announcer)
[11:13:39] announce-orphan: pkg-1 — waiting 20.0s for bids
[11:13:49] bid-recv: pkg-1: agv-2 score=85.0
[11:13:59] claim-publish: pkg-1 winner=agv-2 score=85.0
[11:14:00] yield: pkg-1 → agv-2
```

agv-2 (winner):
```
[11:13:38] fault-detected: agv-1 silent 8.4s → orphan pkg-1 (duplicate)
[11:13:48] orphan-bid: pkg-1 score=85.0 eta=2.5s — Close to pkg-1 and idle
[11:13:59] won: pkg-1 — beginning takeover
```

### Recovery timing
| Event | Timestamp | Δ from kill |
|-------|-----------|-------------|
| `docker stop interact-agv-1` | 11:13:32 | 0 s |
| `task_orphan` (agv-3 announcer) | 11:13:38.550 | +6.5 s |
| `bid` (agv-2 → qwen14b → bus) | 11:13:48.323 | +16.3 s |
| `claim` (winner picked) | 11:13:59.339 | +27.3 s |
| takeover starts (agv-2 running) | 11:13:59.385 | +27.3 s |

## Verification: central brain stayed silent

Backend log grep for `central_brain | _on_agv_fault | reassign` during this run: **0 hits**.

```
$ docker logs --since 5m interact-backend 2>&1 | grep -iE "central_brain|_on_agv_fault|reassign"
(empty)
```

This is the load-bearing check — in `app/agents/central_brain.py` the subscription
to `agv_fault` is gated on `coordination_mode == "centralized"`, so when the
mode flag is `decentralized` the central brain is structurally bypassed. The
takeover above was published, decided, and executed by peer AGVs only.

## Fixes landed while bringing it up

1. **`SocketIOBus(socketio)` → `SocketIOBus()`** — the bus reads `socketio` lazily; constructor takes no args. Was blocking `CentralBrain` init.
2. **`/ai/tactical` now surfaces `parsed`** — qwen14b's parsed JSON fields (`bid`, `score`, `rationale`, `eta_s`) reach the agv brain directly instead of being hidden behind `script` / `value`.
3. **Tolerant bid parser** — strips ```json fences, finds first `{...}` block, falls back to heuristic accept if AI is unparseable but reachable.
4. **Bus-only heartbeat** — fault detection no longer trusts `agv_state.status="stopped"` (false-positive at startup); peer aliveness is purely based on the last `agent_status` we saw on the bus.
5. **Periodic heartbeat publisher** — each AGV emits `agent_status` every 3 s while running so peers' stale timer doesn't trip on healthy work.
6. **Lifecycle-aware orphan dedup** — `_orphaned_task_ids` is cleared when a fresh `agent_status running` for the same task_id appears, so re-running the same plan after a prior fault doesn't permanently block legitimate future orphans.
7. **`_current_state` clears stale task_id on terminal status** — heartbeat no longer publishes `"running pkg-1"` after the brain transitioned to `"done"`, so peers don't phantom-fault a completed task.

## Open issues for later phases

- **Planner under-assignment:** qwq:32b returned only 1 assignment for "send 3 packages" in this run. Phase 1 success doesn't depend on it (one running AGV is enough to kill and recover), but Phase 2's `task_announce` flow needs the planner to enumerate all packages.
- **20 s bid window** is longer than the brief's 2.5 s purely because of qwen14b's TUC-GPU latency. Tighten if the GPU gets faster or if we move tactical to a smaller local model.

## Status

✅ Phase 1 — **decentralized fault recovery proven live**.
