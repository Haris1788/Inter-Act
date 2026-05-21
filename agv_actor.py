"""AGV actor — mobile agent for transportation tasks."""

from typing import List, Optional

from event import Event
from event_types import EventType
from base_actor import BaseActor
from app.agents.message_bus import MessageBus


class AGVActor(BaseActor):
    """Autonomous Guided Vehicle actor. Responds to proposals; no routing logic."""

    def __init__(
        self,
        actor_id: str,
        bus: MessageBus,
        can_reach: Optional[List[int]] = None,
        speed: float = 1.5,
        capacity: float = 100.0
    ):
        """Initialize AGV actor.

        Args:
            actor_id: Unique AGV identifier
            bus: MessageBus instance for pub/sub
            can_reach: List of node IDs this AGV can reach
            speed: Movement speed (units/sec)
            capacity: Load capacity (kg)
        """
        super().__init__(actor_id, "AGV", bus)
        self.can_reach = can_reach or []
        self.speed = speed
        self.capacity = capacity

    def capabilities(self) -> dict:
        """Return AGV capabilities.

        Returns:
            Dict with type, canReach, speed, capacity, status
        """
        return {
            "type": "AGV",
            "canReach": self.can_reach,
            "speed": self.speed,
            "capacity": self.capacity,
            "status": self.status
        }

    def startup(self) -> None:
        """Initialize AGV: publish capabilities and subscribe to events."""
        self.on_startup()
        self.subscribe_to([
            EventType.TASK_CREATED,
            EventType.PATH_PROPOSED,
            EventType.ACTOR_FAILED,
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
        elif event.type == EventType.ACTOR_FAILED:
            self._on_actor_failed(event)
        elif event.type == EventType.TASK_COMPLETED:
            self._on_task_completed(event)

    def _on_task_created(self, event: Event) -> None:
        """Evaluate task locally when created.

        Tasks are evaluated on creation to check capability constraints.
        No formal response yet — Interpreter will propose a path if suitable.
        """
        if self.status != "FREE":
            return

        task_id = event.task_id
        payload = event.payload
        required_nodes = payload.get("requiredNodes", [])

        if self._can_reach_all(required_nodes):
            # Log capability match locally; Interpreter will propose path
            pass

    def _on_path_proposed(self, event: Event) -> None:
        """Accept or ignore path proposal.

        Only respond if this AGV is included in the proposed path.
        """
        if self.status != "FREE":
            return

        payload = event.payload
        proposed_path = payload.get("path", [])

        if self.actor_id in proposed_path:
            self._accept_path(event.task_id, proposed_path)

    def _on_task_completed(self, event: Event) -> None:
        """Reset to FREE when task completes."""
        self._status = "FREE"

    def _on_actor_failed(self, event: Event) -> None:
        """Handle actor failure.

        If this AGV failed, reset state.
        If another actor failed, Interpreter will replan.
        """
        payload = event.payload
        failed_actor_id = payload.get("actorId")

        if failed_actor_id == self.actor_id:
            self._status = "FREE"

    def _can_reach_all(self, required_nodes: List[int]) -> bool:
        """Check if AGV can reach all required nodes.

        Args:
            required_nodes: List of node IDs required for the task

        Returns:
            True if all nodes are reachable
        """
        return all(node in self.can_reach for node in required_nodes)

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

    def on_arm_pickup_ready(self, task_id: str, arm_id: str) -> None:
        """Receive peer signal from Arm when pickup is ready.

        Called directly by ArmActor (not through bus) to signal that pickup is ready.
        AGV can now proceed to the arm's location to receive the load.

        Args:
            task_id: Task identifier for the pickup operation
            arm_id: ID of the arm signaling pickup readiness
        """
        pass
