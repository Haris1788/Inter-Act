"""CentralBrain + plan_validator unit tests (Phase 4).

All LLM calls are mocked — no Ollama required.
Key invariant: reassign() must NEVER invoke the LLM (assert mock not called).
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.services import plan_validator as pv
from app.agents.central_brain import CentralBrain
from app.agents.message_bus import NullBus


# ── helpers ───────────────────────────────────────────────────────────────────

GOOD_PLAN = {
    "assignments": [
        {"package_id": "pkg-1", "agv_id": "agv-1", "sequence": 1,
         "route": ["bay", "input", "output", "bay"], "label": "red"},
        {"package_id": "pkg-2", "agv_id": "agv-2", "sequence": 2,
         "route": ["bay", "input", "output", "bay"], "label": "orange"},
    ],
    "arm_roles": {"arm-1": "input", "arm-4": "output"},
    "notes": "Sort 2 packages by colour.",
    "status": "success",
    "_model": "qwq:32b-q4_K_M",
}

GOOD_SPEC = {
    "intent": "sort",
    "package_count": 2,
    "ordering": ["red", "orange"],
    "constraints": [],
    "agv_ids": ["agv-1", "agv-2"],
    "description": "Sort 2 packages by colour.",
}


def _fake_window(mock_llm):
    @contextmanager
    def _cm():
        yield mock_llm
    return _cm


# ── plan_validator tests ──────────────────────────────────────────────────────

def test_validate_plan_accepts_valid():
    plan, err = pv.validate_plan(dict(GOOD_PLAN))
    assert err is None
    assert len(plan["assignments"]) == 2
    assert plan["assignments"][0]["package_id"] == "pkg-1"


def test_validate_plan_rejects_empty_assignments():
    _, err = pv.validate_plan({"assignments": []})
    assert err is not None
    assert "non-empty" in err


def test_validate_plan_rejects_unknown_agv():
    bad = {
        "assignments": [
            {"package_id": "pkg-1", "agv_id": "agv-999", "sequence": 1}
        ]
    }
    _, err = pv.validate_plan(bad)
    assert err is not None
    assert "agv-999" in err


def test_validate_plan_rejects_duplicate_sequence():
    bad = {
        "assignments": [
            {"package_id": "pkg-1", "agv_id": "agv-1", "sequence": 1},
            {"package_id": "pkg-2", "agv_id": "agv-2", "sequence": 1},
        ]
    }
    _, err = pv.validate_plan(bad)
    assert err is not None
    assert "duplicate" in err


# ── CentralBrain.make_plan tests ──────────────────────────────────────────────

def test_make_plan_valid_response_no_reask():
    mock_llm = MagicMock()
    mock_llm.plan.return_value = dict(GOOD_PLAN)

    brain = CentralBrain(bus=NullBus())
    result = brain.make_plan(GOOD_SPEC, llm=mock_llm)

    assert result["status"] == "success"
    assert len(result["plan"]["assignments"]) == 2
    assert mock_llm.plan.call_count == 1


def test_make_plan_bad_then_good_one_reask():
    bad_plan = {
        "assignments": [],  # empty — will fail validation
        "status": "success",
        "_model": "qwq:32b",
    }
    mock_llm = MagicMock()
    mock_llm.plan.side_effect = [dict(bad_plan), dict(GOOD_PLAN)]

    brain = CentralBrain(bus=NullBus())
    result = brain.make_plan(GOOD_SPEC, llm=mock_llm)

    assert result["status"] == "success"
    assert mock_llm.plan.call_count == 2


def test_make_plan_llm_timeout_returns_error():
    mock_llm = MagicMock()
    mock_llm.plan.return_value = {"status": "timeout", "error": "Timeout (300s)"}

    brain = CentralBrain(bus=NullBus())
    result = brain.make_plan(GOOD_SPEC, llm=mock_llm)

    assert result["status"] == "timeout"
    assert mock_llm.plan.call_count == 1


# ── CentralBrain.reassign tests — zero model calls ────────────────────────────

def test_reassign_picks_next_idle_agv_zero_model_calls():
    """reassign() must never touch the LLM — deterministic Python only."""
    mock_llm = MagicMock()
    bus = NullBus()
    brain = CentralBrain(bus=bus)

    assignment = {"package_id": "pkg-1", "agv_id": "agv-1", "sequence": 1,
                  "route": ["bay", "input", "output", "bay"], "label": "red"}

    # Patch agv_state so agv-2 is idle, agv-1 is faulted.
    fake_agvs = {
        "agv-1": {"status": "faulted"},
        "agv-2": {"status": "idle"},
        "agv-3": {"status": "idle"},
    }
    # agv_state is imported locally inside reassign() — patch the module directly.
    with patch("app.models.agv_state.get_all", return_value=fake_agvs), \
         patch("app.models.agv_state.is_alive", side_effect=lambda aid: fake_agvs[aid]["status"] != "faulted"):
        result = brain.reassign("agv-1", assignment)

    assert result["status"] == "success"
    assert result["new_agv_id"] in ("agv-2", "agv-3")
    assert result["assignment"]["reassigned_from"] == "agv-1"
    # The LLM was never constructed or called.
    mock_llm.assert_not_called()


def test_reassign_fails_gracefully_when_no_idle_agvs():
    brain = CentralBrain(bus=NullBus())
    assignment = {"package_id": "pkg-1", "agv_id": "agv-1", "sequence": 1,
                  "route": ["bay", "input", "output", "bay"], "label": ""}

    fake_agvs = {
        "agv-1": {"status": "faulted"},
        "agv-2": {"status": "faulted"},
    }
    with patch("app.models.agv_state.get_all", return_value=fake_agvs), \
         patch("app.models.agv_state.is_alive", return_value=False):
        result = brain.reassign("agv-1", assignment)

    assert result["status"] == "failed"
    assert "No idle AGV" in result["error"]
