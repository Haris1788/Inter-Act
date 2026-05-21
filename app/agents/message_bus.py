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

import logging
import threading
from typing import Callable, Dict, List, Optional, Union, overload

from event import Event

log = logging.getLogger(__name__)

_init_lock = threading.Lock()


class MessageBus:
    """Minimal pub/sub bus with in-process subscriptions + optional SocketIO emit."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: Dict[str, List[dict]] = {}

    def subscribe(self, topic: str, callback: Callable[[dict], None], task_id: Optional[str] = None) -> None:
        """Subscribe to a topic with optional task_id filtering.

        If task_id is None, callback fires for all events on this topic.
        If task_id is set, callback only fires when event['taskId'] matches.
        """
        with self._lock:
            self._subs.setdefault(topic, []).append({"callback": callback, "task_id": task_id})

    def unsubscribe(self, topic: str, callback: Callable[[dict], None]) -> None:
        """Remove all subscriptions for this callback on the given topic."""
        with self._lock:
            if topic in self._subs:
                self._subs[topic] = [sub for sub in self._subs[topic] if sub["callback"] != callback]

    @overload
    def publish(self, event: Event) -> None:
        ...

    @overload
    def publish(self, topic: str, data: dict) -> None:
        ...

    def publish(self, event: Union[Event, str], data: dict | None = None) -> None:
        """Emit to SocketIO clients then dispatch to in-process subscribers.

        Accepts either:
        - Event object (preferred)
        - (topic: str, data: dict) tuple for backward compatibility
        """
        if isinstance(event, Event):
            evt = event
            topic = evt.type.value
        else:
            topic = event
            if data is None:
                raise ValueError("data required when topic is str")
            evt = None

        emit_data = evt if evt else data
        self._emit(topic, emit_data)

        with self._lock:
            subs = list(self._subs.get(topic, []))
            wildcard_subs = list(self._subs.get("*", []))

        if isinstance(emit_data, Event):
            event_task_id = emit_data.task_id
        else:
            event_task_id = emit_data.get("taskId")
        for sub in subs:
            callback = sub["callback"]
            filter_task_id = sub["task_id"]
            if filter_task_id is None or filter_task_id == event_task_id:
                try:
                    callback(emit_data)
                except Exception:
                    pass
        for sub in wildcard_subs:
            callback = sub["callback"]
            filter_task_id = sub["task_id"]
            if filter_task_id is None or filter_task_id == event_task_id:
                try:
                    callback(emit_data)
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
        with _init_lock:
            if _bus is None:
                _bus = SocketIOBus()
    return _bus


def set_bus(bus: MessageBus) -> None:
    global _bus
    _bus = bus
