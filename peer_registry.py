"""Peer registry — dynamic discovery of actor execution partners post-negotiation.

After PATH_ACCEPTED, actors discover their peers via this registry.
This decouples actors from hardcoded knowledge of each other.
"""

import threading
from typing import Dict, Optional


class PeerRegistry:
    """Tracks peer relationships for post-negotiation direct execution."""

    def __init__(self):
        """Initialize empty peer registry."""
        self._lock = threading.Lock()
        self._peers: Dict[str, Dict[str, Optional[object]]] = {}

    def register_peers(self, task_id: str, actor_pairs: Dict[str, object]) -> None:
        """Register peer references for a task after PATH_ACCEPTED.

        Args:
            task_id: Task identifier
            actor_pairs: Dict mapping actor_id to peer actor instance
                        Example: {"arm-1": agv_instance, "agv-1": arm_instance}
        """
        with self._lock:
            self._peers[task_id] = dict(actor_pairs)

    def get_peer(self, task_id: str, actor_id: str) -> Optional[object]:
        """Get peer actor instance for a given task and actor.

        Args:
            task_id: Task identifier
            actor_id: Actor looking for its peer

        Returns:
            Peer actor instance or None if not registered
        """
        with self._lock:
            if task_id not in self._peers:
                return None
            return self._peers[task_id].get(actor_id)

    def clear_task(self, task_id: str) -> None:
        """Clear peer registry for a completed task.

        Args:
            task_id: Task identifier to clean up
        """
        with self._lock:
            if task_id in self._peers:
                del self._peers[task_id]
