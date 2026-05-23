"""grid_state contract tests — parametrized over inmemory and Redis backends.

Pattern mirrors test_bus_contract.py: both backends must produce identical
observable behaviour.  The Redis backend uses fakeredis so CI needs no real
Redis container.
"""

from __future__ import annotations

import os
import threading
import time

import fakeredis
import pytest

# ── Fixtures ───────────────────────────────────────────────────────────────────


def _setup_redis_backend(fake_client):
    """Point all grid_state Redis operations at the fake client."""
    import app.models.grid_state as gs
    gs._inject_redis_client(fake_client)
    # Also clear the cached RedisLock instances so they pick up the new client.
    import app.models.redis_lock  # noqa: F401
    with gs._redis_locks_lock:
        gs._redis_locks.clear()


@pytest.fixture()
def inmemory_backend(monkeypatch):
    """Run tests against the in-memory backend."""
    monkeypatch.setenv("INTERACT_STATE", "inmemory")
    import app.models.grid_state as gs
    gs.reset()
    yield
    gs.reset()


@pytest.fixture()
def redis_backend(monkeypatch):
    """Run tests against a fakeredis-backed Redis backend."""
    monkeypatch.setenv("INTERACT_STATE", "redis")
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    _setup_redis_backend(client)
    import app.models.grid_state as gs
    gs.reset()
    yield
    gs.reset()


@pytest.fixture(params=["inmemory", "redis"])
def backend(request, inmemory_backend, redis_backend):
    """Parametrize: each test runs against both backends."""
    return request.param


# ── Contract tests ─────────────────────────────────────────────────────────────

class TestCellOccupancy:
    """set_cell / cell_holder / is_cell_free / clear_cell / snapshot."""

    def test_set_and_get_cell(self, backend):
        import app.models.grid_state as gs
        gs.set_cell("agv-1", [2, 1])
        assert gs.cell_holder([2, 1]) == "agv-1"

    def test_is_cell_free_when_occupied(self, backend):
        import app.models.grid_state as gs
        gs.set_cell("agv-1", [2, 1])
        assert gs.is_cell_free([2, 1]) is False

    def test_is_cell_free_when_empty(self, backend):
        import app.models.grid_state as gs
        assert gs.is_cell_free([9, 9]) is True

    def test_is_cell_free_ignore_self(self, backend):
        import app.models.grid_state as gs
        gs.set_cell("agv-1", [2, 1])
        assert gs.is_cell_free([2, 1], ignore="agv-1") is True

    def test_cell_holder_returns_none_for_empty(self, backend):
        import app.models.grid_state as gs
        assert gs.cell_holder([0, 0]) is None

    def test_clear_cell_removes_holder(self, backend):
        import app.models.grid_state as gs
        gs.set_cell("agv-1", [3, 2])
        gs.clear_cell("agv-1")
        assert gs.cell_holder([3, 2]) is None
        assert gs.is_cell_free([3, 2]) is True

    def test_two_agvs_in_different_cells(self, backend):
        import app.models.grid_state as gs
        gs.set_cell("agv-1", [1, 0])
        gs.set_cell("agv-2", [2, 0])
        assert gs.cell_holder([1, 0]) == "agv-1"
        assert gs.cell_holder([2, 0]) == "agv-2"

    def test_agv_moves_cell(self, backend):
        import app.models.grid_state as gs
        gs.set_cell("agv-1", [1, 0])
        gs.set_cell("agv-1", [2, 0])  # move
        assert gs.cell_holder([1, 0]) is None
        assert gs.cell_holder([2, 0]) == "agv-1"


class TestSnapshot:
    def test_snapshot_contains_occupancy(self, backend):
        import app.models.grid_state as gs
        gs.set_cell("agv-1", [2, 1])
        snap = gs.snapshot()
        assert "occupancy" in snap
        assert snap["occupancy"]["agv-1"] == [2, 1]

    def test_snapshot_lane_holder_none_when_free(self, backend):
        import app.models.grid_state as gs
        snap = gs.snapshot()
        assert snap["lane_holder"] is None

    def test_snapshot_lane_holder_after_acquire(self, backend):
        import app.models.grid_state as gs
        gs.lane().acquire("agv-1", timeout=1.0)
        snap = gs.snapshot()
        assert snap["lane_holder"] == "agv-1"
        gs.lane().release("agv-1")

    def test_reset_clears_occupancy_and_snapshot(self, backend):
        import app.models.grid_state as gs
        gs.set_cell("agv-1", [2, 1])
        gs.reset()
        snap = gs.snapshot()
        assert snap["occupancy"] == {}


class TestLaneLock:
    """lane() returns a lock that provides acquire / release / is_free."""

    def test_lane_initially_free(self, backend):
        import app.models.grid_state as gs
        gs.reset()
        assert gs.lane().is_free()

    def test_acquire_and_release(self, backend):
        import app.models.grid_state as gs
        gs.reset()
        ok = gs.lane().acquire("agv-1", timeout=1.0)
        assert ok is True
        assert gs.lane().holder() == "agv-1"
        gs.lane().release("agv-1")
        assert gs.lane().is_free()

    def test_second_agv_blocked_short_timeout(self, backend):
        import app.models.grid_state as gs
        gs.reset()
        gs.lane().acquire("agv-1", timeout=2.0)
        ok = gs.lane().acquire("agv-2", timeout=0.15)
        assert ok is False
        gs.lane().release("agv-1")

    def test_cancel_check_aborts(self, backend):
        import app.models.grid_state as gs
        gs.reset()
        gs.lane().acquire("agv-1", timeout=5.0)
        t0 = time.time()
        ok = gs.lane().acquire("agv-2", timeout=30.0, cancel_check=lambda: False)
        assert ok is False
        assert time.time() - t0 < 2.0
        gs.lane().release("agv-1")

    def test_reentrant_same_holder(self, backend):
        import app.models.grid_state as gs
        gs.reset()
        gs.lane().acquire("agv-1", timeout=1.0)
        # Same holder re-acquiring is a no-op success.
        assert gs.lane().acquire("agv-1", timeout=1.0) is True
        gs.lane().release("agv-1")

    def test_force_release(self, backend):
        import app.models.grid_state as gs
        gs.reset()
        gs.lane().acquire("agv-3", timeout=1.0)
        gs.lane().force_release()
        assert gs.lane().is_free()


class TestStationLock:
    """station_lock(name) returns a lock per station."""

    def test_station_lock_acquires(self, backend):
        import app.models.grid_state as gs
        gs.reset()
        sl = gs.station_lock("input_arm")
        assert sl.acquire("agv-1", timeout=1.0) is True
        assert sl.holder() == "agv-1"
        sl.release("agv-1")

    def test_station_locks_are_independent(self, backend):
        import app.models.grid_state as gs
        gs.reset()
        gs.station_lock("input_arm").acquire("agv-1", timeout=1.0)
        # output_arm is a different lock — must be free.
        assert gs.station_lock("output_arm").is_free() is True
        gs.station_lock("input_arm").release("agv-1")

    def test_station_lock_idempotent_name(self, backend):
        import app.models.grid_state as gs
        gs.reset()
        # Calling station_lock with the same name twice returns same lock.
        sl1 = gs.station_lock("input_arm")
        sl2 = gs.station_lock("input_arm")
        sl1.acquire("agv-1", timeout=1.0)
        # sl2 must reflect the same held state.
        ok = sl2.acquire("agv-2", timeout=0.1)
        assert ok is False
        sl1.release("agv-1")
