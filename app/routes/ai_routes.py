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


@ai_bp.route("/plan", methods=["POST"])
def plan_task():
    """POST /plan {"prompt":"..."} or {"task_spec":{...}[, "sync":true]}

    Atomic interpret+plan: if a raw prompt is given, runs interpret and plan
    inside ONE planning_window (single qwq:32b warm load, no model swap).
    If a pre-built task_spec is given, runs plan only.
    """
    body = request.get_json(force=True, silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    spec_dict = body.get("task_spec")
    sync = bool(body.get("sync", False))

    if not prompt and not spec_dict:
        return jsonify({"status": "failed", "error": "prompt or task_spec required"}), 400

    def _run() -> dict:
        from app.agents.central_brain import get_brain
        from app.agents.message_bus import get_bus
        from app.services.llm_service import planning_window

        brain = get_brain()
        bus = get_bus()

        if prompt:
            _emit_log(
                "info",
                f"Planning: {prompt[:80]}{'…' if len(prompt) > 80 else ''}",
                "planner",
            )
            from app.services.prompt_builder import build_interpreter_prompt
            from app.services import task_spec as ts

            system_interp = build_interpreter_prompt()

            # Single planning_window: interpret + plan share one qwq:32b load.
            with planning_window() as llm:
                interp_raw = llm.plan(system_interp, prompt, num_predict=512)
                if interp_raw.get("status") != "success":
                    _emit_log("error", f"Interpret failed: {interp_raw.get('error')}", "planner")
                    return interp_raw

                spec, err = ts.validate(interp_raw, raw_prompt=prompt)
                if not spec:
                    _emit_log("error", f"Spec invalid: {err}", "planner")
                    return {"status": "failed", "error": err}

                bus.publish("task_spec", spec.to_dict())
                _emit_log(
                    "info",
                    f"Spec: {spec.intent} / {spec.package_count} packages / AGVs={spec.agv_ids}",
                    "planner",
                )

                # Plan with the same warm LLM — no model swap.
                plan_result = brain.make_plan(spec.to_dict(), llm=llm)
        else:
            _emit_log("info", "Planning from provided task_spec", "planner")
            with planning_window() as llm:
                plan_result = brain.make_plan(spec_dict, llm=llm)

        if plan_result.get("status") == "success":
            bus.publish("plan", plan_result["plan"])
            n = len(plan_result["plan"].get("assignments", []))
            _emit_log("info", f"Plan ready: {n} assignments", "planner")
            # Deliver the Master-AI plan to the AGV/arm agents. This runs
            # OUTSIDE the planning_window (closed above), so the agents'
            # tactical qwen14b calls during execution are allowed (the one
            # qwq:32b->qwen14b swap happens here and stays resident).
            try:
                import os as _os
                mode = _os.environ.get("COORDINATION_MODE", "centralized").lower()
                if mode in ("decentralized", "smart"):
                    # US 32 ('decentralized') + US 34 ('smart' superset): master
                    # announces each package to the bus and lets AGVs bid. The
                    # announcer publishes the winning claim and then triggers
                    # the same /assign + arm choreography.
                    from app.agents.master_announcer import get_announcer
                    exec_res = get_announcer().announce_plan(plan_result["plan"])
                    plan_result["execution"] = exec_res
                    mode_tag = "smart" if mode == "smart" else "decentralized"
                    _emit_log(
                        "info",
                        f"Plan announced ({mode_tag} contract-net): "
                        f"{len(exec_res.get('announced', []))} task_announce events",
                        "planner",
                    )
                else:
                    from app.services.plan_executor import dispatch_plan
                    exec_res = dispatch_plan(plan_result["plan"])
                    plan_result["execution"] = exec_res
                    _emit_log(
                        "info",
                        f"Plan handed to agents: {len(exec_res.get('dispatched', []))} "
                        f"AGV(s) executing",
                        "planner",
                    )
            except Exception as exc:  # noqa: BLE001
                _emit_log("error", f"Plan dispatch failed: {exc}", "planner")
        else:
            _emit_log("error", f"Plan failed: {plan_result.get('error', '?')}", "planner")

        return plan_result

    if sync:
        result = _run()
        code = 200 if result.get("status") == "success" else 500
        return jsonify(result), code

    threading.Thread(target=_run, daemon=True).start()
    return (
        jsonify(
            {
                "status": "accepted",
                "message": "planning in background — watch plan SocketIO event",
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
        # Surface ALL parsed JSON keys (bid/score/rationale/eta_s etc.) so
        # decentralized brains can read structured fields directly.
        extras = {k: v for k, v in result.items()
                  if k not in ("script", "value", "status", "_model", "error", "raw")}
        return jsonify({
            "status": result.get("status", "success"),
            "script": script,
            "result": script,
            "raw": result.get("raw", ""),
            "error": result.get("error", ""),
            "parsed": extras,
            "_model": result.get("_model"),
        })
    except Exception as exc:
        return jsonify({"status": "failed", "error": str(exc)}), 500


@ai_bp.route("/ai/health", methods=["GET"])
def ai_health():
    """GET /ai/health — Ollama reachability + loaded model."""
    from app.services.llm_service import get_llm
    return jsonify(get_llm().health())
