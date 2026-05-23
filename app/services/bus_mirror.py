"""SocketIO mirror consumer for the Redis-backed NegotiationBus.

When ``INTERACT_BUS=redis``, the ``InProcessBus._mirror()`` method is NOT
called (it lives on `InProcessBus`, not `RedisStreamsBus`). Instead, this
module provides a single background consumer subscribed as
``"__socketio_mirror__"`` that re-emits each delivered `Message` as the
existing ``bus_message`` SocketIO event.

This is the ONE place backend re-emits the Redis bus to the browser. Do not
also emit from ``RedisStreamsBus.publish()`` — that would double-deliver.

Usage (from ``app/__init__.py``, after ``start_agents()``):

    from app.services.bus_mirror import start_mirror
    start_mirror(bus)

``start_mirror()`` is idempotent — calling it a second time with the same bus
instance is a no-op.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_mirror_started = False


def start_mirror(bus) -> None:
    """Subscribe ``"__socketio_mirror__"`` to `bus` and re-emit each message
    as a ``bus_message`` SocketIO event.

    `bus` must be a `NegotiationBus` instance (expected: `RedisStreamsBus`).
    Importing `socketio` lazily so this module is importable without a Flask app.

    Idempotent — safe to call multiple times (only starts once).
    """
    global _mirror_started
    if _mirror_started:
        return
    _mirror_started = True

    def _emit(message) -> None:
        """Emit one bus message to all connected browser clients. Best-effort."""
        try:
            from app import socketio

            socketio.emit("bus_message", message.to_dict())
        except Exception:
            pass  # no Flask app or SocketIO not serving

    bus.subscribe_all(_emit, consumer_id="__socketio_mirror__")
    logger.info("bus_mirror: Redis SocketIO mirror consumer started")


def reset_mirror() -> None:
    """Test hook — reset the started flag so the mirror can be started again."""
    global _mirror_started
    _mirror_started = False
