"""Tests for the ActorAgent heartbeat (Phase 4 Stage 5, Item 4).

Verifies:
1. Heartbeats emitted at the configured interval.
2. Heartbeat payload contains 'ts' (float) and 'state' (str).
3. Heartbeat thread stops cleanly when agent.stop() is called.
4. last_seen / is_presumed_dead pure-function helpers work correctly.

Uses InProcessBus so no Redis/fakeredis dependency.
"""

from __future__ import annotations

import time

import pytest

from app.agents.arm_agent import ArmAgent
from app.agents.bus import InProcessBus
from app.agents.capability import CapabilityProfile
from app.agents.heartbeat import is_presumed_dead, last_seen
from app.agents.negotiation import Message, MessageType


_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}


@pytest.fixture(autouse=True)
def _patch_stations(monkeypatch):
    from app.models import pipeline_config
    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)


def _arm(bus, actor_id="arm-hb", zone="input_arm", heartbeat_interval=0.2) -> ArmAgent:
    return ArmAgent(
        CapabilityProfile(actor_id=actor_id, kind="arm", reachable_zones=[zone]),
        bus,
        heartbeat_interval=heartbeat_interval,
    )


def _wait_for(cond, timeout=3.0, poll=0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(poll)
    return cond()


class TestHeartbeatEmission:
    def test_heartbeat_emitted_at_interval(self):
        """Start an actor with heartbeat_interval=0.2s; wait 1s; ≥ 4 heartbeats."""
        bus = InProcessBus()
        agent = _arm(bus, "arm-hb-1", heartbeat_interval=0.2)
        agent.start()
        time.sleep(1.1)
        agent.stop()

        heartbeats = [
            m for m in bus.history()
            if m.type == MessageType.AVAILABILITY_UPDATE and m.sender == "arm-hb-1"
        ]
        assert len(heartbeats) >= 4, (
            f"Expected >= 4 heartbeats in 1s, got {len(heartbeats)}"
        )

    def test_heartbeat_includes_state_and_ts(self):
        """Heartbeat payload must contain 'ts' (float) and 'state' (str)."""
        from app.agents.actor_agent import AgentState

        bus = InProcessBus()
        agent = _arm(bus, "arm-hb-2", heartbeat_interval=0.1)
        agent.start()

        # Wait for at least one heartbeat.
        assert _wait_for(
            lambda: any(
                m.type == MessageType.AVAILABILITY_UPDATE and m.sender == "arm-hb-2"
                for m in bus.history()
            ),
            timeout=2.0,
        ), "No heartbeat received within 2s"

        agent.stop()

        hbs = [
            m for m in bus.history()
            if m.type == MessageType.AVAILABILITY_UPDATE and m.sender == "arm-hb-2"
        ]
        assert hbs, "No heartbeat messages found"
        hb = hbs[0]

        ts = hb.payload.get("ts")
        assert ts is not None, "Heartbeat payload missing 'ts'"
        assert isinstance(ts, float), f"'ts' should be float, got {type(ts)}"

        state = hb.payload.get("state")
        assert state is not None, "Heartbeat payload missing 'state'"
        assert isinstance(state, str), f"'state' should be str, got {type(state)}"
        valid_states = {s.value for s in AgentState}
        assert state in valid_states, f"Unknown state value: {state!r}"


class TestHeartbeatStopsOnAgentStop:
    def test_heartbeat_stops_on_agent_stop(self):
        """After agent.stop(), no new heartbeats appear in history."""
        bus = InProcessBus()
        agent = _arm(bus, "arm-hb-stop", heartbeat_interval=0.1)
        agent.start()

        # Wait for a few heartbeats so we're sure it was running.
        assert _wait_for(
            lambda: len([
                m for m in bus.history()
                if m.type == MessageType.AVAILABILITY_UPDATE and m.sender == "arm-hb-stop"
            ]) >= 3,
            timeout=2.0,
        ), "Agent didn't emit 3 heartbeats before stop"

        agent.stop()

        # Record count immediately after stop.
        count_after_stop = len([
            m for m in bus.history()
            if m.type == MessageType.AVAILABILITY_UPDATE and m.sender == "arm-hb-stop"
        ])

        # Wait 0.5s (≥ 5 heartbeat intervals) and check count hasn't grown.
        time.sleep(0.5)

        count_after_wait = len([
            m for m in bus.history()
            if m.type == MessageType.AVAILABILITY_UPDATE and m.sender == "arm-hb-stop"
        ])

        # Allow at most 1 extra heartbeat that may have been in-flight during stop.
        assert count_after_wait <= count_after_stop + 1, (
            f"Heartbeats continued after stop: {count_after_stop} → {count_after_wait}"
        )


class TestDeadAgentInference:
    def test_dead_agent_inference_from_stale_heartbeat(self):
        """last_seen and is_presumed_dead work on fake history without real sleep.

        Two AVAILABILITY_UPDATE messages with ts 20s apart:
        - last_seen returns the latest ts.
        - is_presumed_dead(threshold_s=15) returns True when now > latest_ts + 15.
        """
        base_ts = 1_000_000.0  # arbitrary epoch

        # First heartbeat: 20 seconds before "now".
        hb_old = Message(
            type=MessageType.AVAILABILITY_UPDATE,
            task_id="",
            negotiation_round=0,
            sender="arm-dead",
            payload={"ts": base_ts, "state": "idle"},
        )
        # Second heartbeat: 20 seconds later (still 0 seconds before "now").
        hb_newer = Message(
            type=MessageType.AVAILABILITY_UPDATE,
            task_id="",
            negotiation_round=0,
            sender="arm-dead",
            payload={"ts": base_ts + 20.0, "state": "idle"},
        )

        history: list[Message] = [hb_old, hb_newer]

        # last_seen should return the LATEST ts.
        ls = last_seen(history, "arm-dead")
        assert ls == base_ts + 20.0, f"Expected {base_ts + 20.0}, got {ls}"

        # "now" = base_ts + 20.0 + 1 → only 1s stale → NOT presumed dead.
        now_fresh = base_ts + 21.0
        assert not is_presumed_dead(history, "arm-dead", threshold_s=15.0, now=now_fresh), (
            "Should NOT be presumed dead when heartbeat is only 1s stale"
        )

        # "now" = base_ts + 20.0 + 16 → 16s stale → IS presumed dead.
        now_stale = base_ts + 36.0
        assert is_presumed_dead(history, "arm-dead", threshold_s=15.0, now=now_stale), (
            "Should be presumed dead when heartbeat is 16s stale"
        )

        # Actor with no heartbeats at all is also presumed dead.
        assert is_presumed_dead(history, "arm-never-seen", threshold_s=15.0, now=now_fresh), (
            "Actor with no heartbeats should be presumed dead"
        )
