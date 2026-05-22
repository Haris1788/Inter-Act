"""Unit tests for ArmAgent."""

from app.agents.arm_agent import ArmAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile


def _arm(actor_id="arm-1", zones=("input_arm",)):
    return ArmAgent(
        CapabilityProfile(
            actor_id=actor_id, kind="arm", reachable_zones=list(zones)
        ),
        NegotiationBus(),
    )


def test_arm_bids_on_reachable_arm_slot():
    agent = _arm("arm-1", zones=["input_arm"])
    score = agent._bid_score(
        {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"}
    )
    assert score == 0.0


def test_arm_does_not_bid_on_unreachable_arm_slot():
    agent = _arm("arm-1", zones=["input_arm"])
    score = agent._bid_score(
        {"slot_id": "place", "kind": "arm", "zone": "output_arm"}
    )
    assert score is None


def test_arm_does_not_bid_on_agv_slot():
    agent = _arm("arm-1", zones=["input_arm"])
    score = agent._bid_score(
        {"slot_id": "transport", "kind": "agv",
         "from_zone": "input_arm", "to_zone": "output_arm"}
    )
    assert score is None
