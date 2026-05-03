# INTER.act system documentation

## Start here

INTER.act runs a topology-driven production-line simulation from `pipeline.yaml`. Arms and non-arm assets both participate in runtime flow, and object position in the UI is ownership-driven (`owner_type`, `owner_id`) from backend events.

If you only read one section, read this:

1. `pipeline.yaml` defines the pipeline topology.
2. `app/models/pipeline_config.py` is the single config layer used by backend, demo-runner, and compose generation.
3. `scripts/run_demo.py` orchestrates arm flow and bridges to asset containers over HTTP.
4. Asset containers run `scripts/asset_service.py` with real controller logic.
5. Frontend renders object positions from ownership fields, not heuristics.

## System at a glance

```
Browser
  -> POST /demo/start
Backend (:5000)
  -> proxies to demo-runner (:6000)
Demo-runner
  -> drives arms + HTTP bridges
Asset containers / arm containers
  -> emit health and object events through backend routes
Backend Socket.IO
  -> state_update, object_update, arm_health, asset_health, log_message
Browser
  -> renders scene + ownership state
```

Runtime services:

- `backend` (`run.py`): Flask API, in-memory runtime state, Socket.IO, health monitor.
- `demo-runner` (`scripts/demo_runner_server.py`): start/stop/status wrapper around `scripts/run_demo.py`.
- `arm-*` (`scripts/arm_server.py`): arm microservices.
- asset services (`veh-*`, `conv-*`) (`scripts/asset_service.py`): vehicle/conveyor controller runtime.
- `frontend`: static UI app.

## Configuration source of truth

`app/models/pipeline_config.py` owns:

- validated arm and asset topology
- generated scene config (`/config`)
- module flow order (`module_flow_order`)
- deterministic asset host-port mapping
- dependency graph checks

The same file is consumed by backend routes, demo-runner fallback URL logic, and compose generation.

## Pipeline execution model

`scripts/run_demo.py` builds arm modules and wires arm boundaries in two ways:

- direct arm-to-arm boundary: in-process `TransferPoint` buffer (`capacity=1`)
- arm-to-asset or asset-to-arm boundary: HTTP bridges (`HttpOutputBridge`, `HttpInputBridge`)

Modes:

- `sequential`
- `parallel`
- `dependency`

Current behavior is tuned for linear ownership flow with explicit transfer points between adjacent modules in `module_flow_order`.

## Ownership and flow control

Object updates are validated in `app/routes/arm_routes.py`:

- owner existence is validated against current topology
- ownership transitions are checked (`intake -> arm -> transfer_point -> ... -> output`)
- stale object events are ignored using `event_time`/`event_seq`

Backpressure rules are strict:

- modules do not pick or emit while failed/recovering/stopped
- transfer points are passive single-slot buffers
- blocked downstream moves modules to `draining`

## Failure and recovery

Failure signals come from:

- explicit endpoints (`/arms/<id>/stop`, `/assets/<id>/fail`)
- service health monitor polling
- container shutdown paths that report failure before exit

Recovery flow:

- recover endpoints transition module state to `recovering`
- controllers stop processing while recovering
- monitor and controller state converge back to `idle` when healthy

## Frontend model

Frontend entry: `app/static/js/main.js`.

Main runtime modules:

- `core/state.js`: asset graph + object store + event reconciliation
- `core/socket.js`: Socket.IO subscription and bootstrap sync
- `core/render.js`: canvas rendering

Object placement is ownership-driven:

- arm/asset/zone/transfer-point ownership maps directly to render positions
- when target position is temporarily unresolved, renderer keeps last known position to prevent flicker/disappear behavior

## Core API groups

Page and health:

- `GET /`
- `GET /health`
- `GET /health/ollama`

State and objects:

- `POST /set_angle`
- `GET /state`
- `GET /state/<id>`
- `POST /object_event`
- `GET /objects`
- `POST /objects/reset`
- `POST /log`

Pipeline config/runtime:

- `GET /config`
- `POST /reload_config`
- `POST /restart_pipeline`
- `GET /pipeline/status`

Arm and asset control:

- `GET /arms/health`
- `POST /arms/<id>/health`
- `POST /arms/<id>/stop`
- `POST /arms/<id>/start`
- `GET /arms/<id>/allowed`
- `GET /assets/state`
- `GET /assets/<asset_id>/state`
- `POST /assets/<asset_id>/fail`
- `POST /assets/<asset_id>/recover`

Demo routes (proxy to demo-runner):

- `POST /demo/start`
- `GET /demo/status`
- `POST /demo/stop`

## Run and operate

Docker:

```bash
python scripts/generate_compose.py
docker compose up --build
```

Local backend:

```bash
pip install -r requirements.txt
python run.py
```

Apply config changes without full restart:

```bash
curl -X POST http://localhost:5000/reload_config
curl -X POST http://localhost:5000/restart_pipeline
```

Test:

```bash
pytest tests/ -v
```

## Related docs

- `docs/api_reference.md`
- `docs/config_reference.md`
- `docs/backend_runtime.md`
- `docs/frontend_assets.md`
- `docs/failure_model.md`
- `docs/docker_stack.md`
- `docs/troubleshooting.md`
- `docs/development_workflow.md`
