"""Unit tests for the negotiation history-filtering helpers."""

from app.agents.negotiation import (
    Message,
    MessageType,
    accepted_actors,
    surviving_bids,
)


def _m(mtype, sender, slot_id=None, rnd=0, ver=0, score=None):
    payload = {} if score is None else {"score": score}
    return Message(
        type=mtype,
        task_id="t1",
        negotiation_round=rnd,
        sender=sender,
        slot_id=slot_id,
        bid_version=ver,
        payload=payload,
    )


def test_surviving_bids_keeps_latest_version_per_sender():
    history = [
        _m(MessageType.BID, "agv-1", "transport", ver=0, score=10.0),
        _m(MessageType.BID, "agv-1", "transport", ver=1, score=4.0),
        _m(MessageType.BID, "agv-2", "transport", ver=0, score=7.0),
    ]
    survivors = surviving_bids(history, "t1", 0, "transport")
    by_sender = {m.sender: m for m in survivors}
    assert set(by_sender) == {"agv-1", "agv-2"}
    assert by_sender["agv-1"].payload["score"] == 4.0


def test_surviving_bids_drops_withdrawn_sender():
    history = [
        _m(MessageType.BID, "agv-1", "transport", score=4.0),
        _m(MessageType.BID, "agv-2", "transport", score=7.0),
        _m(MessageType.WITHDRAW, "agv-1", "transport"),
    ]
    survivors = surviving_bids(history, "t1", 0, "transport")
    assert [m.sender for m in survivors] == ["agv-2"]


def test_surviving_bids_filters_by_task_round_slot():
    history = [
        _m(MessageType.BID, "agv-1", "transport", rnd=0, score=4.0),
        _m(MessageType.BID, "agv-2", "transport", rnd=1, score=4.0),
        _m(MessageType.BID, "agv-3", "pickup", rnd=0, score=4.0),
    ]
    survivors = surviving_bids(history, "t1", 0, "transport")
    assert [m.sender for m in survivors] == ["agv-1"]


def test_accepted_actors_maps_slot_to_sender():
    history = [
        _m(MessageType.ACCEPT, "arm-1", "pickup"),
        _m(MessageType.ACCEPT, "agv-2", "transport"),
    ]
    assert accepted_actors(history, "t1", 0) == {
        "pickup": "arm-1",
        "transport": "agv-2",
    }


def test_accepted_actors_drops_accept_superseded_by_withdraw():
    history = [
        _m(MessageType.ACCEPT, "agv-2", "transport"),
        _m(MessageType.WITHDRAW, "agv-2", "transport"),
    ]
    assert accepted_actors(history, "t1", 0) == {}


def test_accepted_actors_ignores_other_round():
    history = [
        _m(MessageType.ACCEPT, "arm-1", "pickup", rnd=1),
    ]
    assert accepted_actors(history, "t1", 0) == {}


def test_surviving_bids_equal_version_takes_last_published():
    # Two bids at the SAME bid_version from one sender: the later-published
    # message wins (publish order is the canonical tiebreak).
    history = [
        _m(MessageType.BID, "agv-1", "transport", ver=0, score=9.0),
        _m(MessageType.BID, "agv-1", "transport", ver=0, score=3.0),
    ]
    survivors = surviving_bids(history, "t1", 0, "transport")
    assert len(survivors) == 1
    assert survivors[0].payload["score"] == 3.0


def test_accepted_actors_withdraw_by_non_holder_does_not_remove_slot():
    # arm-2 holds 'pickup'; a withdraw from arm-9 (not the holder) must NOT
    # remove the slot — only the current holder's withdraw clears it.
    history = [
        _m(MessageType.ACCEPT, "arm-2", "pickup"),
        _m(MessageType.WITHDRAW, "arm-9", "pickup"),
    ]
    assert accepted_actors(history, "t1", 0) == {"pickup": "arm-2"}
