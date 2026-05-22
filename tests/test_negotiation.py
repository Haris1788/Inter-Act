"""Unit tests for the negotiation protocol primitives."""

from app.agents.negotiation import (
    Message,
    MessageType,
    RoundStatus,
    Slot,
    derive_slots,
    public_winner,
)


def test_message_to_dict_roundtrips():
    m = Message(
        type=MessageType.BID,
        task_id="t1",
        negotiation_round=0,
        sender="agv-1",
        slot_id="transport",
        bid_version=2,
        payload={"score": 3.0},
    )
    d = m.to_dict()
    assert d["type"] == "bid"
    assert d["task_id"] == "t1"
    assert d["negotiation_round"] == 0
    assert d["sender"] == "agv-1"
    assert d["slot_id"] == "transport"
    assert d["bid_version"] == 2
    assert d["payload"] == {"score": 3.0}
    assert isinstance(d["ts"], float)


def test_message_defaults():
    m = Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id="t1",
        negotiation_round=0,
        sender="interpreter",
    )
    assert m.slot_id is None
    assert m.bid_version == 0
    assert m.payload == {}
    assert m.ts > 0


def test_eleven_message_types_exist():
    expected = {
        "task_announce", "capability_broadcast", "availability_update",
        "bid", "counter_offer", "accept", "reject", "busy",
        "fault", "withdraw", "consensus_reached",
    }
    assert {t.value for t in MessageType} == expected


def test_round_status_values():
    assert {s.value for s in RoundStatus} == {"open", "converged", "failed", "superseded"}


def test_derive_slots_three_legs():
    slots = derive_slots("input_arm", "output_arm")
    assert [s.slot_id for s in slots] == ["pickup", "transport", "place"]
    assert [s.kind for s in slots] == ["arm", "agv", "arm"]

    pickup, transport, place = slots
    assert pickup.zone == "input_arm"
    assert place.zone == "output_arm"
    assert transport.from_zone == "input_arm"
    assert transport.to_zone == "output_arm"
    assert transport.zone is None


def _bid(sender, score, slot="transport", rnd=0, ver=0):
    return Message(
        type=MessageType.BID,
        task_id="t1",
        negotiation_round=rnd,
        sender=sender,
        slot_id=slot,
        bid_version=ver,
        payload={"score": score},
    )


def test_public_winner_shortest_time_picks_lowest_score():
    bids = [_bid("agv-1", 14.0), _bid("agv-2", 8.0), _bid("agv-3", 20.0)]
    assert public_winner(bids, "shortest time") == "agv-2"


def test_public_winner_shortest_distance_picks_lowest_score():
    bids = [_bid("agv-1", 30.0), _bid("agv-3", 12.0)]
    assert public_winner(bids, "shortest distance") == "agv-3"


def test_public_winner_tie_break_is_lowest_sender_id():
    bids = [_bid("agv-3", 5.0), _bid("agv-1", 5.0), _bid("agv-2", 5.0)]
    assert public_winner(bids, "shortest time") == "agv-1"


def test_public_winner_unconstrained_is_lowest_sender_id():
    bids = [_bid("agv-2", 99.0), _bid("agv-1", 1.0)]
    assert public_winner(bids, "") == "agv-1"


def test_public_winner_empty_set_is_none():
    assert public_winner([], "shortest time") is None
