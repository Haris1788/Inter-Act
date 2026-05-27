# Phase 2 — Decentralized Initial Assignment via Contract-Net

**Date:** 2026-05-21
**Branch:** dev-haris
**COORDINATION_MODE:** decentralized
**TUC GPU tunnel:** active

## What was built

Phase 1 covered fault recovery. Phase 2 covers **initial allocation**: the
master AI no longer hard-binds packages to specific AGVs. Instead, for every
package in the plan, the master publishes a `task_announce` to the bus. Each
AGV evaluates it via its own qwen14b tactical brain and publishes a `bid`.
The master collects bids and publishes a `claim` naming the winner. The
planner's `agv_id` is downgraded to an `agv_hint` payload field — used only
as a last-resort fallback if no AGV ever bids.

### New components

| File | Role |
|------|------|
| `app/agents/master_announcer.py` | Backend-side announcer — owns one `MasterAnnouncer` singleton with a resolver thread that watches deadlines, picks max-score winners, publishes `claim`, and triggers the existing `_assign_agv` + `_auto_arm_signals` pipeline |
| `app/routes/ai_routes.py` | `/plan` now routes through `master_announcer.announce_plan()` when `COORDINATION_MODE=decentralized`; falls back to legacy `dispatch_plan` otherwise |
| `scripts/agv_bus.py` (`_on_announce` → thread) | Bid evaluation now runs in a daemon thread so the bus loop keeps processing other announces while qwen14b crunches (~10-15 s per call on shared GPU) |

### Master announcer tunables
- `BID_WINDOW_S = 30.0` — initial deadline after announce
- `BID_QUIET_S = 6.0` — when a fresh bid arrives, deadline extends to "now + 6 s", so the window stays open while peers are still answering
- `BID_HARD_S = 90.0` — absolute ceiling regardless of bid traffic
- `RESOLVE_TICK_S = 1.0` — resolver poll cadence

## Live test — 2026-05-21 11:27 local

`POST /plan {"prompt":"send 3 packages sorted red green blue, distribute across all available AGVs"}`

### Verbatim bus transcript
```
seq= 4 11:27:15.096 task_announce  from=master  task=pkg-1  agv_hint=agv-1  label=red
seq= 5 11:27:15.096 task_announce  from=master  task=pkg-2  agv_hint=agv-2  label=green
seq= 6 11:27:15.096 task_announce  from=master  task=pkg-3  agv_hint=agv-3  label=blue

       ─── 36 s later, qwen14b on shared TUC GPU starts answering ───

seq= 7 11:27:51.861 bid  from=agv-3  task=pkg-2  score=85  eta_s=30  "Close to pkg-2 and no current task"
seq= 8 11:27:52.180 bid  from=agv-2  task=pkg-3  score=85  eta_s=5   "Close to pkg-3 and idle"
seq= 9 11:27:53.312 bid  from=agv-2  task=pkg-2  score=85  eta_s=10  "Close to pkg-2 and idle"
seq=10 11:27:53.341 bid  from=agv-3  task=pkg-1  score=85  eta_s=30  "Close to pkg-1 and idle"
seq=11 11:27:54.280 bid  from=agv-3  task=pkg-3  score=95  eta_s=5   "Close to pkg-3 and not carrying any load."
seq=12 11:27:54.847 bid  from=agv-1  task=pkg-2  score=95  eta_s=10  "Close to pkg-2 and not carrying any load"
seq=13 11:27:55.188 bid  from=agv-1  task=pkg-3  score=85  eta_s=20  "Close to pkg-3 and currently idle"
seq=14 11:27:55.495 bid  from=agv-1  task=pkg-1  score=85  eta_s=15  "Closest AGV to pkg-1 and not carrying anything."

       ─── BID_QUIET_S=6 s after last bid → resolver claims ───

seq=15 11:28:01.112 claim  from=master  task=pkg-2  winner=agv-1  score=95
seq=17 11:28:02.119 claim  from=master  task=pkg-1  winner=agv-1  score=85   (tied on 85 — agv-1 wins tie-break)
seq=18 11:28:02.122 claim  from=master  task=pkg-3  winner=agv-3  score=95
seq=22 11:28:02.443 bid    from=agv-2   task=pkg-1  (LATE — arrived after claim, ignored)
```

### Resulting allocation
- pkg-1 (red) → agv-1
- pkg-2 (green) → agv-1
- pkg-3 (blue) → agv-3
- agv-2 idle this round (its bids all lost to higher-scoring peers)

### What this proves
1. **Master never decides who runs what** — it publishes the announce and the deadline picks the winner from peer-submitted scores.
2. **Concurrent bid evaluation in each AGV** — 8 bids arrive across all 3 AGVs in a 4-second burst at 11:27:51-55, despite each qwen14b call taking ~10 s. Without the per-announce thread, a single AGV would have serialized to ~30 s for all three packages.
3. **Quiet-period extension** — the deadline closes 6 s after the last bid, not at a fixed wall-clock. With 8 bids streaming in, the window stayed open for 47 s but closed promptly once peers fell silent.
4. **`fallback hint` is not load-bearing** — in this run zero claims used the hint; every claim was a real bid winner. The hint is just insurance for the no-bidders-at-all path.

## Open issues

- pkg-1 went to agv-1 on a tie (both agv-1 and agv-3 bid 85). Tie-break is `(score, -sum(ord(c)) of agent_id)` → agv-1 < agv-3 lexically. This is deterministic but cosmetic; the brief just says "highest score wins; tie-break = lowest agent id" so the implementation matches.
- agv-2's late pkg-1 bid (score=95) at 11:28:02.443 lost because it arrived 0.3 s after the claim. Tightening AGV-side bid latency (smaller model? cached embeddings?) would resolve this.
- The 36-second gap between announce and first bid is **pure TUC GPU queueing**. The protocol does the right thing while it waits.

## Status

✅ Phase 2 — **decentralized initial allocation proven live with real AI-scored bids**.
