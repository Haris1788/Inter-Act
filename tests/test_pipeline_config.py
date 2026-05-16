"""Tests for pipeline_config — the Single Source of Truth module.

Verifies handover state names scale dynamically with arm count, and that
the SSOT contract (no hardcoded handover_a/b, etc.) holds.
"""

from unittest.mock import patch

from app.models import pipeline_config


class TestValidHandoverStates:
    """get_valid_handover_states() must return handover_1..N-1 for N arms."""

    def test_current_config_produces_sequential_handover_names(self):
        """With the default config, handover states should be handover_1..handover_{N-1}."""
        n = pipeline_config.get_arm_count()
        states = pipeline_config.get_valid_handover_states()

        # Contract: intake, carried, handover_1..N-1, output
        assert states[0] == "intake"
        assert states[1] == "carried"
        assert states[-1] == "output"

        # Middle section is exactly handover_1..handover_{N-1}
        middle = states[2:-1]
        assert len(middle) == n - 1
        for i, name in enumerate(middle, start=1):
            assert name == f"handover_{i}"

    def test_no_legacy_handover_a_or_b(self):
        """Legacy handover_a / handover_b names must not appear anywhere in the states list."""
        states = pipeline_config.get_valid_handover_states()
        assert "handover_a" not in states
        assert "handover_b" not in states

    def test_two_arm_config_produces_one_handover(self):
        """A 2-arm pipeline has exactly one handover zone named handover_1."""
        with patch.object(pipeline_config, "get_arm_count", return_value=2):
            states = pipeline_config.get_valid_handover_states()
        assert states == ("intake", "carried", "handover_1", "output")

    def test_five_arm_config_produces_four_handovers(self):
        """A 5-arm pipeline has handovers handover_1..handover_4."""
        with patch.object(pipeline_config, "get_arm_count", return_value=5):
            states = pipeline_config.get_valid_handover_states()
        assert states == (
            "intake", "carried",
            "handover_1", "handover_2", "handover_3", "handover_4",
            "output",
        )

    def test_ten_arm_config_produces_nine_handovers(self):
        """Stress: 10-arm pipeline has 9 handovers, all numeric."""
        with patch.object(pipeline_config, "get_arm_count", return_value=10):
            states = pipeline_config.get_valid_handover_states()
        handover_states = [s for s in states if s.startswith("handover_")]
        assert len(handover_states) == 9
        assert handover_states == [f"handover_{i}" for i in range(1, 10)]


class TestGetHandoverName:
    """get_handover_name(i) returns the canonical handover name for transfer index i."""

    def test_handover_name_is_one_indexed(self):
        """Index 0 maps to handover_1 (transfer point between arm 1 and arm 2)."""
        assert pipeline_config.get_handover_name(0) == "handover_1"
        assert pipeline_config.get_handover_name(1) == "handover_2"
        assert pipeline_config.get_handover_name(4) == "handover_5"


class TestDependencyGraphValidation:
    """validate_dependency_graph catches errors before they corrupt runtime state."""

    def test_default_config_is_valid(self):
        """The pipeline.yaml in the repo should always validate."""
        valid, errors = pipeline_config.validate_dependency_graph()
        assert valid is True
        assert errors == []
