"""Bus contract test suite — parametrized over InProcessBus and RedisStreamsBus.

Every test in this module runs against BOTH implementations so we prove semantic
parity. `RedisStreamsBus` is exercised against a `fakeredis` server so CI needs
no real Redis container.

Contract being verified:
1. publish → history reflects it in order.
2. Publish N messages → all delivered to a subscribe_all callback within 500 ms.
3. Multiple subscribers (different consumer_ids) each get every message.
4. Duplicate XID (simulated redelivery) → callback invoked once per unique XID.
5. A subscriber that raises does not block delivery to others.
6. history is complete after consumer drop+recreate (history = XRANGE - + = full).
7. subscribe (type-filtered) only delivers the requested type.
8. shutdown() stops consumer threads cleanly.
"""

from __future__ import annotations

import threading
import time
from typing import List

import fakeredis
import pytest

from app.agents.bus import InProcessBus
from app.agents.negotiation import Message, MessageType

# NegotiationBus is an alias for InProcessBus; use InProcessBus explicitly.
NegotiationBus = InProcessBus


# ── Helpers ────────────────────────────────────────────────────────────────

def _msg(sender: str = "agent-1", msg_type: MessageType = MessageType.BID) -> Message:
    return Message(
        type=msg_type,
        task_id="task-contract",
        negotiation_round=0,
        sender=sender,
        payload={},
    )


def _wait_for(condition, timeout: float = 1.0, poll: float = 0.02) -> bool:
    """Poll `condition` until True or `timeout` seconds elapsed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(poll)
    return condition()


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture()
def inprocess_bus():
    """Fresh InProcessBus for each test."""
    return InProcessBus()


@pytest.fixture()
def redis_bus():
    """Fresh RedisStreamsBus backed by a fake Redis server for each test."""
    from app.agents.redis_bus import RedisStreamsBus

    fake_server = fakeredis.FakeServer()
    fake_client = fakeredis.FakeRedis(server=fake_server, decode_responses=True)

    bus = RedisStreamsBus.__new__(RedisStreamsBus)
    # Initialise internals manually so we inject the fake client.
    from collections import OrderedDict
    import threading as _threading
    bus._redis_url = "redis://fake"
    bus._client = fake_client
    bus._subscribers = {}
    bus._subscribers_lock = _threading.Lock()
    bus._seen_xids = {}
    bus._consumer_threads = []
    bus._stop_flag = _threading.Event()

    yield bus

    bus.shutdown()


# Parametrize: each test runs with both bus fixtures.
# We use indirect parametrize to reuse the fixtures above.
@pytest.fixture(params=["inprocess", "redis"])
def bus(request, inprocess_bus, redis_bus) -> NegotiationBus:
    if request.param == "inprocess":
        return inprocess_bus
    return redis_bus


# ── Contract tests ─────────────────────────────────────────────────────────

class TestPublishHistory:
    """Contract 1: publish → history reflects it in order."""

    def test_empty_history(self, bus):
        assert bus.history() == []

    def test_single_publish_appears_in_history(self, bus):
        m = _msg()
        bus.publish(m)
        history = bus.history()
        assert len(history) == 1
        assert history[0].sender == m.sender
        assert history[0].type == m.type

    def test_multiple_publishes_ordered(self, bus):
        msgs = [_msg(sender=f"agent-{i}") for i in range(5)]
        for m in msgs:
            bus.publish(m)
        history = bus.history()
        assert len(history) == 5
        for i, m in enumerate(msgs):
            assert history[i].sender == m.sender

    def test_history_is_independent_snapshot(self, bus):
        m1 = _msg(sender="a")
        bus.publish(m1)
        snap1 = bus.history()
        m2 = _msg(sender="b")
        bus.publish(m2)
        snap2 = bus.history()
        # snap1 must not grow after the second publish
        assert len(snap1) == 1
        assert len(snap2) == 2


class TestSubscribeAll:
    """Contract 2: all messages delivered to subscribe_all callback within 500ms."""

    def test_single_subscriber_receives_published_message(self, bus):
        received: List[Message] = []
        bus.subscribe_all(received.append, consumer_id="consumer-a")
        bus.publish(_msg(sender="publisher"))
        assert _wait_for(lambda: len(received) >= 1, timeout=0.5)
        assert received[0].sender == "publisher"

    def test_n_messages_all_delivered(self, bus):
        received: List[Message] = []
        bus.subscribe_all(received.append, consumer_id="consumer-b")
        n = 10
        for i in range(n):
            bus.publish(_msg(sender=f"src-{i}"))
        assert _wait_for(lambda: len(received) >= n, timeout=1.0), (
            f"Only received {len(received)}/{n} messages"
        )

    def test_messages_not_received_before_subscribe(self, bus):
        """Messages published before subscribe_all are NOT delivered to the
        subscriber — that's the at-new semantics of '>' in XREADGROUP and the
        same semantics InProcessBus has (callbacks registered after publish
        miss earlier messages)."""
        bus.publish(_msg(sender="early"))
        received: List[Message] = []
        bus.subscribe_all(received.append, consumer_id="consumer-late")
        # Publish a post-subscribe message.
        bus.publish(_msg(sender="late"))
        assert _wait_for(lambda: len(received) >= 1, timeout=0.5)
        # The late message should arrive; the early one must NOT appear.
        senders = {m.sender for m in received}
        assert "late" in senders
        assert "early" not in senders


class TestMultipleSubscribers:
    """Contract 3: multiple subscribers each get every message."""

    def test_two_consumers_both_get_message(self, bus):
        received_a: List[Message] = []
        received_b: List[Message] = []
        bus.subscribe_all(received_a.append, consumer_id="multi-a")
        bus.subscribe_all(received_b.append, consumer_id="multi-b")
        bus.publish(_msg(sender="broadcaster"))
        assert _wait_for(lambda: len(received_a) >= 1, timeout=0.5)
        assert _wait_for(lambda: len(received_b) >= 1, timeout=0.5)
        assert received_a[0].sender == "broadcaster"
        assert received_b[0].sender == "broadcaster"

    def test_three_consumers_all_get_n_messages(self, bus):
        received = {k: [] for k in ("g1", "g2", "g3")}
        for k in received:
            bus.subscribe_all(received[k].append, consumer_id=k)
        n = 5
        for i in range(n):
            bus.publish(_msg(sender=f"pub-{i}"))
        for k in received:
            assert _wait_for(lambda: len(received[k]) >= n, timeout=1.0), (
                f"Consumer {k} only got {len(received[k])}/{n}"
            )


class TestDedup:
    """Contract 4: duplicate delivery → callback invoked once per unique message.

    For InProcessBus this is guaranteed by the implementation (no dedup needed).
    For RedisStreamsBus the per-consumer LRU handles XREADGROUP redelivery.
    We simulate redelivery by calling _dispatch_entry directly with the same xid.
    """

    def test_inprocess_no_duplicate(self, inprocess_bus):
        received: List[Message] = []
        inprocess_bus.subscribe_all(received.append, consumer_id="dedup-ip")
        m = _msg()
        inprocess_bus.publish(m)
        time.sleep(0.05)
        assert len(received) == 1

    def test_redis_dedup_via_lru(self, redis_bus):
        """Simulate Redis re-delivery of the same XID — callback must fire once."""
        received: List[Message] = []
        redis_bus.subscribe_all(received.append, consumer_id="dedup-redis")
        m = _msg()
        # Manually insert a stream entry with a known xid.
        xid = redis_bus._client.xadd(
            redis_bus.STREAM_KEY, {"data": __import__("json").dumps(m.to_dict())}
        )
        import json
        fields = {"data": json.dumps(m.to_dict())}
        # Dispatch once normally.
        redis_bus._dispatch_entry("dedup-redis", xid, fields)
        # Dispatch again with the SAME xid — should be deduplicated.
        redis_bus._dispatch_entry("dedup-redis", xid, fields)
        assert len(received) == 1, (
            f"Expected 1 delivery, got {len(received)} (dedup failed)"
        )


class TestSubscriberIsolation:
    """Contract 5: a raising subscriber does not block delivery to others."""

    def test_raising_subscriber_does_not_block_other(self, bus):
        good_received: List[Message] = []

        def bad_cb(msg):
            raise RuntimeError("intentional test error")

        def good_cb(msg):
            good_received.append(msg)

        # Register bad before good so it fires first.
        bus.subscribe_all(bad_cb, consumer_id="bad-sub")
        bus.subscribe_all(good_cb, consumer_id="good-sub")
        bus.publish(_msg())
        assert _wait_for(lambda: len(good_received) >= 1, timeout=0.5), (
            "good_cb was not called despite bad_cb raising"
        )

    def test_raising_subscriber_does_not_block_within_same_consumer(self, bus):
        """Two callbacks registered on the same consumer_id; one raises.
        The second must still be invoked."""
        received: List[Message] = []

        def bad_cb(msg):
            raise RuntimeError("boom")

        def good_cb(msg):
            received.append(msg)

        # For InProcessBus both callbacks go to the same internal list per type.
        # For RedisStreamsBus both go in self._subscribers[consumer_id].
        bus.subscribe_all(bad_cb, consumer_id="mixed")
        bus.subscribe_all(good_cb, consumer_id="mixed")
        bus.publish(_msg())
        assert _wait_for(lambda: len(received) >= 1, timeout=0.5), (
            "good_cb was not called when bad_cb raised in the same consumer"
        )


class TestHistoryAfterReconnect:
    """Contract 6: history is complete independent of consumer lifecycle.

    For InProcessBus: the in-memory list is always complete.
    For RedisStreamsBus: XRANGE reads from the stream, which persists
    independent of any consumer group or connection.
    """

    def test_history_survives_consumer_recreation(self, bus):
        bus.publish(_msg(sender="before"))
        # First consumer.
        r1: List[Message] = []
        bus.subscribe_all(r1.append, consumer_id="reconn-1")
        # Second independent subscriber — simulates a reconnected consumer.
        r2: List[Message] = []
        bus.subscribe_all(r2.append, consumer_id="reconn-2")
        bus.publish(_msg(sender="after"))
        assert _wait_for(lambda: len(r1) >= 1, timeout=0.5)
        assert _wait_for(lambda: len(r2) >= 1, timeout=0.5)
        # history() must always return all messages, regardless of consumers.
        history = bus.history()
        senders = [m.sender for m in history]
        assert "before" in senders
        assert "after" in senders


class TestTypeFilteredSubscribe:
    """Contract 7: subscribe(type, callback) only delivers the requested type."""

    def test_type_filter_bids_only(self, bus):
        bids: List[Message] = []
        bus.subscribe(MessageType.BID, bids.append)
        bus.publish(_msg(msg_type=MessageType.BID))
        bus.publish(_msg(msg_type=MessageType.ACCEPT))
        bus.publish(_msg(msg_type=MessageType.FAULT))
        assert _wait_for(lambda: len(bids) >= 1, timeout=0.5)
        time.sleep(0.1)  # drain any extras
        assert all(m.type == MessageType.BID for m in bids)

    def test_type_filter_accept_only(self, bus):
        accepts: List[Message] = []
        bus.subscribe(MessageType.ACCEPT, accepts.append)
        bus.publish(_msg(msg_type=MessageType.BID))
        bus.publish(_msg(msg_type=MessageType.ACCEPT))
        assert _wait_for(lambda: len(accepts) >= 1, timeout=0.5)
        time.sleep(0.1)
        assert all(m.type == MessageType.ACCEPT for m in accepts)


class TestShutdown:
    """Contract 8: shutdown() stops consumer threads cleanly."""

    def test_redis_shutdown_joins_threads(self, redis_bus):
        redis_bus.subscribe_all(lambda m: None, consumer_id="shutdown-test")
        threads_before = list(redis_bus._consumer_threads)
        assert len(threads_before) >= 1
        redis_bus.shutdown()
        for t in threads_before:
            assert not t.is_alive(), f"Thread {t.name} still alive after shutdown"
