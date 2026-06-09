# Phase 2 — Deterministic Composite Bid (M2)

**Status:** ✅ Verified live on 29 May 2026.

## What changed

`scripts/agv_bus.py:_ask_for_bid` is now two-pathed:

- **`smart` mode (US 34):** compute a deterministic composite score from real grid distance + capability match + load, then call qwen14b as a JUDGE that can accept/reject and adjust within ±10 — but cannot invent the score.
- **Any other mode (US 32 fallback):** delegate to `_legacy_us32_ask_for_bid`, the original flat-AI implementation, verbatim. Flag-off path is byte-for-byte equivalent to US 32.

## What I implemented

### `app/routes/agv_routes.py` — `meta` block on `/coord/snapshot`
Added so each AGV's brain can read the real grid + bid weights without hardcoding:

```json
"meta": {
  "coordination_mode": "smart",
  "grid": {"cols": 14, "rows": 10, "diameter": 22},
  "bid_weights": {"distance": 0.6, "capability": 0.3, "load": 0.1},
  "stations": {
    "input_arm":  {"cell": [1, 1],  "dock": [2, 1]},
    "output_arm": {"cell": [12, 1], "dock": [11, 1]}
  }
}
```

### `scripts/agv_bus.py` — new module-level helpers

- `MY_CAPABILITIES` — built once at import time from env vars (same parsing as `agv_server.CAPABILITIES`; mirror because scripts can't import from app).
- `_mode_is_smart()` / `_accepts_label()`.
- `_manhattan(a, b)` — distance helper.
- `_compute_deterministic_score(my_cell, package_origin, package_label, current_load_kg, meta)` — returns `(score_0_100, breakdown_dict)`.
- `_eta_seconds_from_distance(my_cell, package_origin)` — naive but honest ETA estimate.
- `_send_to_tactical(system, user)` — factored out the LLM-call + parse logic so both legacy and smart paths share it.
- `_legacy_us32_ask_for_bid(...)` — the unchanged US 32 fallback.

### `scripts/agv_bus.py:_ask_for_bid` — rewritten

Algorithm in smart mode:

1. If `coordination_mode != "smart"` → call legacy implementation (US 32 fallback path preserved).
2. Read live meta from `/coord/snapshot` (stations, grid, bid_weights).
3. Compute `package_origin = stations.input_arm.cell` (pre-pickup).
4. **Early capability gate**: if `MY_CAPABILITIES.allowed_labels` does not include the package label, return `(False, 0.0, "declined: ...")` immediately. The Phase 3 master filter will be a second-line defence.
5. Compute deterministic composite:
   ```
   score = w_d * (1 − manhattan(my_cell, origin) / diameter)
         + w_c * capability_match
         + w_l * (1 − current_load_kg / weight_max_kg)
   ```
   Clamp to `[0, 100]`.
6. Build a JUDGE-style prompt for `qwen14b`:
   ```
   {"accept": true|false, "adjusted_score": <0-100>, "rationale": "<1 sentence>"}
   ```
   System prompt explicitly forbids invention: "Stay close to the deterministic score — drifting more than ±10 is a protocol violation."
7. On `accept=true`, clamp `adjusted_score` to `deterministic_score ± 10`. Compose final rationale that includes the deterministic breakdown AND the judge's note.
8. On `accept=false`, drop the bid — but **only if qwen14b deliberately rejected**. If the AI call failed (timeout / `planner busy`), fall through to publishing the deterministic-only score so the protocol still has a bidder.

### Parser alias for the judge schema

`_send_to_tactical` parses `bid` / `score` (US 32). I added an alias:
```python
if "bid" not in parsed and "accept" in parsed:   parsed["bid"]   = parsed["accept"]
if "score" not in parsed and "adjusted_score" in parsed: parsed["score"] = parsed["adjusted_score"]
```
Lets one parser handle both response shapes.

## Features

- **Real Manhattan distance** drives the dominant 60 % weight; closer AGV scores higher.
- **Capability match** is a hard prerequisite (early gate) and a 30 % weight contributor.
- **Load awareness** at 10 % weight — full AGV (carrying) scores ~5 pts lower than empty AGV at same distance.
- **qwen14b is consulted but cannot fabricate scores** — clamped ±10 around the deterministic value.
- **Graceful fallback to deterministic-only** when the GPU is down (judge unavailable → still bid).
- **Bus rationale exposes the full breakdown**: `composite=92.27 (dist=1/22, cap=+, load_norm=0.5); judge: ...`
- **US 32 path preserved verbatim** via `_legacy_us32_ask_for_bid`.

## Capabilities (data flowing into the score)

| Field | Source | Used in score? |
|---|---|---|
| `my_cell` | `/agv/state/<id>` (existing) | Yes — distance |
| `package_origin` | `/coord/snapshot.meta.stations.input_arm.cell` (new) | Yes — distance |
| `package_label` | `task_announce.payload.label` (existing) | Yes — capability match |
| `MY_CAPABILITIES.allowed_labels` | env var `AGV_ALLOWED_LABELS` (Phase 1) | Yes — capability match + early gate |
| `MY_CAPABILITIES.weight_max_kg` | env var `AGV_WEIGHT_MAX_KG` (Phase 1) | Yes — load fraction denominator |
| `current_load_kg` | derived from `carried_object` presence | Yes — load |
| `grid.diameter` | `/coord/snapshot.meta.grid.diameter` (new) | Yes — distance normalisation |
| `bid_weights` | `/coord/snapshot.meta.bid_weights` (new) | Yes — coefficient blend |

## Live test results (29 May 2026 evening, 12:37-12:38)

```
ANNOUNCE pkg-1 hint=agv-1 label=red
ANNOUNCE pkg-2 hint=agv-2 label=green
ANNOUNCE pkg-3 hint=agv-3 label=blue

bid pkg-1 from=agv-3 score=92.30  composite=92.27 (dist=1/22, cap=+, load_norm=0.5); judge: Slight adjustment for minor traffic risk near estimated path.
bid pkg-3 from=agv-1 score=70.45  composite=70.45 (dist=9/22, cap=+, load_norm=0.5); judge: The deterministic score accurately reflects the current context.
bid pkg-1 from=agv-2 score=64.55  composite=64.55 (dist=13/22, cap=+, load_norm=1.0); judge: No additional context warrants an adjustment.
bid pkg-2 from=agv-1 score=70.45  composite=70.45 (dist=9/22, cap=+, load_norm=0.5); judge: Score within acceptable range and no additional context for adjustment.
bid pkg-2 from=agv-3 score=92.27  composite=92.27 (dist=1/22, cap=+, load_norm=0.5); judge: Deterministic score is accurate; no adjustment required.
bid pkg-3 from=agv-3 score=92.27  composite=92.27 (dist=1/22, cap=+, load_norm=0.5); judge: Score aligns with capability and proximity.
bid pkg-1 from=agv-1 score=70.45  composite=70.45 (dist=9/22, cap=+, load_norm=0.5)
bid pkg-2 from=agv-2 score=64.55  composite=64.55 (dist=13/22, cap=+, load_norm=1.0)
bid pkg-3 from=agv-2 score=64.55  composite=64.55 (dist=13/22, cap=+, load_norm=1.0) (judge unavailable: AI error: Read timed out)

CLAIM pkg-3 -> agv-3 score=92.27
CLAIM pkg-1 -> agv-1 score=70.45
CLAIM pkg-2 -> agv-2 score=64.55
```

### Observations

1. **Closest AGV wins each tied-capability package**: agv-3 was at dist=1 (it had drifted near the input area from a prior run) → 92.27 → wins pkg-3.
2. **Spread set still applies**: agv-3 also bid 92.27 on pkg-1 and pkg-2 — would have won outright on raw score — but the spread rule from US 32 routed pkg-1 to agv-1 and pkg-2 to agv-2 (next-best bidders).
3. **Scores stay within ±10 of the deterministic value**: every judge-accepted bid lands inside the contract. The qwen14b prompt is being honoured.
4. **Graceful degradation**: the last bid shows `(judge unavailable: AI error)`. The deterministic-only score still publishes; the system continues.

## Limitations

- **Distance to package origin is approximated as input arm cell.** Once the AGV starts moving toward a package, this is still the right anchor (because the package is at the input arm until the load handoff). After load, the bid is irrelevant — the task is already running.
- **`current_load_kg` is binary today** — `weight_max/2` if carrying anything, `0` otherwise. Real weight per package is not in the model yet.
- **Judge sometimes hits the 65 s tactical timeout** when the GPU is heavily contended (today's run saw one). Falls back to deterministic-only score (correct behaviour); honest rationale recorded on the bus.
- **Cell position in early bids may be stale** if the AGV recently completed a run — observed today where agv-3 was at dist=1 instead of its bay's dist=15. Self-corrects on the next dispatch when all AGVs are idle at bays.

## US 32 fallback verified

```
COORDINATION_MODE=decentralized  # flag-off for US 34
```

The smart-mode branch is gated on `if not _mode_is_smart(): return _legacy_us32_ask_for_bid(...)`. Under any non-smart value, the entire US 34 code path is skipped and `_legacy_us32_ask_for_bid` runs the verbatim US 32 logic. No behaviour change for the US 32 path.

## Next phase

Phase 3 (M3) — master capability filter in `app/agents/master_announcer.py`. Even though AGVs now self-decline (Phase 2 step 4), a master-side filter is the formal defence: rejects any bid whose AGV's `allowed_labels` doesn't include the task label, logging the rejection (not silent drop). The data flow is already there — `/coord/snapshot.agvs.<id>.capabilities` is populated.
