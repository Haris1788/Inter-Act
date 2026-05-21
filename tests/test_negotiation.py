"""Happy path negotiation workflow test using NullBus."""

import pytest
import threading
import time
from typing import List, Dict

from app.agents.message_bus import NullBus
from event import Event
from event_types import EventType
from base_actor import BaseActor
from agv_actor import AGVActor
from arm_actor import ArmActor
from interpreter import Interpreter
from actor_registry import ActorRegistry


class EventCapture:
    """Helper to capture published events for verification."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events: List[Event] = []

    def capture(self, event_data) -> None:
        """Capture event (callback for bus subscription)."""
        with self._lock:
            if isinstance(event_data, Event):
                self._events.append(event_data)

    def get_events_by_type(self, event_type: EventType) -> List[Event]:
        """Get all captured events of a specific type."""
        with self._lock:
            return [e for e in self._events if e.type == event_type]

    def get_all(self) -> List[Event]:
        """Get all captured events."""
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        """Clear captured events."""
        with self._lock:
            self._events.clear()


class TestNegotiationHappyPath:
    """Test the happy path of actor negotiation and task completion."""

    @pytest.fixture
    def setup(self):
        """Set up test environment with bus, registry, interpreter, and actors."""
        bus = NullBus()
        registry = ActorRegistry(bus)
        interpreter = Interpreter("interp-1", bus, registry)

        # Event capture BEFORE startup
        capture = EventCapture()
        bus.subscribe("*", capture.capture)

        # Create actors
        agv1 = AGVActor("agv-1", bus, can_reach=[1, 2, 3], speed=1.5, capacity=100.0)
        agv2 = AGVActor("agv-2", bus, can_reach=[2, 3, 4], speed=1.5, capacity=100.0)
        arm1 = ArmActor("arm-1", bus, position=1, actions=["load", "unload"])
        arm2 = ArmActor("arm-2", bus, position=4, actions=["load", "unload"])

        # Connect arm to agv for peer signaling
        arm1.set_agv_instance(agv1)
        arm2.set_agv_instance(agv2)

        # Startup all actors to publish capabilities
        agv1.startup()
        agv2.startup()
        arm1.startup()
        arm2.startup()

        # Small delay to allow capability declarations to propagate
        time.sleep(0.1)

        return {
            "bus": bus,
            "registry": registry,
            "interpreter": interpreter,
            "agv1": agv1,
            "agv2": agv2,
            "arm1": arm1,
            "arm2": arm2,
            "capture": capture
        }

    def test_capabilities_declared(self, setup):
        """Step 1: Verify all actors declare capabilities on startup."""
        capture = setup["capture"]

        capability_events = capture.get_events_by_type(EventType.CAPABILITY_DECLARED)
        assert len(capability_events) == 4, f"Expected 4 CAPABILITY_DECLARED events, got {len(capability_events)}"

        # Verify each actor's capabilities
        actor_ids = {e.payload["actorId"] for e in capability_events}
        assert actor_ids == {"agv-1", "agv-2", "arm-1", "arm-2"}

        # Verify AGV capabilities
        agv_events = [e for e in capability_events if e.payload.get("actorType") == "AGV"]
        assert len(agv_events) == 2
        for event in agv_events:
            caps = event.payload["capabilities"]
            assert "canReach" in caps
            assert "speed" in caps
            assert "capacity" in caps

        # Verify Arm capabilities
        arm_events = [e for e in capability_events if e.payload.get("actorType") == "ARM"]
        assert len(arm_events) == 2
        for event in arm_events:
            caps = event.payload["capabilities"]
            assert "position" in caps
            assert "actions" in caps

    def test_registry_populated(self, setup):
        """Step 2: Verify registry captures all capabilities."""
        registry = setup["registry"]

        all_actors = registry.get_all()
        assert len(all_actors) == 4, f"Expected 4 actors in registry, got {len(all_actors)}"

        # Verify specific actors
        agv1 = registry.get_by_id("agv-1")
        assert agv1 is not None
        assert agv1["actorType"] == "AGV"
        assert 1 in agv1["capabilities"]["canReach"]

        arm1 = registry.get_by_id("arm-1")
        assert arm1 is not None
        assert arm1["actorType"] == "ARM"
        assert arm1["capabilities"]["position"] == 1

    def test_task_creation(self, setup):
        """Step 3: Interpreter creates a task."""
        interpreter = setup["interpreter"]
        capture = setup["capture"]
        capture.clear()

        task_spec = {
            "requiredNodes": [1, 2, 3],
            "requiredActors": ["agv-1", "arm-1"],
            "priority": "high"
        }
        task_id = interpreter.create_task(task_spec)

        assert task_id is not None
        assert isinstance(task_id, str)

        # Verify TASK_CREATED event published
        task_created_events = capture.get_events_by_type(EventType.TASK_CREATED)
        assert len(task_created_events) == 1
        assert task_created_events[0].task_id == task_id
        assert task_created_events[0].payload["taskId"] == task_id

    def test_path_proposal(self, setup):
        """Step 4: Interpreter proposes a path."""
        interpreter = setup["interpreter"]
        capture = setup["capture"]
        capture.clear()

        task_spec = {
            "requiredNodes": [1, 2, 3],
            "requiredActors": ["agv-1", "arm-1"],
            "priority": "high"
        }
        task_id = interpreter.create_task(task_spec)

        # Small delay for TASK_CREATED to propagate
        time.sleep(0.05)

        # Propose a path
        proposed_path = ["arm-1", "agv-1"]
        interpreter.propose_path(task_id, proposed_path)

        # Verify PATH_PROPOSED event
        path_proposed_events = capture.get_events_by_type(EventType.PATH_PROPOSED)
        assert len(path_proposed_events) == 1
        assert path_proposed_events[0].task_id == task_id
        assert set(path_proposed_events[0].payload["path"]) == set(proposed_path)

    def test_path_acceptance_and_busy_state(self, setup):
        """Step 5: Selected actors accept path and enter BUSY state."""
        interpreter = setup["interpreter"]
        agv1 = setup["agv1"]
        agv2 = setup["agv2"]
        arm1 = setup["arm1"]
        arm2 = setup["arm2"]
        capture = setup["capture"]
        capture.clear()

        # Verify all actors start FREE
        assert agv1.status == "FREE"
        assert agv2.status == "FREE"
        assert arm1.status == "FREE"
        assert arm2.status == "FREE"

        # Create task and propose path
        task_spec = {
            "requiredNodes": [1, 2, 3],
            "requiredActors": ["agv-1", "arm-1"],
            "priority": "high"
        }
        task_id = interpreter.create_task(task_spec)
        time.sleep(0.05)

        proposed_path = ["arm-1", "agv-1"]
        interpreter.propose_path(task_id, proposed_path)
        time.sleep(0.1)  # Allow path proposal to propagate

        # Verify selected actors accepted path
        path_accepted_events = capture.get_events_by_type(EventType.PATH_ACCEPTED)
        assert len(path_accepted_events) == 2, f"Expected 2 PATH_ACCEPTED, got {len(path_accepted_events)}"

        accepted_actor_ids = {e.payload["actorId"] for e in path_accepted_events}
        assert accepted_actor_ids == {"agv-1", "arm-1"}

        # Verify selected actors are BUSY
        assert agv1.status == "BUSY"
        assert arm1.status == "BUSY"

        # Verify non-selected actors remain FREE
        assert agv2.status == "FREE"
        assert arm2.status == "FREE"

    def test_interpreter_confirms_path(self, setup):
        """Step 6: Interpreter confirms path once all actors accept."""
        interpreter = setup["interpreter"]
        capture = setup["capture"]
        capture.clear()

        # Create task and propose path
        task_spec = {
            "requiredNodes": [1, 2, 3],
            "requiredActors": ["agv-1", "arm-1"],
            "priority": "high"
        }
        task_id = interpreter.create_task(task_spec)
        time.sleep(0.05)

        proposed_path = ["arm-1", "agv-1"]
        interpreter.propose_path(task_id, proposed_path)
        time.sleep(0.1)

        # Check interpreter state
        with interpreter._lock:
            pending_task = interpreter._pending_tasks.get(task_id)
            assert pending_task is not None
            # After both actors accept, status should be CONFIRMED
            expected_status = "CONFIRMED"
            actual_status = pending_task.get("status")
            assert actual_status == expected_status, \
                f"Expected task status {expected_status}, got {actual_status}"

    def test_task_completion_resets_actors(self, setup):
        """Step 7: Task completion resets all participating actors to FREE."""
        interpreter = setup["interpreter"]
        agv1 = setup["agv1"]
        agv2 = setup["agv2"]
        arm1 = setup["arm1"]
        arm2 = setup["arm2"]
        capture = setup["capture"]
        capture.clear()

        # Create task and complete negotiation
        task_spec = {
            "requiredNodes": [1, 2, 3],
            "requiredActors": ["agv-1", "arm-1"],
            "priority": "high"
        }
        task_id = interpreter.create_task(task_spec)
        time.sleep(0.05)

        proposed_path = ["arm-1", "agv-1"]
        interpreter.propose_path(task_id, proposed_path)
        time.sleep(0.1)

        # Verify actors are BUSY
        assert agv1.status == "BUSY"
        assert arm1.status == "BUSY"
        assert agv2.status == "FREE"
        assert arm2.status == "FREE"

        # Simulate task completion by publishing TASK_COMPLETED
        completion_event = Event(
            type=EventType.TASK_COMPLETED,
            sender="arm-1",
            task_id=task_id,
            payload={"taskId": task_id}
        )
        interpreter.bus.publish(completion_event)
        time.sleep(0.05)

        # Verify all participating actors reset to FREE
        assert agv1.status == "FREE"
        assert arm1.status == "FREE"
        assert agv2.status == "FREE"
        assert arm2.status == "FREE"

        # Verify TASK_COMPLETED was handled by interpreter
        with interpreter._lock:
            task = interpreter._pending_tasks.get(task_id)
            assert task is not None
            assert task["status"] == "COMPLETED"

    def test_peer_to_peer_signaling(self, setup):
        """Step 8: Arm signals AGV directly on path acceptance (peer-to-peer)."""
        arm1 = setup["arm1"]
        agv1 = setup["agv1"]

        # Track if on_arm_pickup_ready was called
        call_tracker = {"called": False, "task_id": None, "arm_id": None}

        original_method = agv1.on_arm_pickup_ready
        def tracked_method(task_id, arm_id):
            call_tracker["called"] = True
            call_tracker["task_id"] = task_id
            call_tracker["arm_id"] = arm_id
            return original_method(task_id, arm_id)

        agv1.on_arm_pickup_ready = tracked_method

        # Simulate arm signaling AGV directly
        task_id = "task-123"
        arm1._signal_agv_pickup_ready(task_id)

        # Verify AGV received the signal directly
        assert call_tracker["called"] is True
        assert call_tracker["task_id"] == task_id
        assert call_tracker["arm_id"] == "arm-1"

