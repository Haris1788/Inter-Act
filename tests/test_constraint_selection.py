"""Phase-6 Stage E — verifies that speed-weighted bidding picks the fastest
effective AGV, not the nearest one, when the constraint is "shortest time".

Three synthetic AGV profiles bid on a single transport slot. Each bidder is
just a `Message` with `payload.score` precomputed using the actual
`AGVAgent._bid_score` formula (`(approach + trip) / speed`). The leaderless
rule `public_winner(bids, constraint)` selects the bid with the lowest score
when the constraint is `"shortest time"`.

We assert:

1. The expected winner is the AGV whose `(distance / speed)` is lowest
   (not the AGV closest to the pickup).
2. Flipping the constraint to `"shortest distance"` keeps the same ordering
   when scores are computed from time, because score is the literal payload
   field both constraints minimise. We therefore re-compute distance-only
   scores and verify the winner differs.
3. A rationale ordering check: sorting bids by score ascending should match
   the rationale the actor would publish on CONSENSUS_REACHED.
"""

from __future__ import annotations

import math

from app.agents.negotiation import Message, MessageType, public_winner


def _bid(sender: str, slot_id: str, score: float, capacity: int = 1) -> Message:
    return Message(
        type=MessageType.BID,
        task_id="t-1",
        negotiation_round=0,
        sender=sender,
        slot_id=slot_id,
        payload={"score": score, "capacity": capacity},
    )


def _eta(agv_pos, pickup, dropoff, speed):
    approach = math.hypot(agv_pos[0] - pickup[0], agv_pos[1] - pickup[1])
    trip = math.hypot(pickup[0] - dropoff[0], pickup[1] - dropoff[1])
    return (approach + trip) / speed


def _distance(agv_pos, pickup, dropoff):
    approach = math.hypot(agv_pos[0] - pickup[0], agv_pos[1] - pickup[1])
    trip = math.hypot(pickup[0] - dropoff[0], pickup[1] - dropoff[1])
    return approach + trip


# Scenario:
#   pickup point at (200, 200), dropoff at (1200, 200).
#   agv-1: near pickup (220, 240), speed 0.5  → close but slow.
#   agv-2: middle of map (700, 400), speed 2.0 → far but fast.
#   agv-3: same as agv-1's neighbourhood (250, 200), speed 1.0 → baseline.

PICKUP = (200.0, 200.0)
DROPOFF = (1200.0, 200.0)
PROFILES = {
    "agv-1": {"pos": (220.0, 240.0), "speed": 0.5},
    "agv-2": {"pos": (700.0, 400.0), "speed": 2.0},
    "agv-3": {"pos": (250.0, 200.0), "speed": 1.0},
}


def test_shortest_time_picks_fastest_not_nearest():
    bids = [
        _bid(aid, "transport", _eta(p["pos"], PICKUP, DROPOFF, p["speed"]))
        for aid, p in PROFILES.items()
    ]
    winner = public_winner(bids, "shortest time")

    # Sanity: agv-1 has the lowest raw distance; if speed were ignored it would win.
    dists = {aid: _distance(p["pos"], PICKUP, DROPOFF) for aid, p in PROFILES.items()}
    nearest = min(dists, key=lambda k: dists[k])
    assert nearest == "agv-1"

    # The actual winner under time-weighting should NOT be the nearest, because
    # agv-2 is fast enough to win despite being far.
    etas = {aid: _eta(p["pos"], PICKUP, DROPOFF, p["speed"]) for aid, p in PROFILES.items()}
    fastest = min(etas, key=lambda k: etas[k])
    assert winner == fastest
    assert winner != nearest


def test_shortest_distance_picks_nearest_when_scores_are_distance():
    """When bids carry RAW DISTANCE as score, `shortest distance` winner is
    the nearest AGV — regardless of how fast the other AGVs are."""
    bids = [
        _bid(aid, "transport", _distance(p["pos"], PICKUP, DROPOFF))
        for aid, p in PROFILES.items()
    ]
    winner = public_winner(bids, "shortest distance")
    assert winner == "agv-1"  # the nearest


def test_rationale_ordering_matches_score_ascending():
    """The rationale block that ActorAgent publishes on CONSENSUS_REACHED is
    sorted by (score, sender) ascending. Reproduce that sort and assert the
    winner is the head of the list."""
    bids = [
        _bid(aid, "transport", _eta(p["pos"], PICKUP, DROPOFF, p["speed"]))
        for aid, p in PROFILES.items()
    ]
    sorted_bids = sorted(
        bids, key=lambda m: (m.payload["score"], m.sender),
    )
    winner = public_winner(bids, "shortest time")
    assert sorted_bids[0].sender == winner


def test_tie_breaks_by_lowest_sender_id():
    """When two AGVs produce the same effective ETA, the lowest sender id wins."""
    bids = [
        _bid("agv-9", "transport", 10.0),
        _bid("agv-1", "transport", 10.0),
        _bid("agv-5", "transport", 10.0),
    ]
    assert public_winner(bids, "shortest time") == "agv-1"
