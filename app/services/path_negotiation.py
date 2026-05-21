"""Collects PATH_ACCEPTED / PATH_REJECTED envelopes per task. 1.5s window."""
from __future__ import annotations

import threading
import time

_responses: dict[str, dict[str, dict]] = {}   # task_id -> {actorId: {...}}
_lock = threading.Lock()


def attach(bus) -> None:
    from app.agents.events import PATH_ACCEPTED, PATH_REJECTED
    bus.subscribe(PATH_ACCEPTED, lambda e: _on(e, True))
    bus.subscribe(PATH_REJECTED, lambda e: _on(e, False))


def _on(env: dict, accepted: bool) -> None:
    task_id = env.get("taskId")
    actor_id = env.get("actorId")
    if not task_id or not actor_id:
        return
    with _lock:
        _responses.setdefault(task_id, {})[actor_id] = {
            "accepted": accepted,
            "reason": env.get("payload", {}).get("reason", ""),
            "ts": env.get("ts", time.time()),
        }


def open_round(task_id: str) -> None:
    with _lock:
        _responses[task_id] = {}


def collect(task_id: str, window_s: float = 1.5) -> dict[str, dict]:
    time.sleep(window_s)
    with _lock:
        return dict(_responses.get(task_id, {}))
