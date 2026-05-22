"""Unit tests for the negotiation bus."""

import pytest

from app.agents.bus import NegotiationBus, get_bus, reset_bus
from app.agents.negotiation import Message, MessageType


@pytest.fixture(autouse=True)
def _fresh_bus():
    reset_bus()
    yield
    reset_bus()


def _msg(mtype, sender="x"):
    return Message(type=mtype, task_id="t1", negotiation_round=0, sender=sender)


def test_subscribe_publish_delivers_to_subscriber():
    bus = NegotiationBus()
    received = []
    bus.subscribe(MessageType.BID, received.append)
    m = _msg(MessageType.BID)
    bus.publish(m)
    assert received == [m]


def test_publish_records_history():
    bus = NegotiationBus()
    m = _msg(MessageType.TASK_ANNOUNCE, sender="interpreter")
    bus.publish(m)
    assert bus.history() == [m]


def test_subscriber_only_receives_its_type():
    bus = NegotiationBus()
    got = []
    bus.subscribe(MessageType.ACCEPT, got.append)
    bus.publish(_msg(MessageType.BID))
    assert got == []


def test_multiple_subscribers_all_receive():
    bus = NegotiationBus()
    a, b = [], []
    bus.subscribe(MessageType.BID, a.append)
    bus.subscribe(MessageType.BID, b.append)
    bus.publish(_msg(MessageType.BID))
    assert len(a) == 1 and len(b) == 1


def test_subscriber_exception_does_not_break_publish():
    bus = NegotiationBus()
    ok = []

    def boom(_m):
        raise RuntimeError("subscriber blew up")

    bus.subscribe(MessageType.BID, boom)
    bus.subscribe(MessageType.BID, ok.append)
    bus.publish(_msg(MessageType.BID))  # must not raise
    assert len(ok) == 1


def test_publish_without_flask_app_does_not_raise():
    # The SocketIO mirror is best-effort; with no running app it must no-op,
    # and the message must still be recorded in history.
    bus = NegotiationBus()
    m = _msg(MessageType.BID)
    bus.publish(m)  # must not raise
    assert bus.history() == [m]


def test_get_bus_is_singleton():
    assert get_bus() is get_bus()


def test_subscribe_all_receives_every_type():
    bus = NegotiationBus()
    got = []
    bus.subscribe_all(got.append)
    for mt in MessageType:
        bus.publish(_msg(mt))
    assert [m.type for m in got] == list(MessageType)
