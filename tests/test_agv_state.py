"""AGV runtime registry (Phase 2) — mirrors test_arm_state/test_asset_state."""

import pytest

from app.models import agv_state


@pytest.fixture(autouse=True)
def _clean():
    agv_state.reset()
    yield
    agv_state.reset()


def test_register_idempotent_and_defaults():
    agv_state.register("agv-1", "AGV 1", "#16a085", [2, 5])
    agv_state.register("agv-1", "AGV 1", "#16a085", [9, 9])  # ignored
    rec = agv_state.get("agv-1")
    assert rec["status"] == "idle"
    assert rec["bay"] == [2, 5]
    assert rec["cell"] == [2, 5]
    assert rec["carried_object"] is None
    assert agv_state.get_agv_ids() == ["agv-1"]


def test_move_and_status_transitions():
    agv_state.register("agv-1", bay_cell=[2, 5])
    agv_state.set_target("agv-1", [5, 1])
    agv_state.set_cell("agv-1", [5, 1])
    rec = agv_state.set_status("agv-1", "moving")
    assert rec["cell"] == [5, 1] and rec["target_cell"] == [5, 1]
    assert rec["status"] == "moving"
    with pytest.raises(ValueError):
        agv_state.set_status("agv-1", "bogus")


def test_fault_then_recover_reparks():
    agv_state.register("agv-1", bay_cell=[2, 5])
    agv_state.set_cell("agv-1", [7, 1])
    agv_state.set_carried("agv-1", "Obj1")
    assert agv_state.fail("agv-1", "motor")["status"] == "faulted"
    assert agv_state.is_alive("agv-1") is False
    rec = agv_state.recover("agv-1")
    assert rec["status"] == "idle"
    assert rec["cell"] == [2, 5]            # re-parked at bay
    assert rec["carried_object"] is None


def test_initialize_from_config_registers_yaml_agvs():
    agv_state.initialize_from_config()
    ids = set(agv_state.get_agv_ids())
    # pipeline.yaml ships agv-1..3 in the warehouse block.
    assert {"agv-1", "agv-2", "agv-3"}.issubset(ids)
