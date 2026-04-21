# INTER.act Docker stack

This file explains what each service does, how they connect, and what to change when you scale or debug the stack.

## Services

- `backend`  
  Flask + Socket.IO API on container port `5000`.

- `frontend`  
  Nginx static host on container port `80`, exposed as host `8080`.

- `demo-runner`  
  Runs pipeline execution in a separate process on container port `6000`.

- `arm-<id>`  
  One arm microservice per configured arm, each listening on container port `8000`.

- `ollama`  
  Local model service, exposed by default on host `11434`.

## Why demo-runner is separate

The backend is API and event broadcast.  
The demo-runner is the workload driver. It calls backend routes over HTTP as an external client.

This separation avoids backend self-call contention and keeps Socket.IO updates responsive during active runs.

## Ports and host mappings

Default host ports:

- backend: `${BACKEND_HOST_PORT:-5000}`
- demo-runner: `${DEMO_RUNNER_HOST_PORT:-6000}`
- frontend: `8080`
- ollama: `${OLLAMA_HOST_PORT:-11434}`
- arms: `8001..` mapped from each arm service (`8000 + arm_id`)

## Environment variables used in compose

- `SERVER_HOST`  
  Internal backend URL used by services.

- `DEMO_RUNNER_HOST`  
  URL backend uses when proxying `/demo/*`.

- `OLLAMA_BASE_URL`  
  Backend target URL for model health/connectivity checks.

- `OLLAMA_MODEL`  
  Model name expected by backend checks and usage.

- `BACKEND_HOST_PORT`, `DEMO_RUNNER_HOST_PORT`, `OLLAMA_HOST_PORT`  
  Host-side port overrides.

## Healthchecks and startup order

`backend` depends on `ollama` health.  
`frontend` and `demo-runner` depend on `backend` health.  
`arm-*` services also depend on `backend` health.

Practical meaning: if backend health is red, the rest of the stack will not settle cleanly.

## Scaling arm services

Arm services are generated from `pipeline.yaml`, not manually edited.

```bash
python scripts/generate_compose.py
docker compose up --build
```

Do not hand-edit generated arm entries unless you are debugging generation itself.

## Common operations

Start full stack:

```bash
docker compose up --build
```

Start only Ollama:

```bash
docker compose up -d ollama
```

Check service health:

```bash
docker compose ps
```

## Gotchas

- If `pipeline.yaml` arm count changes and compose is not regenerated, service count and config will disagree.
- If `DEMO_RUNNER_HOST` is wrong, `/demo/start` works as a route but fails at proxy time.
- If Ollama model is missing, backend health path for Ollama can fail even when container is running.
