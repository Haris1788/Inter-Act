"""Tests for Phase 4 Module interface and unified pipeline builder.

Validates:
- Module base contract: state machine, accept/process/emit, lifecycle.
- AssetController + ArmController inherit from Module.
- get_module_flow_order returns correct ordering for arms-only and mixed configs.
- Linear pipeline wires up uniformly with NO type-specific branching.
- Removing the vehicle from config still produces a working module list.
- Adding a hypothetical new module type works by implementing Module only.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pytest

from app.core.module import Module, VALID_MODULE_STATES
from app.models import asset_state, pipeline_config
from app.services.asset_controllers import (
    AssetController, VehicleController, ConveyorController,
)


@contextmanager
def fake_config(cfg_dict):
    saved = pipeline_config._config
    pipeline_config._config = cfg_dict
    try:
        yield
    finally:
        pipeline_config._config = saved


def _arms_cfg(n=3):
    arms = []
    for i in range(1, n + 1):
        arms.append({
            "id": i, "label": f"Arm {i}", "color": "#000",
            "depends_on": [i - 1] if i > 1 else [], "critical": True,
        })
    return arms


# ── Module base contract ─────────────────────────────────────────────

class TestModuleBase:
    def test_is_thread_subclass(self):
        m = Module("m1")
        assert isinstance(m, threading.Thread)

    def test_initial_state_idle(self):
        m = Module("m1")
        assert m.state == "idle"
        assert m.is_module_alive() is True

    def test_state_machine_values(self):
        m = Module("m1")
        for s in VALID_MODULE_STATES:
            m.set_state(s)
            assert m.state == s

    def test_invalid_state_rejected(self):
        m = Module("m1")
        with pytest.raises(ValueError):
            m.set_state("nonsense")

    def test_failed_module_not_alive(self):
        m = Module("m1")
        m.fail("test")
        assert m.state == "failed"
        assert m.is_module_alive() is False

    def test_recover_clears_failure(self):
        m = Module("m1")
        m.fail("test")
        m.recover()
        assert m.state == "recovering"
        assert m.is_module_alive() is True


# ── Inheritance check ────────────────────────────────────────────────

class TestInheritance:
    def test_asset_controller_is_module(self):
        a = AssetController("veh", "Veh", input_tp=None, output_tp=None,
                            num_products=1)
        assert isinstance(a, Module)

    def test_vehicle_controller_is_module(self):
        v = VehicleController("veh", "Veh", input_tp=None, output_tp=None,
                              num_products=1)
        assert isinstance(v, Module)

    def test_conveyor_controller_is_module(self):
        c = ConveyorController("conv", "Conv", input_tp=None, output_tp=None,
                               num_products=1, slot_count=3)
        assert isinstance(c, Module)


# ── Flow order helper ────────────────────────────────────────────────

class TestFlowOrder:
    def test_arms_only_flow(self):
        cfg = {
            "pipeline": {"mode": "dependency"},
            "arms": _arms_cfg(3),
        }
        with fake_config(cfg):
            order = pipeline_config.get_module_flow_order()
        assert order == ["arm-1", "arm-2", "arm-3"]

    def test_mixed_flow_inserts_assets_between_arms(self):
        cfg = {
            "pipeline": {"mode": "dependency"},
            "arms": _arms_cfg(3),
            "assets": [
                {"id": "veh-a", "type": "vehicle",
                 "speed": 100,
                 "waypoints": [{"x": 0, "y": 0}, {"x": 10, "y": 0}],
                 "upstream": ["arm-2"], "downstream": ["conv-1"]},
                {"id": "conv-1", "type": "conveyor",
                 "from": {"x": 10, "y": 0}, "to": {"x": 30, "y": 0},
                 "slot_count": 3, "shift_interval": 1.0,
                 "upstream": ["veh-a"], "downstream": ["arm-3"]},
            ],
        }
        with fake_config(cfg):
            order = pipeline_config.get_module_flow_order()
        assert order == ["arm-1", "arm-2", "veh-a", "conv-1", "arm-3"]

    def test_remove_vehicle_still_orders(self):
        cfg = {
            "pipeline": {"mode": "dependency"},
            "arms": _arms_cfg(3),
            "assets": [
                {"id": "conv-1", "type": "conveyor",
                 "from": {"x": 0, "y": 0}, "to": {"x": 10, "y": 0},
                 "slot_count": 3, "shift_interval": 1.0,
                 "upstream": ["arm-2"], "downstream": ["arm-3"]},
            ],
        }
        with fake_config(cfg):
            order = pipeline_config.get_module_flow_order()
        assert order == ["arm-1", "arm-2", "conv-1", "arm-3"]


# ── Linear TP wiring (the heart of the new builder) ──────────────────

class StubTP:
    def __init__(self, name="tp"):
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


class TestLinearPipelineWiring:
    """Pipeline builder must connect modules linearly without inspecting type."""

    def test_pipeline_builder_treats_modules_uniformly(self):
        # Build modules of mixed types and connect them linearly.
        asset_state.reset()
        m1 = VehicleController("v1", "V1", None, None, num_products=1, travel_time=0.05)
        m2 = ConveyorController("c1", "C1", None, None, num_products=1,
                                slot_count=2, shift_interval=0.05)
        m3 = VehicleController("v2", "V2", None, None, num_products=1, travel_time=0.05)
        modules = [m1, m2, m3]

        # Same loop the pipeline builder uses — no type-specific code.
        for i in range(len(modules) - 1):
            tp = StubTP(f"tp-{i}")
            modules[i].output_tp = tp
            modules[i + 1].input_tp = tp

        # Source TP feeds m1; sink TP drains m3.
        source = StubTP("source")
        sink = StubTP("sink")
        m1.input_tp = source
        m3.output_tp = sink

        for m in modules:
            m.start()

        source.place("Obj1")
        # Drain sink as a downstream consumer.
        result = []
        def consumer():
            obj = sink.wait_and_pick(timeout=5.0)
            if obj:
                result.append(obj)
        t = threading.Thread(target=consumer, daemon=True)
        t.start()

        for m in modules:
            m.join(timeout=5.0)
        t.join(timeout=2.0)

        assert result == ["Obj1"]


# ── Plug/unplug a new module type without builder changes ────────────

class _DoubleBufferModule(AssetController):
    """Hypothetical new module type added without touching pipeline code.

    Demonstrates the Phase 4 contract: a fresh subclass of AssetController/
    Module needs zero changes outside its own file to participate in the
    pipeline. The builder wires it up identically to vehicles and conveyors.
    """

    asset_type = "double_buffer"

    def process(self):
        # Fast pass-through: no delay, no shifting.
        time.sleep(0.01)


class TestPluggability:
    def test_new_module_type_works_through_same_loop(self):
        asset_state.reset()
        modules = [
            VehicleController("v1", "V1", None, None, num_products=1, travel_time=0.05),
            _DoubleBufferModule("db", "DB", None, None, num_products=1),
            ConveyorController("c1", "C1", None, None, num_products=1,
                               slot_count=2, shift_interval=0.05),
        ]
        for i in range(len(modules) - 1):
            tp = StubTP(f"tp-{i}")
            modules[i].output_tp = tp
            modules[i + 1].input_tp = tp
        src = StubTP("src")
        sink = StubTP("sink")
        modules[0].input_tp = src
        modules[-1].output_tp = sink

        for m in modules:
            m.start()
        src.place("X")

        result = []
        def consumer():
            obj = sink.wait_and_pick(timeout=5.0)
            if obj:
                result.append(obj)
        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        for m in modules:
            m.join(timeout=5.0)
        t.join(timeout=2.0)

        assert result == ["X"]


# ── Logging contract ────────────────────────────────────────────────

class TestModuleLogging:
    def test_module_logs_accept_emit(self):
        asset_state.reset()
        captured = []

        def log_fn(src, msg):
            captured.append((src, msg))

        m = VehicleController("v1", "V1", None, None,
                              num_products=1, travel_time=0.02,
                              log_fn=log_fn)
        src = StubTP("src")
        sink = StubTP("sink")
        m.input_tp = src
        m.output_tp = sink

        m.start()
        src.place("Obj1")

        def drain():
            sink.wait_and_pick(timeout=2.0)
        threading.Thread(target=drain, daemon=True).start()
        m.join(timeout=3.0)

        # Expected messages mention "accepted Obj1" and "emitted Obj1".
        msgs = [m for _, m in captured]
        assert any("accepted Obj1" in m for m in msgs)
        assert any("emitted Obj1" in m for m in msgs)
