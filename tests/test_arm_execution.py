"""Tests for arm_brain validation + arm_execution.execute_task.

Exercises the four code paths that matter for US #16:
  - happy path: brain decides, arm responds 200, executor returns success
  - brain failure: Qwen errors, executor surfaces it without touching arm
  - arm timeout: Qwen decides, arm server times out, executor reports failure
  - validation: bad LLM output is clamped/whitelisted before reaching the arm

We mock requests.post (used by the executor to reach the arm server) and
ArmBrain.decide_action (so we don't need a live Ollama). Reset of the
arm-brain and arm-executor singleton dicts is done by the fixture so each
test starts clean.
"""

import pytest
import requests
from unittest.mock import MagicMock, patch

from app.services import arm_brain, arm_execution
from app.services.arm_brain import _validate_decision, VALID_ACTIONS


@pytest.fixture(autouse=True)
def reset_singletons():
    """Each test gets fresh per-arm brain and executor instances."""
    arm_brain._arm_brains.clear()
    arm_execution._arm_executors.clear()
    yield
    arm_brain._arm_brains.clear()
    arm_execution._arm_executors.clear()


# ── Validation ───────────────────────────────────────────────────────────


class TestValidateDecision:
    def test_clamps_angle_above_180(self):
        out = _validate_decision(
            {"target_angle": 999, "action": "pick", "confidence": 0.9}
        )
        assert out["target_angle"] == 180.0

    def test_clamps_angle_below_zero(self):
        out = _validate_decision(
            {"target_angle": -45, "action": "drop", "confidence": 0.9}
        )
        assert out["target_angle"] == 0.0

    def test_unknown_action_falls_back_to_move(self):
        out = _validate_decision(
            {"target_angle": 90, "action": "yeet", "confidence": 0.5}
        )
        assert out["action"] == "move"
        assert out["action"] in VALID_ACTIONS

    def test_missing_confidence_defaults_to_half(self):
        out = _validate_decision({"target_angle": 90, "action": "pick"})
        assert out["confidence"] == 0.5

    def test_string_angle_is_coerced(self):
        out = _validate_decision(
            {"target_angle": "47.5", "action": "pick", "confidence": 0.8}
        )
        assert out["target_angle"] == 47.5

    def test_failure_dict_passes_through(self):
        failure = {"status": "failed", "error": "Ollama unreachable"}
        out = _validate_decision(failure)
        assert out is failure  # untouched


# ── Executor: happy path ────────────────────────────────────────────────


class TestExecuteTaskHappyPath:
    def test_returns_success_when_brain_and_arm_both_ok(self):
        executor = arm_execution.get_arm_executor(1)

        # Replace the brain with a fake that returns a deterministic decision.
        fake_brain = MagicMock()
        fake_brain.decide_action.return_value = {
            "action": "pick",
            "target_angle": 180.0,
            "confidence": 0.9,
            "reasoning": "Intake at 180",
            "status": "success",
            "validated": True,
            "arm_id": 1,
            "cache_hit": False,
        }
        executor.brain = fake_brain

        # Mock the arm server's /move response - 200 with a JSON body.
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"status": "ok", "arm_id": 1, "angle": 180}

        with patch(
            "app.services.arm_execution.requests.post", return_value=mock_resp
        ) as posted:
            result = executor.execute_task("pick object from intake")

        assert result["status"] == "success"
        assert result["arm_id"] == 1
        assert result["action"] == "pick"
        assert result["target_angle"] == 180.0
        assert result["confidence"] == 0.9

        # Brain was called with the actual task string (Bug #1 regression guard).
        fake_brain.decide_action.assert_called_once()
        call_args = fake_brain.decide_action.call_args
        assert call_args[0][0] == "pick object from intake"

        # Executor POSTed the brain-decided angle.
        posted.assert_called_once()
        body = posted.call_args.kwargs.get("json") or posted.call_args[1].get("json")
        assert body == {"angle": 180.0}


# ── Executor: brain failure ─────────────────────────────────────────────


class TestExecuteTaskBrainFailure:
    def test_returns_failed_without_calling_arm_server(self):
        executor = arm_execution.get_arm_executor(2)
        executor.brain = MagicMock()
        executor.brain.decide_action.return_value = {
            "status": "failed",
            "error": "Ollama timeout",
            "decision": "error",
            "arm_id": 2,
        }

        with patch("app.services.arm_execution.requests.post") as posted:
            result = executor.execute_task("pick object from intake")

        assert result["status"] == "failed"
        assert "Brain error" in result["reason"]
        # Arm server must NOT be hit when the brain itself failed.
        posted.assert_not_called()


# ── Executor: arm-server timeout ────────────────────────────────────────


class TestExecuteTaskArmTimeout:
    def test_returns_failed_when_arm_server_times_out(self):
        # arm_id=2 because current pipeline.yaml only has arms 1 + 2.
        # arm_state validates the id before reaching the timeout path.
        executor = arm_execution.get_arm_executor(2)
        executor.brain = MagicMock()
        executor.brain.decide_action.return_value = {
            "action": "drop",
            "target_angle": 0.0,
            "confidence": 0.85,
            "reasoning": "Output at 0",
            "status": "success",
            "validated": True,
            "arm_id": 2,
            "cache_hit": False,
        }

        with patch(
            "app.services.arm_execution.requests.post",
            side_effect=requests.exceptions.Timeout(),
        ):
            result = executor.execute_task("drop at output")

        assert result["status"] == "failed"
        assert "timeout" in result["reason"].lower()
        assert result["action"] == "drop"
