"""Phase 2 (US #29) — negotiation, failure, replan, completion.

These tests use NullBus (in-process pub/sub, no SocketIO) and stub the
HTTP transport in dispatch routes, so they run without containers.

Run:
    pytest tests/test_phase2_negotiation.py -v
"""
from __future__ import annotations

import importlib
import threading
import time
from typing import Any

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fresh_bus():
    """Build a NullBus + reset all the singleton subscriber modules so each
    test starts clean. Necessary because subscribers register on module
    import and we don't want carry-over between tests."""
    from app.agents.message_bus import NullBus, set_bus
    bus = NullBus()
    set_bus(bus)

    # Re-attach the subscribers we care about against the fresh bus.
    for mod_name in (
        "app.services.actor_registry",
        "app.services.task_availability",
        "app.services.path_negotiation",
        "app.services.task_tracker",
    ):
        mod = importlib.import_module(mod_name)
        importlib.reload(mod)         # drop old state
        mod.attach(bus)

    return bus


def _events_module():
    """Reload events module so any state inside is fresh."""
    import app.agents.events as ev
    return importlib.reload(ev)


# ── Envelope shape ───────────────────────────────────────────────────────────

def test_envelope_shape():
    ev = _events_module()
    env = ev.make_envelope(ev.TASK_CREATED, "interpreter",
                           {"foo": "bar"}, task_id="t-1")
    assert env["type"] == "TASK_CREATED"
    assert env["actorId"] == "interpreter"
    assert env["taskId"] == "t-1"
    assert env["payload"] == {"foo": "bar"}
    assert "ts" in env and "msgId" in env


# ── Actor registry tracks lifecycle ──────────────────────────────────────────

def test_actor_registry_online_then_busy_then_free():
    bus = _fresh_bus()
    ev = _events_module()
    from app.services import actor_registry

    bus.publish(ev.ACTOR_ONLINE, ev.make_envelope(
        ev.ACTOR_ONLINE, "agv-1", {"kind": "agv"}))
    bus.publish(ev.CAPABILITY_DECLARED, ev.make_envelope(
        ev.CAPABILITY_DECLARED, "agv-1",
        {"kind": "agv", "bay": [10, 3], "canReach": ["input_arm", "output_arm"]}))

    roster = {a["actorId"]: a for a in actor_registry.list_actors()}
    assert "agv-1" in roster
    assert roster["agv-1"]["status"] == "FREE"
    assert roster["agv-1"]["capabilities"]["bay"] == [10, 3]

    bus.publish(ev.ACTOR_BUSY, ev.make_envelope(
        ev.ACTOR_BUSY, "agv-1", {"path": ["arm-1", "agv-1", "arm-2"]},
        task_id="t-1"))
    assert actor_registry.list_actors()[0]["status"] == "BUSY"

    bus.publish(ev.ACTOR_FREE, ev.make_envelope(
        ev.ACTOR_FREE, "agv-1", {"reason": "done"}, task_id="t-1"))
    assert actor_registry.list_actors()[0]["status"] == "FREE"


def test_actor_registry_marks_failed():
    """Step 7: ACTOR_FAILED flips roster status to FAILED with a reason."""
    bus = _fresh_bus()
    ev = _events_module()
    from app.services import actor_registry

    bus.publish(ev.ACTOR_ONLINE, ev.make_envelope(
        ev.ACTOR_ONLINE, "agv-2", {"kind": "agv"}))
    bus.publish(ev.ACTOR_FAILED, ev.make_envelope(
        ev.ACTOR_FAILED, "agv-2", {"reason": "brain crashed"},
        task_id="t-1"))

    rec = next(a for a in actor_registry.list_actors() if a["actorId"] == "agv-2")
    assert rec["status"] == "FAILED"
    assert rec["fault_reason"] == "brain crashed"


# ── Availability collection ──────────────────────────────────────────────────

def test_task_availability_collects_within_window():
    """Step 3: backend collects TASK_AVAILABILITY responses inside a window."""
    bus = _fresh_bus()
    ev = _events_module()
    from app.services import task_availability

    task_id = "t-avail-1"
    task_availability.open_round(task_id)

    def _fire(actor_id, available):
        bus.publish(ev.TASK_AVAILABILITY, ev.make_envelope(
            ev.TASK_AVAILABILITY, actor_id,
            {"available": available, "score": 1.0 if available else 0.0,
             "reason": "ready" if available else "busy"},
            task_id=task_id,
        ))

    threading.Thread(target=lambda: (time.sleep(0.1), _fire("agv-1", True))).start()
    threading.Thread(target=lambda: (time.sleep(0.2), _fire("agv-2", False))).start()
    threading.Thread(target=lambda: (time.sleep(0.3), _fire("arm-1", True))).start()

    responses = task_availability.collect(task_id, window_s=0.6)
    by_actor = {r["actorId"]: r for r in responses}
    assert by_actor["agv-1"]["available"] is True
    assert by_actor["agv-2"]["available"] is False
    assert by_actor["arm-1"]["available"] is True


# ── Path negotiation: accept + reject ────────────────────────────────────────

def test_path_negotiation_collects_responses():
    """Step 4: PATH_ACCEPTED / PATH_REJECTED collected per-task."""
    bus = _fresh_bus()
    ev = _events_module()
    from app.services import path_negotiation

    task_id = "t-neg-1"
    path_negotiation.open_round(task_id)

    bus.publish(ev.PATH_ACCEPTED, ev.make_envelope(
        ev.PATH_ACCEPTED, "arm-1",
        {"path": ["arm-1", "agv-1", "arm-2"], "reason": "named"},
        task_id=task_id))
    bus.publish(ev.PATH_REJECTED, ev.make_envelope(
        ev.PATH_REJECTED, "agv-1",
        {"path": ["arm-1", "agv-1", "arm-2"], "reason": "busy"},
        task_id=task_id))
    bus.publish(ev.PATH_ACCEPTED, ev.make_envelope(
        ev.PATH_ACCEPTED, "arm-2",
        {"path": ["arm-1", "agv-1", "arm-2"], "reason": "named"},
        task_id=task_id))

    responses = path_negotiation.collect(task_id, window_s=0.1)
    assert responses["arm-1"]["accepted"] is True
    assert responses["agv-1"]["accepted"] is False
    assert responses["agv-1"]["reason"] == "busy"
    assert responses["arm-2"]["accepted"] is True


# ── Task tracker + replan ────────────────────────────────────────────────────

def test_task_tracker_triggers_replan_on_fault():
    """Step 8: ACTOR_FAILED on an in-flight task publishes REPLAN_REQUIRED
    with an excluded list that includes the failed actor."""
    bus = _fresh_bus()
    ev = _events_module()
    from app.services import task_tracker

    task_id = "t-replan-1"
    task_tracker.register_task(task_id, {"requiredStations": ["input_arm"]})

    # Pretend the dispatch round proposed a path.
    bus.publish(ev.PATH_PROPOSED, ev.make_envelope(
        ev.PATH_PROPOSED, "interpreter",
        {"path": ["arm-1", "agv-1", "arm-2"]},
        task_id=task_id))

    # Hook a collector for REPLAN_REQUIRED.
    received: list[dict] = []
    bus.subscribe(ev.REPLAN_REQUIRED, lambda env: received.append(env))

    # Now fault the AGV.
    bus.publish(ev.ACTOR_FAILED, ev.make_envelope(
        ev.ACTOR_FAILED, "agv-1", {"reason": "boom"},
        task_id=task_id))

    assert len(received) == 1
    env = received[0]
    assert env["taskId"] == task_id
    assert env["payload"]["failedActor"] == "agv-1"
    assert "agv-1" in env["payload"]["excluded"]
    assert env["payload"]["attempts"] == 1


def test_task_tracker_bounds_replan_attempts():
    """Step 8: replan attempts are bounded by MAX_REPLAN_ATTEMPTS.

    Mirrors the real lifecycle: each fault transitions running → replanning;
    a successful replan transitions replanning → running. Once attempts
    exceed MAX_REPLAN_ATTEMPTS the task moves to aborted and stays."""
    bus = _fresh_bus()
    ev = _events_module()
    from app.services import task_tracker

    task_id = "t-bound-1"
    task_tracker.register_task(task_id, {})

    received: list[dict] = []
    bus.subscribe(ev.REPLAN_REQUIRED, lambda env: received.append(env))

    for i in range(task_tracker.MAX_REPLAN_ATTEMPTS + 2):
        path = ["arm-1", f"agv-{i+1}", "arm-2"]
        bus.publish(ev.PATH_PROPOSED, ev.make_envelope(
            ev.PATH_PROPOSED, "interpreter",
            {"path": path},
            task_id=task_id))
        bus.publish(ev.ACTOR_FAILED, ev.make_envelope(
            ev.ACTOR_FAILED, f"agv-{i+1}", {"reason": "boom"},
            task_id=task_id))
        # Simulate a successful replan so the next fault round is processed.
        entry = task_tracker.get(task_id)
        if entry and entry["status"] == "replanning":
            task_tracker.mark_replanning_done(task_id, success=True, new_path=path)

    assert len(received) == task_tracker.MAX_REPLAN_ATTEMPTS
    entry = task_tracker.get(task_id)
    assert entry["status"] == "aborted"


def test_task_tracker_releases_on_completion():
    """Step 9: TASK_COMPLETED publishes ACTOR_FREE for every path participant."""
    bus = _fresh_bus()
    ev = _events_module()
    from app.services import task_tracker

    task_id = "t-done-1"
    task_tracker.register_task(task_id, {})
    bus.publish(ev.PATH_PROPOSED, ev.make_envelope(
        ev.PATH_PROPOSED, "interpreter",
        {"path": ["arm-1", "agv-3", "arm-2"]},
        task_id=task_id))

    freed: list[str] = []
    bus.subscribe(ev.ACTOR_FREE, lambda env: freed.append(env["actorId"]))

    bus.publish(ev.TASK_COMPLETED, ev.make_envelope(
        ev.TASK_COMPLETED, "agv-3", {"package_id": "pkg-1"},
        task_id=task_id))

    assert set(freed) == {"arm-1", "agv-3", "arm-2"}
    assert task_tracker.get(task_id)["status"] == "completed"


def test_actor_failed_on_inactive_task_does_nothing():
    """Sanity: an ACTOR_FAILED for an actor NOT in any active task should
    not trigger REPLAN_REQUIRED."""
    bus = _fresh_bus()
    ev = _events_module()

    received: list[dict] = []
    bus.subscribe(ev.REPLAN_REQUIRED, lambda env: received.append(env))

    bus.publish(ev.ACTOR_FAILED, ev.make_envelope(
        ev.ACTOR_FAILED, "agv-9", {"reason": "boom"},
        task_id="nonexistent-task"))

    assert received == []
