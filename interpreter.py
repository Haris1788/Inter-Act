"""Interpreter — global reasoner for task negotiation and replanning."""

import threading
from typing import Dict, List, Optional
from uuid import uuid4

from event import Event
from event_types import EventType
from actor_registry import ActorRegistry
from app.agents.message_bus import MessageBus


class Interpreter:
    """Global reasoner that proposes plans and handles replanning via the message bus."""

    def __init__(self, interpreter_id: str, bus: MessageBus, registry: ActorRegistry):
        """Initialize Interpreter with identity and connections.

        Args:
            interpreter_id: Unique interpreter identifier
            bus: MessageBus instance for pub/sub
            registry: ActorRegistry instance for querying capabilities
        """
        self.interpreter_id = interpreter_id
        self.bus = bus
        self.registry = registry

        self._lock = threading.Lock()
        self._declared_actors: Dict[str, dict] = {}
        self._pending_tasks: Dict[str, dict] = {}
        self._accepted_paths: Dict[str, set] = {}

        self._subscribe_to_events()

    def _subscribe_to_events(self) -> None:
        """Subscribe to all events the Interpreter needs."""
        self.bus.subscribe(
            EventType.CAPABILITY_DECLARED.value,
            self._on_capability_declared
        )
        self.bus.subscribe(
            EventType.PATH_ACCEPTED.value,
            self._on_path_accepted
        )
        self.bus.subscribe(
            EventType.ACTOR_FAILED.value,
            self._on_actor_failed
        )
        self.bus.subscribe(
            EventType.REPLAN_REQUIRED.value,
            self._on_replan_required
        )
        self.bus.subscribe(
            EventType.TASK_COMPLETED.value,
            self._on_task_completed
        )

    def create_task(self, task_spec: dict) -> str:
        """Create a new task and publish TASK_CREATED event.

        Args:
            task_spec: Dictionary containing task specification
                      (requiredNodes, priority, deadline, etc.)

        Returns:
            task_id for the created task
        """
        task_id = str(uuid4())

        with self._lock:
            self._pending_tasks[task_id] = {
                "task_id": task_id,
                "spec": task_spec,
                "status": "CREATED",
                "required_actors": task_spec.get("requiredActors", []),
                "accepted_by": set()
            }

        event = Event(
            type=EventType.TASK_CREATED,
            sender=self.interpreter_id,
            task_id=task_id,
            payload={
                "taskId": task_id,
                "spec": task_spec
            }
        )
        self.bus.publish(event)

        return task_id

    def propose_path(self, task_id: str, path: List[str]) -> None:
        """Propose a path (sequence of actors) for a task.

        Args:
            task_id: Task identifier
            path: List of actor IDs in sequence for task execution
        """
        if not self._is_valid_path(task_id, path):
            return

        with self._lock:
            if task_id in self._pending_tasks:
                self._pending_tasks[task_id]["proposed_path"] = path
                self._pending_tasks[task_id]["required_actors"] = path
                self._accepted_paths[task_id] = set()

        event = Event(
            type=EventType.PATH_PROPOSED,
            sender=self.interpreter_id,
            task_id=task_id,
            payload={
                "taskId": task_id,
                "path": path
            }
        )
        self.bus.publish(event)

    def _on_capability_declared(self, event_data) -> None:
        """Handle CAPABILITY_DECLARED events from actors.

        When an actor declares capabilities, store the record.
        This is used later for replanning when finding replacements.
        """
        if isinstance(event_data, Event):
            payload = event_data.payload
        else:
            payload = event_data

        actor_id = payload.get("actorId")
        if not actor_id:
            return

        with self._lock:
            self._declared_actors[actor_id] = {
                "actorId": actor_id,
                "actorType": payload.get("actorType"),
                "capabilities": payload.get("capabilities", {}),
                "status": payload.get("status", "FREE")
            }

    def _on_path_accepted(self, event_data) -> None:
        """Handle PATH_ACCEPTED events from actors.

        When an actor accepts a proposed path, track it.
        Once all required actors accept, the path is confirmed.
        """
        if isinstance(event_data, Event):
            event = event_data
            payload = event_data.payload
        else:
            payload = event_data
            event = None

        task_id = payload.get("taskId")
        actor_id = payload.get("actorId")

        if not task_id or not actor_id:
            return

        with self._lock:
            if task_id not in self._accepted_paths:
                self._accepted_paths[task_id] = set()

            self._accepted_paths[task_id].add(actor_id)

            if task_id in self._pending_tasks:
                required = set(self._pending_tasks[task_id]["required_actors"])
                accepted = self._accepted_paths[task_id]

                if required and required.issubset(accepted):
                    self._pending_tasks[task_id]["status"] = "CONFIRMED"

    def _on_actor_failed(self, event_data) -> None:
        """Handle ACTOR_FAILED events.

        When an actor fails, trigger replanning by publishing REPLAN_REQUIRED.
        """
        if isinstance(event_data, Event):
            payload = event_data.payload
        else:
            payload = event_data

        task_id = payload.get("taskId")
        failed_actor_id = payload.get("actorId")

        if not task_id or not failed_actor_id:
            return

        with self._lock:
            if task_id in self._pending_tasks:
                self._pending_tasks[task_id]["status"] = "FAILED"
                self._pending_tasks[task_id]["failed_actor"] = failed_actor_id

        event = Event(
            type=EventType.REPLAN_REQUIRED,
            sender=self.interpreter_id,
            task_id=task_id,
            payload={
                "taskId": task_id,
                "failedActorId": failed_actor_id
            }
        )
        self.bus.publish(event)

    def _on_replan_required(self, event_data) -> None:
        """Handle REPLAN_REQUIRED events.

        Query registry for FREE replacement actors and propose new path.
        """
        if isinstance(event_data, Event):
            payload = event_data.payload
        else:
            payload = event_data

        task_id = payload.get("taskId")
        failed_actor_id = payload.get("failedActorId")

        if not task_id:
            return

        with self._lock:
            if task_id not in self._pending_tasks:
                return

            pending = self._pending_tasks[task_id]
            old_path = pending.get("proposed_path", pending.get("required_actors", []))

        free_actors = self.registry.get_free_actors()

        new_path = self._find_replacement_path(old_path, failed_actor_id, free_actors)

        if new_path:
            self.propose_path(task_id, new_path)

    def _on_task_completed(self, event_data) -> None:
        """Handle TASK_COMPLETED events.

        Clean up pending task and path tracking state.
        """
        if isinstance(event_data, Event):
            payload = event_data.payload
        else:
            payload = event_data

        task_id = payload.get("taskId")

        if not task_id:
            return

        with self._lock:
            if task_id in self._pending_tasks:
                self._pending_tasks[task_id]["status"] = "COMPLETED"

            if task_id in self._accepted_paths:
                del self._accepted_paths[task_id]

    def _is_valid_path(self, task_id: str, path: List[str]) -> bool:
        """Validate that a path contains only known actors.

        Args:
            task_id: Task identifier
            path: Proposed path

        Returns:
            True if all actors in path are registered and FREE
        """
        if not path:
            return False

        for actor_id in path:
            actor = self.registry.get_by_id(actor_id)
            if not actor:
                return False

        return True

    def _find_replacement_path(
        self,
        original_path: List[str],
        failed_actor_id: str,
        free_actors: Dict[str, dict]
    ) -> Optional[List[str]]:
        """Find a replacement path by substituting failed actor with a FREE one.

        Args:
            original_path: Original proposed path
            failed_actor_id: ID of the failed actor to replace
            free_actors: Dict of actors with status FREE

        Returns:
            New path with replacement, or None if no suitable replacement found
        """
        if failed_actor_id not in original_path:
            return original_path

        failed_index = original_path.index(failed_actor_id)
        failed_actor_type = self.registry.get_by_id(failed_actor_id)

        if not failed_actor_type:
            return None

        failed_type = failed_actor_type.get("actorType")

        for actor_id, actor_record in free_actors.items():
            if actor_id == failed_actor_id:
                continue

            if actor_record.get("actorType") != failed_type:
                continue

            new_path = original_path.copy()
            new_path[failed_index] = actor_id
            return new_path

        return None
