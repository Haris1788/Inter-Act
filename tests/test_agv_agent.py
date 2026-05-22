"""Unit tests for AGVAgent."""

from app.agents.agv_agent import AGVAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile


_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}


def _agv(actor_id="agv-1", position=(10, 3)):
    return AGVAgent(
        CapabilityProfile(
            actor_id=actor_id,
            kind="agv",
            reachable_zones=["input_arm", "output_arm"],
            position=[float(position[0]), float(position[1])],
        ),
        NegotiationBus(),
    )


def test_agv_does_not_bid_on_arm_slot():
    agent = _agv()
    assert agent._bid_score({"slot_id": "pickup", "kind": "arm"}) is None


def test_agv_bid_score_is_distance_to_pickup_dock(monkeypatch):
    from app.models import pipeline_config

    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    agent = _agv("agv-1", position=(10, 3))
    score = agent._bid_score(
        {"slot_id": "transport", "kind": "agv",
         "from_zone": "input_arm", "to_zone": "output_arm"}
    )
    # Manhattan distance from [10,3] to input_arm dock [2,1] = 8 + 2 = 10.
    assert score == 10.0


def test_closer_agv_scores_lower(monkeypatch):
    from app.models import pipeline_config

    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    near = _agv("agv-1", position=(3, 1))   # dist to [2,1] = 1
    far = _agv("agv-2", position=(10, 5))   # dist to [2,1] = 8 + 4 = 12
    slot = {"slot_id": "transport", "kind": "agv",
            "from_zone": "input_arm", "to_zone": "output_arm"}
    assert near._bid_score(slot) < far._bid_score(slot)


def test_agv_returns_none_for_unknown_zone(monkeypatch):
    from app.models import pipeline_config

    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    agent = _agv()
    score = agent._bid_score(
        {"slot_id": "transport", "kind": "agv",
         "from_zone": "nonexistent_zone", "to_zone": "output_arm"}
    )
    assert score is None
