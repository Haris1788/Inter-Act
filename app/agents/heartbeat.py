"""Heartbeat helpers — pure functions over bus history.

These are the ONLY public API for dead-agent inference. They make no side
effects and consult no shared state. Callers pass ``bus.history()`` explicitly.

Heartbeats are OBSERVATIONAL ONLY. These helpers MUST NEVER be called from
any consensus-decision path (public_winner, slots_complete, accepted_actors,
_execution_valid, etc.). They exist for:
  - Stage 6 observability dashboards
  - Future audit/diagnostic tooling
  - Phase 5+ dead-agent recovery (not yet wired)
"""

from __future__ import annotations

import time
from typing import Optional

from app.agents.negotiation import Message, MessageType


def last_seen(history: list[Message], actor_id: str) -> Optional[float]:
    """Return the latest ``ts`` payload field from AVAILABILITY_UPDATE messages
    sent by ``actor_id``, or None if no heartbeat is present in history.

    Pure — no side effects. O(n) in history length.
    """
    latest: Optional[float] = None
    for msg in history:
        if msg.type != MessageType.AVAILABILITY_UPDATE:
            continue
        if msg.sender != actor_id:
            continue
        ts = msg.payload.get("ts")
        if ts is None:
            continue
        try:
            ts_f = float(ts)
        except (TypeError, ValueError):
            continue
        if latest is None or ts_f > latest:
            latest = ts_f
    return latest


def is_presumed_dead(
    history: list[Message],
    actor_id: str,
    threshold_s: float = 15.0,
    now: Optional[float] = None,
) -> bool:
    """Return True when the actor's last heartbeat is older than ``threshold_s``
    seconds, or when no heartbeat exists at all.

    ``now`` defaults to ``time.time()``. Pass a fixed value in tests to avoid
    real-time dependency.

    Pure — no side effects. Never consulted for consensus decisions.
    """
    if now is None:
        now = time.time()
    ts = last_seen(history, actor_id)
    if ts is None:
        return True  # never seen → presumed dead
    return (now - ts) > threshold_s
