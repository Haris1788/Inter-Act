"""Unit tests for the swap-aware two-model Ollama client.

These mock `requests` so they need neither pytest network access nor the
Clausthal tunnel. Run: `pytest tests/test_llm_service.py -q` (needs a venv with
requirements installed — system Python may lack pytest).
"""

import importlib
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def llm():
    """Fresh module each test so the module-level gateway state is isolated."""
    import app.services.llm_service as mod

    importlib.reload(mod)
    return mod


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def test_warmup_before_first_real_call(llm):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json["prompt"])
        return _resp({"response": '{"ok": true}'})

    with patch.object(llm.requests, "post", side_effect=fake_post):
        out = llm.get_llm().plan("SYS", "USER")

    assert out["status"] == "success" and out["ok"] is True
    # First call must be the tiny warmup ("ok"), then the real prompt.
    assert calls[0] == "ok"
    assert "SYS" in calls[1] and "USER" in calls[1]
    assert llm.loaded_model() == llm.MODEL_PLANNER


def test_no_swap_when_same_model(llm):
    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append(json["model"])
        return _resp({"response": "{}"})

    with patch.object(llm.requests, "post", side_effect=fake_post):
        client = llm.get_llm()
        client.plan("S", "U")          # warmup + call
        client.plan("S", "U2")         # same model -> NO second warmup
    # 1 warmup + 2 real generations == 3 posts total (not 4).
    assert len(posts) == 3
    assert all(m == llm.MODEL_PLANNER for m in posts)


def test_swap_triggers_new_warmup(llm):
    seq = []

    def fake_post(url, json=None, timeout=None):
        seq.append((json["model"], json["prompt"]))
        return _resp({"response": "{}"})

    with patch.object(llm.requests, "post", side_effect=fake_post):
        client = llm.get_llm()
        client.plan("S", "U")          # planner warmup + call
        client.tactical("S", "U")      # swap -> tactical warmup + call

    models = [m for m, _ in seq]
    assert models == [
        llm.MODEL_PLANNER, llm.MODEL_PLANNER,      # warmup, plan
        llm.MODEL_TACTICAL, llm.MODEL_TACTICAL,    # warmup, tactical
    ]


def test_parse_failure_returns_structured_error(llm):
    with patch.object(
        llm.requests, "post", return_value=_resp({"response": "not json at all"})
    ):
        out = llm.get_llm().plan("S", "U")
    assert out["status"] == "failed"
    assert "Invalid JSON" in out["error"]


def test_tactical_blocked_during_planning_window(llm):
    with patch.object(llm.requests, "post", return_value=_resp({"response": "{}"})):
        with llm.planning_window():
            out = llm.get_llm().tactical("S", "U")
    assert out["status"] == "failed"
    assert out["error"] == "planning_window_active"


def test_health_reports_model_presence(llm):
    tags = {"models": [{"name": llm.MODEL_PLANNER}, {"name": llm.MODEL_TACTICAL}]}
    with patch.object(llm.requests, "get", return_value=_resp(tags)):
        h = llm.get_llm().health()
    assert h["status"] == "connected"
    assert h["planner_present"] and h["tactical_present"]
