"""Tests for bounded retry on RedisStreamsBus.publish and .history.

Verifies that:
1. publish() raises BusUnavailable after _MAX_RETRIES attempts.
2. history() raises BusUnavailable after _MAX_RETRIES attempts.
3. An actor's event loop survives BusUnavailable — logs + continues, no crash.

Uses fakeredis and monkeypatching; no real Redis required.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import fakeredis
import pytest

from app.agents.bus import BusUnavailable, InProcessBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType
from app.agents.redis_bus import RedisStreamsBus, STREAM_KEY


def _make_bus(fake_server: fakeredis.FakeServer) -> RedisStreamsBus:
    """Construct a RedisStreamsBus with an injected fake Redis client."""
    fake_client = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
    bus = RedisStreamsBus.__new__(RedisStreamsBus)
    bus._redis_url = "redis://fake"
    bus._client = fake_client
    bus._subscribers = {}
    bus._subscribers_lock = threading.Lock()
    bus._seen_xids = {}
    bus._consumer_threads = []
    bus._stop_flag = threading.Event()
    return bus


class TestPublishBoundedRetry:
    def test_publish_raises_bus_unavailable_after_max_retries(self, monkeypatch):
        """publish() raises BusUnavailable when all retries fail (fast test)."""
        import redis as redis_lib
        import app.agents.redis_bus as rbus_mod

        fake_server = fakeredis.FakeServer()
        bus = _make_bus(fake_server)

        # Reduce retries and backoff so the test completes fast.
        monkeypatch.setattr(rbus_mod, "_MAX_RETRIES", 3)
        monkeypatch.setattr(rbus_mod, "_BACKOFF_INITIAL_S", 0.01)
        monkeypatch.setattr(rbus_mod, "_BACKOFF_MAX_S", 0.01)

        call_count = 0

        def always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise redis_lib.RedisError("connection refused")

        monkeypatch.setattr(bus._client, "xadd", always_fail)

        msg = Message(
            type=MessageType.BID,
            task_id="task-retry",
            negotiation_round=0,
            sender="arm-1",
            payload={},
        )

        start = time.time()
        with pytest.raises(BusUnavailable):
            bus.publish(msg)
        elapsed = time.time() - start

        assert call_count == 3, f"Expected 3 xadd calls, got {call_count}"
        assert elapsed < 1.0, f"Expected < 1s total wait, got {elapsed:.2f}s"


class TestHistoryBoundedRetry:
    def test_history_raises_bus_unavailable_after_max_retries(self, monkeypatch):
        """history() raises BusUnavailable when all retries fail (fast test)."""
        import redis as redis_lib
        import app.agents.redis_bus as rbus_mod

        fake_server = fakeredis.FakeServer()
        bus = _make_bus(fake_server)

        monkeypatch.setattr(rbus_mod, "_MAX_RETRIES", 3)
        monkeypatch.setattr(rbus_mod, "_BACKOFF_INITIAL_S", 0.01)
        monkeypatch.setattr(rbus_mod, "_BACKOFF_MAX_S", 0.01)

        call_count = 0

        def always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise redis_lib.RedisError("connection refused")

        monkeypatch.setattr(bus._client, "xrange", always_fail)

        start = time.time()
        with pytest.raises(BusUnavailable):
            bus.history()
        elapsed = time.time() - start

        assert call_count == 3, f"Expected 3 xrange calls, got {call_count}"
        assert elapsed < 1.0, f"Expected < 1s total wait, got {elapsed:.2f}s"


class TestActorLoopSurvivesBusUnavailable:
    def test_actor_loop_survives_bus_unavailable(self):
        """Agent event loop must not crash when bus.publish raises BusUnavailable.

        We stub the InProcessBus.publish to always raise BusUnavailable, then
        start a fresh ArmAgent and publish a TASK_ANNOUNCE. The agent should
        handle it without crashing the event-loop thread.
        """
        from app.agents.arm_agent import ArmAgent

        class AlwaysFailBus(InProcessBus):
            """Bus that raises BusUnavailable on every publish call."""

            def publish(self, message: Message) -> None:
                raise BusUnavailable("test: bus always unavailable")

        bus = AlwaysFailBus()
        profile = CapabilityProfile(
            actor_id="arm-test-unavailable",
            kind="arm",
            reachable_zones=["input_arm"],
        )
        agent = ArmAgent(profile, bus, heartbeat_interval=60.0)
        agent.start()

        # Give the heartbeat + event loop a moment to start.
        time.sleep(0.1)

        # Inject a TASK_ANNOUNCE directly into the inbox to exercise the handler.
        now = time.time()
        announce = Message(
            type=MessageType.TASK_ANNOUNCE,
            task_id="task-unavail",
            negotiation_round=0,
            sender="interpreter",
            payload={
                "source_zone": "input_arm",
                "dest_zone": "output_arm",
                "constraint": "",
                "slots": [
                    {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
                ],
                "announce_ts": now,
                "bid_deadline_ts": now + 0.1,
                "consensus_deadline_ts": now + 0.3,
            },
        )
        agent._inbox.put(announce)

        # Wait briefly and assert the thread is still alive (didn't crash).
        time.sleep(0.5)
        assert agent._thread is not None and agent._thread.is_alive(), (
            "Agent event-loop thread died after BusUnavailable"
        )

        agent.stop()
