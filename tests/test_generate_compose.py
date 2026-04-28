from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from scripts.generate_compose import generate


def _write_yaml(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


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
                "downstream": ["conv-1"],
            },
            {
                "id": "conv-1",
                "type": "conveyor",
                "label": "Belt 1",
                "from": {"x": 20, "y": 0},
                "to": {"x": 50, "y": 0},
                "slot_count": 4,
                "shift_interval": 1.2,
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

        assert "arm-1" in services
        assert "arm-2" in services
        assert "arm-3" in services
        assert "veh-a" in services
        assert "conv-1" in services

        # Deterministic asset host ports from declaration order.
        assert services["veh-a"]["ports"] == ["8010:8000"]
        assert services["conv-1"]["ports"] == ["8020:8000"]

        # Compose runtime uses a single mounted pipeline path across services.
        assert "./pipeline.yaml:/config/pipeline.yaml:ro" in services["demo-runner"]["volumes"]
        assert "./pipeline.yaml:/config/pipeline.yaml:ro" in services["backend"]["volumes"]
        assert "PIPELINE_CONFIG=/config/pipeline.yaml" in services["backend"]["environment"]
        assert "PIPELINE_CONFIG=/config/pipeline.yaml" in services["demo-runner"]["environment"]
        assert "PIPELINE_CONFIG=/config/pipeline.yaml" in services["veh-a"]["environment"]
