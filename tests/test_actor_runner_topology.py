"""Phase 5 Stage 3 — actor_runner._load_profile priority tests.

Tests the three priority levels:
  1. Env ACTOR_X + ACTOR_Y → direct CapabilityProfile from env.
  2. Topology lookup via get_topology_or_legacy_shim() when env absent.
  3. Legacy capability.load_profiles() fallback.
"""

from __future__ import annotations

import importlib.util
import logging
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load actor_runner without executing main()
# ---------------------------------------------------------------------------

_RUNNER_PATH = Path(__file__).parents[1] / "scripts" / "actor_runner.py"


def _load_runner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("actor_runner", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("test_actor_runner_topology")
    logger.setLevel(logging.CRITICAL)
    logger.handlers = []
    return logger


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadProfileFromEnvXY:
    def test_env_x_y_builds_profile_from_env(self):
        """Priority 1: ACTOR_X + ACTOR_Y set → profile built directly from env."""
        mod = _load_runner()
        logger = _silent_logger()

        env = {
            "ACTOR_ID": "arm-1",
            "ACTOR_KIND": "arm",
            "REDIS_URL": "redis://localhost:6379/0",
            "ACTOR_X": "120.0",
            "ACTOR_Y": "300.0",
            "ACTOR_REACH_PX": "140.0",
            "ACTOR_CAPACITY": "2",
            "ACTOR_SPEED": "1.5",
        }
        with patch.dict("os.environ", env, clear=True):
            profile = mod._load_profile("arm-1", logger)

        assert profile.actor_id == "arm-1"
        assert profile.kind == "arm"
        assert profile.position_px == pytest.approx((120.0, 300.0))
        assert profile.reach_px == pytest.approx(140.0)
        assert profile.capacity == 2
        assert profile.speed == pytest.approx(1.5)
        # Arms get their own id as reachable zone.
        assert "arm-1" in profile.reachable_zones

    def test_env_x_y_agv_profile(self):
        """Priority 1 also works for AGV kind."""
        mod = _load_runner()
        logger = _silent_logger()

        env = {
            "ACTOR_ID": "agv-1",
            "ACTOR_KIND": "agv",
            "REDIS_URL": "redis://localhost:6379/0",
            "ACTOR_X": "400.0",
            "ACTOR_Y": "600.0",
        }
        with patch.dict("os.environ", env, clear=True):
            profile = mod._load_profile("agv-1", logger)

        assert profile.actor_id == "agv-1"
        assert profile.kind == "agv"
        assert profile.position_px == pytest.approx((400.0, 600.0))


class TestLoadProfileFromTopology:
    def test_topology_lookup_when_env_absent(self, monkeypatch):
        """Priority 2: no ACTOR_X/Y → lookup in topology shim."""
        mod = _load_runner()
        logger = _silent_logger()

        from app.models.topology import ActorSpec, Topology

        topo = Topology(
            actors=(
                ActorSpec(id="arm-3", kind="arm", x=240.0, y=300.0, reach_px=130.0),
            )
        )

        import app.models.pipeline_config as pc
        monkeypatch.setattr(pc, "get_topology_or_legacy_shim", lambda: topo)

        env = {
            "ACTOR_ID": "arm-3",
            "ACTOR_KIND": "arm",
            "REDIS_URL": "redis://localhost:6379/0",
            # No ACTOR_X / ACTOR_Y
        }
        with patch.dict("os.environ", env, clear=True):
            profile = mod._load_profile("arm-3", logger)

        assert profile.actor_id == "arm-3"
        assert profile.kind == "arm"
        assert profile.position_px == pytest.approx((240.0, 300.0))
        assert profile.reach_px == pytest.approx(130.0)
        assert "arm-3" in profile.reachable_zones


class TestLoadProfileLegacyFallback:
    def test_legacy_fallback_when_no_env_and_no_topology_actor(self, monkeypatch):
        """Priority 3: env absent, actor not in topology → legacy load_profiles()."""
        mod = _load_runner()
        logger = _silent_logger()

        from app.models.topology import Topology  # empty topology
        empty_topo = Topology(actors=())

        import app.models.pipeline_config as pc
        import app.agents.capability as cap
        monkeypatch.setattr(pc, "get_topology_or_legacy_shim", lambda: empty_topo)

        from app.agents.capability import CapabilityProfile
        legacy_profile = CapabilityProfile(
            actor_id="arm-1", kind="arm", reachable_zones=["input_arm"]
        )
        monkeypatch.setattr(cap, "load_profiles", lambda: [legacy_profile])

        env = {
            "ACTOR_ID": "arm-1",
            "ACTOR_KIND": "arm",
            "REDIS_URL": "redis://localhost:6379/0",
        }
        with patch.dict("os.environ", env, clear=True):
            profile = mod._load_profile("arm-1", logger)

        assert profile.actor_id == "arm-1"
        assert "input_arm" in profile.reachable_zones
