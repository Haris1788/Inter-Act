"""Tests for app.models.topology — pure dataclass + parser.

No I/O, no Flask, no pipeline_config.  These run in complete isolation.
"""

import pytest

from app.models.topology import ActorSpec, Topology, parse_topology


# ---------------------------------------------------------------------------
# test 1 — dataclass round-trip
# ---------------------------------------------------------------------------

def test_actor_spec_round_trip():
    spec = ActorSpec(id="arm-1", kind="arm", x=120.0, y=300.0, reach_px=140.0)
    assert spec.id == "arm-1"
    assert spec.kind == "arm"
    assert spec.x == 120.0
    assert spec.y == 300.0
    assert spec.reach_px == 140.0
    assert spec.capacity == 1
    assert spec.speed == 1.0
    assert spec.label is None
    assert spec.color is None


# ---------------------------------------------------------------------------
# test 2 — parse_topology full block
# ---------------------------------------------------------------------------

_FULL_RAW = {
    "canvas": {"width": 1600, "height": 1000, "cell_px": 40},
    "actors": [
        {"id": "arm-1", "kind": "arm", "x": 120, "y": 300, "reach_px": 140},
        {"id": "agv-1", "kind": "agv", "x": 800, "y": 600, "capacity": 2},
    ],
}


def test_parse_topology_full():
    topo = parse_topology(_FULL_RAW)
    assert topo.canvas_width == 1600
    assert topo.canvas_height == 1000
    assert topo.cell_px == 40
    assert len(topo.actors) == 2

    arm = topo.actors[0]
    assert arm.id == "arm-1"
    assert arm.kind == "arm"
    assert arm.x == 120.0
    assert arm.y == 300.0
    assert arm.reach_px == 140.0

    agv = topo.actors[1]
    assert agv.id == "agv-1"
    assert agv.kind == "agv"
    assert agv.capacity == 2

    # cell_for snap check
    col, row = topo.cell_for(120.0, 300.0)
    assert col == 3   # 120 // 40
    assert row == 7   # 300 // 40


# ---------------------------------------------------------------------------
# test 3 — parse_topology tolerates missing optional fields
# ---------------------------------------------------------------------------

def test_parse_topology_tolerates_missing_fields():
    minimal_raw = {
        "actors": [
            {"id": "arm-2", "kind": "arm", "x": 0, "y": 0},
        ]
    }
    topo = parse_topology(minimal_raw)
    # canvas defaults
    assert topo.canvas_width == 1600
    assert topo.canvas_height == 1000
    assert topo.cell_px == 40
    actor = topo.actors[0]
    # actor defaults
    assert actor.reach_px == 120.0
    assert actor.capacity == 1
    assert actor.speed == 1.0
    assert actor.label is None
    assert actor.color is None


# ---------------------------------------------------------------------------
# test 4 — arms() and agvs() filters
# ---------------------------------------------------------------------------

def test_topology_arms_agvs_filters():
    raw = {
        "actors": [
            {"id": "arm-1", "kind": "arm", "x": 0, "y": 0},
            {"id": "agv-1", "kind": "agv", "x": 0, "y": 0},
            {"id": "arm-2", "kind": "arm", "x": 0, "y": 0},
            {"id": "agv-2", "kind": "agv", "x": 0, "y": 0},
        ]
    }
    topo = parse_topology(raw)
    arms = topo.arms()
    agvs = topo.agvs()
    assert len(arms) == 2
    assert all(a.kind == "arm" for a in arms)
    assert len(agvs) == 2
    assert all(a.kind == "agv" for a in agvs)
    assert {a.id for a in arms} == {"arm-1", "arm-2"}
    assert {a.id for a in agvs} == {"agv-1", "agv-2"}


# ---------------------------------------------------------------------------
# test 5 — actor() lookup
# ---------------------------------------------------------------------------

def test_actor_lookup():
    topo = parse_topology(_FULL_RAW)
    found = topo.actor("arm-1")
    assert found is not None
    assert found.id == "arm-1"

    missing = topo.actor("arm-999")
    assert missing is None
