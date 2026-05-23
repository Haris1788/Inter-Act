# Phase 4 Stage 2 Committed Sub-Spec — Actor Isolation

Convert actor agents from backend threads into isolated per-process services. Move shared state
(locks, agv_state, arm_state, handshake signals) from backend memory to Redis. Preserve every
existing API contract; consensus + barrier semantics unchanged.

## Decisions (approved)

- Shared-state model: **R — Redis-backed locks + state**. Backend has no orchestration role.
- Lock primitive: `SET key value NX PX <ttl_ms>` + Lua compare-and-delete release.
- Per-actor process via `scripts/actor_runner.py`; label-owned by `container_manager.reconcile_actors`.

## Stage order (within Stage 2)

A (single track, blocking) → B ‖ C ‖ D (parallel after A's contracts land).

### Track A — Shared-state migration (consistency surface, NO parallel)

1. **`app/models/redis_lock.py`** (new) — Redis-backed mutual-exclusion lock matching the
   existing `grid_state.LaneLock` surface:
   ```python
   class RedisLock:
       def __init__(self, key: str, redis_client, default_ttl_ms: int = 180_000): ...
       def acquire(self, holder: str, timeout: float = 60.0,
                   cancel_check: Optional[Callable[[], bool]] = None) -> bool: ...
       def release(self, holder: str) -> bool: ...    # Lua CAS
       def force_release(self) -> None: ...
       def holder(self) -> Optional[str]: ...
       def is_free(self) -> bool: ...
       def touch(self, holder: str) -> bool: ...      # extend TTL
   ```
   - `acquire`: loop `SET key holder NX PX ttl_ms`; on conflict sleep 50 ms and re-check
     `cancel_check`; bounded by `timeout`. Re-entrant if same `holder` already owns (treat OK).
   - `release`: Lua script `if redis.call("GET", KEYS[1]) == ARGV[1] then return
     redis.call("DEL", KEYS[1]) else return 0 end` — CAS, no double-release races.
   - `touch`: Lua script CAS-extends PX. Called by heartbeat (see step 5).
   - Crash-orphan recovery: TTL expires naturally; no central reaper.
2. **`app/models/grid_state.py`** — replace `LaneLock`/`station_lock` internals with `RedisLock`.
   Keep the public functions (`lane()`, `station_lock(name)`, `set_cell`, `cell_holder`,
   `is_cell_free`, `snapshot`) **API-identical**. Cell occupancy moves to a Redis Hash
   `grid:occupancy` (field = `agv_id`, value = `"col,row"`). `snapshot()` reads it. Lane key
   = `grid:lock:lane`; station keys = `grid:lock:station:<name>`. Default TTL 180 s.
3. **`app/models/agv_state.py`** — replace in-memory `_state` dict with Redis Hash per AGV:
   key `agv:<id>`, fields = current dict shape (status, cell, target_cell, carried_object,
   bay, error, last_change). Keep ALL public functions (`get`, `get_all`, `set_cell`,
   `set_status`, `set_target`, `set_carried`, `is_alive`, `fail`, `recover`, `mark_idle`,
   `initialize_from_config`, `register`, `reset`) API-identical. Use HSET/HGETALL; serialize
   list fields (cell/target_cell/bay) as JSON within the hash. `get_all` uses SCAN with
   prefix `agv:`.
4. **`app/models/arm_state.py`** — same migration. Redis Hash per arm: `arm:<id>`.
5. **`app/routes/agv_routes.py`** — the promoted handshake functions (`signal_load`,
   `poll_load`, `clear_load`, signal_unload trio) — back the `_signals` registry with a
   Redis Hash `signals:<agv_id>` (single-task scope — Stage 3 rekeys to per-task). Keep
   function signatures unchanged; HTTP routes call the same functions.
6. **Heartbeat / TTL renewal** — extend `RedisLock` so any holder can call `touch()`. Add a
   minimal `LockKeepalive` helper that an actor uses to spawn a daemon thread renewing its
   held locks every TTL/3 (e.g. every 60 s for 180 s TTL). Wired in `AGVAgent._execute_leg`
   around the lock-holding windows; on lock release the keepalive stops.

**Track A invariants:**
- API surfaces of `grid_state`, `agv_state`, `arm_state`, handshake helpers are byte-identical
  for callers. Direct test parity.
- No central coordinator. Locks expire by TTL; no reaper process owns recovery.
- All writes go through the existing public functions — no new "fast paths".

### Track B — Per-actor process (`scripts/actor_runner.py`)

7. **`scripts/actor_runner.py`** (new) — entrypoint:
   ```
   ACTOR_ID=arm-1 ACTOR_KIND=arm REDIS_URL=redis://redis:6379/0 \
     INTERACT_BUS=redis CONFIG_URL=http://backend:5000/config \
     python scripts/actor_runner.py
   ```
   - Loads `CapabilityProfile` from backend `/config` (or mounts `pipeline.yaml`) by
     `ACTOR_ID`. Reuses `capability.load_profiles()` filtered by id.
   - Constructs `RedisStreamsBus` (Stage 1) — exists already; just `get_bus()` returns it
     when `INTERACT_BUS=redis`.
   - Instantiates `ArmAgent` or `AGVAgent` per `ACTOR_KIND`.
   - Calls `agent.start()`. Installs SIGTERM/SIGINT handler → `agent.stop()` + clean exit.
   - Idle loop: `time.sleep(1)` while agent thread alive. No HTTP server in the actor.

### Track C — Reconciler extension + compose

8. **`app/services/container_manager.py`** — extend `reconcile_actors(desired)` to handle
   `kind="agv"`. The label scheme + idempotent reconcile is already scale-symmetric — just
   add the kind-specific image + env (`ACTOR_ID`, `ACTOR_KIND`, `REDIS_URL`, `INTERACT_BUS`,
   `CONFIG_URL`). Shared image `interact-shared-actor` (one image, runs `actor_runner.py`,
   chooses agent class by env). Existing arm-only `desired` set extended to include every AGV
   from `pipeline_config.get_agvs()`.
9. **`Dockerfile.actor`** (new) — minimal Python image; copies `app/agents/`, `app/models/`,
   `app/routes/` (for handshake helpers), `scripts/actor_runner.py`; CMD `python
   scripts/actor_runner.py`.
10. **`docker-compose.yml`** — no `agv-*` or `arm-*` services (still reconciler-owned). Just
    document the actor image in build context. `generate_compose.py` already core-only.
11. **`app/__init__.py`** — REMOVE the `agent_registry.start_agents()` call at backend
    startup. Actors no longer run in-backend. The backend still: serves API + frontend +
    runs the SocketIO mirror consumer + reconciles actor containers via the existing
    `/deploy/apply` path. Add a startup-time `reconcile_actors(desired)` call so actors
    are spun up automatically (or document that `/deploy/apply` does it).

### Track D — Tests

12. **`tests/test_redis_lock.py`** (new) — `RedisLock` against fakeredis: acquire/release,
    timeout, cancel_check, re-entrancy, CAS release safety (wrong holder cannot delete),
    TTL expiry, touch extends TTL.
13. **`tests/test_grid_state_redis.py`** — parametrize existing grid tests over the Redis
    backend (fakeredis).
14. **`tests/test_agv_state_redis.py`**, **`tests/test_arm_state_redis.py`** — same.
15. **`tests/distributed/test_actor_isolation.py`** (new) — spin up multiple `actor_runner`
    subprocesses against a fakeredis-backed bus; run a full negotiation; assert consensus
    + execution complete; assert kill -9 of an actor mid-leg → its locks expire by TTL +
    reopen fires.

## Hard invariants

- Public APIs of `grid_state`, `agv_state`, `arm_state`, handshake helpers unchanged.
- No backend orchestration. Backend is API + frontend + mirror + reconciler only.
- Locks are CAS-released (Lua). Never a blind DEL.
- TTL default 180 s; touch every 60 s for held locks during legs.
- Consensus + barrier + reopen logic untouched. Pure-functional over `bus.history()` still
  holds across processes.
- `actor_runner.py` is dumb — load profile, start agent, wait. No HTTP, no decisions.

## Verify

- `INTERACT_BUS=inprocess` baseline still passes (262 tests with the legacy in-memory state
  models — backward compatible? See note below.).
- `INTERACT_BUS=redis` against fakeredis: state-model parametrized tests pass; multi-actor
  isolation test passes.
- `python -c "from app import create_app; create_app()"` constructs in both modes.
- `docker compose up -d redis backend` + `POST /deploy/apply` with current `pipeline.yaml`
  → reconciler creates one container per actor; `docker ps` shows labelled actor containers;
  `POST /negotiate` reaches consensus.

**Note on state-model parity:** `grid_state`/`agv_state`/`arm_state` need to support BOTH
backends (in-memory for tests, Redis for prod) for clean parity. Use the same `get_bus()`
env-switch pattern: `INTERACT_STATE=inmemory|redis` (default `inmemory`). The in-memory path
preserves Phase 3's exact behaviour for unit tests.

## Opus review gate

- Authority-boundary: actor processes own decisions; backend has no orchestration role.
- Lock correctness: no double-grant under concurrent acquire; no orphan after holder crash
  (TTL expires); no premature release (CAS); touch races safe.
- State-model parity: identical observable behaviour in-memory vs Redis (parametrized tests).
- Crash recovery: kill -9 of an actor mid-leg → locks expire by TTL → bus shows fault +
  reopen → another actor wins → execution completes.
