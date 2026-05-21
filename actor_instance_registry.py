"""Actor instance registry — maps actor IDs to live instances for peer discovery.

After negotiation, actors discover their execution peers by querying this registry.
This decouples actors from hardcoded references while enabling direct peer signaling.

Populated by the test setup or orchestration layer that creates actors.
"""

import threading
from typing import Dict, Optional


class ActorInstanceRegistry:
    """Maps actor IDs to live actor instances for dynamic peer discovery."""

    def __init__(self):
        """Initialize empty actor instance registry."""
        self._lock = threading.Lock()
        self._actors: Dict[str, object] = {}

    def register(self, actor_id: str, actor_instance: object) -> None:
        """Register an actor instance by ID.

        Args:
            actor_id: Unique actor identifier
            actor_instance: Live actor object (AGVActor, ArmActor, etc.)
        """
        with self._lock:
            self._actors[actor_id] = actor_instance

    def get_actor(self, actor_id: str) -> Optional[object]:
        """Get actor instance by ID.

        Args:
            actor_id: Actor identifier

        Returns:
            Actor instance or None if not registered
        """
        with self._lock:
            return self._actors.get(actor_id)

    def get_all(self) -> Dict[str, object]:
        """Get all registered actor instances.

        Returns:
            Dict mapping actor_id to actor instance
        """
        with self._lock:
            return dict(self._actors)

    def unregister(self, actor_id: str) -> None:
        """Unregister an actor instance.

        Args:
            actor_id: Actor identifier
        """
        with self._lock:
            if actor_id in self._actors:
                del self._actors[actor_id]
