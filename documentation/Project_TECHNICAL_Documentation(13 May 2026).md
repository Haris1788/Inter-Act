# Inter-Act — Project Technical Documentation

> Author: Haris
> Branch of record: `dev-haris`
> Reporting period: project start (2026-04-02) → today (2026-05-13)
> Scope of this document: the AI / decision-engine layer of the system, which is the layer Haris owns. Multi-asset pipeline (5 arms + shuttle + conveyor) was contributed by Sneh on `dev-sneh` and is referenced here only where the AI layer integrates with it.

---

## 1. What this layer is

The AI layer is the "brain" of each industrial actor (robotic arm, shuttle, conveyor) in the Inter-Act simulation. Its job is to decide — given the current world state — which named behaviour (a "script") that actor should execute next.

Concretely the layer answers, at every poll:

> "I am actor X. The object at my upstream zone is A or absent. My downstream actor is healthy or dead. From the closed menu of 3 scripts available to me, which one do I run?"

A small open-source LLM (Qwen 2.5 0.5B, served by Ollama) is consulted for this choice. A deterministic rule layer always validates the LLM's output and can override it.

---

## 2. Architecture (logical view)

```
   ┌──────────────────────────────┐  ┌──────────────────────────────┐
   │  LLM LAYER (per actor)       │  │  RULE LAYER (everywhere)     │
   │  ArmBrain.pick_script        │  │  Source/intermediate checks, │
   │  • closed 3-item menu        │  │  override on contradiction.  │
   │  • fuzzy-match output        │  │  Runs even when LLM does.    │
   │  • per-actor cache           │  │  Short-circuits LLM call.    │
   └──────────────────────────────┘  └──────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────┐
   │  SCRIPT LAYER (the actual physical behaviour)                  │
   │  arm_scripts.py — generic _relay_arm / _hold_arm / etc.        │
   │  • derived from pipeline.yaml flow order                       │
   │  • emits state_update + object_update events                   │
   │  • hold accepts quiet=True for silent no-op                    │
   └─────────────────────────────────────────────────────────────────┘
```

Three layers, two control surfaces: the LLM proposes, the rule verifies, the script executes. Either of the first two layers can be the one that issues the final decision — the rule layer wins on contradiction.

---

## 3. Components delivered

| Component | File | What it does |
|---|---|---|
| **Ollama HTTP client** | `app/services/qwen_brain.py` | Talks to Ollama over `/api/generate`. Handles timeouts, structured JSON output, errors. |
| **Per-arm brain** | `app/services/arm_brain.py` | `pick_script` method — closed menu + safety net + cache. `decide_action` method — free-form angle picker (used by `/execute_task`). |
| **Script registry** | `app/services/arm_scripts.py` | All concrete behaviours (`relay_*`, `hold_*`, `return_to_rest`) built generically and registered from `pipeline.yaml` at module load. |
| **Arm executor** | `app/services/arm_execution.py` | Orchestrates a 6-step task lifecycle when `/execute_task` is called. |
| **Notifications** | `app/services/notifications.py` | WebSocket event emitter (`script_chosen`, `task_status`, `brain_decision`, `log_message`, etc.). |
| **HTTP endpoints** | `app/routes/arm_routes.py` | `POST /execute_task` (angle picker), `POST /run_script` (script picker), `GET /topology`, `GET /actor_scripts/<id>`, `POST /objects/spawn`. |
| **Behaviour spec** | `qwen_behavior.yaml` | The system prompt and 8 named test scenarios used to validate Qwen's decision-making. |
| **Tests** | `tests/test_arm_execution.py` | 9 unit tests: 6 against `_validate_decision`, 3 against the executor (happy / brain-fail / arm-timeout). |
| **Demo integration** | `scripts/run_demo.py` + UI checkbox `[ ] Use Qwen brain` | The `Run Demo` flow can now route every step through Qwen. |

---

## 4. Key technical decisions

### 4.1 Closed menu instead of free-form generation

`pick_script` gives the LLM **3 named options** and asks for one. A 0.5B model is much more reliable at "pick from a list" than at "produce a number." This is the central design choice that makes a tiny model usable for real decisions.

### 4.2 Rule layer as the safety net

The LLM is treated as untrusted input. Three layers of defence:

1. **Fast-path:** if the answer is forced (source empty, intermediate asset dead), the brain returns `hold_*` immediately without consulting Qwen. Saves the 3–10 s LLM round trip on idle polls.
2. **Fuzzy match:** if Qwen returns a typo or invented name, `difflib` snaps it to the closest valid menu entry.
3. **Override:** if Qwen contradicts the world ("relay" when source is empty, or "hold" when source has an object), the rule layer forces the correct choice. **Both** Qwen's suggestion and the rule's override are logged so the operator sees AI reasoning + safety guarantee.

### 4.3 Per-actor decision memory + idle de-dupe

Each actor remembers its last decision. A repeat `hold_*` for the same actor with no world change emits **no** events — pipeline polling continues but the UI stays quiet. Replaces an earlier spam stream of `▶ START / ■ DONE / ✅` every ~1 second.

### 4.4 Per-arm geometry from config

The original code had `per_arm_facts = {1: …, 2: …, 3: …}` hardcoded. After the integration with Sneh's multi-asset pipeline, that dictionary is gone — each arm reads its upstream / downstream zone names from `pipeline.yaml` at call time. Adding a sixth arm or a second shuttle is a config-only change.

### 4.5 Cache keyed by (arm_id, task, angle-bucket-15°)

The same task at the same approximate arm state pays the LLM cost once. Cold call: 3–10 s. Cache hit: ~80 ms (≈100× faster). 64-entry FIFO eviction per arm.

### 4.6 Regex pre-filter for stop and literal angles

The 0.5B model cannot reliably refuse motion when asked. A pre-filter strips `stop / halt / hold / wait / freeze / don't move / abort / cancel` into a `noop` before the LLM is consulted. Literal angle commands (`rotate to 47`) are likewise deterministic and skip the LLM. Improves both correctness and latency.

---

## 5. What works (verified behaviours)

- `pick from intake` vs `drop at output` produce different actions / angles.
- Cache hit returns in ~80 ms after a cold ~3–8 s call.
- `stop arm 3` → arm does not move (regex pre-filter).
- `rotate to 47` → arm at 47° in ≤ 100 ms, LLM not called.
- `rotate to 9999` → clamped to 180° (validation).
- `docker compose kill ollama` → next AI call returns HTTP 500 with structured error; arm stays put. Stop-intent and numeric paths still work without Ollama (fail-closed).
- `Run All (Qwen)` on the integrated 5-arm + shuttle + conveyor pipeline: 7 actors run in parallel, each polls its own input, all objects walk intake → output.
- Mid-run failure injection (`Kill Shuttle A`): pipeline naturally pauses around the failure window, `⚖️ Override` lines appear in the log, recovery resumes from the stranded object.
- Idle pipeline emits **one** `🛌 idle` line per actor then goes silent.

All of the above are reproducible with the cheat-sheet in `documentation/PROJECT_PROGRESS.md`.

---

## 6. Known limitations (honest)

| Limitation | Reason | Mitigation today |
|---|---|---|
| Multi-step plans in one click | 0.5B too small to chain reasoning | Sequencing lives in Python; each LLM call is atomic |
| Per-arm geometry for `decide_action` | Prompt has one global station table | `pick_script` injects it; `decide_action` is the open one |
| Honest confidence | Returns `1.0` ~90% of the time | Surfaced for transparency only; never gated on |
| Arm-to-arm negotiation | Each brain sees only its own state | `TransferPoint` (Python threading) handles handoffs |
| Stop interrupts in-flight movement | `stop` only acts at decision time | Cannot abort mid-cycle; needs `/demo/stop` |
| Shuttle / conveyor not Qwen-driven | Tick rate (50 ms) vs Qwen latency (3–10 s) | Deterministic mini-brain in `_pick_for_asset` |

---

## 7. Performance numbers

| Scenario | Latency |
|---|---|
| Cold Qwen call on CPU | 3–10 s |
| Cache hit | ~80 ms |
| Fast-path idle (source empty / asset dead) | ~1 s (HTTP round trip only) |
| Regex pre-filter (`stop`, literal angle) | < 5 ms |
| Full 3-arm Qwen-driven cycle (3 cold calls + animation) | ~30 s |
| Full 7-actor parallel run (3 objects) | ~50–70 s (was 30 s sequential for 1) |

---

## 8. Path to production-grade

1. **Swap model size:** `qwen2.5:0.5b` → `qwen2.5:7b`. One env var change in `docker-compose.yml`. ~10× memory, ~5× slower on CPU, ~5× faster on GPU. Removes most of the "limitations" above.
2. **GPU runtime:** add `gpus: all` to the Ollama service in compose. Cold call drops from 8 s to under 2 s.
3. **Confidence calibration:** log every (decision, override-or-not, outcome). After ~1000 events, fit a Platt scaler offline. Gate decisions on calibrated confidence.
4. **YAML role files:** externalise the script registry so non-engineers can add actor types.
5. **Inter-actor negotiation protocol:** once each brain refuses correctly, actors can negotiate handoffs over a small JSON protocol, removing `TransferPoint` from Python.

---

## 9. Commit trail (Haris on dev-haris)

| Commit | Date | What |
|---|---|---|
| `6206281` | 2026-04-16 | US 11 — Qwen Behaviour (Ollama HTTP client + behaviour YAML + 8-scenario tests) |
| `999cafb`, `55f9f55` | 2026-04-16 | Weekly documentation structure |
| `928cae7`, `da62163` | 2026-04-22 | US 7 — Qwen as Brains for Each Arm (setup + executor) |
| `5801f1d` | 2026-04-24 | US 16 — Complete implementation (per-arm `ArmBrain`, `/execute_task`, notifications) |
| `58af1f0` | 2026-04-29 | US 16 modified — 6 bug audits + fixes, validation, regex pre-filter, cache, demo integration, 9 unit tests |
| `0e4e822` | 2026-04-29 | Qwen as a 9-script dispatcher — closed-menu classification, fuzzy match, safety-net override, role-bar UI |
| `67c2eae`, `883ac30` | 2026-04-29/30 | Documentation update |
| `937e37b` | 2026-05-03 | Integration AI — 5 arms + shuttle + conveyor with parallel actor loops |
| `1dfaa86` | 2026-05-03 | Merge `dev-sneh` into `feature/Integration-AI-on-multi-assets` |
| `a838239` | 2026-05-03 | Work-log entry for the integration day |

End of technical documentation.
