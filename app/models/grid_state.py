"""Warehouse grid runtime: cell occupancy + shared-lane mutual exclusion.

``LaneLock`` is the spatial analogue of ``scripts/run_demo.py:TransferPoint``. It
uses the same proven ``threading.Condition`` + ``cancel_check`` idiom: exactly one
AGV may hold the shared travel lane at a time; others block in
``acquire(...)`` until it is released, and a waiting AGV bails out promptly when
its ``cancel_check`` says it has been faulted/stopped (drain-before-deadlock).

## Backend selection

``INTERACT_STATE=inmemory``  (default) — all state lives in-process (original
behaviour; all existing unit tests pass with no Redis dependency).

``INTERACT_STATE=redis`` — locks are ``RedisLock`` instances backed by Redis;
cell occupancy goes into the Redis Hash ``grid:occupancy``.  The Redis client is
obtained lazily from ``REDIS_URL``.

All public functions (``lane()``, ``station_lock()``, ``set_cell()``,
``cell_holder()``, ``is_cell_free()``, ``snapshot()``, ``reset()``,
``clear_cell()``) have byte-identical signatures regardless of backend.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

Cell = Tuple[int, int]

# ── Backend mode ──────────────────────────────────────────────────────────────
# Resolved once at import time (or after _reset_backend() in tests).
_MODE_KEY = "INTERACT_STATE"


def _get_mode() -> str:
    return os.environ.get(_MODE_KEY, "inmemory").lower().strip()


# ── In-memory LaneLock (kept for the inmemory path) ──────────────────────────

class LaneLock:
    """Single-holder lock for the shared travel lane.

    Mirrors TransferPoint: all state changes happen under the Condition's
    internal lock; waiters re-check on a bounded timeout so a cancel_check
    (e.g. "am I still allowed to run?") fires within ~2 s even if no
    release/notify arrives.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._holder: Optional[str] = None

    def acquire(
        self,
        agv_id: str,
        timeout: float = 60.0,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Block until the lane is free, then take it for ``agv_id``.

        Returns True on success, False on timeout or cancel. Re-entrant for
        the same holder (calling twice from one AGV is a no-op success).
        """
        deadline = time.time() + timeout
        with self._cond:
            if self._holder == agv_id:
                return True
            while self._holder is not None:
                if cancel_check and not cancel_check():
                    return False
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=min(remaining, 2.0))
            self._holder = agv_id
            self._cond.notify_all()
            return True

    def release(self, agv_id: str) -> bool:
        """Release the lane if ``agv_id`` holds it. Returns True if released."""
        with self._cond:
            if self._holder != agv_id:
                return False
            self._holder = None
            self._cond.notify_all()
            return True

    def force_release(self) -> None:
        """Drop the lane regardless of holder (used on fault/reset)."""
        with self._cond:
            self._holder = None
            self._cond.notify_all()

    def holder(self) -> Optional[str]:
        with self._cond:
            return self._holder

    def is_free(self) -> bool:
        with self._cond:
            return self._holder is None

    # touch() is a no-op for in-memory path (no TTL to renew).
    def touch(self, holder: str) -> bool:  # noqa: ARG002
        return True


# ── Redis key constants ───────────────────────────────────────────────────────
_REDIS_OCCUPANCY_KEY = "grid:occupancy"
_REDIS_LANE_KEY = "grid:lock:lane"
_REDIS_STATION_PREFIX = "grid:lock:station:"

# ── Lazy Redis client ─────────────────────────────────────────────────────────
_redis_client = None
_redis_client_lock = threading.Lock()


def _get_redis():
    global _redis_client
    if _redis_client is None:
        with _redis_client_lock:
            if _redis_client is None:
                import redis as redis_lib
                url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                _redis_client = redis_lib.Redis.from_url(url, decode_responses=True)
    return _redis_client


def _inject_redis_client(client) -> None:
    """Test hook: inject a fake Redis client."""
    global _redis_client
    _redis_client = client


# ── In-memory state (inmemory backend) ───────────────────────────────────────
_lock = threading.RLock()
_occupancy: Dict[str, Cell] = {}          # agv_id -> (col, row)
_lane_lock: LaneLock = LaneLock()
_station_locks: Dict[str, LaneLock] = {
    "input_arm": LaneLock(),
    "output_arm": LaneLock(),
}

# ── Redis-backed lock cache (one RedisLock instance per key) ─────────────────
_redis_locks: Dict[str, "redis_lock_module.RedisLock"] = {}  # type: ignore[name-defined]
_redis_locks_lock = threading.Lock()


def _get_redis_lock(key: str):
    """Return a cached RedisLock for ``key``, creating it if absent."""
    from app.models.redis_lock import RedisLock
    with _redis_locks_lock:
        if key not in _redis_locks:
            _redis_locks[key] = RedisLock(key=key, redis_client=_get_redis())
        return _redis_locks[key]


# ── Public API ────────────────────────────────────────────────────────────────

def reset() -> None:
    """Clear occupancy and free all locks. Called at pipeline start."""
    global _lane_lock
    if _get_mode() == "redis":
        r = _get_redis()
        r.delete(_REDIS_OCCUPANCY_KEY)
        r.delete(_REDIS_LANE_KEY)
        # Clear any cached station locks.
        with _redis_locks_lock:
            for key, rl in list(_redis_locks.items()):
                rl.force_release()
        # Re-add defaults.
        for name in ("input_arm", "output_arm"):
            _get_redis_lock(f"{_REDIS_STATION_PREFIX}{name}")
    else:
        with _lock:
            _occupancy.clear()
        _lane_lock.force_release()
        for sl in _station_locks.values():
            sl.force_release()


def set_cell(agv_id: str, cell: List[int]) -> None:
    if _get_mode() == "redis":
        r = _get_redis()
        r.hset(_REDIS_OCCUPANCY_KEY, agv_id, f"{int(cell[0])},{int(cell[1])}")
    else:
        with _lock:
            _occupancy[agv_id] = (int(cell[0]), int(cell[1]))


def clear_cell(agv_id: str) -> None:
    if _get_mode() == "redis":
        _get_redis().hdel(_REDIS_OCCUPANCY_KEY, agv_id)
    else:
        with _lock:
            _occupancy.pop(agv_id, None)


def cell_holder(cell: List[int]) -> Optional[str]:
    """Return the AGV occupying ``cell``, or None."""
    target_col = int(cell[0])
    target_row = int(cell[1])
    if _get_mode() == "redis":
        r = _get_redis()
        all_fields = r.hgetall(_REDIS_OCCUPANCY_KEY)
        for agv_id, coord in all_fields.items():
            col_s, row_s = coord.split(",", 1)
            if int(col_s) == target_col and int(row_s) == target_row:
                return agv_id
        return None
    else:
        target = (target_col, target_row)
        with _lock:
            for agv_id, c in _occupancy.items():
                if c == target:
                    return agv_id
        return None


def is_cell_free(cell: List[int], ignore: Optional[str] = None) -> bool:
    holder = cell_holder(cell)
    return holder is None or holder == ignore


def lane():
    """The process-wide shared-lane lock (LaneLock or RedisLock)."""
    if _get_mode() == "redis":
        return _get_redis_lock(_REDIS_LANE_KEY)
    return _lane_lock


def station_lock(name: str):
    """Return the per-station docking lock (``'input_arm'`` or ``'output_arm'``)."""
    if _get_mode() == "redis":
        return _get_redis_lock(f"{_REDIS_STATION_PREFIX}{name}")
    if name not in _station_locks:
        _station_locks[name] = LaneLock()
    return _station_locks[name]


def snapshot() -> dict:
    """Serializable view for ``/grid`` and the ``grid_update`` socket event."""
    if _get_mode() == "redis":
        r = _get_redis()
        all_fields = r.hgetall(_REDIS_OCCUPANCY_KEY)
        occ = {}
        for agv_id, coord in all_fields.items():
            col_s, row_s = coord.split(",", 1)
            occ[agv_id] = [int(col_s), int(row_s)]
        lh = r.get(_REDIS_LANE_KEY)
        return {"occupancy": occ, "lane_holder": lh}
    else:
        with _lock:
            occ = {k: list(v) for k, v in _occupancy.items()}
        return {"occupancy": occ, "lane_holder": _lane_lock.holder()}
