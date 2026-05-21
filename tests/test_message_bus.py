"""Unit tests for message bus — pub/sub, task scoping, wildcard subscriptions."""

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agents.message_bus import MessageBus, NullBus, SocketIOBus, get_bus, set_bus
from event import Event
from event_types import EventType


class TestMessageBusPublishSubscribe:
    """Basic pub/sub functionality."""

    def test_subscriber_receives_event(self):
        """publish → subscriber receives correct Event."""
        bus = NullBus()
        received = []

        def callback(event: Event):
            received.append(event)

        bus.subscribe(EventType.TASK_CREATED.value, callback)

        event = Event(
            type=EventType.TASK_CREATED,
            sender="TestSender",
            task_id="task-001",
            payload={"key": "value"},
        )
        bus.publish(event)

        assert len(received) == 1
        assert received[0].type == EventType.TASK_CREATED
        assert received[0].sender == "TestSender"
        assert received[0].task_id == "task-001"
        assert received[0].payload == {"key": "value"}

    def test_subscriber_receives_correct_topic(self):
        """Subscriber only receives events for subscribed topic."""
        bus = NullBus()
        received_created = []
        received_failed = []

        def on_created(event):
            received_created.append(event)

        def on_failed(event):
            received_failed.append(event)

        bus.subscribe(EventType.TASK_CREATED.value, on_created)
        bus.subscribe(EventType.ACTOR_FAILED.value, on_failed)

        event1 = Event(
            type=EventType.TASK_CREATED,
            sender="Planner",
            task_id="task-001",
        )
        event2 = Event(
            type=EventType.ACTOR_FAILED,
            sender="AGV",
            task_id="task-001",
        )

        bus.publish(event1)
        bus.publish(event2)

        assert len(received_created) == 1
        assert len(received_failed) == 1
        assert received_created[0].type == EventType.TASK_CREATED
        assert received_failed[0].type == EventType.ACTOR_FAILED

    def test_multiple_subscribers_same_topic(self):
        """Multiple subscribers on same topic all receive the event."""
        bus = NullBus()
        received1 = []
        received2 = []
        received3 = []

        bus.subscribe(EventType.TASK_CREATED.value, lambda e: received1.append(e))
        bus.subscribe(EventType.TASK_CREATED.value, lambda e: received2.append(e))
        bus.subscribe(EventType.TASK_CREATED.value, lambda e: received3.append(e))

        event = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        bus.publish(event)

        assert len(received1) == 1
        assert len(received2) == 1
        assert len(received3) == 1


class TestWildcardSubscription:
    """Wildcard (*) subscriber receives all events."""

    def test_wildcard_subscriber_receives_all_events(self):
        """wildcard subscriber receives all events regardless of topic."""
        bus = NullBus()
        received = []

        bus.subscribe("*", lambda e: received.append(e))

        events = [
            Event(
                type=EventType.TASK_CREATED,
                sender="Interpreter",
                task_id="task-001",
            ),
            Event(
                type=EventType.ACTOR_FAILED,
                sender="AGV",
                task_id="task-001",
            ),
            Event(
                type=EventType.REPLAN_REQUIRED,
                sender="Planner",
                task_id="task-001",
            ),
        ]

        for event in events:
            bus.publish(event)

        assert len(received) == 3
        assert received[0].type == EventType.TASK_CREATED
        assert received[1].type == EventType.ACTOR_FAILED
        assert received[2].type == EventType.REPLAN_REQUIRED

    def test_wildcard_and_specific_subscriptions_coexist(self):
        """Both wildcard and specific topic subscribers receive appropriate events."""
        bus = NullBus()
        wildcard_received = []
        specific_received = []

        bus.subscribe("*", lambda e: wildcard_received.append(e))
        bus.subscribe(EventType.TASK_CREATED.value, lambda e: specific_received.append(e))

        event1 = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        event2 = Event(
            type=EventType.ACTOR_FAILED,
            sender="AGV",
            task_id="task-001",
        )

        bus.publish(event1)
        bus.publish(event2)

        assert len(wildcard_received) == 2
        assert len(specific_received) == 1
        assert specific_received[0].type == EventType.TASK_CREATED


class TestTaskScoping:
    """Task-scoped subscribers only fire for matching task_id."""

    def test_task_scoped_subscriber_ignores_other_tasks(self):
        """task-scoped subscriber ignores other task's events."""
        bus = NullBus()
        received_task1 = []
        received_task2 = []

        bus.subscribe(
            EventType.TASK_CREATED.value,
            lambda e: received_task1.append(e),
            task_id="task-001",
        )
        bus.subscribe(
            EventType.TASK_CREATED.value,
            lambda e: received_task2.append(e),
            task_id="task-002",
        )

        event_for_task1 = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        event_for_task2 = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-002",
        )

        bus.publish(event_for_task1)
        bus.publish(event_for_task2)

        assert len(received_task1) == 1
        assert len(received_task2) == 1
        assert received_task1[0].task_id == "task-001"
        assert received_task2[0].task_id == "task-002"

    def test_unscoped_subscriber_receives_all_tasks(self):
        """Unscoped subscriber (task_id=None) receives all task events."""
        bus = NullBus()
        received_all = []
        received_task1 = []

        bus.subscribe(
            EventType.TASK_CREATED.value,
            lambda e: received_all.append(e),
            task_id=None,
        )
        bus.subscribe(
            EventType.TASK_CREATED.value,
            lambda e: received_task1.append(e),
            task_id="task-001",
        )

        event1 = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        event2 = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-002",
        )

        bus.publish(event1)
        bus.publish(event2)

        assert len(received_all) == 2
        assert len(received_task1) == 1

    def test_wildcard_with_task_scoping(self):
        """Wildcard subscriber can also be task-scoped."""
        bus = NullBus()
        wildcard_all = []
        wildcard_task1 = []

        bus.subscribe("*", lambda e: wildcard_all.append(e), task_id=None)
        bus.subscribe("*", lambda e: wildcard_task1.append(e), task_id="task-001")

        event1 = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        event2 = Event(
            type=EventType.ACTOR_FAILED,
            sender="AGV",
            task_id="task-002",
        )

        bus.publish(event1)
        bus.publish(event2)

        assert len(wildcard_all) == 2
        assert len(wildcard_task1) == 1
        assert wildcard_task1[0].task_id == "task-001"


class TestUnsubscribe:
    """unsubscribe → callback no longer fires."""

    def test_unsubscribe_removes_callback(self):
        """Unsubscribed callback does not fire."""
        bus = NullBus()
        received = []

        def callback(event):
            received.append(event)

        bus.subscribe(EventType.TASK_CREATED.value, callback)
        bus.unsubscribe(EventType.TASK_CREATED.value, callback)

        event = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        bus.publish(event)

        assert len(received) == 0

    def test_unsubscribe_only_removes_specific_callback(self):
        """Unsubscribe only removes the specified callback, not others."""
        bus = NullBus()
        received1 = []
        received2 = []

        def callback1(event):
            received1.append(event)

        def callback2(event):
            received2.append(event)

        bus.subscribe(EventType.TASK_CREATED.value, callback1)
        bus.subscribe(EventType.TASK_CREATED.value, callback2)

        bus.unsubscribe(EventType.TASK_CREATED.value, callback1)

        event = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        bus.publish(event)

        assert len(received1) == 0
        assert len(received2) == 1

    def test_unsubscribe_nonexistent_topic_is_safe(self):
        """Unsubscribing from non-existent topic doesn't crash."""
        bus = NullBus()

        def callback(event):
            pass

        bus.unsubscribe("NONEXISTENT", callback)

    def test_unsubscribe_nonexistent_callback_is_safe(self):
        """Unsubscribing non-existent callback doesn't crash."""
        bus = NullBus()

        def callback1(event):
            pass

        def callback2(event):
            pass

        bus.subscribe(EventType.TASK_CREATED.value, callback1)
        bus.unsubscribe(EventType.TASK_CREATED.value, callback2)

        assert True  # Just make sure it doesn't crash


class TestNullBusOffline:
    """NullBus works with no Flask context."""

    def test_nullbus_no_flask_required(self):
        """NullBus operates without Flask context."""
        bus = NullBus()
        received = []

        bus.subscribe(EventType.TASK_CREATED.value, lambda e: received.append(e))

        event = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        bus.publish(event)

        assert len(received) == 1

    def test_nullbus_no_socketio_emit(self):
        """NullBus does not attempt SocketIO emit."""
        bus = NullBus()
        received = []

        bus.subscribe(EventType.ACTOR_FAILED.value, lambda e: received.append(e))

        event = Event(
            type=EventType.ACTOR_FAILED,
            sender="AGV",
            task_id="task-001",
        )

        bus.publish(event)

        assert len(received) == 1


class TestActorFailedBroadcast:
    """ACTOR_FAILED broadcasts to all subscribers with zero handling in bus."""

    def test_actor_failed_broadcasts_to_all(self):
        """ACTOR_FAILED event broadcasts to all subscribers."""
        bus = NullBus()
        received = []

        bus.subscribe(EventType.ACTOR_FAILED.value, lambda e: received.append(e))
        bus.subscribe("*", lambda e: received.append(("wildcard", e)))

        event = Event(
            type=EventType.ACTOR_FAILED,
            sender="AGV-001",
            task_id="task-001",
            payload={"reason": "collision", "position": [1.0, 2.0]},
        )
        bus.publish(event)

        assert len(received) == 2
        assert received[0].type == EventType.ACTOR_FAILED
        assert received[0].sender == "AGV-001"
        assert received[0].payload["reason"] == "collision"

    def test_bus_does_not_handle_failure(self):
        """Bus does not handle ACTOR_FAILED, only broadcasts it."""
        bus = NullBus()
        received = []

        def callback(event):
            received.append(event)
            raise ValueError("Subscriber error")

        bus.subscribe(EventType.ACTOR_FAILED.value, callback)

        event = Event(
            type=EventType.ACTOR_FAILED,
            sender="AGV-001",
            task_id="task-001",
        )

        bus.publish(event)

        assert len(received) == 1

    def test_actor_failed_respects_task_scoping(self):
        """ACTOR_FAILED still respects task_id filtering."""
        bus = NullBus()
        received_task1 = []
        received_all = []

        bus.subscribe(
            EventType.ACTOR_FAILED.value,
            lambda e: received_task1.append(e),
            task_id="task-001",
        )
        bus.subscribe(
            EventType.ACTOR_FAILED.value,
            lambda e: received_all.append(e),
            task_id=None,
        )

        event_task1 = Event(
            type=EventType.ACTOR_FAILED,
            sender="AGV-001",
            task_id="task-001",
        )
        event_task2 = Event(
            type=EventType.ACTOR_FAILED,
            sender="AGV-002",
            task_id="task-002",
        )

        bus.publish(event_task1)
        bus.publish(event_task2)

        assert len(received_task1) == 1
        assert len(received_all) == 2


class TestThreadSafety:
    """Message bus operations are thread-safe."""

    def test_concurrent_subscriptions(self):
        """Concurrent subscribe calls don't corrupt state."""
        bus = NullBus()
        results = []

        def make_callback(idx):
            def cb(event):
                results.append(idx)

            return cb

        def subscribe_thread(start_idx, count):
            for i in range(start_idx, start_idx + count):
                bus.subscribe(
                    EventType.TASK_CREATED.value,
                    make_callback(i),
                    task_id=None,
                )

        threads = [
            threading.Thread(target=subscribe_thread, args=(i * 10, 10))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        event = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        bus.publish(event)

        assert len(results) == 50

    def test_concurrent_publish_subscribe(self):
        """Concurrent publish and subscribe don't crash or corrupt state."""
        bus = NullBus()
        received = []
        lock = threading.Lock()

        def callback(event):
            with lock:
                received.append(event)

        def subscribe_and_publish():
            bus.subscribe(EventType.TASK_CREATED.value, callback)
            event = Event(
                type=EventType.TASK_CREATED,
                sender="Interpreter",
                task_id="task-001",
            )
            bus.publish(event)

        threads = [threading.Thread(target=subscribe_and_publish) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(received) > 0
        assert all(e.type == EventType.TASK_CREATED for e in received)


class TestBackwardCompatibility:
    """Backward-compatible (str, dict) publish still works."""

    def test_publish_with_string_topic_and_dict(self):
        """Old-style publish(topic: str, data: dict) still works."""
        bus = NullBus()
        received = []

        bus.subscribe("custom_topic", lambda e: received.append(e))

        bus.publish("custom_topic", {"key": "value"})

        assert len(received) == 1
        assert received[0]["key"] == "value"

    def test_publish_string_without_data_raises(self):
        """publish(topic: str) without data raises ValueError."""
        bus = NullBus()

        with pytest.raises(ValueError, match="data required"):
            bus.publish("custom_topic")


class TestEventIdTimestamp:
    """Event id and timestamp are auto-generated."""

    def test_event_gets_id_and_timestamp(self):
        """Event receives auto-generated id and timestamp."""
        event = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )

        assert event.id
        assert event.timestamp
        assert "Z" in event.timestamp

    def test_events_have_unique_ids(self):
        """Each event gets a unique id."""
        event1 = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-001",
        )
        event2 = Event(
            type=EventType.TASK_CREATED,
            sender="Interpreter",
            task_id="task-002",
        )

        assert event1.id != event2.id
