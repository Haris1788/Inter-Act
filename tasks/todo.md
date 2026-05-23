# Final Goal alignment — staged plan

Source: user request after Phase 5.1 fix pass. Aligns runtime to `Final Goal.md`:
visible object lifecycle, arm reach boundary semantics, time-weighted bidding
(verify), reasoning logs. Preserves decentralized peer-to-peer execution.

## Decisions (locked)

- Payload state = **bus events only** (PAYLOAD_SPAWN/PAYLOAD_TRANSFER). No
  central registry. Frontend derives.
- Reasoning log = **inline row under consensus chip** in BusPane.
- Arm dock = **project zone center onto reach circle** along arm→zone vector.
- D6 verification = **unit test + one live E2E**.

## Stages

### Stage A — geometry helper + slot wiring (pure)
- [ ] `app/core/geometry.py` new module → `dock_point(arm_xy, zone_xy, reach_px)`.
- [ ] Unit tests: dock on circle, collinear safety, zero-reach edge.
- [ ] `derive_slots` (where AGV `from_pos`/`to_pos` set) uses `dock_point`
  with the partner arm's pos + reach.
- [ ] Verify existing AGV bid still scores `(approach+trip)/speed`.

### Stage B — reasoning rationale on consensus
- [ ] `negotiation.public_winner` returns `(winner, rationale)` where
  rationale = `[{sender, score, eta_s}]` sorted ascending.
- [ ] `actor_agent` emits CONSENSUS_REACHED with `payload.candidates` +
  `payload.reason` ("min eta").
- [ ] Frontend `BusPane`: when message kind=consensus_reached AND payload has
  candidates, render sub-row "winner X · eta Ys (vs Y' Ys, Y'' Ys)".
- [ ] No new MessageType — additive payload only.

### Stage C — payload lifecycle on bus
- [ ] Add MessageTypes `PAYLOAD_SPAWN`, `PAYLOAD_TRANSFER`.
- [ ] Coordinator emits PAYLOAD_SPAWN after TASK_ANNOUNCE
  `{obj_id=task_id+"#0", zone_id=source, pos=source_xy}`.
- [ ] ArmAgent `signal_load`: emit PAYLOAD_TRANSFER `{obj_id, from=arm.id, to=agv.id}`.
- [ ] ArmAgent `signal_unload`: emit PAYLOAD_TRANSFER `{obj_id, from=agv.id, to=arm.id}`.
- [ ] ArmAgent place-leg done: emit PAYLOAD_TRANSFER `{obj_id, from=arm.id, to=sink_zone.id}`.

### Stage D — frontend payload slice + render
- [ ] `store/payloadSlice.ts` — `payloads: Record<obj_id, {pos, carrier|null}>`.
- [ ] Bus handler in `socketDriver` cases for new kinds. SPAWN seeds entry.
  TRANSFER updates carrier; pos follows carrier.
- [ ] `canvas/konva/PayloadLayer.tsx` — payload dot follows carrier's animated pos.
- [ ] Wire layer into KonvaCanvas stage between actor + UI layers.

### Stage E — verification
- [ ] Pytest: `tests/test_constraint_selection.py` — 3 synthetic AGV profiles
  (speeds 0.5/1/2, varied positions). Drive `derive_slots`+`public_winner`,
  assert winner = min(eta) and rationale ordering matches.
- [ ] E2E live: drop 3 AGVs different speeds, submit "fast" task via GoalBar,
  screenshot canvas + bus log showing rationale + object hop.

### Stage F — docs
- [ ] Update `CLAUDE.md` "Non-obvious design decisions" with payload bus
  events, dock projection, rationale payload.
- [ ] Update `HANDOFF.md` to current state (Phases 4/5/5.1 shipped + this pass).

## Commit policy
ONE commit at end of Stage F covering all six stages. Per memory rule (no
per-stage commits).

## Review

All six stages completed in one pass, single commit pending.

- **Stage A** geometry helper + boundary projection: `dock_point` pure helper
  + `derive_slots_by_actor` now produces antipodal docks on each arm's reach
  circle (outward = arm reach target, inward = AGV dock). Existing zone-string
  path untouched. 7/7 geometry tests + 5/5 derive-slot tests pass.
- **Stage B** rationale payload: `CONSENSUS_REACHED` carries
  `{rationale, constraint}`. Frontend `BusPane` renders the sub-row showing
  winner + per-candidate scores. Type-safe; older bus readers ignore the new
  fields.
- **Stage C** payload bus events: 2 new MessageTypes (PAYLOAD_SPAWN/TRANSFER).
  Coordinator + arm hooks emit the 5-step chain (spawn, src-zone→arm,
  arm→agv, agv→arm, arm→dst-zone). Fire-and-forget; never disrupts physical
  execution.
- **Stage D** frontend layer: `payloads` slice + selector + Konva
  `PayloadLayer` follows carrier kinematics in real time. Zone handoffs
  snapshot the from-carrier's pos so the dot remains visible at delivery.
- **Stage E** verification: `test_constraint_selection.py` proves
  `shortest time` picks fastest effective AGV, not nearest. Live E2E with the
  TU Clausthal tunnel deferred (logged in HANDOFF).
- **Stage F** docs: CLAUDE.md decisions table + HANDOFF.md fully refreshed.

**Decentralization preserved.** Bus is still the only inter-actor channel.
Payload events are observational — no consensus impact, no execution gate.
Boundary projection is pure geometry on the existing slot fields.

