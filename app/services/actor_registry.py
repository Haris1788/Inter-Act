"""In-memory roster of actors that have announced themselves on the bus."""
from __future__ import annotations

import threading
import time
from typing import Any

from app.agents.events import ACTOR_ONLINE, ACTOR_OFFLINE, CAPABILITY_DECLARED


_roster: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _on_online(env: dict) -> None:
    aid = env.get("actorId")
    if not aid:
        return
    with _lock:
        _roster[aid] = {
            "actorId": aid,
            "kind": env.get("payload", {}).get("kind", "unknown"),
            "status": "FREE",
            "online_since": env.get("ts", time.time()),
            "capabilities": _roster.get(aid, {}).get("capabilities", {}),
        }


def _on_capability(env: dict) -> None:
    aid = env.get("actorId")
    if not aid:
        return
    with _lock:
        _roster.setdefault(aid, {"actorId": aid, "status": "FREE"})
        _roster[aid]["capabilities"] = env.get("payload", {})


def _on_offline(env: dict) -> None:
    aid = env.get("actorId")
    with _lock:
        _roster.pop(aid, None)


def _on_busy(env: dict) -> None:
    aid = env.get("actorId")
    if not aid:
        return
    with _lock:
        if aid in _roster:
            _roster[aid]["status"] = "BUSY"
            _roster[aid]["current_task"] = env.get("taskId")


def _on_free(env: dict) -> None:
    aid = env.get("actorId")
    if not aid:
        return
    with _lock:
        if aid in _roster:
            _roster[aid]["status"] = "FREE"
            _roster[aid].pop("current_task", None)
            _roster[aid].pop("fault_reason", None)


def _on_failed(env: dict) -> None:
    """Phase 2 Step 7: actor has reported a fault."""
    aid = env.get("actorId")
    if not aid:
        return
    payload = env.get("payload", {}) or {}
    with _lock:
        if aid in _roster:
            _roster[aid]["status"] = "FAILED"
            _roster[aid]["fault_reason"] = payload.get("reason", "unspecified")
            _roster[aid]["fault_ts"] = env.get("ts")


def attach(bus) -> None:
    from app.agents.events import (
        ACTOR_ONLINE, ACTOR_OFFLINE, CAPABILITY_DECLARED,
        ACTOR_BUSY, ACTOR_FREE, ACTOR_FAILED,
    )
    bus.subscribe(ACTOR_ONLINE, _on_online)
    bus.subscribe(CAPABILITY_DECLARED, _on_capability)
    bus.subscribe(ACTOR_OFFLINE, _on_offline)
    bus.subscribe(ACTOR_BUSY, _on_busy)
    bus.subscribe(ACTOR_FREE, _on_free)
    bus.subscribe(ACTOR_FAILED, _on_failed)


def list_actors() -> list[dict]:
    with _lock:
        return [dict(r) for r in _roster.values()]
