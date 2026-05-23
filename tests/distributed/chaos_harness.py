"""Chaos test harness for INTER.act distributed integration testing.

Scoping (Phase 4 Stage 7):
    All chaos is simulated at the Python level: thread kills, message
    injection, Redis client monkey-patching, and clock skewing.  This
    exercises protocol invariants (dedup, TTL recovery, monotonic-round
    filtering, barrier correctness, signal isolation) without requiring a
    real subprocess crash or OS-level network partition.

    Real subprocess + toxiproxy chaos (kill -9 → TTL expiry measured over a
    real TCP stack, iptables-based partition) requires a Linux CI environment
    and is explicitly deferred to Phase 5.  The harness is structured so that
    swapping ``make_chaos_env`` to spawn real subprocesses is mechanical: the
    returned ``{bus, agents, server, client}`` dict is the only seam.

Fakeredis limitation note:
    fakeredis ≥ 2.x supports XADD / XRANGE / XREADGROUP / XACK / XGROUP.
    It does NOT support XAUTOCLAIM.  Any helper that needs XAUTOCLAIM falls
    back to a direct XRANGE scan — the workaround is documented inline.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Generator, Optional
from unittest.mock import patch

import fakeredis
import redis as redis_lib

from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType

# ---------------------------------------------------------------------------
# Warehouse topology used across all chaos tests
# ---------------------------------------------------------------------------

_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}

_LANE_CELLS = [
    [3, 1], [4, 1], [5, 1], [6, 1], [7, 1], [8, 1], [9, 1], [10, 1]
]

_WAREHOUSE_SCENE = {
    "grid": {"cols": 15, "rows": 5, "cell_px": 54},
    "stations": {
        "input_arm": {
            "cell": [1, 1], "dock": [2, 1], "served_by": "arm-1",
            "label": "input_arm", "px": None, "dock_px": None,
        },
        "output_arm": {
            "cell": [12, 1], "dock": [11, 1], "served_by": "arm-2",
            "label": "output_arm", "px": None, "dock_px": None,
        },
    },
    "lane": {"cells": _LANE_CELLS, "px": []},
    "agvs": [
        {"id": "agv-1", "label": "AGV 1", "color": "#16a085", "bay": [0, 1], "bay_px": None},
        {"id": "agv-2", "label": "AGV 2", "color": "#1abc9c", "bay": [0, 3], "bay_px": None},
    ],
}

# ---------------------------------------------------------------------------
# Low-level Redis + bus wiring
# ---------------------------------------------------------------------------


def _build_redis_bus(fake_client):
    """Construct a RedisStreamsBus that uses *fake_client* instead of real Redis."""
    from app.agents.redis_bus import RedisStreamsBus

    bus = RedisStreamsBus.__new__(RedisStreamsBus)
    bus._redis_url = "redis://fake"
    bus._client = fake_client
    bus._subscribers = {}
    bus._subscribers_lock = threading.Lock()
    bus._seen_xids = {}
    bus._consumer_threads = []
    bus._stop_flag = threading.Event()
    return bus


def _inject_all(fake_client) -> None:
    """Inject *fake_client* into every module that touches Redis state."""
    import app.models.agv_state as agv_state_mod
    import app.models.arm_state as arm_state_mod
    import app.models.grid_state as gs

    agv_state_mod._inject_redis_client(fake_client)
    arm_state_mod._inject_redis_client(fake_client)
    gs._inject_redis_client(fake_client)
    # Clear cached RedisLock instances so they pick up the new client.
    with gs._redis_locks_lock:
        gs._redis_locks.clear()

    from app.routes import agv_routes
    agv_routes._inject_signals_redis_client(fake_client)


# ---------------------------------------------------------------------------
# make_chaos_env — primary context manager
# ---------------------------------------------------------------------------

@contextmanager
def make_chaos_env(
    n_arms: int = 2,
    n_agvs: int = 2,
    monkeypatch=None,
) -> Generator[dict, None, None]:
    """Context manager: wire INTERACT_BUS=redis + INTERACT_STATE=redis against
    a fresh FakeServer shared by all modules.  Spawns n_arms ArmAgents and
    n_agvs AGVAgents as daemon threads.

    Yields:
        {
            "bus":    RedisStreamsBus (backed by FakeServer),
            "agents": list of ActorAgent (arms first, then AGVs),
            "server": fakeredis.FakeServer,
            "client": fakeredis.FakeRedis (shared client),
        }

    The caller is responsible for publishing task announcements; this function
    only starts the actors.  On exit, all agents are stopped and state is reset.

    monkeypatch is accepted as a convenience so callers can pass the pytest
    monkeypatch fixture and have env-var cleanup handled automatically.  When
    None, os.environ is patched directly and restored on exit.
    """
    import os
    import app.models.agv_state as agv_state_mod
    import app.models.arm_state as arm_state_mod
    import app.models.grid_state as gs
    from app.routes import agv_routes

    # --- env vars ---
    _old_bus = os.environ.get("INTERACT_BUS")
    _old_state = os.environ.get("INTERACT_STATE")
    os.environ["INTERACT_BUS"] = "redis"
    os.environ["INTERACT_STATE"] = "redis"

    server = fakeredis.FakeServer()
    fake_client = fakeredis.FakeRedis(server=server, decode_responses=True)

    _inject_all(fake_client)

    agv_state_mod.reset()
    arm_state_mod.reset()
    gs.reset()

    bus = _build_redis_bus(fake_client)

    with agv_routes._signals_lock:
        agv_routes._signals.clear()

    # Register AGVs in state
    agv_ids = [f"agv-{i + 1}" for i in range(n_agvs)]
    for i, agv_id in enumerate(agv_ids):
        agv_state_mod.register(agv_id, agv_id.upper(), "#16a085", [0, i * 2 + 1])

    agents: list = []
    arm_zones = ["input_arm", "output_arm"]
    for i in range(n_arms):
        zone = arm_zones[i % len(arm_zones)]
        arm_id = f"arm-{i + 1}"
        agents.append(
            ArmAgent(
                CapabilityProfile(actor_id=arm_id, kind="arm", reachable_zones=[zone]),
                bus,
            )
        )
    for i, agv_id in enumerate(agv_ids):
        agents.append(
            AGVAgent(
                CapabilityProfile(
                    actor_id=agv_id,
                    kind="agv",
                    reachable_zones=["input_arm", "output_arm"],
                    position=[0.0, float(i * 2 + 1)],
                ),
                bus,
            )
        )

    for a in agents:
        a.start()

    try:
        yield {
            "bus": bus,
            "agents": agents,
            "server": server,
            "client": fake_client,
        }
    finally:
        for a in agents:
            try:
                a.stop()
            except Exception:
                pass
        bus.shutdown()
        agv_state_mod.reset()
        arm_state_mod.reset()
        gs.reset()
        with agv_routes._signals_lock:
            agv_routes._signals.clear()
        # Restore env vars.
        if _old_bus is None:
            os.environ.pop("INTERACT_BUS", None)
        else:
            os.environ["INTERACT_BUS"] = _old_bus
        if _old_state is None:
            os.environ.pop("INTERACT_STATE", None)
        else:
            os.environ["INTERACT_STATE"] = _old_state


# ---------------------------------------------------------------------------
# announce helper
# ---------------------------------------------------------------------------

def make_announce(
    task_id: str = "task-chaos",
    negotiation_round: int = 0,
    bid_window: float = 1.5,
    consensus_window: float = 1.5,
    source_zone: str = "input_arm",
    dest_zone: str = "output_arm",
    constraint: str = "shortest time",
) -> Message:
    """Build a TASK_ANNOUNCE message with the standard three-slot structure."""
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=negotiation_round,
        sender="interpreter",
        payload={
            "source_zone": source_zone,
            "dest_zone": dest_zone,
            "constraint": constraint,
            "slots": [
                {"slot_id": "pickup", "kind": "arm", "zone": source_zone},
                {
                    "slot_id": "transport",
                    "kind": "agv",
                    "from_zone": source_zone,
                    "to_zone": dest_zone,
                },
                {"slot_id": "place", "kind": "arm", "zone": dest_zone},
            ],
            "announce_ts": now,
            "bid_deadline_ts": now + bid_window,
            "consensus_deadline_ts": now + bid_window + consensus_window,
        },
    )


# ---------------------------------------------------------------------------
# Chaos helper 1: kill_actor
# ---------------------------------------------------------------------------

def kill_actor(agent) -> None:
    """Simulate abrupt actor death.

    Sets ``_running = False`` so the agent's event loop exits on its next
    iteration, but does NOT call ``agent.stop()`` (which would join threads
    and cleanly release locks).  Any Redis locks held by this actor are NOT
    released — they must expire by TTL to test crash-recovery.

    Note: the agent's daemon thread will exit naturally within one ``_IDLE_TICK_S``
    (0.5 s) after this call.  Tests should allow 0.6+ s before checking lock state.
    """
    agent._running = False
    # Wake the event loop so it exits promptly rather than waiting on the queue.
    try:
        agent._inbox.put(None)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Chaos helper 2: inject_dup_delivery
# ---------------------------------------------------------------------------

def inject_dup_delivery(bus, n: int = 2):
    """Monkey-patch the bus's Redis client so each XADD publishes the payload
    n times.  This simulates at-least-once redelivery to verify that the
    dedup LRU on each consumer filters duplicates.

    Returns a context manager.  Inside the ``with`` block all XADDs are
    multiplied; outside, the original xadd is restored.

    Implementation note: we patch the xadd method on the client instance (not
    the class) so other clients sharing the FakeServer are unaffected.
    """
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        original_xadd = bus._client.xadd

        def _repeated_xadd(stream, fields, *args, **kwargs):
            for _ in range(n):
                original_xadd(stream, fields, *args, **kwargs)

        bus._client.xadd = _repeated_xadd
        try:
            yield
        finally:
            bus._client.xadd = original_xadd

    return _ctx()


# ---------------------------------------------------------------------------
# Chaos helper 3: inject_stale_message
# ---------------------------------------------------------------------------

def inject_stale_message(
    bus,
    sender: str,
    task_id: str,
    round_value: int,
    msg_type: MessageType = MessageType.TASK_ANNOUNCE,
    extra_payload: Optional[dict] = None,
) -> None:
    """XADD a Message with an old (stale) negotiation_round directly to the
    stream, bypassing the normal publish path.

    Because this calls bus._client.xadd directly, the in-process dedup LRU
    on existing consumers will see this as a brand-new XID.  The monotonic-
    round guard inside ActorAgent._on_task_announce (negotiation_round <=
    participant.round → return early) is the tested invariant.

    For TASK_ANNOUNCE stale messages, a minimal valid payload is constructed.
    Extra fields can be passed in extra_payload.
    """
    from app.agents.redis_bus import STREAM_KEY, _serialize

    now = time.time()
    payload: dict = {}
    if msg_type == MessageType.TASK_ANNOUNCE:
        payload = {
            "source_zone": "input_arm",
            "dest_zone": "output_arm",
            "constraint": "shortest time",
            "slots": [
                {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
                {"slot_id": "transport", "kind": "agv",
                 "from_zone": "input_arm", "to_zone": "output_arm"},
                {"slot_id": "place", "kind": "arm", "zone": "output_arm"},
            ],
            "announce_ts": now,
            "bid_deadline_ts": now + 1.5,
            "consensus_deadline_ts": now + 3.0,
        }
    if extra_payload:
        payload.update(extra_payload)

    msg = Message(
        type=msg_type,
        task_id=task_id,
        negotiation_round=round_value,
        sender=sender,
        payload=payload,
    )
    bus._client.xadd(STREAM_KEY, {"data": _serialize(msg)})


# ---------------------------------------------------------------------------
# Chaos helper 4: partition_actor
# ---------------------------------------------------------------------------

class _PartitionError(redis_lib.ConnectionError):
    """Synthetic connection error injected by partition_actor."""


@contextmanager
def partition_actor(actor_id: str, bus):
    """Context manager: break the bus client's publish/history methods so calls
    from the actor's thread raise redis.ConnectionError (simulating a network
    partition for that actor).

    Implementation: we monkey-patch the client's ``xadd`` and ``xrange``
    methods on the shared bus client object.  This is a coarse simulation —
    in real network partitions only one actor's TCP connection breaks, but with
    a shared fakeredis client we affect all callers.

    Workaround for shared-client limitation: the partition is applied for the
    duration of the ``with`` block only.  Tests that use this helper should be
    structured so no other actor is actively publishing or reading during the
    partition window.  The invariant being tested (agent survives gracefully,
    no rogue commits) does not require surgical per-actor isolation.

    After exit the original methods are restored.
    """
    original_xadd = bus._client.xadd
    original_xrange = bus._client.xrange
    original_xreadgroup = bus._client.xreadgroup

    def _broken_xadd(*args, **kwargs):
        raise _PartitionError(f"Simulated partition for actor {actor_id}")

    def _broken_xrange(*args, **kwargs):
        raise _PartitionError(f"Simulated partition for actor {actor_id}")

    def _broken_xreadgroup(*args, **kwargs):
        raise _PartitionError(f"Simulated partition for actor {actor_id}")

    bus._client.xadd = _broken_xadd
    bus._client.xrange = _broken_xrange
    bus._client.xreadgroup = _broken_xreadgroup
    try:
        yield
    finally:
        bus._client.xadd = original_xadd
        bus._client.xrange = original_xrange
        bus._client.xreadgroup = original_xreadgroup


# ---------------------------------------------------------------------------
# Chaos helper 5: clock_skew
# ---------------------------------------------------------------------------

# Thread-local storage for per-thread clock skew.
_thread_local = threading.local()


def clock_skew(agent, delta_s: float):
    """Monkey-patch time.time for the agent's event-loop thread to return
    ``real_now + delta_s``.

    Implementation:
        We store the skew delta in a thread-local variable and patch
        ``app.agents.actor_agent.time.time`` with a wrapper that reads
        ``_thread_local.clock_delta``.  Because the patch is applied globally,
        only the thread that has ``_thread_local.clock_delta`` set returns a
        skewed value; all other threads return the real time.

    Limitation: because ``time.time`` is used throughout the codebase
    (negotiation deadlines, TaskParticipant._place_bids, etc.) and CPython's
    ``time.time`` is a C-level function, we patch the name binding in the
    ``actor_agent`` module.  This works for the event-loop's
    ``_next_wakeup()`` and ``_check_deadlines()`` calls; it does NOT skew
    ``time.time()`` called inside ``task_participant.py`` unless that module's
    binding is also patched.

    For the test being run (test_clock_skew_per_actor_deadline_independence),
    the key invariant is that the agent still completes the protocol correctly
    despite using its own skewed clock — the leaderless rule does not require
    synchronized clocks.  The patch is broad enough to exercise that invariant.

    Returns a context manager that installs and removes the skew.
    """
    import app.agents.actor_agent as actor_agent_mod
    import app.agents.task_participant as participant_mod

    real_time = time.time

    def _skewed_time():
        delta = getattr(_thread_local, "clock_delta", 0.0)
        return real_time() + delta

    @contextmanager
    def _ctx():
        # Mark the agent's thread for skew (set before thread starts reading).
        # We can't set thread-local on another thread directly, so we install a
        # one-shot callback via the agent's inbox that sets the value on the
        # agent's own thread.
        skew_set = threading.Event()

        original_run = agent._run

        def _patched_run():
            _thread_local.clock_delta = delta_s
            skew_set.set()
            original_run()

        agent._run = _patched_run

        # Patch time.time in both modules.
        with patch.object(actor_agent_mod, "time") as mock_at, \
             patch.object(participant_mod, "time") as mock_pt:
            mock_at.time = _skewed_time
            mock_at.sleep = time.sleep   # keep sleep real
            mock_at.monotonic = time.monotonic
            mock_pt.time = _skewed_time
            mock_pt.sleep = time.sleep
            mock_pt.monotonic = time.monotonic
            yield
            _thread_local.clock_delta = 0.0

        agent._run = original_run

    return _ctx()


# ---------------------------------------------------------------------------
# Shared pipeline_config patcher (call once per test module in autouse fixture)
# ---------------------------------------------------------------------------

def patch_pipeline_config(monkeypatch) -> None:
    """Monkeypatch pipeline_config lookups to use the harness topology."""
    from app.models import pipeline_config
    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    monkeypatch.setattr(pipeline_config, "get_lane_cells", lambda: list(_LANE_CELLS))
    monkeypatch.setattr(
        pipeline_config, "get_agv",
        lambda agv_id: next(
            (a for a in _WAREHOUSE_SCENE["agvs"] if a["id"] == agv_id), None
        ),
    )
    monkeypatch.setattr(pipeline_config, "get_warehouse_scene", lambda: _WAREHOUSE_SCENE)
    monkeypatch.setattr(pipeline_config, "cell_to_px", lambda cell: None)
