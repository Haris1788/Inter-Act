"""ActorAgent — one autonomous negotiating actor.

Each agent runs on its own thread, owns a CapabilityProfile, and coordinates
solely by publishing to and subscribing from the NegotiationBus. No external
component drives an agent: it owns its participation, bidding, acceptance,
withdrawal, failure, and re-entry.

Phase 4 Stage 3: ActorAgent is now a lifecycle container for per-task
TaskParticipant objects. `self._tasks` maps task_id -> TaskParticipant so
the agent can participate in N concurrent tasks independently.

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

from app.agents.bus import BusUnavailable, NegotiationBus
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
from app.agents.task_participant import TaskParticipant

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    """An agent's lifecycle state (agent-level, not per-task)."""

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
    and leg execution; the base owns the thread, inbox, lifecycle, and the
    per-task participant registry."""

    def __init__(
        self,
        profile: CapabilityProfile,
        bus: NegotiationBus,
        *,
        heartbeat_interval: float = 5.0,
    ) -> None:
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

        # Agent-level fault flag (not per-task).
        self._fault_requested = False
        self._fault_reason = ""

        # Per-task participant registry (Phase 4 Stage 3).
        # Maps task_id -> TaskParticipant. Single-task scenarios produce one entry.
        self._tasks: dict[str, TaskParticipant] = {}

        # Transient: set to the participant being executed, cleared after.
        # Used by subclasses (_valid() in arm/agv agents) to access the
        # participant without requiring a task_id argument on _execute_leg.
        # Safe: the event loop is single-threaded, so no concurrent access.
        self._current_participant: Optional[TaskParticipant] = None

        # Heartbeat (Phase 4 Stage 5).
        self._heartbeat_interval: float = heartbeat_interval
        self._heartbeat_thread: Optional[threading.Thread] = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to the bus, bootstrap from history, and start threads.

        Order of operations (idempotent and race-free under concurrent callers):
        1. subscribe_all — creates the consumer group (for RedisStreamsBus this
           is necessary before PEL drain can work).
        2. _bootstrap_from_history — rehydrate in-flight tasks from bus history
           so a restarted actor remembers accepted slots and completed legs.
        3. Spawn event-loop thread.
        4. Spawn heartbeat thread.
        """
        with self._lifecycle_lock:
            if self._running or self._thread is not None:
                return
            self._running = True
            self.bus.subscribe_all(self._enqueue, consumer_id=self.actor_id)
            # Bootstrap from history BEFORE starting the event loop so that
            # _tasks is populated before any incoming message is processed.
            self._bootstrap_from_history()
            self._thread = threading.Thread(
                target=self._run, name=f"agent-{self.actor_id}", daemon=True
            )
            self._thread.start()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"heartbeat-{self.actor_id}",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def stop(self) -> None:
        """Stop the event loop and heartbeat, then join both threads."""
        with self._lifecycle_lock:
            self._running = False
            thread = self._thread
            self._thread = None
            hb_thread = self._heartbeat_thread
            self._heartbeat_thread = None
        if thread is not None:
            thread.join(timeout=3.0)
            if thread.is_alive():
                logger.warning(
                    "agent %s thread did not stop within 3s", self.actor_id
                )
        if hb_thread is not None:
            hb_thread.join(timeout=3.0)
            if hb_thread.is_alive():
                logger.warning(
                    "agent %s heartbeat thread did not stop within 3s", self.actor_id
                )

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def messages_seen(self) -> int:
        """Count of bus messages this agent has processed (excludes its own)."""
        return self._messages_seen

    # ── bootstrap + heartbeat ──────────────────────────────────────────────

    def _safe_history(self) -> list:
        """Return bus.history(), falling back to [] on BusUnavailable."""
        try:
            return self.bus.history()
        except BusUnavailable:
            logger.warning(
                "agent %s: bus unavailable during history read; using []",
                self.actor_id,
            )
            return []

    def _bootstrap_from_history(self) -> None:
        """Rehydrate in-flight tasks from bus history on actor start.

        Called by start() after subscribe_all but BEFORE spawning the event
        loop thread, so _tasks is populated before any incoming message is
        processed.

        Algorithm:
        1. Fetch full bus history (BusUnavailable → empty history, harmless).
        2. Find every TASK_ANNOUNCE; group by task_id; keep the entry with the
           highest negotiation_round per task.
        3. For each such task: get-or-create a TaskParticipant. The participant's
           __post_init__ calls _rehydrate_executed_legs(history) so already-
           completed legs are known immediately and will not be re-executed.
        4. Rehydrate my_slots from accepted_actors(history, task_id, round)
           filtered to self.actor_id — a restarted actor remembers held slots.

        Phase 4 limitation: a faulted-then-restarted actor starts as IDLE.
        The prior FAULT message remains in bus history and peers have already
        observed it. The new actor instance treats itself as fresh — it will not
        re-enter the faulted task unless a reopen TASK_ANNOUNCE arrives. Full
        faulted-state persistence (so the actor can resume FAULTED across
        restarts) is deferred to Phase 5+.

        A restarted actor with no in-flight tasks gets an empty _tasks dict
        (harmless — the next TASK_ANNOUNCE creates a fresh participant).
        """
        history = self._safe_history()
        if not history:
            return

        # Step 1: find highest-round TASK_ANNOUNCE per task.
        best_announce: dict[str, Message] = {}
        for msg in history:
            if msg.type != MessageType.TASK_ANNOUNCE:
                continue
            task_id = msg.task_id
            existing = best_announce.get(task_id)
            if existing is None or msg.negotiation_round > existing.negotiation_round:
                best_announce[task_id] = msg

        # Step 2: hydrate participants from those announces.
        for task_id, announce in best_announce.items():
            if task_id in self._tasks:
                # Already created (shouldn't happen in start(), but be safe).
                participant = self._tasks[task_id]
            else:
                participant = TaskParticipant(
                    actor_id=self.actor_id,
                    bus=self.bus,
                    bid_score_fn=self._bid_score,
                    execute_leg_fn=self._execute_leg,
                    fault_requested_fn=lambda: self._fault_requested,
                    capability_broadcast_fn=self._broadcast_capability,
                    task_id=task_id,
                    round=announce.negotiation_round,
                    task=dict(announce.payload),
                )
                self._tasks[task_id] = participant

            # Step 3: rehydrate my_slots from accepted_actors filtered to this actor.
            accepts = accepted_actors(history, task_id, announce.negotiation_round)
            my_slots = [
                slot_id for slot_id, actor in accepts.items()
                if actor == self.actor_id
            ]
            participant.my_slots = my_slots

            # Step 4: mark the round as already-evaluated if we have any
            # ACCEPT or WITHDRAW from this actor for this (task_id, round) in
            # history. Without this, _check_deadlines sees evaluated_round=-1
            # and re-evaluates after every restart, re-publishing duplicate
            # ACCEPTs (Bug #6 from real-LLM verification).
            already_evaluated = any(
                m.type in (MessageType.ACCEPT, MessageType.WITHDRAW)
                and m.sender == self.actor_id
                and m.task_id == task_id
                and m.negotiation_round == announce.negotiation_round
                for m in history
            )
            if already_evaluated:
                participant.evaluated_round = announce.negotiation_round

            # Step 5: rehydrate consensus_announced / failure_announced from history
            # so we do not republish observational markers we've already emitted.
            participant.consensus_announced = any(
                m.type == MessageType.CONSENSUS_REACHED
                and m.sender == self.actor_id
                and m.task_id == task_id
                and m.negotiation_round == announce.negotiation_round
                for m in history
            )
            participant.failure_announced = any(
                m.type == MessageType.TASK_FAILED
                and m.sender == self.actor_id
                and m.task_id == task_id
                and m.negotiation_round == announce.negotiation_round
                for m in history
            )

            logger.debug(
                "agent %s: bootstrapped task %s round=%d my_slots=%s executed_legs=%s",
                self.actor_id,
                task_id,
                announce.negotiation_round,
                my_slots,
                participant.executed_legs,
            )

    def _heartbeat_loop(self) -> None:
        """Publish AVAILABILITY_UPDATE at _heartbeat_interval seconds.

        Observational only — heartbeats are NEVER consulted for consensus.
        BusUnavailable is caught and silently dropped; the next interval retries.
        Stops when self._running becomes False.
        """
        while self._running:
            try:
                self.bus.publish(
                    Message(
                        type=MessageType.AVAILABILITY_UPDATE,
                        task_id="",
                        negotiation_round=0,
                        sender=self.actor_id,
                        payload={"ts": time.time(), "state": self.state.value, "heartbeat": True},
                    )
                )
            except BusUnavailable:
                logger.debug(
                    "agent %s: bus unavailable during heartbeat; skipping tick",
                    self.actor_id,
                )
            time.sleep(self._heartbeat_interval)

    def _safe_publish(self, message: Message) -> bool:
        """Publish a message, catching BusUnavailable. Returns True on success."""
        try:
            self.bus.publish(message)
            return True
        except BusUnavailable:
            logger.warning(
                "agent %s: bus unavailable; dropping %s publish",
                self.actor_id,
                message.type.value,
            )
            return False

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
        """Wake at the soonest pending deadline across all participants, else tick slowly."""
        now = time.time()
        targets = []
        for participant in self._tasks.values():
            dl = participant.next_deadline()
            if dl is not None:
                targets.append(dl)
        soonest = min((t - now for t in targets), default=_IDLE_TICK_S)
        return max(0.0, min(soonest, _IDLE_TICK_S))

    # ── handlers ───────────────────────────────────────────────────────────

    def _handle(self, message: Message) -> None:
        """Route incoming bus messages to the appropriate participant."""
        task_id = message.task_id

        if message.type == MessageType.TASK_ANNOUNCE:
            self._on_task_announce(message)
            return

        # For WITHDRAW / FAULT: route to the matching participant's maybe_reopen.
        if message.type in (MessageType.WITHDRAW, MessageType.FAULT):
            participant = self._tasks.get(task_id)
            if participant is not None and self.state != AgentState.FAULTED:
                # Monotonic-round guard: ignore older-round triggers.
                if message.negotiation_round >= participant.round:
                    participant.maybe_reopen(message)

    def _on_task_announce(self, message: Message) -> None:
        """Handle TASK_ANNOUNCE: get-or-create a participant and delegate."""
        # A faulted agent stays out of all negotiations — it does not rejoin
        # on a reopen (Phase 1/Phase 4 invariant: no agent-level recover).
        if self.state == AgentState.FAULTED:
            return

        task_id = message.task_id
        participant = self._tasks.get(task_id)

        if participant is None:
            # New task for this agent — create a participant.
            participant = self._make_participant(task_id, message)
            self._tasks[task_id] = participant
            # Broadcast capability then place bids.
            self._broadcast_capability(task_id, message.negotiation_round)
            bid_placed = self._place_bids_for(participant)
            if bid_placed:
                self.state = AgentState.BIDDING
            # else: state stays IDLE (agent cannot fill any slot in this task)
        else:
            # Existing task — check monotonic-round guard.
            if message.negotiation_round <= participant.round:
                return
            # Higher round: adopt it. Broadcast capability then re-bid.
            participant.round = message.negotiation_round
            participant.task = dict(message.payload)
            participant.consensus_announced = False
            participant.failure_announced = False
            participant.my_slots = []
            # executed_legs preserved — physical work already done.
            self._broadcast_capability(task_id, message.negotiation_round)
            bid_placed = self._place_bids_for(participant)
            if bid_placed:
                self.state = AgentState.BIDDING

    def _make_participant(self, task_id: str, message: Message) -> TaskParticipant:
        """Create a fresh TaskParticipant for a new task."""
        return TaskParticipant(
            actor_id=self.actor_id,
            bus=self.bus,
            bid_score_fn=self._bid_score,
            execute_leg_fn=self._execute_leg,
            fault_requested_fn=lambda: self._fault_requested,
            capability_broadcast_fn=self._broadcast_capability,
            task_id=task_id,
            round=message.negotiation_round,
            task=dict(message.payload),
        )

    def _place_bids_for(self, participant: TaskParticipant) -> bool:
        """Place bids for all slots in the participant's task. Returns True if any bid placed."""
        slots = (participant.task or {}).get("slots", [])
        bid_placed = False
        for slot in slots:
            score = self._bid_score(slot)
            if score is None:
                continue
            published = self._safe_publish(
                Message(
                    type=MessageType.BID,
                    task_id=participant.task_id,
                    negotiation_round=participant.round,
                    sender=self.actor_id,
                    slot_id=slot["slot_id"],
                    payload={
                        "score": score,
                        "basis": self.profile.kind,
                        # Phase 5 Stage 4: peers need capacity + reach_px to
                        # re-derive winners from bus history (leaderless invariant).
                        "capacity": self.profile.capacity,
                        "reach_px": self.profile.reach_px,
                    },
                )
            )
            if published:
                bid_placed = True
        return bid_placed

    def _broadcast_capability(self, task_id: str, negotiation_round: int) -> None:
        """Publish CAPABILITY_BROADCAST for a given task/round."""
        self._safe_publish(
            Message(
                type=MessageType.CAPABILITY_BROADCAST,
                task_id=task_id,
                negotiation_round=negotiation_round,
                sender=self.actor_id,
                payload=self.profile.to_dict(),
            )
        )

    def _bid_score(self, slot: dict) -> Optional[float]:
        """Return this agent's deterministic bid score for `slot`, or None if
        the agent cannot fill it. Base agent never bids."""
        return None

    def _check_deadlines(self) -> None:
        """Iterate all participants and trigger deadline-driven actions."""
        if self.state == AgentState.FAULTED:
            return
        now = time.time()
        for participant in list(self._tasks.values()):
            if participant.task is None:
                continue
            # Bid evaluation deadline.
            if (
                participant.evaluated_round < participant.round
                and now >= participant.task.get("bid_deadline_ts", float("inf"))
            ):
                self._evaluate_participant(participant)

            # Consensus check (triggers execution if complete).
            self._check_consensus_for(participant)

            # Failure check.
            participant.check_failure()

    def _evaluate_participant(self, participant: TaskParticipant) -> None:
        """Leaderless evaluation for one participant.

        For every slot: apply the public winner rule.
        Won -> ACCEPT published + slot recorded + state -> WON.
        Lost (but bid) -> WITHDRAW published.
        """
        from app.agents.negotiation import surviving_bids, public_winner
        participant.evaluated_round = participant.round
        history = self._safe_history()
        constraint = (participant.task or {}).get("constraint", "")
        won_any = False
        for slot in (participant.task or {}).get("slots", []):
            slot_id = slot["slot_id"]
            bids = surviving_bids(history, participant.task_id, participant.round, slot_id)
            i_bid = any(b.sender == self.actor_id for b in bids)
            if not i_bid:
                continue
            winner = public_winner(bids, constraint)
            if winner == self.actor_id:
                self._safe_publish(
                    Message(
                        type=MessageType.ACCEPT,
                        task_id=participant.task_id,
                        negotiation_round=participant.round,
                        sender=self.actor_id,
                        slot_id=slot_id,
                    )
                )
                participant.my_slots.append(slot_id)
                won_any = True
            else:
                self._safe_publish(
                    Message(
                        type=MessageType.WITHDRAW,
                        task_id=participant.task_id,
                        negotiation_round=participant.round,
                        sender=self.actor_id,
                        slot_id=slot_id,
                        payload={"reason": "lost evaluation"},
                    )
                )
        if won_any:
            self.state = AgentState.WON
        # If we didn't win any slots in this task but already have WON/EXECUTING
        # state from another task, don't downgrade to IDLE.
        elif self.state not in (AgentState.WON, AgentState.EXECUTING):
            self.state = AgentState.IDLE

    def _check_consensus_for(self, participant: TaskParticipant) -> None:
        """Observe consensus for one participant, then run held legs.

        Note: we do NOT gate on agent-level state here. Agent state is a
        diagnostic aggregate — per-task consensus must be evaluated independently
        of other tasks' execution status. Participant-level flags
        (consensus_announced) are the idempotency guard.
        """
        from app.agents.negotiation import accepted_actors, slots_complete
        if participant.task is None:
            return
        if self.state == AgentState.FAULTED:
            return
        history = self._safe_history()
        required = {s["slot_id"] for s in participant.task.get("slots", [])}
        accepts = accepted_actors(history, participant.task_id, participant.round)
        if not slots_complete(required, accepts):
            return

        # Observational consensus marker — idempotent, non-authoritative.
        if not participant.consensus_announced:
            participant.consensus_announced = True
            self._safe_publish(
                Message(
                    type=MessageType.CONSENSUS_REACHED,
                    task_id=participant.task_id,
                    negotiation_round=participant.round,
                    sender=self.actor_id,
                    payload={"round": participant.round},
                )
            )

        if not participant.my_slots:
            return  # consensus reached but this agent holds nothing

        self.state = AgentState.EXECUTING
        all_complete = True
        for slot_id in participant.my_slots:
            if slot_id not in participant.executed_legs:
                # Store the participant so _execute_leg implementations can
                # access task_id/round/execution_valid without needing an extra arg.
                # The event loop is single-threaded: no concurrent access.
                self._current_participant = participant
                try:
                    self._publish_leg_event(MessageType.LEG_STARTED, slot_id, participant)
                    completed = self._execute_leg(slot_id)
                    evt = MessageType.LEG_COMPLETED if completed else MessageType.LEG_HALTED
                    self._publish_leg_event(evt, slot_id, participant)
                finally:
                    self._current_participant = None
                if completed:
                    # Keep the in-process append so the local view immediately
                    # matches what _rehydrate_executed_legs would compute from
                    # the LEG_COMPLETED just published. The publish is synchronous
                    # (before this append), so history already reflects the
                    # completion before any other agent's bus.history() read.
                    participant.executed_legs.append(slot_id)
                else:
                    all_complete = False

        if all_complete:
            # Check if ALL participants' slots are complete before moving to DONE.
            all_tasks_done = all(
                all(s in p.executed_legs for s in p.my_slots)
                for p in self._tasks.values()
                if p.my_slots
            )
            if all_tasks_done:
                self.state = AgentState.DONE

    def _publish_leg_event(
        self,
        msg_type: MessageType,
        slot_id: str,
        participant: "TaskParticipant",
    ) -> None:
        """Publish an observational LEG_* transition marker for one slot.

        LEG_STARTED / LEG_COMPLETED / LEG_HALTED are audit-trail messages:
        they record what this actor did, never instruct other actors. No
        other agent changes its decisions based on these messages.

        The payload mirrors the slot/task/round context so the bus log is
        self-contained for replay and state rehydration.
        """
        self._safe_publish(
            Message(
                type=msg_type,
                task_id=participant.task_id,
                negotiation_round=participant.round,
                sender=self.actor_id,
                slot_id=slot_id,
                payload={
                    "slot_id": slot_id,
                    "task_id": participant.task_id,
                    "round": participant.round,
                },
            )
        )

    def fault(self, reason: str) -> None:
        """Request that this agent fault. Thread-safe — callable from any
        thread. It only sets a flag and wakes the event loop; the agent's own
        thread performs the withdrawals, FAULT publication, and state change."""
        self._fault_reason = reason
        self._fault_requested = True
        self._inbox.put(None)  # wake the event loop promptly

    def _do_fault(self, reason: str) -> None:
        """Perform the fault — runs on the agent thread only. Withdraws every
        held slot across every active task, publishes FAULT per task, then
        transitions to FAULTED.

        A faulted agent does not rejoin new task adoptions (Phase 3/4 invariant).
        """
        for task_id, participant in list(self._tasks.items()):
            for slot_id in list(participant.my_slots):
                self._safe_publish(
                    Message(
                        type=MessageType.WITHDRAW,
                        task_id=task_id,
                        negotiation_round=participant.round,
                        sender=self.actor_id,
                        slot_id=slot_id,
                        payload={"reason": f"fault: {reason}"},
                    )
                )
            participant.my_slots.clear()
            self._safe_publish(
                Message(
                    type=MessageType.FAULT,
                    task_id=task_id,
                    negotiation_round=participant.round,
                    sender=self.actor_id,
                    payload={"reason": reason},
                )
            )
        self.state = AgentState.FAULTED

    def _execute_leg(self, slot_id: str, task_id: str = "", round: int = -1) -> bool:
        """Run this agent's physical leg. Stage 3+ subclasses override.

        Base implementation: recorded/simulated stub — always returns True.

        Default args preserve backward compatibility: tests that monkeypatch
        `_execute_leg(self, slot_id)` with the old single-arg signature will
        still work; the new callers pass all three positional args.

        Returns True when the leg completed fully, False when halted early.
        """
        logger.debug(
            "agent %s executing leg %s task=%s round=%s (stub)",
            self.actor_id, slot_id, task_id, round,
        )
        return True

    def executed_legs(self) -> list[str]:
        """All legs executed across all tasks (union). Kept for test compat."""
        result: list[str] = []
        for p in self._tasks.values():
            result.extend(p.executed_legs)
        return result
