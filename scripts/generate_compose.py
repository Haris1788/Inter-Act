"""Read pipeline.yaml and generate docker-compose.yml with N arm services.

Usage:
    python scripts/generate_compose.py
    python scripts/generate_compose.py --config pipeline.yaml --output docker-compose.yml
"""

import argparse
import os
import sys
import yaml


def generate(config_path="pipeline.yaml", output_path="docker-compose.yml"):
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    arms = cfg["arms"]

    services = {
        "backend": {
            "build": ".",
            "ports": ["${BACKEND_HOST_PORT:-5000}:5000"],
            "environment": [
                "SERVER_HOST=http://backend:5000",
                "DEMO_RUNNER_HOST=http://demo-runner:6000",
                "FLASK_ENV=production",
                "FLASK_DEBUG=0",
                "PYTHONUNBUFFERED=1",
            ],
            "healthcheck": {
                "test": [
                    "CMD", "python", "-c",
                    "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=3).read()",
                ],
                "interval": "5s",
                "timeout": "3s",
                "retries": 20,
                "start_period": "5s",
            },
        },
        "frontend": {
            "build": {"context": ".", "dockerfile": "docker/Dockerfile.frontend"},
            "ports": ["8080:80"],
            "depends_on": {"backend": {"condition": "service_healthy"}},
        },
        "demo-runner": {
            "build": {"context": ".", "dockerfile": "docker/Dockerfile.demo-runner"},
            "ports": ["${DEMO_RUNNER_HOST_PORT:-6000}:6000"],
            "environment": [
                "SERVER_HOST=http://backend:5000",
                "PYTHONUNBUFFERED=1",
            ],
            "healthcheck": {
                "test": [
                    "CMD", "python", "-c",
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

    for arm in arms:
        arm_id = arm["id"]
        services[f"arm-{arm_id}"] = {
            "build": {"context": ".", "dockerfile": "docker/Dockerfile.arm"},
            "ports": [f"{8000 + arm_id}:8000"],
            "environment": [
                f"ARM_ID={arm_id}",
                "SERVER_HOST=http://backend:5000",
            ],
            "depends_on": {"backend": {"condition": "service_healthy"}},
        }

    compose = {"services": services}

    with open(output_path, "w") as f:
        yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

    print(f"Generated {output_path}")
    print(f"  Arms: {len(arms)} ({', '.join(f'arm-{a['id']}' for a in arms)})")
    print(f"  Total services: {len(services)}")
    print(f"\nRun: docker compose up --build")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate docker-compose.yml from pipeline.yaml")
    parser.add_argument("--config", default="pipeline.yaml")
    parser.add_argument("--output", default="docker-compose.yml")
    args = parser.parse_args()
    generate(args.config, args.output)
