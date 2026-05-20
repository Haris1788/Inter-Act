# INTER.act

**INTER.act is an adaptive warehouse-automation simulation:** type a natural-language
prompt, an AI planner assigns the work to the robots, and three AGVs + two robotic
arms execute it in real time in the browser. Every per-agent decision is made by a
large language model running on the TU Clausthal GPU.

## What it does

- **Master AI (`qwq:32b`)** interprets a prompt (e.g. *"Sort 3 packages by rainbow
  color, one per AGV"*) and produces a per-AGV plan.
- **Per-agent tactical AI (`qwen2.5:14b`)** reads live shared status and decides
  before every script whether each AGV should **proceed / hold / fault** — collisions
  are avoided by AI coordination, not just locks.
- **Arms** AI-confirm load/unload handshakes with the AGV they are servicing.
- **Hardcoded Python scripts** do all the actual movement (`move_to_input`,
  `traverse_lane`, `load_package`, …). The AI controls only timing.
- 3 AGVs, 2 robotic arms, a 14×10 warehouse grid — all driven from `pipeline.yaml`.

Architecture, code paths, and the live verification of the full chain are documented
in [`documentation/(20 May 2026) AI Integration.md`](documentation/).

## System map

```text
Browser (Konva canvas + prompt bar, :8080)
   ▼
Frontend nginx ── proxies /api, /socket.io ──► backend Flask + SocketIO (:5000)
                                                  ▼
                                              llm_service ─► host.docker.internal:11500
                                                  │              (reverse SSH tunnel)
                                                  ▼                     ▼
                                              ┌─────────┐         cloud-247 (TU Clausthal)
                                              │ agv-1   │           ▲   ▲
                                              │ agv-2   │           │   │
                                              │ agv-3   │      qwq:32b  qwen2.5:14b
                                              └─────────┘
```

## Prerequisites

- **Docker Desktop** (running).
- **Python 3.10+** (only for `scripts/generate_compose.py`).
- **TU Clausthal VPN** access (the AI calls leave the host to reach `cloud-247`).
- The **persistent reverse-SSH tunnel** from `cloud-247` to this host on port `11500`
  has to be up. See [`test_ollama_two_model.py`](test_ollama_two_model.py) for the
  architecture and the operator commands.

## Setup — step by step

```bash
# 1. Clone and switch to the active integration branch
git clone <repo-url> Inter-Act
cd Inter-Act
git checkout dev-haris

# 2. Connect to TU Clausthal via VPN.

# 3. Confirm the SSH tunnel is up and the TUC models are reachable from the host.
curl -s http://127.0.0.1:11500/api/tags | python -m json.tool
#    Expect: qwq:32b-q4_K_M  AND  qwen2.5:14b-instruct-q4_K_M
#    If this fails -> the tunnel is down (see Troubleshooting).

# 4. Regenerate docker-compose.yml from pipeline.yaml.
#    NEVER hand-edit docker-compose.yml — it is generated.
python scripts/generate_compose.py

# 5. Build and start the stack (backend + frontend + 3 AGV containers + ollama + demo-runner).
docker compose up -d --build

# 6. Wait for all containers to be healthy.
docker compose ps

# 7. Verify the backend reaches the real TUC models (not the local 0.5b).
curl -s http://localhost:8080/ai/health | python -m json.tool
#    Expect: status:"connected",
#            url:"http://host.docker.internal:11500",
#            planner_present:true, tactical_present:true
```

## Run it

Open **<http://localhost:8080>** in a browser.

- **Type a prompt** in the prompt bar — examples:
  - *"Sort 3 packages by rainbow color, one per AGV"*
  - *"Send 2 packages from input to output"*
- The master AI plans, hands assignments to the AGV containers, and each AGV's
  tactical AI drives every step. Watch the **log panel** in the UI for `AI decision:`
  lines, or in a terminal:

```bash
docker compose logs -f agv-1 agv-2 agv-3 | grep -E "AI decision|task start|task complete"
```

- The **Dispatch button** runs an AI-free baseline (pure Python `dispatch_routes`) —
  useful to compare with the AI path.

> **First `/plan` call after a restart cold-loads `qwq:32b` on the shared GPU and
> takes ~minutes.** This is the documented `model_swap` event, not a freeze. For a
> live demo, see the warm-up procedure in
> `documentation/(20 May 2026) AI Integration.md`.

## Configuration

`pipeline.yaml` is the single source of truth — arms, AGVs, warehouse grid, AI
endpoints. After editing it:

```bash
python scripts/generate_compose.py        # regenerate docker-compose.yml
docker compose up -d --build              # rebuild affected containers
```

For a running backend without container changes:

```bash
curl -X POST http://localhost:5000/reload_config
curl -X POST http://localhost:5000/restart_pipeline
```

## Documentation

| File | What's in it |
| --- | --- |
| [`documentation/(20 May 2026) AI Integration.md`](documentation/) | **Current architecture** — what was before, what was done, full prompt→AGVs flow, full TUC network journey, demo runbook. **Start here.** |
| [`documentation/(03 May 2026) Integration AI.md`](documentation/) | Previous milestone (3-arm + shuttle + conveyor world). |
| `docs/architecture.md`, `docs/api_reference.md`, `docs/docker_stack.md` | Module-level reference. |
| `docs/troubleshooting.md` | Common issues. |

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `/ai/health` → `status:"disconnected"` | SSH tunnel to `cloud-247` is down. Check the tunnel; reconnect VPN. |
| `curl 127.0.0.1:11500` → *Connection refused* | Same — tunnel not listening on the host. |
| Docker errors / *daemon not running* | Start Docker Desktop. |
| First `/plan` hangs for minutes | Normal — qwq:32b cold-load. Subsequent calls are fast. |
| AGV drives "inside" the arm | Confirm `pipeline.yaml` has `stations.input_arm.dock: [2,1]` and `output_arm.dock: [11,1]`. |
| Frontend shows nothing | Hard-refresh (Ctrl+Shift+R) to clear cached assets after a frontend rebuild. |

## Tests

```bash
pytest tests/ -v
```

## Branch

`dev-haris` is the active integration branch with the TU Clausthal AI layer wired
and verified. Other branches (`dev`, `dev-sneh`) are historical / experimental.
