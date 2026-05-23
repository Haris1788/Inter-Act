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
    """The 16 negotiation message types. `counter_offer` is reserved for a
    later phase and is never emitted in Phase 1. `task_failed`, `leg_started`,
    `leg_completed`, `leg_halted`, and `position_update` are observational
    markers — never authority messages. LEG_* types are bus-visible audit trail
    for replay, recovery, and frontend observation; no actor changes decisions
    based on another actor's LEG_* messages. `position_update` is emitted by
    AGVs at 500 ms cadence during spatial traversal for replay fidelity; it is
    purely observational and has no consensus impact."""

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
    # Phase 5 Stage 8 — spatial position snapshot for replay (observational only).
    # Emitted by AGVs at 500 ms cadence during _traverse_to; no consensus impact.
    POSITION_UPDATE = "position_update"
    # Phase 6 — payload lifecycle (observational; no consensus impact).
    # Coordinator emits PAYLOAD_SPAWN once per task; arms emit PAYLOAD_TRANSFER
    # at load/unload boundaries. Frontend derives object position from carrier.
    PAYLOAD_SPAWN = "payload_spawn"
    PAYLOAD_TRANSFER = "payload_transfer"


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
    Who fills a slot is decided by negotiation, never by this module.

    Phase 1–4 zone-string fields are preserved unchanged.  Phase 5 adds
    optional spatial fields — all have defaults so every existing constructor
    call continues to work without modification.
    """

    slot_id: str
    kind: Literal["arm", "agv"]
    # Zone-string path (Phase 1–4 — keep forever for backward compat)
    zone: Optional[str] = None       # arm slots: the station zone
    from_zone: Optional[str] = None  # agv slot: pickup zone
    to_zone: Optional[str] = None    # agv slot: dropoff zone
    # Phase 5 spatial fields (all optional — new fields with defaults)
    target_pos: Optional[tuple[float, float]] = None   # arm slot: target canvas px
    from_pos: Optional[tuple[float, float]] = None     # agv slot: pickup position px
    to_pos: Optional[tuple[float, float]] = None       # agv slot: dropoff position px
    served_by: Optional[str] = None    # arm slots — pins the slot to a specific actor
    constraint_tag: Optional[str] = None  # forwarded from announce constraint


# Bid / consensus window sizes (seconds). Generous vs. sub-millisecond
# deterministic bids, so every bid lands well before any deadline.
BID_WINDOW_S = 1.5
CONSENSUS_WINDOW_S = 1.5

_SCORED_CONSTRAINTS = (
    "shortest time", "shortest distance",
    # Phase-6: alias common natural-language forms the LLM emits so user
    # phrasings like "optimise for time" / "minimize distance" hit the
    # speed-weighted / distance-weighted winner rules instead of silently
    # falling through to the unconstrained default (lowest sender id).
    "optimise for time", "optimize for time",
    "fastest", "fastest delivery",
    "minimise time", "minimize time",
    "optimise for distance", "optimize for distance",
    "minimise distance", "minimize distance",
    "nearest", "shortest path",
)
_SCORED_CONSTRAINTS_MAX = ("longest time", "longest distance")


def public_winner(bids: list[Message], constraint: Optional[str]) -> Optional[str]:
    """Deterministic winner of ONE slot from the public bid set.

    Every actor calls this with the same surviving-bid set and the same
    constraint, so every actor computes the identical winner. This is the
    leaderless-consensus rule — no component "decides", the rule does.

    `bids` must already be filtered to the surviving bids for one slot
    (latest bid_version per sender, withdrawn bids removed). Returns the
    winning sender id, or None when there are no bids.

    Supported constraints (Phase 5 Stage 4):
        ""  or unrecognised  → lowest sender id (default — Phase 1–4 behaviour).
        "shortest time"      → min payload.score; ties by lowest sender id.
        "shortest distance"  → min payload.score; ties by lowest sender id.
        "longest time"       → max payload.score; ties by lowest sender id.
        "longest distance"   → max payload.score; ties by lowest sender id.
        "heaviest load"      → max payload.capacity (default 1); ties by min score.
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
    if norm in _SCORED_CONSTRAINTS_MAX:
        # Higher score wins; ties broken deterministically by lowest sender id.
        best = max(
            bids,
            key=lambda m: (m.payload.get("score", float("-inf")), [-ord(c) for c in m.sender]),
        )
        # Re-apply tie-break: among those with the max score, pick lowest sender.
        max_score = best.payload.get("score", float("-inf"))
        candidates = [m for m in bids if m.payload.get("score", float("-inf")) >= max_score]
        return min(m.sender for m in candidates)
    if norm == "heaviest load":
        # Max capacity wins; ties by min score, then by lowest sender id.
        best = max(
            bids,
            key=lambda m: (
                m.payload.get("capacity", 1),
                -m.payload.get("score", float("inf")),
                # negate sender for tiebreak (lowest sender preferred)
            ),
        )
        max_cap = best.payload.get("capacity", 1)
        cap_candidates = [m for m in bids if m.payload.get("capacity", 1) >= max_cap]
        # Among max-capacity bidders, pick lowest score; ties by lowest sender id.
        return min(
            cap_candidates,
            key=lambda m: (m.payload.get("score", float("inf")), m.sender),
        ).sender
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

    UNCHANGED from Phase 1–4. Zone-string path kept intact.
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


def derive_slots_by_actor(
    source_arm_id: str,
    dest_arm_id: str,
    topology: Any,
) -> list[Slot]:
    """Phase 5 spatial decomposition — populates positions from Topology.

    Returns the same 3-slot structure (pickup / transport / place) as
    `derive_slots` but fills in canvas-pixel position fields from the Topology.

    Why ``zone=arm.id`` on arm slots: Phase 4 `ArmAgent._bid_score` reads
    ``slot["zone"]`` for zone-match. Setting it to the actor's own id makes
    the pre-Phase-5 logic naturally pin the slot to its served-by arm (an arm
    "reaches" its own zone-id). Stage 4 replaces this with a position+reach
    check; until then, backward-compat is preserved.

    Args:
        source_arm_id: id of the source arm actor (e.g. ``"arm-1"``).
        dest_arm_id:   id of the destination arm actor (e.g. ``"arm-2"``).
        topology:      a ``Topology`` instance (``app.models.topology.Topology``).

    Raises:
        ValueError: if either actor id is not found in the topology, or if
                    either actor is not of kind ``"arm"``.
    """
    src = topology.actor(source_arm_id)
    dst = topology.actor(dest_arm_id)

    if src is None:
        raise ValueError(
            f"derive_slots_by_actor: unknown actor {source_arm_id!r} in topology"
        )
    if dst is None:
        raise ValueError(
            f"derive_slots_by_actor: unknown actor {dest_arm_id!r} in topology"
        )
    if src.kind != "arm":
        raise ValueError(
            f"derive_slots_by_actor: source {source_arm_id!r} must be an arm "
            f"(got kind={src.kind!r})"
        )
    if dst.kind != "arm":
        raise ValueError(
            f"derive_slots_by_actor: dest {dest_arm_id!r} must be an arm "
            f"(got kind={dst.kind!r})"
        )

    src_pos = (src.x, src.y)
    dst_pos = (dst.x, dst.y)

    # Phase-6 boundary projection — Final Goal: pickup/place happen on the
    # arm's reach boundary, not the arm's centre. The arm's "outward" pickup
    # point faces AWAY from its partner arm (toward the presumed source/sink
    # zone). The AGV docks on the boundary FACING the partner arm so it sits
    # between the two arms during transit. The two points on each arm are
    # therefore antipodal across the arm's reach circle.
    from app.core.geometry import dock_point

    # Pickup arm: outward-facing dock = boundary AWAY from dst arm.
    # Compute by projecting toward dst, then reflecting (anchor + (anchor - p)).
    src_inward = dock_point(src_pos, dst_pos, src.reach_px)   # boundary toward AGV
    src_outward = (2 * src_pos[0] - src_inward[0], 2 * src_pos[1] - src_inward[1])
    dst_inward = dock_point(dst_pos, src_pos, dst.reach_px)   # boundary toward AGV
    dst_outward = (2 * dst_pos[0] - dst_inward[0], 2 * dst_pos[1] - dst_inward[1])

    return [
        Slot(
            slot_id="pickup",
            kind="arm",
            zone=src.id,           # backward-compat zone-match pin
            served_by=src.id,
            target_pos=src_outward,   # arm reaches toward source-zone side
        ),
        Slot(
            slot_id="transport",
            kind="agv",
            from_zone=src.id,      # backward-compat zone-match pin
            to_zone=dst.id,
            from_pos=src_inward,      # AGV docks between the arms (src side)
            to_pos=dst_inward,        # AGV docks between the arms (dst side)
        ),
        Slot(
            slot_id="place",
            kind="arm",
            zone=dst.id,           # backward-compat zone-match pin
            served_by=dst.id,
            target_pos=dst_outward,   # arm reaches toward dest-zone side
        ),
    ]
