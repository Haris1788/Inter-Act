"""Unit tests for actor capability profiles."""

from app.agents.capability import CapabilityProfile


def test_capability_profile_to_dict():
    p = CapabilityProfile(
        actor_id="agv-1",
        kind="agv",
        reachable_zones=["input_arm", "output_arm"],
        position=[10.0, 3.0],
        speed=2.0,
        capacity=1,
    )
    d = p.to_dict()
    assert d["actor_id"] == "agv-1"
    assert d["kind"] == "agv"
    assert d["reachable_zones"] == ["input_arm", "output_arm"]
    assert d["position"] == [10.0, 3.0]
    assert d["speed"] == 2.0
    assert d["capacity"] == 1
    d["reachable_zones"].append("extra")
    assert p.reachable_zones == ["input_arm", "output_arm"]


def test_capability_profile_defaults():
    p = CapabilityProfile(actor_id="arm-1", kind="arm")
    assert p.reachable_zones == []
    assert p.position == [0.0, 0.0]
    assert p.speed == 1.0
    assert p.capacity == 1


def test_load_profiles_from_config(monkeypatch):
    from app.agents import capability
    from app.models import pipeline_config

    fake = {
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
                {"id": "agv-1", "bay": [10, 3]},
                {"id": "agv-2", "bay": [10, 5]},
                {"id": "agv-3", "bay": [10, 7]},
            ],
        },
    }
    monkeypatch.setattr(pipeline_config, "_config", fake)

    profiles = capability.load_profiles()
    by_id = {p.actor_id: p for p in profiles}

    assert set(by_id) == {"arm-1", "arm-2", "agv-1", "agv-2", "agv-3"}

    assert by_id["arm-1"].kind == "arm"
    assert "input_arm" in by_id["arm-1"].reachable_zones
    assert "output_arm" in by_id["arm-2"].reachable_zones

    assert by_id["agv-1"].kind == "agv"
    assert set(by_id["agv-1"].reachable_zones) == {"input_arm", "output_arm"}
    assert by_id["agv-1"].position == [10.0, 3.0]
    assert by_id["agv-1"].speed == 1.0
    assert by_id["agv-1"].capacity == 1
