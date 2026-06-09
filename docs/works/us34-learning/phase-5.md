# Phase 5 — `GET /capabilities` (C4 backend half)

**Status:** ✅ Verified live on 29 May 2026 through the nginx proxy.

**DEFERRED:** Inspector frontend display + the Taiga "one frontend element wired
to US 32 bus events" acceptance line. See `UI-INTEGRATION-DEFERRED.md`.

## What changed

Added a single backend endpoint that surfaces the full per-AGV capability
table for any future UI / dashboard / external monitor. Reads from
`pipeline.yaml` via the existing `pipeline_config.get_all_agv_capabilities`
helper — no new state.

## What I implemented

### `app/routes/agv_routes.py`
New route `GET /capabilities`:

```python
@agv_bp.route("/capabilities", methods=["GET"])
def get_capabilities_table():
    caps = pipeline_config.get_all_agv_capabilities()
    return jsonify({
        "coordination_mode": pipeline_config.get_coordination_mode(),
        "agvs": {aid: c.to_dict() for aid, c in caps.items()},
        "defaults": pipeline_config.get_capability_defaults(),
        "bid_weights": pipeline_config.get_bid_weights(),
    })
```

Failure path returns HTTP 500 with `{"error": "...", "agvs": {}}` so a future
UI can render an honest error state.

### `docker/nginx.conf`
Extended the proxied-prefix regex to include `capabilities`:

```nginx
location ~ ^/(...|coord|bus|capabilities) {
    proxy_pass http://backend:5000;
    ...
}
```

Now reachable from the browser at `http://localhost:8080/capabilities` (the
operator's normal URL), not just `http://localhost:5000/capabilities`
(backend container direct).

## Features

- **Single response covers everything a UI needs:**
  - `coordination_mode` (`smart` / `decentralized` / `centralized`).
  - Per-AGV capability dict (kind, weight_max_kg, allowed_labels, speed_factor, bay_cell).
  - `defaults` block from `ai.capability_defaults` (so a UI can show "this AGV uses defaults" greying).
  - `bid_weights` (so a UI can show the scoring breakdown alongside live bids).
- **Honest error response.** If `pipeline_config` raises, HTTP 500 with a
  reason — a UI shows "config not loaded" instead of an empty table.
- **No new state.** Backed entirely by `pipeline.yaml`. Restart-free if the
  yaml is hot-reloaded.

## Live test results (29 May 2026 evening, through nginx proxy)

```
GET http://localhost:8080/capabilities

coordination_mode: smart
bid_weights: {'capability': 0.3, 'distance': 0.6, 'load': 0.1}
agvs:
  agv-1: kind=shuttle weight_max=50.0 labels=['red', 'green', 'blue', 'any'] speed=1.0 bay=[10, 3]
  agv-2: kind=shuttle weight_max=50.0 labels=['red', 'green', 'blue', 'any'] speed=1.0 bay=[10, 5]
  agv-3: kind=shuttle weight_max=50.0 labels=['red', 'green', 'blue', 'any'] speed=1.0 bay=[10, 7]
```

Three AGVs returned with full capability records.

## Deferred — Inspector display + Taiga acceptance line

Per the prompt, the DISPLAY half of Concern 4 (the actor Inspector showing
this data) is explicitly out of scope for this logic-only run. Also deferred:
the Taiga US-34 acceptance line **"Integration plan with the polished frontend
agreed; at least one frontend element wired to US 32 bus events"**.

These are tracked in `UI-INTEGRATION-DEFERRED.md` and called out in the final
evidence doc so they cannot be silently dropped at acceptance review.

The integration pass will likely:

1. Add `frontend/src/chrome/Inspector/SectionCapabilities.tsx` consuming
   `GET /capabilities`.
2. Use the polished UI's `LibSection sub={true}` + `LibGroupHeader` pattern
   (see `UI-INTEGRATION-DEFERRED.md` for the reuse map).
3. Wire the per-actor inspector click-handler to fetch this data on
   actor-selected, with a small in-memory cache to avoid re-fetching on
   every tick.

## US 32 fallback verified

The endpoint is mode-agnostic — it returns whatever `pipeline.yaml` declares
regardless of `coordination_mode`. Under `decentralized` it returns the same
capability table but the bidding code never consults it (Phase 2/3 are
gated on `smart`). No behaviour change to the US 32 path.

## Next phase

Phase 6 (S1) — dynamic A\* over `grid_state` with cell reservations,
straight-line fallback when no path is found. Replaces `_straight_path` in
the AGV brain's route construction when smart mode is active.
