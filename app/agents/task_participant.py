"""TaskParticipant — per-task state machine for a negotiating actor.

Encapsulates all state that belongs to a single task negotiation: round,
accepted slots, executed legs, reopen tracking, deadlines, and halt flag.
Extracted from ActorAgent to support concurrent negotiations (Phase 4 Stage 3).

All decision logic reads from `bus.history()` only — the leaderless rule and
pure-functional consensus are preserved exactly as they were on ActorAgent.

Phase 5 hook: `replay_cursor` (XID checkpoint) is declared now but unused.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

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

if TYPE_CHECKING:
    from app.agents.bus import NegotiationBus

logger = logging.getLogger(__name__)


@dataclass
class TaskParticipant:
    """Per-task negotiation state for one actor.

    Owns all mutable state that varies per task:
        - task payload (slots, deadlines)
        - current round
        - which rounds have been evaluated
        - which slots this actor accepted
        - which legs this actor executed
        - which reopened rounds were issued
        - whether consensus/failure was announced for this round
        - a halt flag (set when execution is aborted — reserved for Phase 5)
        - replay_cursor (Phase 5 XID checkpoint — declared now, unused)

    References held:
        - `actor_id` / `bus`              — for publishing messages
        - `bid_score_fn`                  — actor's _bid_score (slot -> Optional[float])
        - `execute_leg_fn`                — actor's _execute_leg(slot_id, task_id, round) -> bool
        - `fault_requested_fn`            — callable returning bool; True if agent wants to fault
        - `capability_broadcast_fn`       — callable(task_id, round) -> None; broadcasts CAPABILITY
          Called on self-reopen so the agent announces its capability for the new round.
    """

    actor_id: str
    bus: "NegotiationBus"
    bid_score_fn: Callable[[dict], Optional[float]]
    execute_leg_fn: Callable[[str, str, int], bool]   # (slot_id, task_id, round) -> bool
    fault_requested_fn: Callable[[], bool]             # () -> bool
    capability_broadcast_fn: Callable[[str, int], None]  # (task_id, round) -> None

    task_id: str
    round: int
    task: dict                                         # task_announce payload
    evaluated_round: int = -1
    my_slots: list = field(default_factory=list)       # list[str]
    executed_legs: list = field(default_factory=list)  # list[str] — derived view of bus log;
    # mutated ONLY by _rehydrate_executed_legs and by _check_consensus_for after a
    # LEG_COMPLETED is published (so the in-process append matches what rehydration
    # would compute from bus.history()).
    reopened_rounds: set = field(default_factory=set)  # set[int]
    consensus_announced: bool = False
    failure_announced: bool = False
    halt_flag: bool = False
    replay_cursor: Optional[str] = None               # Phase 5 — declared now, unused

    def __post_init__(self) -> None:
        """Rehydrate executed_legs from bus history on participant creation.

        Handles the restart-recovery path: if the bus already contains
        LEG_COMPLETED messages for this actor+task (e.g. after a crash and
        reconnect), executed_legs is populated immediately so _check_consensus_for
        skips those slots rather than re-executing them.
        """
        self._rehydrate_executed_legs(self.bus.history())

    # ── Public methods (called by ActorAgent dispatch) ─────────────────────

    def on_reopen_announce(self, message: Message) -> None:
        """Adopt a new (higher) round from a self-published TASK_ANNOUNCE reopen.

        Used exclusively in maybe_reopen to adopt the new round locally after the
        agent publishes a reopen (bus does not echo own messages back). Resets
        per-round state and places bids. executed_legs is NOT reset — it is
        re-derived from bus history so any LEG_COMPLETED records from prior rounds
        are preserved and will not be re-executed.
        """
        if message.negotiation_round <= self.round:
            return
        self.round = message.negotiation_round
        self.task = dict(message.payload)
        self.consensus_announced = False
        self.failure_announced = False
        self.my_slots = []
        # Re-derive executed_legs from bus history rather than simply preserving
        # the prior list — ensures the log is the single source of truth and
        # any new LEG_COMPLETED messages are reflected.
        self._rehydrate_executed_legs(self.bus.history())
        self.capability_broadcast_fn(self.task_id, self.round)
        self._place_bids()

    def _place_bids(self) -> None:
        """Bid on every slot this actor can fill (internal use only)."""
        slots = (self.task or {}).get("slots", [])
        for slot in slots:
            score = self.bid_score_fn(slot)
            if score is None:
                continue
            self._publish(
                MessageType.BID,
                slot_id=slot["slot_id"],
                payload={"score": score, "basis": "actor"},
            )

    def check_failure(self) -> None:
        """Observe (never decide) the failure condition.

        Mirrors the original _check_failure: consensus deadline expired with no
        viable bids for some slot. Idempotent and decentralised.
        """
        if self.task is None or self.consensus_announced or self.failure_announced:
            return
        if time.time() < self.task.get("consensus_deadline_ts", float("inf")):
            return
        history = self.bus.history()
        required = {s["slot_id"] for s in self.task.get("slots", [])}
        accepts = accepted_actors(history, self.task_id, self.round)
        if slots_complete(required, accepts):
            return
        if (self.round + 1) in self.reopened_rounds:
            return
        reason = "consensus deadline expired"
        for slot_id in required:
            if not surviving_bids(history, self.task_id, self.round, slot_id):
                reason = "no viable bids"
                break
        self.failure_announced = True
        self._publish(
            MessageType.TASK_FAILED,
            payload={"round": self.round, "reason": reason},
        )

    def maybe_reopen(self, trigger: Message) -> None:
        """If a previously-accepted slot lost its holder, any agent may publish
        the round+1 reopen. Idempotent: concurrent reopens from different agents
        are byte-identical (announce_ts = trigger.ts).
        """
        if self.task is None:
            return
        if (
            trigger.task_id != self.task_id
            or trigger.negotiation_round != self.round
        ):
            return
        next_round = self.round + 1
        if next_round in self.reopened_rounds:
            return
        if time.time() < self.task.get("bid_deadline_ts", float("inf")):
            return
        history = self.bus.history()
        ever_accepted = {
            m.slot_id
            for m in history
            if m.type == MessageType.ACCEPT
            and m.task_id == self.task_id
            and m.negotiation_round == self.round
            and m.slot_id is not None
        }
        currently_accepted = set(
            accepted_actors(history, self.task_id, self.round)
        )
        if not (ever_accepted - currently_accepted):
            return  # no previously-held slot was lost — nothing to reopen

        self.reopened_rounds.add(next_round)
        base_ts = trigger.ts
        payload = dict(self.task)
        payload["announce_ts"] = base_ts
        payload["bid_deadline_ts"] = base_ts + BID_WINDOW_S
        payload["consensus_deadline_ts"] = base_ts + BID_WINDOW_S + CONSENSUS_WINDOW_S
        reopen = Message(
            type=MessageType.TASK_ANNOUNCE,
            task_id=self.task_id,
            negotiation_round=next_round,
            sender=self.actor_id,
            ts=base_ts,
            payload=payload,
        )
        self.bus.publish(reopen)
        # Bus does not echo own messages — adopt the new round locally.
        self.on_reopen_announce(reopen)

    def execution_valid(self, slot_id: str) -> bool:
        """Execution barrier for a specific slot.

        Returns True only when ALL of:
        1. Consensus is still complete (all required slots still accepted).
        2. This actor still holds slot_id (no superseding withdraw/fault).
        3. No higher round has been announced for this task_id.
        4. No agent-level fault was requested.

        Pure read from bus.history() — same invariant as original _execution_valid.
        """
        if self.fault_requested_fn():
            return False
        if self.task is None:
            return False

        history = self.bus.history()
        required = {s["slot_id"] for s in self.task.get("slots", [])}
        accepts = accepted_actors(history, self.task_id, self.round)

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
                and m.task_id == self.task_id
                and m.negotiation_round > self.round
            ):
                return False

        return True

    def next_deadline(self) -> Optional[float]:
        """Return the soonest pending deadline timestamp for this participant,
        or None if no deadline is pending (e.g. already evaluated + announced).
        """
        targets = []
        if self.evaluated_round < self.round:
            deadline = self.task.get("bid_deadline_ts")
            if deadline is not None:
                targets.append(deadline)
        if not self.consensus_announced and not self.failure_announced:
            consensus_deadline = self.task.get("consensus_deadline_ts")
            if consensus_deadline is not None:
                targets.append(consensus_deadline)
        return min(targets) if targets else None

    # ── Internal helpers ───────────────────────────────────────────────────

    def _rehydrate_executed_legs(self, history: list) -> None:
        """Rebuild executed_legs from bus history.

        Scans history for LEG_COMPLETED messages where:
            sender == self.actor_id  AND  task_id == self.task_id

        Idempotent: running this method multiple times with the same history
        produces the same result. Uses a set for dedup so duplicate LEG_COMPLETED
        messages (e.g. from bus redelivery) do not cause double-entries. Preserves
        insertion order by processing history in publish order and using a seen-set.

        Public API: explicit re-derivation callers (Stage 5 reconnect/replay)
        may call this directly with a pre-fetched history slice.

        This is the single source of truth for executed_legs — the bus log IS
        the persistence layer (no separate Redis Hash or execution store).
        """
        seen: set[str] = set()
        new_legs: list[str] = []
        for m in history:
            if (
                m.type == MessageType.LEG_COMPLETED
                and m.task_id == self.task_id
                and m.sender == self.actor_id
                and m.slot_id is not None
                and m.slot_id not in seen
            ):
                seen.add(m.slot_id)
                new_legs.append(m.slot_id)
        self.executed_legs = new_legs

    def _publish(
        self,
        msg_type: MessageType,
        slot_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        self.bus.publish(
            Message(
                type=msg_type,
                task_id=self.task_id,
                negotiation_round=self.round,
                sender=self.actor_id,
                slot_id=slot_id,
                payload=payload or {},
            )
        )
