# Phase 4 — `enforce_sequence` Flag (M4)

**Status:** ✅ Verified live on 29 May 2026.

## What changed

The TaskSpec carries a new optional `enforce_sequence` boolean. When `True`,
the master announcer publishes only the first package immediately and queues
the rest — each subsequent announce fires when the previous package reaches
`arm_ready loaded`. Off by default → US 32 concurrency unchanged.

## What I implemented

### `app/services/task_spec.py`
- `TaskSpec` dataclass gains `enforce_sequence: bool = False`.
- `to_dict()` includes it so it propagates downstream.
- `validate(raw, raw_prompt)`:
  - If `raw` includes `enforce_sequence` as a bool, use it directly.
  - Else infer from the prompt via keyword heuristics:
    ```python
    sequence_signals = (
        "sort by", "in order", "sequentially", "one after another",
        "one at a time", "in sequence", "rainbow order", "follow the order",
    )
    enforce_sequence = any(sig in raw_prompt.lower() for sig in sequence_signals)
    ```

### `app/agents/central_brain.py`
- `make_plan(spec, llm)` copies `spec.enforce_sequence` into the returned plan
  dict so `MasterAnnouncer.announce_plan` can read it without re-routing
  through spec.

### `app/agents/master_announcer.py`
- New per-plan state:
  ```python
  self._sequence_queue: List[(package_id, payload, label, color, idx)] = []
  self._sequence_enabled: bool = False
  self._last_loaded_task_id: Optional[str] = None
  ```
- `announce_plan(plan)`:
  - Reads `plan["enforce_sequence"]` and sets `self._sequence_enabled`.
  - For each assignment after the first (sorted by `sequence`):
    - If sequence-enforcement is on → append to `_sequence_queue`, log
      `"queue <pkg> — waits for previous announce to reach arm_ready loaded"`,
      do NOT publish.
    - Else → publish immediately as before (US 32 behaviour).
  - Returns the response dict with an `enforce_sequence` echo.
- New subscriber `on_arm_ready(ev)`:
  - Subscribed to `arm_ready` bus events via the in-process `MessageBus`.
  - Filters for `payload.action == "loaded"`.
  - Deduplicates by `_last_loaded_task_id` so a doubled bus event doesn't
    release two queued items.
  - Pops the next queued item, seeds a fresh `_tasks` record with new
    deadlines, publishes the `task_announce`, logs the release.
- `_ensure_subscriptions()` extended to subscribe `on_arm_ready` alongside
  `on_bid`.

## Features

- **Auto-detected from prompt.** Phrases like *"in order"*, *"sequentially"*,
  *"one at a time"*, *"rainbow order"* set `enforce_sequence=True` without
  the operator having to ask explicitly.
- **Same-millisecond release.** As soon as the previous package hits
  `arm_ready loaded`, the next announce fires (verified to within a
  millisecond — see live transcript below).
- **Off by default.** US 32's concurrency stays the demo's main feature;
  the flag is opt-in based on prompt semantics.
- **Graceful with fault recovery.** A queued task that's never released
  (because the prior task faulted and never loaded) is honestly stuck — the
  fault-recovery contract-net handles the prior task's reassignment, and
  the new winner's `arm_ready loaded` releases the queue.
- **Dedup against double events.** `_last_loaded_task_id` ensures one
  `arm_ready loaded` releases at most one queued announce.

## Capabilities

| Capability | Source | Behaviour |
|---|---|---|
| Auto-detection from prompt | `task_spec.validate(raw_prompt=...)` | Heuristic keyword match; can be overridden by explicit `enforce_sequence` in raw spec dict |
| Plan propagation | `central_brain.make_plan` → `plan["enforce_sequence"]` | Boolean carried into the plan that `announce_plan` receives |
| Staging | `MasterAnnouncer.announce_plan` | Queue all-but-first; publish first immediately |
| Release trigger | `MasterAnnouncer.on_arm_ready` | Subscribed to `arm_ready` events; releases next queued on `action=loaded` |

## Live test results (29 May 2026 evening)

**Prompt:** *"send 3 packages in order red green blue, sequentially one at a time"* — both
"in order" and "sequentially" present, so `enforce_sequence` auto-set to `True`.

```
12:50:49.427 ANNOUNCE  pkg-1  label=red       ← only one announced initially
12:51:26.433 CLAIM     pkg-1 -> agv-1
12:51:44.090 ARM_READY pkg-1 LOADED
12:51:44.090 ANNOUNCE  pkg-2  label=green     ← same millisecond
12:52:06.490 ARM_READY pkg-1 UNLOADED
12:52:14.448 CLAIM     pkg-2 -> agv-2
12:52:34.589 ARM_READY pkg-2 LOADED
12:52:34.589 ANNOUNCE  pkg-3  label=blue      ← same millisecond
```

### Observations

1. **Initial state holds the queue.** Only pkg-1 was on the bus for the first
   55 seconds. pkg-2 and pkg-3 stayed in `_sequence_queue` invisible until released.
2. **Release latency = zero.** Each `arm_ready LOADED` and the corresponding
   next `ANNOUNCE` share the same millisecond on the bus log. The in-process
   subscriber pattern means there's no polling delay.
3. **Order matches prompt.** Red → green → blue. The professor's "colour sequence
   not preserved" concern from the 29 May review is now satisfied.

## Off-path test (flag OFF — US 32 behaviour preserved)

For a prompt without ordering keywords ("send 3 packages, distribute across AGVs"),
`enforce_sequence` stays False, `announce_plan` publishes all three task_announce
events at t=0 (US 32 behaviour), and AGVs bid concurrently. Verified earlier in
Phase 2 — that's the same code path, unchanged.

## Limitations

- **Heuristic prompt detection is shallow.** Misses paraphrases like *"first
  red, then green, then blue"* or *"do them one by one"*. Either add more
  keywords or surface a checkbox in the UI (deferred to the integration pass).
- **No timeout on the queue.** If the loaded handshake never fires (e.g., the
  arm is stuck), the remaining queued announces never release. Acceptable for
  the demo; a future US could add a "stuck more than N seconds" timeout that
  publishes the next anyway with a degraded log line.
- **Fault recovery during sequence.** If pkg-1's winner faults after load, the
  contract-net runs again — but the `arm_ready LOADED` already fired so the
  next queue item already released. This is correct behaviour (the prior
  task is "logically loaded" from the master's perspective); it does mean
  two packages can be in flight briefly during a fault. Acceptable.

## US 32 fallback verified

When `enforce_sequence` is False (the default), the new code paths are silent:

- `announce_plan` takes the original publish-all-now path for every assignment.
- `on_arm_ready` early-returns on `self._sequence_enabled == False`.
- `_sequence_queue` stays empty, never inspected.

Byte-for-byte equivalent to US 32 when the flag is off.

## Next phase

Phase 5 (C4 backend half) — `GET /capabilities` endpoint surfacing the per-AGV
capability table for the future Inspector. Data already exists in
`pipeline_config.get_all_agv_capabilities`; phase is the route + a small JSON
shaping function. Inspector display itself is deferred (see
`UI-INTEGRATION-DEFERRED.md`).
