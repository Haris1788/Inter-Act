# Plan B — 7-day sprint (April 2 to April 9)

6 people, 3 pairs. We're starting a day late. The demo is still Wednesday April 9th, so everyone carries a bit more per day.

---

## Team

| Pair | Who | What they own |
|------|-----|--------------|
| Frontend | Montu + Amir | Canvas scene, arm drawing, animation |
| Backend | Sneh + Purvil | Flask API, arm state, endpoints |
| Infra + LLM | Haris + Reza | Docker, GitHub, project scaffold, LLM research/prototype |

---

## Git branching strategy

```
main                  ← protected, only updated at end of sprint (production-ready)
  └── dev             ← integration branch, all pairs merge here
        ├── dev-montu     ← Montu commits here (frontend: canvas scene)
        ├── dev-amir      ← Amir commits here (frontend: arm animation)
        ├── dev-sneh      ← Sneh commits here (backend: arm state)
        ├── dev-purvil    ← Purvil commits here (backend: API endpoints)
        ├── dev-haris     ← Haris commits here (infra: Docker, GitHub, scaffold)
        └── dev-reza      ← Reza commits here (LLM: research, prototype)
```

Rules:
- Everyone works on their own branch (`dev-yourname`). No one touches another person's branch.
- When your feature is done, open a PR from `dev-yourname` into `dev`. Haris reviews and merges.
- `dev` is the shared integration branch. It should always be runnable. If your merge breaks it, you fix it.
- `main` stays untouched until the end of the sprint. On demo day, Haris merges `dev` into `main` after everything passes.
- Never push directly to `main` or `dev`. Always use a PR.

Haris sets up the repo today with `main` (protected) and `dev` branches. Then each person creates their own `dev-name` branch from `dev`.

---

## Wednesday April 2 — kickoff (today)

Everyone gets their task and materials today and starts immediately.

Montu and Amir get:
- A wireframe sketch of the UI
- Pixel values: canvas auto-sizes from config, arm pivots evenly spaced, length 150px for 3 arms
- MDN Canvas tutorial link
- The angle math: `tipX = pivot_x + length * cos(angle)`, `tipY = pivot_y - length * sin(angle)`

Sneh and Purvil get:
- A blank Flask project structure
- The rules: arm_id must be valid. Angle is 0 to 180. Everything else returns 400.
- A curl command to test with: `curl -X POST localhost:5000/set_angle -d '{"arm_id":1,"angle":45}'`

Haris and Reza get:
- Haris sets up the GitHub repo with branches by end of today
- Reza gets Docker Desktop installed and a Claude or OpenAI API key

Everyone pushes their first commit by Thursday evening.

---

### Montu — static scene + responsive layout (2 days)

> As a user, I want to see a simulation canvas with colored reach zones, handoff zones, and intake/output areas that scales to my screen.

Build:

1. `index.html` with a `<canvas>` element inside a wrapper div
2. `style.css` with responsive layout (canvas fills width, maintains aspect ratio)
3. JS that draws dashed circles at arm positions, orange handoff rectangles, gray intake zone, green output zone, labels

Done when: open the HTML file, scene looks like the wireframe. Looks fine on a laptop and a wide monitor.

Tip: draw the background into an offscreen canvas once. Composite it each frame.

No time for perfectionism. Get the zones and circles right, the labels can be rough.

---

### Amir — arm drawing + animation (3 days, start immediately)

> As a user, I want to see arms that animate smoothly to any angle between 0 and 180.

Build:

1. `drawArm(arm, angleDeg)` — line from pivot to tip, circle at each end
2. `renderFrame()` — clears canvas, draws background, draws all arms (loop through config)
3. `requestAnimationFrame` loop
4. Time-based easing: `easeOutCubic(t) = 1 - (1-t)^3` over 600ms

Done when: change `targetAngles[1] = 45` in browser console, arm 1 glides to 45 degrees. All arms drawn with correct colors.

Hardest frontend task. If easing is taking too long, skip it and use instant angle changes. Correctly positioned arms that draw from a config array is the minimum. Easing is a bonus.

Amir should pair with Montu from the start — they work on the same JS file, just different functions.

---

### Sneh — arm state module + scene config (1.5 days, start today)

> As a developer, I want a Python module that stores arm angles with validation, and all layout constants in one file.

Build both in one push:

1. `arm_state.py` — `_state` dict, `set_angle()`, `get_state()`, `get_all()`, `reset()`, `threading.Lock`
2. `scene_config.py` — canvas size, arm positions, zone positions, `get_full_config()`

Done when:

```python
>>> from app.models.arm_state import set_angle, get_all
>>> set_angle(1, 45.0)
True
>>> set_angle(1, 200.0)  # raises ValueError
```

This used to be split across two people. Now Sneh does both because we lost a day. Should take 3-4 hours total. After finishing, jump to helping Purvil with endpoints.

---

### Purvil — API endpoints (2 days, start today with mock state)

> As a client, I want REST endpoints to move arms and read their state.

Build:

1. `POST /set_angle` — validates `{arm_id, angle}`, updates state, returns 200 or 400
2. `GET /state` — all arm states
3. `GET /state/<id>` — single arm
4. `GET /config` — scene layout constants

Start immediately. If Sneh's state module isn't ready yet, mock it:

```python
# Temporary mock — replace when real module is ready
_state = {1: {"arm_id": 1, "angle": 90.0, "status": "idle"}}
```

Done when:

```bash
curl -X POST localhost:5000/set_angle -H "Content-Type: application/json" -d '{"arm_id":1,"angle":45}'
# {"status": "ok", "arm_id": 1, "angle": 45.0}
```

Once Sneh's module is done, swap the mock for the real import. Should be a one-line change.

---

### Haris — project structure + Docker + GitHub + tests (start today, run all week)

> As a team, we need a repo, a running Flask app, Docker, and tests — all by Thursday.

This is more work than before because we compressed the timeline. Prioritize in this order:

Day 1 (today):

1. Create GitHub repo with branches: `frontend`, `backend`, `infra`
2. `app/__init__.py` — Flask factory, SocketIO init
3. `run.py` — entry point
4. `page_routes.py` — serves `index.html`, `/health`
5. `requirements.txt` with pinned versions
6. `.gitignore`, basic `README.md`

Day 2-3: 7. `Dockerfile` — python:3.11-slim, install requirements, expose 5000 8. `docker-compose.yml` — backend service with health check 9. SocketIO wiring: `events.py` with connect/disconnect, emit `state_update` on `/set_angle`

Day 4-5: 10. Write 15+ tests: `test_arm_state.py`, `test_arm_routes.py`, `conftest.py` 11. Merge all branches on Monday

Done when: `git clone` + `pip install` + `python run.py` works, `docker compose up --build` works, `pytest tests/ -v` shows 15+ passing.

Haris is the repo owner. He merges PRs and resolves conflicts. If frontend or backend fall behind, Haris helps wire things together.

---

### Reza — LLM research + prototype (start today, 5 days)

> As a team, we need to understand how the LLM will control arms, and have a working proof of concept.

Background (what you need to know before writing any code):

The LLM in this project is a spatial planner. It does not control arms in real time. It receives a description of the current arm state and a task, then outputs a JSON plan of sequential movement steps. Python validates every step before execution. The LLM is never trusted blindly.

The project has three phases. Phase 1 (what the rest of the team builds this week) is scripted arm control via API. Phase 2 (your territory) is LLM-planned arm control. Phase 3 (later, maybe after May) is multi-agent coordination where each arm has its own LLM agent and they negotiate peer-to-peer.

The LLM strategy is system prompt injection. All the arm config (positions, reach zones, angle rules) fits in about 40 lines. That goes directly into the system prompt. No RAG, no vector database, no fine-tuning. The knowledge base is small enough that putting it in the prompt is faster, cheaper, and more reliable than any retrieval system.

What the LLM needs to know:

- There are 3 arms. Each has a pivot point, a length (150px), and an angle range (0-180 degrees)
- 0 degrees = arm points right, 90 degrees = arm points up, 180 degrees = arm points left
- Each arm can only reach objects within its reach circle
- Arms hand objects off at handoff zones between adjacent reach circles
- To move an object from intake to output: arm 1 picks from intake (180 deg), carries to handoff 1 (0 deg), arm 2 picks from handoff 1 (180 deg), carries to handoff 2 (0 deg), arm 3 picks from handoff 2 (180 deg), carries to output (0 deg)

What the LLM outputs:

- A JSON array of movement steps: `[{"arm_id": 1, "angle": 180}, {"arm_id": 1, "angle": 0}, ...]`
- Each step moves one arm to one angle
- Steps are executed sequentially with timing delays between them

Day 1-2: System prompt design

- Write a system prompt that includes the arm positions, angle convention, and output rules
- Test it manually in the Claude or ChatGPT web UI (paste the prompt, type a task, see if the JSON makes sense)
- Iterate until it reliably returns valid plans for simple tasks like "Move Arm 1 to 45 degrees"

Day 3-4: Build prototype script

- `scripts/llm_prototype.py` — calls Claude API (or OpenAI) with the system prompt
- Send task: "Move Arm 1 to 45 degrees"
- Parse the JSON response
- Add basic validation: is each arm_id valid? is each angle 0-180?
- Test with 5 different tasks, note how many return valid plans

Day 5: Document findings

- Which LLM provider and model you chose, and why
- API key setup (env variable `LLM_API_KEY`, never hardcoded)
- Approximate cost per planning call
- Success rate across test tasks
- Common failure modes (does the LLM invent arm IDs? output angles beyond 180? add explanation text instead of pure JSON?)

System prompt to start from:

```
You are a robotic arm movement planner. You plan movements for 3 robotic arms on a 2D canvas.

ARM POSITIONS:
- Arm 1: pivot at x=200, y=300, length 150px
- Arm 2: pivot at x=500, y=300, length 150px
- Arm 3: pivot at x=800, y=300, length 150px

ANGLE RULES:
- 0 degrees = arm points RIGHT (toward output)
- 90 degrees = arm points UP (rest position)
- 180 degrees = arm points LEFT (toward intake)
- Valid range: 0 to 180 degrees. Never output angles outside this range.

WORKSPACE:
- Intake zone is to the left of Arm 1. Arm 1 reaches it at 180 degrees.
- Handoff zone 1 is between Arm 1 and Arm 2. Arm 1 reaches it at 0 degrees. Arm 2 reaches it at 180 degrees.
- Handoff zone 2 is between Arm 2 and Arm 3. Arm 2 reaches it at 0 degrees. Arm 3 reaches it at 180 degrees.
- Output zone is to the right of Arm 3. Arm 3 reaches it at 0 degrees.

OUTPUT FORMAT:
Respond with ONLY a JSON array of movement steps. No explanation, no markdown, no comments.
Each step: {"arm_id": <number>, "angle": <number>}

Example for "Move Arm 1 to 45 degrees":
[{"arm_id": 1, "angle": 45}]

Example for "Move object from intake to handoff zone 1":
[{"arm_id": 1, "angle": 180}, {"arm_id": 1, "angle": 0}]
```

Done when: run the script, get back valid JSON, and the plan makes physical sense for the given task.

---

## Tuesday April 8 — integration day

Haris merges all branches. Everyone helps.

Morning:

1. Merge frontend + backend into main
2. Amir's JS reads initial angles from `/config` on page load
3. Purvil's `/set_angle` triggers SocketIO broadcast (if ready) or page refresh

Afternoon:
4. Smoke test: start Flask, open browser, curl a `/set_angle`, see the arm move
5. `docker compose up --build` — verify it works in Docker
6. `pytest tests/ -v` — all tests pass
7. Reza runs the LLM prototype for the team

If SocketIO isn't wired: the minimum is "curl changes the angle, refresh shows it." Real time updates come next week.

If the LLM prototype only works as a terminal script that prints JSON: that's enough. The team sees it works.

---

## Wednesday April 9 — demo

| Who | Shows |
|-----|-------|
| Montu | The simulation scene: zones, labels, responsive layout |
| Amir | Arms animating with easing (or at least correct positioning) |
| Sneh | State module + scene config validating in a Python shell |
| Purvil | curl setting angles and reading state back |
| Haris | Docker running, project structure, tests passing, GitHub repo |
| Reza | LLM prototype: type a task, get a JSON movement plan |

## Why the LLM work starts with the prompt, not code

The LLM integration depends on getting the system prompt right. If you jump to code first, the LLM returns garbage and you spend hours debugging Python when the problem is the prompt.

Reza spends the first two days writing and testing the system prompt manually (paste it into Claude or ChatGPT, type a task, check the JSON output). Only after the prompt reliably produces valid plans does the Python code get written. The prototype script is just a wrapper around a working prompt.

Day 2 is prompt testing in the web UI, not code. Only after the prompt works manually does the code get written.

---

## What could go wrong

| Risk                                     | What happens                                                               |
| ---------------------------------------- | -------------------------------------------------------------------------- |
| Amir can't get easing working | Fall back to instant angle changes. Easing is a bonus. |
| Sneh takes longer than expected | Purvil keeps building with mock state, swaps later. |
| Docker won't install on someone's laptop | They run locally with `python run.py`. Docker is Haris's job. |
| LLM returns invalid JSON | Reza iterates on the prompt. The prototype doesn't need to be perfect. |
| Integration fails on Tuesday | Haris wires it. The demo works as separate pieces if needed. |
| Someone doesn't show up                  | Pairs cover for each other.                                                |

---

## What comes next

If this sprint goes well, the next sprint adds:

- SocketIO wiring (if not done this week)
- Object tracking (Obj1, Obj2 moving through the pipeline)
- LLM prototype wired into Flask as `/plan` endpoint
- Arm scripts for CLI control
- Docker Compose with multiple containers
- Pipeline demo with concurrent arms

That's next week. This week: get the foundation solid despite the late start.
