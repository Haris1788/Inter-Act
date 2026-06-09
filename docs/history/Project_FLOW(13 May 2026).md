# Inter-Act — Project Flow

> Author: Haris
> A non-technical week-by-week story of the project — what we set out to do, what happened each sprint, what was shown at each review.
> Intended audience: company, product owner, professor. No code references — pure timeline.

---

## 0. The big picture in one paragraph

Inter-Act is a simulation of a small industrial line: robotic arms pass an object from an intake station, through intermediate stations, to an output station. The project has two layers — an **infrastructure layer** (the simulated arms, containers, and the pipeline that moves objects) and an **AI layer** (a small language model that decides what each actor should do next, with a safety rule that overrides bad decisions). The team works in parallel on different branches and merges results at sprint boundaries.

---

## 1. Project timeline (high level)

```
   APR ───────────── MAY ───────────── JUN ─────────── JUL
   │                 │                  │                │
   ▲  Project start  ▲  Today           ▲  Review #2     ▲  Review #3
      (2026-04-02)      (2026-05-13)       (1st week     (1st week
                                            of June)       of July)
                       ▲
                       └── Review #1 already done.
                           ~33% of total project time has been spent.
```

---

## 2. Sprint by sprint

### Sprint 1 — Week ending 2026-04-12 — Project setup and foundation

- Team formed. Roles split: infrastructure , AI brain (Haris), and other contributor CI/CD and merges, on their own branches.
- Initial project skeleton committed: Docker stack with 3 arms, a backend, a frontend canvas, and a demo runner.
- First weekly documentation structure put in place.
- Branch model agreed: each contributor works on `dev-<name>`; integration happens via feature branches.

**Visible result:** a working Docker stack with 3 arms passing an object intake → output via a hardcoded auto-pipeline. No AI yet.

---

### Sprint 2 — Week ending 2026-04-19 — First language model in the loop

- Ollama service added to the Docker stack with Qwen 2.5 (0.5B) — the smallest practical LLM that can run on a laptop without a GPU.
- First AI user story (US 11 — "Qwen Behaviour") completed: the LLM is reachable from the backend, replies with structured JSON, and has a documented set of test scenarios.
- Weekly tracking document started — used at every review to answer the five core questions: *what / how / why / how to improve / what's next*.

**Visible result:** the team can ask Qwen a question from a script and get a typed answer back. Not yet wired to the arms.

---

### Sprint 3 — Week ending 2026-04-26 — Each arm gets a brain

- User Story 7 / 16 — "Qwen as Brains for Each Arm" — completed end to end.
- A per-arm "ArmBrain" was built: given an English task ("pick from intake", "drop at output"), the brain asks Qwen for an angle, validates the answer, then drives the arm to that angle through a 6-step lifecycle.
- A new endpoint `/execute_task` became the single entry point for any AI-driven movement.
- 9 unit tests added for the executor and the validation layer.

**Visible result:** the operator can type a free-form instruction in the UI and watch the arm rotate to the requested position, with the LLM's reasoning visible in the log panel.

**Review #1 (held in this window):** The first formal review with the product owner / company. Both layers were demonstrated: the 3-arm auto-pipeline (no AI) and the manual `/execute_task` flow (with AI). The stakeholders accepted the milestone.

---

### Sprint 4 — Week ending 2026-05-03 — Hardening + integration

Two parts to this sprint.

**Part A — hardening the AI layer.** A self-audit of the AI code surfaced six real bugs that would have embarrassed the team during a demo (the task description never actually reached the LLM; the prompt referenced fields the world did not have; bad LLM outputs were silently turned into fake movements; the demo path didn't even consult Qwen). All six were fixed. A regex pre-filter was added so that the LLM cannot be tricked into moving when the user says "stop." A per-arm cache was added so the same decision is paid for only once.

A second design pattern was introduced: instead of asking Qwen to **generate** an angle, ask it to **classify** which of three named scripts to run. A 0.5B model is far more reliable at "pick from a menu of three" than at "produce a number between 0 and 180." This became the design that all subsequent AI work follows.

**Part B — integration with the multi-asset pipeline.** While the AI layer was being built on `dev-haris`, the infrastructure layer on `dev-sneh` had expanded the world from 3 arms to **5 arms + 1 shuttle + 1 conveyor**. The two roads had not yet met. In this sprint they did:

- New changes merged in `dev-haris` and `dev-sneh` into it.
- The AI brain was made generic: instead of being hardcoded for arms 1, 2, 3, it now reads each arm's zone names from a configuration file at runtime. Adding a sixth arm or a second shuttle is a configuration change, not a code change.
- The brain learned to check the **health of intermediate assets** — an arm now refuses to relay an object when the shuttle or the conveyor in front of it is dead.
- Seven actors now run **in parallel** when the operator clicks `Run All (Qwen)` — each polls its own input independently. The pipeline no longer goes sequentially; arm-1 picks the next object from intake while arm-2 carries the previous one forward.

**Visible result:** a single click runs the full chain — five arms, one shuttle, one conveyor — with Qwen consulted for each arm's decision and a safety rule visibly overriding the LLM whenever the LLM contradicts the data.

---

### Sprint 5 — Week of 2026-05-08 — **New direction from the product owner**

In the Friday 2026-05-08 sprint review, the product owner introduced a major change of direction.

**The new direction:** the world is no longer "a chain of 5 arms with a shuttle and a conveyor in the middle." It is now:

- **1 input arm** at the intake station.
- **1 output arm** at the output station.
- **3 AGVs** (autonomous guided vehicles) moving between them.

The intake arm runs an AI that, on each incoming object, computes the **distance** to each available AGV and its **speed**, and dispatches the **nearest** AGV to pick the object up. The chosen AGV travels to the intake, takes the object, drives to the output station, and hands it to the output arm.

**Product owner explanation for this** the new layout is closer to a real warehouse / factory floor, where mobile robots — not fixed conveyors — are responsible for transport. It also exposes a richer set of AI decisions (which AGV to call, when, based on physical constraints) than the linear pipeline.

**What it changes for the project:** the AI layer's design (closed-menu classifier + safety net + cache) carries over. The infrastructure layer (containers, frontend canvas, brain endpoints) carries over. **What needs to be rebuilt** is the **world model**: the chain of 7 actors becomes 2 arms + 3 vehicles, with dispatching logic at the intake arm. The shuttle + conveyor work from `dev-sneh` becomes a reference implementation rather than the production world.

The work to-date — roughly 33% of the planned project time — is not lost. The patterns (closed menu, safety-net override, parallel actor loops, config-driven topology, fail-closed behaviour) were the deliverables of the first third. The remaining two-thirds is to re-target those patterns at the new AGV-based world.

---

## 3. What is still ahead

| When | What |
|---|---|
| Sprint 6 — Week of 2026-05-15 → 2026-05-22 | Build the AGV abstraction (position, speed, route, status). Add 3 AGV containers, modelled on the existing arm / asset module pattern. |
| Sprint 7 — Week of 2026-05-22 → 2026-05-29 | Build the intake-arm dispatcher: given an object at intake, compute distance to each AGV, pick the nearest available one, hand off. Reuse the closed-menu pattern (menu = AGV ids that are alive and idle). |
| Sprint 8 — Week of 2026-05-29 → 2026-06-05 | UI for the AGV world: replace the chain canvas with a floor view; show AGVs as moving entities; log AI decisions per dispatch. **Review #2 happens at end of this sprint.** |
| Sprint 9 — Week of 2026-06-05 → 2026-06-12 | Stress and failure cases: AGV breakdown mid-route, AGV battery / capacity model, intake arm under burst load. |
| Sprint 10 — Week of 2026-06-12 → 2026-06-19 | Bigger model upgrade evaluation (qwen2.5:0.5b → qwen2.5:7b) and GPU runtime; document the cost / quality trade-off. |
| Sprint 11 — Week of 2026-06-19 → 2026-06-26 | End-to-end demo polish, presentation deck, full system documentation. |
| Sprint 12 — Week of 2026-06-26 → 2026-07-03 | Final hardening + dry-run. |
| **Review #3** — 1st week of July | Final review party presentation and project final. |

---

## 4. Health of the project (Haris's reading)

- **What is healthy:** the AI design patterns are robust and survive the direction change. The team has a working integration habit (branch + merge + verify) and a weekly documentation rhythm.
- **What is at risk:** the AGV pivot is a significant scope shift announced after a third of the project is spent. The two remaining sprints to Review #2 are tight, given that the world model needs to be rebuilt and the dispatcher is a fresh user story.
- **What needs a decision from the stakeholders before Sprint 6:** physical scale of the floor (room size, AGV speeds), AGV count flexibility (is 3 fixed or a starting point?), and whether the existing 5-arm + shuttle + conveyor demo should remain as a "legacy showcase" or be retired.

End of project flow.
