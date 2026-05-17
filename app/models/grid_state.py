"""Warehouse grid runtime: cell occupancy + shared-lane mutual exclusion.

`LaneLock` is the spatial analogue of `scripts/run_demo.py:TransferPoint`. It
uses the same proven `threading.Condition` + `cancel_check` idiom: exactly one
AGV may hold the shared travel lane at a time; others block in
`acquire(...)` until it is released, and a waiting AGV bails out promptly when
its `cancel_check` says it has been faulted/stopped (drain-before-deadlock).

The Phase 2 deliverable is this primitive plus an occupancy snapshot for the
UI / `/grid`. The AGV movement loop that actually drives `acquire/release`
around bay->input->output->bay is Phase 5.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

Cell = Tuple[int, int]


class LaneLock:
    """Single-holder lock for the shared travel lane.

    Mirrors TransferPoint: all state changes happen under the Condition's
    internal lock; waiters re-check on a bounded timeout so a cancel_check
    (e.g. "am I still allowed to run?") fires within ~2s even if no
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
        """Block until the lane is free, then take it for `agv_id`.

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
        """Release the lane if `agv_id` holds it. Returns True if released."""
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


# ── Cell occupancy registry ──────────────────────────────────────────────────
_lock = threading.RLock()
_occupancy: Dict[str, Cell] = {}          # agv_id -> (col,row)
_lane_lock = LaneLock()


def reset() -> None:
    """Clear occupancy and free the lane. Called at pipeline start."""
    global _lane_lock
    with _lock:
        _occupancy.clear()
    _lane_lock.force_release()


def set_cell(agv_id: str, cell: List[int]) -> None:
    with _lock:
        _occupancy[agv_id] = (int(cell[0]), int(cell[1]))


def clear_cell(agv_id: str) -> None:
    with _lock:
        _occupancy.pop(agv_id, None)


def cell_holder(cell: List[int]) -> Optional[str]:
    """Return the AGV occupying `cell`, or None."""
    target = (int(cell[0]), int(cell[1]))
    with _lock:
        for agv_id, c in _occupancy.items():
            if c == target:
                return agv_id
    return None


def is_cell_free(cell: List[int], ignore: Optional[str] = None) -> bool:
    holder = cell_holder(cell)
    return holder is None or holder == ignore


def lane() -> LaneLock:
    """The process-wide shared-lane lock."""
    return _lane_lock


def snapshot() -> dict:
    """Serializable view for `/grid` and the `grid_update` socket event."""
    with _lock:
        occ = {k: list(v) for k, v in _occupancy.items()}
    return {"occupancy": occ, "lane_holder": _lane_lock.holder()}
