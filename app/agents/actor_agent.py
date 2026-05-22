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

import logging
import queue
import threading
import time
from enum import Enum
from typing import Optional

from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import (
    Message,
    MessageType,
)

logger = logging.getLogger(__name__)


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
        # `state` is read cross-thread by tests/registry for diagnostics only;
        # CPython reference assignment is atomic, so observational reads are
        # safe. It is never used for cross-thread control flow.
        self.state: AgentState = AgentState.IDLE

        self._inbox: queue.Queue[Message] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._messages_seen = 0
        self._lifecycle_lock = threading.Lock()

        # Active-task tracking. Phase 1: one task at a time.
        self._task_id: Optional[str] = None
        self._round: int = -1
        self._task: Optional[dict] = None  # the task_announce payload
        self._evaluated_round: int = -1    # highest round this agent evaluated

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to the bus and start the event-loop thread. Idempotent
        and race-free under concurrent callers."""
        with self._lifecycle_lock:
            if self._running or self._thread is not None:
                return
            self._running = True
            self.bus.subscribe_all(self._enqueue)
            self._thread = threading.Thread(
                target=self._run, name=f"agent-{self.actor_id}", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the event loop and join the thread. Logs a warning if the
        thread does not exit within the join timeout."""
        with self._lifecycle_lock:
            self._running = False
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=3.0)
            if thread.is_alive():
                logger.warning(
                    "agent %s thread did not stop within 3s", self.actor_id
                )

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
                # Single writer (this thread); GIL makes the int read on other threads safe.
                self._messages_seen += 1
                try:
                    self._handle(message)
                except Exception:
                    logger.exception("agent %s _handle failed", self.actor_id)
            try:
                self._check_deadlines()
            except Exception:
                logger.exception(
                    "agent %s _check_deadlines failed", self.actor_id
                )

    def _next_wakeup(self) -> float:
        """Seconds until the loop should wake even with no message. Task 6
        overrides the effective wakeup via pending deadlines; the base just
        ticks slowly."""
        return _IDLE_TICK_S

    # ── handlers ───────────────────────────────────────────────────────────

    def _handle(self, message: Message) -> None:
        # Monotonic-round invariant: ignore anything older than the round we
        # are already on for this task.
        if (
            message.task_id == self._task_id
            and message.negotiation_round < self._round
        ):
            return
        # _on_task_announce applies a stricter (<=) guard so a duplicate
        # announce for the round we are already on is ignored.
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

    def _bid_score(self, slot: dict) -> Optional[float]:
        """Return this agent's deterministic bid score for `slot`, or None if
        the agent cannot fill it. Base agent never bids."""
        return None

    def _check_deadlines(self) -> None:
        """Act on elapsed negotiation deadlines. Base: no-op (Task 6 fills)."""
