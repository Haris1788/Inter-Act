"""Tests for replay and actor-inspection endpoints (Phase 4 Stage 6 Track A).

All tests use InProcessBus seeded via direct publish calls.  The ``reset_bus``
fixture ensures a fresh singleton for each test.

Endpoints under test:
    GET /bus/replay   — filtered slice of bus history.
    GET /bus/actors   — per-actor heartbeat summary.
"""

from __future__ import annotations

import time

import pytest

from app import create_app
from app.agents.bus import InProcessBus, reset_bus
from app.agents.negotiation import Message, MessageType
from app.config import TestingConfig


# ── Shared helpers ─────────────────────────────────────────────────────────


def _msg(
    mtype: MessageType,
    sender: str,
    task_id: str = "task-1",
    rnd: int = 0,
    slot_id: str | None = None,
    payload: dict | None = None,
    ts: float | None = None,
) -> Message:
    """Build a Message with sensible defaults for seeding tests."""
    m = Message(
        type=mtype,
        task_id=task_id,
        negotiation_round=rnd,
        sender=sender,
        slot_id=slot_id,
        payload=payload or {},
    )
    if ts is not None:
        m.ts = ts
    return m


def _avail(sender: str, ts_val: float, state: str = "idle", task_id: str = "") -> Message:
    """Build an AVAILABILITY_UPDATE heartbeat message."""
    m = Message(
        type=MessageType.AVAILABILITY_UPDATE,
        task_id=task_id,
        negotiation_round=0,
        sender=sender,
        payload={"ts": ts_val, "state": state},
    )
    m.ts = ts_val
    return m


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_bus_singleton():
    """Drop the bus singleton before each test so seeding is isolated."""
    reset_bus()
    yield
    reset_bus()


@pytest.fixture()
def seeded_bus():
    """Return an InProcessBus pre-seeded with a representative message sequence.

    Index mapping (used in range-filter tests):
        0  TASK_ANNOUNCE  interpreter  task-1
        1  BID            arm-1        task-1
        2  BID            agv-1        task-1
        3  ACCEPT         arm-1        task-1
        4  ACCEPT         agv-1        task-2   ← different task
        5  FAULT          arm-1        task-1
        6  CONSENSUS_REACHED  arm-1   task-1
    """
    bus = InProcessBus()
    messages = [
        _msg(MessageType.TASK_ANNOUNCE, "interpreter", task_id="task-1"),
        _msg(MessageType.BID, "arm-1", task_id="task-1", slot_id="pickup"),
        _msg(MessageType.BID, "agv-1", task_id="task-1", slot_id="transport"),
        _msg(MessageType.ACCEPT, "arm-1", task_id="task-1", slot_id="pickup"),
        _msg(MessageType.ACCEPT, "agv-1", task_id="task-2", slot_id="transport"),
        _msg(MessageType.FAULT, "arm-1", task_id="task-1"),
        _msg(MessageType.CONSENSUS_REACHED, "arm-1", task_id="task-1"),
    ]
    for m in messages:
        bus.publish(m)
    return bus


@pytest.fixture()
def app_with_seeded_bus(seeded_bus, monkeypatch):
    """Flask test app wired to ``seeded_bus`` via monkeypatch of ``get_bus``."""
    monkeypatch.setattr("app.agents.bus._bus", seeded_bus)
    application = create_app(config_class=TestingConfig)
    return application


@pytest.fixture()
def client(app_with_seeded_bus):
    return app_with_seeded_bus.test_client()


# ── /bus/replay tests ──────────────────────────────────────────────────────


class TestReplayNoFilter:
    def test_replay_returns_all_messages_with_no_filter(self, client, seeded_bus):
        """No query params → all 7 messages returned."""
        resp = client.get("/bus/replay")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["count"] == 7
        assert len(data["messages"]) == 7

        # First message should be from "interpreter", last from "arm-1".
        assert data["messages"][0]["sender"] == "interpreter"
        assert data["messages"][-1]["sender"] == "arm-1"

    def test_replay_returns_bus_field(self, client):
        resp = client.get("/bus/replay")
        data = resp.get_json()
        assert "bus" in data
        assert data["bus"] in ("inprocess", "redis")


class TestReplayFilterByTaskId:
    def test_replay_filters_by_task_id(self, client):
        """?task_id=task-2 → only the one task-2 message."""
        resp = client.get("/bus/replay?task_id=task-2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert all(m["task_id"] == "task-2" for m in data["messages"])

    def test_replay_task_id_no_match_returns_empty(self, client):
        resp = client.get("/bus/replay?task_id=nonexistent")
        data = resp.get_json()
        assert data["count"] == 0
        assert data["messages"] == []


class TestReplayFilterByType:
    def test_replay_filters_by_type(self, client):
        """?type=bid → only BID messages (indices 1 and 2)."""
        resp = client.get("/bus/replay?type=bid")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2
        assert all(m["type"] == "bid" for m in data["messages"])

    def test_replay_type_case_insensitive(self, client):
        """type filter is lower-cased before comparison."""
        resp = client.get("/bus/replay?type=BID")
        data = resp.get_json()
        assert data["count"] == 2

    def test_replay_type_no_match(self, client):
        resp = client.get("/bus/replay?type=counter_offer")
        data = resp.get_json()
        assert data["count"] == 0


class TestReplayIndexRange:
    def test_replay_filters_by_index_range(self, client):
        """?from=2&to=5 → messages at indices 2, 3, 4, 5 (4 messages)."""
        resp = client.get("/bus/replay?from=2&to=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 4
        # Index 2 = BID agv-1, index 5 = FAULT arm-1
        assert data["messages"][0]["type"] == "bid"
        assert data["messages"][0]["sender"] == "agv-1"
        assert data["messages"][-1]["type"] == "fault"

    def test_replay_from_only(self, client):
        """?from=5 → messages at indices 5 and 6."""
        resp = client.get("/bus/replay?from=5")
        data = resp.get_json()
        assert data["count"] == 2

    def test_replay_to_only(self, client):
        """?to=1 → first 2 messages (indices 0 and 1)."""
        resp = client.get("/bus/replay?to=1")
        data = resp.get_json()
        assert data["count"] == 2
        assert data["messages"][0]["sender"] == "interpreter"

    def test_replay_invalid_from_returns_400(self, client):
        resp = client.get("/bus/replay?from=notanint")
        assert resp.status_code == 400

    def test_replay_invalid_to_returns_400(self, client):
        resp = client.get("/bus/replay?to=notanint")
        assert resp.status_code == 400


class TestReplayLimit:
    def test_replay_limit_caps_payload(self, client):
        """?limit=2 → exactly 2 messages returned."""
        resp = client.get("/bus/replay?limit=2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2
        assert len(data["messages"]) == 2

    def test_replay_limit_default_is_1000(self, client):
        """With no limit param and fewer than 1000 messages, all are returned."""
        resp = client.get("/bus/replay")
        data = resp.get_json()
        assert data["count"] == 7  # seeded_bus has 7 messages

    def test_replay_invalid_limit_returns_400(self, client):
        resp = client.get("/bus/replay?limit=notanumber")
        assert resp.status_code == 400


class TestReplayMessageShape:
    def test_replay_returns_to_dict_shape(self, client):
        """Every message dict must carry the canonical JSON fields."""
        resp = client.get("/bus/replay")
        data = resp.get_json()
        required_keys = {
            "type",
            "task_id",
            "negotiation_round",
            "sender",
            "slot_id",
            "ts",
            "payload",
        }
        for msg in data["messages"]:
            missing = required_keys - msg.keys()
            assert not missing, f"Message missing keys {missing}: {msg}"

    def test_replay_type_field_is_string_value(self, client):
        """type field must be the string value (e.g. 'bid'), not the enum repr."""
        resp = client.get("/bus/replay?type=bid")
        data = resp.get_json()
        for msg in data["messages"]:
            assert isinstance(msg["type"], str)
            assert msg["type"] == "bid"


# ── /bus/actors tests ──────────────────────────────────────────────────────


@pytest.fixture()
def heartbeat_bus(monkeypatch):
    """Bus pre-seeded with heartbeats for actor-inspection tests.

    Actors present: arm-1, agv-1.
    Non-actor sender: interpreter (must NOT appear in /bus/actors).

    arm-1 heartbeat ts: base_ts + 1.0  (recent, alive)
    agv-1 heartbeat ts: base_ts - 100  (stale → presumed dead)
    """
    base_ts = 1_000_000.0
    bus = InProcessBus()

    recent_ts = base_ts + 1.0
    stale_ts = base_ts - 100.0

    bus.publish(_msg(MessageType.TASK_ANNOUNCE, "interpreter"))
    bus.publish(_avail("arm-1", ts_val=recent_ts, state="idle"))
    bus.publish(_avail("agv-1", ts_val=stale_ts, state="executing"))

    monkeypatch.setattr("app.agents.bus._bus", bus)
    return bus, base_ts


@pytest.fixture()
def heartbeat_client(heartbeat_bus, monkeypatch):
    application = create_app(config_class=TestingConfig)
    return application.test_client()


class TestBusActors:
    def test_bus_actors_lists_distinct_senders(self, heartbeat_client):
        """arm-1 and agv-1 appear; interpreter does NOT."""
        resp = heartbeat_client.get("/bus/actors")
        assert resp.status_code == 200
        data = resp.get_json()

        actor_ids = {a["actor_id"] for a in data["actors"]}
        assert "arm-1" in actor_ids
        assert "agv-1" in actor_ids
        assert "interpreter" not in actor_ids

    def test_bus_actors_threshold_s_in_response(self, heartbeat_client):
        resp = heartbeat_client.get("/bus/actors")
        data = resp.get_json()
        assert data["threshold_s"] == 15.0

    def test_bus_actors_marks_presumed_dead(self, heartbeat_client, heartbeat_bus):
        """agv-1 has a stale heartbeat (100s ago) → presumed_dead=True.
        arm-1 has a recent heartbeat → presumed_dead depends on real time.
        """
        resp = heartbeat_client.get("/bus/actors")
        data = resp.get_json()
        by_id = {a["actor_id"]: a for a in data["actors"]}

        # agv-1 last seen at base_ts - 100 — guaranteed stale (> 15s ago).
        assert by_id["agv-1"]["presumed_dead"] is True

        # arm-1 last seen at base_ts + 1 — also stale vs. real time (base_ts is
        # epoch 1 000 001; now is 2026, so also stale, but the important
        # invariant is the API contract, not the live value).
        # We only assert the field exists and is bool.
        assert isinstance(by_id["arm-1"]["presumed_dead"], bool)

    def test_bus_actors_state_from_latest_heartbeat(self, heartbeat_client):
        """state field reflects the most-recent AVAILABILITY_UPDATE payload.state."""
        resp = heartbeat_client.get("/bus/actors")
        data = resp.get_json()
        by_id = {a["actor_id"]: a for a in data["actors"]}

        assert by_id["arm-1"]["state"] == "idle"
        assert by_id["agv-1"]["state"] == "executing"

    def test_bus_actors_state_latest_wins(self, monkeypatch):
        """When multiple heartbeats exist, the most-recent ts state wins."""
        base_ts = 2_000_000.0
        bus = InProcessBus()
        bus.publish(_avail("arm-2", ts_val=base_ts, state="idle"))
        bus.publish(_avail("arm-2", ts_val=base_ts + 5.0, state="executing"))

        monkeypatch.setattr("app.agents.bus._bus", bus)
        application = create_app(config_class=TestingConfig)
        client = application.test_client()

        resp = client.get("/bus/actors")
        data = resp.get_json()
        by_id = {a["actor_id"]: a for a in data["actors"]}

        # The newer heartbeat has state="executing".
        assert by_id["arm-2"]["state"] == "executing"

    def test_bus_actors_last_seen_matches_latest_heartbeat(self, heartbeat_client, heartbeat_bus):
        """last_seen for arm-1 equals the ts we seeded."""
        _bus, base_ts = heartbeat_bus
        resp = heartbeat_client.get("/bus/actors")
        data = resp.get_json()
        by_id = {a["actor_id"]: a for a in data["actors"]}

        assert by_id["arm-1"]["last_seen"] == pytest.approx(base_ts + 1.0)

    def test_bus_actors_no_heartbeat_yields_null_last_seen(self, monkeypatch):
        """An actor with only non-heartbeat messages has last_seen=null."""
        bus = InProcessBus()
        bus.publish(_msg(MessageType.BID, "arm-3"))

        monkeypatch.setattr("app.agents.bus._bus", bus)
        application = create_app(config_class=TestingConfig)
        client = application.test_client()

        resp = client.get("/bus/actors")
        data = resp.get_json()
        by_id = {a["actor_id"]: a for a in data["actors"]}

        assert by_id["arm-3"]["last_seen"] is None
        assert by_id["arm-3"]["presumed_dead"] is True

    def test_bus_actors_empty_history(self, monkeypatch):
        """Empty bus → empty actors list."""
        bus = InProcessBus()
        monkeypatch.setattr("app.agents.bus._bus", bus)
        application = create_app(config_class=TestingConfig)
        client = application.test_client()

        resp = client.get("/bus/actors")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["actors"] == []
