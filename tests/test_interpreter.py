"""Interpreter AI (Phase 3) — unit tests with mocked LLM.

All tests use unittest.mock to avoid real Ollama calls.
task_spec.validate() is tested independently; interpreter.interpret()
is tested with a fake planning_window context manager.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.services import task_spec as ts
from app.agents.interpreter import interpret


# ── helpers ───────────────────────────────────────────────────────────────────

GOOD_RESPONSE = {
    "intent": "sort",
    "package_count": 6,
    "ordering": ["red", "orange", "yellow", "green", "blue", "violet"],
    "constraints": [],
    "agv_ids": ["agv-1"],
    "description": "Sort 6 packages by rainbow colour order.",
    "status": "success",
    "_model": "qwq:32b-q4_K_M",
}


def _fake_window(mock_llm):
    """Return a context manager that yields mock_llm — mimics planning_window."""
    @contextmanager
    def _cm():
        yield mock_llm
    return _cm


# ── task_spec.validate() unit tests ──────────────────────────────────────────

def test_validate_accepts_valid_spec():
    spec, err = ts.validate(
        {
            "intent": "sort",
            "package_count": 6,
            "ordering": ["red", "orange", "yellow", "green", "blue", "violet"],
            "constraints": [],
            "agv_ids": ["agv-1", "agv-2"],
            "description": "Sort 6 packages by rainbow colour.",
        },
        raw_prompt="Sort by rainbow",
    )
    assert err is None
    assert spec.intent == "sort"
    assert spec.package_count == 6
    assert len(spec.ordering) == 6


def test_validate_rejects_unknown_intent():
    spec, err = ts.validate(
        {
            "intent": "fly",
            "package_count": 3,
            "ordering": [],
            "constraints": [],
            "agv_ids": [],
            "description": "bad intent",
        }
    )
    assert spec is None
    assert "intent" in err


def test_validate_rejects_out_of_range_package_count():
    spec, err = ts.validate(
        {
            "intent": "move",
            "package_count": 99,
            "ordering": [],
            "constraints": [],
            "agv_ids": [],
            "description": "too many packages",
        }
    )
    assert spec is None
    assert "package_count" in err


def test_validate_rejects_unknown_agv():
    spec, err = ts.validate(
        {
            "intent": "move",
            "package_count": 2,
            "ordering": [],
            "constraints": [],
            "agv_ids": ["agv-999"],
            "description": "unknown agv",
        }
    )
    assert spec is None
    assert "agv-999" in err


def test_validate_to_dict_roundtrip():
    spec, err = ts.validate(
        {
            "intent": "demo",
            "package_count": 1,
            "ordering": [],
            "constraints": ["fast"],
            "agv_ids": [],
            "description": "run demo",
        },
        raw_prompt="demo please",
    )
    assert err is None
    d = spec.to_dict()
    assert d["intent"] == "demo"
    assert d["raw_prompt"] == "demo please"
    assert d["constraints"] == ["fast"]


# ── interpreter.interpret() tests ─────────────────────────────────────────────

def test_interpret_valid_response_no_reask():
    """Valid LLM response → TaskSpec, planning_window entered once."""
    mock_llm = MagicMock()
    mock_llm.plan.return_value = dict(GOOD_RESPONSE)

    with patch("app.agents.interpreter.planning_window", _fake_window(mock_llm)):
        result = interpret("Sort by the colour of the rainbow")

    assert result["status"] == "success"
    assert result["task_spec"]["intent"] == "sort"
    assert mock_llm.plan.call_count == 1  # no re-ask needed


def test_interpret_bad_first_then_good_does_one_reask():
    """First response invalid → one re-ask within the same window → success."""
    bad = {
        "intent": "fly",
        "package_count": 99,
        "ordering": [],
        "constraints": [],
        "agv_ids": [],
        "description": "bad",
        "status": "success",
        "_model": "qwq:32b-q4_K_M",
    }
    mock_llm = MagicMock()
    mock_llm.plan.side_effect = [dict(bad), dict(GOOD_RESPONSE)]

    with patch("app.agents.interpreter.planning_window", _fake_window(mock_llm)):
        result = interpret("Sort by rainbow")

    assert result["status"] == "success"
    assert mock_llm.plan.call_count == 2  # first try + one re-ask


def test_interpret_both_fail_returns_error():
    """Both attempts produce invalid output → structured error, no exception."""
    bad = {
        "intent": "fly",
        "package_count": 99,
        "ordering": [],
        "constraints": [],
        "agv_ids": [],
        "description": "bad",
        "status": "success",
        "_model": "qwq:32b-q4_K_M",
    }
    mock_llm = MagicMock()
    mock_llm.plan.side_effect = [dict(bad), dict(bad)]

    with patch("app.agents.interpreter.planning_window", _fake_window(mock_llm)):
        result = interpret("Do something impossible")

    assert result["status"] == "failed"
    assert "error" in result
    assert mock_llm.plan.call_count == 2


def test_interpret_llm_timeout_returns_error():
    """LLM timeout → structured error dict, no exception raised."""
    mock_llm = MagicMock()
    mock_llm.plan.return_value = {"status": "timeout", "error": "Timeout (300s)"}

    with patch("app.agents.interpreter.planning_window", _fake_window(mock_llm)):
        result = interpret("Sort packages")

    assert result["status"] == "timeout"
    assert "error" in result
    assert mock_llm.plan.call_count == 1  # no re-ask on timeout
