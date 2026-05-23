"""Phase 4 Stage 4 — Replay determinism test.

Verifies that deriving executed_legs / accepted_actors / consensus_announced
from a recorded bus.history() log is idempotent and deterministic: running the
derivation N times over the same history always produces the same state.
"""

from __future__ import annotations

import time

from app.agents.bus import NegotiationBus
from app.agents.negotiation import (
    Message,
    MessageType,
    accepted_actors,
    slots_complete,
)
from app.agents.task_participant import TaskParticipant


def _make_participant(
    actor_id: str,
    bus: NegotiationBus,
    task_id: str,
    round_: int = 0,
) -> TaskParticipant:
    """Create a minimal TaskParticipant whose bus history is pre-populated."""
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
            "bid_deadline_ts": time.time() - 1,
            "consensus_deadline_ts": time.time() + 60,
        },
    )


def test_state_function_of_history():
    """Given a recorded bus history of a converged scenario, deriving
    executed_legs / accepted_actors / consensus_announced via
    accepted_actors() + LEG_COMPLETED scan produces the same state regardless
    of how many times the derivation runs (idempotent, deterministic).

    Scenario: arm-1 won pickup, agv-1 won transport, arm-2 won place.
    Each actor published LEG_COMPLETED for its slot.
    """
    bus = NegotiationBus()
    task_id = "task-replay"
    round_ = 0
    now = time.time()

    # Populate a realistic negotiation history on the bus.
    messages = [
        # Task announce
        Message(
            type=MessageType.TASK_ANNOUNCE, task_id=task_id, negotiation_round=round_,
            sender="interpreter",
            payload={
                "source_zone": "input_arm", "dest_zone": "output_arm",
                "constraint": "shortest time",
                "slots": [
                    {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
                    {"slot_id": "transport", "kind": "agv",
                     "from_zone": "input_arm", "to_zone": "output_arm"},
                    {"slot_id": "place", "kind": "arm", "zone": "output_arm"},
                ],
                "announce_ts": now,
                "bid_deadline_ts": now + 1.5,
                "consensus_deadline_ts": now + 3.0,
            },
        ),
        # Bids
        Message(type=MessageType.BID, task_id=task_id, negotiation_round=round_,
                sender="arm-1", slot_id="pickup", payload={"score": 1.0}),
        Message(type=MessageType.BID, task_id=task_id, negotiation_round=round_,
                sender="agv-1", slot_id="transport", payload={"score": 2.0}),
        Message(type=MessageType.BID, task_id=task_id, negotiation_round=round_,
                sender="arm-2", slot_id="place", payload={"score": 1.0}),
        # Accepts
        Message(type=MessageType.ACCEPT, task_id=task_id, negotiation_round=round_,
                sender="arm-1", slot_id="pickup", payload={}),
        Message(type=MessageType.ACCEPT, task_id=task_id, negotiation_round=round_,
                sender="agv-1", slot_id="transport", payload={}),
        Message(type=MessageType.ACCEPT, task_id=task_id, negotiation_round=round_,
                sender="arm-2", slot_id="place", payload={}),
        # Consensus markers (observational — one per actor)
        Message(type=MessageType.CONSENSUS_REACHED, task_id=task_id, negotiation_round=round_,
                sender="arm-1", payload={"round": round_}),
        Message(type=MessageType.CONSENSUS_REACHED, task_id=task_id, negotiation_round=round_,
                sender="agv-1", payload={"round": round_}),
        Message(type=MessageType.CONSENSUS_REACHED, task_id=task_id, negotiation_round=round_,
                sender="arm-2", payload={"round": round_}),
        # LEG_STARTED + LEG_COMPLETED for each actor's leg
        Message(type=MessageType.LEG_STARTED, task_id=task_id, negotiation_round=round_,
                sender="arm-1", slot_id="pickup",
                payload={"slot_id": "pickup", "task_id": task_id, "round": round_}),
        Message(type=MessageType.LEG_COMPLETED, task_id=task_id, negotiation_round=round_,
                sender="arm-1", slot_id="pickup",
                payload={"slot_id": "pickup", "task_id": task_id, "round": round_}),
        Message(type=MessageType.LEG_STARTED, task_id=task_id, negotiation_round=round_,
                sender="agv-1", slot_id="transport",
                payload={"slot_id": "transport", "task_id": task_id, "round": round_}),
        Message(type=MessageType.LEG_COMPLETED, task_id=task_id, negotiation_round=round_,
                sender="agv-1", slot_id="transport",
                payload={"slot_id": "transport", "task_id": task_id, "round": round_}),
        Message(type=MessageType.LEG_STARTED, task_id=task_id, negotiation_round=round_,
                sender="arm-2", slot_id="place",
                payload={"slot_id": "place", "task_id": task_id, "round": round_}),
        Message(type=MessageType.LEG_COMPLETED, task_id=task_id, negotiation_round=round_,
                sender="arm-2", slot_id="place",
                payload={"slot_id": "place", "task_id": task_id, "round": round_}),
    ]
    for m in messages:
        bus.publish(m)

    required = {"pickup", "transport", "place"}

    def _derive_state_for(actor_id: str, bus: NegotiationBus) -> tuple:
        """Derive (executed_legs_set, accepted_actors_dict, consensus_reached) from history."""
        history = bus.history()
        # executed_legs: scan LEG_COMPLETED for this actor + task
        executed = {
            m.slot_id
            for m in history
            if m.type == MessageType.LEG_COMPLETED
            and m.task_id == task_id
            and m.sender == actor_id
            and m.slot_id is not None
        }
        # accepted_actors for round 0
        accepts = accepted_actors(history, task_id, round_)
        # consensus check
        consensus = slots_complete(required, accepts)
        return (frozenset(executed), dict(accepts), consensus)

    # Derive state for arm-1 ten times — must be identical each time.
    results = [_derive_state_for("arm-1", bus) for _ in range(10)]
    first = results[0]
    for i, r in enumerate(results[1:], start=2):
        assert r == first, (
            f"State derivation is non-deterministic: run 1 != run {i}\n"
            f"  run 1:   {first}\n"
            f"  run {i}: {r}"
        )

    # Verify the derived state is correct.
    executed_arm1, accepts_dict, consensus = first
    assert "pickup" in executed_arm1, "arm-1 executed_legs missing 'pickup'"
    assert accepts_dict == {
        "pickup": "arm-1", "transport": "agv-1", "place": "arm-2"
    }, f"Unexpected accepts: {accepts_dict}"
    assert consensus is True, "Consensus should be True for a fully converged scenario"

    # Same for agv-1 and arm-2 — each sees only their own LEG_COMPLETED.
    executed_agv1 = _derive_state_for("agv-1", bus)[0]
    executed_arm2 = _derive_state_for("arm-2", bus)[0]
    assert "transport" in executed_agv1, "agv-1 executed_legs missing 'transport'"
    assert "place" in executed_arm2, "arm-2 executed_legs missing 'place'"
    # Cross-contamination check: arm-1 must NOT see agv-1's or arm-2's LEG_COMPLETEDs.
    assert "transport" not in executed_arm1, "arm-1 executed_legs leaked 'transport'"
    assert "place" not in executed_arm1, "arm-1 executed_legs leaked 'place'"

    # Now create TaskParticipant objects and verify _rehydrate_executed_legs
    # is also idempotent: calling it multiple times on the same history
    # produces the same executed_legs list.
    p_arm1 = _make_participant("arm-1", bus, task_id)
    initial_legs = list(p_arm1.executed_legs)

    for _ in range(5):
        p_arm1._rehydrate_executed_legs(bus.history())
        assert list(p_arm1.executed_legs) == initial_legs, (
            "TaskParticipant._rehydrate_executed_legs is not idempotent: "
            f"repeated calls changed executed_legs to {p_arm1.executed_legs}"
        )
