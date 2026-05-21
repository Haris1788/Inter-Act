"""Phase 2 Step 8: react to REPLAN_REQUIRED by running a fresh negotiation
round with the failed actor (and any previously-failed actors) excluded.

Lives in a separate module from task_tracker so the tracker stays a pure
state-keeper and this module owns the (blocking, HTTP-fanout) replan loop.
"""
from __future__ import annotations

import threading

import requests


_AGV_URLS = {
    "agv-1": "http://agv-1:8100",
    "agv-2": "http://agv-2:8100",
    "agv-3": "http://agv-3:8100",
}
_ARM_URLS = {
    "arm-1": "http://arm-1:8000",
    "arm-2": "http://arm-2:8000",
}


def attach(bus) -> None:
    from app.agents.events import REPLAN_REQUIRED
    bus.subscribe(REPLAN_REQUIRED, _on_replan)


def _release_http(actor_id: str) -> None:
    """Tell an actor to clear its local busy flag via HTTP /release."""
    url = _AGV_URLS.get(actor_id) or _ARM_URLS.get(actor_id)
    if not url:
        return
    try:
        requests.post(f"{url}/release", timeout=1.0)
    except Exception:
        pass


def _on_replan(env: dict) -> None:
    task_id = env.get("taskId")
    payload = env.get("payload", {}) or {}
    if not task_id:
        return
    # Long-running — push into a daemon thread so we don't block the bus dispatch.
    threading.Thread(
        target=_run_replan, args=(task_id, payload), daemon=True
    ).start()


def _run_replan(task_id: str, payload: dict) -> None:
    """Release the non-failed actors on the old path, then re-dispatch."""
    from app.services import task_tracker
    from app.routes.dispatch_routes import _run_dispatch_round

    failed = payload.get("failedActor")
    excluded = set(payload.get("excluded") or [])
    task = payload.get("task") or {}

    entry = task_tracker.get(task_id) or {}
    old_path = entry.get("path", [])

    # Free the survivors on the previous path so they can re-participate.
    for actor_id in old_path:
        if actor_id != failed:
            _release_http(actor_id)
            # Also publish ACTOR_FREE so the backend roster reflects it.
            try:
                from app.agents.events import ACTOR_FREE, make_envelope
                from app.agents.message_bus import get_bus
                get_bus().publish(ACTOR_FREE, make_envelope(
                    ACTOR_FREE, actor_id,
                    {"reason": f"replan after {failed} failed"},
                    task_id=task_id,
                ))
            except Exception:
                pass

    # Re-dispatch. Bypass HTTP — call the round function directly. Reuse the
    # same task_id so the tracker, not a new id, holds the canonical history.
    result = _run_dispatch_round(task_id=task_id, task=task, excluded=excluded)
    success = result.get("status") == "agreed"
    new_path = result.get("path") if success else None
    task_tracker.mark_replanning_done(task_id, success=success, new_path=new_path)
