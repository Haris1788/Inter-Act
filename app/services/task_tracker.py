"""Phase 2 Step 7-9: track active tasks across the failure / replan workflow.

Responsibilities:
  - On PATH_PROPOSED, remember the path that was just announced (this is the
    most reliable place to capture the path because every dispatch round
    publishes it before fan-out to actors).
  - On ACTOR_FAILED, find active tasks that include the failed actor and
    publish REPLAN_REQUIRED (with an excluded-actors list bounded by
    MAX_REPLAN_ATTEMPTS).
  - On TASK_COMPLETED, mark the task done and release every participant via
    ACTOR_FREE.

Listening side only — never holds the dispatch lock. The actual replan loop
lives in app/services/replan_handler.py so this module stays small and
testable.
"""
from __future__ import annotations

import threading
import time
from typing import Any

MAX_REPLAN_ATTEMPTS = 3

_lock = threading.Lock()
# task_id -> {
#   "path": [...], "status": running|replanning|completed|aborted,
#   "attempts": int, "excluded": set[str], "task": dict, "ts": float
# }
_tasks: dict[str, dict[str, Any]] = {}


def attach(bus) -> None:
    from app.agents.events import (
        PATH_PROPOSED, ACTOR_FAILED, TASK_COMPLETED,
    )
    bus.subscribe(PATH_PROPOSED, _on_proposed)
    bus.subscribe(ACTOR_FAILED, _on_failed)
    bus.subscribe(TASK_COMPLETED, _on_completed)


def register_task(task_id: str, task: dict) -> None:
    """Called by dispatch routes when a fresh /bus/task/dispatch round starts."""
    with _lock:
        if task_id not in _tasks:
            _tasks[task_id] = {
                "path": [],
                "status": "running",
                "attempts": 0,
                "excluded": set(),
                "task": dict(task or {}),
                "ts": time.time(),
            }


def _on_proposed(env: dict) -> None:
    task_id = env.get("taskId")
    payload = env.get("payload", {}) or {}
    path = list(payload.get("path", []))
    if not task_id:
        return
    with _lock:
        entry = _tasks.setdefault(task_id, {
            "path": list(path), "status": "running", "attempts": 0,
            "excluded": set(), "task": {}, "ts": env.get("ts", time.time()),
        })
        entry["path"] = list(path)


def _on_failed(env: dict) -> None:
    """An actor reported a fault. If it was in an active task, fire REPLAN_REQUIRED."""
    from app.agents.events import REPLAN_REQUIRED, make_envelope
    from app.agents.message_bus import get_bus

    failed_actor = env.get("actorId")
    if not failed_actor:
        return
    bus = get_bus()

    # Snapshot the affected tasks under lock, then publish outside the lock.
    affected: list[tuple[str, dict]] = []
    with _lock:
        for task_id, entry in _tasks.items():
            if entry["status"] == "running" and failed_actor in entry["path"]:
                affected.append((task_id, entry))

    for task_id, entry in affected:
        with _lock:
            entry["excluded"].add(failed_actor)
            entry["attempts"] += 1
            attempts = entry["attempts"]
            excluded = list(entry["excluded"])
            task = dict(entry["task"])
            if attempts > MAX_REPLAN_ATTEMPTS:
                entry["status"] = "aborted"
                aborted = True
            else:
                entry["status"] = "replanning"
                aborted = False
        if aborted:
            continue
        bus.publish(REPLAN_REQUIRED, make_envelope(
            REPLAN_REQUIRED, "task_tracker",
            {"failedActor": failed_actor, "excluded": excluded,
             "attempts": attempts, "task": task},
            task_id=task_id,
        ))


def _on_completed(env: dict) -> None:
    """Mark the task done; release every actor still on its path."""
    from app.agents.events import ACTOR_FREE, make_envelope
    from app.agents.message_bus import get_bus

    task_id = env.get("taskId")
    if not task_id:
        return
    bus = get_bus()

    with _lock:
        entry = _tasks.get(task_id)
        if not entry:
            return
        path = list(entry["path"])
        entry["status"] = "completed"

    for actor_id in path:
        bus.publish(ACTOR_FREE, make_envelope(
            ACTOR_FREE, actor_id,
            {"reason": f"task {task_id} completed"},
            task_id=task_id,
        ))


def mark_replanning_done(task_id: str, success: bool, new_path: list[str] | None = None) -> None:
    """Called by replan_handler after a replan attempt finishes."""
    with _lock:
        entry = _tasks.get(task_id)
        if not entry:
            return
        if success:
            entry["status"] = "running"
            if new_path is not None:
                entry["path"] = list(new_path)
        else:
            entry["status"] = "aborted"


def get(task_id: str) -> dict | None:
    with _lock:
        entry = _tasks.get(task_id)
        if not entry:
            return None
        return {
            "taskId": task_id,
            "path": list(entry["path"]),
            "status": entry["status"],
            "attempts": entry["attempts"],
            "excluded": list(entry["excluded"]),
            "task": dict(entry["task"]),
        }


def list_active() -> list[dict]:
    with _lock:
        return [
            {
                "taskId": tid,
                "path": list(e["path"]),
                "status": e["status"],
                "attempts": e["attempts"],
                "excluded": list(e["excluded"]),
            }
            for tid, e in _tasks.items()
            if e["status"] in ("running", "replanning")
        ]
