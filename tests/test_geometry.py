"""Unit tests for `app.core.geometry`. Pure functions, no fixtures needed."""

from __future__ import annotations

import math

import pytest

from app.core.geometry import dock_point


def _close(a, b, tol=1e-9):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def test_dock_point_on_circle_along_axis():
    """A simple horizontal projection should land on the circle, on the
    anchor→toward ray."""
    p = dock_point((0.0, 0.0), (100.0, 0.0), 25.0)
    assert _close(p, (25.0, 0.0))


def test_dock_point_diagonal_unit_distance():
    """45° diagonal projection: result should be reach_px from anchor at 45°."""
    p = dock_point((0.0, 0.0), (10.0, 10.0), 1.0)
    s = 1.0 / math.sqrt(2.0)
    assert _close(p, (s, s), tol=1e-12)


def test_dock_point_distance_invariant():
    """Result must always be exactly reach_px from anchor for non-degenerate input."""
    p = dock_point((50.0, 30.0), (200.0, 400.0), 75.0)
    d = math.hypot(p[0] - 50.0, p[1] - 30.0)
    assert math.isclose(d, 75.0, rel_tol=1e-12)


def test_dock_point_collinear_safe():
    """When toward == anchor, no direction exists. Return anchor unchanged."""
    p = dock_point((10.0, 20.0), (10.0, 20.0), 50.0)
    assert p == (10.0, 20.0)


def test_dock_point_zero_reach_safe():
    """reach_px <= 0 should return anchor unchanged (degenerate)."""
    assert dock_point((1.0, 1.0), (10.0, 10.0), 0.0) == (1.0, 1.0)
    assert dock_point((1.0, 1.0), (10.0, 10.0), -5.0) == (1.0, 1.0)


def test_dock_point_along_partner_direction():
    """Vector should point from anchor toward partner. Sign check."""
    # arm1 at (100,100), arm2 at (300,100). dock toward arm2 = right of arm1.
    p = dock_point((100.0, 100.0), (300.0, 100.0), 60.0)
    assert p[0] > 100.0  # to the right of arm1
    assert _close(p, (160.0, 100.0))


def test_dock_point_inverted_direction():
    """Symmetric: from arm2 toward arm1 is left of arm2."""
    p = dock_point((300.0, 100.0), (100.0, 100.0), 60.0)
    assert p[0] < 300.0
    assert _close(p, (240.0, 100.0))
