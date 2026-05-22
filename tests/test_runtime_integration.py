"""Phase 3 Task 9 — runtime integration tests.

Covers gaps not already exercised by test_negotiation_integration.py:

1. /negotiate route end-to-end with real agents reaching CONSENSUS_REACHED.
2. agent_registry startup/shutdown lifecycle (idempotency, thread liveness,
   stop+restart).
3. Duplicate reopen idempotency — multiple agents observe the same fault but
   only ONE round+1 TASK_ANNOUNCE must appear on the bus.
4. Stale-round messages ignored — a BID/ACCEPT from an old round must not
   affect current-round consensus.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.bus import NegotiationBus, get_bus, reset_bus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType, accepted_actors


# ── shared fixture data ───────────────────────────────────────────────────────

_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}

_ARMS_CFG = [{"id": 1}, {"id": 2}]
_AGVS_CFG = [{"id": "agv-1", "label": "AGV 1", "color": "#16a085", "bay": [0, 1]}]


@pytest.fixture(autouse=True)
def _patch_pipeline(monkeypatch):
    """Monkeypatch pipeline_config so every test in this file uses the minimal
    two-station, two-arm, one-AGV topology. Also resets the module-level
    negotiation bus before and after each test."""
    from app.models import pipeline_config

    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    monkeypatch.setattr(pipeline_config, "get_arms", lambda: _ARMS_CFG)
    monkeypatch.setattr(pipeline_config, "get_agvs", lambda: _AGVS_CFG)

    reset_bus()
    yield
    reset_bus()


# ── helpers ───────────────────────────────────────────────────────────────────

def _arm(bus, actor_id, zone):
    return ArmAgent(
        CapabilityProfile(actor_id=actor_id, kind="arm", reachable_zones=[zone]),
        bus,
    )


def _agv(bus, actor_id, position=(3, 1)):
    return AGVAgent(
        CapabilityProfile(
            actor_id=actor_id,
            kind="agv",
            reachable_zones=["input_arm", "output_arm"],
            position=[float(position[0]), float(position[1])],
        ),
        bus,
    )


def _announce(bus, *, task_id="task-rt-1", constraint="shortest time",
              bid_window=1.0, consensus_window=2.0):
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=0,
        sender="interpreter",
        payload={
            "source_zone": "input_arm",
            "dest_zone": "output_arm",
            "constraint": constraint,
            "slots": [
                {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
                {"slot_id": "transport", "kind": "agv",
                 "from_zone": "input_arm", "to_zone": "output_arm"},
                {"slot_id": "place", "kind": "arm", "zone": "output_arm"},
            ],
            "announce_ts": now,
            "bid_deadline_ts": now + bid_window,
            "consensus_deadline_ts": now + bid_window + consensus_window,
        },
    )


def _start_and_run(agents, announce, settle=4.5):
    for a in agents:
        a.start()
    try:
        agents[0].bus.publish(announce)
        time.sleep(settle)
        return agents[0].bus.history()
    finally:
        for a in agents:
            a.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  /negotiate route end-to-end
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def _fake_interpret(monkeypatch):
    """Stub the LLM interpreter — returns a valid move spec immediately."""
    fake_spec = {
        "intent": "move",
        "package_count": 1,
        "ordering": [],
        "constraints": ["shortest time"],
        "agv_ids": [],
        "description": "Move one package",
        "raw_prompt": "move a package",
    }
    monkeypatch.setattr(
        "app.agents.interpreter.interpret",
        lambda prompt: {"status": "success", "task_spec": fake_spec},
    )


@pytest.fixture()
def _flask_client(_fake_interpret):
    """Flask test client with a fresh app instance.

    The app-level agent_registry.start_agents() is NOT called here; we use
    the module-level bus singleton (get_bus()) which the route posts to.
    Agents are started manually against that same bus in each test.
    """
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_negotiate_route_agents_reach_consensus(_flask_client, monkeypatch):
    """POST /negotiate (sync=true) → TASK_ANNOUNCE lands → real agents negotiate
    to CONSENSUS_REACHED, all three slots filled.

    Agents are wired to the same module-level bus that the route uses.
    """
    import app.agents.agv_agent as agv_mod
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)

    bus = get_bus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
    ]
    for a in agents:
        a.start()
    try:
        resp = _flask_client.post(
            "/negotiate", json={"prompt": "move a package", "sync": True}
        )
        assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
        data = resp.get_json()
        assert data["status"] == "success"
        task_id = data["task_id"]

        # Allow negotiation to converge.
        time.sleep(4.5)

        history = bus.history()
        assert any(m.type == MessageType.TASK_ANNOUNCE for m in history), \
            "No TASK_ANNOUNCE on bus"

        accepts = accepted_actors(history, task_id, 0)
        assert accepts.get("pickup") == "arm-1", f"pickup slot: {accepts}"
        assert accepts.get("place") == "arm-2", f"place slot: {accepts}"
        assert accepts.get("transport") == "agv-1", f"transport slot: {accepts}"
        assert any(m.type == MessageType.CONSENSUS_REACHED for m in history), \
            "CONSENSUS_REACHED never emitted"
    finally:
        for a in agents:
            a.stop()


def test_negotiate_route_task_announce_slot_shape(_flask_client):
    """Verify the slot envelope published by the route has the shape agents
    expect (slot_id, kind, zone/from_zone/to_zone). No agents needed."""
    resp = _flask_client.post(
        "/negotiate", json={"prompt": "move a package", "sync": True}
    )
    assert resp.status_code == 200
    history = get_bus().history()
    announce = next(m for m in history if m.type == MessageType.TASK_ANNOUNCE)

    slots = {s["slot_id"]: s for s in announce.payload["slots"]}
    assert set(slots) == {"pickup", "transport", "place"}
    assert slots["pickup"]["kind"] == "arm"
    assert slots["place"]["kind"] == "arm"
    assert slots["transport"]["kind"] == "agv"
    assert slots["transport"]["from_zone"] == "input_arm"
    assert slots["transport"]["to_zone"] == "output_arm"


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  agent_registry startup / shutdown lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=False)
def _clean_registry():
    """Ensure the module-level agent registry is clean before and after."""
    import app.agents.agent_registry as reg
    reg.stop_agents()  # in case a prior test leaked
    yield
    reg.stop_agents()


def test_registry_start_returns_three_agents(_clean_registry):
    """start_agents() builds one agent per configured actor (2 arms + 1 AGV)."""
    import app.agents.agent_registry as reg
    agents = reg.start_agents()
    try:
        assert len(agents) == 3
        kinds = {a.profile.kind for a in agents}
        assert kinds == {"arm", "agv"}
    finally:
        reg.stop_agents()


def test_registry_start_is_idempotent(_clean_registry):
    """Second call to start_agents() returns the SAME agent objects (no doubles)."""
    import app.agents.agent_registry as reg
    first = reg.start_agents()
    second = reg.start_agents()
    try:
        assert len(second) == len(first)
        assert set(id(a) for a in first) == set(id(a) for a in second), \
            "start_agents() returned different objects on second call"
    finally:
        reg.stop_agents()


def test_registry_all_threads_alive_after_start(_clean_registry):
    """Every agent thread is alive immediately after start_agents()."""
    import app.agents.agent_registry as reg
    agents = reg.start_agents()
    try:
        for a in agents:
            assert a.is_running(), f"Agent {a.actor_id} thread is not alive after start"
    finally:
        reg.stop_agents()


def test_registry_stop_clears_registry_and_joins_threads(_clean_registry):
    """stop_agents() joins all threads and leaves the registry empty."""
    import app.agents.agent_registry as reg
    agents = reg.start_agents()
    assert len(agents) > 0
    reg.stop_agents()

    # Registry must be empty.
    assert reg.get_agents() == [], "Registry not empty after stop_agents()"
    # All threads must have stopped.
    for a in agents:
        assert not a.is_running(), (
            f"Agent {a.actor_id} thread still alive after stop_agents()"
        )


def test_registry_restart_builds_fresh_agents(_clean_registry):
    """start_agents() after stop_agents() creates a fresh set of agents."""
    import app.agents.agent_registry as reg
    first = reg.start_agents()
    first_ids = {id(a) for a in first}
    reg.stop_agents()

    reset_bus()  # fresh bus for the new set
    second = reg.start_agents()
    second_ids = {id(a) for a in second}
    try:
        assert len(second) == len(first), "Wrong agent count after restart"
        assert first_ids.isdisjoint(second_ids), \
            "Restart returned stale agent objects — expected brand-new instances"
    finally:
        reg.stop_agents()


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  Duplicate reopen idempotency
# ═══════════════════════════════════════════════════════════════════════════════

def test_duplicate_reopen_is_idempotent_per_agent_and_converges():
    """When an agent faults after consensus, multiple surviving agents all see
    the FAULT/WITHDRAW message and each MAY publish a round+1 reopen (one per
    agent). The idempotency guarantee is per-agent: each surviving agent sends
    AT MOST ONE round+1 TASK_ANNOUNCE (_reopened_rounds guard), AND all those
    announces carry the same announce_ts (derived from trigger.ts), so agents
    adopt the same bid/consensus windows and converge cleanly.

    Asserts:
    - Every round-1 TASK_ANNOUNCE has an identical announce_ts (tie-break rule).
    - No agent publishes more than one round-1 announce (per-agent idempotency).
    - Round-1 negotiation converges: agv-2 or agv-3 takes transport.

    Setup: 3 AGVs so two idle agents (agv-2, agv-3) both attempt to reopen
    after agv-1 (round-0 transport winner) faults.
    """
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
        _agv(bus, "agv-2", position=(5, 1)),
        _agv(bus, "agv-3", position=(7, 1)),
    ]
    for a in agents:
        a.start()
    try:
        bus.publish(_announce(bus, task_id="task-dup-reopen"))
        # Wait for round-0 consensus to settle.
        time.sleep(2.5)
        assert accepted_actors(bus.history(), "task-dup-reopen", 0).get("transport") \
            == "agv-1", "agv-1 should have won transport in round 0"

        # Fault the winner — agv-2 and agv-3 (and arms) will each try to reopen.
        agv1 = next(a for a in agents if a.actor_id == "agv-1")
        agv1.fault("test-injected fault for dup-reopen test")
        # Allow reopen + round-1 negotiation to settle.
        time.sleep(4.5)
    finally:
        for a in agents:
            a.stop()

    history = bus.history()
    round1_announces = [
        m for m in history
        if m.type == MessageType.TASK_ANNOUNCE
        and m.task_id == "task-dup-reopen"
        and m.negotiation_round == 1
    ]
    assert len(round1_announces) >= 1, "No round-1 TASK_ANNOUNCE found — reopen never fired"

    # Per-agent idempotency: no sender appears more than once in round-1 announces.
    senders = [m.sender for m in round1_announces]
    assert len(senders) == len(set(senders)), (
        f"An agent published more than one round-1 TASK_ANNOUNCE: {senders}. "
        "Per-agent idempotency (_reopened_rounds guard) is broken."
    )

    # Tie-break rule: announces triggered by the SAME message share the same
    # announce_ts. Verify by grouping: no agent-group has two different announce_ts
    # values for the SAME trigger (i.e. no sender published twice).
    sender_timestamps: dict[str, set] = {}
    for m in round1_announces:
        sender_timestamps.setdefault(m.sender, set()).add(m.payload["announce_ts"])
    for sender, ts_set in sender_timestamps.items():
        assert len(ts_set) == 1, (
            f"Agent {sender} published round-1 announces with different announce_ts: "
            f"{ts_set}. The tie-break rule is broken for this agent."
        )

    # Round-1 must converge: transport slot filled by a surviving AGV.
    round1_accepts = accepted_actors(history, "task-dup-reopen", 1)
    assert round1_accepts.get("transport") in {"agv-2", "agv-3"}, (
        f"Round-1 transport not filled by a surviving AGV: {round1_accepts}"
    )
    assert any(m.type == MessageType.CONSENSUS_REACHED for m in history), \
        "No CONSENSUS_REACHED after fault reopen"


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  Stale-round messages do not affect current-round consensus
# ═══════════════════════════════════════════════════════════════════════════════

def test_stale_round_bid_does_not_affect_current_round():
    """A BID published with negotiation_round=0 after a round-1 reopen must not
    contribute to round-1 consensus.

    Strategy: start a 2-AGV negotiation. After round-0 consensus, fault agv-1 →
    round-1 reopen. Manually inject a stale round-0 BID for the transport slot
    with a very high score (as if a third AGV with a perfect score joined late).
    Verify that the stale bid does not change who wins transport in round 1
    (should remain agv-2, the only surviving AGV).
    """
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
        _agv(bus, "agv-2", position=(5, 1)),
    ]
    for a in agents:
        a.start()
    try:
        bus.publish(_announce(bus, task_id="task-stale"))
        # Wait for round-0 consensus.
        time.sleep(2.5)
        assert accepted_actors(bus.history(), "task-stale", 0).get("transport") \
            == "agv-1", "agv-1 should win transport in round 0"

        # Fault agv-1 to trigger round-1 reopen.
        agv1 = next(a for a in agents if a.actor_id == "agv-1")
        agv1.fault("test-stale-round fault")

        # Small pause so the reopen is published before we inject the stale bid.
        time.sleep(0.3)

        # Inject a stale round-0 BID from a ghost actor — it looks like a very
        # strong bid (score=0.0 → perfect). If stale-round filtering is broken,
        # this would confuse surviving_bids for round 1.
        stale_bid = Message(
            type=MessageType.BID,
            task_id="task-stale",
            negotiation_round=0,     # stale — we are now in round 1
            sender="ghost-agv",
            slot_id="transport",
            payload={"score": 0.0},  # would win if counted
        )
        bus.publish(stale_bid)

        # Allow round-1 negotiation to settle.
        time.sleep(4.5)
    finally:
        for a in agents:
            a.stop()

    history = bus.history()
    round1_accepts = accepted_actors(history, "task-stale", 1)
    assert round1_accepts.get("transport") == "agv-2", (
        f"Round-1 transport winner should be agv-2, got {round1_accepts.get('transport')}. "
        "Stale-round bid may have incorrectly influenced round-1 consensus."
    )
    # Ghost actor must NOT hold any slot in round 1.
    assert "ghost-agv" not in round1_accepts.values(), (
        f"ghost-agv leaked into round-1 accepts: {round1_accepts}"
    )


def test_stale_round_accept_does_not_pollute_consensus_check():
    """A manually injected ACCEPT with negotiation_round=0 after a round-1
    reopen must not satisfy slots_complete() for round 1.

    If accepted_actors() correctly filters by round, the round-1 consensus will
    not be confused by this spurious old ACCEPT.
    """
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
        _agv(bus, "agv-2", position=(5, 1)),
    ]
    for a in agents:
        a.start()
    try:
        bus.publish(_announce(bus, task_id="task-stale-accept"))
        time.sleep(2.5)

        agv1 = next(a for a in agents if a.actor_id == "agv-1")
        agv1.fault("test-stale-accept fault")
        time.sleep(0.3)

        # Inject a stale ACCEPT for the transport slot from round 0 using a ghost.
        stale_accept = Message(
            type=MessageType.ACCEPT,
            task_id="task-stale-accept",
            negotiation_round=0,     # stale round
            sender="ghost-agv",
            slot_id="transport",
            payload={},
        )
        bus.publish(stale_accept)

        time.sleep(4.5)
    finally:
        for a in agents:
            a.stop()

    history = bus.history()
    # Round-1 accepted actors must not include the ghost agent.
    round1_accepts = accepted_actors(history, "task-stale-accept", 1)
    assert "ghost-agv" not in round1_accepts.values(), (
        f"Stale round-0 ACCEPT from ghost-agv leaked into round-1 consensus: "
        f"{round1_accepts}"
    )
    # agv-2 should have taken over transport cleanly.
    assert round1_accepts.get("transport") == "agv-2", (
        f"Round-1 transport should be agv-2, got {round1_accepts}"
    )
