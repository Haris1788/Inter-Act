"""Collects TASK_AVAILABILITY envelopes per task. 1.5s window per task."""
from __future__ import annotations

import threading
import time
from typing import Any

_responses: dict[str, list[dict]] = {}
_lock = threading.Lock()


def attach(bus) -> None:
    from app.agents.events import TASK_AVAILABILITY
    bus.subscribe(TASK_AVAILABILITY, _on_availability)


def _on_availability(env: dict) -> None:
    task_id = env.get("taskId")
    if not task_id:
        return
    with _lock:
        _responses.setdefault(task_id, []).append({
            "actorId": env.get("actorId"),
            "available": env.get("payload", {}).get("available", False),
            "score": env.get("payload", {}).get("score", 0.0),
            "reason": env.get("payload", {}).get("reason", ""),
            "ts": env.get("ts", time.time()),
        })


def open_round(task_id: str) -> None:
    """Reset the bucket for a new evaluation round."""
    with _lock:
        _responses[task_id] = []


def collect(task_id: str, window_s: float = 1.5) -> list[dict]:
    """Block until window_s elapsed, then return responses for task_id."""
    time.sleep(window_s)
    with _lock:
        return list(_responses.get(task_id, []))
