# Phase 5 Stage 9 Committed Sub-Spec — Chaos + E2E Validation

The final stage. Spatial chaos tests + 4 mandatory E2E scenarios via the
Vercel `agent-browser` CLI against the real running INTER.act stack. Per
Phase 5 prompt: **NO Playwright, NO mock, NO synthetic** — Vercel
agent-browser only against the real frontend + backend + distributed
runtime.

## Tracks

- A (single track, blocking): spatial chaos harness + 3 spatial chaos tests
  (pytest, fakeredis OR real-Redis-via-docker).
- B ‖ C ‖ D ‖ E (concurrent after A): 4 E2E scenarios. One script per
  scenario invoking `agent-browser` CLI.

### Track A — Spatial chaos tests

**Files:** `tests/distributed/test_spatial_chaos.py` (new), reuses Phase 4
`tests/distributed/chaos_harness.py` patterns.

Tests:
1. **`test_placement_change_changes_winner_under_chaos`** — multi-actor
   topology; place agv-1 closest; assert wins; relocate agv-1 far away
   (mid-test by updating its profile.position_px); assert next negotiation
   produces a different winner. This is the acceptance gate echoed at the
   chaos level.
2. **`test_path_replan_on_concurrent_reservation`** — pre-reserve cells
   under another actor id; assert AGV's A* avoids them; release; assert
   subsequent path can use them.
3. **`test_agv_crash_releases_reservation_via_ttl`** — AGV acquires a
   path reservation with short TTL (e.g. 800 ms); kill the actor; wait
   past TTL; assert another AGV can claim the same cells.

Use `make_chaos_env` pattern from `tests/distributed/chaos_harness.py`
(Phase 4 Stage 7) extended for spatial — pass a topology with
`position_px` actors. Build the AGV directly with spatial profile.

### Track B/C/D/E — E2E scenarios via agent-browser CLI

**Files:** `tests/e2e/scenarios/scenario_1_basic.sh`,
`scenario_2_longest_time.sh`, `scenario_3_multi_agent.sh`,
`scenario_4_fault_recovery.sh`. Plus `tests/e2e/runbook.md`.

Each script:
1. Checks the stack is up (`curl localhost:8080/`, `curl localhost:5000/health`).
2. Resets state: stop+remove actor containers; flush Redis stream + signals
   + reservation hashes; restart backend so reconciler reseeds clean.
3. Invokes `agent-browser` CLI with selector-based steps:
   - Navigate to `http://localhost:8080/`.
   - Drag arms + AGVs from sidebar (use `agent-browser` drag/drop primitive — confirm via `agent-browser skills get core --full`).
   - Place actors at scenario-specific coordinates.
   - Enter goal text in the Negotiate input.
   - Click Negotiate button.
   - Wait + take screenshot.
4. Assertions: poll `/bus/replay` via curl from inside the script; assert
   the scenario-specific invariant (winner identity, leg completion,
   return-to-origin).
5. On failure: capture bus log + screenshots + Redis state dump into
   `tests/e2e/results/<scenario>_<timestamp>/`.

#### Scenario 1 — Basic transport (`scenario_1_basic.sh`)
- Place arm-1 at (200, 200), arm-2 at (1200, 200).
- Place agv-1 close to arm-1 at (300, 200); agv-2 center at (700, 200);
  agv-3 far from arm-1 at (1100, 200).
- Goal: "move 1 parcel using shortest time".
- Assert: agv-1 wins transport (closest to pickup); LEG_COMPLETED for all
  3 slots; agv-1 ends at original drop position (300, 200) ± snap.

#### Scenario 2 — Longest time (`scenario_2_longest_time.sh`)
- Same topology as scenario 1.
- Goal: "move 1 parcel using longest time".
- Assert: agv-3 wins (farthest); LEG_COMPLETED for all 3 slots.

#### Scenario 3 — Multi-agent (`scenario_3_multi_agent.sh`)
- Same topology + add more AGVs / capacity tweaks if needed.
- Goal: "move 3 objects using 2 AGVs".
- Assert: at minimum two distinct AGVs participate in BIDs/ACCEPTs;
  no single AGV monopolizes; consensus emerges per-task.

#### Scenario 4 — Fault recovery (`scenario_4_fault_recovery.sh`)
- Same topology; start a transport.
- After CONSENSUS_REACHED for round 0: kill the winning AGV's container
  (`docker stop interact-agv-N`).
- Assert: WITHDRAW or FAULT appears for that AGV; round-1 TASK_ANNOUNCE
  appears (reopen); a different AGV wins round 1; LEG_COMPLETED for all
  3 slots in round 1.

## Tooling — agent-browser CLI

Located at `/c/Users/snehd/AppData/Roaming/npm/agent-browser` (v0.27.0).
First step in writing the scripts: `agent-browser skills get core --full`
to surface selector patterns + ref usage.

The CLI controls a real Chrome/Chromium session. Stage 9 ONLY uses it (per
prompt mandate forbidding Playwright/mock for E2E).

## Runbook (`tests/e2e/runbook.md`)

Document:
- Prereqs: docker stack up with Phase 5 images built (`docker compose build`
  + the actor-image build profile), Ollama tunnel at :11500 with qwq:32b +
  qwen2.5:14b, Redis running.
- Run order: scenario 1 → 2 → 3 → 4 (each resets state).
- Pass criteria per scenario.
- What to capture on failure.
- Manual triage for false-fail (e.g. LLM tunnel slow, retry once).

## Hard invariants

- E2E uses ONLY agent-browser CLI. NO Playwright, NO mocked browser.
- Each scenario starts from a CLEAN state (Redis flush + container restart).
- Assertions read REAL bus data via `/bus/replay`.
- The Phase 5 acceptance gate (placement-changes-winner) is provable from
  scenarios 1 + 2 alone — same topology, different constraint, different
  winner.

## Verify

- `python -m pytest tests/distributed/test_spatial_chaos.py -q` — all pass.
- All 4 E2E scenarios pass when run against the live stack.
- Results captured per scenario.

## Opus review gate

- Chaos tests assert spatial invariants explicitly.
- E2E scripts hit real frontend (no mocks).
- The Phase 5 acceptance gate is provable from at-least scenarios 1 + 2.
- No scenario depends on Playwright or any tool other than agent-browser CLI.

## Note on this session

This session has Playwright MCP loaded but NOT agent-browser MCP. The
agent-browser CLI is available locally. Stage 9 Track A (pytest chaos)
runs cleanly in this session. Tracks B-E (agent-browser scripts) — the
SCRIPTS get authored here; their execution against the live stack happens
at your direction (real LLM, real browser). The scripts are deliverables.

If you want the agent to also EXECUTE the scripts in this session, that
requires (a) the stack rebuilt with Phase 5 images and (b) the
`agent-browser` CLI driving from this shell. Both are achievable in this
session — proceed in order: rebuild stack → run Track A → execute Track
B/C/D/E.
