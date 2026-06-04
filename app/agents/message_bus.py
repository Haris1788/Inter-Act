"""Message bus — event publication layer for the AI coordination stack.

SocketIOBus wraps socketio.emit so every agent can publish without knowing
about Flask. It also maintains an in-process subscriber registry so backend
components (CentralBrain, health_monitor) can react to events without going
through a WebSocket round-trip.

Topics used by the AI layer:
    task_spec       — interpreter output (TaskSpec dict)
    plan            — planner output (plan dict)
    agv_status      — AGV position/status change
    arm_status      — arm status change
    agv_fault       — AGV reported faulted (reassignment trigger)
    reassignment    — planner assigned a task to a replacement AGV
    model_swap      — llm_service swapping VRAM resident model
    notification    — user-visible alert
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List


class MessageBus:
    """Minimal pub/sub bus with in-process subscriptions + optional SocketIO emit."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: Dict[str, List[Callable]] = {}

    def subscribe(self, topic: str, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._subs.setdefault(topic, []).append(callback)

    def publish(self, topic: str, data: dict) -> None:
        """Emit to SocketIO clients then dispatch to in-process subscribers."""
        self._emit(topic, data)
        with self._lock:
            cbs = list(self._subs.get(topic, []))
        for cb in cbs:
            try:
                cb(data)
            except Exception:
                pass

    def _emit(self, topic: str, data: dict) -> None:
        raise NotImplementedError


class SocketIOBus(MessageBus):
    """Production bus: publishes over SocketIO + dispatches in-process."""

    def _emit(self, topic: str, data: dict) -> None:
        try:
            from app import socketio
            socketio.emit(topic, data)
        except Exception:
            pass


class NullBus(MessageBus):
    """Test/offline bus: in-process dispatch only, no SocketIO."""

    def _emit(self, topic: str, data: dict) -> None:
        pass


# ── module-level singleton (set by create_app) ────────────────────────────────
_bus: MessageBus | None = None


def get_bus() -> MessageBus:
    global _bus
    if _bus is None:
        _bus = SocketIOBus()
    return _bus


def set_bus(bus: MessageBus) -> None:
    global _bus
    _bus = bus
