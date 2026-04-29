# Qwen Works — 29 April 2026

> Working notes from the day I made Qwen actually drive the pipeline, not just decorate it.
> 2-minute read. Author: Haris.

---

## What this is about

Before today, my US 16 work shipped a Qwen brain that **looked** wired up but had a hidden bug: the task description never reached the LLM. Today I audited all of it, fixed 6 bugs, then added a new design where **Qwen picks one of 9 named scripts** instead of inventing angles. The result is a clean, demo-able AI dispatcher that is provably making decisions.

---

## Before vs After

| | Before today | After today |
|---|---|---|
| **What Qwen did** | invented an angle for every move | picks 1 of 3 named scripts per arm (9 total) |
| **What "pick"/"drop" meant** | just labels — arm rotated, no object moved | scripts actually relay real objects through the pipeline |
| **Visibility in UI** | nothing — no log, no button | log panel shows `🎬 Qwen chose: <script> — <reasoning>` per arm |
| **Reliability** | wrong half the time (model is 0.5 B) | safety net overrides bad picks, fuzzy-match fixes typos |
| **Demo path** | `Run Demo` (hardcoded) — Qwen not consulted | `Run All (1→2→3)` — full Qwen-driven pipeline visible step by step |

---

## Files changed (one line each)

### Added (new)

| File | Lines | What |
|---|---|---|
| `app/services/arm_scripts.py` | 303 | 9 named behaviours (3 per arm) + registry. The menu Qwen picks from. |
| `tests/test_arm_execution.py` | 160 | Unit tests: validation + 3 executor paths (happy / brain-fail / arm-timeout). |

### Modified (backend logic)

| File | What changed |
|---|---|
| `app/services/qwen_brain.py` | `ask_decision` now accepts an optional `user_message` — fixes Bug #1 where the task never reached Qwen. |
| `app/services/arm_brain.py` | Real prompt with task + state; output validation (clamp 0–180°, whitelist verb); decision cache; stop/numeric pre-filter; **new** `pick_script` method = fuzzy-match + safety net. |
| `app/services/arm_execution.py` | Removed duplicate `set_angle` write; added `noop` handling. |
| `app/routes/arm_routes.py` | New endpoints: `POST /run_script`, `GET /scripts/<id>`, `POST /objects/spawn`. Object-state helpers. |

### Modified (frontend)

| File | What changed |
|---|---|
| `app/templates/index.html` | New checkbox `[✓] Use Qwen brain`; new bar with **Run Arm 1/2/3 Role**, **Run All**, **Spawn**, **Reset**. |
| `app/static/js/simulation.js` | Handlers for `task_status`, `brain_decision`, `task_complete`, `script_chosen`. Functions: `sendTask`, `runRole`, `runAllRoles`, `spawnObject`, `resetWorld`. |
| `app/static/css/style.css` | Styles for the new task-bar + role-bar. |

### Modified (demo runner)

| File | What changed |
|---|---|
| `scripts/run_demo.py` | `--use-brain` flag + `USE_QWEN_BRAIN` env: pipeline routes every move through `/execute_task` instead of hardcoded angles. |
| `scripts/utils.py` | New `send_brain_task(host, arm_id, task)` HTTP helper. |
| `scripts/demo_runner_server.py` | `/run` accepts `{"use_brain": true}` so the UI checkbox flows through. |

### Personal docs (gitignored)

| File | Reason |
|---|---|
| `documentation/PROJECT_PROGRESS.md` | Weekly tracking — local only. |
| `documentation/PROJECT_FLOWCHART.md` | Architecture diagram — local only. |
| `documentation/README.md` | Index — local only. |

`.gitignore` updated; the three were `git rm --cached`'d so they stay on disk but never push.

---

## How it functions (the 4-step decision)

```
User clicks "Run Arm 1 Role"  (or Run All)
    │
    ▼  POST /run_script  {arm_id:1, task:"do your relay"}
[backend] count real objects per location ─► pass to brain
    │
    ▼
[ArmBrain.pick_script]
    1. ask Qwen with: arm geometry + 3-item menu + plain-English availability
    2. fuzzy-match Qwen's reply to the real menu          (typo guard)
    3. safety net: if source has obj → must pick relay_*  (override guard)
    4. return final script name
    │
    ▼  run_script(arm_id, name)
[arm_scripts.py]  the chosen Python function runs:
    rotate to source → mark obj "carried" → rotate to dest → mark obj at dest → rest
    │
    ▼  WebSocket events
[browser]  log panel: 🎬 Qwen chose / ▶ START / step / ■ DONE / ✅
           canvas: arm animates, orange Obj box follows the arm tip
```

---

## How to build & run

```bash
# from project root, first time or after pulling changes:
docker compose up -d --build

# wait ~10 s for all containers to be healthy:
docker compose ps

# open the UI:
http://localhost:8080

# follow Qwen decisions in a terminal:
docker compose logs -f backend | grep -E "🎬|🤖|⚖|▶|■"
```

Stop / restart:

```bash
docker compose down                # tear down everything
docker compose up -d               # bring up (uses cached images)
docker compose up -d --build       # rebuild after code change
```

---

## What each button does

| Button | Calls | What you see |
|---|---|---|
| **Run Demo** | `/demo/start` (hardcoded) | Old behaviour, fast. No Qwen unless `[✓]` ticked. |
| **`[✓] Use Qwen brain`** | flips a flag for Run Demo | Run Demo asks Qwen for every angle (slow but visible). |
| **Send Task (Qwen)** | `/execute_task` | One arm rotates once. Qwen picks the angle. No object handling. |
| **Run Arm 1 Role** | `/run_script` for arm 1 | Qwen picks 1 of arm 1's 3 scripts; the script runs. |
| **Run Arm 2 Role** | `/run_script` for arm 2 | Same, arm 2. |
| **Run Arm 3 Role** | `/run_script` for arm 3 | Same, arm 3. |
| **Run All (1→2→3)** | reset + spawn + arm 1 + arm 2 + arm 3 | Self-contained Qwen demo, 1 object end-to-end. |
| **Spawn obj at intake** | `/objects/spawn` | Adds Obj1, Obj2, … at intake. |
| **Reset** | `/objects/reset` + park arms | Clean slate. |

---

## Why this design

`qwen2.5:0.5b` is small (397 MB) and unreliable at inventing angles or following nuanced rules. So:

- **Don't ask it to invent.** Give it a closed menu of 3 options.
- **Don't trust it blindly.** Fuzzy-match its output and override when it contradicts the data.
- **Show the work.** Every decision (and every override) appears in the log panel.

The result is a demo where the AI *visibly* makes a meaningful choice, the rule layer guarantees correctness, and the operator sees both.

---

## Tests & verification

`pytest==8.3.3` is in `requirements.txt` and already installed inside the
backend container. No local Python setup is required — run everything via
`docker compose exec`.

### A. Automated unit tests (committed in `tests/`)

```bash
# run the 9 new tests for the brain + executor
docker compose exec backend pytest tests/test_arm_execution.py -v

# run the entire suite (includes US 16 + earlier tests)
docker compose exec backend pytest tests/ -v
```

**Where the results appear:** directly in your terminal as standard pytest
output (`PASSED` / `FAILED` lines + a summary). Exit code is 0 on pass,
non-zero on failure — usable in CI.

**Latest run (29 April 2026):**

```
tests/test_arm_execution.py ..... 9 passed
tests/test_arm_routes.py     ..... 12 passed
tests/test_arm_state.py      ..... 15 passed
tests/test_concurrent.py     ..... 1 passed
============= 37 passed in 0.33s =============
```

The 9 new tests in `tests/test_arm_execution.py`:

| # | Test | What it verifies |
|---|---|---|
| 1 | `test_clamps_angle_above_180` | LLM angle 999 → 180 |
| 2 | `test_clamps_angle_below_zero` | LLM angle -45 → 0 |
| 3 | `test_unknown_action_falls_back_to_move` | unknown verb → `move` |
| 4 | `test_missing_confidence_defaults_to_half` | missing `confidence` → 0.5 |
| 5 | `test_string_angle_is_coerced` | `"47.5"` → 47.5 |
| 6 | `test_failure_dict_passes_through` | failed-status dict not mangled |
| 7 | `test_returns_success_when_brain_and_arm_both_ok` | happy path end-to-end (mocked) |
| 8 | `test_returns_failed_without_calling_arm_server` | brain fail → arm never POSTed |
| 9 | `test_returns_failed_when_arm_server_times_out` | brain ok + arm timeout → failure |

### B. Manual smoke checks (run by hand against the live stack)

For demos and "is the system actually working?" questions. Run after
`docker compose up -d --build`. **Where the results appear:** in the
terminal (curl output) AND in the browser at <http://localhost:8080>
(canvas animation + log panel).

```bash
# 1. single-object pipeline — Obj1 should end at "output"
curl -X POST localhost:5000/objects/reset
curl -X POST localhost:5000/objects/spawn -d '{}' -H 'Content-Type: application/json'
for id in 1 2 3; do
  curl -X POST localhost:5000/run_script \
    -d "{\"arm_id\":$id,\"task\":\"do your relay\"}" \
    -H 'Content-Type: application/json'
done
curl localhost:5000/objects     # expect: Obj1 → "output"

# 2. empty pipeline — all arms must pick hold_*, no ghost objects
curl -X POST localhost:5000/objects/reset
for id in 1 2 3; do
  curl -X POST localhost:5000/run_script \
    -d "{\"arm_id\":$id,\"task\":\"do your relay\"}" \
    -H 'Content-Type: application/json'
done

# 3. Qwen offline — system must fail closed (HTTP 500, arm doesn't move)
docker compose kill ollama
curl -X POST localhost:5000/run_script \
  -d '{"arm_id":1,"task":"do your relay"}' \
  -H 'Content-Type: application/json'
docker compose start ollama

# 4. The browser demo — open http://localhost:8080 and click:
#    Reset → Spawn obj at intake → Run All (1→2→3)
#    Watch the log panel and canvas.
```

All three curl checks return JSON with `"status": "success"` (or
`"failed"` in case 3) and the live status of objects/arms updates in the
browser canvas in real time.

Ready for the team review and the professor demo.
