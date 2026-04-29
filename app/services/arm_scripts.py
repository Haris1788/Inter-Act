"""
Named arm behaviours — 3 per arm, 9 total.

A "script" here is a small Python function that performs a complete physical
behaviour for one arm: a sequence of rotations + object events + log lines.
It is the unit Qwen now picks (instead of single angles).

Why this exists
---------------
Asking a 0.5 B model to invent angles led to wrong choices (arm 3 agreeing to
"pick from intake"). Asking it to choose 1 of 3 named behaviours from a per-arm
menu is a classification task it can do reliably — and the chosen name is far
more informative for an operator watching the log panel.

Visibility
----------
Every script step emits a `log_message` over WebSocket so the browser's log
panel shows the whole sequence. The dispatcher also emits a single
`script_chosen` event so the operator sees which behaviour Qwen picked.

Usage
-----
    from app.services.arm_scripts import ARM_SCRIPTS, run_script
    name = "relay_intake_to_handoff_1"   # Qwen chooses this
    result = run_script(arm_id=1, script_name=name)
"""

import time
import requests
from datetime import datetime
from typing import Dict, Any, Callable, List

from app import socketio
from app.models import arm_state


# ────────────────────────────────────────────────────────────────────────────
# Constants — angles each arm uses for each station
# Pivot positions (from scene config, validated earlier):
#   arm 1: x=300   reaches  intake @180°,    handoff_1 @0°
#   arm 2: x=600   reaches  handoff_1 @180°, handoff_2 @0°
#   arm 3: x=900   reaches  handoff_2 @180°, output @0°
# ────────────────────────────────────────────────────────────────────────────

REST_ANGLE = 90.0
STEP_PAUSE = 1.2  # seconds between rotations — visible in the canvas


# ────────────────────────────────────────────────────────────────────────────
# Helpers — emit log + drive the arm
# ────────────────────────────────────────────────────────────────────────────


def _log(arm_id: int, message: str, level: str = "info") -> None:
    """Emit a timestamped log line to the browser log panel."""
    socketio.emit("log_message", {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "source": f"arm_{arm_id}",
        "message": message,
        "level": level,
    })
    print(f"[arm_{arm_id}] {message}")


def _rotate(arm_id: int, target_angle: float) -> None:
    """Rotate this arm by writing arm_state directly + emit state_update."""
    arm_state.set_angle(arm_id, target_angle)
    socketio.emit("state_update", arm_state.get_all())


def _emit_object(object_id: str, state: str, carrier_arm: int = None) -> None:
    """Update an object's position. Mirrors POST /object_event so we update both
    the canonical _object_states dict AND emit the WebSocket event the canvas listens to."""
    # Lazy import to avoid circular import at module load time.
    from app.routes import arm_routes
    payload = {"object_id": object_id, "state": state, "carrier_arm": carrier_arm}
    with arm_routes._object_lock:
        arm_routes._object_states[object_id] = payload
    socketio.emit("object_update", payload)


def _find_object_at(state: str):
    """Return the first real object currently at <state>, or None."""
    from app.routes import arm_routes
    return arm_routes.find_object_at(state)


def _move_step(arm_id: int, target_angle: float, label: str) -> None:
    """One annotated rotation: log + rotate + brief pause for visible animation."""
    _log(arm_id, f"→ {label} (rotate to {target_angle:.0f}°)")
    _rotate(arm_id, target_angle)
    time.sleep(STEP_PAUSE)


# ════════════════════════════════════════════════════════════════════════════
# ARM 1 — left side (intake ↔ handoff 1)
# ════════════════════════════════════════════════════════════════════════════


def arm_1_relay_intake_to_handoff_1(arm_id: int, **_kwargs) -> Dict[str, Any]:
    """Pick the REAL object currently at intake, carry it, drop at handoff 1.
    Refuses (no movement, no fake object) if intake is empty."""
    obj = _find_object_at("intake")
    if obj is None:
        _log(arm_id, "✋ no object at intake — nothing to relay", level="warning")
        return {"script": "arm_1_relay_intake_to_handoff_1",
                "object_id": None, "status": "no_object"}
    object_id = obj["object_id"]

    _log(arm_id, f"▶ START relay_intake_to_handoff_1 (object={object_id})")

    _move_step(arm_id, 180.0, "face intake")
    _emit_object(object_id, "carried", carrier_arm=arm_id)
    _log(arm_id, f"  • picked {object_id}")
    time.sleep(0.4)

    _move_step(arm_id, 0.0, "carry to handoff 1")
    _emit_object(object_id, "handoff_1")
    _log(arm_id, f"  • dropped {object_id} at handoff 1")
    time.sleep(0.4)

    _move_step(arm_id, REST_ANGLE, "return to rest")
    _log(arm_id, "■ DONE relay_intake_to_handoff_1", level="success")
    return {"script": "arm_1_relay_intake_to_handoff_1", "object_id": object_id, "status": "done"}


def arm_1_return_to_rest(arm_id: int, **_kwargs) -> Dict[str, Any]:
    """Return to rest position. Use for abort / reset."""
    _log(arm_id, "▶ START return_to_rest")
    _move_step(arm_id, REST_ANGLE, "return to rest")
    _log(arm_id, "■ DONE return_to_rest", level="success")
    return {"script": "arm_1_return_to_rest", "status": "done"}


def arm_1_hold_at_intake(arm_id: int, **_kwargs) -> Dict[str, Any]:
    """Park facing intake, do nothing — useful when waiting for upstream."""
    _log(arm_id, "▶ START hold_at_intake")
    _move_step(arm_id, 180.0, "face intake (waiting)")
    _log(arm_id, "■ DONE hold_at_intake", level="success")
    return {"script": "arm_1_hold_at_intake", "status": "done"}


# ════════════════════════════════════════════════════════════════════════════
# ARM 2 — middle (handoff 1 ↔ handoff 2)
# ════════════════════════════════════════════════════════════════════════════


def arm_2_relay_handoff1_to_handoff2(arm_id: int, **_kwargs) -> Dict[str, Any]:
    """Pick the REAL object at handoff 1 (left by arm 1), carry, drop at handoff 2.
    Refuses if handoff 1 is empty."""
    obj = _find_object_at("handoff_1")
    if obj is None:
        _log(arm_id, "✋ no object at handoff 1 — nothing to relay", level="warning")
        return {"script": "arm_2_relay_handoff1_to_handoff2",
                "object_id": None, "status": "no_object"}
    object_id = obj["object_id"]

    _log(arm_id, f"▶ START relay_handoff1_to_handoff2 (object={object_id})")

    _move_step(arm_id, 180.0, "face handoff 1")
    _emit_object(object_id, "carried", carrier_arm=arm_id)
    _log(arm_id, f"  • picked {object_id} from handoff 1")
    time.sleep(0.4)

    _move_step(arm_id, 0.0, "carry to handoff 2")
    _emit_object(object_id, "handoff_2")
    _log(arm_id, f"  • dropped {object_id} at handoff 2")
    time.sleep(0.4)

    _move_step(arm_id, REST_ANGLE, "return to rest")
    _log(arm_id, "■ DONE relay_handoff1_to_handoff2", level="success")
    return {"script": "arm_2_relay_handoff1_to_handoff2", "object_id": object_id, "status": "done"}


def arm_2_return_to_rest(arm_id: int, **_kwargs) -> Dict[str, Any]:
    _log(arm_id, "▶ START return_to_rest")
    _move_step(arm_id, REST_ANGLE, "return to rest")
    _log(arm_id, "■ DONE return_to_rest", level="success")
    return {"script": "arm_2_return_to_rest", "status": "done"}


def arm_2_hold_at_handoff1(arm_id: int, **_kwargs) -> Dict[str, Any]:
    _log(arm_id, "▶ START hold_at_handoff1")
    _move_step(arm_id, 180.0, "face handoff 1 (waiting)")
    _log(arm_id, "■ DONE hold_at_handoff1", level="success")
    return {"script": "arm_2_hold_at_handoff1", "status": "done"}


# ════════════════════════════════════════════════════════════════════════════
# ARM 3 — right side (handoff 2 ↔ output)
# ════════════════════════════════════════════════════════════════════════════


def arm_3_relay_handoff2_to_output(arm_id: int, **_kwargs) -> Dict[str, Any]:
    """Pick the REAL object at handoff 2 (left by arm 2), carry, deliver to output.
    Refuses if handoff 2 is empty."""
    obj = _find_object_at("handoff_2")
    if obj is None:
        _log(arm_id, "✋ no object at handoff 2 — nothing to relay", level="warning")
        return {"script": "arm_3_relay_handoff2_to_output",
                "object_id": None, "status": "no_object"}
    object_id = obj["object_id"]

    _log(arm_id, f"▶ START relay_handoff2_to_output (object={object_id})")

    _move_step(arm_id, 180.0, "face handoff 2")
    _emit_object(object_id, "carried", carrier_arm=arm_id)
    _log(arm_id, f"  • picked {object_id} from handoff 2")
    time.sleep(0.4)

    _move_step(arm_id, 0.0, "carry to output")
    _emit_object(object_id, "output")
    _log(arm_id, f"  • delivered {object_id} to output")
    time.sleep(0.4)

    _move_step(arm_id, REST_ANGLE, "return to rest")
    _log(arm_id, "■ DONE relay_handoff2_to_output", level="success")
    return {"script": "arm_3_relay_handoff2_to_output", "object_id": object_id, "status": "done"}


def arm_3_return_to_rest(arm_id: int, **_kwargs) -> Dict[str, Any]:
    _log(arm_id, "▶ START return_to_rest")
    _move_step(arm_id, REST_ANGLE, "return to rest")
    _log(arm_id, "■ DONE return_to_rest", level="success")
    return {"script": "arm_3_return_to_rest", "status": "done"}


def arm_3_hold_at_handoff2(arm_id: int, **_kwargs) -> Dict[str, Any]:
    _log(arm_id, "▶ START hold_at_handoff2")
    _move_step(arm_id, 180.0, "face handoff 2 (waiting)")
    _log(arm_id, "■ DONE hold_at_handoff2", level="success")
    return {"script": "arm_3_hold_at_handoff2", "status": "done"}


# ════════════════════════════════════════════════════════════════════════════
# Registry — the menu Qwen picks from
# Each arm has exactly 3 named behaviours. The keys are stable identifiers
# (used in API + log lines); the descriptions are what Qwen sees in the prompt.
# ════════════════════════════════════════════════════════════════════════════


ARM_SCRIPTS: Dict[int, Dict[str, Dict[str, Any]]] = {
    1: {
        "relay_intake_to_handoff_1": {
            "fn": arm_1_relay_intake_to_handoff_1,
            "desc": "Pick an object from intake and drop it at handoff 1 for arm 2.",
        },
        "return_to_rest": {
            "fn": arm_1_return_to_rest,
            "desc": "Return to rest position (90°). Use for abort or reset.",
        },
        "hold_at_intake": {
            "fn": arm_1_hold_at_intake,
            "desc": "Face intake and wait. Use when no work to do yet.",
        },
    },
    2: {
        "relay_handoff1_to_handoff2": {
            "fn": arm_2_relay_handoff1_to_handoff2,
            "desc": "Pick the object arm 1 left at handoff 1 and drop it at handoff 2 for arm 3.",
        },
        "return_to_rest": {
            "fn": arm_2_return_to_rest,
            "desc": "Return to rest position (90°). Use for abort or reset.",
        },
        "hold_at_handoff1": {
            "fn": arm_2_hold_at_handoff1,
            "desc": "Face handoff 1 and wait for arm 1 to deliver.",
        },
    },
    3: {
        "relay_handoff2_to_output": {
            "fn": arm_3_relay_handoff2_to_output,
            "desc": "Pick the object arm 2 left at handoff 2 and deliver it to output.",
        },
        "return_to_rest": {
            "fn": arm_3_return_to_rest,
            "desc": "Return to rest position (90°). Use for abort or reset.",
        },
        "hold_at_handoff2": {
            "fn": arm_3_hold_at_handoff2,
            "desc": "Face handoff 2 and wait for arm 2 to deliver.",
        },
    },
}


def list_scripts(arm_id: int) -> List[Dict[str, str]]:
    """Return the menu as Qwen sees it: [{name, desc}, ...]."""
    if arm_id not in ARM_SCRIPTS:
        return []
    return [{"name": k, "desc": v["desc"]} for k, v in ARM_SCRIPTS[arm_id].items()]


def run_script(arm_id: int, script_name: str, **kwargs) -> Dict[str, Any]:
    """Execute a script by name. Raises ValueError if unknown."""
    if arm_id not in ARM_SCRIPTS:
        raise ValueError(f"Unknown arm_id: {arm_id}")
    if script_name not in ARM_SCRIPTS[arm_id]:
        raise ValueError(f"Unknown script for arm {arm_id}: {script_name}. "
                         f"Valid: {list(ARM_SCRIPTS[arm_id].keys())}")
    fn: Callable = ARM_SCRIPTS[arm_id][script_name]["fn"]
    return fn(arm_id, **kwargs)
