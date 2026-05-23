"""Negotiation protocol primitives — message types, envelope, public rules.

Pure logic: no I/O, no threads, no Flask. Safe to unit-test in isolation.
This module is the contract every actor agent and the bus share.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


class MessageType(str, Enum):
    """The 15 negotiation message types. `counter_offer` is reserved for a
    later phase and is never emitted in Phase 1. `task_failed`, `leg_started`,
    `leg_completed`, and `leg_halted` are observational markers — never
    authority messages. LEG_* types are bus-visible audit trail for replay,
    recovery, and frontend observation; no actor changes decisions based on
    another actor's LEG_* messages."""

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
    TASK_FAILED = "task_failed"
    # Execution-transition markers (Phase 4 Stage 4) — observational only.
    # Published by the actor executing a leg; no actor acts on another's LEG_*.
    LEG_STARTED = "leg_started"
    LEG_COMPLETED = "leg_completed"
    LEG_HALTED = "leg_halted"


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
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict — used for the SocketIO mirror to the browser."""
        d = asdict(self)
        d["type"] = self.type.value
        return d


@dataclass
class Slot:
    """One leg of a transport task. Slot *structure* is a physical-topology
    fact (an arm serves each station; AGVs move between) — it names no actor.
    Who fills a slot is decided by negotiation, never by this module."""

    slot_id: str
    kind: Literal["arm", "agv"]
    zone: Optional[str] = None       # arm slots: the station zone
    from_zone: Optional[str] = None  # agv slot: pickup zone
    to_zone: Optional[str] = None    # agv slot: dropoff zone


# Bid / consensus window sizes (seconds). Generous vs. sub-millisecond
# deterministic bids, so every bid lands well before any deadline.
BID_WINDOW_S = 1.5
CONSENSUS_WINDOW_S = 1.5

_SCORED_CONSTRAINTS = ("shortest time", "shortest distance")


def public_winner(bids: list[Message], constraint: Optional[str]) -> Optional[str]:
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
        best = min(
            bids,
            key=lambda m: (m.payload.get("score", float("inf")), m.sender),
        )
        return best.sender
    # Unconstrained: lowest sender id among valid bidders.
    return min(b.sender for b in bids)


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


def slots_complete(required_slot_ids: set[str], accepts: dict[str, str]) -> bool:
    """Validation only: True when every required slot has exactly one accepted
    actor and no extra slot is present.

    `accepts` maps slot_id -> accepting actor id, already filtered to valid,
    non-withdrawn accepts. This function NEVER picks an actor — it only checks.
    """
    # all(...) guards against empty/blank actor ids — precondition: the caller
    # has already filtered withdrawn/invalid accepts before passing `accepts`.
    return required_slot_ids == set(accepts.keys()) and all(accepts.values())


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
