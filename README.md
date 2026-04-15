# INTER.act

Browser-based 2D simulation of robotic arms coordinating object handoffs through a production pipeline. The number of arms is configurable — edit `pipeline.yaml` and rebuild. Each arm runs in its own Docker container with independent health monitoring and failure propagation.

Built for Review Party #1 — Ostfalia SoSe 2026, IN3 Group + AiSuNe Foundation.

## Quick start (Docker)

```bash
docker compose up --build
# http://localhost:5000
# Click "Run Demo" to start the pipeline
```

Ollama is now included in the Docker stack and starts automatically.
The backend is configured to use `qwen2.5:0.5b` via `OLLAMA_MODEL`.
Default endpoint:

- Inside containers: `http://ollama:11434`
- On your host machine: `http://localhost:11434`

Custom port:

```bash
BACKEND_HOST_PORT=5001 docker compose up --build
```

## Quick start (local)

```bash
pip install -r requirements.txt
python run.py
# http://localhost:5000

# Run the demo from a second terminal:
python scripts/run_demo.py --speed normal --products 3
```

## Ollama setup (Docker)

Start only Ollama:

```bash
docker compose up -d ollama
```

Check Ollama container health:

```bash
docker compose ps ollama
```

Pull the configured Qwen model into the Ollama container:

```bash
docker compose exec ollama ollama pull qwen2.5:0.5b
```

List models:

```bash
docker compose exec ollama ollama list
```

Override the model (optional):

```bash
OLLAMA_MODEL=qwen2.5:1.5b docker compose up --build
```

Full stack with Ollama:

```bash
docker compose up --build
```

## Architecture

6+ containers (scales with arm count):

```
┌──────────┐     ┌─────────┐     ┌─────────────┐
│ frontend │────>│ backend │<────│ demo-runner  │
│ nginx:80 │     │ :5000   │     │ :6000        │
└──────────┘     └────┬────┘     └──────────────┘
                      │
         ┌────────────┼────────────┐
         │            │            │
      ┌──┴──┐     ┌──┴──┐     ┌──┴──┐
      │arm-1│     │arm-2│ ... │arm-N│
      └─────┘     └─────┘     └─────┘
```

- **backend** — Flask API + SocketIO. Pure API server, no pipeline execution.
- **frontend** — nginx serving static files, proxying API and WebSocket to backend.
- **demo-runner** — Executes the pipeline via HTTP calls to backend. Separate container to avoid thread starvation.
- **arm-1..N** — Independent microservices, one per arm. Health-monitored by backend.

## Configuration

Everything is driven by `pipeline.yaml`:

```yaml
pipeline:
  mode: dependency # sequential | parallel | dependency
  products: 3
  speed: normal

arms:
  - id: 1
    label: "Arm 1"
    color: "#e74c3c"
    depends_on: []
    critical: true
  - id: 2
    label: "Arm 2"
    color: "#2ecc71"
    depends_on: [1]
    critical: true
  # add more arms here — no code changes needed
```

Adding an arm:

```bash
# 1. Edit pipeline.yaml (add arm entry)
# 2. Regenerate Docker Compose:
python scripts/generate_compose.py
# 3. Rebuild:
docker compose up --build
```

## Pipeline modes

- **sequential** — arms chain linearly with handoff zones: Arm 1 → Arm 2 → ... → Arm N
- **parallel** — each arm works independently, no handoffs
- **dependency** — arms chain based on the `depends_on` graph (supports branching DAGs)

## Failure handling

When an arm container stops or fails:

1. Backend health monitor detects missed pings (~6 seconds)
2. Marks the arm as dead
3. All downstream arms (from `depends_on` graph) are stopped automatically
4. Demo-runner threads detect the health change within 2 seconds and stop

Failure policies per arm (in `pipeline.yaml`):

- `stop` — halt and propagate to downstream (default)
- `skip` — skip the failed product, continue
- `retry` — retry N times with delay
- `isolate` — stop this arm, don't propagate

Test it: `docker stop interact-arm-6-1` while the demo is running.

## API endpoints

| Endpoint             | Method | Description                                                                 |
| -------------------- | ------ | --------------------------------------------------------------------------- |
| `/`                  | GET    | Simulation page                                                             |
| `/health`            | GET    | Health check                                                                |
| `/health/ollama`     | GET    | Backend -> Ollama connectivity + configured model visibility                |
| `/set_angle`         | POST   | Move arm: `{"arm_id": 1, "angle": 45}`                                      |
| `/state`             | GET    | All arm states                                                              |
| `/state/<id>`        | GET    | Single arm state                                                            |
| `/config`            | GET    | Scene layout (dynamic, from pipeline.yaml)                                  |
| `/object_event`      | POST   | Track object: `{"object_id": "Obj1", "state": "carried", "carrier_arm": 1}` |
| `/objects`           | GET    | All tracked objects                                                         |
| `/objects/reset`     | POST   | Clear objects                                                               |
| `/log`               | POST   | Send log to browser panel                                                   |
| `/arms/health`       | GET    | All arm health states                                                       |
| `/arms/<id>/health`  | POST   | Report arm health                                                           |
| `/arms/<id>/stop`    | POST   | Stop arm + propagate to downstream                                          |
| `/arms/<id>/allowed` | GET    | Check if arm can execute                                                    |
| `/pipeline/status`   | GET    | Full pipeline status, DAG validity, health                                  |
| `/reload_config`     | POST   | Hot-reload pipeline.yaml without restart                                    |
| `/demo/start`        | POST   | Start pipeline demo (proxied to demo-runner)                                |
| `/demo/status`       | GET    | Check if demo is running                                                    |
| `/demo/stop`         | POST   | Stop running demo                                                           |

## Individual arm control

```bash
# CLI scripts
python scripts/arm_1_script.py --angle 45
python scripts/arm_2_script.py --sequence 30 90 150

# Arm microservice endpoints (Docker)
curl http://localhost:8001/health
curl -X POST http://localhost:8001/move -H "Content-Type: application/json" -d '{"angle": 45}'
```

## Running tests

```bash
pytest tests/ -v    # 28 tests
```

## Tech stack

- Flask + Flask-SocketIO (backend)
- Vanilla JS + HTML5 Canvas (frontend)
- Docker Compose (containerization)
- pyyaml (config-driven architecture)
- pytest (testing)
- simple-websocket (WebSocket transport)

## Docker troubleshooting (Windows)

**`docker` not recognized:** Add `C:\Program Files\Docker\Docker\resources\bin` to PATH, or run:

```powershell
$env:Path = "C:\Program Files\Docker\Docker\resources\bin;" + $env:Path
```

**Port already in use:** `docker compose down --remove-orphans` or use `BACKEND_HOST_PORT=5001`.

**Credential helper error:** Same PATH fix as above.

**Browser can't connect:** Use `http://localhost:5000` or `http://127.0.0.1:5000`.
