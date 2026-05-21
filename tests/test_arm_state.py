"""Tests for arm_state module - state management and validation.

All arm IDs are sourced from pipeline_config.get_arm_ids() so these tests
scale automatically when pipeline.yaml changes arm count.
"""

import pytest
from app.models import arm_state
from app.models.pipeline_config import get_arm_count, get_arm_ids

# Derive canonical arm IDs once per test session - tests adapt to any arm count.
ARM_IDS = get_arm_ids()
FIRST_ARM = ARM_IDS[0]
LAST_ARM = ARM_IDS[-1]


class TestInitialState:
    """Tests for default arm state."""

    def test_initial_state_all_at_90(self):
        """All arms should start at 90.0 degrees."""
        state = arm_state.get_all()
        assert len(state["arms"]) == get_arm_count()
        for arm in state["arms"]:
            assert arm["angle"] == 90.0

    def test_initial_status_is_idle(self):
        """All arms should start with idle status."""
        for arm_id in get_arm_ids():
            state = arm_state.get_state(arm_id)
            assert state["status"] == "idle"


class TestSetAngle:
    """Tests for set_angle validation and behavior."""

    def test_set_valid_angle(self):
        """Setting a valid angle should succeed and be retrievable."""
        result = arm_state.set_angle(FIRST_ARM, 45.0)
        assert result is True
        assert arm_state.get_state(FIRST_ARM)["angle"] == 45.0

    def test_set_boundary_angle_zero(self):
        """Angle 0.0 (minimum) should be accepted."""
        arm_state.set_angle(FIRST_ARM, 0.0)
        assert arm_state.get_state(FIRST_ARM)["angle"] == 0.0

    def test_set_boundary_angle_180(self):
        """Angle 180.0 (maximum) should be accepted."""
        arm_state.set_angle(FIRST_ARM, 180.0)
        assert arm_state.get_state(FIRST_ARM)["angle"] == 180.0

    def test_set_integer_angle_converts_to_float(self):
        """Integer angles should be accepted and stored as float."""
        arm_state.set_angle(FIRST_ARM, 45)
        assert arm_state.get_state(FIRST_ARM)["angle"] == 45.0

    def test_reject_angle_below_0(self):
        """Negative angles should raise ValueError."""
        with pytest.raises(ValueError, match="out of range"):
            arm_state.set_angle(FIRST_ARM, -1.0)

    def test_reject_angle_above_180(self):
        """Angles above 180 should raise ValueError."""
        with pytest.raises(ValueError, match="out of range"):
            arm_state.set_angle(FIRST_ARM, 181.0)

    def test_reject_invalid_arm_id_zero(self):
        """arm_id 0 should raise ValueError (arm IDs start at 1)."""
        with pytest.raises(ValueError, match="Invalid arm_id"):
            arm_state.set_angle(0, 45.0)

    def test_reject_invalid_arm_id_beyond_config(self):
        """arm_id beyond configured count should raise ValueError."""
        invalid_id = max(ARM_IDS) + 1
        with pytest.raises(ValueError, match="Invalid arm_id"):
            arm_state.set_angle(invalid_id, 45.0)

    def test_reject_non_numeric_angle(self):
        """Non-numeric angle should raise ValueError."""
        with pytest.raises(ValueError, match="must be a number"):
            arm_state.set_angle(FIRST_ARM, "hello")


class TestGetState:
    """Tests for get_state and get_all."""

    def test_get_state_returns_dict(self):
        """get_state should return a dict with expected keys."""
        state = arm_state.get_state(FIRST_ARM)
        assert "arm_id" in state
        assert "angle" in state
        assert "status" in state

    def test_get_all_returns_configured_arms(self):
        """get_all should return the configured number of arm states."""
        state = arm_state.get_all()
        assert len(state["arms"]) == get_arm_count()

    def test_get_state_invalid_arm_id(self):
        """get_state with invalid arm_id should raise ValueError."""
        invalid_id = max(ARM_IDS) + 100
        with pytest.raises(ValueError, match="Invalid arm_id"):
            arm_state.get_state(invalid_id)

    def test_set_one_arm_does_not_affect_others(self):
        """Setting one arm's angle should not change any other arm."""
        # Reset all arms to 90 first
        for arm_id in ARM_IDS:
            arm_state.set_angle(arm_id, 90.0)

        arm_state.set_angle(FIRST_ARM, 30.0)

        # Every other arm should still be at 90
        for arm_id in ARM_IDS:
            if arm_id == FIRST_ARM:
                assert arm_state.get_state(arm_id)["angle"] == 30.0
            else:
                assert arm_state.get_state(arm_id)["angle"] == 90.0
