# INTER.act

INTER.act is a browser simulation of a production pipeline with robotic arms, live state updates, and config-driven topology.

## Quick start

Docker:

```bash
docker compose up --build
# open http://localhost:5000
```

Local:

```bash
pip install -r requirements.txt
python run.py
python scripts/run_demo.py --speed normal --products 3
```

## Configuration

Main config file is `pipeline.yaml`.

After changing config in a running backend:

```bash
curl -X POST http://localhost:5000/reload_config
curl -X POST http://localhost:5000/restart_pipeline
```

If arm count changed in Docker mode:

```bash
python scripts/generate_compose.py
docker compose up --build
```

## Documentation map

Start here:

- `docs/system_documentation.md`

Detailed docs:

- `docs/api_reference.md`
- `docs/failure_model.md`
- `docs/frontend_assets.md`
- `docs/docker_stack.md`
- `docs/backend_runtime.md`
- `docs/config_reference.md`
- `docs/development_workflow.md`
- `docs/troubleshooting.md`

## Tests

```bash
pytest tests/ -v
```
