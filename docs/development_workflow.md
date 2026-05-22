# INTER.act development workflow

This is the practical dev loop for day-to-day work.

## Local development

Start backend:

```bash
pip install -r requirements.txt
python run.py
```

Run demo-runner server from another terminal:

```bash
python scripts/demo_runner_server.py
```

## Docker development

Bring up stack:

```bash
docker compose up --build
```

If arm count changed in `pipeline.yaml`:

```bash
python scripts/generate_compose.py
docker compose up --build
```

## Safe config change cycle

Use this sequence after editing `pipeline.yaml`:

```bash
curl -X POST http://localhost:5000/reload_config
curl -X POST http://localhost:5000/restart_pipeline
```

For Docker arm-count changes, also regenerate compose.

## Tests

Run full test suite:

```bash
pytest tests/ -v
```

Run a focused file:

```bash
pytest tests/test_pipeline_config.py -v
```

## Quick runtime checks

Backend state:

```bash
curl http://localhost:5000/state
curl http://localhost:5000/arms/health
curl http://localhost:5000/assets/state
curl http://localhost:5000/pipeline/status
```

Demo lifecycle:

```bash
curl -X POST http://localhost:5000/demo/start -H "Content-Type: application/json" -d "{\"products\":3,\"speed\":\"normal\"}"
curl http://localhost:5000/demo/status
curl -X POST http://localhost:5000/demo/stop
```

## Recommended file reading order for new contributors

1. `docs/system_documentation.md`
2. `docs/config_reference.md`
3. `docs/backend_runtime.md`
4. `docs/api_reference.md`
5. `docs/frontend_assets.md`
6. `docs/failure_model.md`
7. `docs/docker_stack.md`
8. `docs/troubleshooting.md`

## Known dev-time traps

- changing `pipeline.yaml` but forgetting reload/restart endpoints
- changing topology but not regenerating compose
- assuming `/demo/stop` is a hard immediate interrupt
- debugging frontend visuals without checking `/objects` ownership fields first
