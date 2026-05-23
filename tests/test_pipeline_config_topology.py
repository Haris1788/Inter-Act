"""Tests for Phase 5 Stage 1 topology extensions to pipeline_config.

Covers get_topology(), get_topology_or_legacy_shim(), reload() re-parse.
All tests use monkeypatching / temporary YAML to avoid touching the real
pipeline.yaml on disk.
"""

import os
import textwrap
import tempfile

import pytest

import app.models.pipeline_config as pc
from app.models.topology import Topology


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WAREHOUSE_ONLY_YAML = textwrap.dedent("""\
    pipeline:
      mode: dependency
    arms:
    - id: 1
      label: Input Arm
      color: '#e74c3c'
      depends_on: []
    - id: 2
      label: Output Arm
      color: '#27ae60'
      depends_on: []
    warehouse:
      grid:
        cols: 14
        rows: 10
        cell_px: 120
      origin:
        x: 30
        y: 30
      stations:
        input_arm:
          cell: [1, 1]
          dock: [2, 1]
          label: IN
          served_by: arm-1
        output_arm:
          cell: [12, 1]
          dock: [11, 1]
          label: OUT
          served_by: arm-2
      lane:
        - [2, 1]
        - [11, 1]
      agvs:
      - id: agv-1
        label: AGV 1
        color: '#16a085'
        bay: [10, 3]
      - id: agv-2
        label: AGV 2
        color: '#8e44ad'
        bay: [10, 5]
""")

_TOPOLOGY_YAML = textwrap.dedent("""\
    pipeline:
      mode: dependency
    arms:
    - id: 1
      label: Input Arm
      color: '#e74c3c'
      depends_on: []
    topology:
      canvas:
        width: 800
        height: 600
        cell_px: 40
      actors:
        - id: arm-1
          kind: arm
          x: 120.0
          y: 300.0
          reach_px: 150.0
        - id: agv-1
          kind: agv
          x: 400.0
          y: 300.0
""")

_BOTH_YAML = textwrap.dedent("""\
    pipeline:
      mode: dependency
    arms:
    - id: 1
      label: Input Arm
      color: '#e74c3c'
      depends_on: []
    - id: 2
      label: Output Arm
      color: '#27ae60'
      depends_on: []
    warehouse:
      grid:
        cols: 14
        rows: 10
        cell_px: 120
      origin:
        x: 30
        y: 30
      stations:
        input_arm:
          cell: [1, 1]
          dock: [2, 1]
          label: IN
          served_by: arm-1
        output_arm:
          cell: [12, 1]
          dock: [11, 1]
          label: OUT
          served_by: arm-2
      lane:
        - [2, 1]
        - [11, 1]
      agvs:
      - id: agv-1
        label: AGV 1
        color: '#16a085'
        bay: [10, 3]
    topology:
      canvas:
        width: 900
        height: 700
        cell_px: 50
      actors:
        - id: arm-1
          kind: arm
          x: 200.0
          y: 400.0
""")


def _write_tmp(content: str) -> str:
    """Write content to a named temp file; caller responsible for cleanup."""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset pipeline_config module state before/after every test."""
    pc._config = None
    pc._topology_cache = pc._TOPOLOGY_UNSET
    yield
    pc._config = None
    pc._topology_cache = pc._TOPOLOGY_UNSET


# ---------------------------------------------------------------------------
# test 1 — get_topology returns None when only warehouse: block present
# ---------------------------------------------------------------------------

def test_get_topology_returns_none_when_only_warehouse():
    path = _write_tmp(_WAREHOUSE_ONLY_YAML)
    try:
        pc.load_config(path)
        result = pc.get_topology()
        assert result is None
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# test 2 — get_topology returns parsed Topology when topology: block present
# ---------------------------------------------------------------------------

def test_get_topology_returns_parsed_when_present():
    path = _write_tmp(_TOPOLOGY_YAML)
    try:
        pc.load_config(path)
        topo = pc.get_topology()
        assert topo is not None
        assert isinstance(topo, Topology)
        assert topo.canvas_width == 800
        assert topo.canvas_height == 600
        assert topo.cell_px == 40
        assert len(topo.actors) == 2
        arm = topo.actor("arm-1")
        assert arm is not None
        assert arm.x == 120.0
        assert arm.reach_px == 150.0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# test 3 — legacy shim synthesises Topology from warehouse: block
# ---------------------------------------------------------------------------

def test_legacy_shim_synthesizes_topology_from_warehouse():
    path = _write_tmp(_WAREHOUSE_ONLY_YAML)
    try:
        pc.load_config(path)
        topo = pc.get_topology_or_legacy_shim()
        assert topo is not None
        assert isinstance(topo, Topology)

        # Should have 2 arms + 2 AGVs = 4 actors
        assert len(topo.actors) == 4
        assert len(topo.arms()) == 2
        assert len(topo.agvs()) == 2

        arm_ids = {a.id for a in topo.arms()}
        assert arm_ids == {"arm-1", "arm-2"}

        agv_ids = {a.id for a in topo.agvs()}
        assert agv_ids == {"agv-1", "agv-2"}

        # Verify arm-1 position: dock=[2,1], cell_px=120, origin={x:30, y:30}
        # centre_x = 30 + (2 + 0.5) * 120 = 30 + 300 = 330
        # centre_y = 30 + (1 + 0.5) * 120 = 30 + 180 = 210
        arm1 = topo.actor("arm-1")
        assert arm1 is not None
        assert arm1.x == pytest.approx(330.0)
        assert arm1.y == pytest.approx(210.0)
        assert arm1.reach_px == 120.0   # default shim reach

        # Verify agv-1 position: bay=[10,3], cell_px=120, origin={x:30, y:30}
        # centre_x = 30 + (10 + 0.5) * 120 = 30 + 1260 = 1290
        # centre_y = 30 + (3 + 0.5) * 120 = 30 + 420 = 450
        agv1 = topo.actor("agv-1")
        assert agv1 is not None
        assert agv1.x == pytest.approx(1290.0)
        assert agv1.y == pytest.approx(450.0)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# test 4 — topology: overrides warehouse: when both present
# ---------------------------------------------------------------------------

def test_topology_overrides_warehouse_when_both_present():
    path = _write_tmp(_BOTH_YAML)
    try:
        pc.load_config(path)
        topo = pc.get_topology_or_legacy_shim()
        assert topo is not None
        # The topology: block wins — canvas_width=900 not derived from warehouse
        assert topo.canvas_width == 900
        assert topo.canvas_height == 700
        assert topo.cell_px == 50
        # topology block only defines 1 actor
        assert len(topo.actors) == 1
        arm = topo.actor("arm-1")
        assert arm is not None
        assert arm.x == 200.0
        assert arm.y == 400.0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# test 5 — reload() re-parses topology changes
# ---------------------------------------------------------------------------

def test_reload_picks_up_topology_change():
    path = _write_tmp(_WAREHOUSE_ONLY_YAML)
    try:
        pc.load_config(path)
        # Initially no topology
        assert pc.get_topology() is None

        # Write new content with topology block to the same path
        with open(path, "w") as f:
            f.write(_TOPOLOGY_YAML)

        # Reload — must clear caches and re-parse
        pc._config = None
        pc._topology_cache = pc._TOPOLOGY_UNSET
        pc.load_config(path)

        topo = pc.get_topology()
        assert topo is not None
        assert topo.canvas_width == 800
        arm = topo.actor("arm-1")
        assert arm is not None
        assert arm.x == 120.0
    finally:
        os.unlink(path)
