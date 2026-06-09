# INTER.act — FDAS (Fully Decentralized Architecture System)

**Type a natural-language prompt; one AI plan is made; then the robots coordinate among themselves.** Each AGV and arm is an autonomous container that bids on packages by capability (colour, weight) and distance — and a **peer, not a central controller, decides the winner**. If an actor fails mid-task, capable survivors re-bid and finish the job. The backend plans once, hosts the message bus, and mirrors activity to the operator; it makes **no coordination decisions**.

---

## What it does

- **Plan once, then step out.** A 32B planner (`qwq:32b`) interprets the prompt (e.g. *"send a blue package, a green package and a yellow package"*) into per-package work and broadcasts it onto the bus. After that, the backend takes no further coordination decisions.
- **Decentralized winner-selection.** Every capable, free AGV computes one local score (distance + best-fit capacity) and bids; a **peer** (the lowest-id bidder) collects the bids and publishes the claim — leaderless and deterministic.
- **Capability-aware allocation.** A hard filter (colour, weight) decides who *may* bid; a best-fit term makes the tightest-capacity AGV win, and a heavier AGV takes a light job **only when the lighter ones are busy** (overflow).
- **Arms are AI peers.** Each arm runs its own `qwen2.5:14b` and decides its load/unload action, announcing the decision on the bus.
- **Peer fault recovery.** A crashed actor's heartbeat goes stale; a peer orphans the task and survivors re-bid and finish it — no central reassignment.
- **A\* routing** per AGV (around obstacles and peers), with per-AGV speed.
- **Config-driven scenarios.** The same engine runs **strict colour-routing** or **colour-free capacity allocation** by changing settings only.

## What works (verified live)

| Behaviour | Status |
|---|---|
| Peer winner-resolution (a peer publishes the claim, not the backend) | ✅ |
| Capability-aware composite bidding + distance scoring | ✅ |
| Colour hard-filter (each colour routes to its capable AGV) | ✅ |
| Weight best-fit + overflow (tightest fit wins; heavier only when lighter busy) | ✅ |
| Arm AI load/unload decision on the bus | ✅ |
| Station-lock release on fault → no warehouse jam | ✅ |
| A\* routing downstream of the claim | ✅ |
| Both capability scenarios (strict-colour and colour-free) | ✅ |

## How it works

```text
Browser (Konva canvas + prompt bar, :8080)
   │
   ▼
nginx (frontend) ── proxies /api, /socket.io ──► backend  Flask + SocketIO (:5000)
                                                   │  plan once · host the bus · mirror to UI
                                                   ▼
                                          host.docker.internal:11500  ──► TU Clausthal GPU
                                              (reverse SSH tunnel)          qwq:32b + qwen2.5:14b
   message bus (HTTP ring + SocketIO mirror)
        ▲                ▲                ▲
   ┌────────┐       ┌────────┐       ┌────────┐        ┌──────────┐
   │ agv-1  │  …    │ agv-2  │  …    │ agv-3  │        │ arms     │   ← equal peers,
   └────────┘       └────────┘       └────────┘        └──────────┘     each its own container
```

**End to end:** prompt → 32B plans once → packages broadcast on the bus → each capable+free AGV bids (deterministic score, 14B as a bid-judge) → a peer resolves the winner and publishes the claim → the winner routes via A\* and docks → the arm peer decides load/unload → traverse → unload → deliver. If an actor crashes mid-task, a peer orphans its job and a survivor takes over.

The coordination layer lives in `app/agents/` (planner, announcer, in-process bus) and `scripts/` (the containerized AGV/arm peers: `agv_bus.py`, `agv_brain.py`, `arm_bus.py`). The whole topology — AGVs, arms, grid, capabilities, AI endpoints — is declared in `pipeline.yaml`.

## Prerequisites

- **Docker Desktop** (running).
- **Python 3.10+** (only to run `scripts/generate_compose.py`).
- **TU Clausthal VPN** access — the AI calls leave the host to reach the GPU.
- A **reverse-SSH tunnel** exposing the GPU's Ollama on host port `11500`:
  ```bash
  ssh -N -L 11500:127.0.0.1:11434 <user>@<tuc-gpu-host>
  ```

## Setup & run

```bash
# 1. Connect to the TU Clausthal VPN and bring up the SSH tunnel (above).

# 2. Confirm the GPU models are reachable from the host:
curl -s http://127.0.0.1:11500/api/tags | python -m json.tool
#    Expect: qwq:32b-q4_K_M  AND  qwen2.5:14b-instruct-q4_K_M

# 3. Regenerate the compose from pipeline.yaml (never hand-edit docker-compose.yml):
python scripts/generate_compose.py

# 4. Build and start the stack:
docker compose up -d --build

# 5. Confirm the backend reaches the GPU:
curl -s http://localhost:8080/ai/health | python -m json.tool
#    Expect: status:"connected", planner_present:true, tactical_present:true
```

Then open **<http://localhost:8080>** and type a prompt.

- Strict-colour scenario: *"send a blue package, a green package and a yellow package"*
- Capacity scenario: *"send a 45kg package and a 15kg package"*

Watch the coordination happen on the bus:
```bash
curl -s "http://localhost:5000/bus/events?since=0&limit=300"
#  task_announce → bid → claim (from a peer) → arm_decision → arm_ready → done
```

### Switching scenarios

```powershell
scripts\switch-scenario.ps1 colour   # each AGV one colour + a weight band
scripts\switch-scenario.ps1 free     # colour-free; weight-only allocation
```

### Coordination flags

Set on the backend and every AGV (and survive `generate_compose.py`):

| Flag | Meaning |
|---|---|
| `COORDINATION_MODE=smart` | capability-aware bidding, distance scoring, A\* routing |
| `FDAS_PEER_RESOLVE=on` | a peer (not the backend) resolves the winner |
| `ARM_AUTONOMY=on` | arms decide load/unload via their own LLM |

With the flags off, the system falls back to the earlier centralized / contract-net paths, unchanged.

## Configuration

`pipeline.yaml` is the **single source of truth** — AGVs, arms, the warehouse grid, capabilities (colour / weight / speed), and AI endpoints. After editing it:

```bash
python scripts/generate_compose.py      # regenerate docker-compose.yml
docker compose up -d --build            # rebuild affected containers
```

## Tests

```bash
pytest tests/ -v
```

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `/ai/health` → `disconnected` | SSH tunnel to the GPU is down — reconnect the VPN and re-open the tunnel. |
| `curl 127.0.0.1:11500` → connection refused | Same — the tunnel isn't listening on the host. |
| First `/plan` after a restart is slow | Normal — the 32B planner cold-loads on the shared GPU; later calls are fast. |
| A package isn't bid on | Check its colour/weight against the AGV capabilities in `pipeline.yaml` — no capable AGV means no bid (correct behaviour). |
| Frontend looks stale after a rebuild | Hard-refresh (Ctrl+Shift+R) to drop cached assets. |
