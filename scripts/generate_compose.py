"""Generate docker-compose.yml from declarative pipeline.yaml.

The output compose file includes:
  - core services (ollama, backend, frontend, demo-runner)
  - one service per configured arm
  - one service per configured non-arm asset (vehicle)

Any topology change in pipeline.yaml should only require:
  1) python scripts/generate_compose.py
  2) docker compose up --build
"""

from __future__ import annotations

import argparse
import math
import yaml

ASSET_PORT_BASE = 8010
ASSET_PORT_STEP = 10
COMPOSE_PREFIX = "interact"


def _asset_host_ports(assets):
    return {
        a["id"]: ASSET_PORT_BASE + i * ASSET_PORT_STEP for i, a in enumerate(assets)
    }


def _asset_url(asset_id):
    if not asset_id:
        return ""
    return f"http://{asset_id}:8000"


def _vehicle_travel_time(asset):
    speed = float(asset.get("speed", 100) or 100)
    waypoints = asset.get("waypoints") or []
    if len(waypoints) < 2:
        return 2.0

    distance = 0.0
    for i in range(len(waypoints) - 1):
        a = waypoints[i]
        b = waypoints[i + 1]
        distance += math.dist(
            (a.get("x", 0), a.get("y", 0)), (b.get("x", 0), b.get("y", 0))
        )

    if speed <= 0:
        return 2.0
    return max(0.1, round(distance / speed, 3))


def _non_arm_assets(cfg):
    raw = cfg.get("assets") or []
    for a in raw:
        if a.get("type") == "conveyor":
            raise ValueError(
                f"Asset {a.get('id')!r}: unsupported asset type 'conveyor' - "
                f"conveyor support has been removed from pipeline.yaml"
            )
    return [a for a in raw if a.get("type") == "vehicle"]


def generate(config_path="pipeline.yaml", output_path="docker-compose.yml"):
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    arms = cfg.get("arms") or []
    assets = _non_arm_assets(cfg)
    asset_ports = _asset_host_ports(assets)
    agvs = (cfg.get("warehouse") or {}).get("agvs") or []
    ai_cfg = cfg.get("ai") or {}
    ollama_base_url = ai_cfg.get("ollama_base_url", "http://ollama:11434")
    # The heavy two-model AI layer (llm_service) points at the TU Clausthal
    # tunnel, separate from qwen_brain's local container. Defaults to the
    # ollama url if not configured (back-compat).
    llm_base_url = ai_cfg.get("llm_base_url", ollama_base_url)
    model_planner = ai_cfg.get("model_planner", "qwq:32b-q4_K_M")
    model_tactical = ai_cfg.get("model_tactical", "qwen2.5:14b-instruct-q4_K_M")

    services = {
        "ollama": {
            "image": "ollama/ollama:latest",
            "ports": ["${OLLAMA_HOST_PORT:-11434}:11434"],
            "volumes": ["ollama_data:/root/.ollama"],
            "environment": ["OLLAMA_KEEP_ALIVE=24h"],
            "healthcheck": {
                "test": ["CMD", "ollama", "list"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 12,
                "start_period": "20s",
            },
        },
        "backend": {
            "build": ".",
            "container_name": f"{COMPOSE_PREFIX}-backend",
            "ports": ["${BACKEND_HOST_PORT:-5000}:5000"],
            "environment": [
                "SERVER_HOST=http://backend:5000",
                "DEMO_RUNNER_HOST=http://demo-runner:6000",
                f"OLLAMA_BASE_URL={ollama_base_url}",
                f"LLM_BASE_URL={llm_base_url}",
                f"OLLAMA_MODEL_PLANNER={model_planner}",
                f"OLLAMA_MODEL_TACTICAL={model_tactical}",
                "PIPELINE_CONFIG=/config/pipeline.yaml",
                "FLASK_ENV=production",
                "FLASK_DEBUG=0",
                "PYTHONUNBUFFERED=1",
                "CONTAINER_CONTROL=on",
                f"COMPOSE_CONTAINER_PREFIX={COMPOSE_PREFIX}",
            ],
            "volumes": [
                "/var/run/docker.sock:/var/run/docker.sock",
                # Writable: the deploy bridge rewrites this on POST
                # /deploy/apply, then reloads. Arms/demo-runner keep :ro.
                "./pipeline.yaml:/config/pipeline.yaml:rw",
            ],
            "healthcheck": {
                "test": [
                    "CMD",
                    "python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=3).read()",
                ],
                "interval": "5s",
                "timeout": "3s",
                "retries": 20,
                "start_period": "5s",
            },
            # So the container can reach the TU Clausthal reverse-SSH tunnel
            # bound on the host (host.docker.internal:11500). Resolves
            # automatically on Docker Desktop; required on a Linux host.
            "extra_hosts": ["host.docker.internal:host-gateway"],
            "depends_on": {"ollama": {"condition": "service_healthy"}},
        },
        "frontend": {
            "build": {"context": ".", "dockerfile": "docker/Dockerfile.frontend"},
            "ports": ["8080:80"],
            "depends_on": {"backend": {"condition": "service_healthy"}},
        },
        "demo-runner": {
            "build": {"context": ".", "dockerfile": "docker/Dockerfile.demo-runner"},
            "container_name": f"{COMPOSE_PREFIX}-demo-runner",
            "ports": ["${DEMO_RUNNER_HOST_PORT:-6000}:6000"],
            "environment": [
                "SERVER_HOST=http://backend:5000",
                "PIPELINE_CONFIG=/config/pipeline.yaml",
                "PYTHONUNBUFFERED=1",
            ],
            "volumes": ["./pipeline.yaml:/config/pipeline.yaml:ro"],
            "healthcheck": {
                "test": [
                    "CMD",
                    "python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('http://localhost:6000/health', timeout=3).read()",
                ],
                "interval": "5s",
                "timeout": "3s",
                "retries": 10,
                "start_period": "3s",
            },
            "depends_on": {"backend": {"condition": "service_healthy"}},
        },
    }

    # Arms are managed dynamically by topology reconciliation (container_manager).
    # AGVs are static infrastructure — always running, one container per AGV.
    _ = (arms, asset_ports)  # arms parsed for validation/summary only

    for i, agv in enumerate(agvs, start=1):
        agv_id = agv["id"]
        services[agv_id] = {
            "build": {"context": ".", "dockerfile": "docker/Dockerfile.agv"},
            "container_name": f"{COMPOSE_PREFIX}-{agv_id}",
            "ports": [f"{8100 + i}:8100"],
            "environment": [
                f"AGV_ID={agv_id}",
                "SERVER_HOST=http://backend:5000",
                f"OLLAMA_BASE_URL={ollama_base_url}",
                "PYTHONUNBUFFERED=1",
            ],
            "depends_on": {"backend": {"condition": "service_healthy"}},
        }

    compose = {"services": services, "volumes": {"ollama_data": {}}}

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

    arm_names = ", ".join(f"arm-{a['id']}" for a in arms) if arms else "-"
    agv_names = ", ".join(a["id"] for a in agvs) if agvs else "-"
    print(f"Generated {output_path}")
    print(f"  Core    : ollama, backend, frontend, demo-runner")
    print(f"  AGVs    : {len(agvs)} ({agv_names})  — static compose services")
    print(f"  Arms    : {len(arms)} ({arm_names})  — reconciled at deploy")
    print(f"  Ollama  : {ollama_base_url}")
    print("\nRun: docker compose up --build")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate docker-compose.yml from pipeline.yaml"
    )
    parser.add_argument("--config", default="pipeline.yaml")
    parser.add_argument("--output", default="docker-compose.yml")
    args = parser.parse_args()
    generate(args.config, args.output)
