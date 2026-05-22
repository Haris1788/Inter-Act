# Phase 1 — Negotiation Primitives Implementation Plan (Stage 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure, fully-unit-tested foundation of the decentralized negotiation layer — the message protocol, the typed pub/sub bus, and per-actor capability profiles.

**Architecture:** Three new modules under `app/agents/`. `negotiation.py` is pure logic (message envelope, the 11 message types, the deterministic public consensus rule, slot derivation, completeness check) with zero I/O. `bus.py` is a typed in-process pub/sub bus with a best-effort SocketIO mirror for the UI. `capability.py` builds one capability profile per actor from `pipeline.yaml`. No threads, no agents, no route changes yet — those are Stage 2 and 3.

**Tech Stack:** Python 3.11, `dataclasses`, `enum`, `threading`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-22-phase1-decentralized-negotiation-design.md` (sections 3.1, 4, 5, 7.1).

**This plan does NOT delete anything.** `central_brain.py`, `plan_executor.py`, `message_bus.py` are untouched in Stage 1 — they are removed in Stage 3 once the replacement is wired. The new modules are added alongside.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/agents/negotiation.py` (create) | Message envelope + 11 `MessageType`s + `RoundStatus` + `Slot`/`derive_slots` + `public_winner` rule + `slots_complete` check. Pure logic. |
| `app/agents/bus.py` (create) | `NegotiationBus` typed pub/sub + `get_bus`/`reset_bus` singleton accessors + best-effort SocketIO mirror. |
| `app/agents/capability.py` (create) | `CapabilityProfile` dataclass + `load_profiles()` reading `pipeline_config`. |
| `tests/test_negotiation.py` (create) | Unit tests for `negotiation.py`. |
| `tests/test_bus.py` (create) | Unit tests for `bus.py`. |
| `tests/test_capability.py` (create) | Unit tests for `capability.py`. |

`app/agents/__init__.py` already exists (keep as-is).

---

## Task 1: Message envelope and types

**Files:**
- Create: `app/agents/negotiation.py`
- Test: `tests/test_negotiation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_negotiation.py`:

```python
"""Unit tests for the negotiation protocol primitives."""

from app.agents.negotiation import Message, MessageType, RoundStatus


def test_message_to_dict_roundtrips():
    m = Message(
        type=MessageType.BID,
        task_id="t1",
        negotiation_round=0,
        sender="agv-1",
        slot_id="transport",
        bid_version=2,
        payload={"score": 3.0},
    )
    d = m.to_dict()
    assert d["type"] == "bid"
    assert d["task_id"] == "t1"
    assert d["negotiation_round"] == 0
    assert d["sender"] == "agv-1"
    assert d["slot_id"] == "transport"
    assert d["bid_version"] == 2
    assert d["payload"] == {"score": 3.0}
    assert isinstance(d["ts"], float)


def test_message_defaults():
    m = Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id="t1",
        negotiation_round=0,
        sender="interpreter",
    )
    assert m.slot_id is None
    assert m.bid_version == 0
    assert m.payload == {}
    assert m.ts > 0


def test_eleven_message_types_exist():
    expected = {
        "task_announce", "capability_broadcast", "availability_update",
        "bid", "counter_offer", "accept", "reject", "busy",
        "fault", "withdraw", "consensus_reached",
    }
    assert {t.value for t in MessageType} == expected


def test_round_status_values():
    assert RoundStatus.OPEN.value == "open"
    assert RoundStatus.CONVERGED.value == "converged"
    assert RoundStatus.FAILED.value == "failed"
    assert RoundStatus.SUPERSEDED.value == "superseded"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_negotiation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.negotiation'`

- [ ] **Step 3: Write minimal implementation**

Create `app/agents/negotiation.py`:

```python
"""Negotiation protocol primitives — message types, envelope, public rules.

Pure logic: no I/O, no threads, no Flask. Safe to unit-test in isolation.
This module is the contract every actor agent and the bus share.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional


class MessageType(str, Enum):
    """The 11 negotiation message types. `counter_offer` is reserved for a
    later phase and is never emitted in Phase 1."""

    TASK_ANNOUNCE = "task_announce"
    CAPABILITY_BROADCAST = "capability_broadcast"
    AVAILABILITY_UPDATE = "availability_update"
    BID = "bid"
    COUNTER_OFFER = "counter_offer"  # reserved — never emitted in Phase 1
    ACCEPT = "accept"
    REJECT = "reject"
    BUSY = "busy"
    FAULT = "fault"
    WITHDRAW = "withdraw"
    CONSENSUS_REACHED = "consensus_reached"


class RoundStatus(str, Enum):
    """Observed (never owned) status of a single negotiation round."""

    OPEN = "open"
    CONVERGED = "converged"
    FAILED = "failed"
    SUPERSEDED = "superseded"


@dataclass
class Message:
    """A single bus message. `negotiation_round` + `bid_version` make stale
    messages detectable; see the staleness rules in the design spec section 5."""

    type: MessageType
    task_id: str
    negotiation_round: int
    sender: str
    slot_id: Optional[str] = None
    bid_version: int = 0
    ts: float = field(default_factory=time.time)
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-safe dict — used for the SocketIO mirror to the browser."""
        d = asdict(self)
        d["type"] = self.type.value
        return d
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_negotiation.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/negotiation.py tests/test_negotiation.py
git commit -m "Add negotiation message envelope and types"
```

---

## Task 2: Slot derivation

**Files:**
- Modify: `app/agents/negotiation.py`
- Test: `tests/test_negotiation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_negotiation.py`:

```python
def test_derive_slots_three_legs():
    from app.agents.negotiation import derive_slots

    slots = derive_slots("input_arm", "output_arm")
    assert [s.slot_id for s in slots] == ["pickup", "transport", "place"]
    assert [s.kind for s in slots] == ["arm", "agv", "arm"]

    pickup, transport, place = slots
    assert pickup.zone == "input_arm"
    assert place.zone == "output_arm"
    assert transport.from_zone == "input_arm"
    assert transport.to_zone == "output_arm"
    assert transport.zone is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_negotiation.py::test_derive_slots_three_legs -v`
Expected: FAIL — `ImportError: cannot import name 'derive_slots'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/agents/negotiation.py` (after the `Message` class):

```python
@dataclass
class Slot:
    """One leg of a transport task. Slot *structure* is a physical-topology
    fact (an arm serves each station; AGVs move between) — it names no actor.
    Who fills a slot is decided by negotiation, never by this module."""

    slot_id: str
    kind: str  # "arm" | "agv"
    zone: Optional[str] = None       # arm slots: the station zone
    from_zone: Optional[str] = None  # agv slot: pickup zone
    to_zone: Optional[str] = None    # agv slot: dropoff zone


def derive_slots(source_zone: str, dest_zone: str) -> list[Slot]:
    """Decompose a transport task into its three structural slots.

    "move X -> Y" always needs: an arm at X, an AGV X->Y, an arm at Y.
    This is structural validation, not assignment.
    """
    return [
        Slot(slot_id="pickup", kind="arm", zone=source_zone),
        Slot(
            slot_id="transport",
            kind="agv",
            from_zone=source_zone,
            to_zone=dest_zone,
        ),
        Slot(slot_id="place", kind="arm", zone=dest_zone),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_negotiation.py::test_derive_slots_three_legs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agents/negotiation.py tests/test_negotiation.py
git commit -m "Add transport-task slot derivation"
```

---

## Task 3: The public consensus rule

**Files:**
- Modify: `app/agents/negotiation.py`
- Test: `tests/test_negotiation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_negotiation.py`:

```python
def _bid(sender, score, slot="transport", rnd=0, ver=0):
    from app.agents.negotiation import Message, MessageType

    return Message(
        type=MessageType.BID,
        task_id="t1",
        negotiation_round=rnd,
        sender=sender,
        slot_id=slot,
        bid_version=ver,
        payload={"score": score},
    )


def test_public_winner_shortest_time_picks_lowest_score():
    from app.agents.negotiation import public_winner

    bids = [_bid("agv-1", 14.0), _bid("agv-2", 8.0), _bid("agv-3", 20.0)]
    assert public_winner(bids, "shortest time") == "agv-2"


def test_public_winner_shortest_distance_picks_lowest_score():
    from app.agents.negotiation import public_winner

    bids = [_bid("agv-1", 30.0), _bid("agv-3", 12.0)]
    assert public_winner(bids, "shortest distance") == "agv-3"


def test_public_winner_tie_break_is_lowest_sender_id():
    from app.agents.negotiation import public_winner

    bids = [_bid("agv-3", 5.0), _bid("agv-1", 5.0), _bid("agv-2", 5.0)]
    assert public_winner(bids, "shortest time") == "agv-1"


def test_public_winner_unconstrained_is_lowest_sender_id():
    from app.agents.negotiation import public_winner

    bids = [_bid("agv-2", 99.0), _bid("agv-1", 1.0)]
    assert public_winner(bids, "") == "agv-1"


def test_public_winner_empty_set_is_none():
    from app.agents.negotiation import public_winner

    assert public_winner([], "shortest time") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_negotiation.py -k public_winner -v`
Expected: FAIL — `ImportError: cannot import name 'public_winner'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/agents/negotiation.py`:

```python
# Bid / consensus window sizes (seconds). Generous vs. sub-millisecond
# deterministic bids, so every bid lands well before any deadline.
BID_WINDOW_S = 1.5
CONSENSUS_WINDOW_S = 1.5

_SCORED_CONSTRAINTS = ("shortest time", "shortest distance")


def public_winner(bids: list[Message], constraint: str) -> Optional[str]:
    """Deterministic winner of ONE slot from the public bid set.

    Every actor calls this with the same surviving-bid set and the same
    constraint, so every actor computes the identical winner. This is the
    leaderless-consensus rule — no component "decides", the rule does.

    `bids` must already be filtered to the surviving bids for one slot
    (latest bid_version per sender, withdrawn bids removed). Returns the
    winning sender id, or None when there are no bids.
    """
    if not bids:
        return None
    norm = (constraint or "").strip().lower()
    if norm in _SCORED_CONSTRAINTS:
        # Lower score wins; ties broken deterministically by lowest sender id.
        best = min(bids, key=lambda m: (m.payload["score"], m.sender))
        return best.sender
    # Unconstrained: lowest sender id among valid bidders.
    return min(b.sender for b in bids)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_negotiation.py -k public_winner -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/negotiation.py tests/test_negotiation.py
git commit -m "Add deterministic public consensus rule"
```

---

## Task 4: Completeness check

**Files:**
- Modify: `app/agents/negotiation.py`
- Test: `tests/test_negotiation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_negotiation.py`:

```python
def test_slots_complete_true_when_every_slot_filled():
    from app.agents.negotiation import slots_complete

    required = {"pickup", "transport", "place"}
    accepts = {"pickup": "arm-1", "transport": "agv-2", "place": "arm-2"}
    assert slots_complete(required, accepts) is True


def test_slots_complete_false_when_a_slot_missing():
    from app.agents.negotiation import slots_complete

    required = {"pickup", "transport", "place"}
    accepts = {"pickup": "arm-1", "transport": "agv-2"}
    assert slots_complete(required, accepts) is False


def test_slots_complete_false_when_extra_slot_present():
    from app.agents.negotiation import slots_complete

    required = {"pickup", "transport"}
    accepts = {"pickup": "arm-1", "transport": "agv-2", "ghost": "agv-9"}
    assert slots_complete(required, accepts) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_negotiation.py -k slots_complete -v`
Expected: FAIL — `ImportError: cannot import name 'slots_complete'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/agents/negotiation.py`:

```python
def slots_complete(required_slot_ids: set[str], accepts: dict[str, str]) -> bool:
    """Validation only: True when every required slot has exactly one accepted
    actor and no extra slot is present.

    `accepts` maps slot_id -> accepting actor id, already filtered to valid,
    non-withdrawn accepts. This function NEVER picks an actor — it only checks.
    """
    return required_slot_ids == set(accepts.keys()) and all(accepts.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_negotiation.py -v`
Expected: PASS (all `test_negotiation.py` tests, 14 total)

- [ ] **Step 5: Commit**

```bash
git add app/agents/negotiation.py tests/test_negotiation.py
git commit -m "Add slot completeness validation check"
```

---

## Task 5: The negotiation bus

**Files:**
- Create: `app/agents/bus.py`
- Test: `tests/test_bus.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bus.py`:

```python
"""Unit tests for the negotiation bus."""

import pytest

from app.agents.bus import NegotiationBus, get_bus, reset_bus
from app.agents.negotiation import Message, MessageType


@pytest.fixture(autouse=True)
def _fresh_bus():
    reset_bus()
    yield
    reset_bus()


def _msg(mtype, sender="x"):
    return Message(type=mtype, task_id="t1", negotiation_round=0, sender=sender)


def test_subscribe_publish_delivers_to_subscriber():
    bus = NegotiationBus()
    received = []
    bus.subscribe(MessageType.BID, received.append)
    m = _msg(MessageType.BID)
    bus.publish(m)
    assert received == [m]


def test_publish_records_history():
    bus = NegotiationBus()
    m = _msg(MessageType.TASK_ANNOUNCE, sender="interpreter")
    bus.publish(m)
    assert bus.history() == [m]


def test_subscriber_only_receives_its_type():
    bus = NegotiationBus()
    got = []
    bus.subscribe(MessageType.ACCEPT, got.append)
    bus.publish(_msg(MessageType.BID))
    assert got == []


def test_multiple_subscribers_all_receive():
    bus = NegotiationBus()
    a, b = [], []
    bus.subscribe(MessageType.BID, a.append)
    bus.subscribe(MessageType.BID, b.append)
    bus.publish(_msg(MessageType.BID))
    assert len(a) == 1 and len(b) == 1


def test_subscriber_exception_does_not_break_publish():
    bus = NegotiationBus()
    ok = []

    def boom(_m):
        raise RuntimeError("subscriber blew up")

    bus.subscribe(MessageType.BID, boom)
    bus.subscribe(MessageType.BID, ok.append)
    bus.publish(_msg(MessageType.BID))  # must not raise
    assert len(ok) == 1


def test_publish_without_flask_app_does_not_raise():
    # The SocketIO mirror is best-effort; with no running app it must no-op.
    bus = NegotiationBus()
    bus.publish(_msg(MessageType.BID))  # must not raise


def test_get_bus_is_singleton():
    assert get_bus() is get_bus()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.bus'`

- [ ] **Step 3: Write minimal implementation**

Create `app/agents/bus.py`:

```python
"""Negotiation bus — typed in-process pub/sub with a SocketIO mirror.

Replaces the old emit-only message_bus.py. Actors both publish and subscribe;
every message is also mirrored to the browser as a `bus_message` SocketIO
event so the UI shows real negotiation traffic.

The bus is deliberately dumb: it delivers messages and records history. It
makes no decisions. Subscribers (actor agents, Stage 2) keep their own inbox
queues so a slow or recursive subscriber cannot stall a publisher.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable

from app.agents.negotiation import Message, MessageType


class NegotiationBus:
    """Minimal typed pub/sub. Thread-safe. No authority, no decisions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: dict[MessageType, list[Callable[[Message], None]]] = (
            defaultdict(list)
        )
        self._history: list[Message] = []

    def subscribe(
        self, msg_type: MessageType, callback: Callable[[Message], None]
    ) -> None:
        """Register `callback` to receive every message of `msg_type`."""
        with self._lock:
            self._subs[msg_type].append(callback)

    def publish(self, message: Message) -> None:
        """Record, mirror to the UI, then dispatch to in-process subscribers.

        A subscriber that raises is isolated — it never breaks delivery to the
        others or to the publisher.
        """
        with self._lock:
            self._history.append(message)
            callbacks = list(self._subs.get(message.type, []))
        self._mirror(message)
        for cb in callbacks:
            try:
                cb(message)
            except Exception:
                pass

    def history(self) -> list[Message]:
        """Every message published, in order. Used by tests and by actors
        rejoining after a fault to derive the current round."""
        with self._lock:
            return list(self._history)

    def _mirror(self, message: Message) -> None:
        """Best-effort SocketIO mirror for the browser bus panel. Imported
        lazily so the bus is testable with no Flask app. Never raises."""
        try:
            from app import socketio

            socketio.emit("bus_message", message.to_dict())
        except Exception:
            pass


# ── module-level singleton ────────────────────────────────────────────────
_bus: NegotiationBus | None = None


def get_bus() -> NegotiationBus:
    """Return the process-wide negotiation bus, creating it on first use."""
    global _bus
    if _bus is None:
        _bus = NegotiationBus()
    return _bus


def reset_bus() -> None:
    """Test hook — drop the singleton so each test gets a fresh bus."""
    global _bus
    _bus = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bus.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/bus.py tests/test_bus.py
git commit -m "Add typed negotiation bus with SocketIO mirror"
```

---

## Task 6: Capability profile dataclass

**Files:**
- Create: `app/agents/capability.py`
- Test: `tests/test_capability.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_capability.py`:

```python
"""Unit tests for actor capability profiles."""

from app.agents.capability import CapabilityProfile


def test_capability_profile_to_dict():
    p = CapabilityProfile(
        actor_id="agv-1",
        kind="agv",
        reachable_zones=["input_arm", "output_arm"],
        position=[10.0, 3.0],
        speed=2.0,
        capacity=1,
    )
    d = p.to_dict()
    assert d["actor_id"] == "agv-1"
    assert d["kind"] == "agv"
    assert d["reachable_zones"] == ["input_arm", "output_arm"]
    assert d["position"] == [10.0, 3.0]
    assert d["speed"] == 2.0
    assert d["capacity"] == 1


def test_capability_profile_defaults():
    p = CapabilityProfile(actor_id="arm-1", kind="arm")
    assert p.reachable_zones == []
    assert p.position == [0.0, 0.0]
    assert p.speed == 1.0
    assert p.capacity == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_capability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.capability'`

- [ ] **Step 3: Write minimal implementation**

Create `app/agents/capability.py`:

```python
"""Capability profiles — what each actor independently knows about itself.

Each actor owns its profile and broadcasts it on the bus. There is no central
capability registry: discovery emerges from `capability_broadcast` messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CapabilityProfile:
    """One actor's self-knowledge: which zones it reaches, where it is, how
    fast, how much it carries."""

    actor_id: str
    kind: str  # "arm" | "agv"
    reachable_zones: list[str] = field(default_factory=list)
    position: list[float] = field(default_factory=lambda: [0.0, 0.0])
    speed: float = 1.0
    capacity: int = 1

    def to_dict(self) -> dict:
        """JSON-safe dict — the payload of a `capability_broadcast` message."""
        return {
            "actor_id": self.actor_id,
            "kind": self.kind,
            "reachable_zones": list(self.reachable_zones),
            "position": list(self.position),
            "speed": self.speed,
            "capacity": self.capacity,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_capability.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/capability.py tests/test_capability.py
git commit -m "Add CapabilityProfile dataclass"
```

---

## Task 7: Load capability profiles from config

**Files:**
- Modify: `app/agents/capability.py`
- Test: `tests/test_capability.py`

Background: `app/models/pipeline_config.py` exposes `get_arms()` (list of `{id,label,...}`), `get_stations()` (`{station_name: {served_by, cell, ...}}`), and `get_agvs()` (list of `{id,label,color,bay}`). It caches the parsed YAML in the module global `pipeline_config._config`; setting that global bypasses disk. AGV configs in `pipeline.yaml` carry no `speed`/`capacity`, so both default — equal-speed AGVs mean the bid differentiator is distance, which matches `goal.md`'s "nearest vehicle" default.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_capability.py`:

```python
def test_load_profiles_from_config(monkeypatch):
    from app.agents import capability
    from app.models import pipeline_config

    fake = {
        "arms": [
            {"id": 1, "label": "Input Arm"},
            {"id": 2, "label": "Output Arm"},
        ],
        "warehouse": {
            "stations": {
                "input_arm": {"served_by": "arm-1", "cell": [1, 1]},
                "output_arm": {"served_by": "arm-2", "cell": [12, 1]},
            },
            "agvs": [
                {"id": "agv-1", "bay": [10, 3]},
                {"id": "agv-2", "bay": [10, 5]},
                {"id": "agv-3", "bay": [10, 7]},
            ],
        },
    }
    monkeypatch.setattr(pipeline_config, "_config", fake)

    profiles = capability.load_profiles()
    by_id = {p.actor_id: p for p in profiles}

    assert set(by_id) == {"arm-1", "arm-2", "agv-1", "agv-2", "agv-3"}

    assert by_id["arm-1"].kind == "arm"
    assert "input_arm" in by_id["arm-1"].reachable_zones
    assert "output_arm" in by_id["arm-2"].reachable_zones

    assert by_id["agv-1"].kind == "agv"
    assert set(by_id["agv-1"].reachable_zones) == {"input_arm", "output_arm"}
    assert by_id["agv-1"].position == [10.0, 3.0]
    assert by_id["agv-1"].speed == 1.0
    assert by_id["agv-1"].capacity == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_capability.py::test_load_profiles_from_config -v`
Expected: FAIL — `AttributeError: module 'app.agents.capability' has no attribute 'load_profiles'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/agents/capability.py`:

```python
def load_profiles() -> list[CapabilityProfile]:
    """Build one CapabilityProfile per actor from pipeline.yaml.

    Arms: one per `arms:` entry; reachable zones are the station(s) that name
    the arm in `served_by`. AGVs: one per `warehouse.agvs` entry; they reach
    every station (they travel the lane between docks); position is the bay
    cell. AGV speed/capacity are not in config today, so both default — equal
    speed makes distance the bid differentiator (goal.md's "nearest" default).
    """
    from app.models import pipeline_config

    stations = pipeline_config.get_stations()

    # arm id -> the station zone(s) it serves
    arm_zones: dict[str, list[str]] = {}
    for station_name, station in stations.items():
        served = station.get("served_by")
        if served:
            arm_zones.setdefault(served, []).append(station_name)

    profiles: list[CapabilityProfile] = []

    for arm in pipeline_config.get_arms():
        actor_id = f"arm-{arm['id']}"
        profiles.append(
            CapabilityProfile(
                actor_id=actor_id,
                kind="arm",
                reachable_zones=arm_zones.get(actor_id, []),
                speed=1.0,
                capacity=1,
            )
        )

    all_station_zones = list(stations.keys())
    for agv in pipeline_config.get_agvs():
        bay = agv.get("bay", [0, 0])
        profiles.append(
            CapabilityProfile(
                actor_id=agv["id"],
                kind="agv",
                reachable_zones=list(all_station_zones),
                position=[float(bay[0]), float(bay[1])],
                speed=float(agv.get("speed", 1.0)) or 1.0,
                capacity=int(agv.get("capacity", 1)),
            )
        )

    return profiles
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_capability.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/capability.py tests/test_capability.py
git commit -m "Add load_profiles from pipeline config"
```

---

## Task 8: Full Stage 1 regression

**Files:** none (verification only)

- [ ] **Step 1: Run the new test modules together**

Run: `python -m pytest tests/test_negotiation.py tests/test_bus.py tests/test_capability.py -v`
Expected: PASS — 24 tests (14 negotiation + 7 bus + 3 capability)

- [ ] **Step 2: Run the full backend suite to confirm no regression**

Run: `python -m pytest tests/ -q`
Expected: all previously-passing tests still PASS; the 24 new tests added. Stage 1 adds files only — it deletes nothing — so no existing test should break. If any pre-existing test fails, it was already failing before this plan; note it but do not fix it here (out of scope for Stage 1).

- [ ] **Step 3: Commit (only if Step 1/2 surfaced a fixable new-code issue)**

If Steps 1–2 are clean, no commit is needed — every task already committed. If a new-module bug was found and fixed:

```bash
git add app/agents/ tests/test_negotiation.py tests/test_bus.py tests/test_capability.py
git commit -m "Fix negotiation primitive issue found in Stage 1 regression"
```

---

## Self-Review

**1. Spec coverage (spec sections 3.1, 4, 5, 7.1):**
- 11 message types → Task 1 (`MessageType`). ✅
- Message envelope (`task_id`, `negotiation_round`, `slot_id`, `bid_version`, `ts`) → Task 1. ✅
- `RoundStatus` (open/converged/failed/superseded) → Task 1. ✅
- Slot derivation from source/dest → Task 2. ✅
- Public consensus rule (constraint→rule, tie-break lowest id) → Task 3. ✅
- Completeness check (validation, not assignment) → Task 4. ✅
- Typed pub/sub bus + SocketIO mirror → Task 5. ✅
- Capability profile + load from config → Tasks 6–7. ✅
- `counter_offer` reserved-not-emitted → present in `MessageType`, asserted by Task 1's type-set test; no emit path exists in Stage 1. ✅
- Out of Stage 1 scope (correctly deferred): the bus deadlines `BID_WINDOW_S`/`CONSENSUS_WINDOW_S` are *defined* in Task 3 but *consumed* by agents in Stage 2; staleness/idempotency/monotonic-round enforcement lives in the agents (Stage 2); deletions (Stage 3). No gap — these are explicitly later stages.

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later". Every code step has complete code. Every test step has complete test code. ✅

**3. Type consistency:** `Message`, `MessageType`, `RoundStatus`, `Slot`, `derive_slots`, `public_winner`, `slots_complete`, `BID_WINDOW_S`, `CONSENSUS_WINDOW_S` (negotiation.py); `NegotiationBus`, `get_bus`, `reset_bus` (bus.py); `CapabilityProfile`, `load_profiles` (capability.py). Names are used identically across tasks and tests. `public_winner` takes `(bids, constraint)` in both Task 3's definition and test. `slots_complete` takes `(required_slot_ids, accepts)` consistently. ✅

No issues found.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-phase1-negotiation-primitives.md`. This is Stage 1 of 3 — it produces a fully-tested negotiation protocol + bus + capability library, adding files only (no deletions). Stages 2 (actor agents) and 3 (wiring & cleanup) will be planned after Stage 1 lands.

Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent implements each task, with review between tasks; fast iteration, clean context per task.
2. **Inline Execution** — execute the tasks in this session with checkpoints for review.

Which approach?
