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


def test_capability_profile_defaults():
    p = CapabilityProfile(actor_id="arm-1", kind="arm")
    assert p.reachable_zones == []
    assert p.position == [0.0, 0.0]
    assert p.speed == 1.0
    assert p.capacity == 1
