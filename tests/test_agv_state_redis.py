"""agv_state contract tests — parametrized over inmemory and Redis backends.

Pattern mirrors test_bus_contract.py.  Redis backend uses fakeredis.
"""

from __future__ import annotations

import fakeredis
import pytest


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def inmemory_backend(monkeypatch):
    monkeypatch.setenv("INTERACT_STATE", "inmemory")
    import app.models.agv_state as agv
    agv.reset()
    yield
    agv.reset()


@pytest.fixture()
def redis_backend(monkeypatch):
    monkeypatch.setenv("INTERACT_STATE", "redis")
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    import app.models.agv_state as agv
    agv._inject_redis_client(client)
    agv.reset()
    yield
    agv.reset()


@pytest.fixture(params=["inmemory", "redis"])
def backend(request, inmemory_backend, redis_backend):
    return request.param


# ── Contract tests ─────────────────────────────────────────────────────────────

class TestRegister:
    def test_register_default_state(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", "AGV 1", "#16a085", [2, 5])
        rec = agv.get("agv-1")
        assert rec["status"] == "idle"
        assert rec["bay"] == [2, 5]
        assert rec["cell"] == [2, 5]
        assert rec["carried_object"] is None
        assert rec["target_cell"] is None

    def test_register_idempotent(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", "AGV 1", "#16a085", [2, 5])
        agv.register("agv-1", "AGV 1", "#16a085", [9, 9])  # ignored
        rec = agv.get("agv-1")
        assert rec["bay"] == [2, 5]

    def test_get_returns_none_for_unknown(self, backend):
        import app.models.agv_state as agv
        assert agv.get("unknown-agv") is None

    def test_get_agv_ids(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[0, 0])
        agv.register("agv-2", bay_cell=[1, 0])
        ids = set(agv.get_agv_ids())
        assert "agv-1" in ids
        assert "agv-2" in ids


class TestMutators:
    def test_set_cell(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[0, 0])
        rec = agv.set_cell("agv-1", [5, 3])
        assert rec["cell"] == [5, 3]

    def test_set_target(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[0, 0])
        rec = agv.set_target("agv-1", [3, 2])
        assert rec["target_cell"] == [3, 2]
        rec2 = agv.set_target("agv-1", None)
        assert rec2["target_cell"] is None

    def test_set_status_valid(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[0, 0])
        rec = agv.set_status("agv-1", "moving")
        assert rec["status"] == "moving"

    def test_set_status_invalid_raises(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[0, 0])
        with pytest.raises(ValueError):
            agv.set_status("agv-1", "bogus")

    def test_set_status_unknown_agv_raises(self, backend):
        import app.models.agv_state as agv
        with pytest.raises(KeyError):
            agv.set_status("no-such-agv", "idle")

    def test_set_carried(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[0, 0])
        agv.set_carried("agv-1", "Obj1")
        assert agv.get("agv-1")["carried_object"] == "Obj1"
        agv.set_carried("agv-1", None)
        assert agv.get("agv-1")["carried_object"] is None


class TestGetAll:
    def test_get_all_returns_all_registered(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[0, 0])
        agv.register("agv-2", bay_cell=[1, 0])
        all_agvs = agv.get_all()
        assert "agv-1" in all_agvs
        assert "agv-2" in all_agvs

    def test_get_all_after_reset_is_empty(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[0, 0])
        agv.reset()
        assert agv.get_all() == {}


class TestFaultRecover:
    def test_fail_marks_faulted(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[2, 5])
        rec = agv.fail("agv-1", "motor failure")
        assert rec["status"] == "faulted"

    def test_fail_unknown_agv_returns_none(self, backend):
        import app.models.agv_state as agv
        assert agv.fail("no-such", "err") is None

    def test_is_alive_false_when_faulted(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[2, 5])
        agv.fail("agv-1", "err")
        assert agv.is_alive("agv-1") is False

    def test_is_alive_true_when_idle(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[2, 5])
        assert agv.is_alive("agv-1") is True

    def test_recover_re_parks_at_bay(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[2, 5])
        agv.set_cell("agv-1", [7, 1])
        agv.set_carried("agv-1", "Obj1")
        agv.fail("agv-1", "motor")
        rec = agv.recover("agv-1")
        assert rec["status"] == "idle"
        assert rec["cell"] == [2, 5]
        assert rec["carried_object"] is None

    def test_recover_unknown_agv_returns_none(self, backend):
        import app.models.agv_state as agv
        assert agv.recover("no-such") is None

    def test_mark_idle(self, backend):
        import app.models.agv_state as agv
        agv.register("agv-1", bay_cell=[0, 0])
        agv.set_status("agv-1", "moving")
        rec = agv.mark_idle("agv-1")
        assert rec["status"] == "idle"

    def test_mark_idle_unknown_returns_none(self, backend):
        import app.models.agv_state as agv
        assert agv.mark_idle("no-such") is None


class TestInitializeFromConfig:
    def test_initialize_from_config_registers_yaml_agvs(self, backend):
        import app.models.agv_state as agv
        agv.initialize_from_config()
        ids = set(agv.get_agv_ids())
        # pipeline.yaml ships agv-1..3 in the warehouse block.
        assert {"agv-1", "agv-2", "agv-3"}.issubset(ids)
