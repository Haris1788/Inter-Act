# UI Integration — Deferred from US 34 logic pass

> **This run is logic-only.** Frontend integration is deferred to a later pass.
> This file is the entry point for that future pass — captures what to reuse
> from the parallel UI work, and what to NOT pull now.

## Background: parallel UI work is alive and evolving

A recent (29 May 2026) commit on the parallel UI branch is **pure left-workspace-registry polish** — three files, ~62 lines added:

| File | Change |
|---|---|
| `frontend/src/chrome/LeftRegistry/LibSection.tsx` | Adds a `sub` prop for indented subsections (smaller font, indented header). Exports a new `LibGroupHeader` component for group headers above multiple subsections. |
| `frontend/src/chrome/LeftRegistry/index.tsx` | Restructures the left registry to use group headers + subsections (cleaner visual hierarchy). |
| `frontend/src/runtime/nodeFactory.ts` | Renames zone types: `"Source feed"` → `"Input Zone"`, `"Sink / outflow"` → `"Output Zone"`. |

Nothing backend. No actor logic. Pure visual organisation of the left-side palette.

## What to reuse later (do NOT pull now)

When the future integration pass happens, these primitives from the parallel UI are worth pulling **selectively**:

### `LibSection sub={true}` + `LibGroupHeader` → Inspector "Capabilities" section

The Inspector's right-side panel currently shows "Select an actor" placeholder. When wired (separate future US):

- Main "Capabilities" section using `LibSection` (label `CAPABILITIES`, count = `len(fields)`)
- Sub-sections (using `sub={true}`) for:
  - `allowed_labels` (chip list)
  - `weight_max_kg` (numeric)
  - `speed_factor` (numeric)
  - `bay_cell` (coordinate)

This matches the polished UI's visual hierarchy from the start — no separate styling needed.

### `LibGroupHeader` → bus-panel event grouping

Today the bus panel (`frontend/src/chrome/LogsPanel/BusPane.tsx`) only knows 4 event kinds: `announce`, `bid`, `accept`, `dock`. US 32 added 9 kinds: `task_announce`, `bid`, `claim`, `task_orphan`, `intent`, `arm_approach`, `arm_ready`, `agent_status`, `yield`. Future bus-panel extension can use `LibGroupHeader` to group them:

- **`CONTRACT-NET`** group: `task_announce`, `bid`, `claim`, `task_orphan`
- **`EXECUTION`** group: `intent`, `arm_approach`, `arm_ready`, `agent_status`
- **`ESCALATION`** group: `yield`, `help_request`

Plus the chip-style filter labels per kind (the parallel UI already has the styling).

### `"Input Zone"` / `"Output Zone"` vocabulary alignment

Our bus payloads use `arm_station: input_arm` and `arm_station: output_arm`. The frontend was renamed to use `"Input Zone"` / `"Output Zone"` instead of the older `"Source feed"` / `"Sink / outflow"`. Aligning the bus payload labels and the frontend display vocabulary is good housekeeping; should be a small mapping function during the integration pass.

## What NOT to pull (or only with care)

- The parallel UI's **reconciler, actor lifecycle management, deploy/materialize** logic is stable, productive work in its own right — out of scope for the US 34 integration; do not touch.
- The parallel UI is **still evolving** (the 29 May polish happened during my US 32 demo). At integration time, re-compare with the latest state — don't trust this snapshot as final.

## Files in the parallel UI that the future integration pass will touch

| File | Why |
|---|---|
| `frontend/src/chrome/Inspector/SectionCapabilities.tsx` | NEW — uses `LibSection` + `LibGroupHeader` patterns to display the per-AGV capability table from `GET /capabilities` (Phase 5 endpoint). |
| `frontend/src/chrome/Inspector/SectionRuntime.tsx` | EXTEND — surfaces current US 32 bus state per actor (current task, last bid score, last AI decision text). Reads from `/coord/snapshot` + recent `/bus/events`. |
| `frontend/src/chrome/Inspector/SectionBrain.tsx` | EXTEND — shows the actor's last `intent`, `arm_approach`, last `_decide_next_script` choice. |
| `frontend/src/chrome/LogsPanel/BusPane.tsx` | EXTEND — type map covers all 9 US 32 event kinds; toggle for "show heartbeats"; group headers per kind family. |
| `frontend/src/canvas/overlay/RouteLayer.tsx` | NEW (or existing) — draws the A\* polyline path for each running AGV (reads `route_to_input`, `route_to_output`, `route_to_bay` from `/grid` or `/coord/snapshot`). |
| `frontend/src/canvas/overlay/PackageHandoffLayer.tsx` | NEW — sprite that tracks the arm tip during load/unload sweep, then snaps onto the AGV cargo bed when `arm_ready loaded` arrives. |
| `frontend/src/runtime/socketDriver.ts` | Timezone conversion on incoming bus events; tz display in local time, not UTC. |

## Integration pass acceptance criteria (for the future US)

- Click an actor on the canvas → Inspector populates with its current task, capabilities, last AI decision, last bid score, last intent.
- Bus panel labels each event kind with a coloured chip; groups by kind family; toggle to show/hide heartbeats; live count of hidden heartbeats visible.
- Coordination panel uses `LibGroupHeader` for kind groupings.
- All timestamps display in operator's local timezone.
- Each running AGV's planned route is drawn as a polyline overlay.
- During load/unload, the package sprite visibly moves from table → arm tip → AGV cargo bed (or reverse).
- The single Taiga acceptance line **"at least one frontend element wired to US 32 bus events"** is satisfied by any one of these.

## Decision

**Integrate later, not now.** This US 34 logic pass leaves all frontend untouched. The future integration pass starts from this file as a checklist and re-confirms the parallel UI's then-current state before pulling anything.

## Tracked deferral

The Taiga acceptance line **"Integration plan with the polished frontend agreed; at least one frontend element wired to US 32 bus events"** is **NOT MET** by this logic pass. It is explicitly deferred to the future integration US and tracked here + in `docs/us34-learning/PROGRESS.md` + in `docs/coordination-us34-evidence.md` so it cannot be silently dropped.
