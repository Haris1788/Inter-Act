"""Actor registry — passive store of declared capabilities."""

import threading
from typing import Dict, List, Optional
from datetime import datetime

from event import Event
from event_types import EventType
from app.agents.message_bus import MessageBus


class ActorRegistry:
    """Passive registry that tracks actor capabilities via CAPABILITY_DECLARED events."""

    def __init__(self, bus: MessageBus):
        """Initialize registry and subscribe to capability announcements.

        Args:
            bus: MessageBus instance for subscribing to events
        """
        self.bus = bus
        self._lock = threading.Lock()
        self._actors: Dict[str, dict] = {}

        self.bus.subscribe(
            EventType.CAPABILITY_DECLARED.value,
            self._on_capability_declared
        )

    def _on_capability_declared(self, event_data) -> None:
        """Handle CAPABILITY_DECLARED events and update registry."""
        if isinstance(event_data, Event):
            payload = event_data.payload
        else:
            payload = event_data

        actor_id = payload.get("actorId")
        if not actor_id:
            return

        with self._lock:
            self._actors[actor_id] = {
                "actorId": actor_id,
                "actorType": payload.get("actorType"),
                "capabilities": payload.get("capabilities", {}),
                "status": payload.get("status", "FREE"),
                "last_seen": datetime.now().isoformat()
            }

    def get_all(self) -> Dict[str, dict]:
        """Return all registered actors.

        Returns:
            Dict mapping actorId to actor record
        """
        with self._lock:
            return dict(self._actors)

    def get_by_id(self, actor_id: str) -> Optional[dict]:
        """Return record for a single actor.

        Args:
            actor_id: Actor identifier

        Returns:
            Actor record or None if not found
        """
        with self._lock:
            return self._actors.get(actor_id)

    def get_free_actors(self) -> Dict[str, dict]:
        """Return all actors with status FREE.

        Returns:
            Dict of FREE actors by actorId
        """
        with self._lock:
            return {
                actor_id: record
                for actor_id, record in self._actors.items()
                if record.get("status") == "FREE"
            }
