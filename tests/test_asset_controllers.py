"""Tests for Phase 2 asset controllers (vehicle) + lifecycle.

Covers:
- arm -> vehicle -> arm (single asset chain)
- arm -> vehicle -> vehicle -> arm (multi-vehicle chain)
- vehicle failure blocks downstream
- recover() resumes processing
- asset_state registry transitions

These tests exercise the controllers in isolation by stubbing
TransferPoints and event callbacks. They do NOT spin up Flask or
sockets - pure threading + state machine validation.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.models import asset_state
from app.services.asset_controllers import VehicleController

# ── Test doubles ──────────────────────────────────────────────────────


class StubTP:
    """Minimal TransferPoint compatible with AssetController contract."""

    def __init__(self, name: str = "tp"):
        self.name = name
        self._obj = None
        self._cond = threading.Condition()

    def place(self, obj):
        with self._cond:
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
                self._cond.wait(timeout=min(remaining, 0.2))
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
                self._cond.wait(timeout=min(remaining, 0.2))
            return True

    def is_empty(self):
        with self._cond:
            return self._obj is None


class EventLog:
    """Capture object_event callbacks emitted by controllers."""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, obj_id, state, **extra):
        with self.lock:
            self.events.append((obj_id, state, dict(extra)))

    def states_for(self, obj_id):
        with self.lock:
            return [(s, e) for o, s, e in self.events if o == obj_id]


@pytest.fixture(autouse=True)
def reset_registry():
    asset_state.reset()
    yield
    asset_state.reset()


def _drain(controllers, timeout=10.0):
    """Wait for every controller to finish its threading.Thread.run()."""
    for c in controllers:
        c.join(timeout=timeout)


# ── arm -> vehicle -> arm ─────────────────────────────────────────────


class TestArmVehicleArmFlow:
    def test_vehicle_ferries_object_between_arms(self):
        in_tp = StubTP("arm1->veh")
        out_tp = StubTP("veh->arm2")
        ev = EventLog()

        veh = VehicleController(
            asset_id="veh-a",
            label="Shuttle",
            input_tp=in_tp,
            output_tp=out_tp,
            speed_factor=0.05,  # fast for test
            object_event_fn=ev,
            num_products=1,
            travel_time=0.3,
        )
        veh.start()

        in_tp.place("Obj1")  # arm1 places

        # Drain output as if arm2 picks immediately.
        def consumer():
            for _ in range(50):
                obj = out_tp.wait_and_pick(timeout=2.0)
                if obj is not None:
                    return obj
                time.sleep(0.02)
            return None

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        _drain([veh], timeout=5.0)

        states = [s for s, _ in ev.states_for("Obj1")]
        # Vehicle emits "carried" on accept.
        assert "carried" in states
        rec = asset_state.get("veh-a")
        assert rec["status"] in ("idle", "stopped")  # final state varies on timing
        assert rec["carried_object"] is None


# ── arm -> vehicle -> vehicle -> arm ─────────────────────────────────


class TestMultiVehicleChain:
    def test_two_vehicles_in_series(self):
        a1_to_v1 = StubTP("arm1->v1")
        v1_to_v2 = StubTP("v1->v2")
        v2_to_a2 = StubTP("v2->arm2")
        ev = EventLog()

        v1 = VehicleController(
            asset_id="veh-a",
            label="Shuttle A",
            input_tp=a1_to_v1,
            output_tp=v1_to_v2,
            speed_factor=0.05,
            object_event_fn=ev,
            num_products=1,
            travel_time=0.2,
        )
        v2 = VehicleController(
            asset_id="veh-b",
            label="Shuttle B",
            input_tp=v1_to_v2,
            output_tp=v2_to_a2,
            speed_factor=0.05,
            object_event_fn=ev,
            num_products=1,
            travel_time=0.2,
        )

        v1.start()
        v2.start()
        a1_to_v1.place("Obj1")

        result = []

        def arm2_consumer():
            obj = v2_to_a2.wait_and_pick(timeout=5.0)
            if obj:
                result.append(obj)

        t = threading.Thread(target=arm2_consumer, daemon=True)
        t.start()
        _drain([v1, v2], timeout=10.0)
        t.join(timeout=2.0)

        assert result == ["Obj1"]


# ── Failure handling ─────────────────────────────────────────────────


class TestVehicleFailure:
    def test_fail_blocks_downstream(self):
        in_tp = StubTP("in")
        out_tp = StubTP("out")
        ev = EventLog()

        veh = VehicleController(
            asset_id="veh-a",
            label="Shuttle",
            input_tp=in_tp,
            output_tp=out_tp,
            speed_factor=1.0,
            object_event_fn=ev,
            num_products=1,
            travel_time=5.0,  # long enough to fail mid-travel
        )
        veh.start()

        in_tp.place("Obj1")
        # Wait briefly for the controller to pick it up but stay in travel.
        time.sleep(0.1)
        veh.fail("simulated failure")
        _drain([veh], timeout=3.0)

        rec = asset_state.get("veh-a")
        assert rec["status"] in ("failed", "stopped")
        assert out_tp.is_empty(), "downstream must NOT have received the object"


class TestVehicleBackpressure:
    def test_vehicle_accepts_and_holds_when_downstream_not_ready(self):
        in_tp = StubTP("in")
        out_tp = StubTP("out")
        veh = VehicleController(
            asset_id="veh-bp",
            label="Shuttle",
            input_tp=in_tp,
            output_tp=out_tp,
            speed_factor=0.05,
            num_products=1,
            travel_time=0.05,
            keep_alive=True,
            downstream_alive_fn=lambda: False,
        )
        veh.start()
        in_tp.place("Obj1")
        time.sleep(0.3)

        # Passive buffer semantics: vehicle accepts one object, then drains
        # while holding it until downstream path is available again.
        assert in_tp.is_empty() is True
        assert getattr(veh, "_current_object", None) == "Obj1"
        assert asset_state.get("veh-bp")["status"] == "draining"

        veh.stop_module()
        veh.join(timeout=3.0)

    def test_vehicle_holds_emit_until_downstream_transfer_clears(self):
        in_tp = StubTP("in")
        out_tp = StubTP("out")

        veh = VehicleController(
            asset_id="veh-hold",
            label="Shuttle",
            input_tp=in_tp,
            output_tp=out_tp,
            speed_factor=1.0,
            num_products=1,
            travel_time=0.6,
            keep_alive=True,
            downstream_alive_fn=lambda: True,
        )
        veh.start()
        in_tp.place("Obj1")
        deadline = time.time() + 2.0
        while (
            time.time() < deadline and getattr(veh, "_current_object", None) != "Obj1"
        ):
            time.sleep(0.02)
        assert getattr(veh, "_current_object", None) == "Obj1"
        out_tp.place("blocker")
        time.sleep(0.2)

        assert out_tp.is_empty() is False
        assert getattr(veh, "_current_object", None) == "Obj1"

        assert out_tp.wait_and_pick(timeout=1.0) == "blocker"
        delivered = out_tp.wait_and_pick(timeout=3.0)
        assert delivered == "Obj1"

        veh.stop_module()
        veh.join(timeout=3.0)


# ── Recovery ─────────────────────────────────────────────────────────


class TestRecovery:
    def test_recover_clears_failed_state(self):
        in_tp = StubTP("in")
        out_tp = StubTP("out")
        veh = VehicleController(
            asset_id="veh-a",
            label="Shuttle",
            input_tp=in_tp,
            output_tp=out_tp,
            num_products=1,
            travel_time=0.1,
        )
        veh.start()

        veh.fail("test")
        _drain([veh], timeout=2.0)
        assert asset_state.get("veh-a")["status"] in ("failed", "stopped")

        veh.recover()
        rec = asset_state.get("veh-a")
        assert rec["status"] == "recovering"
        assert rec["error"] is None


# ── State registry ───────────────────────────────────────────────────


class TestAssetStateRegistry:
    def test_register_then_get(self):
        asset_state.register("veh-a", "vehicle", "Shuttle")
        rec = asset_state.get("veh-a")
        assert rec["id"] == "veh-a"
        assert rec["type"] == "vehicle"
        assert rec["status"] == "idle"
        assert rec["carried_object"] is None

    def test_set_status_invalid_rejected(self):
        asset_state.register("veh-a", "vehicle")
        with pytest.raises(ValueError):
            asset_state.set_status("veh-a", "weird-state")

    def test_is_alive_unknown_id_returns_true(self):
        # Unknown ids treated as transparent for back-compat with arms-only.
        assert asset_state.is_alive("never-registered") is True

    def test_is_alive_after_fail(self):
        asset_state.register("veh-q", "vehicle")
        asset_state.fail("veh-q", "broken")
        assert asset_state.is_alive("veh-q") is False
