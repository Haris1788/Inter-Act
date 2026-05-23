"""Tests for ActorAgent._bootstrap_from_history (Phase 4 Stage 5, Item 3).

Verifies that a restarted actor correctly rehydrates:
- active tasks from TASK_ANNOUNCE history
- my_slots from ACCEPT history (filtered to this actor only)
- executed_legs from LEG_COMPLETED history (via TaskParticipant.__post_init__)

Uses InProcessBus so no Redis/fakeredis dependency.
"""

from __future__ import annotations

import time

import pytest

from app.agents.arm_agent import ArmAgent
from app.agents.bus import InProcessBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType


_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}


@pytest.fixture(autouse=True)
def _patch_stations(monkeypatch):
    from app.models import pipeline_config
    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)


def _arm(bus, actor_id="arm-1", zone="input_arm", heartbeat_interval=60.0) -> ArmAgent:
    return ArmAgent(
        CapabilityProfile(actor_id=actor_id, kind="arm", reachable_zones=[zone]),
        bus,
        heartbeat_interval=heartbeat_interval,
    )


def _announce_msg(task_id="task-X", round=0, now=None):
    if now is None:
        now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=round,
        sender="interpreter",
        payload={
            "source_zone": "input_arm",
            "dest_zone": "output_arm",
            "constraint": "",
            "slots": [
                {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
            ],
            "announce_ts": now,
            "bid_deadline_ts": now + 100.0,
            "consensus_deadline_ts": now + 200.0,
        },
    )


def _bid_msg(actor_id, task_id="task-X", round=0, slot_id="pickup"):
    return Message(
        type=MessageType.BID,
        task_id=task_id,
        negotiation_round=round,
        sender=actor_id,
        slot_id=slot_id,
        payload={"score": 0.0, "basis": "arm"},
    )


def _accept_msg(actor_id, task_id="task-X", round=0, slot_id="pickup"):
    return Message(
        type=MessageType.ACCEPT,
        task_id=task_id,
        negotiation_round=round,
        sender=actor_id,
        slot_id=slot_id,
        payload={},
    )


def _leg_completed_msg(actor_id, task_id="task-X", round=0, slot_id="pickup"):
    return Message(
        type=MessageType.LEG_COMPLETED,
        task_id=task_id,
        negotiation_round=round,
        sender=actor_id,
        slot_id=slot_id,
        payload={"slot_id": slot_id, "task_id": task_id, "round": round},
    )


class TestBootstrapFromHistory:
    def test_actor_restart_rehydrates_active_task(self):
        """Fresh ArmAgent sees TASK_ANNOUNCE + BID + ACCEPT in history →
        _tasks[task_id] exists with my_slots=["pickup"]."""
        bus = InProcessBus()

        # Pre-seed history: announce → bid → accept for arm-1/pickup.
        bus.publish(_announce_msg())
        bus.publish(_bid_msg("arm-1"))
        bus.publish(_accept_msg("arm-1"))

        # Instantiate a "restarted" arm-1 against the same bus (same history).
        agent = _arm(bus, "arm-1")
        agent.start()
        # start() calls _bootstrap_from_history before spawning the event loop.
        # No sleep needed — bootstrap is synchronous within start().
        try:
            assert "task-X" in agent._tasks, (
                "task-X should be in _tasks after bootstrap"
            )
            participant = agent._tasks["task-X"]
            assert participant.my_slots == ["pickup"], (
                f"Expected my_slots=['pickup'], got {participant.my_slots}"
            )
        finally:
            agent.stop()

    def test_actor_restart_skips_already_completed_legs(self):
        """Pre-seeded LEG_COMPLETED for arm-1/pickup → executed_legs populated;
        the leg is NOT re-executed on next consensus check."""
        bus = InProcessBus()

        bus.publish(_announce_msg())
        bus.publish(_bid_msg("arm-1"))
        bus.publish(_accept_msg("arm-1"))
        bus.publish(_leg_completed_msg("arm-1"))

        agent = _arm(bus, "arm-1")
        agent.start()
        try:
            participant = agent._tasks.get("task-X")
            assert participant is not None, "task-X should exist after bootstrap"
            # executed_legs rehydrated from LEG_COMPLETED in bus history.
            assert "pickup" in participant.executed_legs, (
                f"Expected 'pickup' in executed_legs, got {participant.executed_legs}"
            )
            # my_slots also rehydrated.
            assert "pickup" in participant.my_slots, (
                f"Expected 'pickup' in my_slots, got {participant.my_slots}"
            )
        finally:
            agent.stop()

    def test_actor_fresh_start_no_active_tasks_empty_tasks_dict(self):
        """Empty bus → fresh actor → _tasks == {} after bootstrap."""
        bus = InProcessBus()
        # No messages published.
        agent = _arm(bus, "arm-fresh")
        agent.start()
        try:
            assert agent._tasks == {}, (
                f"Expected empty _tasks on fresh bus, got {agent._tasks}"
            )
        finally:
            agent.stop()
