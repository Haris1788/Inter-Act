"""Tests for POST /negotiate (Phase 3 Task 2).

Uses sync=true so the route blocks and we can assert bus state without timing.
The bus is reset between tests via reset_bus() for isolation.
Monkeypatches interpreter.interpret so these tests run without Ollama.
"""

from __future__ import annotations

import pytest

from app.agents.bus import get_bus, reset_bus
from app.agents.negotiation import MessageType


@pytest.fixture(autouse=True)
def _fresh_bus():
    """Give each test a clean negotiation bus."""
    reset_bus()
    yield
    reset_bus()


@pytest.fixture()
def client(monkeypatch):
    """Flask test client with interpreter stubbed out."""
    # Stub the interpreter so tests don't need Ollama.
    fake_spec = {
        "intent": "move",
        "package_count": 1,
        "ordering": [],
        "constraints": ["shortest time"],
        "agv_ids": [],
        "description": "Move one package",
        "raw_prompt": "move a package",
    }

    monkeypatch.setattr(
        "app.agents.interpreter.interpret",
        lambda prompt: {"status": "success", "task_spec": fake_spec},
    )

    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_negotiate_returns_202_async(client):
    resp = client.post("/negotiate", json={"prompt": "move a package"})
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "accepted"


def test_negotiate_sync_publishes_task_announce(client):
    resp = client.post("/negotiate", json={"prompt": "move a package", "sync": True})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert "task_id" in data
    assert data["source_zone"] == "input_arm"
    assert data["dest_zone"] == "output_arm"

    # Verify a TASK_ANNOUNCE landed on the bus.
    history = get_bus().history()
    assert any(m.type == MessageType.TASK_ANNOUNCE for m in history)

    announce = next(m for m in history if m.type == MessageType.TASK_ANNOUNCE)
    assert announce.sender == "interpreter"
    assert announce.negotiation_round == 0
    assert announce.payload["source_zone"] == "input_arm"
    assert announce.payload["dest_zone"] == "output_arm"

    # Slot shape must match what agents expect.
    slots = {s["slot_id"]: s for s in announce.payload["slots"]}
    assert set(slots) == {"pickup", "transport", "place"}
    assert slots["pickup"]["kind"] == "arm"
    assert slots["pickup"]["zone"] == "input_arm"
    assert slots["transport"]["kind"] == "agv"
    assert slots["transport"]["from_zone"] == "input_arm"
    assert slots["transport"]["to_zone"] == "output_arm"
    assert slots["place"]["kind"] == "arm"
    assert slots["place"]["zone"] == "output_arm"


def test_negotiate_sync_missing_prompt_returns_400(client):
    resp = client.post("/negotiate", json={})
    assert resp.status_code == 400


def test_negotiate_sync_interpreter_failure_returns_500(client, monkeypatch):
    monkeypatch.setattr(
        "app.agents.interpreter.interpret",
        lambda prompt: {"status": "failed", "error": "Ollama unavailable"},
    )
    resp = client.post("/negotiate", json={"prompt": "move something", "sync": True})
    assert resp.status_code == 500
    assert resp.get_json()["status"] == "failed"
