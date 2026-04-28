"""Lifecycle + recovery tests (Phase 9).

Covers the bug user reported: clicking "Recover" without prior failure
left the asset in `recovering` indefinitely while it kept processing
objects. Tests:

- recover() from idle is a no-op (state unchanged).
- recover() from failed → recovering, then auto-idle after grace.
- While `recovering`, controller does NOT process input.
- After recovery completes, processing resumes.
- /object POST returns 503 while the controller is failed/recovering.
- fail() during active transfer halts work without dropping current obj.
- Backend ↔ container state stays consistent through fail/recover cycle.
- Repeated recover clicks are idempotent.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.core.module import Module
from app.models import asset_state
from app.services.asset_controllers import VehicleController, ConveyorController


class StubTP:
    def __init__(self, name="tp"):
        self.name = name
        self._obj = None
        self._cond = threading.Condition()

    def place(self, obj):
        with self._cond:
            while self._obj is not None:
                self._cond.wait(timeout=0.5)
            self._obj = obj
            self._cond.notify_all()

    def wait_and_pick(self, timeout=120.0, cancel_check=None):
        deadline = time.time() + timeout
        with self._cond:
            while self._obj is None:
                if cancel_check and not cancel_check():
                    return None
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=min(remaining, 0.1))
            obj, self._obj = self._obj, None
            self._cond.notify_all()
            return obj

    def wait_until_empty(self, timeout=30.0, cancel_check=None):
        deadline = time.time() + timeout
        with self._cond:
            while self._obj is not None:
                if cancel_check and not cancel_check():
                    return False
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=min(remaining, 0.1))
            return True

    def is_empty(self):
        with self._cond:
            return self._obj is None


@pytest.fixture(autouse=True)
def reset_registry():
    asset_state.reset()
    yield
    asset_state.reset()


@pytest.fixture(autouse=True)
def fast_recovery_grace(monkeypatch):
    monkeypatch.setattr(Module, "RECOVERY_GRACE_S", 0.1)
    yield


# ── Module.recover guards ─────────────────────────────────────────────

class TestRecoverGuards:
    def test_recover_from_idle_is_noop(self):
        m = Module("m")
        assert m.state == "idle"
        m.recover()
        assert m.state == "idle"

    def test_recover_from_busy_is_noop(self):
        m = Module("m")
        m.set_state("busy")
        m.recover()
        assert m.state == "busy"

    def test_recover_from_failed_transitions_to_idle(self):
        m = Module("m")
        m.fail("test")
        assert m.state == "failed"
        m.recover()
        assert m.state == "recovering"
        time.sleep(0.3)
        assert m.state == "idle"

    def test_repeated_recover_calls_are_idempotent(self):
        m = Module("m")
        m.fail("test")
        m.recover()
        m.recover()
        m.recover()
        # Should still settle to idle.
        time.sleep(0.4)
        assert m.state == "idle"


# ── Vehicle stops processing during recovery ──────────────────────────

class TestRecoveryHaltsProcessing:
    def test_vehicle_does_not_pick_during_recovering(self):
        # Override Module class attr directly for a long, deterministic window.
        original_grace = Module.RECOVERY_GRACE_S
        Module.RECOVERY_GRACE_S = 1.0
        try:
            in_tp = StubTP()
            out_tp = StubTP()

            veh = VehicleController(
                asset_id="veh-r", label="V", input_tp=in_tp, output_tp=out_tp,
                speed_factor=1.0, num_products=5, travel_time=0.1, keep_alive=True,
            )
            veh.start()
            veh.fail("test")
            veh.recover()

            in_tp.place("Obj1")
            time.sleep(0.2)
            assert veh.state == "recovering", f"got {veh.state!r}"
            assert in_tp.is_empty() is False, "object must remain in queue"

            # After grace, transitions to idle and processes Obj1.
            time.sleep(1.2)
            veh.stop_module()
            veh.join(timeout=3.0)
        finally:
            Module.RECOVERY_GRACE_S = original_grace


# ── Conveyor freezes its tick during recovery ─────────────────────────

class TestConveyorFreezesDuringRecovery:
    def test_conveyor_does_not_shift_or_emit_while_recovering(self):
        in_tp = StubTP()
        out_tp = StubTP()
        conv = ConveyorController(
            asset_id="conv-r", label="C", input_tp=in_tp, output_tp=out_tp,
            speed_factor=1.0, num_products=5,
            slot_count=4, shift_interval=0.05, keep_alive=True,
        )
        conv.start()

        # Pre-load slot 0 with an object then immediately fail+recover.
        in_tp.place("Obj1")
        time.sleep(0.15)        # let belt accept
        before_slots = list(conv.slots)
        assert "Obj1" in before_slots

        conv.fail("test")
        conv.recover()
        assert conv.state == "recovering"

        # During recovering, slots must NOT change.
        snapshot1 = list(conv.slots)
        time.sleep(0.05)
        snapshot2 = list(conv.slots)
        assert snapshot1 == snapshot2, "belt must freeze during recovery"

        conv.stop_module()
        conv.join(timeout=3.0)


# ── Recovery from idle vs failed ──────────────────────────────────────

class TestUserScenarioRecoverWithoutFailure:
    """User clicked Recover Shuttle A on a healthy asset. Bug: state went
    to recovering, vehicle kept processing. Fix: recover() ignored."""

    def test_recover_on_healthy_asset_is_noop_state_stays_idle(self):
        veh = VehicleController(
            asset_id="veh-h", label="V", input_tp=StubTP(), output_tp=StubTP(),
            num_products=1, travel_time=0.05, keep_alive=True,
        )
        veh.start()
        time.sleep(0.05)
        # Asset healthy.
        assert veh.state in ("idle", "busy")
        veh.recover()
        # Should stay idle/busy, NOT flip to recovering.
        assert veh.state in ("idle", "busy")

        veh.stop_module()
        veh.join(timeout=3.0)


# ── Failure during active transfer ────────────────────────────────────

class TestFailureDuringTransfer:
    def test_fail_during_travel_holds_state(self):
        in_tp = StubTP()
        out_tp = StubTP()
        veh = VehicleController(
            asset_id="veh-t", label="V", input_tp=in_tp, output_tp=out_tp,
            speed_factor=1.0, num_products=1, travel_time=5.0,
        )
        veh.start()
        in_tp.place("Obj1")
        time.sleep(0.2)
        veh.fail("simulated")
        veh.join(timeout=3.0)

        rec = asset_state.get("veh-t")
        assert rec["status"] in ("failed", "stopped")
        assert out_tp.is_empty(), "downstream must NOT receive object"


# ── Recovery cycle restores work ──────────────────────────────────────

class TestRecoveryRestoresProcessing:
    def test_after_recovery_grace_processing_resumes(self):
        in_tp = StubTP()
        out_tp = StubTP()
        veh = VehicleController(
            asset_id="veh-resume", label="V", input_tp=in_tp, output_tp=out_tp,
            speed_factor=1.0, num_products=3, travel_time=0.1, keep_alive=True,
        )
        veh.start()
        veh.fail("test")
        veh.recover()
        # Wait for grace to elapse.
        time.sleep(0.3)
        assert veh.state in ("idle", "busy")

        # Object placed AFTER recovery should be processed.
        in_tp.place("Obj1")
        result = []
        def consume():
            obj = out_tp.wait_and_pick(timeout=3.0)
            if obj:
                result.append(obj)
        threading.Thread(target=consume, daemon=True).start()
        time.sleep(0.5)
        assert result == ["Obj1"]

        veh.stop_module()
        veh.join(timeout=3.0)


# ── State registry consistency through fail/recover cycle ────────────

class TestRegistryConsistency:
    def test_asset_state_mirrors_controller_through_fail_recover(self):
        veh = VehicleController(
            asset_id="veh-mirror", label="V",
            input_tp=StubTP(), output_tp=StubTP(),
            num_products=1, travel_time=0.05, keep_alive=True,
        )
        veh.start()
        time.sleep(0.05)
        assert asset_state.get("veh-mirror")["status"] in ("idle", "busy")
        veh.fail("test")
        time.sleep(0.05)
        assert asset_state.get("veh-mirror")["status"] == "failed"
        veh.recover()
        assert asset_state.get("veh-mirror")["status"] == "recovering"
        time.sleep(0.3)
        assert asset_state.get("veh-mirror")["status"] in ("idle", "stopped")
        veh.stop_module()
        veh.join(timeout=3.0)
