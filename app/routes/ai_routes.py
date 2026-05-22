"""AI routes — interpreter endpoint (Phase 3) and planner endpoint (Phase 4).

POST /interpret  {"prompt": "..."}
    Runs the Interpreter AI (qwq:32b) and returns a TaskSpec.
    By default runs asynchronously: returns 202 immediately and emits the
    result via SocketIO events (task_spec, log_message).
    Pass {"prompt": "...", "sync": true} for a blocking response (useful
    for testing / curl).

GET /ai/health
    Returns Ollama reachability + loaded model status.
"""

from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify, request

from app import socketio

ai_bp = Blueprint("ai", __name__)


def _emit_log(level: str, message: str, source: str = "interpreter") -> None:
    socketio.emit(
        "log_message",
        {
            "timestamp": time.strftime("%H:%M:%S"),
            "source": source,
            "level": level,
            "message": message,
        },
    )


@ai_bp.route("/interpret", methods=["POST"])
def interpret_prompt():
    """POST /interpret {"prompt": "...[, "sync": true]"}"""
    body = request.get_json(force=True, silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"status": "failed", "error": "prompt is required"}), 400

    sync = bool(body.get("sync", False))

    def _run() -> dict:
        _emit_log(
            "info",
            f"Interpreting: {prompt[:80]}{'…' if len(prompt) > 80 else ''}",
        )
        from app.agents.interpreter import interpret as _interpret
        result = _interpret(prompt)

        if result.get("status") == "success":
            spec = result["task_spec"]
            socketio.emit("task_spec", spec)
            _emit_log(
                "info",
                (
                    f"Task ready: intent={spec['intent']}  "
                    f"packages={spec['package_count']}  "
                    f"AGVs={spec['agv_ids']}  "
                    f"— {spec['description']}"
                ),
            )
        else:
            _emit_log("error", f"Interpret failed: {result.get('error', '?')}")

        return result

    if sync:
        result = _run()
        code = 200 if result.get("status") == "success" else 500
        return jsonify(result), code

    threading.Thread(target=_run, daemon=True).start()
    return (
        jsonify(
            {
                "status": "accepted",
                "message": "interpreting in background — watch task_spec SocketIO event",
            }
        ),
        202,
    )


@ai_bp.route("/ai/tactical", methods=["POST"])
def tactical_decision():
    """POST /ai/tactical {"system": "...", "user": "..."}

    Used by AGV and arm brain containers to invoke qwen2.5:14b for a tactical
    decision without direct Ollama access. The backend serialises all tactical
    calls through the model_gateway, enforcing the VRAM constraint.

    Returns 409 while a planning window (qwq:32b) is open.
    """
    from app.services.llm_service import get_llm, planning_active

    if planning_active():
        return jsonify({
            "status": "busy",
            "error": "planning window open — tactical calls blocked",
        }), 409

    body = request.get_json(force=True, silent=True) or {}
    system_prompt = (body.get("system") or "").strip()
    user_message = (body.get("user") or "").strip()

    if not user_message:
        return jsonify({"status": "failed", "error": "user message required"}), 400

    try:
        result = get_llm().tactical(system_prompt, user_message, num_predict=64)
        if result.get("status") == "failed" and result.get("error") == "planning_window_active":
            return jsonify({"status": "busy",
                            "error": "planning window active"}), 409
        # Surface the parsed "script" key (if qwen14b returned {"script":"..."})
        # alongside the full result so the brain can pick whichever field exists.
        script = result.get("script") or result.get("value") or ""
        return jsonify({
            "status": result.get("status", "success"),
            "script": script,
            "result": script,
            "_model": result.get("_model"),
        })
    except Exception as exc:
        return jsonify({"status": "failed", "error": str(exc)}), 500


@ai_bp.route("/ai/health", methods=["GET"])
def ai_health():
    """GET /ai/health — Ollama reachability + loaded model."""
    from app.services.llm_service import get_llm
    return jsonify(get_llm().health())
