"""Phase 5 Stage 3 — capability.load_profiles topology path tests.

Verifies that:
  - load_profiles() uses topology actors when topology block is present,
    including reachable_zones=[actor_id] for arms.
  - Falls back to the legacy warehouse path when no topology.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_topology(arm_specs, agv_specs=None):
    from app.models.topology import ActorSpec, Topology

    actors = []
    for s in (arm_specs or []):
        actors.append(ActorSpec(
            id=s["id"], kind="arm",
            x=s.get("x", 100.0), y=s.get("y", 300.0),
            reach_px=s.get("reach_px", 120.0),
            capacity=s.get("capacity", 1),
            speed=s.get("speed", 1.0),
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

class TestLoadProfilesUsesTopologyWhenPresent:
    def test_topology_profiles_have_correct_ids_and_kinds(self, monkeypatch):
        """load_profiles() returns one profile per topology actor."""
        from app.agents import capability
        from app.models import pipeline_config as pc

        topo = _make_topology(
            arm_specs=[
                {"id": "arm-1", "x": 120.0, "y": 300.0, "reach_px": 140.0},
                {"id": "arm-2", "x": 480.0, "y": 300.0, "capacity": 2},
            ],
            agv_specs=[
                {"id": "agv-1", "x": 300.0, "y": 600.0, "speed": 1.5},
            ],
        )
        monkeypatch.setattr(pc, "get_topology", lambda: topo)

        profiles = capability.load_profiles()
        by_id = {p.actor_id: p for p in profiles}

        assert set(by_id) == {"arm-1", "arm-2", "agv-1"}

        arm1 = by_id["arm-1"]
        assert arm1.kind == "arm"
        assert arm1.position_px == pytest.approx((120.0, 300.0))
        assert arm1.reach_px == pytest.approx(140.0)
        # Arms get own id as zone so Phase 4 zone-match still pins slots.
        assert arm1.reachable_zones == ["arm-1"]

        arm2 = by_id["arm-2"]
        assert arm2.capacity == 2
        assert arm2.reachable_zones == ["arm-2"]

        agv1 = by_id["agv-1"]
        assert agv1.kind == "agv"
        assert agv1.speed == pytest.approx(1.5)
        # AGVs reach all arm ids.
        assert set(agv1.reachable_zones) == {"arm-1", "arm-2"}

    def test_arm_reachable_zones_pins_to_own_id(self, monkeypatch):
        """reachable_zones=[actor_id] is the Phase 4 zone-match invariant."""
        from app.agents import capability
        from app.models import pipeline_config as pc

        topo = _make_topology(arm_specs=[{"id": "arm-5", "x": 800.0, "y": 300.0}])
        monkeypatch.setattr(pc, "get_topology", lambda: topo)

        profiles = capability.load_profiles()
        arm5 = next(p for p in profiles if p.actor_id == "arm-5")
        assert arm5.reachable_zones == ["arm-5"]


class TestLoadProfilesFallsBackToWarehouse:
    def test_legacy_path_when_no_topology(self, monkeypatch):
        """load_profiles() falls back to warehouse when topology is None."""
        from app.agents import capability
        from app.models import pipeline_config as pc

        fake_config = {
            "arms": [
                {"id": 1, "label": "Input Arm"},
                {"id": 2, "label": "Output Arm"},
            ],
            "warehouse": {
                "stations": {
                    "input_arm": {"served_by": "arm-1", "cell": [1, 1]},
                    "output_arm": {"served_by": "arm-2", "cell": [12, 1]},
                },
                "agvs": [
                    {"id": "agv-1", "bay": [6, 3]},
                ],
            },
        }
        monkeypatch.setattr(pc, "get_config", lambda: fake_config)
        monkeypatch.setattr(pc, "get_topology", lambda: None)

        profiles = capability.load_profiles()
        by_id = {p.actor_id: p for p in profiles}

        assert set(by_id) == {"arm-1", "arm-2", "agv-1"}
        assert "input_arm" in by_id["arm-1"].reachable_zones
        assert "output_arm" in by_id["arm-2"].reachable_zones
        # AGV reaches all station zones.
        assert "input_arm" in by_id["agv-1"].reachable_zones
        assert "output_arm" in by_id["agv-1"].reachable_zones
