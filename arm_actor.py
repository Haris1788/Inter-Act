"""Arm actor — robotic arm for load/unload operations."""

from typing import List, Optional

from event import Event
from event_types import EventType
from base_actor import BaseActor
from app.agents.message_bus import MessageBus


class ArmActor(BaseActor):
    """Robotic Arm actor. Accepts proposals and signals AGV directly after acceptance."""

    def __init__(
        self,
        actor_id: str,
        bus: MessageBus,
        position: int = 0,
        actions: Optional[List[str]] = None
    ):
        """Initialize Arm actor.

        Args:
            actor_id: Unique arm identifier
            bus: MessageBus instance for pub/sub
            position: Node ID where this arm is located
            actions: List of actions (e.g., ['load', 'unload'])
        """
        super().__init__(actor_id, "ARM", bus)
        self.position = position
        self.actions = actions or ["load", "unload"]
        self._agv_instance = None

    def capabilities(self) -> dict:
        """Return Arm capabilities.

        Returns:
            Dict with type, position, actions, status
        """
        return {
            "type": "ARM",
            "position": self.position,
            "actions": self.actions,
            "status": self.status
        }

    def set_agv_instance(self, agv_instance) -> None:
        """Store reference to AGV for direct peer signaling (not through bus).

        Args:
            agv_instance: AGVActor instance to signal directly
        """
        self._agv_instance = agv_instance

    def startup(self) -> None:
        """Initialize Arm: publish capabilities and subscribe to events."""
        self.on_startup()
        self.subscribe_to([
            EventType.TASK_CREATED,
            EventType.PATH_PROPOSED,
            EventType.TASK_COMPLETED
        ])

    def _handle_event(self, event_data) -> None:
        """Dispatch events to appropriate handlers."""
        if isinstance(event_data, Event):
            event = event_data
        else:
            event = Event(**event_data) if isinstance(event_data, dict) else None

        if not event:
            return

        if event.type == EventType.TASK_CREATED:
            self._on_task_created(event)
        elif event.type == EventType.PATH_PROPOSED:
            self._on_path_proposed(event)
        elif event.type == EventType.TASK_COMPLETED:
            self._on_task_completed(event)

    def _on_task_created(self, event: Event) -> None:
        """Evaluate task locally when created.

        Tasks are evaluated on creation to check capability constraints.
        No formal response yet — Interpreter will propose a path if suitable.
        """
        if self.status != "FREE":
            return

    def _on_path_proposed(self, event: Event) -> None:
        """Accept or ignore path proposal.

        Only respond if this Arm is included in the proposed path.
        After acceptance, signal AGV directly (not through bus).
        """
        if self.status != "FREE":
            return

        payload = event.payload
        proposed_path = payload.get("path", [])

        if self.actor_id in proposed_path:
            self._accept_path(event.task_id, proposed_path)
            self._signal_agv_pickup_ready(event.task_id)

    def _on_task_completed(self, event: Event) -> None:
        """Reset to FREE when task completes."""
        self._status = "FREE"

    def _accept_path(self, task_id: str, path: List[str]) -> None:
        """Accept path proposal and enter BUSY state.

        Args:
            task_id: Task identifier
            path: Proposed path with actor IDs
        """
        self._status = "BUSY"

        event = Event(
            type=EventType.PATH_ACCEPTED,
            sender=self.actor_id,
            task_id=task_id,
            payload={
                "actorId": self.actor_id,
                "taskId": task_id,
                "path": path
            }
        )
        self.bus.publish(event)

    def _signal_agv_pickup_ready(self, task_id: str) -> None:
        """Signal AGV directly (peer-to-peer) that pickup is ready.

        This bypasses the bus entirely — direct method call to AGV instance.
        Arm must have AGV instance set via set_agv_instance() first.

        Args:
            task_id: Task identifier for the pickup operation
        """
        if self._agv_instance is None:
            return

        self._agv_instance.on_arm_pickup_ready(task_id, self.actor_id)

    def on_arm_unload_complete(self, task_id: str) -> None:
        """Receive peer signal from AGV when unload is complete.

        Called directly by AGV (not through bus) to signal operation completion.

        Args:
            task_id: Task identifier for the unload operation
        """
        pass
