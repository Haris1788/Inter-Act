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
