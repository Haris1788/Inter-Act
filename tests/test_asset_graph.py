"""Tests for the unified asset graph (Phase 1 backend foundation).

Covers:
- arms-only configs (legacy back-compat)
- mixed arms + vehicle + conveyor configs
- /config response shape exposes asset_graph
- graph validation rejects invalid references and detects cycles
- registry helpers (get_all_assets, get_asset, get_assets_by_type)
"""

from contextlib import contextmanager

import pytest

from app.models import pipeline_config


@contextmanager
def fake_config(cfg_dict):
    """Inject a fake parsed pipeline.yaml into the SSOT module."""
    saved = pipeline_config._config
    pipeline_config._config = cfg_dict
    try:
        yield
    finally:
        pipeline_config._config = saved


def _arms_only_cfg():
    return {
        "pipeline": {"mode": "dependency"},
        "failure_policy": {"default": "stop"},
        "health": {"check_interval": 2.0, "timeout": 5.0, "max_missed": 3},
        "arms": [
            {"id": 1, "label": "Arm 1", "color": "#e74c3c", "depends_on": [], "critical": True},
            {"id": 2, "label": "Arm 2", "color": "#2ecc71", "depends_on": [1], "critical": True},
            {"id": 3, "label": "Arm 3", "color": "#3498db", "depends_on": [2], "critical": True},
        ],
    }


def _mixed_cfg():
    cfg = _arms_only_cfg()
    cfg["assets"] = [
        {
            "id": "veh-a", "type": "vehicle", "label": "Shuttle A", "color": "#9b59b6",
            "speed": 120, "loop": False,
            "waypoints": [{"x": 730, "y": 300}, {"x": 900, "y": 300}],
            "upstream": ["arm-2"], "downstream": ["conv-1"],
        },
        {
            "id": "conv-1", "type": "conveyor", "label": "Belt 1", "color": "#34495e",
            "from": {"x": 900, "y": 300}, "to": {"x": 1250, "y": 300},
            "slot_count": 4, "shift_interval": 1.2,
            "upstream": ["veh-a"], "downstream": ["arm-3"],
        },
    ]
    return cfg


# ---------------------------------------------------------------------------
# Arms-only registry
# ---------------------------------------------------------------------------

class TestArmsOnly:
    def test_registry_contains_only_arms(self):
        with fake_config(_arms_only_cfg()):
            assets = pipeline_config.get_all_assets()
        assert len(assets) == 3
        assert all(a["type"] == "arm" for a in assets)
        assert [a["id"] for a in assets] == ["arm-1", "arm-2", "arm-3"]

    def test_arm_upstream_downstream_derived_from_depends_on(self):
        with fake_config(_arms_only_cfg()):
            assets = {a["id"]: a for a in pipeline_config.get_all_assets()}
        assert assets["arm-1"]["upstream"] == []
        assert assets["arm-1"]["downstream"] == ["arm-2"]
        assert assets["arm-2"]["upstream"] == ["arm-1"]
        assert assets["arm-2"]["downstream"] == ["arm-3"]
        assert assets["arm-3"]["upstream"] == ["arm-2"]
        assert assets["arm-3"]["downstream"] == []

    def test_arm_geometry_uses_layout_engine_pivots(self):
        with fake_config(_arms_only_cfg()):
            assets = pipeline_config.get_all_assets()
        for a in assets:
            geom = a["geometry"]
            assert "pivot" in geom and "x" in geom["pivot"] and "y" in geom["pivot"]
            assert geom["length"] > 0
            assert geom["min_angle"] == pipeline_config.MIN_ANGLE
            assert geom["max_angle"] == pipeline_config.MAX_ANGLE

    def test_legacy_validation_still_works(self):
        with fake_config(_arms_only_cfg()):
            valid, errors = pipeline_config.validate_dependency_graph()
        assert valid
        assert errors == []


# ---------------------------------------------------------------------------
# Mixed registry: arms + vehicle + conveyor
# ---------------------------------------------------------------------------

class TestMixedAssets:
    def test_registry_includes_all_asset_types(self):
        with fake_config(_mixed_cfg()):
            assets = pipeline_config.get_all_assets()
        ids = [a["id"] for a in assets]
        assert ids == ["arm-1", "arm-2", "arm-3", "veh-a", "conv-1"]

    def test_arm_downstream_bridges_to_non_arm_assets(self):
        with fake_config(_mixed_cfg()):
            arm2 = pipeline_config.get_asset("arm-2")
            arm3 = pipeline_config.get_asset("arm-3")
        assert "veh-a" in arm2["downstream"]
        assert "conv-1" in arm3["upstream"]

    def test_vehicle_upstream_downstream_preserved(self):
        with fake_config(_mixed_cfg()):
            veh = pipeline_config.get_asset("veh-a")
        assert veh["type"] == "vehicle"
        assert veh["upstream"] == ["arm-2"]
        assert veh["downstream"] == ["conv-1"]

    def test_conveyor_geometry_has_slot_positions(self):
        with fake_config(_mixed_cfg()):
            conv = pipeline_config.get_asset("conv-1")
        slots = conv["geometry"]["slot_positions"]
        assert len(slots) == 4
        assert slots[0]["x"] == conv["geometry"]["from"]["x"]
        assert slots[-1]["x"] == conv["geometry"]["to"]["x"]

    def test_get_assets_by_type_filters_correctly(self):
        with fake_config(_mixed_cfg()):
            arms = pipeline_config.get_assets_by_type("arm")
            vehicles = pipeline_config.get_assets_by_type("vehicle")
            conveyors = pipeline_config.get_assets_by_type("conveyor")
        assert len(arms) == 3
        assert len(vehicles) == 1
        assert len(conveyors) == 1

    def test_get_asset_returns_none_for_unknown_id(self):
        with fake_config(_mixed_cfg()):
            assert pipeline_config.get_asset("nope") is None


# ---------------------------------------------------------------------------
# /config response shape
# ---------------------------------------------------------------------------

class TestSceneConfigShape:
    def test_arms_only_keys_preserved(self):
        with fake_config(_arms_only_cfg()):
            scene = pipeline_config.generate_scene_config()
        for k in ("canvas_width", "canvas_height", "arms", "assets",
                  "reach_zones", "handover_zones", "intake_zone",
                  "output_zone", "labels", "min_angle", "max_angle",
                  "default_angle"):
            assert k in scene, f"existing key missing: {k}"

    def test_asset_graph_present_in_response(self):
        with fake_config(_arms_only_cfg()):
            scene = pipeline_config.generate_scene_config()
        assert "asset_graph" in scene
        assert isinstance(scene["asset_graph"], list)
        assert len(scene["asset_graph"]) == 3
        assert "graph_valid" in scene and scene["graph_valid"] is True
        assert scene["graph_errors"] == []

    def test_mixed_response_includes_all_assets(self):
        with fake_config(_mixed_cfg()):
            scene = pipeline_config.generate_scene_config()
        assert len(scene["asset_graph"]) == 5
        types = [a["type"] for a in scene["asset_graph"]]
        assert types.count("arm") == 3
        assert types.count("vehicle") == 1
        assert types.count("conveyor") == 1

    def test_back_compat_arms_array_unchanged_for_legacy_consumers(self):
        with fake_config(_arms_only_cfg()):
            scene = pipeline_config.generate_scene_config()
        assert isinstance(scene["arms"], list)
        # Legacy frontend expects integer ids on arms[].
        assert all(isinstance(a["id"], int) for a in scene["arms"])


# ---------------------------------------------------------------------------
# Graph validation
# ---------------------------------------------------------------------------

class TestAssetGraphValidation:
    def test_valid_arms_only_graph(self):
        with fake_config(_arms_only_cfg()):
            valid, errors = pipeline_config.validate_asset_graph()
        assert valid
        assert errors == []

    def test_valid_mixed_graph(self):
        with fake_config(_mixed_cfg()):
            valid, errors = pipeline_config.validate_asset_graph()
        assert valid, errors

    def test_missing_arm_dependency_rejected(self):
        cfg = _arms_only_cfg()
        cfg["arms"][1]["depends_on"] = [99]
        with fake_config(cfg):
            valid, errors = pipeline_config.validate_asset_graph()
        assert not valid
        assert any("99" in e for e in errors)

    def test_unknown_upstream_reference_rejected(self):
        cfg = _mixed_cfg()
        cfg["assets"][0]["upstream"] = ["arm-99"]
        with fake_config(cfg):
            valid, errors = pipeline_config.validate_asset_graph()
        assert not valid
        assert any("arm-99" in e for e in errors)

    def test_unknown_downstream_reference_rejected(self):
        cfg = _mixed_cfg()
        cfg["assets"][1]["downstream"] = ["ghost-1"]
        with fake_config(cfg):
            valid, errors = pipeline_config.validate_asset_graph()
        assert not valid
        assert any("ghost-1" in e for e in errors)

    def test_unknown_asset_type_rejected(self):
        cfg = _mixed_cfg()
        cfg["assets"].append({
            "id": "drone-1", "type": "drone",
            "upstream": ["arm-3"], "downstream": [],
        })
        with fake_config(cfg):
            valid, errors = pipeline_config.validate_asset_graph()
        assert not valid
        assert any("drone" in e for e in errors)

    def test_cycle_in_arm_graph_detected(self):
        cfg = _arms_only_cfg()
        cfg["arms"][0]["depends_on"] = [3]   # arm-1 <- arm-3, with arm-3 <- arm-2 <- arm-1 cycle
        with fake_config(cfg):
            valid, errors = pipeline_config.validate_asset_graph()
        assert not valid
        assert any("cycle" in e.lower() for e in errors)

    def test_cycle_across_asset_types_detected(self):
        cfg = _mixed_cfg()
        # Add a back-edge: conveyor -> vehicle creates a cycle.
        cfg["assets"][1]["downstream"].append("veh-a")
        cfg["assets"][0]["upstream"].append("conv-1")
        with fake_config(cfg):
            valid, errors = pipeline_config.validate_asset_graph()
        assert not valid
        assert any("cycle" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Sanity: existing arm_state generator still uses arms-only contract
# ---------------------------------------------------------------------------

class TestArmStateBackCompat:
    def test_generate_arm_state_unaffected_by_assets_block(self):
        with fake_config(_mixed_cfg()):
            state = pipeline_config.generate_arm_state()
        assert set(state.keys()) == {1, 2, 3}
        for arm in state.values():
            assert arm["angle"] == pipeline_config.DEFAULT_ANGLE
            assert arm["status"] == "idle"
