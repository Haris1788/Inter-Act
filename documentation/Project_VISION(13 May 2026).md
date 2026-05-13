# Inter-Act — Project Vision

> Author: Haris
> A first-person account of what I understood the project to be at each stage, how that understanding evolved, and where I think the project is heading after the 2026-05-08 direction change.
> Not a technical document. Not a flow document. This is the *why*.

---

## 1. What I understood at the start (early April 2026)

When the project was first described to me, I saw Inter-Act as a teaching simulator: a small industrial cell with three robotic arms, a Docker-based stack, and a frontend canvas to visualise an object being passed from intake to output. My initial impression was that the value would come from the **infrastructure**: how containers communicate, how the frontend animates state, how the demo runner orchestrates a sequence of arm movements.

At that point I did not yet see AI as the centre of the project. Qwen / Ollama were in the requirements but felt like a side feature — "we'll attach an LLM later." My mental model was *robots first, brains second*.

---

## 2. How my understanding changed (mid-April 2026 — US 11 and US 16)

The first AI user story (US 11 — "Qwen Behaviour") changed my view. Once Ollama was running inside the stack and Qwen was answering structured questions, I realised the actual interesting question of the project was not "can we build a pipeline?" but **"can a small open-source LLM make safe decisions inside a real control loop?"** The pipeline became the *substrate*; the AI became the *subject*.

Two weeks later, after US 7 and US 16 ("Qwen as Brains for Each Arm"), my view sharpened further. I wrote a per-arm brain that asked Qwen for an angle and drove the arm to it. It worked on simple cases and failed on subtle ones:

- I gave Qwen the instruction "stop the arm" and it moved the arm.
- I asked it to "rotate to 47" and it would think for 8 seconds.
- I gave it a state schema that did not match the prompt, and it confidently invented a position that did not exist.
- The "confidence" field in its reply was almost always 1.0, even when wrong.

This was the most important week of the project for me intellectually, because it forced a shift: I stopped thinking of Qwen as a "decision-maker" and started thinking of it as **untrusted suggested input** that must always pass through a rule layer.

---

## 3. The product owner's first re-direction (late April 2026)

Around US 16 the product owner asked for the AI's behaviour to be **visible** — not just present. The operator should see *what Qwen suggested*, *whether the rule overrode it*, *what was finally executed*. This was the first product-owner pivot, and it landed exactly on the lesson I had been learning: AI in a control loop is only useful if humans can see what it is doing and why.

I responded by:

- Replacing free-form generation with closed-menu classification (give Qwen three named scripts, ask which one).
- Building a fuzzy-match layer to absorb typos and invented names.
- Building a deterministic safety-net override that wins when Qwen contradicts the data.
- Logging *both* Qwen's suggestion and the rule's correction, side by side, so the operator sees the full chain.

This is the design I still believe in. It is the layer I am most willing to defend.

---

## 4. Sprint 4 (early May 2026) — the integration moment

Until early May, the AI layer (mine) and the multi-asset pipeline layer (Sneh's, five arms + shuttle + conveyor) were two separate worlds. I had a brain with no world; Sneh had a world with no brain. The integration sprint (week ending 2026-05-03) was where I understood the **project's centre of gravity**: it is not "AI" and it is not "infrastructure," it is the **interface between them**. Specifically: a brain that is generic across actors, a configuration file that defines the topology, parallel actor loops, and a safety net that knows about intermediate-asset health.

By the end of that week the system was running seven actors in parallel, each consulting its own brain, with the operator able to inject failures (kill a shuttle, kill a belt) and watch the pipeline pause and recover correctly. That was the high point of the project so far.

---

## 5. The 2026-05-08 direction change

In the Friday 2026-05-08 sprint review the product owner announced a major redirection.

**The new world:**

- 1 input arm at intake.
- 1 output arm at output.
- 3 AGVs (autonomous guided vehicles).
- The intake arm runs an AI that calculates **distance** to each AGV and the **speed** at which each is moving, and dispatches the **nearest available** AGV to take the object from input to output.

**Why I think the product owner asked for this:** the chain-of-arms-with-shuttle layout is a clean tutorial model, but it does not resemble a real warehouse or factory floor. Real environments use mobile robots whose dispatching is a non-trivial decision (which one is free? which one is closest? which one is fastest right now?). The AGV layout puts the AI on a richer decision surface — fewer actors, but each decision has more meaningful inputs (distance, velocity, availability) than "should arm-3 hold or relay."

**Why this felt like a shock at first.** Roughly a third of the project's time has been spent. Five arms, a shuttle, and a conveyor exist and work. The first review was done with the old world. Two more reviews remain (1st week of June and 1st week of July). On first hearing, this looked like throwing away one third of the work.

**Why I now think it is not.** On reflection, the *patterns* are what we built — not the *world*. Specifically:

- The closed-menu pattern for an LLM still applies: at the intake arm the menu becomes "AGV-1 / AGV-2 / AGV-3."
- The safety-net override still applies: if the LLM picks an AGV that is busy or dead, the rule layer corrects it.
- The fast-path still applies: if there is no object at intake, do not call the LLM.
- The per-actor parallel loop pattern still applies: each AGV has its own life cycle.
- The configuration-driven topology still applies: 3 AGVs today, 5 tomorrow, defined in a config file.

The world-builder code is partly obsolete. The AI patterns are not. The first third of the project produced **a library of patterns** that the next two thirds will spend re-applying to a more interesting decision surface.

---

## 6. What I understand the project to be *now*

After the 2026-05-08 pivot, my current understanding of Inter-Act is:

> Inter-Act is a study of **how a small open-source LLM can safely make dispatch and control decisions in a simulated industrial cell**, when the LLM's output is wrapped in a rule-based safety net, fed from a configuration-driven world model, and surfaced transparently to a human operator. The new AGV layout is the most realistic test case for that study yet.

The deliverables in the second two thirds of the project are not "build a new system." They are:

1. **Replace the chain world** (5 arms + shuttle + conveyor) with the **AGV world** (2 arms + 3 mobile vehicles).
2. **Add a dispatcher** at the intake arm: distance + speed + availability → choose AGV.
3. **Show that the AI patterns we built earlier survive** the new world unchanged. This is the strongest possible argument that the patterns are right.
4. **Demonstrate failure cases on AGVs** (vehicle breakdown, route blocked, all AGVs busy) — same override pattern, same logged transparency.
5. **Upgrade the model** (0.5B → 7B with GPU runtime if possible) so the dispatcher can use richer reasoning. The code path does not change; one environment variable does.

---

## 7. What concerns me, honestly

- **Time pressure.** Two reviews remain in roughly eight weeks. Rebuilding the world plus adding a dispatcher plus polishing the demo is achievable but not generous.
- **Scope of the dispatcher.** "Calculate distance and speed and pick the nearest AGV" is one sentence in a product owner's mouth and many decisions in code. We need clarification: is distance Euclidean? Manhattan? Along a route graph? Is speed instantaneous or average? How does the dispatcher know an AGV is available? These need to be answered before Sprint 6 begins.
- **The legacy demo.** The 5-arm + shuttle + conveyor integration is the most polished thing the team has produced. I would prefer to keep it as a "previous milestone" demo rather than delete it, so Review #2 has a working anchor while the new AGV world is still under construction.

---

## 8. Questions to the stakeholders

Each of the items below needs an answer before Sprint 6 work begins — a wrong assumption on any one of them costs roughly a week of rework.

### A — The physical world

1. **Is the existing 5-arm + shuttle + conveyor demo retired, or kept as a reference milestone?**
   A clear answer drives whether we freeze that branch or keep evolving it. Keeping it as a "previous milestone" gives Review #2 a working anchor while the new AGV world is still under construction or I say still in thoughts to work on.

2. **What coordinate system is the AGV floor expressed in?**
   The dispatcher AI cannot compute "distance to AGV" without one. Three options, each implying a different implementation:
   - **Cartesian (x, y) in metres** — distance is Euclidean. Simplest. Assumes free-floating motion.
   - **Grid (row, col) in cells** — distance is Manhattan or grid-shortest-path. Suits an aisle-based warehouse.
   - **Topological route graph (nodes + edges + travel times)** — distance is graph-shortest-path. Suits a floor where AGVs follow fixed lanes.
   The AGV state schema, the dispatcher's prompt, and the canvas rendering all depend on this choice.

3. **What is each AGV's physical model?**
   Speed range, acceleration, battery / capacity, turning radius (if any). The dispatcher needs at least: current position, current velocity, available capacity, and estimated time-to-free.

4. **Is 3 AGVs a fixed number or a starting point?**
   The closed-menu dispatching pattern works for any number, but the prompt size grows linearly with the AGV count. Knowing the upper bound now lets the prompt template scale correctly.

### B — Coordination between agents

1. **What level of AGV-to-AGV coordination is expected?**
   Two designs are possible and they are very different systems:
   - **Star (Master AI only).** The intake arm AI makes all dispatching decisions; AGVs are obedient executors. Simpler. Single point of failure.
   - **Peer-to-peer with a consensus algorithm.** AGVs negotiate among themselves who takes a job ("I am closer, but I am at 12 % battery — you take it"). Requires a lightweight consensus / mutual-exclusion algorithm (token passing, leader election, or a small Raft-style protocol) so that two AGVs never accept the same job. More robust, more interesting to demonstrate, but adds real distributed-systems engineering on top of the AI layer.

2. **Should the AI participate in the coordination protocol itself, or only at dispatch points?**
   This is the most important architectural question of the new phase. Our recommendation is to keep the AI **only at dispatch points** (Master AI → "which AGV?", AGV AI → "do I accept this job?") and make the **consensus and messaging layer deterministic Python**. The reasoning is below.

### C — Why Qwen 2.5 0.5B is not enough for peer-to-peer coordination

Even with a consensus algorithm wrapped around it, the 0.5 B model is the weak link in any AGV-to-AGV or Master-AI-to-AGV protocol. Concretely:

- **No spatial reasoning.** Given two distances in the prompt, 0.5 B cannot reliably say which is smaller. Distance comparison must live in Python — the LLM cannot be the arbiter.
- **No state across calls.** A negotiation ("I propose I take it" → "Counter: you are farther" → "Withdrawn, you take it") spans multiple calls. 0.5 B treats each call as fresh and routinely contradicts what it said two turns ago.
- **Cannot parse structured peer messages.** Given a JSON payload from another AGV, 0.5 B often misreads fields it has not seen verbatim. Peer messages need a deterministic parser, not an LLM.
- **Hallucinated confidence.** Peer protocols rely on trust signals (am I sure? defer if not). 0.5 B returns confidence 1.0 about 90 % of the time, even on wrong decisions. A consensus built on these signals would converge — *on the wrong answer*.
- **Latency.** A 3-step negotiation at 3–10 s per LLM call is 10–30 s of dead time per AGV pair. Mobile robots cannot wait for that.
- **Consensus does not save it.** Consensus algorithms guarantee *agreement* among voters, not *correctness*. If every voter is an unreliable 0.5 B model, the system agrees on bad answers reliably and quickly.

A bigger model (`qwen2.5:7b`) closes some gaps (multi-turn coherence, structured-message parsing, better instruction-following). It does **not** fix the spatial-reasoning gap — that always has to be Python. So the design we recommend is: **deterministic code for the consensus protocol and the physics; LLM for the dispatch decisions, where the menu is small and the value of judgement is high.**

---

## 9. Closing thought

Two months remain to Review #3. What we have already built — closed-menu classification, safety-net override, fast-path, parallel actor loops, config-driven topology, fail-closed behaviour — is the foundation. What we still need to build — the AGV abstraction, the dispatcher AI, the coordinate model, and (if asked for) a deterministic peer-to-peer consensus layer — is well-scoped and **doable in the eight weeks until Review #3**, provided the questions in Section 8 are answered in this review.

If the stakeholders see corrections, missing pieces, or a different framing of the project's purpose, this is the moment to surface them. The patterns we built were designed to be re-targeted; we would rather re-target them once toward a clear goal than three times toward shifting ones.

End of vision document.
