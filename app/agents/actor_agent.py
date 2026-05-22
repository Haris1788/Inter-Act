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
    BID_WINDOW_S,
    CONSENSUS_WINDOW_S,
    Message,
    MessageType,
    accepted_actors,
    public_winner,
    slots_complete,
    surviving_bids,
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

        self._inbox: queue.Queue = queue.Queue()  # Messages + None wakeup sentinels
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._messages_seen = 0
        self._lifecycle_lock = threading.Lock()

        # Active-task tracking. Phase 1: one task at a time.
        self._task_id: Optional[str] = None
        self._round: int = -1
        self._task: Optional[dict] = None  # the task_announce payload
        self._evaluated_round: int = -1    # highest round this agent evaluated
        self._my_slots: list[str] = []       # slots this agent has accepted
        self._executed_legs: list[str] = []  # slots this agent has executed
        self._consensus_announced = False
        self._failure_announced = False
        self._reopened_rounds: set[int] = set()  # rounds this agent reopened
        self._fault_requested = False
        self._fault_reason = ""

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
            if self._fault_requested:
                self._fault_requested = False
                try:
                    self._do_fault(self._fault_reason)
                except Exception:
                    logger.exception(
                        "agent %s _do_fault failed", self.actor_id
                    )

    def _next_wakeup(self) -> float:
        """Wake the loop at the next pending deadline, else tick slowly."""
        if self._task is None:
            return _IDLE_TICK_S
        now = time.time()
        targets = []
        if self._evaluated_round < self._round:
            deadline = self._task.get("bid_deadline_ts")
            if deadline is not None:
                targets.append(deadline)
        if not self._consensus_announced and not self._failure_announced:
            consensus_deadline = self._task.get("consensus_deadline_ts")
            if consensus_deadline is not None:
                targets.append(consensus_deadline)
        soonest = min((t - now for t in targets), default=_IDLE_TICK_S)
        return max(0.0, min(soonest, _IDLE_TICK_S))

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
        elif message.type in (MessageType.WITHDRAW, MessageType.FAULT):
            self._maybe_reopen(message)

    def _on_task_announce(self, message: Message) -> None:
        # A faulted agent stays out of the negotiation — it does not rejoin
        # on a reopen (Phase 1 has no agent-level recover).
        if self.state == AgentState.FAULTED:
            return
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

        self._consensus_announced = False
        self._failure_announced = False
        self._my_slots = []
        # _executed_legs is NOT reset — a leg already physically done stays done.

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
        if self._task is None:
            return
        now = time.time()
        if (
            self._evaluated_round < self._round
            and now >= self._task.get("bid_deadline_ts", float("inf"))
        ):
            self._evaluate()
        # _evaluate (above) publishes ACCEPTs to the bus before _check_consensus
        # reads history — relies on synchronous in-process bus delivery.
        self._check_consensus()
        self._check_failure()

    def _evaluate(self) -> None:
        """Leaderless evaluation: for every slot, apply the public rule to the
        public bid set. Win -> accept; lost (but bid) -> withdraw."""
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
                self._my_slots.append(slot_id)
                won_any = True
            else:
                self._publish(
                    MessageType.WITHDRAW,
                    slot_id=slot_id,
                    payload={"reason": "lost evaluation"},
                )
        self.state = AgentState.WON if won_any else AgentState.IDLE

    def executed_legs(self) -> list[str]:
        return list(self._executed_legs)

    def fault(self, reason: str) -> None:
        """Request that this agent fault. Thread-safe — callable from any
        thread. It only sets a flag and wakes the event loop; the agent's own
        thread performs the withdrawals, FAULT publication, and state change."""
        self._fault_reason = reason
        self._fault_requested = True
        self._inbox.put(None)  # wake the event loop promptly

    def _do_fault(self, reason: str) -> None:
        """Perform the fault — runs on the agent thread only. Withdraws every
        held slot, publishes a FAULT, transitions to FAULTED."""
        for slot_id in list(self._my_slots):
            self._publish(
                MessageType.WITHDRAW,
                slot_id=slot_id,
                payload={"reason": f"fault: {reason}"},
            )
        self._my_slots.clear()
        self._publish(MessageType.FAULT, payload={"reason": reason})
        self.state = AgentState.FAULTED

    def _check_consensus(self) -> None:
        """Observe (never decide) the consensus condition, then run our leg.

        Execution barrier: we execute only when we locally observe consensus,
        we hold at least one accepted slot, and no higher round has appeared.
        """
        if self._task is None or self.state in (
            AgentState.BIDDING,
            AgentState.DONE,
            AgentState.FAULTED,
        ):
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
        all_complete = True
        for slot_id in self._my_slots:
            if slot_id not in self._executed_legs:
                completed = self._execute_leg(slot_id)
                if completed:
                    self._executed_legs.append(slot_id)
                else:
                    # Leg halted before completion (superseded round / fault).
                    # Do NOT record it — if this slot is re-won on a reopened
                    # round the agent must re-execute it.
                    all_complete = False
        if all_complete:
            self.state = AgentState.DONE
        # else: leave state as-is; a superseding _on_task_announce or _do_fault
        # will set the next state.

    def _check_failure(self) -> None:
        """Observe (never decide) the failure condition: consensus deadline
        expired with no viable bids for some slot. Idempotent and decentralised
        — mirrors how _check_consensus works, purely from bus-visible data."""
        if self._task is None or self._consensus_announced or self._failure_announced:
            return
        if self.state == AgentState.FAULTED:
            return
        if time.time() < self._task.get("consensus_deadline_ts", float("inf")):
            return
        # Double-check: if consensus is actually complete now, do not fail.
        history = self.bus.history()
        required = {s["slot_id"] for s in self._task.get("slots", [])}
        accepts = accepted_actors(history, self._task_id, self._round)
        if slots_complete(required, accepts):
            return
        # A slot reopen for the next round already handles a lost-holder case.
        if (self._round + 1) in self._reopened_rounds:
            return
        # Determine reason: any required slot with zero surviving bids → unfillable.
        reason = "consensus deadline expired"
        for slot_id in required:
            if not surviving_bids(history, self._task_id, self._round, slot_id):
                reason = "no viable bids"
                break
        self._failure_announced = True
        self._publish(
            MessageType.TASK_FAILED,
            payload={"round": self._round, "reason": reason},
        )

    def _execution_valid(self, slot_id: str) -> bool:
        """Execution barrier — re-derive validity from a fresh bus snapshot.

        Returns True only when ALL of:
        1. Consensus is still complete (all required slots still accepted).
        2. This agent still holds `slot_id` (no superseding withdraw/fault).
        3. No higher round has been announced for this task_id.
        4. No fault was requested on this agent (`_fault_requested`).

        Called before each leg and between every motion chunk so a superseding
        round or an agent fault halts execution promptly.
        """
        if self._fault_requested:
            return False
        if self._task is None or self._task_id is None:
            return False

        history = self.bus.history()
        required = {s["slot_id"] for s in self._task.get("slots", [])}
        accepts = accepted_actors(history, self._task_id, self._round)

        # 1. Consensus still complete.
        if not slots_complete(required, accepts):
            return False

        # 2. We still hold our slot.
        if accepts.get(slot_id) != self.actor_id:
            return False

        # 3. No higher round announced for this task.
        for m in history:
            if (
                m.type == MessageType.TASK_ANNOUNCE
                and m.task_id == self._task_id
                and m.negotiation_round > self._round
            ):
                return False

        return True

    def _execute_leg(self, slot_id: str) -> bool:
        """Run this agent's physical leg. Stage 2: a recorded/simulated step.
        Stage 3 wires real AGV-container HTTP and arm animation here.

        Returns True when the leg completed fully, False when it halted early
        (superseded round, fault, or any mid-execution abort). Callers in
        _check_consensus only record the leg as done when True is returned.
        """
        logger.debug(
            "agent %s executing leg %s (Stage 2 recorded stub)",
            self.actor_id,
            slot_id,
        )
        return True

    def _maybe_reopen(self, trigger: Message) -> None:
        """If a slot that WAS accepted has lost its holder (a fault/withdraw
        after the bid deadline), any agent may publish the round+1 reopen.
        The reopen's announce_ts is the trigger message's ts, so concurrent
        reopens from different agents are byte-identical (idempotent). The
        publishing agent adopts the new round locally — the bus does not echo
        an agent its own messages."""
        if self._task is None or self.state == AgentState.FAULTED:
            return
        if (
            trigger.task_id != self._task_id
            or trigger.negotiation_round != self._round
        ):
            return
        next_round = self._round + 1
        if next_round in self._reopened_rounds:
            return
        # Before the bid deadline, evaluation already excludes a lost bid, so
        # no reopen is needed. A missing deadline means never reopen.
        if time.time() < self._task.get("bid_deadline_ts", float("inf")):
            return
        history = self.bus.history()
        # Reopen only when a slot that was ACCEPTED has since lost its holder
        # (the holder withdrew/faulted). A slot that simply never received a
        # bid is an unfillable task, not a fault — reopening would not help.
        ever_accepted = {
            m.slot_id
            for m in history
            if m.type == MessageType.ACCEPT
            and m.task_id == self._task_id
            and m.negotiation_round == self._round
            and m.slot_id is not None
        }
        currently_accepted = set(
            accepted_actors(history, self._task_id, self._round)
        )
        if not (ever_accepted - currently_accepted):
            return  # no previously-held slot was lost — nothing to reopen

        self._reopened_rounds.add(next_round)
        base_ts = trigger.ts
        payload = dict(self._task)
        payload["announce_ts"] = base_ts
        payload["bid_deadline_ts"] = base_ts + BID_WINDOW_S
        payload["consensus_deadline_ts"] = (
            base_ts + BID_WINDOW_S + CONSENSUS_WINDOW_S
        )
        reopen = Message(
            type=MessageType.TASK_ANNOUNCE,
            task_id=self._task_id or "",
            negotiation_round=next_round,
            sender=self.actor_id,
            ts=base_ts,
            payload=payload,
        )
        self.bus.publish(reopen)
        # The bus does not echo own messages back, so adopt the round locally.
        self._on_task_announce(reopen)
