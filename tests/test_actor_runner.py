"""Unit tests for scripts/actor_runner.py — env validation and module import.

Track B spec (item 7) requires:
- Module imports without error.
- main() exits nonzero with a clear stderr message when ACTOR_ID, ACTOR_KIND, or
  REDIS_URL is missing.
- main() exits nonzero when ACTOR_KIND is an invalid value.

These tests do NOT spin up Redis or real agent threads (Track D covers that).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load the module without executing main()
# ---------------------------------------------------------------------------

_RUNNER_PATH = Path(__file__).parents[1] / "scripts" / "actor_runner.py"


def _load_runner() -> types.ModuleType:
    """Import actor_runner from the filesystem without running it."""
    spec = importlib.util.spec_from_file_location("actor_runner", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_imports_without_error():
    """The module must be importable without side-effects."""
    mod = _load_runner()
    assert hasattr(mod, "main"), "actor_runner must expose a main() function"
    assert callable(mod.main)


# ---------------------------------------------------------------------------
# _validate_env — env validation helper
# ---------------------------------------------------------------------------

def _run_validate(env: dict[str, str]) -> tuple[int | None, str]:
    """Run _validate_env() under the given env.

    Returns (exit_code, stderr_output).
    exit_code is None when _validate_env returns normally (no sys.exit).
    """
    mod = _load_runner()
    stderr_output: list[str] = []

    original_print = __builtins__["print"] if isinstance(__builtins__, dict) else print

    import builtins

    captured_stderr: list[str] = []

    def _fake_print(*args, file=None, **kwargs):
        if file is sys.stderr:
            captured_stderr.append(" ".join(str(a) for a in args))
        else:
            original_print(*args, file=file, **kwargs)

    exit_code: list[int | None] = [None]

    class _FakeExit(Exception):
        def __init__(self, code):
            self.code = code

    def _fake_exit(code=0):
        exit_code[0] = code
        raise _FakeExit(code)

    with (
        patch.dict("os.environ", env, clear=True),
        patch.object(builtins, "print", side_effect=_fake_print),
        patch("sys.exit", side_effect=_fake_exit),
    ):
        try:
            mod._validate_env()
        except _FakeExit:
            pass

    return exit_code[0], " ".join(captured_stderr)


class TestValidateEnv:
    _GOOD_ENV = {
        "ACTOR_ID": "arm-1",
        "ACTOR_KIND": "arm",
        "REDIS_URL": "redis://localhost:6379/0",
    }

    def test_valid_env_returns_tuple(self):
        mod = _load_runner()
        with patch.dict("os.environ", self._GOOD_ENV, clear=True):
            result = mod._validate_env()
        assert result == ("arm-1", "arm", "redis://localhost:6379/0")

    def test_missing_actor_id_exits_nonzero(self):
        env = {k: v for k, v in self._GOOD_ENV.items() if k != "ACTOR_ID"}
        code, stderr = _run_validate(env)
        assert code is not None and code != 0, "must exit nonzero when ACTOR_ID missing"
        assert "ACTOR_ID" in stderr, "stderr must name the missing variable"

    def test_missing_actor_kind_exits_nonzero(self):
        env = {k: v for k, v in self._GOOD_ENV.items() if k != "ACTOR_KIND"}
        code, stderr = _run_validate(env)
        assert code is not None and code != 0
        assert "ACTOR_KIND" in stderr

    def test_missing_redis_url_exits_nonzero(self):
        env = {k: v for k, v in self._GOOD_ENV.items() if k != "REDIS_URL"}
        code, stderr = _run_validate(env)
        assert code is not None and code != 0
        assert "REDIS_URL" in stderr

    def test_missing_all_required_exits_nonzero_and_lists_them(self):
        code, stderr = _run_validate({})
        assert code is not None and code != 0
        # All three must be named
        for var in ("ACTOR_ID", "ACTOR_KIND", "REDIS_URL"):
            assert var in stderr, f"{var} must appear in error message"

    def test_invalid_actor_kind_exits_nonzero(self):
        env = {**self._GOOD_ENV, "ACTOR_KIND": "conveyor"}
        code, stderr = _run_validate(env)
        assert code is not None and code != 0
        assert "ACTOR_KIND" in stderr

    def test_valid_agv_kind_accepted(self):
        env = {**self._GOOD_ENV, "ACTOR_KIND": "agv", "ACTOR_ID": "agv-1"}
        mod = _load_runner()
        with patch.dict("os.environ", env, clear=True):
            result = mod._validate_env()
        assert result == ("agv-1", "agv", "redis://localhost:6379/0")

    def test_whitespace_stripped(self):
        env = {
            "ACTOR_ID": "  arm-2  ",
            "ACTOR_KIND": "  arm  ",
            "REDIS_URL": "  redis://host:6379/0  ",
        }
        mod = _load_runner()
        with patch.dict("os.environ", env, clear=True):
            actor_id, actor_kind, redis_url = mod._validate_env()
        assert actor_id == "arm-2"
        assert actor_kind == "arm"
        assert redis_url == "redis://host:6379/0"

    def test_empty_string_env_is_treated_as_missing(self):
        env = {**self._GOOD_ENV, "ACTOR_ID": ""}
        code, stderr = _run_validate(env)
        assert code is not None and code != 0
        assert "ACTOR_ID" in stderr
