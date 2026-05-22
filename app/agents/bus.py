"""Negotiation bus — typed in-process pub/sub with a SocketIO mirror.

Replaces the old emit-only message_bus.py. Actors both publish and subscribe;
every message is also mirrored to the browser as a `bus_message` SocketIO
event so the UI shows real negotiation traffic.

The bus is deliberately dumb: it delivers messages and records history. It
makes no decisions. Subscribers (actor agents, Stage 2) keep their own inbox
queues so a slow or recursive subscriber cannot stall a publisher.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable

from app.agents.negotiation import Message, MessageType


class NegotiationBus:
    """Minimal typed pub/sub. Thread-safe. No authority, no decisions.

    Subscriptions are permanent for the lifetime of the bus instance — there
    is no unsubscribe.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: dict[MessageType, list[Callable[[Message], None]]] = (
            defaultdict(list)
        )
        self._history: list[Message] = []

    def subscribe(
        self, msg_type: MessageType, callback: Callable[[Message], None]
    ) -> None:
        """Register `callback` to receive every message of `msg_type`."""
        with self._lock:
            self._subs[msg_type].append(callback)

    def publish(self, message: Message) -> None:
        """Record, mirror to the UI, then dispatch to in-process subscribers.

        A subscriber that raises is isolated — it never breaks delivery to the
        others or to the publisher.
        """
        with self._lock:
            self._history.append(message)
            callbacks = list(self._subs.get(message.type, []))
        self._mirror(message)
        for cb in callbacks:
            try:
                cb(message)
            except Exception:
                pass

    def history(self) -> list[Message]:
        """Every message published, in order. Used by tests and by actors
        rejoining after a fault to derive the current round."""
        with self._lock:
            return list(self._history)

    def _mirror(self, message: Message) -> None:
        """Best-effort SocketIO mirror for the browser bus panel. Imported
        lazily so the bus is testable with no Flask app. Never raises."""
        try:
            from app import socketio

            socketio.emit("bus_message", message.to_dict())
        except Exception:
            pass  # no Flask app / SocketIO not serving — mirror is best-effort


# ── module-level singleton ────────────────────────────────────────────────
_bus: NegotiationBus | None = None
_bus_lock = threading.Lock()


def get_bus() -> NegotiationBus:
    """Return the process-wide negotiation bus, creating it on first use.

    Thread-safe: Stage 2 wires multiple actor threads to this singleton.
    """
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = NegotiationBus()
    return _bus


def reset_bus() -> None:
    """Test hook — drop the singleton so each test gets a fresh bus."""
    global _bus
    _bus = None
