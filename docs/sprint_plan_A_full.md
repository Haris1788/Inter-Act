# Plan A — Full roadmap (April 2 to May 7)

5 weeks, 6 people.

---

## Team

| Pair | Who | What they own |
|------|-----|--------------|
| Frontend | Montu + Amir | Canvas, arm animation, object rendering, UI controls |
| Backend | Sneh + Purvil | Flask API, state management, SocketIO emitters, arm scripts |
| Infra + LLM | Haris + Reza | Docker, GitHub, project structure, LLM integration, system prompts |

---

## Git branching strategy

```
main                  ← protected, only updated at end of sprint
  └── dev             ← integration branch, all pairs merge here via PR
        ├── dev-montu
        ├── dev-amir
        ├── dev-sneh
        ├── dev-purvil
        ├── dev-haris
        └── dev-reza
```

Everyone commits on `dev-yourname`. PRs go into `dev`. Haris reviews and merges. `main` only gets updated when `dev` is stable (end of each week or end of sprint).

---

## Week 1 — April 1 to April 4 (foundation)

Goal: backend stores arm state, frontend draws the scene, project is on GitHub and runs in Docker. LLM research starts.

### Montu — canvas background

> As a user, I want to see a simulation workspace with labeled zones.

- Gets: a wireframe sketch and pixel values
- Builds: `index.html`, `style.css`, JS that draws static background (reach circles, handoff zones, intake, output, labels)
- Done when: open the file, see colored circles and labeled zones

### Amir — draw one arm

> As a user, I want to see an arm on the canvas that points in the right direction when I change an angle variable.

- Builds: `drawArm(pivot_x, pivot_y, length, angle)` function
- Done when: change `angle = 45` in code, refresh, arm points correctly

### Sneh — arm state module

> As a developer, I want a Python module that stores arm angles with validation and thread safety.

- Builds: `arm_state.py` with `set_angle()`, `get_state()`, `get_all()`, `reset()`, threading.Lock
- Done when: works in a Python shell, rejects angles outside 0-180

### Purvil — scene config

> As a developer, I want all layout constants in one file.

- Builds: `scene_config.py` with canvas size, arm positions, zone positions, `get_full_config()`
- Done when: calling `get_full_config()` returns a valid dict

### Haris — project structure + Docker + GitHub

> As a team, we need a repo anyone can clone and run.

- Builds: `app/__init__.py`, `run.py`, `page_routes.py`, `Dockerfile`, `docker-compose.yml`, `README.md`, `.gitignore`
- Sets up GitHub repo with branches (frontend, backend, infra)
- Done when: `git clone` + `python run.py` works, `docker compose up --build` works

### Reza — LLM research + system prompt design

> As a team, we need to know how the LLM will plan arm movements.

- Understands: the LLM is a spatial planner that receives arm positions and a task, returns a JSON array of movement steps. It is not a real time controller. Python validates every output.
- Writes: a system prompt that includes arm positions (3 arms, pivots at x=200/500/800, length 150px), angle convention (0=right, 90=up, 180=left), and output format (JSON array of {arm_id, angle} steps)
- Builds: `scripts/llm_prototype.py` that calls Claude or GPT-4, sends a task, parses the JSON
- Documents: which LLM provider, API key setup (env var, never hardcoded), cost per call
- Done when: running the script returns valid JSON for "Move Arm 1 to 45 degrees"

---

## Week 2 — April 7 to April 11 (API + animation)

Goal: backend has working endpoints, frontend arms animate. LLM prototype improves.

### Montu — draw all arms

> As a user, I want to see all arms, each a different color, at their correct positions.

- Loop through arm configs, draw each
- Done when: all arms visible, correct colors, all at 90 degrees

### Amir — arm animation with easing

> As a user, I want arms to glide smoothly instead of jumping.

- Builds: requestAnimationFrame loop, easeOutCubic easing, 600ms duration
- Done when: change target angle in console, arm glides
- Hardest frontend task. Pair with Montu if stuck.

### Sneh — /set_angle endpoint

> As a client, I want to POST an angle and get 200 or 400 back.

- Builds: route handler with validation
- Done when: curl works

### Purvil — /state and /config endpoints

> As a browser, I want to GET arm states and scene layout.

- Builds: two GET routes
- Done when: `curl localhost:5000/state` returns arms

### Haris — tests + CI setup

> As a team, we need automated tests and a way to know when something breaks.

- Writes: 15+ pytest tests for state module and API
- Sets up: GitHub Actions to run tests on push (if time allows)
- Done when: `pytest tests/ -v` passes with 15+ tests

### Reza — LLM multi-step planning

> As a team, the LLM needs to plan multi-arm sequences, not just single moves.

- Extends the prototype: "Move object from intake to output" should return a full sequence (arm 1 picks up, carries, hands to arm 2, etc.)
- Tests: run 10 different task descriptions, check how many return valid plans
- Writes: validation logic that checks each step (is arm_id valid? is angle 0-180?)
- Documents: failure rate, common mistakes the LLM makes, prompt tweaks that helped
- Done when: 8 out of 10 tasks return valid multi-step plans

---

## Week 3 — April 14 to April 18 (real time connection)

Goal: frontend and backend talk via SocketIO. LLM plans can be executed via API.

### Montu — SocketIO client in JS

> As a user, I want the browser to update when arm angles change on the server.

- Builds: SocketIO connection, listen for `state_update`, feed to animation system
- Done when: POST `/set_angle` from terminal, browser arm moves without refresh

### Amir — object rendering

> As a user, I want to see labeled objects in zones.

- Builds: `getObjectPosition()`, `drawObject()`, dynamic stacking
- Done when: hardcode 3 objects in "intake" state, they appear stacked

### Sneh — SocketIO broadcasting

> As a browser, I want to receive events when arm angles change.

- Builds: `socketio.emit('state_update')` inside `/set_angle`
- Done when: browser console shows events arriving

### Purvil — object tracking endpoints

> As a pipeline runner, I want to track where objects are.

- Builds: `/object_event`, `/objects`, `/objects/reset` with thread-safe dict
- Done when: POST object events, GET `/objects` shows them

### Haris — arm scripts + utils

> As a user, I want to control each arm from the command line.

- Builds: `arm_1_script.py`, `arm_2_script.py`, `arm_3_script.py`, `utils.py` with `send_command()`
- Done when: `python scripts/arm_1_script.py --angle 45` moves arm 1 in browser

### Reza — LLM service module + /plan endpoint

> As a user, I want to type a task and get a plan back from the LLM.

- Moves prototype into Flask: `app/services/llm_service.py`
- Builds: `POST /plan` endpoint that takes `{"task": "Move object from intake to output"}` and returns the LLM's JSON plan
- Adds: input validation (reject empty tasks, validate LLM output before returning)
- Builds: `prompt_builder.py` that constructs the system prompt from live arm state (reads from `/state` and `/config`)
- Done when: `curl -X POST localhost:5000/plan -d '{"task":"Move Arm 1 to 45"}'` returns a valid plan

---

## Week 4 — April 21 to April 25 (pipeline demo)

Goal: click Run Demo and watch objects flow. LLM plans can be executed end to end.

### Montu — UI controls

> As a user, I want a Run Demo button, connection status, and a log panel.

- Builds: button handler, status dot (green/red), log panel with color coded messages
- Done when: click Run Demo, button says "Running...", logs appear

### Amir — object-follows-arm animation

> As a user, I want objects to ride arm tips when carried.

- Builds: carried state at arm tip, 200ms snap, 400ms zone easing
- Done when: object picks up from intake, follows arm, detaches at handoff

### Sneh — TransferPoint class

> As a pipeline runner, I need thread-safe handshake between arms.

- Builds: `TransferPoint` with `place()`, `wait_and_pick()`, `wait_until_empty()` using `threading.Condition`
- Done when: two threads hand off an object without races
- This is hard. Needs a walkthrough on Condition variables.

### Purvil — ArmController class

> As a pipeline runner, I need each arm to cycle: pick(180) -> carry(0) -> place -> retract(90).

- Builds: `ArmController` with `move_to()`, `emit_object()`, `_run_cycle()`
- Done when: one arm processes 3 objects in a thread

### Haris — pipeline orchestration + Docker multi-container

> As a user, I want `python scripts/run_demo.py --products 3` to run arms concurrently.

- Builds: `_run_pipeline_core()`, transfer points, arm threads
- Builds: `docker-compose.yml` with backend + frontend + demo-runner containers
- Done when: demo runs in Docker, 3 products flow through

### Reza — LLM plan execution + "Ask LLM" button

> As a user, I want to type a task in the browser and watch the LLM plan execute.

- Builds: `plan_executor.py` that takes the LLM's JSON plan and calls `/set_angle` for each step with timing delays
- Builds: a text input in the UI where you type a task, hit Enter, and the LLM plans + executes
- Adds: `plan_validator.py` that checks plans before execution (arm_id valid, angles in range, no impossible movements)
- Done when: type "Move Arm 1 from intake to handoff" in the browser, arm 1 moves

---

## Week 5 — April 28 to May 2 (containerization + polish)

Goal: everything runs in Docker. Full demo with LLM control works.

### Montu + Amir (together) — nginx frontend container + responsive polish

> As a deployer, I want the frontend in its own container.

- Build: `docker/Dockerfile.frontend`, `docker/nginx.conf`
- Polish: make sure the UI looks good on a projector for the demo
- Done when: nginx serves the page, SocketIO proxies through

### Sneh + Purvil (together) — demo runner container + arm containers

> As a deployer, I want the pipeline and each arm in separate containers.

- Build: `docker/Dockerfile.demo-runner`, `docker/Dockerfile.arm`, `scripts/arm_server.py`
- Build: `scripts/demo_runner_server.py`
- Done when: `docker compose up --build` starts all containers, Run Demo works

### Haris — Docker Compose full setup + generate_compose.py

> As a deployer, I want one command to start everything.

- Build: `docker-compose.yml` with all services (backend, frontend, demo-runner, N arm containers)
- Build: `scripts/generate_compose.py` that reads `pipeline.yaml` and generates the compose file
- Done when: edit pipeline.yaml to add an arm, run the generator, docker compose up, new arm appears

### Reza — LLM integration polish + demo script

> As a presenter, I want the LLM demo to work reliably for the review party.

- Hardens: error handling when LLM returns bad JSON, retry logic, timeout handling
- Builds: a demo script that runs through 3 prepared scenarios (single arm move, full pipeline, failure recovery)
- Tests: run the demo 10 times, note any failures, fix the prompts
- Writes: `docs/llm_integration.md` documenting the system prompt, the planning approach, known limitations
- Done when: the LLM demo works 9 out of 10 times without manual intervention

---

## May 5-7 — buffer

- Fix bugs from integration
- Practice the demo presentation
- Update documentation
- Get ready for Review Party

---

## Weekly summary by pair

| Week | Frontend (Montu + Amir) | Backend (Sneh + Purvil) | Infra+LLM (Haris + Reza) |
|------|------------------------|----------------------|---------------------------|
| 1 | Static scene + one arm drawing | State module + scene config | Docker, GitHub, LLM research + prototype |
| 2 | All arms + easing animation | /set_angle, /state, /config | Tests, CI, LLM multi-step planning |
| 3 | SocketIO client + object rendering | SocketIO emit + object tracking | Arm scripts, /plan endpoint, prompt builder |
| 4 | UI controls + object-follows-arm | TransferPoint + ArmController | Pipeline orchestration, LLM plan execution, Docker |
| 5 | Nginx container + polish | Demo runner + arm containers | Compose generator, LLM hardening, demo script |

---

## LLM integration roadmap (Haris + Reza)

This is the thread that runs through all 5 weeks for Haris and Reza. Each week builds on the last.

Week 1: Research. Understand what the LLM needs to know (arm positions, angle rules, workspace layout). Write a system prompt. Test it manually in Claude/ChatGPT. Build a standalone prototype script.

Week 2: Improve. Test with 10 tasks, fix the prompt, add validation. Figure out failure modes (does the LLM return angles outside 0-180? does it invent arm IDs that don't exist?).

Week 3: Integrate. Move the prototype into Flask as `/plan`. Build `prompt_builder.py` that reads live arm state so the LLM always knows current positions. The LLM stops being a standalone script and becomes part of the server.

Week 4: Execute. Build `plan_executor.py` that takes the LLM plan and actually moves the arms. Add a text input to the UI. The user types a task, the LLM plans, Python validates, and the arms move.

Week 5: Harden. Make it reliable for the demo. Error handling, retry, timeout. Test repeatedly. Write documentation.

The system prompt approach (from the strategic plan): inject arm positions, reach zones, and rules directly into the system prompt. No RAG, no vector database, no fine tuning. The knowledge base is 40 lines of config. Putting it in the prompt is faster, cheaper, and more reliable than any retrieval system.
