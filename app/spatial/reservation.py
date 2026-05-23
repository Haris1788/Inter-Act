"""Epoch-scoped per-cell reservation store for AGV path collision prevention.

Every reservation key ALWAYS encodes the full tuple
``(task_id, actor_id, epoch, col, row)``.  Bare ``cell:<col>:<row>`` keys are
FORBIDDEN — they would produce ghost occupancy across replans and corrupt the
Stage 8 replay log.

Key format (Redis string, value "1", TTL = ttl_ms):
    resv:task:<task_id>:actor:<actor_id>:epoch:<epoch>:cell:<col>:<row>

Epoch counter key (Redis string, value = monotonic int, no TTL):
    epoch:task:<task_id>:actor:<actor_id>

Backend selection:
    ``INTERACT_STATE=inmemory``  (default, used by tests) — in-process dicts,
                                  RLock-guarded, monotonic counter per pair.
    ``INTERACT_STATE=redis``     — all operations go to Redis; INCR for epoch,
                                  SET NX PX for reservation, SCAN for bulk
                                  reads/deletes, PEXPIRE for touch.

Module-level singleton ``get_store()`` + ``_inject_redis_client(client)`` test
hook mirror the pattern in ``app/models/agv_state.py``.

``PathKeepalive`` daemon thread mirrors ``LockKeepalive`` in
``app/models/redis_lock.py``.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

Cell = tuple[int, int]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Reservation:
    """Snapshot of one actor's path reservation for one epoch."""

    task_id: str
    actor_id: str
    epoch: int                    # monotonic per (task_id, actor_id)
    cells: list[Cell]
    expires_at: float             # wall-clock seconds (time.time())


# ---------------------------------------------------------------------------
# Key helpers  — always include the full tuple
# ---------------------------------------------------------------------------

_RESV_PREFIX = "resv"
_EPOCH_PREFIX = "epoch"


def _resv_key(task_id: str, actor_id: str, epoch: int, col: int, row: int) -> str:
    """Canonical cell-reservation key.  Never a bare cell key."""
    return f"{_RESV_PREFIX}:task:{task_id}:actor:{actor_id}:epoch:{epoch}:cell:{col}:{row}"


def _actor_epoch_key(task_id: str, actor_id: str) -> str:
    """Epoch counter key for (task_id, actor_id)."""
    return f"{_EPOCH_PREFIX}:task:{task_id}:actor:{actor_id}"


def _actor_resv_prefix(task_id: str, actor_id: str, epoch: int) -> str:
    """Prefix used to SCAN all cell keys for a given (task, actor, epoch)."""
    return f"{_RESV_PREFIX}:task:{task_id}:actor:{actor_id}:epoch:{epoch}:cell:"


def _task_resv_prefix(task_id: str) -> str:
    """Prefix used to SCAN all reservations for a given task."""
    return f"{_RESV_PREFIX}:task:{task_id}:"


def _parse_cell_from_key(key: str) -> Optional[Cell]:
    """Extract ``(col, row)`` from a reservation key.  Returns None on parse error."""
    # Key form: resv:task:<t>:actor:<a>:epoch:<e>:cell:<col>:<row>
    try:
        cell_part = key.rsplit(":cell:", 1)[1]
        col_s, row_s = cell_part.rsplit(":", 1)
        return (int(col_s), int(row_s))
    except (IndexError, ValueError):
        return None


def _parse_actor_from_key(key: str) -> Optional[str]:
    """Extract actor_id from a reservation key."""
    # Key form: resv:task:<t>:actor:<actor_id>:epoch:<e>:cell:<col>:<row>
    try:
        after_actor = key.split(":actor:", 1)[1]
        actor_id = after_actor.split(":epoch:", 1)[0]
        return actor_id
    except (IndexError, ValueError):
        return None


# ---------------------------------------------------------------------------
# In-memory backend helpers
# ---------------------------------------------------------------------------

class _InMemoryReservationStore:
    """Pure in-process store used when INTERACT_STATE=inmemory (default)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # {full_key: expires_at_monotonic}
        self._cells: dict[str, float] = {}
        # {epoch_key: int}
        self._epochs: dict[str, int] = {}

    def _now_ms(self) -> float:
        return time.monotonic() * 1000.0

    def _is_alive(self, expires_at_mono_ms: float) -> bool:
        return self._now_ms() < expires_at_mono_ms

    def next_epoch(self, task_id: str, actor_id: str) -> int:
        key = _actor_epoch_key(task_id, actor_id)
        with self._lock:
            new_val = self._epochs.get(key, 0) + 1
            self._epochs[key] = new_val
            return new_val

    def reserve(
        self,
        task_id: str,
        actor_id: str,
        epoch: int,
        cells: Iterable[Cell],
        ttl_ms: int,
    ) -> bool:
        cells_list = list(cells)
        expires_mono_ms = self._now_ms() + ttl_ms
        owned: list[str] = []
        with self._lock:
            for col, row in cells_list:
                key = _resv_key(task_id, actor_id, epoch, col, row)
                # Check: is this cell already held by someone else?
                for existing_key, exp in self._cells.items():
                    if not self._is_alive(exp):
                        continue
                    if existing_key == key:
                        # Same full tuple — idempotent, update expiry and continue.
                        break
                    # Different key but same cell: check if it belongs to a different actor/epoch.
                    existing_cell = _parse_cell_from_key(existing_key)
                    existing_actor = _parse_actor_from_key(existing_key)
                    if existing_cell == (col, row) and existing_actor != actor_id:
                        # Conflict — rollback and return False.
                        for owned_key in owned:
                            self._cells.pop(owned_key, None)
                        return False
                self._cells[key] = expires_mono_ms
                owned.append(key)
        return True

    def release(self, task_id: str, actor_id: str, epoch: int) -> int:
        prefix = _actor_resv_prefix(task_id, actor_id, epoch)
        count = 0
        with self._lock:
            keys_to_delete = [k for k in self._cells if k.startswith(prefix)]
            for k in keys_to_delete:
                del self._cells[k]
                count += 1
        return count

    def held_by_others(self, task_id: str, exclude_actor: str) -> set[Cell]:
        """Return all live cell reservations for *task_id* not owned by *exclude_actor*."""
        prefix = _task_resv_prefix(task_id)
        result: set[Cell] = set()
        now_mono_ms = self._now_ms()
        with self._lock:
            for key, exp in self._cells.items():
                if not key.startswith(prefix):
                    continue
                if exp <= now_mono_ms:
                    continue
                actor = _parse_actor_from_key(key)
                if actor == exclude_actor:
                    continue
                cell = _parse_cell_from_key(key)
                if cell is not None:
                    result.add(cell)
        return result

    def touch(
        self,
        task_id: str,
        actor_id: str,
        epoch: int,
        ttl_ms: int,
    ) -> bool:
        prefix = _actor_resv_prefix(task_id, actor_id, epoch)
        new_exp = self._now_ms() + ttl_ms
        extended = False
        with self._lock:
            for key in list(self._cells):
                if key.startswith(prefix):
                    self._cells[key] = new_exp
                    extended = True
        return extended


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------

_SCAN_BATCH = 100   # SCAN COUNT hint


class _RedisReservationStore:
    """Redis-backed store used when INTERACT_STATE=redis."""

    def __init__(self, client) -> None:
        self._client = client

    def next_epoch(self, task_id: str, actor_id: str) -> int:
        key = _actor_epoch_key(task_id, actor_id)
        return int(self._client.incr(key))

    def reserve(
        self,
        task_id: str,
        actor_id: str,
        epoch: int,
        cells: Iterable[Cell],
        ttl_ms: int,
    ) -> bool:
        cells_list = list(cells)
        # First, check for live reservations of these physical cells by OTHER actors.
        # We scan the task prefix to find all live cell reservations for this task.
        task_prefix = _task_resv_prefix(task_id)
        owned_cells: set[tuple[int, int]] = set()
        cursor = 0
        while True:
            cursor, keys = self._client.scan(
                cursor, match=task_prefix + "*", count=_SCAN_BATCH
            )
            for existing_key in keys:
                existing_actor = _parse_actor_from_key(existing_key)
                if existing_actor == actor_id:
                    continue  # own key — skip
                existing_cell = _parse_cell_from_key(existing_key)
                if existing_cell is not None:
                    owned_cells.add(existing_cell)
            if cursor == 0:
                break

        # Check conflict: any cell we want to reserve is already held by another actor?
        cells_set = set(cells_list)
        conflict = owned_cells & cells_set
        if conflict:
            return False

        # No conflict — proceed with SET NX per cell.
        owned: list[str] = []
        for col, row in cells_list:
            key = _resv_key(task_id, actor_id, epoch, col, row)
            ok = self._client.set(key, "1", nx=True, px=ttl_ms)
            if ok:
                owned.append(key)
            else:
                # Key already exists. Check whether it's ours (idempotent) or a race conflict.
                existing_actor = None
                # Parse actor from this key directly.
                existing_actor = _parse_actor_from_key(key)
                if existing_actor == actor_id:
                    # Our own key (same full tuple — idempotent); extend TTL.
                    self._client.pexpire(key, ttl_ms)
                else:
                    # Race: another actor set this key between our SCAN and SET NX.
                    if owned:
                        self._client.delete(*owned)
                    return False
        return True

    def release(self, task_id: str, actor_id: str, epoch: int) -> int:
        prefix = _actor_resv_prefix(task_id, actor_id, epoch)
        pattern = prefix + "*"
        count = 0
        cursor = 0
        while True:
            cursor, keys = self._client.scan(cursor, match=pattern, count=_SCAN_BATCH)
            if keys:
                self._client.delete(*keys)
                count += len(keys)
            if cursor == 0:
                break
        return count

    def held_by_others(self, task_id: str, exclude_actor: str) -> set[Cell]:
        prefix = _task_resv_prefix(task_id)
        pattern = prefix + "*"
        result: set[Cell] = set()
        cursor = 0
        while True:
            cursor, keys = self._client.scan(cursor, match=pattern, count=_SCAN_BATCH)
            for key in keys:
                actor = _parse_actor_from_key(key)
                if actor == exclude_actor:
                    continue
                cell = _parse_cell_from_key(key)
                if cell is not None:
                    result.add(cell)
            if cursor == 0:
                break
        return result

    def touch(
        self,
        task_id: str,
        actor_id: str,
        epoch: int,
        ttl_ms: int,
    ) -> bool:
        prefix = _actor_resv_prefix(task_id, actor_id, epoch)
        pattern = prefix + "*"
        extended = False
        cursor = 0
        while True:
            cursor, keys = self._client.scan(cursor, match=pattern, count=_SCAN_BATCH)
            for key in keys:
                self._client.pexpire(key, ttl_ms)
                extended = True
            if cursor == 0:
                break
        return extended


# ---------------------------------------------------------------------------
# ReservationStore — public facade
# ---------------------------------------------------------------------------

class ReservationStore:
    """Public facade that delegates to either the in-memory or Redis backend.

    Do NOT instantiate directly in application code — use ``get_store()``.
    """

    def __init__(self, backend) -> None:
        self._backend = backend

    def next_epoch(self, task_id: str, actor_id: str) -> int:
        """Return the next monotonic epoch for *(task_id, actor_id)*.

        In Redis mode this is an atomic INCR so concurrent callers never see
        the same epoch.
        """
        return self._backend.next_epoch(task_id, actor_id)

    def reserve(
        self,
        task_id: str,
        actor_id: str,
        epoch: int,
        cells: Iterable[Cell],
        ttl_ms: int,
    ) -> bool:
        """Atomically reserve *cells* for *(task_id, actor_id, epoch)*.

        Returns True only when ALL cells were reserved.  On any conflict the
        cells already written by this call are rolled back and False is returned.
        Idempotent: reserving the same full tuple again extends the TTL.

        Key format:
            ``resv:task:<task_id>:actor:<actor_id>:epoch:<epoch>:cell:<col>:<row>``
        """
        return self._backend.reserve(task_id, actor_id, epoch, cells, ttl_ms)

    def release(self, task_id: str, actor_id: str, epoch: int) -> int:
        """Delete all cell reservation keys for *(task_id, actor_id, epoch)*.

        Uses SCAN-by-prefix; the caller does not need to track individual keys.
        Returns the number of keys deleted.
        """
        return self._backend.release(task_id, actor_id, epoch)

    def held_by_others(self, task_id: str, exclude_actor: str) -> set[Cell]:
        """Return all live reserved cells for *task_id* NOT owned by *exclude_actor*.

        Scope: ONE task (prefix ``resv:task:<task_id>:``).  Self-owned
        reservations are always filtered out so they never block the caller.
        """
        return self._backend.held_by_others(task_id, exclude_actor)

    def touch(
        self,
        task_id: str,
        actor_id: str,
        epoch: int,
        ttl_ms: int,
    ) -> bool:
        """Renew the TTL on all cell keys for *(task_id, actor_id, epoch)*.

        Returns True if at least one key was extended.  Returns False when no
        matching keys exist (e.g. all expired or never reserved).
        """
        return self._backend.touch(task_id, actor_id, epoch, ttl_ms)


# ---------------------------------------------------------------------------
# Module-level singleton + dual-backend selection
# ---------------------------------------------------------------------------

_MODE_KEY = "INTERACT_STATE"

_store_lock = threading.Lock()
_store_instance: Optional[ReservationStore] = None

# Test hook — injected fakeredis client.
_injected_redis_client = None


def _get_mode() -> str:
    return os.environ.get(_MODE_KEY, "inmemory").lower().strip()


def _inject_redis_client(client) -> None:
    """Test hook: inject a fake Redis client and reset the singleton.

    Call before ``get_store()`` in each test that exercises the Redis backend.
    Mirrors the pattern in ``app/models/agv_state.py``.
    """
    global _injected_redis_client, _store_instance
    _injected_redis_client = client
    _store_instance = None  # force re-creation on next get_store()


def _get_redis():
    global _injected_redis_client
    if _injected_redis_client is not None:
        return _injected_redis_client
    import redis as redis_lib
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis_lib.Redis.from_url(url, decode_responses=True)


def get_store() -> ReservationStore:
    """Return the process-wide singleton ``ReservationStore``.

    Backend is chosen once at first call based on ``INTERACT_STATE`` env var
    (or the injected client set by ``_inject_redis_client``).
    """
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                if _get_mode() == "redis" or _injected_redis_client is not None:
                    backend = _RedisReservationStore(_get_redis())
                else:
                    backend = _InMemoryReservationStore()
                _store_instance = ReservationStore(backend)
    return _store_instance


# ---------------------------------------------------------------------------
# PathKeepalive — mirrors LockKeepalive
# ---------------------------------------------------------------------------

class PathKeepalive:
    """Daemon thread that renews a path reservation's TTL at a regular interval.

    Mirrors ``LockKeepalive`` in ``app/models/redis_lock.py``.

    Usage::

        keepalive = PathKeepalive(store, task_id, actor_id, epoch, ttl_ms=180_000, interval_s=30.0)
        keepalive.start()
        try:
            ... traverse cells ...
        finally:
            store.release(task_id, actor_id, epoch)
            keepalive.stop()

    Or as a context manager::

        with PathKeepalive(store, task_id, actor_id, epoch, ttl_ms=180_000, interval_s=30.0):
            ... traverse cells ...
        store.release(task_id, actor_id, epoch)

    Parameters
    ----------
    store:
        The ``ReservationStore`` holding the reservation.
    task_id:
        Task identifier.
    actor_id:
        Actor identifier (e.g. ``"agv-1"``).
    epoch:
        Epoch number to keep alive.
    ttl_ms:
        TTL in milliseconds to reset on each touch.
    interval_s:
        How often to call ``touch``.  Default: ``ttl_ms / 3 / 1000``.
    """

    def __init__(
        self,
        store: ReservationStore,
        task_id: str,
        actor_id: str,
        epoch: int,
        ttl_ms: int,
        interval_s: Optional[float] = None,
    ) -> None:
        self._store = store
        self._task_id = task_id
        self._actor_id = actor_id
        self._epoch = epoch
        self._ttl_ms = ttl_ms
        self._interval_s = interval_s if interval_s is not None else (ttl_ms / 3 / 1000)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "PathKeepalive":
        """Start the keepalive daemon thread.  Returns self for chaining."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"path-keepalive-{self._actor_id}-e{self._epoch}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Signal the keepalive thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 1.0)

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=self._interval_s):
            self._store.touch(self._task_id, self._actor_id, self._epoch, self._ttl_ms)

    def __enter__(self) -> "PathKeepalive":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
