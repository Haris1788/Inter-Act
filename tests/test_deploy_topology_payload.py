"""Phase 5 Stage 3 — deploy_bridge topology round-trip tests.

Verifies that:
  - build_pipeline_yaml serializes the topology block into the yaml dict.
  - apply_topology writes it to disk and pipeline_config.reload() picks it up.
  - The yaml has no topology key when the payload carries none.
"""

from __future__ import annotations

import os

import yaml
import pytest

from app.services import deploy_bridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_payload(with_topology: bool = True) -> dict:
    payload: dict = {
        "actors": [
            {"id": "arm-1", "kind": "arm", "label": "Source",
             "position": {"x": 100, "y": 100}},
            {"id": "arm-2", "kind": "arm", "label": "Dest",
             "position": {"x": 400, "y": 100}},
            {"id": "agv-1", "kind": "agv", "label": "Shuttle",
             "position": {"x": 250, "y": 300}},
        ],
        "zones": [],
        "goal": {"text": "", "constraints": {}},
    }
    if with_topology:
        payload["topology"] = {
            "canvas": {"width": 1600, "height": 1000, "cell_px": 40},
            "actors": [
                {
                    "id": "arm-1", "kind": "arm",
                    "x": 120.0, "y": 300.0,
                    "reach_px": 140.0, "capacity": 1, "speed": 1.0,
                    "label": "Source", "color": "#e74c3c",
                },
                {
                    "id": "arm-2", "kind": "arm",
                    "x": 480.0, "y": 300.0,
                    "reach_px": 120.0, "capacity": 2, "speed": 1.5,
                    "label": "Dest",
                },
                {
                    "id": "agv-1", "kind": "agv",
                    "x": 300.0, "y": 600.0,
                    "capacity": 1, "speed": 1.0,
                },
            ],
        }
    return payload


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeployWritesTopologyBlock:
    def test_build_pipeline_yaml_includes_topology_block(self):
        """build_pipeline_yaml emits the topology: block when payload has one."""
        doc = deploy_bridge.build_pipeline_yaml(_base_payload(with_topology=True))

        assert "topology" in doc, "topology key must be present in output dict"
        topo = doc["topology"]
        assert "canvas" in topo
        assert topo["canvas"]["width"] == 1600
        assert topo["canvas"]["height"] == 1000
        assert topo["canvas"]["cell_px"] == 40

        actors_by_id = {a["id"]: a for a in topo["actors"]}
        assert set(actors_by_id) == {"arm-1", "arm-2", "agv-1"}

        arm1 = actors_by_id["arm-1"]
        assert arm1["kind"] == "arm"
        assert arm1["x"] == pytest.approx(120.0)
        assert arm1["y"] == pytest.approx(300.0)
        assert arm1["reach_px"] == pytest.approx(140.0)
        assert arm1["capacity"] == 1
        assert arm1["speed"] == pytest.approx(1.0)
        assert arm1.get("label") == "Source"
        assert arm1.get("color") == "#e74c3c"

        arm2 = actors_by_id["arm-2"]
        assert arm2["capacity"] == 2

        agv1 = actors_by_id["agv-1"]
        assert agv1["kind"] == "agv"
        assert agv1["x"] == pytest.approx(300.0)

    def test_deploy_omits_topology_when_absent(self):
        """yaml dict has no topology: key when payload carries none."""
        doc = deploy_bridge.build_pipeline_yaml(_base_payload(with_topology=False))
        assert "topology" not in doc

    def test_deploy_writes_topology_to_disk_and_reload_picks_it_up(self, tmp_path):
        """apply_topology writes pipeline.yaml with topology block; reload sees it."""
        from app.models import pipeline_config as pc

        cfg = tmp_path / "pipeline.yaml"
        comp = tmp_path / "docker-compose.yml"

        result = deploy_bridge.apply_topology(
            _base_payload(with_topology=True),
            config_path=str(cfg),
            compose_path=str(comp),
            run_compose=False,
            reload_runtime=False,
        )
        assert result["steps"]["config_written"] == "ok"

        # The yaml on disk must contain the topology block.
        with open(cfg, encoding="utf-8") as f:
            on_disk = yaml.safe_load(f)

        assert "topology" in on_disk
        assert on_disk["topology"]["canvas"]["width"] == 1600
        actor_ids = [a["id"] for a in on_disk["topology"]["actors"]]
        assert "arm-1" in actor_ids
        assert "agv-1" in actor_ids

        # Reload via pipeline_config and check get_topology() returns it.
        import importlib
        # Force re-parse from the file we just wrote.
        orig_path = pc._CONFIG_PATH
        try:
            pc._CONFIG_PATH = str(cfg)
            pc._config = None
            pc._topology_cache = pc._TOPOLOGY_UNSET
            topology = pc.get_topology()
            assert topology is not None
            ids = [a.id for a in topology.actors]
            assert "arm-1" in ids
            assert "agv-1" in ids
        finally:
            pc._CONFIG_PATH = orig_path
            pc._config = None
            pc._topology_cache = pc._TOPOLOGY_UNSET
