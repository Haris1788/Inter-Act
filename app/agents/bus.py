"""Negotiation bus — typed pub/sub with a SocketIO mirror.

Replaces the old emit-only message_bus.py. Actors both publish and subscribe;
every message is also mirrored to the browser as a `bus_message` SocketIO
event so the UI shows real negotiation traffic.

The bus is deliberately dumb: it delivers messages and records history. It
makes no decisions. Subscribers (actor agents) keep their own inbox queues so
a slow or recursive subscriber cannot stall a publisher.

## Implementations

`NegotiationBus` / `InProcessBus` — in-process implementation (default).
  These two names refer to the same class for backward compatibility. All
  existing tests that do ``NegotiationBus()`` continue to work unchanged.

`RedisStreamsBus` — distributed transport backed by Redis Streams. Selected
  when ``INTERACT_BUS=redis`` is set; requires ``REDIS_URL``.

`get_bus()` returns the right implementation based on ``INTERACT_BUS``.

## Bus interface contract (four methods)

``publish(message)``      — record + deliver to all subscribers.
``history()``             — every published message in publish order (pure read).
``subscribe(type, cb)``   — register callback for one message type.
``subscribe_all(cb, consumer_id=None)`` — register callback for every type.

Implementations deliver and record; they do NOT decide consensus.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from typing import Callable, Optional

from app.agents.negotiation import Message, MessageType


# ── Exceptions ─────────────────────────────────────────────────────────────

class BusUnavailable(Exception):
    """Raised by RedisStreamsBus.publish / history when all retry attempts are
    exhausted and the bus remains unreachable.

    Callers (actor agent event loop) must catch this, log, and continue —
    never let it crash the agent thread.
    """


# ── InProcessBus (the canonical in-process implementation) ─────────────────

class NegotiationBus:
    """In-process typed pub/sub — RLock-guarded, list[Message] history, SocketIO
    mirror on every publish.

    This class doubles as the public interface name (`NegotiationBus`) for
    backward compatibility with all existing tests and callers that do
    ``NegotiationBus()``. The type annotation ``NegotiationBus`` in function
    signatures acts as the interface contract for type-checkers; both
    ``NegotiationBus`` and ``InProcessBus`` refer to this class.

    Thread-safe. No authority, no decisions. Subscriptions are permanent for
    the lifetime of the bus instance — there is no unsubscribe.

    ``consumer_id`` kwarg in ``subscribe_all`` is accepted for API parity with
    ``RedisStreamsBus`` but is ignored — in-process delivery is always
    exactly-once.
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

    def subscribe_all(
        self,
        callback: Callable[[Message], None],
        consumer_id: Optional[str] = None,
    ) -> None:
        """Register `callback` for every message type.

        `consumer_id` is accepted for API parity with `RedisStreamsBus` but
        ignored — in-process delivery is always exactly-once.

        Call once per agent — calling again with the same callback causes
        duplicate deliveries.
        """
        for msg_type in MessageType:
            self.subscribe(msg_type, callback)

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


# Backward-compatible alias: InProcessBus == NegotiationBus.
# New code should prefer `InProcessBus` to distinguish from `RedisStreamsBus`.
InProcessBus = NegotiationBus


# ── module-level singleton ─────────────────────────────────────────────────

_bus: Optional[NegotiationBus] = None
_bus_lock = threading.Lock()


def get_bus() -> NegotiationBus:
    """Return the process-wide negotiation bus, creating it on first use.

    Selects implementation from the ``INTERACT_BUS`` environment variable:
    - ``"inprocess"`` (default) → `InProcessBus` / `NegotiationBus`
    - ``"redis"``              → `RedisStreamsBus` (requires ``REDIS_URL``)

    Thread-safe: double-checked locking under ``_bus_lock``.
    """
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                mode = os.environ.get("INTERACT_BUS", "inprocess").lower().strip()
                if mode == "redis":
                    from app.agents.redis_bus import RedisStreamsBus

                    redis_url = os.environ["REDIS_URL"]
                    _bus = RedisStreamsBus(redis_url=redis_url)
                else:
                    _bus = InProcessBus()
    return _bus


def reset_bus() -> None:
    """Test hook — drop the singleton so each test gets a fresh bus.

    For `RedisStreamsBus`, also calls `shutdown()` to stop consumer threads
    before discarding the instance.
    """
    global _bus
    with _bus_lock:
        old = _bus
        _bus = None
    if old is not None and hasattr(old, "shutdown"):
        try:
            old.shutdown()
        except Exception:
            pass
