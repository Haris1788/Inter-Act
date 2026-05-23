"""Tests for snapshot_all() and GET /reservations — Phase 5.1 Stage 4.

Covers:
  - snapshot_all() on in-memory backend: empty, single group, multiple groups.
  - snapshot_all() on Redis backend (fakeredis): same contract.
  - GET /reservations endpoint: shape, ts field, empty case.
"""

from __future__ import annotations

import time

import fakeredis
import pytest

from app.spatial.reservation import (
    ReservationStore,
    _InMemoryReservationStore,
    _RedisReservationStore,
    _inject_redis_client,
    get_store,
)
from app import create_app
from app.config import TestingConfig


# ---------------------------------------------------------------------------
# Fixtures — store (parametrized over both backends)
# ---------------------------------------------------------------------------

@pytest.fixture(params=["inmemory", "redis"])
def store(request, monkeypatch) -> ReservationStore:
    """Return a fresh ReservationStore for each backend."""
    backend_name: str = request.param

    if backend_name == "inmemory":
        monkeypatch.delenv("INTERACT_STATE", raising=False)
        _inject_redis_client(None)
        backend = _InMemoryReservationStore()
        return ReservationStore(backend)
    else:
        server = fakeredis.FakeServer()
        client = fakeredis.FakeRedis(server=server, decode_responses=True)
        _inject_redis_client(client)
        monkeypatch.setenv("INTERACT_STATE", "redis")
        backend = _RedisReservationStore(client)
        return ReservationStore(backend)


# ---------------------------------------------------------------------------
# snapshot_all tests
# ---------------------------------------------------------------------------

class TestSnapshotAllEmpty:
    def test_empty_store_returns_empty_list(self, store):
        """snapshot_all() on a fresh store returns []."""
        result = store.snapshot_all()
        assert result == []


class TestSnapshotAllSingleGroup:
    def test_single_group_structure(self, store):
        """One (task, actor, epoch) reservation appears as one entry with correct cells."""
        epoch = store.next_epoch("task-1", "agv-1")
        cells = [(0, 0), (1, 0), (2, 0)]
        ok = store.reserve("task-1", "agv-1", epoch, cells, ttl_ms=60_000)
        assert ok is True

        snapshot = store.snapshot_all()
        assert len(snapshot) == 1

        entry = snapshot[0]
        assert entry["task_id"] == "task-1"
        assert entry["actor_id"] == "agv-1"
        assert entry["epoch"] == epoch
        # Cells are [[col, row], ...]; compare as sets to allow any ordering.
        actual_cells = {(c[0], c[1]) for c in entry["cells"]}
        assert actual_cells == {(0, 0), (1, 0), (2, 0)}


class TestSnapshotAllMultipleGroups:
    def test_multiple_groups_returned(self, store):
        """Two different (actor, epoch) groups each appear separately."""
        e1 = store.next_epoch("task-A", "agv-1")
        e2 = store.next_epoch("task-A", "agv-2")

        # agv-1 takes (0,0), (0,1); agv-2 takes (5,5).
        store.reserve("task-A", "agv-1", e1, [(0, 0), (0, 1)], ttl_ms=60_000)
        store.reserve("task-A", "agv-2", e2, [(5, 5)], ttl_ms=60_000)

        snapshot = store.snapshot_all()
        assert len(snapshot) == 2

        by_actor = {e["actor_id"]: e for e in snapshot}
        assert "agv-1" in by_actor
        assert "agv-2" in by_actor

        cells_1 = {(c[0], c[1]) for c in by_actor["agv-1"]["cells"]}
        cells_2 = {(c[0], c[1]) for c in by_actor["agv-2"]["cells"]}
        assert cells_1 == {(0, 0), (0, 1)}
        assert cells_2 == {(5, 5)}


class TestSnapshotAllReleasedCellsExcluded:
    def test_released_cells_not_in_snapshot(self, store):
        """Cells released before snapshot_all() do not appear."""
        epoch = store.next_epoch("task-B", "agv-3")
        store.reserve("task-B", "agv-3", epoch, [(3, 3)], ttl_ms=60_000)
        store.release("task-B", "agv-3", epoch)

        snapshot = store.snapshot_all()
        assert snapshot == []


# ---------------------------------------------------------------------------
# GET /reservations endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_with_clean_store(monkeypatch):
    """Flask test client with a fresh in-memory reservation store."""
    monkeypatch.delenv("INTERACT_STATE", raising=False)
    _inject_redis_client(None)
    # Reset singleton so each test gets a fresh in-memory store.
    import app.spatial.reservation as resv_mod
    resv_mod._store_instance = None

    application = create_app(config_class=TestingConfig)
    with application.test_client() as c:
        yield c

    # Clean up singleton after test.
    resv_mod._store_instance = None


class TestReservationsEndpointEmpty:
    def test_returns_200_with_empty_list(self, client_with_clean_store):
        """GET /reservations on empty store → 200, {reservations: [], ts: ...}."""
        r = client_with_clean_store.get("/reservations")
        assert r.status_code == 200
        data = r.get_json()
        assert "reservations" in data
        assert data["reservations"] == []
        assert "ts" in data
        assert isinstance(data["ts"], float)


class TestReservationsEndpointWithData:
    def test_returns_reservation_groups(self, client_with_clean_store, monkeypatch):
        """GET /reservations reflects live reservation state."""
        # Seed the store directly (same singleton used by the endpoint).
        s = get_store()
        epoch = s.next_epoch("task-X", "agv-1")
        s.reserve("task-X", "agv-1", epoch, [(0, 0), (1, 0)], ttl_ms=60_000)

        r = client_with_clean_store.get("/reservations")
        assert r.status_code == 200
        data = r.get_json()
        resv = data["reservations"]
        assert len(resv) == 1
        entry = resv[0]
        assert entry["task_id"] == "task-X"
        assert entry["actor_id"] == "agv-1"
        assert entry["epoch"] == epoch
        actual_cells = {(c[0], c[1]) for c in entry["cells"]}
        assert actual_cells == {(0, 0), (1, 0)}

    def test_ts_is_recent(self, client_with_clean_store):
        """The ts field is within 5 s of time.time()."""
        before = time.time()
        r = client_with_clean_store.get("/reservations")
        after = time.time()
        data = r.get_json()
        assert before - 1 <= data["ts"] <= after + 1
