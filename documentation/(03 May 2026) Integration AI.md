# (03 May 2026) Integration AI

> Working notes from the day the AI brain layer met the multi-asset pipeline.
> 5-minute read.

---

## What this is about

Two roads. They didn't meet. Today they do.

- One road had a Qwen brain wired to 3 arms with a closed 9-script menu (the AI demo).
- The other road had a 5-arm + 1-shuttle + 1-conveyor pipeline running through a common `Module` abstraction (the auto-pipeline).

Today's work merged them into a single branch (`feature/Integration-AI-on-multi-assets`) and rebuilt the AI layer to be:

- Generic across any number of arms (config-driven from `pipeline.yaml`).
- Aware of intermediate assets — arms refuse to relay when the shuttle / belt are dead.
- Aware of asset health both upstream (chain) and immediately downstream.
- Fast on idle polls (no Qwen call when the answer is forced).
- Surfaced through a `Run All` button that fires N parallel actor loops — every actor watches its own input independently.

---

## Before vs After

| | Before today | After today |
|---|---|---|
| **Pipeline width** | 3 arms only | 5 arms + 1 shuttle + 1 conveyor (config-driven) |
| **AI scope** | Hardcoded for arms 1/2/3 | Generic — derived from `pipeline.yaml` at runtime |
| **Asset decisions** | n/a | Shuttle + conveyor have their own (deterministic) mini-brain |
| **Coordination** | One arm at a time, sequential | All actors run their own loop in parallel |
| **Spawn at intake (UI)** | Object visible immediately | Object visible immediately (fixed bug where new owner namespace was missing) |
| **Idle pipeline** | Spammed `▶ START / face / ■ DONE / ✅ Task completed` per actor every 1 s | Each actor logs ONE `🛌 idle: …` line then goes silent |
| **Kill shuttle / belt** | AI ignored asset health; objects "teleported" past dead assets | Brain forces hold; pipeline naturally pauses + resumes around the failure window |
| **Qwen on idle calls** | Called every poll (5–30 s on CPU) | Skipped — fast-path returns `hold` in ~0 ms when source empty / assets dead |
| **Run All semantics** | Single sequential pass | N parallel loops; each actor polls its own input until Stop |

---

## Files changed

### New / heavily rewritten

| File | Lines | What |
|---|---|---|
| [INTEGRATION.md](../INTEGRATION.md) | ~220 | Public-facing integration readme: before / after / build / run / what Qwen can do. |
| [app/services/arm_scripts.py](../app/services/arm_scripts.py) | ~360 | Rewritten as actor-aware (arms + assets), zones derived from full flow order. Adds shuttle / conveyor relay scripts that animate the asset. Hold scripts accept `quiet=True`. |

### Modified (backend logic)

| File | What changed |
|---|---|
| [app/services/arm_brain.py](../app/services/arm_brain.py) | `pick_script` derives per-arm geometry from `pipeline.yaml` (no more `per_arm_facts` dict). New `_dead_intermediates()` checks both upstream chain AND immediate downstream actor. New fast-path: skip Qwen entirely when source empty / assets dead. |
| [app/routes/arm_routes.py](../app/routes/arm_routes.py) | `/run_script` accepts `actor_id` (string for assets) plus legacy `arm_id` (int). `_pick_for_asset` deterministic mini-brain for shuttle / conveyor. New `/topology` endpoint. New `/actor_scripts/<id>` endpoint. `/objects/spawn` emits new owner namespace. Per-actor `_actor_last_decision` memory suppresses repeat-idle log spam. |
| [scripts/demo_runner_server.py](../scripts/demo_runner_server.py) | Parked the `use_brain` pass-through (kept the arg in the wrapper but no longer forwarded into `run_pipeline()`). |
| [docker-compose.yml](../docker-compose.yml) | Regenerated via `python scripts/generate_compose.py` for 5 arms. |

### Modified (frontend)

| File | What changed |
|---|---|
| [app/templates/index.html](../app/templates/index.html) | New `#ai-bar` markup with Reset / Spawn / Run All / actor buttons. Cache-busted to `?v=28`. |
| [app/static/js/ui/controls.js](../app/static/js/ui/controls.js) | AI-bar wiring, per-actor button rendering from `state.assets`, parallel loops in `Run All`, Run / Stop toggle, idle backoff per actor, per-actor busy state. |
| [app/static/js/core/socket.js](../app/static/js/core/socket.js) | `script_chosen` handler with honest labels: `🛌 idle` for rule fast-path, `🎬 Qwen chose` for actual LLM, `⚖️ Override →` for safety-net correction. Reads `actor_id` (back-compat with `arm_id`). |
| [app/static/css/style.css](../app/static/css/style.css) | `#ai-bar` block + status colours. |

---

## How it functions now (the 5-step cycle)

```text
[BROWSER]  click Run All (Qwen)
   │
   ▼  starts 7 async loops, one per actor (arm-1 … arm-5, veh-a, conv-1)
[FRONTEND]  for each actor in parallel:
   │     while not stopped:
   │       POST /run_script {arm_id|actor_id, task: "do your relay"}
   │       sleep (200 ms after a relay, up to 5 s after consecutive holds)
   │
   ▼
[BACKEND  /run_script]  ── arm_routes.py
   │  resolve actor_id → arm or asset
   │  build per-actor menu (3 scripts) + world-state snapshot
   │
   ▼
[ArmBrain.pick_script | _pick_for_asset]
   │  fast-path: source empty?              → hold (no Qwen)
   │  fast-path: intermediate asset dead?   → hold (no Qwen)
   │  otherwise:                            → ASK QWEN  (qwen2.5:0.5b, ~3-10 s)
   │  fuzzy-match menu → safety-net override → final script
   │
   ▼
[arm_routes.py]
   │  if final == last && hold_*:           → silent mode (no events emitted)
   │  else:                                 → emit script_chosen + log_message
   │  call run_script(actor_id, name, quiet=is_silent)
   │
   ▼
[arm_scripts.run_script]
   │  rotate arm / animate shuttle / shift conveyor slots
   │  emit object_update with new owner_type / owner_id namespace
   │  emit log_message per step (skipped if quiet=True)
   │
   ▼  HTTP 200
[BROWSER]
   │  state_update    → canvas eases arm to new angle
   │  object_update   → object position re-renders
   │  script_chosen   → log panel shows 🎬 / 🛌 / ⚖️ line (when not silent)
```

---

## Tests + verification

### Endpoint smoke tests

```bash
# Topology endpoint:
curl -s localhost:5000/topology
# → {"flow":["arm-1","arm-2","veh-a","conv-1","arm-3","arm-4","arm-5"]}

# Per-actor menu:
curl -s localhost:5000/actor_scripts/veh-a
# → {"actor_id":"veh-a","scripts":[{"name":"relay_on_veh-a_to_on_conv-1",…},
#                                  {"name":"hold_at_on_veh-a",…}]}

# Per-arm menu (5 arms, same shape):
for a in 1 2 3 4 5; do curl -s localhost:5000/scripts/$a | python -c \
  "import sys,json;d=json.load(sys.stdin);print(d['arm_id'],'->',[s['name'] for s in d['scripts']])"; done

# Fast-path latency: empty pipeline + arm-3 should return in ~1 s, not 5–30 s:
curl -s -X POST localhost:5000/objects/reset > /dev/null
time curl -s -X POST localhost:5000/run_script -H 'Content-Type: application/json' \
  -d '{"arm_id":3,"task":"do your relay"}'
# → real ~1.3 s, chosen: hold_at_handover_2, reasoning: "handover_2 is empty"

# Full pipeline through 7 actors:
curl -s -X POST localhost:5000/objects/reset > /dev/null
curl -s -X POST localhost:5000/objects/spawn -d '{}' -H 'Content-Type: application/json' > /dev/null
for a in '"arm_id":1' '"arm_id":2' '"actor_id":"veh-a"' '"actor_id":"conv-1"' \
         '"arm_id":3' '"arm_id":4' '"arm_id":5'; do
  curl -s -X POST localhost:5000/run_script -H 'Content-Type: application/json' \
    -d "{$a,\"task\":\"do your relay\"}" --max-time 60 \
    | python -c "import sys,json;d=json.load(sys.stdin);print('  →',d.get('chosen_script'),d.get('result',{}).get('status'))"
done
# → final state: Obj1 at output-zone
```

### Manual smoke checks (browser at <http://localhost:8080>)

1. `Reset` → world clears.
2. `Spawn obj` ×3 → three Obj's stack at intake (visible).
3. `Run All (Qwen)` → button turns red ("Stop"); arm-1 picks Obj1 while arm-2 picks Obj1 mid-air; loop spreads work in parallel; all 3 reach output.
4. While running, `Spawn obj` again → joins the line within ~1–5 s.
5. While running, `Kill Shuttle A` → arm-2 logs `⚖️ Override → hold_at_handover_1`; pipeline pauses around the gap.
6. `Recover Shuttle A` + `Recover Belt 1` → flow resumes; the stranded object gets picked up again.
7. After all objects reach output → each actor logs ONE `🛌 idle: hold_at_…` line; log panel goes silent.
8. `Stop` (the Run All button while looping) → loops exit cleanly; per-actor buttons re-enable.

---

## Why this design

A 0.5 B model is too small to invent angles, follow nuanced multi-step rules, or refuse on its own. The integration leans on three patterns to work around that:

1. **Closed menu of 3 scripts per actor.** Multiple choice, not essay. The model picks one name from a list.
2. **Deterministic safety net.** After Qwen replies, a rule layer overrides if Qwen contradicts the world (object available but Qwen picked rest, or no object but Qwen picked relay, or intermediate assets dead). The override is logged alongside the AI suggestion — both visible.
3. **Fast-path bypass.** When the answer is forced ("nothing to do" or "can't do it"), the brain returns the override answer directly without consulting the LLM. Idle polls cost ~1 s instead of 5–30 s.

Each actor reads only its own slice of the world — no actor messages another. Coordination emerges from shared state (object positions, asset health, transfer-point occupancy). This matches how real factory automation works.

---

## Known gaps (carried forward)

- The shuttle and conveyor have their own deterministic mini-brain (no Qwen). Adding LLM-driven shift decisions to the conveyor would require a per-object cache layer (the conveyor's continuous slot-shift loop ticks every 50 ms; Qwen takes 5–30 s).
- The AI manual demo path (`Run All (Qwen)`) and the auto-pipeline (`Run Demo`) are decoupled. Both update the canvas, but they should not run simultaneously — the AI demo writes object state directly while the auto-pipeline drives state through `Module` threads.
- Qwen reasonings are occasionally nonsensical (the 0.5 B model's writing quality). The safety net catches actual contradictions; nonsense reasoning is cosmetic. Bigger model (`qwen2.5:7b`) eliminates this — one env var change.

---

## Next user story

Size + weight constraints. Each actor declares `max_payload_kg` / `max_dim_cm`. Menu grows by one option (`reject_oversized`). Safety-net rule grows by one clause. Spawn dialog gets size + weight inputs. Estimated effort: ~1 day; no new files beyond maybe `app/models/object_attributes.py`.
