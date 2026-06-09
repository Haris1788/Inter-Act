# Phase 3 — Master Capability Filter (M3)

**Status:** ✅ Verified live on 29 May 2026 (direct in-process injection).

## What changed

`app/agents/master_announcer.py:MasterAnnouncer.on_bid` now rejects bids whose
bidder's `allowed_labels` does not include the task's label, when running in
`smart` (US 34) mode.

## What I implemented

### `app/agents/master_announcer.py:on_bid`

New filter block, executed before the bid is added to the task's `bids` dict:

```python
from app.models import pipeline_config
if pipeline_config.coordination_mode_is_smart():
    with self._lock:
        record = self._tasks.get(task_id) or {}
    task_label = (record.get("payload") or {}).get("label") or record.get("label", "")
    caps = pipeline_config.get_all_agv_capabilities()
    bidder_caps = caps.get(bidder)
    if bidder_caps is not None and task_label and \
            not bidder_caps.accepts_label(task_label):
        _log("planner",
             f"REJECT bid {bidder} for {task_id} (label={task_label!r}): "
             f"not in allowed_labels={bidder_caps.allowed_labels}")
        return  # bid discarded; NOT added to record["bids"]
```

The filter is wrapped in `try / except` so any reflection or config error
falls through to the legacy permissive path — the filter must never block
the protocol.

## Features

- **Defence in depth.** The AGV-side gate (Phase 2 step 4: "early decline if
  not _accepts_label") prevents most mismatches from ever leaving the
  container. The master filter is the second line — catches anything that
  slips through (misconfigured / malicious / bug).
- **Logged, not silent.** Rejected bids produce a `REJECT bid <agv> for
  <task> (label=...): not in allowed_labels=...` line on the planner log
  — visible in the operations bus / SocketIO log panel.
- **Mode-gated.** In `centralized` or `decentralized` (US 32) mode, the
  filter is bypassed entirely. US 32 fallback verified unchanged.

## Capabilities

| Filter behaviour | Mode | Source of truth |
|---|---|---|
| All bids accepted (US 32 behaviour) | `centralized`, `decentralized` | n/a — filter not executed |
| Bid rejected when label not in `allowed_labels` | `smart` | `pipeline_config.get_all_agv_capabilities(agv_id).allowed_labels` (built from `pipeline.yaml`) |

## Live test results (29 May 2026 evening)

### Test 1 — natural live run

`pipeline.yaml` configured agv-2 with `allowed_labels: [green]` only. Dispatched
the standard 3-package run (red / green / blue). Bus transcript:

- agv-2 bid ONLY on pkg-2 (green); did NOT bid on pkg-1 (red) or pkg-3 (blue).
- This is the **AGV-side Phase 2 early-gate** working: agv-2's brain declined
  to bid on red/blue before sending anything to the bus.
- Final allocation: pkg-1 → agv-1, pkg-2 → agv-2, pkg-3 → agv-3. Spread rule
  + capability matching produced the right answer.

This run proves end-to-end correctness but doesn't isolate the master filter
because the inner gate prevented mismatched bids from ever reaching it.

### Test 2 — direct in-process injection (isolates the master filter)

Bypassed the AGV-side gate by calling `bus_routes.publish_local('bid', ...)`
directly inside the backend container, simulating a malformed bid that didn't
go through any AGV brain. Test command and result:

```
seeded pkg-filtertest-red with label=red

--- injecting agv-2 (allowed=[green]) bid — expect REJECT ---
--- injecting agv-1 (allowed=red,green,blue,any) bid — expect ACCEPT ---

FINAL state:
  agv-2 in bids? False   (filter rejected — bid not added to record)
  agv-1 in bids? True    (filter accepted)
  bids dict: {'agv-1': {'score': 50.0, ...}}
```

The filter behaves exactly as designed: agv-2's bid is dropped from the task
record; agv-1's bid is accepted. **Master-side defence proven independent of
the AGV-side gate.**

## Limitations

- **REJECT log line goes through `_log` (SocketIO emit)** — when triggered from
  a `docker exec` script there's no SocketIO server available so the log
  isn't visible in `docker logs`. In normal operation (via the live planner
  thread), the REJECT line appears in the planner log + SocketIO log panel.
- **Filter consults `pipeline.yaml` for capabilities**, not `agv_state`.
  Rationale: `agv_state` is populated from `pipeline.yaml` at startup so they
  agree; reading directly from config avoids a circular dependency
  (`agv_state` → `pipeline_config`).

## US 32 fallback verified

`coordination_mode_is_smart()` returns `False` when `COORDINATION_MODE` is
`centralized` or `decentralized`. In both cases the filter block is skipped
and `on_bid` behaves byte-for-byte like the US 32 version.

## Next phase

Phase 4 (M4) — `enforce_sequence` flag in TaskSpec. When `true`, master holds
back announce N+1 until announce N's winning AGV reaches `arm_ready loaded`.
Default off — concurrent execution stays the demo's main feature; the flag is
opt-in when the prompt implies ordering ("sort by", "in order").
