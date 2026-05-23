"""Tests for Phase 5 Stage 1 spatial slot derivation.

Covers derive_slots_by_actor() (new) and a byte-equivalence test confirming
the existing derive_slots(source_zone, dest_zone) is UNCHANGED from Phase 4.
"""

import pytest

from app.models.topology import ActorSpec, Topology, parse_topology
from app.agents.negotiation import Slot, derive_slots, derive_slots_by_actor


# ---------------------------------------------------------------------------
# Shared fixture topology
# ---------------------------------------------------------------------------

def _make_topology():
    raw = {
        "canvas": {"width": 1600, "height": 1000, "cell_px": 40},
        "actors": [
            {"id": "arm-1", "kind": "arm", "x": 330.0, "y": 210.0, "reach_px": 120.0},
            {"id": "arm-2", "kind": "arm", "x": 1350.0, "y": 210.0, "reach_px": 120.0},
            {"id": "agv-1", "kind": "agv", "x": 1290.0, "y": 450.0},
        ],
    }
    return parse_topology(raw)


# ---------------------------------------------------------------------------
# test 1 — derive_slots_by_actor populates positions
# ---------------------------------------------------------------------------

def test_derive_slots_by_actor_populates_positions():
    topo = _make_topology()
    slots = derive_slots_by_actor("arm-1", "arm-2", topo)

    assert len(slots) == 3

    pickup = slots[0]
    assert pickup.slot_id == "pickup"
    assert pickup.kind == "arm"
    assert pickup.target_pos == (330.0, 210.0)
    assert pickup.from_pos is None
    assert pickup.to_pos is None

    transport = slots[1]
    assert transport.slot_id == "transport"
    assert transport.kind == "agv"
    assert transport.from_pos == (330.0, 210.0)
    assert transport.to_pos == (1350.0, 210.0)
    assert transport.target_pos is None

    place = slots[2]
    assert place.slot_id == "place"
    assert place.kind == "arm"
    assert place.target_pos == (1350.0, 210.0)


# ---------------------------------------------------------------------------
# test 2 — pickup and place slots have served_by pinned to the arm id
# ---------------------------------------------------------------------------

def test_derive_slots_by_actor_pins_served_by():
    topo = _make_topology()
    slots = derive_slots_by_actor("arm-1", "arm-2", topo)

    pickup = slots[0]
    assert pickup.served_by == "arm-1"

    place = slots[2]
    assert place.served_by == "arm-2"

    transport = slots[1]
    assert transport.served_by is None  # AGV slot has no served_by

    # zone fields must also be set for backward-compat Phase 4 zone-match
    assert pickup.zone == "arm-1"
    assert place.zone == "arm-2"
    assert transport.from_zone == "arm-1"
    assert transport.to_zone == "arm-2"


# ---------------------------------------------------------------------------
# test 3 — raises ValueError for unknown actor id
# ---------------------------------------------------------------------------

def test_derive_slots_by_actor_raises_on_unknown_actor():
    topo = _make_topology()
    with pytest.raises(ValueError, match="unknown actor"):
        derive_slots_by_actor("arm-99", "arm-2", topo)

    with pytest.raises(ValueError, match="unknown actor"):
        derive_slots_by_actor("arm-1", "arm-99", topo)


# ---------------------------------------------------------------------------
# test 4 — raises ValueError when a non-arm actor (AGV) is used as endpoint
# ---------------------------------------------------------------------------

def test_derive_slots_by_actor_raises_on_non_arm_endpoints():
    topo = _make_topology()
    with pytest.raises(ValueError, match="must be an arm"):
        derive_slots_by_actor("agv-1", "arm-2", topo)

    with pytest.raises(ValueError, match="must be an arm"):
        derive_slots_by_actor("arm-1", "agv-1", topo)


# ---------------------------------------------------------------------------
# test 5 — derive_slots(zone, zone) signature is byte-identical to Phase 4
# ---------------------------------------------------------------------------

def test_derive_slots_zone_signature_unchanged():
    """Regression guard: the existing Phase 4 zone-string derive_slots() must
    return the exact same Slot shape it always has — no field changes, no
    unexpected defaults, no new mandatory fields.
    """
    slots = derive_slots("input_arm", "output_arm")

    assert len(slots) == 3

    pickup = slots[0]
    assert pickup.slot_id == "pickup"
    assert pickup.kind == "arm"
    assert pickup.zone == "input_arm"
    assert pickup.from_zone is None
    assert pickup.to_zone is None
    # Phase 5 new fields — must be None/default when using the legacy path
    assert pickup.target_pos is None
    assert pickup.from_pos is None
    assert pickup.to_pos is None
    assert pickup.served_by is None
    assert pickup.constraint_tag is None

    transport = slots[1]
    assert transport.slot_id == "transport"
    assert transport.kind == "agv"
    assert transport.zone is None
    assert transport.from_zone == "input_arm"
    assert transport.to_zone == "output_arm"
    assert transport.target_pos is None
    assert transport.from_pos is None
    assert transport.to_pos is None
    assert transport.served_by is None
    assert transport.constraint_tag is None

    place = slots[2]
    assert place.slot_id == "place"
    assert place.kind == "arm"
    assert place.zone == "output_arm"
    assert place.from_zone is None
    assert place.to_zone is None
    assert place.target_pos is None
    assert place.from_pos is None
    assert place.to_pos is None
    assert place.served_by is None
    assert place.constraint_tag is None
