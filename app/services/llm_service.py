"""
llm_service — shared, swap-aware two-model Ollama client for the AI layer.

WHY THIS EXISTS
===============
`app/services/qwen_brain.py` already speaks to Ollama (single model, 30s timeout)
and the per-arm AI depends on it — it is left untouched. The multi-agent AI layer
(interpreter / planner / agent brains) needs TWO models on the shared TU Clausthal
GPU reached through the reverse SSH tunnel:

    interpreter + master/planner  ->  qwq:32b-q4_K_M        (heavy reasoning)
    per-AGV / per-arm tactical    ->  qwen2.5:14b-instruct-q4_K_M

THE HARD CONSTRAINT
===================
The shared GPU holds ONE model in VRAM at a time. Swapping qwq:32b <-> qwen14b
pays a cold load that can take minutes (see test_ollama_two_model.py). So every
call funnels through a single serialized `_model_gateway`:

  * a planning window keeps qwq:32b resident for interpret -> plan (no swap),
  * one swap to qwen14b then keeps it resident for the whole execution run,
  * `tactical()` is refused while a planning window is open (callers map to 409),
  * every swap emits a UI-visible `model_swap` event so the cold load is not a
    mysterious freeze.

Reuses the proven request/parse contract from qwen_brain (`/api/generate`,
`format:json`, `temperature:0.1`, defensive json.loads, `status` field) and the
warmup / timeout / tunnel-default conventions from test_ollama_two_model.py.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

import requests

# ── Configuration (env-driven, same pattern as qwen_brain) ───────────────────
# Default to the reverse-tunnel endpoint on the company server (the real
# Clausthal GPU models). qwen_brain keeps its own 11434 default for the local
# CPU 0.5b model — these two clients are intentionally separate.
BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11500")
MODEL_PLANNER = os.environ.get("OLLAMA_MODEL_PLANNER", "qwq:32b-q4_K_M")
MODEL_TACTICAL = os.environ.get("OLLAMA_MODEL_TACTICAL", "qwen2.5:14b-instruct-q4_K_M")

# A cold VRAM load on the shared GPU can take minutes; the generation cap is
# generous because the model is already warm by then.
WARMUP_TIMEOUT = int(os.environ.get("OLLAMA_WARMUP_TIMEOUT", "600"))
GEN_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "300"))
TEMPERATURE = float(os.environ.get("OLLAMA_TEMPERATURE", "0.1"))

# When 0 (default), the planner is never re-invoked mid-run — reassignment is
# deterministic Python (no model call), so qwen14b stays resident. When 1, a
# one-time swap back to the planner for an explicit replan is tolerated.
ALLOW_MIDRUN_REPLAN = os.environ.get("ALLOW_MIDRUN_REPLAN", "0") == "1"


# ── Best-effort UI event (no hard dependency on the Flask app) ───────────────
def _emit(event: str, data: Dict[str, Any]) -> None:
    """Push a SocketIO event if the app is up; never raise, never block import.

    Imported lazily so this module can be unit-tested / imported without a
    running Flask app (mirrors how notifications.py emits, but fault-tolerant).
    """
    try:
        from app import socketio  # local import on purpose

        socketio.emit(event, data)
        if event != "log_message":
            socketio.emit(
                "log_message",
                {
                    "timestamp": time.strftime("%H:%M:%S"),
                    "source": "llm",
                    "message": data.get("message", event),
                    "level": data.get("level", "info"),
                },
            )
    except Exception:
        # No app context (tests, CLI) or socket not ready — log to console.
        print(f"[llm_service] {event}: {data.get('message', data)}")


# ── Model gateway: one model resident at a time, swaps are explicit ──────────
_gw_lock = threading.Lock()
_loaded_model: Optional[str] = None

# Planning-window guard. Re-entrant by depth so nested plan()+interpret() inside
# one `with planning_window()` does not double-toggle.
_pw_lock = threading.Lock()
_pw_depth = 0


class PlanningWindowActive(RuntimeError):
    """Raised/returned when a tactical call is attempted during planning."""


def planning_active() -> bool:
    with _pw_lock:
        return _pw_depth > 0


def loaded_model() -> Optional[str]:
    with _gw_lock:
        return _loaded_model


def _raw_generate(model: str, prompt: str, *, num_predict: int, timeout: int) -> dict:
    """Low-level POST /api/generate (mirrors test_ollama_two_model._generate)."""
    r = requests.post(
        f"{BASE_URL}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "temperature": TEMPERATURE,
            "options": {"num_predict": num_predict},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def _ensure_model(model: str) -> None:
    """Guarantee `model` is the one resident in GPU VRAM.

    Serialized by `_gw_lock`: only one swap can be in flight, and a swap is a
    loud, logged event because it can stall for minutes.
    """
    global _loaded_model
    with _gw_lock:
        if _loaded_model == model:
            return
        previous = _loaded_model
        _emit(
            "model_swap",
            {
                "from": previous,
                "to": model,
                "message": (
                    f"AI model swap: {previous or 'none'} -> {model} "
                    f"(cold GPU load, may take minutes)"
                ),
                "level": "warning",
            },
        )
        try:
            # Tiny throwaway prompt forces Ollama to pull the model into VRAM.
            _raw_generate(model, "ok", num_predict=1, timeout=WARMUP_TIMEOUT)
            _loaded_model = model
            _emit(
                "model_swap",
                {
                    "from": previous,
                    "to": model,
                    "message": f"AI model ready: {model}",
                    "level": "info",
                },
            )
        except Exception as exc:
            # Leave _loaded_model unchanged; the next call retries the warmup.
            _emit(
                "model_swap",
                {
                    "from": previous,
                    "to": model,
                    "message": f"AI model load FAILED for {model}: {exc}",
                    "level": "error",
                },
            )
            raise


def _generate_json(
    model: str, system_prompt: str, user_message: str, *, num_predict: int, timeout: int
) -> Dict[str, Any]:
    """Call `model` and parse a JSON object reply.

    Returns the parsed object with an added `status: success`, or a structured
    error dict (`status` in failed/timeout/exception) — same contract as
    qwen_brain.ask_decision so callers handle one shape.
    """
    prompt = f"{system_prompt}\n\n{user_message}"
    try:
        result = _raw_generate(model, prompt, num_predict=num_predict, timeout=timeout)
        text = (result.get("response") or "").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {
                "status": "failed",
                "error": f"Invalid JSON from {model}: {text[:200]}",
                "raw": text,
            }
        if not isinstance(parsed, dict):
            parsed = {"value": parsed}
        parsed["status"] = "success"
        parsed["_model"] = model
        return parsed
    except requests.Timeout:
        return {"status": "timeout", "error": f"Timeout ({timeout}s) on {model}"}
    except requests.HTTPError as exc:
        return {"status": "failed", "error": f"HTTP {exc} on {model}"}
    except Exception as exc:  # noqa: BLE001 - surface anything as a structured error
        return {"status": "exception", "error": str(exc)}


class LLMService:
    """Swap-aware two-model client. Single chokepoint for the AI layer."""

    base_url = BASE_URL
    planner_model = MODEL_PLANNER
    tactical_model = MODEL_TACTICAL

    # ── reasoning models (qwq:32b) — interpreter + planner ───────────────
    def plan(
        self, system_prompt: str, user_message: str, num_predict: int = 1024
    ) -> Dict[str, Any]:
        """Heavy-reasoning call (qwq:32b). Intended to run inside a
        `planning_window()` so interpret() + plan() share one warm load."""
        _ensure_model(self.planner_model)
        return _generate_json(
            self.planner_model,
            system_prompt,
            user_message,
            num_predict=num_predict,
            timeout=GEN_TIMEOUT,
        )

    # ── tactical model (qwen14b) — per-agent brains ──────────────────────
    def tactical(
        self, system_prompt: str, user_message: str, num_predict: int = 256
    ) -> Dict[str, Any]:
        """Fast per-agent decision (qwen14b). Refused while a planning window
        is open — that is the VRAM-thrash guard. Callers should map a
        `planning_window_active` status to HTTP 409."""
        if planning_active():
            return {
                "status": "failed",
                "error": "planning_window_active",
                "message": "tactical calls are blocked while the planner is running",
            }
        _ensure_model(self.tactical_model)
        return _generate_json(
            self.tactical_model,
            system_prompt,
            user_message,
            num_predict=num_predict,
            timeout=GEN_TIMEOUT,
        )

    # ── health / preflight (mirrors qwen_brain.test_connection) ──────────
    def health(self) -> Dict[str, Any]:
        """Is the tunnel/Ollama reachable, and are both target models present?"""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            return {
                "status": "connected",
                "url": self.base_url,
                "models": models,
                "planner_present": self.planner_model in models,
                "tactical_present": self.tactical_model in models,
                "loaded_model": loaded_model(),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "disconnected",
                "url": self.base_url,
                "error": str(exc),
                "hint": (
                    "Reverse SSH tunnel likely down. On the company server: "
                    "`ss -tlnp | grep 11500`; on cloud-247: `tail ~/revtun.log`."
                ),
            }


@contextmanager
def planning_window():
    """Open a planning window: keeps qwq:32b resident and blocks tactical calls.

    Use around interpret()+plan() so they share one warm load and no tactical
    call can sneak in and force a model swap mid-planning.
    """
    global _pw_depth
    with _pw_lock:
        _pw_depth += 1
    try:
        _ensure_model(MODEL_PLANNER)
        yield get_llm()
    finally:
        with _pw_lock:
            _pw_depth -= 1


# ── singleton (same accessor pattern as qwen_brain.get_qwen_brain) ───────────
_llm: Optional[LLMService] = None


def get_llm() -> LLMService:
    global _llm
    if _llm is None:
        _llm = LLMService()
    return _llm
