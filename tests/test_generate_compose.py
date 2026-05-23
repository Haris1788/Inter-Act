from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from scripts.generate_compose import generate


def _write_yaml(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


import pytest


def test_generate_compose_includes_assets_from_pipeline_yaml():
    cfg = {
        "pipeline": {"mode": "dependency"},
        "arms": [
            {"id": 1, "label": "Arm 1", "color": "#111", "depends_on": [], "critical": True},
            {"id": 2, "label": "Arm 2", "color": "#222", "depends_on": [1], "critical": True},
            {"id": 3, "label": "Arm 3", "color": "#333", "depends_on": [2], "critical": True},
        ],
        "assets": [
            {
                "id": "veh-a",
                "type": "vehicle",
                "label": "Shuttle A",
                "speed": 100,
                "waypoints": [{"x": 0, "y": 0}, {"x": 20, "y": 0}],
                "upstream": ["arm-2"],
                "downstream": ["veh-b"],
            },
            {
                "id": "veh-b",
                "type": "vehicle",
                "label": "Shuttle B",
                "speed": 100,
                "waypoints": [{"x": 20, "y": 0}, {"x": 50, "y": 0}],
                "upstream": ["veh-a"],
                "downstream": ["arm-3"],
            },
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "pipeline.yaml"
        out_path = Path(tmp) / "docker-compose.yml"
        _write_yaml(cfg_path, cfg)

        generate(str(cfg_path), str(out_path))

        compose = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        services = compose["services"]

        # Unified model: compose defines ONLY core infrastructure. Arms /
        # AGVs are reconciler-owned actor containers, never compose services.
        # Phase 4 Stage 1: redis is a core service.
        # Phase 4 Stage 2 Track C: actor-image is a build-only service
        # (profiles: [build-only]) so `docker compose build actor-image`
        # pre-builds interact-shared-actor without starting a container.
        # Phase 5 cleanup: ollama service removed — LLM via TU Clausthal
        # tunnel (host.docker.internal:11500).
        assert set(services) == {
            "redis",
            "backend",
            "frontend",
            "demo-runner",
            "actor-image",
        }
        # actor-image must be build-only (never started as a running service).
        assert services["actor-image"]["profiles"] == ["build-only"]
        assert services["actor-image"]["image"] == "interact-shared-actor:latest"
        assert "arm-1" not in services
        assert "veh-a" not in services

        # Backend mount writable so the deploy bridge can rewrite config;
        # demo-runner reads it read-only.
        assert "./pipeline.yaml:/config/pipeline.yaml:rw" in services["backend"]["volumes"]
        assert "./pipeline.yaml:/config/pipeline.yaml:ro" in services["demo-runner"]["volumes"]
        assert "PIPELINE_CONFIG=/config/pipeline.yaml" in services["backend"]["environment"]
        assert "PIPELINE_CONFIG=/config/pipeline.yaml" in services["demo-runner"]["environment"]


def test_generate_compose_rejects_conveyor_type():
    """Regression: conveyor support removed; declaring it must fail loudly."""
    cfg = {
        "pipeline": {"mode": "dependency"},
        "arms": [
            {"id": 1, "label": "Arm 1", "color": "#111", "depends_on": [], "critical": True},
            {"id": 2, "label": "Arm 2", "color": "#222", "depends_on": [1], "critical": True},
        ],
        "assets": [
            {
                "id": "conv-1", "type": "conveyor", "label": "Belt",
                "from": {"x": 0, "y": 0}, "to": {"x": 50, "y": 0},
                "slot_count": 4, "shift_interval": 1.0,
                "upstream": ["arm-1"], "downstream": ["arm-2"],
            },
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "pipeline.yaml"
        out_path = Path(tmp) / "docker-compose.yml"
        _write_yaml(cfg_path, cfg)
        with pytest.raises(ValueError, match="conveyor"):
            generate(str(cfg_path), str(out_path))
