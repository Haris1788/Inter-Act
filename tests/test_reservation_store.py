"""Unit tests for app.spatial.reservation.ReservationStore.

Eight tests per spec, parametrized over both backends:
  - inmemory  (default; no external deps)
  - redis     (fakeredis)

Parametrization mirrors tests/test_redis_lock.py (inject fake client for
the redis backend; reset singleton before each test).
"""

from __future__ import annotations

import os
import time
import threading

import fakeredis
import pytest

from app.spatial.reservation import (
    ReservationStore,
    PathKeepalive,
    _InMemoryReservationStore,
    _RedisReservationStore,
    _inject_redis_client,
    get_store,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(params=["inmemory", "redis"])
def store(request, monkeypatch) -> ReservationStore:
    """Return a fresh ReservationStore for each backend."""
    backend_name: str = request.param

    if backend_name == "inmemory":
        # Reset singleton; env = inmemory (default).
        monkeypatch.delenv("INTERACT_STATE", raising=False)
        _inject_redis_client(None)
        backend = _InMemoryReservationStore()
        return ReservationStore(backend)
    else:
        # Redis backend — inject fresh fakeredis client.
        server = fakeredis.FakeServer()
        client = fakeredis.FakeRedis(server=server, decode_responses=True)
        _inject_redis_client(client)
        monkeypatch.setenv("INTERACT_STATE", "redis")
        backend = _RedisReservationStore(client)
        s = ReservationStore(backend)
        return s


# ---------------------------------------------------------------------------
# 1. reserve — idempotent same owner
# ---------------------------------------------------------------------------

class TestReserveIdempotentSameOwner:
    def test_reserve_idempotent_same_owner(self, store):
        """Reserving the same (task, actor, epoch, cell) twice returns True both times."""
        cells = [(1, 2), (3, 4)]
        epoch = store.next_epoch("task-A", "agv-1")
        r1 = store.reserve("task-A", "agv-1", epoch, cells, ttl_ms=60_000)
        r2 = store.reserve("task-A", "agv-1", epoch, cells, ttl_ms=60_000)
        assert r1 is True
        assert r2 is True


# ---------------------------------------------------------------------------
# 2. reserve — conflict different owner
# ---------------------------------------------------------------------------

class TestReserveConflictDifferentOwner:
    def test_reserve_conflict_different_owner(self, store):
        """Different actor reserving the same cell returns False; no ghost key."""
        epoch1 = store.next_epoch("task-B", "agv-1")
        epoch2 = store.next_epoch("task-B", "agv-2")

        ok1 = store.reserve("task-B", "agv-1", epoch1, [(5, 5)], ttl_ms=60_000)
        assert ok1 is True

        # agv-2 tries to reserve the same cell (5, 5).
        ok2 = store.reserve("task-B", "agv-2", epoch2, [(5, 5)], ttl_ms=60_000)
        assert ok2 is False

        # agv-2's reservation should not appear in held_by_others for agv-1.
        held = store.held_by_others("task-B", exclude_actor="agv-1")
        # agv-1 is excluded; only agv-2's live cells would appear — but reserve failed.
        assert (5, 5) not in held


# ---------------------------------------------------------------------------
# 3. release by epoch garbage-collects all cells for that epoch
# ---------------------------------------------------------------------------

class TestReleaseByEpoch:
    def test_release_by_epoch_garbage_collects(self, store):
        """release(task, actor, E) deletes ALL epoch-E cell keys for that actor."""
        epoch = store.next_epoch("task-C", "agv-1")
        cells = [(0, 0), (1, 0), (2, 0), (3, 0)]
        store.reserve("task-C", "agv-1", epoch, cells, ttl_ms=60_000)

        deleted = store.release("task-C", "agv-1", epoch)
        assert deleted == len(cells)

        # After release, held_by_others should be empty for this task.
        held = store.held_by_others("task-C", exclude_actor="nobody")
        assert (0, 0) not in held
        assert (3, 0) not in held

    def test_release_returns_zero_when_nothing_reserved(self, store):
        """release on an unreserved epoch returns 0."""
        epoch = store.next_epoch("task-D", "agv-1")
        deleted = store.release("task-D", "agv-1", epoch)
        assert deleted == 0


# ---------------------------------------------------------------------------
# 4. next_epoch — monotonic per (task, actor) pair
# ---------------------------------------------------------------------------

class TestNextEpochMonotonic:
    def test_next_epoch_monotonic_per_pair(self, store):
        """Epochs increase strictly per (task, actor) pair; pairs are independent."""
        e1 = store.next_epoch("task-E", "agv-1")
        e2 = store.next_epoch("task-E", "agv-1")
        e3 = store.next_epoch("task-E", "agv-1")
        assert e1 < e2 < e3

        # Different actor — independent counter.
        e_other = store.next_epoch("task-E", "agv-2")
        # agv-2 starts from 1 regardless of agv-1's counter.
        assert e_other == 1

        # Different task — independent counter.
        e_task2 = store.next_epoch("task-F", "agv-1")
        assert e_task2 == 1


# ---------------------------------------------------------------------------
# 5. held_by_others — filters self
# ---------------------------------------------------------------------------

class TestHeldByOthersFiltersSelf:
    def test_held_by_others_filters_self(self, store):
        """Own actor's cells must NOT appear in held_by_others result."""
        epoch1 = store.next_epoch("task-G", "agv-1")
        epoch2 = store.next_epoch("task-G", "agv-2")

        store.reserve("task-G", "agv-1", epoch1, [(10, 10), (11, 10)], ttl_ms=60_000)
        store.reserve("task-G", "agv-2", epoch2, [(20, 20)], ttl_ms=60_000)

        # From agv-1's perspective: only agv-2's cells.
        held = store.held_by_others("task-G", exclude_actor="agv-1")
        assert (10, 10) not in held
        assert (11, 10) not in held
        assert (20, 20) in held

        # From agv-2's perspective: only agv-1's cells.
        held2 = store.held_by_others("task-G", exclude_actor="agv-2")
        assert (10, 10) in held2
        assert (20, 20) not in held2

    def test_held_by_others_scope_one_task(self, store):
        """held_by_others is scoped to one task — other tasks are not included."""
        e1 = store.next_epoch("task-H", "agv-2")
        e2 = store.next_epoch("task-X", "agv-2")  # different task

        store.reserve("task-H", "agv-2", e1, [(5, 5)], ttl_ms=60_000)
        store.reserve("task-X", "agv-2", e2, [(9, 9)], ttl_ms=60_000)

        # Asking about task-H; (9, 9) belongs to task-X — must not appear.
        held = store.held_by_others("task-H", exclude_actor="agv-1")
        assert (5, 5) in held       # agv-2 holds this in task-H
        assert (9, 9) not in held   # different task


# ---------------------------------------------------------------------------
# 6. TTL expiry clears cells
# ---------------------------------------------------------------------------

class TestTTLExpiry:
    def test_ttl_expiry_clears_cells(self, store):
        """After a short TTL expires, cells are no longer held by others."""
        epoch = store.next_epoch("task-I", "agv-1")
        store.reserve("task-I", "agv-1", epoch, [(3, 3)], ttl_ms=100)

        # Immediately: cell is held (by agv-2's perspective).
        held_before = store.held_by_others("task-I", exclude_actor="agv-2")
        assert (3, 3) in held_before

        # After TTL elapses: cell must be gone.
        time.sleep(0.25)
        held_after = store.held_by_others("task-I", exclude_actor="agv-2")
        assert (3, 3) not in held_after


# ---------------------------------------------------------------------------
# 7. touch() renews TTL
# ---------------------------------------------------------------------------

class TestTouchRenewsTTL:
    def test_touch_renews_ttl(self, store):
        """touch() extends the TTL so cells remain live past the original expiry."""
        epoch = store.next_epoch("task-J", "agv-1")
        store.reserve("task-J", "agv-1", epoch, [(4, 4)], ttl_ms=200)

        time.sleep(0.1)  # ~half the original TTL
        extended = store.touch("task-J", "agv-1", epoch, ttl_ms=500)
        assert extended is True

        time.sleep(0.2)  # would have expired without touch (0.1+0.2=0.3 > 0.2 original)
        # Cell should still be live after touch renewal.
        held = store.held_by_others("task-J", exclude_actor="agv-2")
        assert (4, 4) in held

    def test_touch_returns_false_when_no_keys(self, store):
        """touch() returns False when no matching keys exist."""
        epoch = store.next_epoch("task-K", "agv-1")
        # Nothing was reserved.
        result = store.touch("task-K", "agv-1", epoch, ttl_ms=60_000)
        assert result is False


# ---------------------------------------------------------------------------
# 8. PathKeepalive renews until stopped
# ---------------------------------------------------------------------------

class TestPathKeepaliveRenewsUntilStopped:
    def test_path_keepalive_renews_until_stopped(self, store):
        """PathKeepalive keeps cells alive past their original TTL; stop() ends renewal."""
        epoch = store.next_epoch("task-L", "agv-1")
        store.reserve("task-L", "agv-1", epoch, [(6, 6)], ttl_ms=200)

        keepalive = PathKeepalive(
            store,
            task_id="task-L",
            actor_id="agv-1",
            epoch=epoch,
            ttl_ms=500,
            interval_s=0.05,
        )
        keepalive.start()
        try:
            # Wait longer than the original 200 ms TTL; keepalive should renew.
            time.sleep(0.35)
            held = store.held_by_others("task-L", exclude_actor="agv-2")
            assert (6, 6) in held, "PathKeepalive failed to renew TTL — cell expired"
        finally:
            keepalive.stop()

    def test_path_keepalive_context_manager(self, store):
        """PathKeepalive works as a context manager."""
        epoch = store.next_epoch("task-M", "agv-1")
        store.reserve("task-M", "agv-1", epoch, [(7, 7)], ttl_ms=300)

        with PathKeepalive(store, "task-M", "agv-1", epoch, ttl_ms=500, interval_s=0.05):
            time.sleep(0.2)
            held = store.held_by_others("task-M", exclude_actor="agv-2")
            assert (7, 7) in held

        # After context exits: keepalive stopped; cells still reserved (we did not release).
        # Release manually and verify.
        store.release("task-M", "agv-1", epoch)
        held_after = store.held_by_others("task-M", exclude_actor="agv-2")
        assert (7, 7) not in held_after
