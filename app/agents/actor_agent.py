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
