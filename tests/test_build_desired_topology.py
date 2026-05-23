"""Phase 5 Stage 3 — build_desired_from_config topology path tests.

Verifies that:
  - build_desired_from_config uses the topology block when present, including
    spatial fields (x, y, reach_px, capacity, speed).
  - Falls back to the legacy arms+AGVs path when no topology block.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_topology(arm_specs, agv_specs=None):
    """Build a minimal Topology object from simple spec dicts."""
    from app.models.topology import ActorSpec, Topology

    actors = []
    for s in (arm_specs or []):
        actors.append(ActorSpec(
            id=s["id"], kind="arm",
            x=s.get("x", 100.0), y=s.get("y", 300.0),
            reach_px=s.get("reach_px", 120.0),
            capacity=s.get("capacity", 1),
            speed=s.get("speed", 1.0),
            label=s.get("label"),
        ))
    for s in (agv_specs or []):
        actors.append(ActorSpec(
            id=s["id"], kind="agv",
            x=s.get("x", 400.0), y=s.get("y", 600.0),
            capacity=s.get("capacity", 1),
            speed=s.get("speed", 1.0),
        ))
    return Topology(actors=tuple(actors))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildDesiredUsesTopologyWhenPresent:
    def test_desired_set_matches_topology_actors(self, monkeypatch):
        """When topology is present, desired list = topology actors with spatial fields."""
        from app.services import container_manager as cm
        from app.models import pipeline_config as pc

        topo = _make_topology(
            arm_specs=[
                {"id": "arm-1", "x": 120.0, "y": 300.0, "reach_px": 140.0},
                {"id": "arm-2", "x": 480.0, "y": 300.0, "reach_px": 120.0, "capacity": 2},
            ],
            agv_specs=[
                {"id": "agv-1", "x": 300.0, "y": 600.0, "speed": 1.5},
            ],
        )
        monkeypatch.setattr(pc, "get_topology", lambda: topo)

        desired = cm.build_desired_from_config()

        ids = {d["id"] for d in desired}
        assert ids == {"arm-1", "arm-2", "agv-1"}

        by_id = {d["id"]: d for d in desired}
        arm1 = by_id["arm-1"]
        assert arm1["kind"] == "arm"
        assert arm1["x"] == pytest.approx(120.0)
        assert arm1["y"] == pytest.approx(300.0)
        assert arm1["reach_px"] == pytest.approx(140.0)

        arm2 = by_id["arm-2"]
        assert arm2["capacity"] == 2

        agv1 = by_id["agv-1"]
        assert agv1["kind"] == "agv"
        assert agv1["speed"] == pytest.approx(1.5)

    def test_topology_overrides_warehouse_arms(self, monkeypatch):
        """Topology actors win even if get_arms/get_agvs would return different data."""
        from app.services import container_manager as cm
        from app.models import pipeline_config as pc

        topo = _make_topology(arm_specs=[{"id": "arm-7", "x": 800.0, "y": 400.0}])
        monkeypatch.setattr(pc, "get_topology", lambda: topo)
        # get_arms would return arm-1 in legacy path — but topology wins.
        monkeypatch.setattr(pc, "get_arms", lambda: [{"id": 1, "label": "old"}])

        desired = cm.build_desired_from_config()
        ids = [d["id"] for d in desired]
        assert ids == ["arm-7"]
        assert "arm-1" not in ids


class TestBuildDesiredFallsBackToWarehouse:
    def test_falls_back_when_no_topology(self, monkeypatch):
        """When get_topology() returns None, legacy arms+AGVs path is used."""
        from app.services import container_manager as cm
        from app.models import pipeline_config as pc

        monkeypatch.setattr(pc, "get_topology", lambda: None)
        monkeypatch.setattr(pc, "get_arms", lambda: [
            {"id": 1, "label": "Source"},
            {"id": 2, "label": "Dest"},
        ])
        monkeypatch.setattr(pc, "get_agvs", lambda: [
            {"id": "agv-1", "label": "Shuttle"},
        ])

        desired = cm.build_desired_from_config()
        ids = {d["id"] for d in desired}
        assert ids == {"arm-1", "arm-2", "agv-1"}

        by_id = {d["id"]: d for d in desired}
        assert by_id["arm-1"]["kind"] == "arm"
        assert by_id["agv-1"]["kind"] == "agv"
        # No spatial fields in legacy path (not from topology).
        assert "x" not in by_id["arm-1"]
        assert "y" not in by_id["arm-1"]
