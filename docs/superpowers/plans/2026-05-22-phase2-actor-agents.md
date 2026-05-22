# Phase 1 — Actor Agents & Negotiation Runtime Implementation Plan (Stage 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the live decentralized negotiation runtime — autonomous in-process actor agents that broadcast capabilities, bid, and converge on consensus by leaderless evaluation, with no central component owning allocation.

**Architecture:** One `ActorAgent` per actor (arm or AGV), each on its own thread running an inbox event-loop. Agents coordinate only by publishing to / subscribing from the Stage 1 `NegotiationBus`. The bus's message history *is* the shared public bid set: each agent independently filters it and applies the deterministic `public_winner` rule, so every agent computes the identical winner — consensus emerges, no one decides it. `ArmAgent` and `AGVAgent` specialize bid scoring. `agent_registry` instantiates and starts all agents from `load_profiles()`.

**Tech Stack:** Python 3.11, `threading`, `queue`, `dataclasses`, `enum`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-22-phase1-decentralized-negotiation-design.md` (sections 4, 5, 6, 7).
**Builds on:** Stage 1 (`docs/superpowers/plans/2026-05-22-phase1-negotiation-primitives.md`) — `negotiation.py`, `bus.py`, `capability.py`.

## Scope decisions (locked for Stage 2)

- **One active negotiation at a time.** A single `task_id` is negotiated at once. Concurrent/parallel tasks are deferred (not in the Phase 1 spec's critical path).
- **Each agent = one thread, one inbox event-loop.** The thread blocks on `inbox.get(timeout=...)`, where the timeout is the time until the soonest negotiation deadline. Messages and deadline checks are handled on this one thread — no per-agent lock needed for agent-local state.
- **Leaderless evaluation reads `bus.history()`.** Agents do not maintain private bid dicts; they filter the public history. This *is* the "same public bid set" the spec requires.
- **`_execute_leg` is recorded/simulated in Stage 2.** When an agent wins a slot and the execution barrier passes, it calls `_execute_leg(slot)`, which (Stage 2) records the leg and marks it done. Real AGV-container HTTP and arm animation are wired in Stage 3. This keeps Stage 2 fully testable with no Docker.
- **Stage 2 does NOT wire the `/negotiate` route or `app/__init__.py` startup** — that is Stage 3. Stage 2 is exercised by integration tests that construct agents directly.
- **Stage 2 deletes nothing.** `central_brain.py`, `plan_executor.py`, `message_bus.py` remain until Stage 3.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/agents/negotiation.py` (modify) | Add pure history-filtering helpers: `surviving_bids`, `accepted_actors`. |
| `app/agents/bus.py` (modify) | Add `subscribe_all(callback)` convenience; `public_winner` score guard (Stage 1 carry-over). |
| `app/agents/__init__.py` (modify) | Fix stale docstring (Stage 1 carry-over). |
| `app/agents/actor_agent.py` (create) | `AgentState` enum + `ActorAgent` base — thread, inbox event-loop, lifecycle, bid/evaluate/accept/withdraw, consensus observation, fault/reopen, `_execute_leg` hook. |
| `app/agents/arm_agent.py` (create) | `ArmAgent(ActorAgent)` — fills `arm` slots; reach-based bid score. |
| `app/agents/agv_agent.py` (create) | `AGVAgent(ActorAgent)` — fills the `transport` slot; distance-based bid score. |
| `app/agents/agent_registry.py` (create) | `start_agents()` / `stop_agents()` — instantiate + run one agent per `load_profiles()` entry. |
| `tests/test_negotiation_helpers.py` (create) | Unit tests for `surviving_bids`, `accepted_actors`. |
| `tests/test_actor_agent.py` (create) | Unit tests for the `ActorAgent` base. |
| `tests/test_arm_agent.py`, `tests/test_agv_agent.py` (create) | Unit tests for the specializations. |
| `tests/test_agent_registry.py` (create) | Unit tests for the registry. |
| `tests/test_negotiation_integration.py` (create) | End-to-end negotiation: announce → consensus; fault → reopen → reconverge; no-bid → failed. |

---

## Task 1: Stage 1 carry-over fixes

Three small items raised by Stage 1 reviews, fixed together before new work.

**Files:**
- Modify: `app/agents/bus.py`, `app/agents/negotiation.py`, `app/agents/__init__.py`
- Test: `tests/test_bus.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bus.py`:

```python
def test_subscribe_all_receives_every_type():
    bus = NegotiationBus()
    got = []
    bus.subscribe_all(got.append)
    bus.publish(_msg(MessageType.BID))
    bus.publish(_msg(MessageType.ACCEPT))
    bus.publish(_msg(MessageType.TASK_ANNOUNCE, sender="interpreter"))
    assert [m.type for m in got] == [
        MessageType.BID, MessageType.ACCEPT, MessageType.TASK_ANNOUNCE,
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bus.py::test_subscribe_all_receives_every_type -v`
Expected: FAIL — `AttributeError: 'NegotiationBus' object has no attribute 'subscribe_all'`

- [ ] **Step 3: Write minimal implementation**

In `app/agents/bus.py`, add this method to `NegotiationBus` (after `subscribe`):

```python
    def subscribe_all(self, callback: Callable[[Message], None]) -> None:
        """Register `callback` for every message type. Convenience for actor
        agents, which need the whole negotiation stream."""
        for msg_type in MessageType:
            self.subscribe(msg_type, callback)
```

In `app/agents/negotiation.py`, make `public_winner` tolerate a bid with no `score` (treat it as worst) — change the scored-branch line:

```python
        best = min(bids, key=lambda m: (m.payload["score"], m.sender))
```

to:

```python
        best = min(
            bids,
            key=lambda m: (m.payload.get("score", float("inf")), m.sender),
        )
```

In `app/agents/__init__.py`, replace the stale docstring line `"""Agent layer - multi-agent coordination (Phase 3+)."""` with:

```python
"""Agent layer — decentralized negotiation runtime (Phase 1)."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bus.py tests/test_negotiation.py -v`
Expected: PASS (8 bus + 14 negotiation = 22 tests; the new `subscribe_all` test included makes 8 bus tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/bus.py app/agents/negotiation.py app/agents/__init__.py tests/test_bus.py
git commit -m "Add subscribe_all, guard public_winner score, fix agents docstring"
```

---

## Task 2: History-filtering helpers

`negotiation.py` gains two pure functions that turn raw bus history into the surviving-bid set and the accepted-slot map. They are pure (no I/O) and rely only on history being in publish order, which `NegotiationBus.history()` guarantees.

**Files:**
- Modify: `app/agents/negotiation.py`
- Test: `tests/test_negotiation_helpers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_negotiation_helpers.py`:

```python
"""Unit tests for the negotiation history-filtering helpers."""

from app.agents.negotiation import (
    Message,
    MessageType,
    accepted_actors,
    surviving_bids,
)


def _m(mtype, sender, slot_id=None, rnd=0, ver=0, score=None):
    payload = {} if score is None else {"score": score}
    return Message(
        type=mtype,
        task_id="t1",
        negotiation_round=rnd,
        sender=sender,
        slot_id=slot_id,
        bid_version=ver,
        payload=payload,
    )


def test_surviving_bids_keeps_latest_version_per_sender():
    history = [
        _m(MessageType.BID, "agv-1", "transport", ver=0, score=10.0),
        _m(MessageType.BID, "agv-1", "transport", ver=1, score=4.0),
        _m(MessageType.BID, "agv-2", "transport", ver=0, score=7.0),
    ]
    survivors = surviving_bids(history, "t1", 0, "transport")
    by_sender = {m.sender: m for m in survivors}
    assert set(by_sender) == {"agv-1", "agv-2"}
    assert by_sender["agv-1"].payload["score"] == 4.0  # latest version wins


def test_surviving_bids_drops_withdrawn_sender():
    history = [
        _m(MessageType.BID, "agv-1", "transport", score=4.0),
        _m(MessageType.BID, "agv-2", "transport", score=7.0),
        _m(MessageType.WITHDRAW, "agv-1", "transport"),
    ]
    survivors = surviving_bids(history, "t1", 0, "transport")
    assert [m.sender for m in survivors] == ["agv-2"]


def test_surviving_bids_filters_by_task_round_slot():
    history = [
        _m(MessageType.BID, "agv-1", "transport", rnd=0, score=4.0),
        _m(MessageType.BID, "agv-2", "transport", rnd=1, score=4.0),
        _m(MessageType.BID, "agv-3", "pickup", rnd=0, score=4.0),
    ]
    survivors = surviving_bids(history, "t1", 0, "transport")
    assert [m.sender for m in survivors] == ["agv-1"]


def test_accepted_actors_maps_slot_to_sender():
    history = [
        _m(MessageType.ACCEPT, "arm-1", "pickup"),
        _m(MessageType.ACCEPT, "agv-2", "transport"),
    ]
    assert accepted_actors(history, "t1", 0) == {
        "pickup": "arm-1",
        "transport": "agv-2",
    }


def test_accepted_actors_drops_accept_superseded_by_withdraw():
    history = [
        _m(MessageType.ACCEPT, "agv-2", "transport"),
        _m(MessageType.WITHDRAW, "agv-2", "transport"),
    ]
    assert accepted_actors(history, "t1", 0) == {}


def test_accepted_actors_ignores_other_round():
    history = [
        _m(MessageType.ACCEPT, "arm-1", "pickup", rnd=1),
    ]
    assert accepted_actors(history, "t1", 0) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_negotiation_helpers.py -v`
Expected: FAIL — `ImportError: cannot import name 'surviving_bids'`

- [ ] **Step 3: Write minimal implementation**

Add to `app/agents/negotiation.py` (after `public_winner`):

```python
def _latest_versions(bids: list[Message]) -> list[Message]:
    """Keep only the highest-bid_version Message per sender."""
    best: dict[str, Message] = {}
    for m in bids:
        cur = best.get(m.sender)
        if cur is None or m.bid_version >= cur.bid_version:
            best[m.sender] = m
    return list(best.values())


def surviving_bids(
    history: list[Message],
    task_id: str,
    negotiation_round: int,
    slot_id: str,
) -> list[Message]:
    """The bids for one slot that still stand.

    Pure function over the public bus history: keeps the latest bid_version
    per sender and drops any sender that has since published a `withdraw`
    for this slot. Every actor calling this with the same history computes
    the identical set — the basis of leaderless evaluation.
    """
    withdrawn = {
        m.sender
        for m in history
        if m.type == MessageType.WITHDRAW
        and m.task_id == task_id
        and m.negotiation_round == negotiation_round
        and m.slot_id == slot_id
    }
    bids = [
        m
        for m in history
        if m.type == MessageType.BID
        and m.task_id == task_id
        and m.negotiation_round == negotiation_round
        and m.slot_id == slot_id
        and m.sender not in withdrawn
    ]
    return _latest_versions(bids)


def accepted_actors(
    history: list[Message], task_id: str, negotiation_round: int
) -> dict[str, str]:
    """Map slot_id -> accepting actor id for one task/round.

    An `accept` superseded by a later `withdraw` from the same actor for the
    same slot is removed. History must be in publish order.
    """
    accepts: dict[str, str] = {}
    for m in history:
        if m.task_id != task_id or m.negotiation_round != negotiation_round:
            continue
        if m.type == MessageType.ACCEPT and m.slot_id is not None:
            accepts[m.slot_id] = m.sender
        elif m.type == MessageType.WITHDRAW and m.slot_id is not None:
            if accepts.get(m.slot_id) == m.sender:
                del accepts[m.slot_id]
    return accepts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_negotiation_helpers.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/negotiation.py tests/test_negotiation_helpers.py
git commit -m "Add surviving_bids and accepted_actors history helpers"
```

---

## Task 3: ActorAgent — lifecycle, thread, bus wiring

The `ActorAgent` base class: an agent that can start/stop, runs one thread, drains an inbox the bus feeds, and exposes its lifecycle state. This task builds the skeleton — no negotiation behavior yet (handlers are stubs).

**Files:**
- Create: `app/agents/actor_agent.py`
- Test: `tests/test_actor_agent.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_actor_agent.py`:

```python
"""Unit tests for the ActorAgent base class."""

import time

import pytest

from app.agents.actor_agent import ActorAgent, AgentState
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType


def _profile(actor_id="arm-1", kind="arm"):
    return CapabilityProfile(
        actor_id=actor_id, kind=kind, reachable_zones=["input_arm"]
    )


def test_agent_starts_idle_and_stops_cleanly():
    bus = NegotiationBus()
    agent = ActorAgent(_profile(), bus)
    assert agent.state == AgentState.IDLE
    agent.start()
    try:
        assert agent.is_running()
    finally:
        agent.stop()
    assert not agent.is_running()


def test_agent_subscribes_and_receives_bus_messages():
    bus = NegotiationBus()
    agent = ActorAgent(_profile(), bus)
    agent.start()
    try:
        bus.publish(
            Message(
                type=MessageType.AVAILABILITY_UPDATE,
                task_id="t1",
                negotiation_round=0,
                sender="agv-9",
            )
        )
        # The agent's inbox thread should drain the message within a moment.
        deadline = time.time() + 2.0
        while time.time() < deadline and agent.messages_seen() == 0:
            time.sleep(0.02)
        assert agent.messages_seen() >= 1
    finally:
        agent.stop()


def test_agent_ignores_its_own_messages():
    bus = NegotiationBus()
    agent = ActorAgent(_profile("arm-1"), bus)
    agent.start()
    try:
        bus.publish(
            Message(
                type=MessageType.AVAILABILITY_UPDATE,
                task_id="t1",
                negotiation_round=0,
                sender="arm-1",  # the agent's own id
            )
        )
        time.sleep(0.3)
        assert agent.messages_seen() == 0
    finally:
        agent.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actor_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.actor_agent'`

- [ ] **Step 3: Write minimal implementation**

Create `app/agents/actor_agent.py`:

```python
"""ActorAgent — one autonomous negotiating actor.

Each agent runs on its own thread, owns a CapabilityProfile, and coordinates
solely by publishing to and subscribing from the NegotiationBus. No external
component drives an agent: it owns its participation, bidding, acceptance,
withdrawal, failure, and re-entry.

Phase 1 (Stage 2) scope: one active task at a time; `_execute_leg` is a
recorded/simulated step — real container/arm I/O is wired in Stage 3.

The agent is a single-threaded event loop: the thread blocks on the inbox
queue with a timeout equal to the time until the next negotiation deadline,
so messages and deadline-driven actions are all handled on one thread with
no agent-local locking.
"""

from __future__ import annotations

import queue
import threading
import time
from enum import Enum
from typing import Optional

from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message


class AgentState(str, Enum):
    """An agent's lifecycle state for the current task."""

    IDLE = "idle"
    BIDDING = "bidding"
    WON = "won"
    EXECUTING = "executing"
    DONE = "done"
    FAULTED = "faulted"


# How often the event loop wakes when there is no pending deadline.
_IDLE_TICK_S = 0.5


class ActorAgent:
    """Base autonomous actor. Subclasses (ArmAgent, AGVAgent) add bid scoring
    and leg execution; the base owns the thread, inbox, and lifecycle."""

    def __init__(self, profile: CapabilityProfile, bus: NegotiationBus) -> None:
        self.profile = profile
        self.actor_id = profile.actor_id
        self.bus = bus
        self.state: AgentState = AgentState.IDLE

        self._inbox: queue.Queue[Message] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._messages_seen = 0

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to the bus and start the event-loop thread."""
        if self._running:
            return
        self._running = True
        self.bus.subscribe_all(self._enqueue)
        self._thread = threading.Thread(
            target=self._run, name=f"agent-{self.actor_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the event loop and join the thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def messages_seen(self) -> int:
        """Count of bus messages this agent has processed (excludes its own)."""
        return self._messages_seen

    # ── bus intake ─────────────────────────────────────────────────────────

    def _enqueue(self, message: Message) -> None:
        """Bus callback — runs on the publisher's thread. Keep it cheap: just
        drop the message in the inbox; the agent's own thread does the work.
        The agent ignores messages it sent itself."""
        if message.sender == self.actor_id:
            return
        self._inbox.put(message)

    # ── event loop ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        while self._running:
            try:
                message = self._inbox.get(timeout=self._next_wakeup())
            except queue.Empty:
                message = None
            if message is not None:
                self._messages_seen += 1
                try:
                    self._handle(message)
                except Exception:
                    pass
            try:
                self._check_deadlines()
            except Exception:
                pass

    def _next_wakeup(self) -> float:
        """Seconds until the loop should wake even with no message. Task 6
        overrides the effective wakeup via pending deadlines; the base just
        ticks slowly."""
        return _IDLE_TICK_S

    # ── handlers (filled in by later tasks) ────────────────────────────────

    def _handle(self, message: Message) -> None:
        """Dispatch one inbound message. Base: no-op (Task 4+ fills this)."""

    def _check_deadlines(self) -> None:
        """Act on elapsed negotiation deadlines. Base: no-op (Task 6 fills)."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actor_agent.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/actor_agent.py tests/test_actor_agent.py
git commit -m "Add ActorAgent lifecycle, thread and bus wiring"
```

---

## Task 4: ActorAgent — handle task_announce, broadcast capability, bid

On a `task_announce` the agent records the task, broadcasts its capability, and — if it can fill a slot — publishes a bid. Bid *scoring* is delegated to `_bid_score`, which the base returns `None` from (cannot bid); `ArmAgent`/`AGVAgent` override it (Tasks 9–10).

**Files:**
- Modify: `app/agents/actor_agent.py`
- Test: `tests/test_actor_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_actor_agent.py`:

```python
def _announce(round_=0, constraint="", task_id="t1"):
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=round_,
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
            "bid_deadline_ts": now + 1.5,
            "consensus_deadline_ts": now + 3.0,
        },
    )


class _ScoringAgent(ActorAgent):
    """Test agent that bids a fixed score on every slot whose kind matches."""

    def __init__(self, profile, bus, score=5.0):
        super().__init__(profile, bus)
        self._score = score

    def _bid_score(self, slot: dict):
        if slot["kind"] == self.profile.kind:
            return self._score
        return None


def _published(bus, mtype, sender):
    return [m for m in bus.history() if m.type == mtype and m.sender == sender]


def test_task_announce_makes_agent_broadcast_capability():
    bus = NegotiationBus()
    agent = _ScoringAgent(_profile("arm-1", "arm"), bus)
    agent.start()
    try:
        bus.publish(_announce())
        time.sleep(0.3)
        caps = _published(bus, MessageType.CAPABILITY_BROADCAST, "arm-1")
        assert len(caps) == 1
        assert caps[0].payload["actor_id"] == "arm-1"
    finally:
        agent.stop()


def test_task_announce_makes_capable_agent_bid_and_enter_bidding():
    bus = NegotiationBus()
    agent = _ScoringAgent(_profile("arm-1", "arm"), bus)
    agent.start()
    try:
        bus.publish(_announce())
        time.sleep(0.3)
        bids = _published(bus, MessageType.BID, "arm-1")
        # arm agent bids on the two arm slots: pickup and place
        assert {b.slot_id for b in bids} == {"pickup", "place"}
        assert agent.state == AgentState.BIDDING
    finally:
        agent.stop()


def test_agent_that_cannot_fill_any_slot_does_not_bid():
    bus = NegotiationBus()
    # base ActorAgent._bid_score returns None → never bids
    agent = ActorAgent(_profile("arm-1", "arm"), bus)
    agent.start()
    try:
        bus.publish(_announce())
        time.sleep(0.3)
        assert _published(bus, MessageType.BID, "arm-1") == []
        assert agent.state == AgentState.IDLE
    finally:
        agent.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actor_agent.py -k task_announce -v`
Expected: FAIL — agent does not broadcast/bid (handler is a no-op)

- [ ] **Step 3: Write minimal implementation**

In `app/agents/actor_agent.py`, add the imports at the top (extend the existing `negotiation` import):

```python
from app.agents.negotiation import (
    Message,
    MessageType,
)
```

Add a task-state container and replace the stub `_handle` with a real dispatcher. Add these to the `ActorAgent` class. First, in `__init__`, add task-tracking fields (after `self._messages_seen = 0`):

```python
        # Active-task tracking. Phase 1: one task at a time.
        self._task_id: Optional[str] = None
        self._round: int = -1
        self._task: Optional[dict] = None  # the task_announce payload
        self._evaluated_round: int = -1    # highest round this agent evaluated
```

Then add the methods:

```python
    # ── message dispatch ───────────────────────────────────────────────────

    def _handle(self, message: Message) -> None:
        # Monotonic-round invariant: ignore anything older than the round we
        # are already on for this task.
        if (
            message.task_id == self._task_id
            and message.negotiation_round < self._round
        ):
            return
        if message.type == MessageType.TASK_ANNOUNCE:
            self._on_task_announce(message)

    def _on_task_announce(self, message: Message) -> None:
        # Adopt the (possibly new) task/round. A higher round supersedes.
        if (
            message.task_id == self._task_id
            and message.negotiation_round <= self._round
        ):
            return
        self._task_id = message.task_id
        self._round = message.negotiation_round
        self._task = dict(message.payload)
        self.state = AgentState.IDLE

        self._broadcast_capability()
        self._place_bids()

    def _broadcast_capability(self) -> None:
        self._publish(
            MessageType.CAPABILITY_BROADCAST,
            payload=self.profile.to_dict(),
        )

    def _place_bids(self) -> None:
        slots = (self._task or {}).get("slots", [])
        bid_placed = False
        for slot in slots:
            score = self._bid_score(slot)
            if score is None:
                continue
            self._publish(
                MessageType.BID,
                slot_id=slot["slot_id"],
                payload={"score": score, "basis": self.profile.kind},
            )
            bid_placed = True
        if bid_placed:
            self.state = AgentState.BIDDING

    # ── publishing ─────────────────────────────────────────────────────────

    def _publish(
        self,
        msg_type: MessageType,
        slot_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        self.bus.publish(
            Message(
                type=msg_type,
                task_id=self._task_id or "",
                negotiation_round=self._round,
                sender=self.actor_id,
                slot_id=slot_id,
                payload=payload or {},
            )
        )

    # ── bid scoring (subclasses override) ──────────────────────────────────

    def _bid_score(self, slot: dict):
        """Return this agent's deterministic bid score for `slot`, or None if
        the agent cannot fill it. Base agent never bids."""
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actor_agent.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/actor_agent.py tests/test_actor_agent.py
git commit -m "Add task_announce handling, capability broadcast and bidding"
```

---

## Task 5: ActorAgent — leaderless evaluation at the bid deadline

At `bid_deadline_ts` the agent evaluates every slot it bid on: it reads `bus.history()`, computes `surviving_bids` per slot, applies `public_winner`. If it is the winner it publishes `accept` and enters `WON`; if it lost it publishes `withdraw`. Evaluation runs once per round.

**Files:**
- Modify: `app/agents/actor_agent.py`
- Test: `tests/test_actor_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_actor_agent.py`:

```python
def test_agent_accepts_slot_it_wins_at_bid_deadline():
    bus = NegotiationBus()
    # Two arm agents bid on the arm slots; arm-1 bids lower → wins.
    a1 = _ScoringAgent(_profile("arm-1", "arm"), bus, score=3.0)
    a2 = _ScoringAgent(_profile("arm-2", "arm"), bus, score=9.0)
    a1.start()
    a2.start()
    try:
        bus.publish(_announce(constraint="shortest time"))
        time.sleep(2.2)  # past the 1.5s bid deadline
        a1_accepts = _published(bus, MessageType.ACCEPT, "arm-1")
        a2_withdraws = _published(bus, MessageType.WITHDRAW, "arm-2")
        assert {m.slot_id for m in a1_accepts} == {"pickup", "place"}
        assert {m.slot_id for m in a2_withdraws} == {"pickup", "place"}
        assert a1.state == AgentState.WON
    finally:
        a1.stop()
        a2.stop()


def test_agent_evaluates_only_once_per_round():
    bus = NegotiationBus()
    a1 = _ScoringAgent(_profile("arm-1", "arm"), bus, score=3.0)
    a1.start()
    try:
        bus.publish(_announce())
        time.sleep(2.5)
        # Exactly one accept per won slot — no repeats from repeated ticks.
        accepts = _published(bus, MessageType.ACCEPT, "arm-1")
        assert len(accepts) == 2
    finally:
        a1.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actor_agent.py -k bid_deadline -v`
Expected: FAIL — no accepts published (no deadline handling yet)

- [ ] **Step 3: Write minimal implementation**

In `app/agents/actor_agent.py`, extend the `negotiation` import to include the helpers and `public_winner`:

```python
from app.agents.negotiation import (
    Message,
    MessageType,
    public_winner,
    surviving_bids,
)
```

Replace `_next_wakeup` so the loop wakes at the bid deadline, and replace the stub `_check_deadlines`:

```python
    def _next_wakeup(self) -> float:
        """Wake the loop at the next pending deadline, else tick slowly."""
        if self._task is None:
            return _IDLE_TICK_S
        now = time.time()
        targets = []
        if self._evaluated_round < self._round:
            targets.append(self._task.get("bid_deadline_ts", now))
        soonest = min((t - now for t in targets), default=_IDLE_TICK_S)
        return max(0.0, min(soonest, _IDLE_TICK_S))

    def _check_deadlines(self) -> None:
        if self._task is None:
            return
        now = time.time()
        if (
            self._evaluated_round < self._round
            and now >= self._task.get("bid_deadline_ts", now)
        ):
            self._evaluate()

    def _evaluate(self) -> None:
        """Leaderless evaluation: for every slot, apply the public rule to the
        public bid set. Win → accept; lost (but bid) → withdraw."""
        self._evaluated_round = self._round
        history = self.bus.history()
        constraint = (self._task or {}).get("constraint", "")
        won_any = False
        for slot in (self._task or {}).get("slots", []):
            slot_id = slot["slot_id"]
            bids = surviving_bids(history, self._task_id, self._round, slot_id)
            i_bid = any(b.sender == self.actor_id for b in bids)
            if not i_bid:
                continue
            winner = public_winner(bids, constraint)
            if winner == self.actor_id:
                self._publish(MessageType.ACCEPT, slot_id=slot_id)
                won_any = True
            else:
                self._publish(
                    MessageType.WITHDRAW,
                    slot_id=slot_id,
                    payload={"reason": "lost evaluation"},
                )
        self.state = AgentState.WON if won_any else AgentState.IDLE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actor_agent.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/actor_agent.py tests/test_actor_agent.py
git commit -m "Add leaderless bid-deadline evaluation"
```

---

## Task 6: ActorAgent — consensus observation and leg execution

After accepting, the agent watches for the consensus condition (every slot accepted, stable). When it observes consensus AND holds a slot AND no higher round exists, the execution barrier passes and it runs `_execute_leg`. `_execute_leg` is a recorded step in Stage 2.

**Files:**
- Modify: `app/agents/actor_agent.py`
- Test: `tests/test_actor_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_actor_agent.py`:

```python
def test_winning_agent_executes_its_leg_after_consensus():
    bus = NegotiationBus()
    # One agent fills every slot kind so consensus completes with one actor.
    class _OmniAgent(_ScoringAgent):
        def _bid_score(self, slot):
            return self._score  # bids on all 3 slots

    agent = _OmniAgent(_profile("arm-1", "arm"), bus, score=2.0)
    agent.start()
    try:
        bus.publish(_announce())
        time.sleep(3.2)  # past bid + consensus windows
        assert agent.state == AgentState.DONE
        assert sorted(agent.executed_legs()) == ["pickup", "place", "transport"][:0] + sorted(
            ["pickup", "transport", "place"]
        )
        consensus = [m for m in bus.history()
                     if m.type == MessageType.CONSENSUS_REACHED]
        assert len(consensus) >= 1
    finally:
        agent.stop()


def test_agent_does_not_execute_without_full_consensus():
    bus = NegotiationBus()
    # arm agent wins only the 2 arm slots; transport slot never filled →
    # consensus never reached → no execution.
    agent = _ScoringAgent(_profile("arm-1", "arm"), bus, score=2.0)
    agent.start()
    try:
        bus.publish(_announce())
        time.sleep(3.4)
        assert agent.executed_legs() == []
        assert agent.state != AgentState.DONE
    finally:
        agent.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actor_agent.py -k execute -v`
Expected: FAIL — `AttributeError: 'ActorAgent' object has no attribute 'executed_legs'`

- [ ] **Step 3: Write minimal implementation**

In `app/agents/actor_agent.py`, extend the `negotiation` import again:

```python
from app.agents.negotiation import (
    Message,
    MessageType,
    accepted_actors,
    public_winner,
    slots_complete,
    surviving_bids,
)
```

In `__init__`, add execution tracking (after `self._evaluated_round = -1`):

```python
        self._my_slots: list[str] = []       # slots this agent has accepted
        self._executed_legs: list[str] = []  # slots this agent has executed
        self._consensus_announced = False
```

In `_evaluate`, record won slots — replace the `if winner == self.actor_id:` branch body:

```python
            if winner == self.actor_id:
                self._publish(MessageType.ACCEPT, slot_id=slot_id)
                self._my_slots.append(slot_id)
                won_any = True
```

Extend `_check_deadlines` to also poll for consensus — add at the end of the method:

```python
        self._check_consensus()
```

Add the consensus + execution methods:

```python
    def executed_legs(self) -> list[str]:
        return list(self._executed_legs)

    def _check_consensus(self) -> None:
        """Observe (never decide) the consensus condition, then run our leg.

        Execution barrier: we execute only when we locally observe consensus,
        we hold at least one accepted slot, and no higher round has appeared.
        """
        if self._task is None or self.state in (AgentState.DONE, AgentState.FAULTED):
            return
        history = self.bus.history()
        required = {s["slot_id"] for s in self._task.get("slots", [])}
        accepts = accepted_actors(history, self._task_id, self._round)
        if not slots_complete(required, accepts):
            return

        # Observational consensus marker — idempotent, non-authoritative.
        if not self._consensus_announced:
            self._consensus_announced = True
            self._publish(
                MessageType.CONSENSUS_REACHED, payload={"round": self._round}
            )

        if not self._my_slots:
            return  # consensus reached, but this agent holds nothing to run
        self.state = AgentState.EXECUTING
        for slot_id in self._my_slots:
            if slot_id not in self._executed_legs:
                self._execute_leg(slot_id)
                self._executed_legs.append(slot_id)
        self.state = AgentState.DONE

    # ── leg execution (subclasses may override) ────────────────────────────

    def _execute_leg(self, slot_id: str) -> None:
        """Run this agent's physical leg. Stage 2: a recorded/simulated step.
        Stage 3 wires real AGV-container HTTP and arm animation here."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actor_agent.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/actor_agent.py tests/test_actor_agent.py
git commit -m "Add consensus observation and leg execution"
```

---

## Task 7: ActorAgent — fault and slot reopening

An agent can be told to fault. It publishes `fault` + `withdraw` for any slot it holds/bid on. Any agent observing a slot that lost its accepted holder publishes a `round+1` reopen `task_announce` scoped to the live task — with `announce_ts` derived from the triggering message's `ts` so concurrent reopens are byte-identical.

**Files:**
- Modify: `app/agents/actor_agent.py`
- Test: `tests/test_actor_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_actor_agent.py`:

```python
def test_faulted_agent_publishes_fault_and_withdraws():
    bus = NegotiationBus()
    agent = _ScoringAgent(_profile("arm-1", "arm"), bus, score=2.0)
    agent.start()
    try:
        bus.publish(_announce())
        time.sleep(2.2)  # arm-1 has won pickup + place
        agent.fault("test injected fault")
        time.sleep(0.3)
        faults = _published(bus, MessageType.FAULT, "arm-1")
        withdraws = _published(bus, MessageType.WITHDRAW, "arm-1")
        assert len(faults) == 1
        assert {w.slot_id for w in withdraws} == {"pickup", "place"}
        assert agent.state == AgentState.FAULTED
    finally:
        agent.stop()


def test_reopen_announce_is_published_when_a_held_slot_is_withdrawn():
    bus = NegotiationBus()
    # observer fills the transport slot; arm-1 fills the arm slots then faults.
    class _OmniAgent(_ScoringAgent):
        def _bid_score(self, slot):
            return self._score

    arm = _ScoringAgent(_profile("arm-1", "arm"), bus, score=2.0)
    observer = _OmniAgent(_profile("agv-1", "agv"), bus, score=5.0)
    arm.start()
    observer.start()
    try:
        bus.publish(_announce())
        time.sleep(2.2)
        arm.fault("boom")
        time.sleep(1.0)
        reopens = [
            m for m in bus.history()
            if m.type == MessageType.TASK_ANNOUNCE and m.negotiation_round == 1
        ]
        assert len(reopens) >= 1
    finally:
        arm.stop()
        observer.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actor_agent.py -k fault -v`
Expected: FAIL — `AttributeError: 'ActorAgent' object has no attribute 'fault'`

- [ ] **Step 3: Write minimal implementation**

In `app/agents/actor_agent.py`, in `__init__` add (after `self._consensus_announced = False`):

```python
        self._reopened_rounds: set[int] = set()  # rounds this agent reopened
```

Add the `fault` method and reopen detection. Add `fault` as a public method:

```python
    # ── failure ────────────────────────────────────────────────────────────

    def fault(self, reason: str) -> None:
        """Self-report a fault: withdraw every held/bid slot, publish fault.
        Thread-safe to call from outside the agent thread (only publishes)."""
        for slot_id in list(self._my_slots):
            self._publish(
                MessageType.WITHDRAW,
                slot_id=slot_id,
                payload={"reason": f"fault: {reason}"},
            )
        self._my_slots.clear()
        self._publish(MessageType.FAULT, payload={"reason": reason})
        self.state = AgentState.FAULTED
```

In `_handle`, after the `TASK_ANNOUNCE` branch, react to withdraw/fault by checking for an uncovered slot:

```python
        elif message.type in (MessageType.WITHDRAW, MessageType.FAULT):
            self._maybe_reopen(message)
```

Add `_maybe_reopen`:

```python
    def _maybe_reopen(self, trigger: Message) -> None:
        """If a slot has lost its accepted holder, any agent may publish the
        round+1 reopen. The reopen's announce_ts is derived from the trigger
        message's ts so concurrent reopens are byte-identical (idempotent)."""
        if self._task is None or self.state == AgentState.FAULTED:
            return
        if trigger.task_id != self._task_id or trigger.negotiation_round != self._round:
            return
        next_round = self._round + 1
        if next_round in self._reopened_rounds:
            return
        history = self.bus.history()
        required = {s["slot_id"] for s in self._task.get("slots", [])}
        accepts = accepted_actors(history, self._task_id, self._round)
        # Reopen only once the bid deadline has passed and a slot is uncovered
        # (before the deadline, evaluation will simply exclude the lost bid).
        if time.time() < self._task.get("bid_deadline_ts", 0):
            return
        if slots_complete(required, accepts):
            return
        self._reopened_rounds.add(next_round)
        base_ts = trigger.ts
        payload = dict(self._task)
        payload["announce_ts"] = base_ts
        payload["bid_deadline_ts"] = base_ts + BID_WINDOW_S
        payload["consensus_deadline_ts"] = base_ts + BID_WINDOW_S + CONSENSUS_WINDOW_S
        self.bus.publish(
            Message(
                type=MessageType.TASK_ANNOUNCE,
                task_id=self._task_id or "",
                negotiation_round=next_round,
                sender=self.actor_id,
                ts=base_ts,
                payload=payload,
            )
        )
```

Add `BID_WINDOW_S` and `CONSENSUS_WINDOW_S` to the `negotiation` import:

```python
from app.agents.negotiation import (
    BID_WINDOW_S,
    CONSENSUS_WINDOW_S,
    Message,
    MessageType,
    accepted_actors,
    public_winner,
    slots_complete,
    surviving_bids,
)
```

A reopened-round `task_announce` is handled by the existing `_on_task_announce` (it supersedes the current round and the agent re-bids), because `_handle`'s monotonic-round guard admits a higher round. Note `_on_task_announce` must reset per-round execution flags — update `_on_task_announce` so that after `self._round = message.negotiation_round` it also resets:

```python
        self._consensus_announced = False
        self._my_slots = []
        # _executed_legs is NOT reset — a leg already physically done stays done.
```

Insert those three lines in `_on_task_announce` right after `self.state = AgentState.IDLE`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actor_agent.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/actor_agent.py tests/test_actor_agent.py
git commit -m "Add fault handling and slot reopening"
```

---

## Task 8: ArmAgent

`ArmAgent` fills `arm`-kind slots whose zone is in its `reachable_zones`. Bid score is reach cost: `0.0` when it serves the slot's zone (Phase 1 arms each serve exactly one station, so the score is trivial but the bid is real).

**Files:**
- Create: `app/agents/arm_agent.py`
- Test: `tests/test_arm_agent.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_arm_agent.py`:

```python
"""Unit tests for ArmAgent."""

from app.agents.arm_agent import ArmAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile


def _arm(actor_id="arm-1", zones=("input_arm",)):
    return ArmAgent(
        CapabilityProfile(
            actor_id=actor_id, kind="arm", reachable_zones=list(zones)
        ),
        NegotiationBus(),
    )


def test_arm_bids_on_reachable_arm_slot():
    agent = _arm("arm-1", zones=["input_arm"])
    score = agent._bid_score(
        {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"}
    )
    assert score == 0.0


def test_arm_does_not_bid_on_unreachable_arm_slot():
    agent = _arm("arm-1", zones=["input_arm"])
    score = agent._bid_score(
        {"slot_id": "place", "kind": "arm", "zone": "output_arm"}
    )
    assert score is None


def test_arm_does_not_bid_on_agv_slot():
    agent = _arm("arm-1", zones=["input_arm"])
    score = agent._bid_score(
        {"slot_id": "transport", "kind": "agv",
         "from_zone": "input_arm", "to_zone": "output_arm"}
    )
    assert score is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_arm_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.arm_agent'`

- [ ] **Step 3: Write minimal implementation**

Create `app/agents/arm_agent.py`:

```python
"""ArmAgent — a robotic-arm actor in the decentralized negotiation runtime.

An arm fills `arm`-kind slots (pickup / place) for a station zone it can
reach. Phase 1 (Stage 2): leg execution is a recorded/simulated step; real
arm animation is wired in Stage 3.
"""

from __future__ import annotations

from typing import Optional

from app.agents.actor_agent import ActorAgent


class ArmAgent(ActorAgent):
    """Robotic-arm actor. Bids on reachable arm slots; bid score is reach
    cost (0.0 when it serves the slot's zone)."""

    def _bid_score(self, slot: dict) -> Optional[float]:
        if slot.get("kind") != "arm":
            return None
        zone = slot.get("zone")
        if zone is None or zone not in self.profile.reachable_zones:
            return None
        return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_arm_agent.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/arm_agent.py tests/test_arm_agent.py
git commit -m "Add ArmAgent"
```

---

## Task 9: AGVAgent

`AGVAgent` fills the `transport` slot. Bid score = grid distance from the AGV's current position to the slot's `from_zone` station dock (with equal AGV speed, nearest-to-pickup wins — `goal.md`'s intended default). The from-zone dock cell comes from `pipeline_config.get_stations()`.

**Files:**
- Create: `app/agents/agv_agent.py`
- Test: `tests/test_agv_agent.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agv_agent.py`:

```python
"""Unit tests for AGVAgent."""

from app.agents.agv_agent import AGVAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile


_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}


def _agv(actor_id="agv-1", position=(10, 3)):
    return AGVAgent(
        CapabilityProfile(
            actor_id=actor_id,
            kind="agv",
            reachable_zones=["input_arm", "output_arm"],
            position=[float(position[0]), float(position[1])],
        ),
        NegotiationBus(),
    )


def test_agv_does_not_bid_on_arm_slot():
    agent = _agv()
    assert agent._bid_score({"slot_id": "pickup", "kind": "arm"}) is None


def test_agv_bid_score_is_distance_to_pickup_dock(monkeypatch):
    from app.models import pipeline_config

    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    agent = _agv("agv-1", position=(10, 3))
    score = agent._bid_score(
        {"slot_id": "transport", "kind": "agv",
         "from_zone": "input_arm", "to_zone": "output_arm"}
    )
    # Manhattan distance from [10,3] to input_arm dock [2,1] = 8 + 2 = 10.
    assert score == 10.0


def test_closer_agv_scores_lower(monkeypatch):
    from app.models import pipeline_config

    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    near = _agv("agv-1", position=(3, 1))   # dist to [2,1] = 1
    far = _agv("agv-2", position=(10, 5))   # dist to [2,1] = 8 + 4 = 12
    slot = {"slot_id": "transport", "kind": "agv",
            "from_zone": "input_arm", "to_zone": "output_arm"}
    assert near._bid_score(slot) < far._bid_score(slot)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agv_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.agv_agent'`

- [ ] **Step 3: Write minimal implementation**

Create `app/agents/agv_agent.py`:

```python
"""AGVAgent — an Automated Guided Vehicle actor in the negotiation runtime.

An AGV fills the `transport` slot. Its bid score is the grid (Manhattan)
distance from its current cell to the pickup station's dock — with equal AGV
speed, the nearest-to-pickup AGV wins, which is goal.md's intended default.
Phase 1 (Stage 2): leg execution is a recorded/simulated step; real AGV
container commands are wired in Stage 3.
"""

from __future__ import annotations

from typing import Optional

from app.agents.actor_agent import ActorAgent


class AGVAgent(ActorAgent):
    """AGV actor. Bids on the transport slot; score = distance to pickup."""

    def _bid_score(self, slot: dict) -> Optional[float]:
        if slot.get("kind") != "agv":
            return None
        from_zone = slot.get("from_zone")
        dock = self._dock_cell(from_zone)
        if dock is None:
            return None
        col, row = self.profile.position[0], self.profile.position[1]
        return abs(col - dock[0]) + abs(row - dock[1])

    def _dock_cell(self, zone: Optional[str]) -> Optional[list[float]]:
        """The [col,row] dock cell of a station zone, from pipeline config."""
        if zone is None:
            return None
        from app.models import pipeline_config

        station = pipeline_config.get_stations().get(zone)
        if not station:
            return None
        cell = station.get("dock") or station.get("cell")
        if not cell:
            return None
        return [float(cell[0]), float(cell[1])]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agv_agent.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/agv_agent.py tests/test_agv_agent.py
git commit -m "Add AGVAgent"
```

---

## Task 10: agent_registry

`agent_registry` builds one agent per `load_profiles()` entry (arm → `ArmAgent`, agv → `AGVAgent`), starts them, and can stop them all. It is a process-level singleton like the bus.

**Files:**
- Create: `app/agents/agent_registry.py`
- Test: `tests/test_agent_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_registry.py`:

```python
"""Unit tests for the agent registry."""

import pytest

from app.agents import agent_registry
from app.agents.arm_agent import ArmAgent
from app.agents.agv_agent import AGVAgent
from app.agents.bus import reset_bus
from app.agents.capability import CapabilityProfile


@pytest.fixture(autouse=True)
def _clean():
    reset_bus()
    agent_registry.stop_agents()
    yield
    agent_registry.stop_agents()
    reset_bus()


def test_start_agents_builds_one_agent_per_profile(monkeypatch):
    profiles = [
        CapabilityProfile(actor_id="arm-1", kind="arm",
                          reachable_zones=["input_arm"]),
        CapabilityProfile(actor_id="agv-1", kind="agv",
                          reachable_zones=["input_arm", "output_arm"]),
    ]
    monkeypatch.setattr(
        "app.agents.agent_registry.load_profiles", lambda: profiles
    )
    agents = agent_registry.start_agents()
    try:
        by_id = {a.actor_id: a for a in agents}
        assert set(by_id) == {"arm-1", "agv-1"}
        assert isinstance(by_id["arm-1"], ArmAgent)
        assert isinstance(by_id["agv-1"], AGVAgent)
        assert all(a.is_running() for a in agents)
    finally:
        agent_registry.stop_agents()


def test_stop_agents_stops_every_agent(monkeypatch):
    profiles = [
        CapabilityProfile(actor_id="arm-1", kind="arm",
                          reachable_zones=["input_arm"]),
    ]
    monkeypatch.setattr(
        "app.agents.agent_registry.load_profiles", lambda: profiles
    )
    agents = agent_registry.start_agents()
    agent_registry.stop_agents()
    assert all(not a.is_running() for a in agents)


def test_get_agents_returns_running_set(monkeypatch):
    monkeypatch.setattr(
        "app.agents.agent_registry.load_profiles", lambda: []
    )
    agent_registry.start_agents()
    assert agent_registry.get_agents() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.agent_registry'`

- [ ] **Step 3: Write minimal implementation**

Create `app/agents/agent_registry.py`:

```python
"""Agent registry — instantiate and run one actor agent per configured actor.

Builds the live agent set from `capability.load_profiles()`: an `ArmAgent`
for every arm profile, an `AGVAgent` for every AGV profile. They all share
the process-wide negotiation bus. Stage 3 calls `start_agents()` at app
startup; Stage 2 exercises it from tests.
"""

from __future__ import annotations

import threading

from app.agents.actor_agent import ActorAgent
from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.bus import get_bus
from app.agents.capability import load_profiles

_lock = threading.Lock()
_agents: list[ActorAgent] = []


def start_agents() -> list[ActorAgent]:
    """Build, start, and return one agent per configured actor. Idempotent:
    if agents are already running, returns the existing set."""
    global _agents
    with _lock:
        if _agents:
            return list(_agents)
        bus = get_bus()
        built: list[ActorAgent] = []
        for profile in load_profiles():
            if profile.kind == "agv":
                built.append(AGVAgent(profile, bus))
            else:
                built.append(ArmAgent(profile, bus))
        for agent in built:
            agent.start()
        _agents = built
        return list(_agents)


def stop_agents() -> None:
    """Stop every running agent and clear the registry."""
    global _agents
    with _lock:
        for agent in _agents:
            agent.stop()
        _agents = []


def get_agents() -> list[ActorAgent]:
    """The currently-running agent set."""
    with _lock:
        return list(_agents)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agents/agent_registry.py tests/test_agent_registry.py
git commit -m "Add agent registry"
```

---

## Task 11: End-to-end negotiation integration tests

Drive a complete negotiation through real agents on a real bus and assert that consensus emerges, that the constraint selects the right AGV, that a fault reopens and reconverges, and that an unfillable task ends in failure. No mocks of agent logic — only the `task_announce` is injected.

**Files:**
- Create: `tests/test_negotiation_integration.py`

- [ ] **Step 1: Write the test (this task is test-only — the runtime already exists)**

Create `tests/test_negotiation_integration.py`:

```python
"""End-to-end integration tests for the decentralized negotiation runtime.

A task is announced on the bus; real ArmAgent / AGVAgent instances negotiate
to consensus with no central component. Only the task announcement is
injected — every bid, accept, withdraw, and consensus event is produced by
the agents themselves.
"""

import time

import pytest

from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType, accepted_actors


_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}


@pytest.fixture(autouse=True)
def _stations(monkeypatch):
    from app.models import pipeline_config

    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)


def _arm(bus, actor_id, zone):
    return ArmAgent(
        CapabilityProfile(actor_id=actor_id, kind="arm",
                          reachable_zones=[zone]),
        bus,
    )


def _agv(bus, actor_id, position):
    return AGVAgent(
        CapabilityProfile(
            actor_id=actor_id, kind="agv",
            reachable_zones=["input_arm", "output_arm"],
            position=[float(position[0]), float(position[1])],
        ),
        bus,
    )


def _announce(constraint=""):
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id="task-1",
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
            "bid_deadline_ts": now + 1.5,
            "consensus_deadline_ts": now + 3.0,
        },
    )


def _run(agents, announce, settle=4.0):
    for a in agents:
        a.start()
    try:
        announce_bus = agents[0].bus
        announce_bus.publish(announce)
        time.sleep(settle)
        return announce_bus.history()
    finally:
        for a in agents:
            a.stop()


def test_full_negotiation_reaches_consensus():
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
        _agv(bus, "agv-2", position=(10, 5)),
    ]
    history = _run(agents, _announce(constraint="shortest time"))
    accepts = accepted_actors(history, "task-1", 0)
    assert accepts == {
        "pickup": "arm-1",
        "transport": "agv-1",   # nearest to the input dock → lowest score
        "place": "arm-2",
    }
    assert any(m.type == MessageType.CONSENSUS_REACHED for m in history)


def test_constraint_selects_nearest_agv():
    bus = NegotiationBus()
    # agv-2 is nearest to the input dock [2,1] this time.
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(11, 7)),
        _agv(bus, "agv-2", position=(3, 1)),
    ]
    history = _run(agents, _announce(constraint="shortest distance"))
    accepts = accepted_actors(history, "task-1", 0)
    assert accepts["transport"] == "agv-2"


def test_fault_reopens_and_reconverges_to_a_new_agv():
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),   # wins round 0
        _agv(bus, "agv-2", position=(5, 1)),   # wins round 1 after agv-1 faults
    ]
    for a in agents:
        a.start()
    try:
        bus.publish(_announce(constraint="shortest time"))
        time.sleep(2.2)  # round 0 converged
        assert accepted_actors(bus.history(), "task-1", 0)["transport"] == "agv-1"
        agents[2].fault("injected")  # agv-1 faults
        time.sleep(4.0)              # reopen round 1 + reconverge
        round1 = accepted_actors(bus.history(), "task-1", 1)
        assert round1.get("transport") == "agv-2"
    finally:
        for a in agents:
            a.stop()


def test_unfillable_task_never_reaches_consensus():
    bus = NegotiationBus()
    # No AGV at all → the transport slot can never be filled.
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
    ]
    history = _run(agents, _announce(), settle=4.0)
    accepts = accepted_actors(history, "task-1", 0)
    assert "transport" not in accepts
    assert not any(m.type == MessageType.CONSENSUS_REACHED for m in history)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_negotiation_integration.py -v`
Expected: PASS (4 tests). These tests use real timing (the bid/consensus windows are 1.5s each) so each test takes a few seconds — that is expected.

If a test fails: do NOT weaken the assertion. The negotiation runtime is built in Tasks 3–10; a failure here means a real defect in that runtime. Report it with the failing assertion and the relevant `bus.history()` dump.

- [ ] **Step 3: Commit**

```bash
git add tests/test_negotiation_integration.py
git commit -m "Add end-to-end negotiation integration tests"
```

---

## Task 12: Stage 2 regression

**Files:** none (verification only)

- [ ] **Step 1: Run all Stage 2 test modules together**

Run: `python -m pytest tests/test_negotiation.py tests/test_negotiation_helpers.py tests/test_bus.py tests/test_capability.py tests/test_actor_agent.py tests/test_arm_agent.py tests/test_agv_agent.py tests/test_agent_registry.py tests/test_negotiation_integration.py -v`
Expected: all PASS.

- [ ] **Step 2: Run the full backend suite**

Run: `python -m pytest tests/ -q`
Expected: all the Stage 1 + Stage 2 agent tests PASS. The 5 pre-existing unrelated failures (`tests/test_warehouse_config.py` ×4, `tests/test_arm_execution.py` ×1) documented in Stage 1 may still be present — they are out of scope and were failing before this plan. Confirm no NEW failures were introduced by Stage 2.

- [ ] **Step 3: Commit (only if a new-code fix was required)**

If Steps 1–2 are clean, no commit is needed. If a Stage 2 defect was found and fixed:

```bash
git add app/agents/ tests/
git commit -m "Fix Stage 2 negotiation runtime issue found in regression"
```

---

## Self-Review

**1. Spec coverage (spec sections 4, 5, 6, 7):**
- 11 message types used; envelope round/version → Stage 1, consumed here (Tasks 4–7). ✅
- Capability broadcast on task_announce → Task 4. ✅
- Bidding, deterministic scores → Tasks 4, 8, 9. ✅
- Leaderless evaluation (every actor applies `public_winner` to the same public history) → Task 5. ✅
- Observational consensus (`consensus_reached` is a non-authoritative marker; each agent observes independently) → Task 6. ✅
- Execution barrier (consensus observed + holds a slot + no higher round) → Task 6 `_check_consensus`. ✅
- Slot derivation structure carried in the announce payload; agents never assign → Tasks 4–6. ✅
- Shared absolute deadlines (`bid_deadline_ts`, `consensus_deadline_ts` from the announce) → Tasks 5, 7. ✅
- Monotonic-round invariant → `_handle` guard, Task 4. ✅
- Idempotency: evaluate-once-per-round (`_evaluated_round`), consensus marker once (`_consensus_announced`), reopen once (`_reopened_rounds`) → Tasks 5–7. ✅
- `withdraw` supersedes accept/bid → `surviving_bids` / `accepted_actors`, Task 2. ✅
- No round ownership; reopen `announce_ts` derived from trigger `ts` → Task 7 `_maybe_reopen`. ✅
- Fault → withdraw → reopen → reconverge → Task 7 + integration Task 11. ✅
- Actor lifecycle states → `AgentState`, Tasks 3–7. ✅
- Broadcast-only (no actor-addressed messages); all via the bus → every `_publish` call. ✅
- `counter_offer` reserved, never emitted → no agent emits it. ✅
- Out of Stage 2 scope (correctly deferred): `/negotiate` route, `__init__.py` startup wiring, real container/arm I/O, deletions, frontend — all Stage 3. The `consensus_deadline_ts` → observed `failed` round status is partially exercised (Task 11 `test_unfillable_task_never_reaches_consensus` asserts no consensus); an explicit `RoundStatus.FAILED` surfacing is a Stage 3 concern when the route reports task outcome.

**2. Placeholder scan:** No "TBD"/"TODO". Every code step has complete code; every test step has complete test code. The `_handle`/`_check_deadlines` stubs in Task 3 are explicitly replaced with full implementations in Tasks 4–7 (incremental build of one class, each task shows the complete added code).

**3. Type consistency:** `ActorAgent`, `AgentState`, `_bid_score`, `_execute_leg`, `executed_legs`, `fault`, `_publish`, `_handle`, `_check_deadlines`, `_evaluate`, `_check_consensus`, `_maybe_reopen`, `_on_task_announce` — used identically across Tasks 3–10. `surviving_bids(history, task_id, negotiation_round, slot_id)` and `accepted_actors(history, task_id, negotiation_round)` — signatures consistent between Task 2's definition and Tasks 5–7's calls. `_bid_score(slot: dict) -> Optional[float]` consistent across base (Task 4), ArmAgent (Task 8), AGVAgent (Task 9). `start_agents`/`stop_agents`/`get_agents` consistent in Task 10 and its tests.

No issues found.

---

## Execution Handoff

Plan complete. This is Stage 2 of 3 — it produces the working decentralized negotiation runtime, verified by end-to-end integration tests. Stage 3 (wiring & cleanup: `/negotiate` route, `app/__init__.py` startup, deletion of `central_brain`/`plan_executor`/`message_bus`, honest frontend `BusPane`, and `PHASE_1_IMPLEMENTATION_AUDIT.md`) will be planned after Stage 2 lands.

Execution: subagent-driven, task-by-task, with spec-compliance then code-quality review after each task — continuing the workflow used for Stage 1.
