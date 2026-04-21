"""Tests for pipeline_config — the Single Source of Truth module.

Verifies handoff state names scale dynamically with arm count, and that
the SSOT contract (no hardcoded handoff_a/b, etc.) holds.
"""

from unittest.mock import patch

from app.models import pipeline_config


class TestValidHandoffStates:
    """get_valid_handoff_states() must return handoff_1..N-1 for N arms."""

    def test_current_config_produces_sequential_handoff_names(self):
        """With the default config, handoff states should be handoff_1..handoff_{N-1}."""
        n = pipeline_config.get_arm_count()
        states = pipeline_config.get_valid_handoff_states()

        # Contract: intake, carried, handoff_1..N-1, output
        assert states[0] == "intake"
        assert states[1] == "carried"
        assert states[-1] == "output"

        # Middle section is exactly handoff_1..handoff_{N-1}
        middle = states[2:-1]
        assert len(middle) == n - 1
        for i, name in enumerate(middle, start=1):
            assert name == f"handoff_{i}"

    def test_no_legacy_handoff_a_or_b(self):
        """Legacy handoff_a / handoff_b names must not appear anywhere in the states list."""
        states = pipeline_config.get_valid_handoff_states()
        assert "handoff_a" not in states
        assert "handoff_b" not in states

    def test_two_arm_config_produces_one_handoff(self):
        """A 2-arm pipeline has exactly one handoff zone named handoff_1."""
        with patch.object(pipeline_config, "get_arm_count", return_value=2):
            states = pipeline_config.get_valid_handoff_states()
        assert states == ("intake", "carried", "handoff_1", "output")

    def test_five_arm_config_produces_four_handoffs(self):
        """A 5-arm pipeline has handoffs handoff_1..handoff_4."""
        with patch.object(pipeline_config, "get_arm_count", return_value=5):
            states = pipeline_config.get_valid_handoff_states()
        assert states == (
            "intake", "carried",
            "handoff_1", "handoff_2", "handoff_3", "handoff_4",
            "output",
        )

    def test_ten_arm_config_produces_nine_handoffs(self):
        """Stress: 10-arm pipeline has 9 handoffs, all numeric."""
        with patch.object(pipeline_config, "get_arm_count", return_value=10):
            states = pipeline_config.get_valid_handoff_states()
        handoff_states = [s for s in states if s.startswith("handoff_")]
        assert len(handoff_states) == 9
        assert handoff_states == [f"handoff_{i}" for i in range(1, 10)]


class TestGetHandoffName:
    """get_handoff_name(i) returns the canonical handoff name for transfer index i."""

    def test_handoff_name_is_one_indexed(self):
        """Index 0 maps to handoff_1 (transfer point between arm 1 and arm 2)."""
        assert pipeline_config.get_handoff_name(0) == "handoff_1"
        assert pipeline_config.get_handoff_name(1) == "handoff_2"
        assert pipeline_config.get_handoff_name(4) == "handoff_5"


class TestDependencyGraphValidation:
    """validate_dependency_graph catches errors before they corrupt runtime state."""

    def test_default_config_is_valid(self):
        """The pipeline.yaml in the repo should always validate."""
        valid, errors = pipeline_config.validate_dependency_graph()
        assert valid is True
        assert errors == []
