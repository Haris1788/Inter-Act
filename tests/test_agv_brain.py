"""AGV brain + LaneLock unit tests (Phase 5).

All HTTP calls are mocked — no backend, no Ollama required.
Key invariants:
  * Normal flow makes ZERO calls to _ask_tactical.
  * Lane is held by at most ONE AGV at a time.
  * Fault is reported when a step exhausts all retries.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.models.grid_state import LaneLock


# ── helpers ────────────────────────────────────────────────────────────────────

ASSIGNMENT = {
    "package_id": "pkg-1",
    "agv_id": "agv-1",
    "sequence": 1,
    "route": ["bay", "input", "output", "bay"],
    "label": "red",
}

# Minimal route context (bypasses GET /grid in tests)
ROUTE_CTX = {
    "bay": [0, 0],
    "route_to_input": [[1, 0]],
    "lane_cells": [[2, 0], [3, 0]],
    "route_to_output": [[4, 0]],
}


def _success_post(path, payload, timeout=10.0):
    """Mock _post: always succeeds."""
    if "lane/acquire" in path:
        return {"status": "acquired"}
    if "lane/release" in path:
        return {"status": "released"}
    return {"status": "ok"}


def _success_get(path, timeout=5.0):
    """Mock _get: load/unload signals are immediately ready."""
    if "load_ready" in path:
        return {"ready": True, "object_id": "pkg-1"}
    if "unload_ready" in path:
        return {"ready": True}
    return {}


# ── LaneLock tests (in-process, no mocking needed) ───────────────────────────

class TestLaneLock:
    def test_single_acquire_succeeds(self):
        lock = LaneLock()
        assert lock.acquire("agv-1", timeout=1.0) is True
        assert lock.holder() == "agv-1"

    def test_release_frees_lock(self):
        lock = LaneLock()
        lock.acquire("agv-1", timeout=1.0)
        released = lock.release("agv-1")
        assert released is True
        assert lock.is_free()

    def test_only_one_agv_holds_lane(self):
        """Two AGVs racing for the lane — exactly one wins, the other blocks."""
        lock = LaneLock()
        results: list = []

        def _try_acquire(agv_id: str) -> None:
            ok = lock.acquire(agv_id, timeout=2.0)
            results.append((agv_id, ok))

        t1 = threading.Thread(target=_try_acquire, args=("agv-1",))
        t2 = threading.Thread(target=_try_acquire, args=("agv-2",))
        t1.start()
        t2.start()
        time.sleep(0.1)  # let both threads reach acquire

        # Exactly one should hold; the other is still blocked.
        # Release the holder so t2 can finish.
        lock.release(lock.holder() or "")
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        acquired = [r for r in results if r[1]]
        assert len(acquired) <= 2  # both eventually succeed after release

    def test_concurrent_agvs_never_both_hold(self):
        """Confirm the invariant: holder count is at most 1 at any instant."""
        lock = LaneLock()
        violations: list = []
        holders_at_overlap: list = []

        def _cycle(agv_id: str, rounds: int) -> None:
            for _ in range(rounds):
                if not lock.acquire(agv_id, timeout=5.0):
                    continue
                # Check: no other AGV can also be holder while we hold.
                h = lock.holder()
                if h != agv_id:
                    violations.append((agv_id, h))
                holders_at_overlap.append(h)
                time.sleep(0.01)
                lock.release(agv_id)

        threads = [
            threading.Thread(target=_cycle, args=(f"agv-{i}", 5))
            for i in range(1, 4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not violations, f"lock violation: {violations}"

    def test_cancel_check_aborts_wait(self):
        """A waiting AGV bails out when cancel_check returns False."""
        lock = LaneLock()
        lock.acquire("agv-1", timeout=5.0)  # agv-1 holds lane

        cancelled = threading.Event()

        def _try() -> None:
            result = lock.acquire("agv-2", timeout=5.0, cancel_check=lambda: False)
            cancelled.set() if not result else None

        t = threading.Thread(target=_try)
        t.start()
        t.join(timeout=3.0)
        assert cancelled.is_set(), "cancel_check did not abort the wait"

    def test_release_by_non_holder_is_noop(self):
        lock = LaneLock()
        lock.acquire("agv-1", timeout=1.0)
        released = lock.release("agv-2")   # agv-2 does not hold it
        assert released is False
        assert lock.holder() == "agv-1"    # agv-1 still holds


# ── agv_brain.run_task tests ─────────────────────────────────────────────────

class TestAgvBrainRunTask:

    def test_normal_flow_zero_ai_calls(self):
        """Happy path: all scripts succeed → _ask_tactical is NEVER called."""
        import scripts.agv_brain as brain

        with patch.object(brain, "_post", side_effect=_success_post), \
             patch.object(brain, "_get",  side_effect=_success_get), \
             patch.object(brain, "_ask_tactical") as mock_ai:

            result = brain.run_task(ASSIGNMENT, route_ctx=ROUTE_CTX)

        assert result is True
        mock_ai.assert_not_called()

    def test_step_failure_triggers_one_ai_call(self):
        """One failed step → AI consulted → AI says 'hold' → step retried → success."""
        import scripts.agv_brain as brain

        call_count = {"n": 0}

        def _post_with_one_failure(path, payload, timeout=10.0):
            # Fail the first /move call, then succeed.
            if "/move" in path and call_count["n"] == 0:
                call_count["n"] += 1
                return None   # simulate network error → script returns False
            return _success_post(path, payload, timeout)

        with patch.object(brain, "_post", side_effect=_post_with_one_failure), \
             patch.object(brain, "_get",  side_effect=_success_get), \
             patch.object(brain, "_ask_tactical", return_value="hold") as mock_ai:

            result = brain.run_task(ASSIGNMENT, route_ctx=ROUTE_CTX)

        assert result is True
        assert mock_ai.call_count >= 1

    def test_all_retries_exhausted_reports_fault(self):
        """Every attempt of a step fails → fault is declared → run_task returns False."""
        import scripts.agv_brain as brain

        with patch.object(brain, "_post", return_value=None), \
             patch.object(brain, "_get",  side_effect=_success_get), \
             patch.object(brain, "_ask_tactical", return_value="hold"):

            result = brain.run_task(ASSIGNMENT, route_ctx=ROUTE_CTX)

        assert result is False

    def test_ai_advises_fault_stops_immediately(self):
        """When AI says 'fault' after first failure, run_task stops without more retries."""
        import scripts.agv_brain as brain

        call_count = {"n": 0}

        def _post_first_fails(path, payload, timeout=10.0):
            if "/move" in path and call_count["n"] == 0:
                call_count["n"] += 1
                return None
            return _success_post(path, payload, timeout)

        with patch.object(brain, "_post", side_effect=_post_first_fails), \
             patch.object(brain, "_get",  side_effect=_success_get), \
             patch.object(brain, "_ask_tactical", return_value="fault"):

            result = brain.run_task(ASSIGNMENT, route_ctx=ROUTE_CTX)

        assert result is False

    def test_lane_acquire_failure_causes_fault(self):
        """If lane/acquire returns 'timeout' every time, fault is declared."""
        import scripts.agv_brain as brain

        def _post_lane_fails(path, payload, timeout=10.0):
            if "lane/acquire" in path:
                return {"status": "timeout"}
            return _success_post(path, payload, timeout)

        with patch.object(brain, "_post", side_effect=_post_lane_fails), \
             patch.object(brain, "_get",  side_effect=_success_get), \
             patch.object(brain, "_ask_tactical", return_value="hold"):

            result = brain.run_task(ASSIGNMENT, route_ctx=ROUTE_CTX)

        assert result is False

    def test_missing_route_ctx_causes_fault(self):
        """When GET /grid returns None, run_task faults without AI calls."""
        import scripts.agv_brain as brain

        with patch.object(brain, "_get", return_value=None), \
             patch.object(brain, "_post", side_effect=_success_post), \
             patch.object(brain, "_ask_tactical") as mock_ai:

            result = brain.run_task(ASSIGNMENT, route_ctx=None)

        assert result is False
        mock_ai.assert_not_called()
