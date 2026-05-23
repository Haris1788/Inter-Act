# INTER.act Phase 5 Stage 9 — E2E Runbook

## Prerequisites

### Stack
1. Docker Desktop running with Phase 5 images built:
   ```bash
   cd "D:/Inter Act"
   docker compose build
   docker compose up -d
   ```
2. Actor containers running (the backend reconciler creates them automatically):
   ```bash
   docker ps --filter label=interact.role=actor
   # Should show arm-1, arm-2 containers (agv-1/2/3 run in-backend as threads).
   ```
3. Redis running as `interact-redis-1`:
   ```bash
   docker inspect interact-redis-1 | jq '.[0].State.Running'
   # Should print: true
   ```

### Ollama tunnel
4. The TU Clausthal LLM tunnel must be running at `http://localhost:11500`:
   - Models required: `qwq:32b` (interpreter) + `qwen2.5:14b` (bidding).
   - Verify: `curl http://localhost:11500/api/tags | jq '.models[].name'`
   - Without the tunnel, the interpreter cannot parse the natural-language goal
     and negotiations will not start.

### CLI tools
5. `agent-browser` in PATH (v0.27.0+):
   ```bash
   agent-browser --version
   # Should print: 0.27.0 or higher
   ```
6. `jq` in PATH (used for all bus JSON assertions):
   ```bash
   jq --version
   ```
7. `bc` in PATH (used for tolerance arithmetic in `assert_returned_to_origin`):
   ```bash
   bc --version
   ```
8. `curl` in PATH:
   ```bash
   curl --version
   ```

### Chrome
9. agent-browser needs a Chrome/Chromium installation:
   ```bash
   agent-browser install   # run once to install bundled Chromium
   ```

---

## Environment variables (optional overrides)

| Variable | Default | Purpose |
|---|---|---|
| `FRONTEND_URL` | `http://localhost:8080` | Frontend address |
| `BACKEND_URL` | `http://localhost:5000` | Backend API address |
| `REDIS_CONTAINER` | `interact-redis-1` | Redis container name |
| `BACKEND_CONTAINER` | `interact-backend-1` | Backend container (for restart in reset_state) |
| `RESULTS_DIR` | `tests/e2e/results` | Where artefacts are written |

---

## Run order

Always run in this order — each scenario resets state at startup, so they
are independent, but running 1 → 2 → 3 → 4 sequentially avoids Redis contention.

```bash
cd "D:/Inter Act"

bash tests/e2e/scenarios/scenario_1_basic.sh
bash tests/e2e/scenarios/scenario_2_longest_time.sh
bash tests/e2e/scenarios/scenario_3_multi_agent.sh
bash tests/e2e/scenarios/scenario_4_fault_recovery.sh
```

Or as a suite runner:

```bash
set -e
for s in 1 2 3 4; do
  bash "tests/e2e/scenarios/scenario_${s}_"*.sh
done
```

---

## Pass criteria per scenario

### Scenario 1 — Basic transport / shortest time
- `agv-1` wins the transport slot (closest to arm-1 at x=300 vs arm-1 at x=200).
- 3 `LEG_COMPLETED` events (pickup + transport + place) for the task.
- `agv-1` returns to approximately (300, 200) ±80 px after the task.
- **Acceptance gate contribution**: establishes the baseline winner for the default constraint.

### Scenario 2 — Longest time
- `agv-3` wins the transport slot (farthest from arm-1 at x=1100).
- 3 `LEG_COMPLETED` events.
- **Acceptance gate proof**: same topology, different constraint → different winner.
  Scenarios 1 + 2 together prove that placement drives negotiation outcome.

### Scenario 3 — Multi-agent (3 objects, 2 AGVs)
- At least 2 distinct AGV ids appear in `BID` or `ACCEPT` messages across all tasks.
- At least 1 task fully completes with 3 `LEG_COMPLETED` events.
- No single AGV wins all transport slots (soft assertion; logged as warning if only 1 winner).

### Scenario 4 — Fault recovery
- `FAULT` or `WITHDRAW` message for the killed/faulted AGV appears in bus history.
- A `TASK_ANNOUNCE` with `negotiation_round ≥ 1` appears (task reopened).
- A different AGV wins the round-1 transport slot.
- 3 `LEG_COMPLETED` events for round 1 (or total ≥ 3 as fallback if round field absent).

---

## Failure capture location

On any assertion failure the script calls `capture_failure <scenario_name>` which writes:

```
tests/e2e/results/<scenario_name>_<YYYYMMDD_HHMMSS>/
  bus_full.json          — full /bus/replay output at time of failure
  runtime_status.json    — GET /runtime/status snapshot
  redis_keys.txt         — all Redis keys at time of failure
  browser.png            — last agent-browser screenshot
  actor_containers.txt   — docker ps --filter label=interact.role=actor output
```

Examine `bus_full.json` first — it contains the complete negotiation trace and
is usually sufficient to diagnose failures.

---

## Manual triage notes

### LLM tunnel slowness
The `qwq:32b` model on the TU Clausthal tunnel can take 30-90 s to interpret
a prompt.  `wait_for_task_announce` has a 120 s timeout.  If you see:

```
[FAIL] No TASK_ANNOUNCE appeared within 120s
```

**Action**: Check `docker logs interact-backend-1 | grep interpret` for LLM
latency.  Retry the scenario once; if the tunnel is consistently slow, increase
`TASK_ANNOUNCE_TIMEOUT`:

```bash
# Run with extended timeout
TASK_ANNOUNCE_TIMEOUT=300 bash tests/e2e/scenarios/scenario_1_basic.sh
```

### Topology not applied (actors missing from canvas)
If `apply_topology` returns HTTP 200 but actors don't appear, the reconciler
may still be starting containers:

```bash
# Watch reconciler progress
curl http://localhost:5000/deploy/status
docker ps --filter label=interact.role=actor --format 'table {{.Names}}\t{{.Status}}'
```

Wait 15-30 s and re-run.

### agent-browser cannot find Negotiate button
If `find text "Negotiate" click` fails, the Goal Bar may have a different
label or be hidden.  The scripts fall back to `POST /negotiate` automatically.
This is safe — it still hits the real backend + LLM, just bypasses the UI click.

### Scenario 4: container name not found
In Phase 1, AGV agents run as in-backend threads (no separate containers).
The script falls back to `POST /agents/<agv-id>/fault` for fault injection.
If that endpoint also doesn't exist, the scenario logs a warning and tests
whatever fault events the backend's heartbeat monitor emits.  Check
`bus_full.json` for `task_fault` or `withdraw` messages from the AGV.

### Redis flush fails
If `FLUSHALL` is blocked by a persistence operation:

```bash
docker exec interact-redis-1 redis-cli BGSAVE   # finish pending save
sleep 2
docker exec interact-redis-1 redis-cli FLUSHALL
```

### Consensus never reached
Check `/bus/actors` for actors marked `presumed_dead` — a dead actor won't bid
and the consensus window will time out:

```bash
curl http://localhost:5000/bus/actors | jq '.actors[] | select(.presumed_dead == true)'
```

If an actor is dead, restart the backend container to trigger reconciliation:

```bash
docker restart interact-backend-1
# Then re-run the scenario.
```

---

## Notes on the agent-browser drag-drop strategy

All four scenarios use `POST /deploy/apply` as the topology application
mechanism (the "fallback" path that is actually the primary path).  This is
because ReactFlow's drag-and-drop fires custom `dragstart`/`drop` events that
may not be reachable by agent-browser's HTML5 drag primitive depending on the
ReactFlow version's event internals.

The scripts still:
1. Call `agent-browser open` and `wait --load networkidle` to load the real browser.
2. Take screenshots for audit purposes.
3. Use `find placeholder` / `find text` / `find role button` to interact with
   the Goal Bar — this part of the UI is standard DOM and fully reachable.
4. Read ALL assertions from real `/bus/replay` data — no mocking.

The topology is pre-applied via `/deploy/apply` before the browser session, so
the actors exist at the correct positions when the negotiation fires.
