"""Unit tests for public_winner constraint extensions — Phase 5 Stage 4."""

from app.agents.negotiation import Message, MessageType, public_winner


def _bid(sender, score, capacity=1, slot="transport", rnd=0):
    return Message(
        type=MessageType.BID,
        task_id="t1",
        negotiation_round=rnd,
        sender=sender,
        slot_id=slot,
        payload={"score": score, "capacity": capacity},
    )


def test_shortest_time_picks_min():
    """Under 'shortest time', the bidder with the lowest score wins."""
    bids = [
        _bid("agv-1", score=900.0),
        _bid("agv-2", score=1700.0),
        _bid("agv-3", score=500.0),
    ]
    winner = public_winner(bids, "shortest time")
    assert winner == "agv-3", f"Expected agv-3 (score=500); got {winner}"


def test_longest_time_picks_max():
    """Under 'longest time', the bidder with the highest score wins."""
    bids = [
        _bid("agv-1", score=900.0),
        _bid("agv-2", score=1700.0),
        _bid("agv-3", score=500.0),
    ]
    winner = public_winner(bids, "longest time")
    assert winner == "agv-2", f"Expected agv-2 (score=1700); got {winner}"


def test_longest_distance_picks_max():
    """Under 'longest distance', the bidder with the highest score wins."""
    bids = [
        _bid("agv-a", score=300.0),
        _bid("agv-b", score=800.0),
    ]
    winner = public_winner(bids, "longest distance")
    assert winner == "agv-b", f"Expected agv-b (score=800); got {winner}"


def test_heaviest_load_breaks_capacity_first():
    """Under 'heaviest load', capacity dominates; ties broken by min score."""
    bids = [
        _bid("agv-light", score=100.0, capacity=1),
        _bid("agv-heavy", score=800.0, capacity=4),
    ]
    winner = public_winner(bids, "heaviest load")
    assert winner == "agv-heavy", (
        f"'heaviest load' should pick agv-heavy (cap=4) regardless of score; got {winner}"
    )

    # Tie in capacity — lower score wins.
    bids_tied = [
        _bid("agv-x", score=500.0, capacity=4),
        _bid("agv-y", score=200.0, capacity=4),
    ]
    winner_tied = public_winner(bids_tied, "heaviest load")
    assert winner_tied == "agv-y", (
        f"Capacity tie: lower score should win; got {winner_tied}"
    )


def test_default_constraint_unchanged():
    """Empty constraint → lowest sender id wins (Phase 4 / leaderless default)."""
    bids = [
        _bid("agv-3", score=5.0),
        _bid("agv-1", score=9999.0),
        _bid("agv-2", score=1.0),
    ]
    # Default: no constraint → lowest sender id, ignoring score.
    winner = public_winner(bids, "")
    assert winner == "agv-1", f"Default constraint: lowest sender id should win; got {winner}"

    winner_none = public_winner(bids, None)
    assert winner_none == "agv-1", f"None constraint: lowest sender id should win; got {winner_none}"
