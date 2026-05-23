"""Phase 4 Stage 4 — LEG_* event tests.

Five tests verifying the emission, ordering, and observational nature of
LEG_STARTED / LEG_COMPLETED / LEG_HALTED on the negotiation bus, plus
executed_legs rehydration from bus history.
"""

from __future__ import annotations

import time
from typing import Optional
from unittest.mock import patch

import pytest

from app.agents.actor_agent import ActorAgent, AgentState
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import (
    Message,
    MessageType,
    accepted_actors,
)
from app.agents.task_participant import TaskParticipant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile(actor_id: str) -> CapabilityProfile:
    return CapabilityProfile(
        actor_id=actor_id,
        kind="arm",
        reachable_zones=["input_arm", "output_arm"],
    )


def _announce(task_id: str = "task-leg-1", bid_window: float = 0.4,
              consensus_window: float = 0.6) -> Message:
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=0,
        sender="interpreter",
        payload={
            "source_zone": "input_arm",
            "dest_zone": "output_arm",
            "constraint": "",
            "slots": [
                {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
                {"slot_id": "transport", "kind": "agv",
                 "from_zone": "input_arm", "to_zone": "output_arm"},
                {"slot_id": "place", "kind": "arm", "zone": "output_arm"},
            ],
            "announce_ts": now,
            "bid_deadline_ts": now + bid_window,
            "consensus_deadline_ts": now + bid_window + consensus_window,
        },
    )


def _make_participant(
    actor_id: str,
    bus: NegotiationBus,
    task_id: str = "task-leg-1",
    round_: int = 0,
) -> TaskParticipant:
    """Create a minimal TaskParticipant with stub callables."""
    return TaskParticipant(
        actor_id=actor_id,
        bus=bus,
        bid_score_fn=lambda slot: None,
        execute_leg_fn=lambda slot_id, tid, rnd: True,
        fault_requested_fn=lambda: False,
        capability_broadcast_fn=lambda tid, rnd: None,
        task_id=task_id,
        round=round_,
        task={
            "slots": [
                {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
                {"slot_id": "transport", "kind": "agv",
                 "from_zone": "input_arm", "to_zone": "output_arm"},
                {"slot_id": "place", "kind": "arm", "zone": "output_arm"},
            ],
            "constraint": "",
            "bid_deadline_ts": time.time() - 1,  # already past
            "consensus_deadline_ts": time.time() + 60,
        },
    )


# ---------------------------------------------------------------------------
# Stub agents
# ---------------------------------------------------------------------------

class _WinnerAgent(ActorAgent):
    """Agent that bids on everything and returns True from _execute_leg."""

    def _bid_score(self, slot: dict) -> Optional[float]:
        return 1.0  # bid on every slot

    def _execute_leg(self, slot_id: str) -> bool:
        return True  # always completes


class _HaltingAgent(ActorAgent):
    """Agent that bids on everything but halts on _execute_leg."""

    def _bid_score(self, slot: dict) -> Optional[float]:
        return 1.0

    def _execute_leg(self, slot_id: str) -> bool:
        return False  # always halts


# ---------------------------------------------------------------------------
# Test 1: LEG_STARTED then LEG_COMPLETED emitted for a full leg
# ---------------------------------------------------------------------------

def test_leg_started_completed_emitted_on_full_leg():
    """Single converging task with one agent winning all slots → for each held
    slot there must be exactly one LEG_STARTED followed by one LEG_COMPLETED,
    in that order, with sender == actor_id.
    """
    bus = NegotiationBus()
    agent = _WinnerAgent(_profile("arm-winner"), bus)
    agent.start()
    try:
        bus.publish(_announce("task-full-leg"))
        # bid window (0.4) + consensus window (0.6) + execution + buffer
        time.sleep(3.0)
    finally:
        agent.stop()

    history = bus.history()

    leg_started = [
        m for m in history
        if m.type == MessageType.LEG_STARTED
        and m.task_id == "task-full-leg"
        and m.sender == "arm-winner"
    ]
    leg_completed = [
        m for m in history
        if m.type == MessageType.LEG_COMPLETED
        and m.task_id == "task-full-leg"
        and m.sender == "arm-winner"
    ]
    leg_halted = [
        m for m in history
        if m.type == MessageType.LEG_HALTED
        and m.task_id == "task-full-leg"
        and m.sender == "arm-winner"
    ]

    # Must have at least one slot executed (the agent wins all arm slots).
    assert leg_started, "No LEG_STARTED emitted for the winning agent"
    assert leg_completed, "No LEG_COMPLETED emitted for the winning agent"
    assert not leg_halted, f"Unexpected LEG_HALTED: {leg_halted}"

    # Counts must match (one STARTED + one COMPLETED per slot).
    assert len(leg_started) == len(leg_completed), (
        f"LEG_STARTED count ({len(leg_started)}) != LEG_COMPLETED count ({len(leg_completed)})"
    )

    # Order: for each slot, STARTED must precede COMPLETED.
    # Build a timeline of (index, type, slot_id) for arm-winner.
    timeline = [
        (i, m.type, m.slot_id)
        for i, m in enumerate(history)
        if m.sender == "arm-winner"
        and m.type in (MessageType.LEG_STARTED, MessageType.LEG_COMPLETED)
        and m.task_id == "task-full-leg"
    ]
    # Group by slot_id and verify order within each group.
    from collections import defaultdict
    by_slot: dict = defaultdict(list)
    for idx, mtype, slot_id in timeline:
        by_slot[slot_id].append((idx, mtype))

    for slot_id, events in by_slot.items():
        events.sort()  # sort by index
        types = [e[1] for e in events]
        assert types[0] == MessageType.LEG_STARTED, (
            f"First event for slot {slot_id} was not LEG_STARTED: {types}"
        )
        assert types[-1] == MessageType.LEG_COMPLETED, (
            f"Last event for slot {slot_id} was not LEG_COMPLETED: {types}"
        )

    # sender is always the publishing actor's own id.
    for m in leg_started + leg_completed:
        assert m.sender == "arm-winner", f"Unexpected sender on LEG_* msg: {m.sender}"


# ---------------------------------------------------------------------------
# Test 2: LEG_HALTED emitted when _execute_leg returns False
# ---------------------------------------------------------------------------

def test_leg_halted_emitted_on_halt():
    """Agent wins slots but _execute_leg returns False → LEG_STARTED then
    LEG_HALTED; no LEG_COMPLETED.
    """
    bus = NegotiationBus()
    agent = _HaltingAgent(_profile("arm-halter"), bus)
    agent.start()
    try:
        bus.publish(_announce("task-halt-leg"))
        time.sleep(3.0)
    finally:
        agent.stop()

    history = bus.history()

    leg_started = [
        m for m in history
        if m.type == MessageType.LEG_STARTED
        and m.task_id == "task-halt-leg"
        and m.sender == "arm-halter"
    ]
    leg_halted = [
        m for m in history
        if m.type == MessageType.LEG_HALTED
        and m.task_id == "task-halt-leg"
        and m.sender == "arm-halter"
    ]
    leg_completed = [
        m for m in history
        if m.type == MessageType.LEG_COMPLETED
        and m.task_id == "task-halt-leg"
        and m.sender == "arm-halter"
    ]

    assert leg_started, "No LEG_STARTED emitted for halting agent"
    assert leg_halted, "No LEG_HALTED emitted when _execute_leg returns False"
    assert not leg_completed, f"Unexpected LEG_COMPLETED on a halting agent: {leg_completed}"

    # Counts match: one STARTED per HALTED.
    assert len(leg_started) == len(leg_halted), (
        f"LEG_STARTED ({len(leg_started)}) != LEG_HALTED ({len(leg_halted)})"
    )


# ---------------------------------------------------------------------------
# Test 3: executed_legs rebuilt from pre-seeded history (no real execution)
# ---------------------------------------------------------------------------

def test_executed_legs_rebuilt_from_history():
    """Create a fresh TaskParticipant with a bus pre-seeded with a LEG_COMPLETED
    message for (sender=actor_id, task_id). The participant's executed_legs must
    include that slot_id WITHOUT any real execution having occurred.
    """
    bus = NegotiationBus()
    task_id = "task-rehydrate"
    actor_id = "arm-rehydrate"

    # Pre-seed the bus with a LEG_COMPLETED for the pickup slot.
    bus.publish(
        Message(
            type=MessageType.LEG_COMPLETED,
            task_id=task_id,
            negotiation_round=0,
            sender=actor_id,
            slot_id="pickup",
            payload={"slot_id": "pickup", "task_id": task_id, "round": 0},
        )
    )

    # Also publish a LEG_COMPLETED from a different actor — must NOT appear.
    bus.publish(
        Message(
            type=MessageType.LEG_COMPLETED,
            task_id=task_id,
            negotiation_round=0,
            sender="arm-other",
            slot_id="place",
            payload={"slot_id": "place", "task_id": task_id, "round": 0},
        )
    )

    # Also publish a LEG_COMPLETED for a different task — must NOT appear.
    bus.publish(
        Message(
            type=MessageType.LEG_COMPLETED,
            task_id="task-other",
            negotiation_round=0,
            sender=actor_id,
            slot_id="transport",
            payload={"slot_id": "transport", "task_id": "task-other", "round": 0},
        )
    )

    # Create participant — __post_init__ calls _rehydrate_executed_legs.
    participant = _make_participant(actor_id, bus, task_id=task_id)

    assert "pickup" in participant.executed_legs, (
        "Rehydration failed: 'pickup' not in executed_legs despite LEG_COMPLETED on bus"
    )
    assert "place" not in participant.executed_legs, (
        "Rehydration leaked: 'place' (from arm-other) appeared in executed_legs"
    )
    assert "transport" not in participant.executed_legs, (
        "Rehydration leaked: 'transport' (from task-other) appeared in executed_legs"
    )


# ---------------------------------------------------------------------------
# Test 4: no double execution after rehydration (simulated restart)
# ---------------------------------------------------------------------------

def test_no_double_execution_after_rehydration():
    """An actor 'restarts' (new ActorAgent instance, same bus history).

    On the next consensus check, the prior LEG_COMPLETED slot must be skipped
    — verified by confirming _execute_leg is NOT called again for that slot.
    """
    bus = NegotiationBus()
    task_id = "task-no-double"
    actor_id = "arm-restart"

    # Simulate a prior run: put a LEG_COMPLETED for "pickup" on the bus.
    bus.publish(
        Message(
            type=MessageType.LEG_COMPLETED,
            task_id=task_id,
            negotiation_round=0,
            sender=actor_id,
            slot_id="pickup",
            payload={"slot_id": "pickup", "task_id": task_id, "round": 0},
        )
    )

    # Track execution calls.
    executed_slots: list[str] = []

    class _TrackingAgent(ActorAgent):
        def _bid_score(self, slot: dict) -> Optional[float]:
            return 1.0

        def _execute_leg(self, slot_id: str) -> bool:
            executed_slots.append(slot_id)
            return True

    # "Restart": fresh agent instance, same bus (which has the LEG_COMPLETED).
    agent = _TrackingAgent(_profile(actor_id), bus)
    agent.start()
    try:
        bus.publish(_announce(task_id, bid_window=0.4, consensus_window=0.6))
        time.sleep(3.0)
    finally:
        agent.stop()

    # "pickup" must NOT appear in executed_slots — it was already completed.
    assert "pickup" not in executed_slots, (
        f"Agent re-executed 'pickup' after rehydration. executed_slots={executed_slots}"
    )


# ---------------------------------------------------------------------------
# Test 5: LEG_* messages are observational (sender == publishing actor)
# ---------------------------------------------------------------------------

def test_leg_events_are_observational():
    """Every LEG_* message in the bus history must have the publishing actor's
    own id as sender — never another actor's id. Pure audit trail, not authority.

    Two agents each win their own slots in one task; we verify that each
    agent's LEG_* messages carry only their own actor_id as sender.
    """
    bus = NegotiationBus()

    # arm-obs-1 can serve input_arm; arm-obs-2 can serve output_arm.
    # They bid on their respective slots only, so there is no competition.
    class _ZoneArm(ActorAgent):
        def __init__(self, actor_id: str, zone: str, bus: NegotiationBus) -> None:
            super().__init__(
                CapabilityProfile(actor_id=actor_id, kind="arm", reachable_zones=[zone]),
                bus,
            )
            self._my_zone = zone

        def _bid_score(self, slot: dict) -> Optional[float]:
            if slot.get("kind") == "arm" and slot.get("zone") == self._my_zone:
                return 1.0
            return None

        def _execute_leg(self, slot_id: str) -> bool:
            return True

    agent_a = _ZoneArm("arm-obs-1", "input_arm", bus)
    agent_b = _ZoneArm("arm-obs-2", "output_arm", bus)

    for a in (agent_a, agent_b):
        a.start()
    try:
        bus.publish(_announce("task-obs", bid_window=0.4, consensus_window=0.6))
        time.sleep(3.0)
    finally:
        for a in (agent_a, agent_b):
            a.stop()

    history = bus.history()
    leg_msgs = [
        m for m in history
        if m.type in (
            MessageType.LEG_STARTED,
            MessageType.LEG_COMPLETED,
            MessageType.LEG_HALTED,
        )
    ]

    # Every LEG_* message sender must be one of the two known agent ids.
    known_actors = {"arm-obs-1", "arm-obs-2"}
    for m in leg_msgs:
        assert m.sender in known_actors, (
            f"LEG_* message has unexpected sender '{m.sender}' — "
            "only the publishing actor's own id should appear"
        )

    # arm-obs-1's LEG_* must all have sender == arm-obs-1.
    for m in leg_msgs:
        if m.slot_id == "pickup":
            assert m.sender == "arm-obs-1", (
                f"pickup LEG_* msg sender should be arm-obs-1, got '{m.sender}'"
            )
        if m.slot_id == "place":
            assert m.sender == "arm-obs-2", (
                f"place LEG_* msg sender should be arm-obs-2, got '{m.sender}'"
            )
