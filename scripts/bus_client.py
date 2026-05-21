"""Minimal bus client for actor containers.

Phase 2 (this PR): publish() POSTs to backend's /bus/publish.
                   subscribe() is a no-op stub until Phase 1's SSE lands.

Phase 1 (Purvil's PR): subscribe() opens an SSE stream to /bus/subscribe and
                       dispatches incoming envelopes to registered handlers.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

import requests

DEFAULT_HOST: str = os.environ.get("SERVER_HOST", "http://backend:5000")

_handlers: dict[str, list[Callable[[dict], None]]] = {}
_lock = threading.Lock()


def publish(envelope: dict, timeout: float = 2.0) -> bool:
    """POST envelope to backend /bus/publish. Returns True on 2xx, False otherwise."""
    try:
        r = requests.post(
            f"{DEFAULT_HOST}/bus/publish",
            json=envelope,
            timeout=timeout,
        )
        return 200 <= r.status_code < 300
    except requests.exceptions.RequestException:
        return False


def subscribe(event_type: str, handler: Callable[[dict], None]) -> None:
    """Register handler for an event_type. (Phase-1 SSE wiring TBD.)"""
    with _lock:
        _handlers.setdefault(event_type, []).append(handler)
