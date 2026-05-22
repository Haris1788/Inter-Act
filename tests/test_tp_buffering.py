"""Regression tests for transfer-point passive buffering under downstream failure.

Covers:
- upstream arm can place into an empty TP when downstream is failed (buffering allowed)
- upstream arm blocks when TP is already occupied (capacity=1 enforced)
- TP holds the object until downstream recovers, then downstream picks normally
- ownership chain: arm → transfer_point → arm (always valid, even during failure)
- arm readiness signal propagation: arm is ready-for-input iff its output TP is free
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_demo import ArmController, TransferPoint


def _arm(
    arm_id,
    *,
    input_tp=None,
    output_tp=None,
    downstream_alive=None,
    is_terminal=False,
    events=None,
    num_products=3,
    **kw
):
    ev = [] if events is None else events
    return ArmController(
        arm_id=arm_id,
        host="http://unused",
        speed_factor=0,
        input_tp=input_tp,
        output_tp=output_tp,
        num_products=num_products,
        log_fn=lambda *_: None,
        set_angle_fn=lambda *_: None,
        object_event_fn=lambda oid, state, *_, **k: ev.append((oid, state, k)),
        downstream_alive_fn=downstream_alive or (lambda: True),
        is_terminal_fn=lambda: is_terminal,
        **kw,
    )


class TestTPBufferingUnderDownstreamFailure:

    def test_upstream_places_into_empty_tp_when_downstream_failed(self):
        """Core buffering case: TP is empty, downstream arm is failed.
        Upstream must be able to place - that's what the buffer is for."""
        tp = TransferPoint("TP arm-3->arm-4")
        events = []

        arm4 = _arm(4, input_tp=tp, is_terminal=True)
        arm4.fail("simulated failure")

        arm3 = _arm(3, output_tp=tp, events=events)
        arm3._process_one_product("Obj1")

        # Object must be sitting in the TP (buffered)
        assert not tp.is_empty()
        # Ownership transition arm → transfer_point must be emitted
        tp_events = [s for _, s, _ in events if s == "transfer_point"]
        assert tp_events, "must emit transfer_point ownership event"

    def test_upstream_blocks_when_tp_occupied_and_downstream_failed(self):
        """TP is already full and downstream is failed.
        Upstream must block - stacking is not allowed."""
        tp = TransferPoint("TP arm-3->arm-4")
        tp.place("Obj0")  # pre-occupy the slot

        arm4 = _arm(4, input_tp=tp, is_terminal=True)
        arm4.fail("simulated failure")

        arm3 = _arm(3, output_tp=tp)
        completed = threading.Event()

        def try_place():
            arm3._process_one_product("Obj1")
            completed.set()

        t = threading.Thread(target=try_place, daemon=True)
        t.start()
        time.sleep(0.35)

        assert not completed.is_set(), "arm must be blocked while TP is full"
        assert tp._object_id == "Obj0", "original object must still occupy the TP"

        arm3.stop_module()
        t.join(timeout=3.0)

    def test_arm_continues_after_tp_is_cleared(self):
        """Once the TP is drained (downstream recovered and picked), upstream
        can place the next object into the now-empty slot."""
        tp = TransferPoint("TP arm-3->arm-4")

        arm3 = _arm(3, output_tp=tp)
        arm4 = _arm(4, input_tp=tp, is_terminal=True)
        arm4.fail("simulated failure")

        # First product - buffers in TP
        arm3._process_one_product("Obj1")
        assert not tp.is_empty()

        # Simulate downstream recovery: manually drain the TP
        picked = tp.wait_and_pick(timeout=0.5)
        assert picked == "Obj1"
        assert tp.is_empty()

        # Second product - must succeed now
        arm3._process_one_product("Obj2")
        assert not tp.is_empty()
        assert tp._object_id == "Obj2"

    def test_tp_capacity_controls_arm_readiness_signal(self):
        """arm-3 reports ready-for-input when its output TP is free, and NOT
        ready when the TP is occupied - regardless of downstream module state."""
        tp = TransferPoint("TP arm-3->arm-4")

        arm4 = _arm(4, input_tp=tp, is_terminal=True)
        arm4.fail("simulated failure")

        arm3 = _arm(3, output_tp=tp, downstream_alive=lambda: arm4.is_ready_for_input())

        # TP empty → arm3 is ready for input even though downstream is failed
        assert arm3.tp_has_capacity() is True
        assert arm3.is_ready_for_input() is True

        # TP occupied → arm3 is NOT ready
        tp.place("Occupant")
        assert arm3.tp_has_capacity() is False
        assert arm3.is_ready_for_input() is False

    def test_upstream_cascade_blocks_when_tp_full(self):
        """Cascade check: arm-3's output TP full → arm-3 not ready for input.
        arm-2 can still buffer one object in its own TP, then blocks too."""
        tp34 = TransferPoint("TP arm-3->arm-4")
        tp23 = TransferPoint("TP arm-2->arm-3")

        arm4 = _arm(4, input_tp=tp34, is_terminal=True)
        arm4.fail("simulated failure")

        arm3 = _arm(
            3,
            input_tp=tp23,
            output_tp=tp34,
            downstream_alive=lambda: arm4.is_ready_for_input(),
        )

        # Directly fill arm-3's output TP (simulates arm-3 having buffered one object)
        tp34.place("Obj1")
        assert not tp34.is_empty()

        # arm-3 must not accept more input - its output slot is taken
        assert arm3.is_ready_for_input() is False

        # arm-2 checks its own TP (tp23), which is still empty → one buffer slot free
        arm2 = _arm(
            2, output_tp=tp23, downstream_alive=lambda: arm3.is_ready_for_input()
        )
        assert arm2.tp_has_capacity() is True

        # Once arm-2's TP fills, arm-2 also blocks
        tp23.place("Obj2")
        assert arm2.tp_has_capacity() is False
        assert not arm2.downstream_ready()

    def test_ownership_chain_valid_arm_to_tp_to_arm(self):
        """The arm → transfer_point → arm chain must emit the right owner fields."""
        tp = TransferPoint("TP arm-3->arm-4")
        arm3_events = []
        arm4_events = []

        arm4 = _arm(
            4, input_tp=tp, is_terminal=True, events=arm4_events, keep_alive=True
        )
        arm4.fail("test failure")

        arm3 = _arm(3, output_tp=tp, events=arm3_events)
        arm3._process_one_product("ObjX")

        # arm3 must have emitted a transfer_point event with correct owner fields
        tp_evt = next((e for e in arm3_events if e[1] == "transfer_point"), None)
        assert tp_evt is not None
        assert tp_evt[2].get("owner_type") == "transfer_point"
        assert tp_evt[2].get("owner_id") == "TP arm-3->arm-4"

        # Object currently in TP - now simulate arm-4 picking it
        arm4.recover()
        time.sleep(0.05)
        # Manually pick (simulating arm-4 thread picking from its input_tp)
        picked = tp.wait_and_pick(timeout=0.5)
        assert picked == "ObjX"
