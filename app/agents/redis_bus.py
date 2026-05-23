"""RedisStreamsBus — distributed NegotiationBus backed by Redis Streams.

Design decisions (Phase 4 Stage 1 spec):
- ONE global stream: ``interact:negotiation``. ``Message.task_id`` lives in the
  payload; consumers filter client-side. Ordering is XID-monotonic = publish
  order.
- One consumer group per ``consumer_id`` (= ``actor_id`` for agents, or the
  special ``"__socketio_mirror__"`` consumer).  Within each group one consumer
  (named ``"c1"``) reads every message. Every group sees every message → same
  semantics as `InProcessBus.subscribe_all`.
- At-least-once delivery + consumer-side dedup via a bounded LRU of seen XIDs
  (capacity 1024). Pure consensus is already idempotent; dedup is belt-and-
  braces.
- Persistence: Redis AOF ``appendfsync everysec`` (configured in docker-compose).
- SocketIO mirror is NOT done here — it is done by a dedicated mirror consumer
  in ``app/services/bus_mirror.py``. `publish()` is a pure XADD with no side
  effects beyond writing to the stream.
- Connection errors: logged + retried with exponential back-off (1 s → max 30 s).
  Consumer threads never crash the process.

This module contains NO consensus semantics — it delivers and records only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from typing import Callable, Optional

import redis as redis_lib

from app.agents.bus import BusUnavailable
from app.agents.negotiation import Message, MessageType

logger = logging.getLogger(__name__)

STREAM_KEY = "interact:negotiation"

# Bounded LRU for per-consumer dedup.
_DEDUP_CAPACITY = 1024

# XREADGROUP tuning.
_XREADGROUP_COUNT = 64
_XREADGROUP_BLOCK_MS = 100  # milliseconds

# Backoff on connection error.
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0

# Maximum retry attempts before raising BusUnavailable.
_MAX_RETRIES = 30


class _LRUSet:
    """Bounded ordered-insertion set for XID dedup. O(1) add and contains."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._data: OrderedDict[str, None] = OrderedDict()

    def add(self, item: str) -> None:
        if item in self._data:
            self._data.move_to_end(item)
            return
        self._data[item] = None
        if len(self._data) > self._capacity:
            self._data.popitem(last=False)

    def __contains__(self, item: object) -> bool:
        return item in self._data


def _serialize(message: Message) -> str:
    """JSON-encode a Message for storage in the Redis stream field ``data``."""
    return json.dumps(message.to_dict())


def _deserialize(raw: str) -> Optional[Message]:
    """Decode a JSON string back into a Message. Returns None on error."""
    try:
        d = json.loads(raw)
        d["type"] = MessageType(d["type"])
        return Message(**d)
    except Exception:
        logger.exception("RedisStreamsBus: failed to deserialize message: %r", raw)
        return None


class RedisStreamsBus:
    """Distributed NegotiationBus backed by Redis Streams.

    All public methods are thread-safe. Consumer threads are daemon threads
    and are joined on ``shutdown()``.
    """

    STREAM_KEY = STREAM_KEY

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        # consumer_id -> list of callbacks
        self._subscribers: dict[str, list[Callable[[Message], None]]] = {}
        self._subscribers_lock = threading.Lock()
        # consumer_id -> _LRUSet of seen XIDs
        self._seen_xids: dict[str, _LRUSet] = {}
        # consumer threads + stop flags
        self._consumer_threads: list[threading.Thread] = []
        self._stop_flag = threading.Event()

    # ── NegotiationBus interface ───────────────────────────────────────────

    def publish(self, message: Message) -> None:
        """XADD the serialized message to the stream.

        Does NOT emit to SocketIO — the dedicated mirror consumer handles that.
        Retries with back-off on connection errors up to _MAX_RETRIES times;
        raises BusUnavailable when all attempts are exhausted.
        """
        data = _serialize(message)
        backoff = _BACKOFF_INITIAL_S
        for attempt in range(_MAX_RETRIES):
            try:
                self._client.xadd(STREAM_KEY, {"data": data})
                return
            except redis_lib.RedisError as exc:
                logger.error(
                    "RedisStreamsBus.publish: Redis error (%s); "
                    "retrying in %.1fs (attempt %d/%d)",
                    exc,
                    backoff,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
        raise BusUnavailable(
            f"RedisStreamsBus.publish: failed after {_MAX_RETRIES} retries"
        )

    def history(self) -> list[Message]:
        """XRANGE STREAM_KEY - + → deserialize and return in publish order.

        Retries with back-off on connection errors up to _MAX_RETRIES times;
        raises BusUnavailable when all attempts are exhausted.
        """
        backoff = _BACKOFF_INITIAL_S
        for attempt in range(_MAX_RETRIES):
            try:
                entries = self._client.xrange(STREAM_KEY)
                messages = []
                for _xid, fields in entries:
                    raw = fields.get("data", "")
                    msg = _deserialize(raw)
                    if msg is not None:
                        messages.append(msg)
                return messages
            except redis_lib.RedisError as exc:
                logger.error(
                    "RedisStreamsBus.history: Redis error (%s); "
                    "retrying in %.1fs (attempt %d/%d)",
                    exc,
                    backoff,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
        raise BusUnavailable(
            f"RedisStreamsBus.history: failed after {_MAX_RETRIES} retries"
        )

    def subscribe(
        self, msg_type: MessageType, callback: Callable[[Message], None]
    ) -> None:
        """Type-filtered subscription: internally subscribe_all + client-side filter.

        Redis Streams is not pub/sub-per-topic; we deliver all messages to the
        consumer and filter before invoking `callback`.
        """
        def _filtered(msg: Message) -> None:
            if msg.type == msg_type:
                callback(msg)

        # Use a generated consumer_id for type-filtered subscriptions.
        cid = f"sub-type-{msg_type.value}-{uuid.uuid4().hex[:8]}"
        self.subscribe_all(_filtered, consumer_id=cid)

    def subscribe_all(
        self,
        callback: Callable[[Message], None],
        consumer_id: Optional[str] = None,
    ) -> None:
        """Register `callback` to receive every message via a XREADGROUP loop.

        `consumer_id` is used as the consumer-group name. If None, a random
        ephemeral id is generated. Calling this multiple times with the same
        `consumer_id` appends the callback — all registered callbacks for a
        group are invoked on each message.

        A single daemon thread is spawned per unique `consumer_id`.
        """
        if consumer_id is None:
            consumer_id = f"sub-{uuid.uuid4().hex[:8]}"

        with self._subscribers_lock:
            if consumer_id not in self._subscribers:
                self._subscribers[consumer_id] = []
                self._seen_xids[consumer_id] = _LRUSet(_DEDUP_CAPACITY)
                # Create the consumer group idempotently.
                # MKSTREAM creates the stream if it does not exist.
                # BUSYGROUP is raised when the group already exists — that's fine.
                try:
                    self._client.xgroup_create(
                        STREAM_KEY, consumer_id, id="$", mkstream=True
                    )
                except redis_lib.ResponseError as exc:
                    if "BUSYGROUP" not in str(exc):
                        raise
                # Spawn the reader thread.
                t = threading.Thread(
                    target=self._consumer_loop,
                    args=(consumer_id,),
                    name=f"redis-consumer-{consumer_id}",
                    daemon=True,
                )
                self._consumer_threads.append(t)
                t.start()
            self._subscribers[consumer_id].append(callback)

    def shutdown(self) -> None:
        """Stop all consumer threads and wait for them to exit."""
        self._stop_flag.set()
        for t in self._consumer_threads:
            t.join(timeout=3.0)
            if t.is_alive():
                logger.warning(
                    "RedisStreamsBus: consumer thread %s did not stop within 3s",
                    t.name,
                )

    # ── consumer loop ─────────────────────────────────────────────────────

    def _drain_pel(self, consumer_id: str) -> None:
        """Drain the Pending Entries List (PEL) for this consumer group on startup.

        On entry, before switching to the live `>` loop, read all entries that
        were delivered to this consumer group but not yet ACKed (id=0 in
        XREADGROUP). This handles the crash-and-restart scenario: entries that
        were in-flight when the previous instance crashed are re-delivered here
        before any new messages, restoring at-least-once semantics.

        The dedup LRU in _dispatch_entry prevents double-processing when an entry
        was already processed but the ACK was lost.

        Loops until XREADGROUP with id=0 returns an empty result set (PEL exhausted).
        """
        logger.debug(
            "RedisStreamsBus consumer %s: draining PEL before live loop", consumer_id
        )
        while not self._stop_flag.is_set():
            try:
                # id="0" means "entries pending (delivered but unacknowledged)"
                results = self._client.xreadgroup(
                    consumer_id,
                    "c1",
                    {STREAM_KEY: "0"},
                    count=_XREADGROUP_COUNT,
                )
            except redis_lib.RedisError as exc:
                logger.error(
                    "RedisStreamsBus consumer %s: PEL drain Redis error (%s); skipping",
                    consumer_id,
                    exc,
                )
                return  # best-effort; proceed to live loop

            if not results:
                break
            any_entries = False
            for _stream_key, entries in results:
                for xid, fields in entries:
                    any_entries = True
                    self._dispatch_entry(consumer_id, xid, fields)
            if not any_entries:
                break  # empty batch → PEL exhausted

        logger.debug(
            "RedisStreamsBus consumer %s: PEL drain complete", consumer_id
        )

    def _consumer_loop(self, consumer_id: str) -> None:
        """XREADGROUP loop for one consumer group. Runs as a daemon thread.

        Delivery guarantees:
        - PEL drain on entry: unacknowledged entries from a prior crash are
          replayed before entering the live `>` loop. The dedup LRU prevents
          accidental double-dispatch when an entry was processed but not ACK'd.
        - BLOCK 100ms so the loop is low-latency without busy-spinning.
        - COUNT 64 messages per call caps memory pressure.
        - XACK on success prevents redelivery of processed messages.
        - XID dedup (LRU 1024) handles at-least-once redelivery edge cases.
        - A subscriber that raises is isolated; delivery continues to others.
        """
        # ── Phase 1: drain PEL before going live ─────────────────────────────
        self._drain_pel(consumer_id)

        # ── Phase 2: live delivery loop ───────────────────────────────────────
        backoff = _BACKOFF_INITIAL_S
        while not self._stop_flag.is_set():
            try:
                results = self._client.xreadgroup(
                    consumer_id,       # group
                    "c1",             # consumer name within group
                    {STREAM_KEY: ">"},  # ">" = only new (undelivered) messages
                    count=_XREADGROUP_COUNT,
                    block=_XREADGROUP_BLOCK_MS,
                )
                backoff = _BACKOFF_INITIAL_S  # reset on success
                if not results:
                    continue
                for _stream_key, entries in results:
                    for xid, fields in entries:
                        self._dispatch_entry(consumer_id, xid, fields)
            except redis_lib.RedisError as exc:
                if self._stop_flag.is_set():
                    return
                logger.error(
                    "RedisStreamsBus consumer %s: Redis error (%s); "
                    "retrying in %.1fs",
                    consumer_id,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
            except Exception:
                if self._stop_flag.is_set():
                    return
                logger.exception(
                    "RedisStreamsBus consumer %s: unexpected error in loop",
                    consumer_id,
                )

    def _dispatch_entry(self, consumer_id: str, xid: str, fields: dict) -> None:
        """Parse, dedup, and invoke all subscriber callbacks for one entry."""
        seen = self._seen_xids.get(consumer_id)
        if seen is not None and xid in seen:
            # Duplicate delivery — already processed.
            try:
                self._client.xack(STREAM_KEY, consumer_id, xid)
            except redis_lib.RedisError:
                pass
            return

        raw = fields.get("data", "")
        msg = _deserialize(raw)
        if msg is None:
            # Unparseable — ack and skip so it never blocks the group.
            try:
                self._client.xack(STREAM_KEY, consumer_id, xid)
            except redis_lib.RedisError:
                pass
            return

        with self._subscribers_lock:
            callbacks = list(self._subscribers.get(consumer_id, []))

        delivered = False
        for cb in callbacks:
            try:
                cb(msg)
                delivered = True
            except Exception:
                # Isolated — log but continue delivering to remaining callbacks.
                logger.exception(
                    "RedisStreamsBus consumer %s: subscriber raised on %s",
                    consumer_id,
                    msg.type,
                )
                delivered = True  # still ack — the entry was processed

        # Ack regardless of whether any callback raised; an unack'd PEL entry
        # will be redelivered on reconnect, which is acceptable but not desired
        # for ephemeral subscribers.
        try:
            self._client.xack(STREAM_KEY, consumer_id, xid)
        except redis_lib.RedisError as exc:
            logger.warning(
                "RedisStreamsBus consumer %s: XACK failed for xid %s: %s",
                consumer_id,
                xid,
                exc,
            )

        if seen is not None:
            seen.add(xid)
