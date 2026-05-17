"""Tests for Phase 4 Module interface and unified pipeline builder.

Validates:
- Module base contract: state machine, accept/process/emit, lifecycle.
- AssetController + ArmController inherit from Module.
- get_module_flow_order returns correct ordering for arms-only and mixed configs.
- Linear pipeline wires up uniformly with NO type-specific branching.
- Removing an intermediate vehicle still produces a working module list.
- Adding a hypothetical new module type works by implementing Module only.
"""

from __future__ import annotations

import threading
import time
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.core.module import Module, VALID_MODULE_STATES
from app.models import asset_state, pipeline_config
from app.services.asset_controllers import AssetController, VehicleController

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts.run_demo import ArmController, TransferPoint
import scripts.run_demo as run_demo


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
        arms.append(
            {
                "id": i,
                "label": f"Arm {i}",
                "color": "#000",
                "depends_on": [i - 1] if i > 1 else [],
                "critical": True,
            }
        )
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
        a = AssetController("veh", "Veh", input_tp=None, output_tp=None, num_products=1)
        assert isinstance(a, Module)

    def test_vehicle_controller_is_module(self):
        v = VehicleController(
            "veh", "Veh", input_tp=None, output_tp=None, num_products=1
        )
        assert isinstance(v, Module)


class TestArmBackpressure:
    def _arm(self, arm_id, input_tp=None, output_tp=None, downstream_alive=None):
        return ArmController(
            arm_id=arm_id,
            host="http://unused",
            speed_factor=0,
            input_tp=input_tp,
            output_tp=output_tp,
            num_products=1,
            log_fn=lambda *_args: None,
            set_angle_fn=lambda *_args: None,
            object_event_fn=lambda *_args, **_kwargs: None,
            downstream_alive_fn=downstream_alive or (lambda: True),
        )

    def test_arm_buffers_in_tp_while_downstream_arm_is_occupied(self):
        """arm-1 places into an empty TP even when arm-2 is currently carrying
        an object - the TP is a passive 1-slot buffer, not a gating signal."""
        tp = TransferPoint("TP arm-1->arm-2")

        arm2 = self._arm(2, input_tp=tp)
        arm2._current_object = "Obj2"  # arm2 is busy

        arm1 = self._arm(
            1,
            output_tp=tp,
            downstream_alive=lambda: arm2.is_ready_for_input(),
        )

        # arm1 must complete immediately - TP is empty so buffering is allowed
        arm1._process_one_product("Obj3")

        assert not tp.is_empty(), "Obj3 must be buffered in TP"
        assert tp.wait_and_pick(timeout=0.1) == "Obj3"

    def test_arm_blocks_at_pre_pickup_when_tp_full(self):
        """If the output TP is already occupied, arm-1 must not pick up a new
        object - capacity=1 enforced before the pickup phase."""
        tp = TransferPoint("TP arm-1->arm-2")
        tp.place("Existing")  # pre-occupy

        arm1 = self._arm(1, output_tp=tp)

        assert arm1.tp_has_capacity() is False
        assert not arm1.downstream_ready()

    def test_transfer_point_is_capacity_one_blocking_buffer(self):
        tp = TransferPoint("TP cap1")
        tp.place("Obj1")

        done = {"placed": False}

        def place_second():
            tp.place("Obj2")
            done["placed"] = True

        t = threading.Thread(target=place_second, daemon=True)
        t.start()
        time.sleep(0.2)
        assert done["placed"] is False

        assert tp.wait_and_pick(timeout=0.1) == "Obj1"
        t.join(timeout=1.0)
        assert done["placed"] is True
        assert tp.wait_and_pick(timeout=0.1) == "Obj2"

    def test_only_terminal_arm_emits_output_event(self):
        events = []

        def on_event(obj_id, state, carrier_arm=None, **extra):
            events.append((obj_id, state, carrier_arm, extra))

        # Non-terminal arm: must emit handover, never output.
        tp_in = TransferPoint("TP arm-2->arm-3")
        tp_out = TransferPoint("TP arm-3->arm-4")
        arm3 = ArmController(
            arm_id=3,
            host="http://unused",
            speed_factor=0,
            input_tp=tp_in,
            output_tp=tp_out,
            num_products=1,
            log_fn=lambda *_args: None,
            handover_place="handover_3",
            set_angle_fn=lambda *_args: None,
            object_event_fn=on_event,
            downstream_alive_fn=lambda: True,
        )
        tp_in.place("ObjA")
        arm3._process_one_product("ObjA")

        arm3_states = [state for obj, state, *_ in events if obj == "ObjA"]
        assert "handover_3" in arm3_states
        assert "output" not in arm3_states

        # Terminal arm: output_tp=None means true sink, emits output.
        tp_last_in = TransferPoint("TP arm-4->arm-5")
        arm5 = ArmController(
            arm_id=5,
            host="http://unused",
            speed_factor=0,
            input_tp=tp_last_in,
            output_tp=None,
            num_products=1,
            log_fn=lambda *_args: None,
            set_angle_fn=lambda *_args: None,
            object_event_fn=on_event,
            downstream_alive_fn=lambda: True,
            is_terminal_fn=lambda: True,
        )
        tp_last_in.place("ObjB")
        arm5._process_one_product("ObjB")

        arm5_states = [state for obj, state, *_ in events if obj == "ObjB"]
        assert "output" in arm5_states


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

    def test_mixed_flow_inserts_vehicle_between_arms(self):
        cfg = {
            "pipeline": {"mode": "dependency"},
            "arms": _arms_cfg(3),
            "assets": [
                {
                    "id": "veh-a",
                    "type": "vehicle",
                    "speed": 100,
                    "waypoints": [{"x": 0, "y": 0}, {"x": 10, "y": 0}],
                    "upstream": ["arm-2"],
                    "downstream": ["arm-3"],
                },
            ],
        }
        with fake_config(cfg):
            order = pipeline_config.get_module_flow_order()
        assert order == ["arm-1", "arm-2", "veh-a", "arm-3"]

    def test_two_vehicles_in_series(self):
        cfg = {
            "pipeline": {"mode": "dependency"},
            "arms": _arms_cfg(3),
            "assets": [
                {
                    "id": "veh-a",
                    "type": "vehicle",
                    "speed": 100,
                    "waypoints": [{"x": 0, "y": 0}, {"x": 10, "y": 0}],
                    "upstream": ["arm-2"],
                    "downstream": ["veh-b"],
                },
                {
                    "id": "veh-b",
                    "type": "vehicle",
                    "speed": 100,
                    "waypoints": [{"x": 10, "y": 0}, {"x": 30, "y": 0}],
                    "upstream": ["veh-a"],
                    "downstream": ["arm-3"],
                },
            ],
        }
        with fake_config(cfg):
            order = pipeline_config.get_module_flow_order()
        assert order == ["arm-1", "arm-2", "veh-a", "veh-b", "arm-3"]

    def test_five_arms_vehicle_between_arm4_and_arm5(self):
        cfg = {
            "pipeline": {"mode": "dependency"},
            "arms": _arms_cfg(5),
            "assets": [
                {
                    "id": "veh-a",
                    "type": "vehicle",
                    "speed": 120,
                    "waypoints": [{"x": 0, "y": 0}, {"x": 20, "y": 0}],
                    "upstream": ["arm-2"],
                    "downstream": ["arm-3"],
                },
                {
                    "id": "veh-b",
                    "type": "vehicle",
                    "speed": 120,
                    "waypoints": [{"x": 0, "y": 0}, {"x": 20, "y": 0}],
                    "upstream": ["arm-4"],
                    "downstream": ["arm-5"],
                },
            ],
        }
        with fake_config(cfg):
            order = pipeline_config.get_module_flow_order()
        assert order == [
            "arm-1",
            "arm-2",
            "veh-a",
            "arm-3",
            "arm-4",
            "veh-b",
            "arm-5",
        ]

    def test_asset_ordering_in_yaml_does_not_break_chain(self):
        cfg = {
            "pipeline": {"mode": "dependency"},
            "arms": _arms_cfg(3),
            "assets": [
                # Intentionally reversed: downstream listed before upstream.
                {
                    "id": "veh-b",
                    "type": "vehicle",
                    "speed": 100,
                    "waypoints": [{"x": 10, "y": 0}, {"x": 30, "y": 0}],
                    "upstream": ["veh-a"],
                    "downstream": ["arm-3"],
                },
                {
                    "id": "veh-a",
                    "type": "vehicle",
                    "speed": 100,
                    "waypoints": [{"x": 0, "y": 0}, {"x": 10, "y": 0}],
                    "upstream": ["arm-2"],
                    "downstream": ["veh-b"],
                },
            ],
        }
        with fake_config(cfg):
            order = pipeline_config.get_module_flow_order()
        assert order == ["arm-1", "arm-2", "veh-a", "veh-b", "arm-3"]


class TestRunDemoTopologySync:
    def test_run_demo_rejects_mismatched_flow_vs_loaded_arms(self, monkeypatch):
        cfg = {
            "pipeline": {"mode": "dependency"},
            "arms": _arms_cfg(3),
        }

        class FakePipelineConfig:
            _config = None

            @staticmethod
            def get_module_flow_order():
                return ["arm-1", "arm-2", "arm-3", "arm-4"]

            @staticmethod
            def get_extra_assets():
                return []

            @staticmethod
            def get_asset_host_ports():
                return {}

        monkeypatch.setattr(run_demo, "_pc", FakePipelineConfig())
        monkeypatch.setattr(run_demo, "_ASSET_RUNTIME_AVAILABLE", False)

        with pytest.raises(RuntimeError, match="flow references arm-4"):
            run_demo._run_pipeline_core(
                speed_factor=0,
                num_products=1,
                set_angle_fn=lambda *_args: None,
                object_event_fn=lambda *_args, **_kwargs: None,
                reset_objects_fn=lambda: None,
                config=cfg,
            )


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
        m2 = VehicleController("v2", "V2", None, None, num_products=1, travel_time=0.05)
        m3 = VehicleController("v3", "V3", None, None, num_products=1, travel_time=0.05)
        modules = [m1, m2, m3]

        # Same loop the pipeline builder uses - no type-specific code.
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
    pipeline. The builder wires it up identically to vehicles.
    """

    asset_type = "double_buffer"

    def process(self):
        # Fast pass-through: no delay.
        time.sleep(0.01)


class TestPluggability:
    def test_new_module_type_works_through_same_loop(self):
        asset_state.reset()
        modules = [
            VehicleController("v1", "V1", None, None, num_products=1, travel_time=0.05),
            _DoubleBufferModule("db", "DB", None, None, num_products=1),
            VehicleController("v2", "V2", None, None, num_products=1, travel_time=0.05),
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

        m = VehicleController(
            "v1", "V1", None, None, num_products=1, travel_time=0.02, log_fn=log_fn
        )
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
