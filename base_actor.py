"""Abstract base class for all actors in the coordination system."""

from abc import ABC, abstractmethod
from typing import List

from event import Event
from event_types import EventType
from app.agents.message_bus import MessageBus


class BaseActor(ABC):
    """Base class for all actors (AGV, Arm, etc.)."""

    def __init__(self, actor_id: str, actor_type: str, bus: MessageBus):
        """Initialize actor with identity and bus connection.

        Args:
            actor_id: Unique actor identifier
            actor_type: Type of actor (e.g., 'AGV', 'ARM')
            bus: MessageBus instance for pub/sub
        """
        self.actor_id = actor_id
        self.actor_type = actor_type
        self.bus = bus
        self._status = "FREE"

    @property
    def status(self) -> str:
        """Current actor status: FREE or BUSY."""
        return self._status

    @abstractmethod
    def capabilities(self) -> dict:
        """Return actor's capabilities dictionary.

        Subclasses must implement. Example:
            return {
                "type": "AGV",
                "canReach": [1, 2, 3],
                "speed": 1.5,
                "capacity": 100
            }
        """
        pass

    def on_startup(self) -> None:
        """Publish CAPABILITY_DECLARED event on startup."""
        event = Event(
            type=EventType.CAPABILITY_DECLARED,
            sender=self.actor_id,
            task_id="",
            payload={
                "actorId": self.actor_id,
                "actorType": self.actor_type,
                "capabilities": self.capabilities(),
                "status": self.status
            }
        )
        self.bus.publish(event)

    def subscribe_to(self, event_types: List[EventType], task_id: str = None) -> None:
        """Helper to subscribe to multiple event types at once.

        Args:
            event_types: List of EventType enums to subscribe to
            task_id: Optional task_id filter (None = all events)
        """
        for event_type in event_types:
            self.bus.subscribe(
                event_type.value,
                self._handle_event,
                task_id=task_id
            )

    def _handle_event(self, event_data) -> None:
        """Internal dispatch to event handlers. Subclasses override as needed."""
        pass
