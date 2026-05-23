# Phase 4 Stage 1 Committed Sub-Spec — Distributed Bus (Redis Streams)

Replace the in-process singleton `NegotiationBus` with a real distributed transport (Redis
Streams) while preserving exact semantic parity. Single Sonnet implementation agent, no parallel
sub-tasks (Stage 1 = highest review risk first distributed change). Opus review gate after.

## Decisions (approved)

- Transport: **Redis Streams**.
- Stream layout: **one global stream** `interact:negotiation`. `Message.task_id` lives in
  payload; consumers filter client-side.
- Consumer model: **one consumer group per actor** (`group=<actor_id>`, one consumer per group
  in Phase 4). Preserves `subscribe_all` semantics + gives PEL crash recovery.
- Idempotency: at-least-once + consumer-side dedup via bounded LRU of seen XIDs (1024 entries).
  Pure consensus is already idempotent; dedup is belt-and-braces.
- Persistence: Redis AOF, `appendfsync everysec`. Persistent volume.
- Frontend contract unchanged: backend keeps a single mirror consumer that re-emits each bus
  message as the existing `bus_message` SocketIO event.

## Required behaviour

`bus.history()`, `bus.publish(message)`, `bus.subscribe(type, cb)`, `bus.subscribe_all(cb)` must
behave identically whether the backend is `InProcessBus` or `RedisStreamsBus`. Every Phase 3
test that passes with `InProcessBus` must pass with `RedisStreamsBus` against a real (or fake)
Redis. Pure-functional consensus over `bus.history()` is the invariant — do not change it.

## Files

### 1. `app/agents/bus.py` (refactor)

- Promote the current class to an interface (Protocol or ABC) `NegotiationBus` with the four
  methods above.
- Rename the existing implementation to `InProcessBus(NegotiationBus)`. Behaviour unchanged.
- Add module-level `get_bus()` selecting based on `INTERACT_BUS` env var:
  - `"inprocess"` (default) → `InProcessBus`
  - `"redis"` → `RedisStreamsBus(redis_url=os.environ["REDIS_URL"])`
- Keep `reset_bus()` test hook working for both impls.
- `subscribe_all(callback, consumer_id: str | None = None)` — new optional kwarg. `InProcessBus`
  ignores it. `RedisStreamsBus` uses it as the consumer-group name. Backwards-compatible.
- Document that `subscribe` (type-filtered) is implemented as `subscribe_all` + client-side
  filter (Redis Streams isn't pub/sub-per-topic).

### 2. `app/agents/redis_bus.py` (new)

```python
class RedisStreamsBus(NegotiationBus):
    STREAM_KEY = "interact:negotiation"

    def __init__(self, redis_url: str): ...
    def publish(self, message: Message) -> None: ...           # XADD
    def history(self) -> list[Message]: ...                    # XRANGE - +
    def subscribe(self, msg_type, callback) -> None: ...       # subscribe_all + filter
    def subscribe_all(self, callback, consumer_id=None) -> None: ...
    def shutdown(self) -> None: ...                            # stop consumer threads
```

- Use `redis.Redis.from_url` (sync client — backend threads are blocking already).
- `publish`: `XADD STREAM_KEY * data <json>` where `<json>` is `message.to_dict()` JSON-encoded.
  Mirror the message to SocketIO IS NOT done here — it's done by the dedicated mirror
  consumer (see file 5). Publishing process simply XADDs; the mirror consumer broadcasts.
- `history`: `XRANGE STREAM_KEY - +` → deserialize each entry's `data` field back into a
  `Message`. Return in publish order. (XRANGE results are XID-ordered which equals publish
  order.)
- `subscribe_all`: if `consumer_id` is None, generate `f"sub-{uuid4().hex[:8]}"` (ephemeral).
  Idempotently `XGROUP CREATE STREAM_KEY <consumer_id> $ MKSTREAM` (catch `BUSYGROUP`). Spawn
  one daemon thread that loops:
  - `XREADGROUP GROUP <consumer_id> c1 COUNT 64 BLOCK 100 STREAMS STREAM_KEY >`
  - For each entry: parse, dedup via per-consumer `seen_xids` LRU, invoke callback in a
    try/except (a raising subscriber must not stall delivery), `XACK` on success.
- Track consumer threads in `self._consumer_threads`; `shutdown()` flips a stop flag and joins.
- Connection errors: log + retry with simple backoff (1 s, max 30 s); never crash the process.

### 3. `app/agents/actor_agent.py` (one-line change)

In `start()`, pass actor id to `subscribe_all`:
```python
self.bus.subscribe_all(self._enqueue, consumer_id=self.actor_id)
```
That's it. Consensus, barrier, evaluate, reopen — all unchanged. Pure-functional over
`bus.history()` carries over for free.

### 4. `app/__init__.py` (small change)

After the existing AGV pre-register block, before `start_agents()`, log which bus is active.
`get_bus()` is already lazy — first call from `start_agents()` triggers construction. No new
code path needed beyond the env var selection inside `bus.py`. Verify the SocketIO mirror
consumer (file 5) is started here for the Redis path (skip for in-process — its `_mirror`
already handles it).

### 5. `app/services/bus_mirror.py` (new)

Single background consumer running in the backend process when `INTERACT_BUS=redis`. Uses
`bus.subscribe_all(emit_to_socketio, consumer_id="__socketio_mirror__")`. On each delivered
`Message`, emits `socketio.emit("bus_message", message.to_dict())`. This is the ONE place
SocketIO mirroring lives in the distributed path. Started from `app/__init__.py` after
`start_agents()`.

### 6. `requirements.txt`

Add `redis>=5.0,<6` and `fakeredis>=2.20` (dev/test).

### 7. `docker-compose.yml` + `scripts/generate_compose.py`

Add Redis service to core compose:
```yaml
redis:
  image: redis:7-alpine
  command: ["redis-server", "--appendonly", "yes", "--appendfsync", "everysec"]
  volumes:
    - redis-data:/data
  ports:
    - "6379:6379"
  restart: unless-stopped
volumes:
  redis-data:
```
- Backend service: `depends_on: [redis]` + env `REDIS_URL=redis://redis:6379/0` and
  `INTERACT_BUS=redis`.
- Mirror the same in `scripts/generate_compose.py`.

### 8. Tests

- `tests/test_bus_contract.py` (new): parametrize a contract suite over both impls. Use
  `fakeredis` for the Redis variant so CI doesn't need a Redis container.
  Tests:
  - publish → history reflects it in order.
  - publish N messages → all delivered to a subscribe_all callback within 500 ms.
  - multiple subscribers each get every message (per-consumer groups in Redis).
  - duplicate XADD (simulated redelivery) → callback invoked once per unique XID.
  - subscriber raising an exception does not block delivery to others.
  - history is monotonic and matches publish order across reconnects (drop+recreate the
    consumer, history still complete).
- All existing Phase 3 tests must keep passing under `INTERACT_BUS=inprocess` (default).
  Negotiation integration tests under Redis fake: run a representative subset (full
  negotiation, fault-reopen, deadline-failure, higher-round halt) parametrized over both buses.

## Hard constraints

- The bus delivers + records. It does NOT decide consensus. Do not bake any consensus
  semantics into Redis adapter code.
- Do not introduce any synchronization primitive that requires a single coordinator (no global
  Redis "negotiation lock", no central planner-like helper).
- Do not change `actor_agent.py` consensus/barrier/reopen/failure logic. Pure-functional over
  `bus.history()` is the invariant.
- SocketIO mirror is the ONLY place backend re-emits the bus to the browser in the Redis path.
  Do not also emit from `publish()` — that would double-deliver.

## Verify

- `python -m pytest tests/ -q` with `INTERACT_BUS` unset → 248 pass / 5 pre-existing fail
  baseline (no regression).
- `INTERACT_BUS=redis REDIS_URL=fakeredis://... python -m pytest tests/test_bus_contract.py
  tests/test_negotiation_integration.py -q` → all pass (use fakeredis monkeypatch for the test
  case where REDIS_URL is fake).
- `docker compose up -d redis backend && curl localhost:5000/negotiate -X POST -d '{"prompt":"move package"}'` → TASK_ANNOUNCE appears in `redis-cli XRANGE interact:negotiation - +`, agents reach consensus, frontend BusPane shows real `bus_message`s.
- `python -c "from app import create_app; create_app()"` still constructs in both modes.

## Opus review gate

- Ordering preserved per stream (XID monotonic = publish order).
- No message loss across consumer reconnect (PEL covers in-flight; new consumers start at `>`).
- Effectively-once consensus despite at-least-once delivery (dedup + idempotent flags).
- Mirror does not double-deliver to the browser.
- `bus.history()` semantically identical for replay in both impls.
- No consensus/decision logic leaks into the bus layer.
