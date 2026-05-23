"""Tests for PEL (Pending Entries List) replay on consumer restart.

When a consumer group has unacknowledged entries (due to a crash or restart),
_consumer_loop drains the PEL (XREADGROUP id=0) before entering the live `>`
loop. The dedup LRU prevents double-dispatch if the entry was already processed
but not yet ACKed.

Two test strategies are used:
1. Integration-style: use the real _drain_pel / _dispatch_entry path with a
   mock redis client that simulates PEL entries, verifying the dispatch path.
2. Dedup correctness: confirm dedup LRU blocks double-dispatch for same XID.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from app.agents.negotiation import Message, MessageType
from app.agents.redis_bus import RedisStreamsBus, STREAM_KEY, _serialize


def _make_bus(fake_server: fakeredis.FakeServer) -> RedisStreamsBus:
    """Construct a RedisStreamsBus injecting a shared fake Redis client."""
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


def _msg(n: int) -> Message:
    return Message(
        type=MessageType.BID,
        task_id="task-pel",
        negotiation_round=0,
        sender=f"agent-{n}",
        payload={"n": n},
    )


def _wait_for(cond, timeout: float = 2.0, poll: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(poll)
    return cond()


class TestPelDrainOnConsumerRestart:
    """_drain_pel dispatches PEL entries via the same _dispatch_entry path."""

    def test_pel_drains_on_consumer_restart(self):
        """
        Scenario:
        Simulate a consumer restart by calling _drain_pel directly with a mocked
        redis client that returns PEL-style entries (id=0 response), then verify
        all entries are dispatched to subscribers before the live loop begins.

        Note: fakeredis 2.35.1 does not persist PEL across separate client
        instances; we verify the PEL drain path by mocking the redis client's
        xreadgroup to return synthetic PEL entries for the id=0 call, then
        return empty for subsequent id=0 calls (PEL exhausted). The dedup LRU
        on the bus ensures entries processed before are not re-dispatched.
        """
        N = 5
        consumer_id = "test-pel-group"

        # Build the messages we'll simulate as PEL entries.
        messages = [_msg(i) for i in range(N)]
        pel_entries = [
            (f"fake-xid-{i}", {"data": _serialize(messages[i])})
            for i in range(N)
        ]

        fake_server = fakeredis.FakeServer()
        bus = _make_bus(fake_server)

        delivered: list[Message] = []
        lock = threading.Lock()

        def on_message(msg: Message) -> None:
            with lock:
                delivered.append(msg)

        # Attach callback and set up seen_xids for this consumer.
        from app.agents.redis_bus import _LRUSet, _DEDUP_CAPACITY
        bus._subscribers[consumer_id] = [on_message]
        bus._seen_xids[consumer_id] = _LRUSet(_DEDUP_CAPACITY)

        # Mock xreadgroup:
        # - First batch (id=0): return the N PEL entries
        # - Second batch (id=0): return empty → PEL exhausted
        # - Subsequent calls (id=">"): return empty (no live traffic)
        drain_call_count = [0]
        original_xreadgroup = bus._client.xreadgroup

        def mock_xreadgroup(group, consumer, streams, count=None, block=None):
            stream_id = list(streams.values())[0]
            if stream_id == "0":
                drain_call_count[0] += 1
                if drain_call_count[0] == 1:
                    # First PEL drain: return all N entries.
                    return [[STREAM_KEY, list(pel_entries)]]
                else:
                    # PEL exhausted.
                    return [[STREAM_KEY, []]]
            # Live loop (">"): return empty — no new messages.
            return []

        # Also mock xack to avoid errors (no real stream to ack).
        bus._client.xreadgroup = mock_xreadgroup
        bus._client.xack = MagicMock(return_value=1)

        # Run _drain_pel directly (synchronous).
        bus._drain_pel(consumer_id)

        # All N PEL entries should have been dispatched.
        with lock:
            count = len(delivered)
        assert count == N, f"Expected {N} deliveries from PEL drain, got {count}"

        # Each message should have arrived with correct sender.
        with lock:
            senders = {m.sender for m in delivered}
        expected = {f"agent-{i}" for i in range(N)}
        assert senders == expected, f"Unexpected senders: {senders}"

    def test_pel_dedup_prevents_double_dispatch(self):
        """A PEL entry whose XID is already in the dedup LRU is not dispatched again.

        This simulates: entry was processed + LRU updated, but XACK failed.
        On reconnect the entry re-appears in PEL; dedup LRU blocks re-dispatch.
        """
        consumer_id = "dedup-group"
        fake_server = fakeredis.FakeServer()
        bus = _make_bus(fake_server)

        delivered: list[Message] = []
        from app.agents.redis_bus import _LRUSet, _DEDUP_CAPACITY
        bus._subscribers[consumer_id] = [delivered.append]
        lru = _LRUSet(_DEDUP_CAPACITY)
        bus._seen_xids[consumer_id] = lru

        msg = _msg(0)
        xid = "1234567890-0"
        fields = {"data": _serialize(msg)}

        # First dispatch — should deliver.
        bus._client.xack = MagicMock(return_value=1)
        bus._dispatch_entry(consumer_id, xid, fields)
        assert len(delivered) == 1, "First dispatch should deliver"

        # dedup LRU now contains this XID.
        assert xid in lru

        # Second dispatch same XID — dedup blocks it.
        bus._dispatch_entry(consumer_id, xid, fields)
        assert len(delivered) == 1, "Dedup should block second dispatch of same XID"
