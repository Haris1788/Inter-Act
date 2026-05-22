"""LaneLock mutual exclusion + occupancy (Phase 2).

LaneLock mirrors TransferPoint: exactly one holder; waiters block; a
cancel_check lets a waiter bail out promptly.
"""

import threading
import time

from app.models import grid_state
from app.models.grid_state import LaneLock


def test_single_holder_and_release():
    lane = LaneLock()
    assert lane.is_free()
    assert lane.acquire("agv-1", timeout=1) is True
    assert lane.holder() == "agv-1"
    # Re-acquire by same holder is a no-op success.
    assert lane.acquire("agv-1", timeout=1) is True
    # A different AGV cannot take it while held (short timeout -> False).
    assert lane.acquire("agv-2", timeout=0.2) is False
    assert lane.release("agv-1") is True
    assert lane.is_free()


def test_second_agv_unblocks_after_release():
    lane = LaneLock()
    lane.acquire("agv-1", timeout=1)
    got = []

    def waiter():
        got.append(lane.acquire("agv-2", timeout=3))

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.3)               # agv-2 is blocked
    assert got == []
    lane.release("agv-1")
    t.join(timeout=3)
    assert got == [True]
    assert lane.holder() == "agv-2"


def test_cancel_check_aborts_waiter():
    lane = LaneLock()
    lane.acquire("agv-1", timeout=5)
    # cancel_check returns False -> waiter should give up fast, not wait full timeout.
    t0 = time.time()
    ok = lane.acquire("agv-2", timeout=10, cancel_check=lambda: False)
    assert ok is False
    assert time.time() - t0 < 2.0


def test_force_release_and_occupancy():
    lane = LaneLock()
    lane.acquire("agv-3", timeout=1)
    lane.force_release()
    assert lane.is_free()

    grid_state.reset()
    grid_state.set_cell("agv-1", [2, 1])
    assert grid_state.cell_holder([2, 1]) == "agv-1"
    assert grid_state.is_cell_free([2, 1]) is False
    assert grid_state.is_cell_free([2, 1], ignore="agv-1") is True
    assert grid_state.is_cell_free([3, 1]) is True
    snap = grid_state.snapshot()
    assert snap["occupancy"]["agv-1"] == [2, 1]
    grid_state.clear_cell("agv-1")
    assert grid_state.cell_holder([2, 1]) is None
